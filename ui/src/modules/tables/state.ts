/**
 * Sets of drillholes and the «Таблица» panel (ТЗ 7.10): the active set, its tabs, pages loaded on demand,
 * sorting (asc → desc → off), the selected hole, a filter by expression, edits with the journal. No React.
 * «Показать» of the plaque of a selection of samples (ТЗ 8.3, № 237; ADR 0019 §8): only its rows (`selection=`).
 */
import { api, CoreError } from "../../shell/api";
import { handleSelectionError, isSelectionGone } from "../../shell/sampleSelection";
import { Store } from "../../shell/store";

export const PAGE = 200;

export interface SetInfo {
  id: number;
  name: string;
  holes: number;
  crs?: string | null;
}

export interface TabInfo {
  table: string;
  kind: "collar" | "survey" | "assay" | "interval" | "event";
  name: string;
  rows: number;
  rev: number;
  /** A result of sample processing (ADR 0018 §2): its cells and rows are not edited (decision 24). */
  origin?: { result_id: number; op: string; stale: boolean; reason: string | null } | null;
}

/** Why the cells and rows of a result table are not edited (ADR 0018 decision 24; the core answers 422 too). */
export const RESULT_TABLE_LOCKED = "Таблица — результат расчёта; правьте источник и перестройте";

/** The reason the cells and rows of the open table cannot be edited; null — they can. Columns may be added. */
export function rowsLocked(s: Pick<TablesState, "tabs" | "table">): string | null {
  return s.tabs.find((t) => t.table === s.table)?.origin ? RESULT_TABLE_LOCKED : null;
}

export interface ColumnInfo {
  key: string;
  name: string;
  kind: "number" | "text";
  required: boolean;
  unit: string | null;
  role: string;
}

export type Row = Record<string, unknown> & { id: number; hole_id: number; hole: string };

export interface EditRecord {
  id: number;
  group: number;
  action: string;
  table: string;
  row: number | null;
  hole: string | null;
  changes: Record<string, unknown>;
  undone: boolean;
  author: string | null;
  at: string;
}

export interface TablesState {
  sets: SetInfo[];
  setId: number | null;
  tabs: TabInfo[];
  table: string | null;
  columns: ColumnInfo[];
  total: number;
  rev: number | null;
  /** Loaded pages by their first row: rows of the current sort/filter. */
  pages: Record<number, Row[]>;
  sort: string | null;
  desc: boolean;
  hole: { id: number; name: string } | null;
  where: string;
  /** the token of the selection of samples whose rows only are shown («Показать»); null — all rows */
  selection: string | null;
  error: string | null;
  busy: boolean;
  version: number;
}

export const tablesStore = new Store<TablesState>({
  sets: [], setId: null, tabs: [], table: null, columns: [], total: 0, rev: null, pages: {}, sort: null, desc: false,
  hole: null, where: "", selection: null, error: null, busy: false, version: 0,
});

const text = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** Reloads the list of sets. The active set changes only when it is gone, or with `pickFirst` (a project was
 * opened): a background reload (another window, an import) never resets the table the user is looking at. */
export async function loadSets(pickFirst = false): Promise<SetInfo[]> {
  try {
    const r = await api.get<{ sets: SetInfo[] }>("/drillholes/sets");
    const s = tablesStore.get();
    const exists = r.sets.some((x) => x.id === s.setId);
    tablesStore.set({ sets: r.sets, error: null });
    if (s.setId !== null && !exists) await selectSet(r.sets[0]?.id ?? null);
    else if (s.setId === null && pickFirst && r.sets.length) await selectSet(r.sets[0].id);
    return r.sets;
  } catch {
    tablesStore.set({ sets: [], setId: null, tabs: [], table: null, columns: [], total: 0, pages: {} });
    return [];
  }
}

export async function selectSet(setId: number | null, table?: string): Promise<void> {
  tablesStore.set({ setId, tabs: [], table: null, columns: [], total: 0, pages: {}, hole: null, where: "", selection: null, sort: null, error: null });
  if (setId === null) return;
  await loadTabs();
  const tabs = tablesStore.get().tabs;
  await openTable(table ?? tabs.find((t) => t.kind === "assay")?.table ?? tabs[0]?.table ?? null);
}

