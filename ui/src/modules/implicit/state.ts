/**
 * The implicit models of the open project (ТЗ 8.9.4; ADR 0026 «API»): the list (`GET /models`) for the section «Неявные модели» of the
 * tree, the commands and the wizard, and the details of the models a window opened (`GET /models/{id}`: the parameters as stored, the
 * outputs). Read when the project opens and on `implicit.changed {kind: "implicit_model", id, deleted?}` of the core; a read that a newer
 * one overtook is dropped. The wizard keeps its own state (`wizardState.ts`). No React.
 */
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { loadModel, loadModels as loadModelsApi, type ModelInfo, type ModelRow } from "./api";

export interface ImplicitState {
  rows: ModelRow[];
  loaded: boolean;
  error: string | null;
  /** `GET /models/{id}` of the models a window opened */
  details: Record<number, ModelInfo>;
}

const EMPTY: ImplicitState = { rows: [], loaded: false, error: null, details: {} };

export const implicitStore = new Store<ImplicitState>(EMPTY);

export const modelById = (id: number): ModelRow | undefined => implicitStore.get().rows.find((m) => m.id === id);

export function clearImplicit(): void {
  implicitStore.set(EMPTY);
}

let seq = 0;

/** Reads the list; the details of a model that is gone are dropped, of a changed one — read again. */
export async function loadRows(): Promise<void> {
  const uid = projectStore.get().current?.uid ?? null;
  const mine = ++seq;
  if (!uid) return clearImplicit();
  try {
    const rows = await loadModelsApi();
    if (mine !== seq || projectStore.get().current?.uid !== uid) return;
    const cur = implicitStore.get();
    const details: Record<number, ModelInfo> = {};
    const stale: number[] = [];
    for (const r of rows) {
      const d = cur.details[r.id];
      if (!d) continue;
      if (d.version === r.version && d.state === r.state && d.stale === r.stale) details[r.id] = d;
      else stale.push(r.id);
    }
    implicitStore.set({ rows, loaded: true, error: null, details });
    for (const id of stale) void loadDetails(id).catch(() => undefined); // a failed read: the window asks again when it needs the details
  } catch (e) {
    if (mine !== seq) return;
    implicitStore.set({ loaded: true, error: errorText(e) });
  }
}

const detailSeq = new Map<number, number>();
const inflight = new Map<number, Promise<ModelInfo | null>>();

/**
 * The model with its parameters as stored (the wizard «Изменить», the windows of the groups, «Свойства»). `null` — only when another
 * project was opened meanwhile; a read that a newer read of the same model overtook answers with that newer read.
 */
export function loadDetails(id: number): Promise<ModelInfo | null> {
  const mine = (detailSeq.get(id) ?? 0) + 1;
  detailSeq.set(id, mine);
  const uid = projectStore.get().current?.uid ?? null;
  const p = (async (): Promise<ModelInfo | null> => {
    try {
      const info = await loadModel(id);
      if (projectStore.get().current?.uid !== uid) return null;
      if (detailSeq.get(id) !== mine) return (await inflight.get(id)) ?? implicitStore.get().details[id] ?? info; // overtaken: the newer answer
      implicitStore.set((s) => ({ details: { ...s.details, [id]: info } }));
      return info;
    } finally {
      if (detailSeq.get(id) === mine) inflight.delete(id); // the newest read leaves the map when it is done
    }
  })();
  inflight.set(id, p);
  return p;
}

/** What an `implicit.changed` event means for the list: true — read it again (a trend or a planned hole of Э19б is not a model). */
export function isImplicitEvent(data: string): boolean {
  try {
    const kind = (JSON.parse(data) as { kind?: unknown }).kind;
    return kind === undefined || kind === "implicit_model";
  } catch {
    return true; // an event without a body: whatever changed, read again
  }
}

/** The id of the model chosen in the tree (`app.store.selected`), if one is; the kind is the tree's own (`implicit`). */
export function selectedModelId(selected: { kind: string; id: number } | null): number | null {
  return selected && (selected.kind === "implicit" || selected.kind === "implicit_model") ? selected.id : null;
}

/** The model of a command: the one given, else the one chosen in the tree, else the only one of the project. */
export function pickModel(given: unknown, selected: { kind: string; id: number } | null): number | null {
  if (typeof given === "number") return given;
  const sel = selectedModelId(selected);
  if (sel !== null) return sel;
  const rows = implicitStore.get().rows;
  return rows.length === 1 ? rows[0].id : null;
}
