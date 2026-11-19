/**
 * The catalog of the core for «Менеджер данных» (ADR 0017 §6): `GET /catalog` (pages `{total, rows}`),
 * `GET /catalog/folders`, `POST /catalog/move {ids, folder}`. The store holds every row of the project — the
 * model (catalog.ts) filters and sorts them in the window at once. Reread on `objects.changed`, at the end of an
 * import and when another project opens; a read overtaken by a newer one is dropped and its caller waits for the
 * newest (the lesson of Э10: a caller that awaits the list gets it fresh). Answers are read leniently.
 */
import { api, CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { normFolder, type CatalogRow, type FolderCount } from "./catalog";

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** A row of `GET /catalog`; null — not a row (no id or no type). */
export function parseRow(raw: unknown): CatalogRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = num(o.id);
  const kind = str(o.kind) ?? str(o.type);
  if (id === null || !kind) return null;
  return {
    id,
    kind,
    name: str(o.name) ?? `№${id}`,
    folder: normFolder(str(o.folder)),
    format: str(o.format) ?? str(o.source_format),
    size: num(o.size),
    rows: num(o.rows),
    modified: str(o.modified) ?? str(o.updated_at),
    crs: str(o.crs) ?? str(o.crs_name),
    state: str(o.state) ?? "ready",
  };
}

export function parsePage(raw: unknown): { total: number; rows: CatalogRow[] } {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rows = (Array.isArray(o.rows) ? o.rows : []).map(parseRow).filter((r): r is CatalogRow => r !== null);
  return { total: num(o.total) ?? rows.length, rows };
}

export function parseFolders(raw: unknown): FolderCount[] {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const list = Array.isArray(raw) ? raw : Array.isArray(o.folders) ? o.folders : [];
  return list.flatMap((f) => {
    if (typeof f === "string") return normFolder(f) ? [{ folder: normFolder(f)!, count: 0 }] : [];
    if (!f || typeof f !== "object") return [];
    const r = f as Record<string, unknown>;
    const folder = normFolder(str(r.folder) ?? str(r.path) ?? str(r.name));
    return folder ? [{ folder, count: num(r.count) ?? 0 }] : [];
  });
}

export interface CatalogQuery {
  folder?: string;
  kind?: string;
  q?: string;
  sort?: string;
  desc?: boolean;
  offset?: number;
  limit?: number;
}

export function catalogPath(q: CatalogQuery = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, typeof v === "boolean" ? (v ? "1" : "0") : String(v));
  const s = p.toString();
  return s ? `/catalog?${s}` : "/catalog";
}

export const PAGE = 2000;
/** Pages read at most (a project of 200 000 data objects): a broken `total` does not loop for ever. */
const MAX_PAGES = 100;

/** Every row of the catalog, page by page. */
export async function fetchAllRows(get: (path: string) => Promise<unknown> = (p) => api.get(p)): Promise<CatalogRow[]> {
  const out: CatalogRow[] = [];
  for (let i = 0; i < MAX_PAGES; i++) {
    const page = parsePage(await get(catalogPath({ offset: out.length, limit: PAGE })));
    out.push(...page.rows);
    if (!page.rows.length || out.length >= page.total) break;
  }
  return out;
}

export async function fetchFolders(): Promise<FolderCount[]> {
  return parseFolders(await api.get<unknown>("/catalog/folders"));
}

/** Moves the objects into the folder in one transaction ("" — the root); the core sends `objects.changed`. */
export async function moveToFolder(ids: readonly number[], folder: string): Promise<number> {
  const r = await api.post<{ moved?: number }>("/catalog/move", { ids: [...ids], folder: normFolder(folder) ?? "" });
  await loadCatalog();
  return typeof r?.moved === "number" ? r.moved : ids.length;
}

// ——— the store ———

export interface CatalogState {
  rows: CatalogRow[];
  folders: FolderCount[];
  loaded: boolean;
  error: string | null;
}

const EMPTY: CatalogState = { rows: [], folders: [], loaded: false, error: null };
export const catalogStore = new Store<CatalogState>(EMPTY);

let seq = 0;
let newest: Promise<void> = Promise.resolve();

async function readCatalog(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) {
    catalogStore.set(EMPTY);
    return;
  }
  try {
    const [rows, folders] = await Promise.all([fetchAllRows(), fetchFolders()]);
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = catalogStore.get();
    // the same lists keep their identity: the window does not redo its model for an unchanged answer
    const same = cur.loaded && !cur.error && JSON.stringify(rows) === JSON.stringify(cur.rows) && JSON.stringify(folders) === JSON.stringify(cur.folders);
    if (!same) catalogStore.set({ rows, folders, loaded: true, error: null });
  } catch (e) {
    if (mine !== seq) return;
    catalogStore.set({ loaded: true, error: e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e) });
  }
}

/** Reads the catalog; overtaken by a newer read, it waits for the newest one to settle. */
export async function loadCatalog(): Promise<void> {
  const mine = readCatalog();
  newest = mine;
  await mine;
  while (newest !== mine) {
    const next = newest;
    await next;
    if (newest === next) break;
  }
}

/** Keeps the catalog in step while the window is open; returns the unsubscribe. */
export function bindCatalog(): () => void {
  let uid = projectStore.get().current?.uid ?? null;
  const offs = [
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      catalogStore.set(EMPTY);
      void loadCatalog();
    }),
    onCoreEvent((event) => {
      if (event === "objects.changed" || event === "drillholes.changed") void loadCatalog();
    }),
    onJobFinished((job) => {
      if (job.kind.startsWith("import.") || job.kind === "model.dtm") void loadCatalog();
    }),
  ];
  void loadCatalog();
  return () => {
    for (const off of offs) off();
  };
}