export async function loadTabs(): Promise<void> {
  const s = tablesStore.get();
  if (s.setId === null) return;
  try {
    const r = await api.get<{ set: SetInfo; tables: TabInfo[] }>(`/drillholes/sets/${s.setId}/tables`);
    tablesStore.set((st) => ({ tabs: r.tables, sets: st.sets.map((x) => (x.id === r.set.id ? { ...x, ...r.set } : x)) }));
  } catch (e) {
    tablesStore.set({ error: text(e) });
  }
}

/** A tab by its key or by a kind («collar», «survey», «assay»: the first of that kind). */
export function resolveTable(tabs: TabInfo[], kindOrId: string): string | null {
  return tabs.find((t) => t.table === kindOrId)?.table ?? tabs.find((t) => t.kind === kindOrId)?.table ?? null;
}

export async function openTable(table: string | null): Promise<void> {
  generation += 1;
  tablesStore.set({ table, pages: {}, total: 0, columns: [], sort: null, desc: false, selection: null, error: null });
  if (table !== null) await loadPage(0);
}

function query(offset: number): string {
  const s = tablesStore.get();
  const q = new URLSearchParams({ offset: String(offset), limit: String(PAGE) });
  if (s.sort) q.set("sort", s.sort);
  if (s.desc) q.set("desc", "true");
  if (s.hole) q.set("hole", String(s.hole.id));
  if (s.where.trim()) q.set("where", s.where.trim());
  if (s.selection) q.set("selection", s.selection);
  return q.toString();
}

const inflight = new Set<string>();
/** Grows on every reload: a page answered for an older generation is dropped (it may hold old values and rev). */
let generation = 0;

/** Loads the page holding row `offset` (pages of 200, cached until the sort, filter or data change). */
export async function loadPage(offset: number): Promise<void> {
  const s = tablesStore.get();
  if (s.setId === null || s.table === null) return;
  const start = Math.floor(offset / PAGE) * PAGE;
  if (s.pages[start]) return;
  const q = query(start);
  const key = `${s.setId}:${s.table}:${q}`;
  const gen = generation;
  const flight = `${gen}:${key}`;
  if (inflight.has(flight)) return;
  inflight.add(flight);
  tablesStore.set({ busy: true });
  try {
    const r = await api.get<{ rows: Row[]; total: number; columns: ColumnInfo[]; rev: number }>(`/drillholes/sets/${s.setId}/tables/${s.table}?${q}`);
    const now = tablesStore.get();
    if (gen !== generation || now.setId !== s.setId || now.table !== s.table || query(start) !== q) return; // stale
    tablesStore.set((st) => ({ pages: { ...st.pages, [start]: r.rows }, total: r.total, columns: r.columns, rev: r.rev, error: null, version: st.version + 1 }));
  } catch (e) {
    // the selection shown is gone (its table changed, the core forgot it): all rows again, the reason in the status bar
    if (s.selection && isSelectionGone(e)) {
      handleSelectionError(s.selection, e);
      if (tablesStore.get().selection === s.selection) void showSelection(null);
    } else tablesStore.set({ error: text(e) });
  } finally {
    inflight.delete(flight);
    tablesStore.set({ busy: false });
  }
}

export function rowAt(s: TablesState, index: number): Row | undefined {
  const start = Math.floor(index / PAGE) * PAGE;
  return s.pages[start]?.[index - start];
}

export async function reload(): Promise<void> {
  generation += 1;
  tablesStore.set({ pages: {} });
  await loadPage(0);
}

/** Header click: ascending → descending → off (ТЗ 7.10). */
export function nextSort(s: Pick<TablesState, "sort" | "desc">, key: string): { sort: string | null; desc: boolean } {
  if (s.sort !== key) return { sort: key, desc: false };
  if (!s.desc) return { sort: key, desc: true };
  return { sort: null, desc: false };
}

export async function sortBy(key: string): Promise<void> {
  tablesStore.set((s) => nextSort(s, key));
  await reload();
}

export async function filterHole(hole: { id: number; name: string } | null): Promise<void> {
  tablesStore.set({ hole });
  await reload();
}

/** «Показать» of the plaque: only the rows of the selection `token` (null — all rows again). */
export async function showSelection(token: string | null): Promise<void> {
  if (tablesStore.get().selection === token) return;
  tablesStore.set({ selection: token });
  await reload();
}

export async function setWhere(where: string): Promise<void> {
  tablesStore.set({ where });
  await reload();
}

async function afterEdit(): Promise<void> {
  await Promise.all([loadTabs(), reload()]);
}

/** The value as the editor shows it: full precision with a decimal comma (not the rounded cell text). */
export function editText(v: unknown, kind: "number" | "text"): string {
  if (v === null || v === undefined) return "";
  if (kind === "number" && typeof v === "number") return String(v).replace(".", ",");
  return String(v);
}

export function findRow(s: TablesState, id: number): { index: number; row: Row } | null {
  for (const [start, rows] of Object.entries(s.pages)) {
    const i = rows.findIndex((r) => r.id === id);
    if (i >= 0) return { index: Number(start) + i, row: rows[i] };
  }
  return null;
}

export async function editCell(row: Row, column: string, value: string): Promise<string | null> {
  const s = tablesStore.get();
  if (rowsLocked(s)) return rowsLocked(s);
  try {
    await api.patch(`/drillholes/sets/${s.setId}/tables/${s.table}/rows/${row.id}`, { column, value: value === "" ? null : value, rev: s.rev });
    await afterEdit();
    return null;
  } catch (e) {
    if (e instanceof CoreError && e.code === "version_conflict") await afterEdit();
    return text(e);
  }
}

export async function addRow(values: Record<string, string>): Promise<string | null> {
  const s = tablesStore.get();
  if (rowsLocked(s)) return rowsLocked(s);
  try {
    await api.post(`/drillholes/sets/${s.setId}/tables/${s.table}/rows`, { values, rev: s.rev });
    await afterEdit();
    return null;
  } catch (e) {
    return text(e);
  }
}

export async function deleteRow(row: Row): Promise<string | null> {
  const s = tablesStore.get();
  if (rowsLocked(s)) return rowsLocked(s);
  try {
    await api.del(`/drillholes/sets/${s.setId}/tables/${s.table}/rows/${row.id}?rev=${s.rev ?? ""}`);
    await afterEdit();
    return null;
  } catch (e) {
    return text(e);
  }
}

export async function loadEdits(): Promise<EditRecord[]> {
  const s = tablesStore.get();
  if (s.setId === null) return [];
  const r = await api.get<{ edits: EditRecord[] }>(`/drillholes/sets/${s.setId}/edits`);
  return r.edits;
}

export async function undoEdit(id: number): Promise<string | null> {
  try {
    await api.post(`/drillholes/edits/${id}/undo`, {});
    await afterEdit();
    return null;
  } catch (e) {
    return text(e);
  }
}

export interface FieldStats {
  kind: "number" | "text";
  rows: number;
  filled: number;
  empty: number;
  min?: number;
  max?: number;
  mean?: number;
  median?: number;
  std?: number;
  unique?: number;
  top?: { value: string; count: number }[];
}

export async function fieldStats(column: string): Promise<FieldStats> {
  const s = tablesStore.get();
  const q = new URLSearchParams({ column });
  if (s.hole) q.set("hole", String(s.hole.id));
  if (s.where.trim()) q.set("where", s.where.trim());
  return api.get<FieldStats>(`/drillholes/sets/${s.setId}/tables/${s.table}/stats?${q}`);
}

/** Runs a tool of the panel (replace, columns, append, merge); the table reloads after it. */
export async function runTool(path: string, body: Record<string, unknown>, method: "post" | "patch" | "del" = "post"): Promise<{ ok: true; result: Record<string, unknown> } | { ok: false; error: string }> {
  const s = tablesStore.get();
  const url = `/drillholes/sets/${s.setId}/tables/${s.table}/${path}`;
  try {
    const result = method === "del" ? await api.del<Record<string, unknown>>(`${url}?rev=${s.rev ?? ""}`) : await api[method]<Record<string, unknown>>(url, { ...body, rev: s.rev });
    await afterEdit();
    return { ok: true, result };
  } catch (e) {
    return { ok: false, error: text(e) };
  }
}

/** Format of a cell: numbers with a decimal comma, missing — empty (never 0). */
export function cellText(v: unknown, kind: "number" | "text"): string {
  if (v === null || v === undefined) return "";
  if (kind === "number" && typeof v === "number") return v.toLocaleString("ru-RU", { maximumFractionDigits: 6, useGrouping: false });
  return String(v);
}
