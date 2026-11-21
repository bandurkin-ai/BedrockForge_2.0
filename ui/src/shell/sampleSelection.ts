/**
 * The selection of samples of the application (ТЗ 8.3, № 237; ADR 0019 §8): one at a time — `{set_id, table,
 * token, n}`; its rows (`dh_rows.id`, ≤ 200 000) the core keeps (`POST /stats/selection`). A frame or a lasso on a
 * chart, «Выделить на графиках» of the «Таблица» panel make it; the 3D layer of segments, the plaque of the panel
 * and the marks of the open charts follow it. An edit of its table (`rev`) — the core answers 409
 * `selection_stale`, 404 `selection_not_found` once it forgot it: the selection goes with a notice in the status
 * bar. Apart from it — the samples of the hole selected (`hole.select`): only marked on the charts. No React.
 */
import { api, CoreError } from "./api";
import { Store } from "./store";

export const MAX_SELECTION_ROWS = 200_000;

export type SelectionSource = "rows" | "scatter" | "box" | "probplot" | "table";

/** The answer of `POST /stats/selection` (and of `GET …/{token}` without `rows`). */
export interface SelectionInfo {
  token: string;
  set_id: number;
  table_id: number;
  table_name: string;
  rev: number;
  n: number;
  /** ids given that are not samples of the table */
  unknown: number;
  source: SelectionSource;
}

/** The samples of the selected hole: marks on the charts only (no 3D layer, no plaque). */
export interface HoleSamples {
  /** `${set}:${table}:${hole}` it was made for */
  key: string;
  token: string;
  table_id: number;
  rows: Float64Array;
}

export interface SampleSelectionState {
  current: SelectionInfo | null;
  /** ids of the rows of `current`, ascending (to mark the points of the charts); null — being read */
  rows: Float64Array | null;
  hole: HoleSamples | null;
  /** a request making a selection is on its way */
  busy: boolean;
  /** segments drawn in 3D for `current` (the counter of the layer, E2E); null — none drawn */
  segments: number | null;
  /** samples of the selection without a position (a hole without a trajectory) */
  noPosition: number;
}

const EMPTY: SampleSelectionState = { current: null, rows: null, hole: null, busy: false, segments: null, noPosition: 0 };

export const sampleSelectionStore = new Store<SampleSelectionState>({ ...EMPTY });

let notify: (text: string) => void = () => undefined;
/** Where the notices go (the status bar; `bindSampleSelection`). */
export function setSelectionNotice(fn: (text: string) => void): void {
  notify = fn;
}

/** A line about the selection in the status bar (what the charts could not select). */
export const selectionNotice = (text: string): void => notify(text);

const errorLine = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** The core does not hold the selection any more: its table changed (409) or it was forgotten (404). */
export const isSelectionGone = (e: unknown): boolean => e instanceof CoreError && (e.code === "selection_stale" || e.code === "selection_not_found");

/** «Выделено 1 проба», «Выделено 3 пробы», «Выделено 12 проб». */
export function selectedText(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  const word = m10 === 1 && m100 !== 11 ? "проба" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "пробы" : "проб";
  return `Выделено ${n.toLocaleString("ru-RU")} ${word}`;
}

/** The ids ascending as float64 (an id may pass 2³¹). */
export function sortedIds(ids: ArrayLike<number>): Float64Array {
  const out = Float64Array.from(ids);
  out.sort();
  return out;
}

/** Is `id` among the ascending `sorted` (binary search). */
export function hasId(sorted: ArrayLike<number>, id: number): boolean {
  let lo = 0;
  let hi = sorted.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const v = sorted[mid];
    if (v === id) return true;
    if (v < id) lo = mid + 1;
    else hi = mid - 1;
  }
  return false;
}

/** Indices of the points of a chart (their `row` ids) that are among the marked rows. */
export function markIndices(rowIds: ArrayLike<number | null | undefined>, sorted: ArrayLike<number> | null): number[] {
  if (!sorted || !sorted.length) return [];
  const out: number[] = [];
  for (let i = 0; i < rowIds.length; i++) {
    const id = rowIds[i];
    if (typeof id === "number" && hasId(sorted, id)) out.push(i);
  }
  return out;
}

/** What the charts mark: the rows of the selection, else the samples of the selected hole. */
export const markedRows = (s: Pick<SampleSelectionState, "current" | "rows" | "hole">): Float64Array | null => (s.current ? s.rows : (s.hole?.rows ?? null));

const forget = (token: string) => void api.del(`/stats/selection/${encodeURIComponent(token)}`).catch(() => undefined);

/** Grow with every new selection (and its clearing), every new hole: an answer for an older one is dropped. */
let generation = 0;
let holeGeneration = 0;

async function readRows(info: SelectionInfo, gen: number): Promise<void> {
  try {
    const r = await api.get<SelectionInfo & { rows: number[] }>(`/stats/selection/${encodeURIComponent(info.token)}`);
    if (gen !== generation) return;
    sampleSelectionStore.set({ rows: sortedIds(r.rows) });
  } catch (e) {
    if (gen === generation) handleSelectionError(info.token, e);
  }
}

/** Makes the selection of the application from one of the bodies of `POST /stats/selection`; the old one goes. */
export async function makeSelection(body: Record<string, unknown>): Promise<SelectionInfo | null> {
  const gen = ++generation;
  sampleSelectionStore.set({ busy: true });
  try {
    const info = await api.post<SelectionInfo>("/stats/selection", body);
    if (gen !== generation) {
      forget(info.token);
      return null;
    }
    const old = sampleSelectionStore.get().current;
    if (old && old.token !== info.token) forget(old.token);
    sampleSelectionStore.set({ current: info, rows: null, busy: false, segments: null, noPosition: 0 });
    void readRows(info, gen);
    if (info.unknown) notify(`${selectedText(info.n)}; строк не из таблицы «${info.table_name}»: ${info.unknown.toLocaleString("ru-RU")}.`);
    return info;
  } catch (e) {
    if (gen === generation) {
      sampleSelectionStore.set({ busy: false });
      notify(`Выделение не сделано: ${errorLine(e)}`);
    }
    return null;
  }
}

/** The points of a chart (rows of the table `table`; null — the sample table of the set). */
export const selectRows = (setId: number, table: string | number | null, rows: readonly number[]) => makeSelection({ set_id: setId, table, rows: [...rows] });

/** A table of the «Таблица» panel (its key is the id as text) as the core takes it: by the id. */
export const panelTable = (table: string | number | null): string | number | null => (typeof table === "string" && /^\d+$/.test(table) ? Number(table) : table);

/** «И наоборот»: the rows of the filter of the «Таблица» panel. */
export const selectByFilter = (setId: number, table: string | number | null, where: string | null, hole: number | null) => makeSelection({ set_id: setId, table: panelTable(table), filter: { where: where && where.trim() ? where.trim() : null, hole } });

/** Clears the selection (`chart.select_clear`, `Esc`, «Снять»); `notice` — a line for the status bar. */
export function clearSelection(notice?: string): boolean {
  const cur = sampleSelectionStore.get().current;
  generation += 1;
  sampleSelectionStore.set({ current: null, rows: null, busy: false, segments: null, noPosition: 0 });
  if (cur) forget(cur.token);
  if (notice) notify(notice);
  return cur !== null;
}

/**
 * An answer about the selection `token`: gone (409 `selection_stale`, 404) — it is cleared with the reason in the
 * status bar; true — it was. Another error — false (the caller tells it).
 */
export function handleSelectionError(token: string, e: unknown): boolean {
  if (!isSelectionGone(e)) return false;
  const s = sampleSelectionStore.get();
  if (s.current?.token === token) {
    generation += 1;
    sampleSelectionStore.set({ current: null, rows: null, busy: false, segments: null, noPosition: 0 });
    notify(e instanceof CoreError && e.code === "selection_stale" ? `${e.message} Выделение проб снято.` : "Выделение проб снято: ядро его больше не хранит.");
  }
  if (s.hole?.token === token) sampleSelectionStore.set({ hole: null });
  return true;
}

/** The data of a set changed (`drillholes.changed`): the selection of it is asked again — gone, it is cleared. */
export async function revalidate(setId?: number): Promise<void> {
  const cur = sampleSelectionStore.get().current;
  if (!cur || (setId !== undefined && cur.set_id !== setId)) return;
  const gen = generation;
  await readRows(cur, gen);
}

/** Another project (or none): what the core held for the old one is not asked about. */
export function resetSelection(): void {
  generation += 1;
  holeGeneration += 1;
  sampleSelectionStore.set({ ...EMPTY });
}

/**
 * The samples of the selected hole for the marks of the charts: `hole` null — none. The table: of the panel when it
 * is an interval one, else (null) the sample table of the set. The old token goes.
 */
export async function setHoleSamples(want: { setId: number; table: string | null; hole: number } | null): Promise<void> {
  const key = want ? `${want.setId}:${want.table ?? ""}:${want.hole}` : null;
  const old = sampleSelectionStore.get().hole;
  if ((old?.key ?? null) === key) return;
  const gen = ++holeGeneration;
  if (old) forget(old.token);
  sampleSelectionStore.set({ hole: null });
  if (!want || !key) return;
  try {
    const info = await api.post<SelectionInfo>("/stats/selection", { set_id: want.setId, table: panelTable(want.table), filter: { where: null, hole: want.hole } });
    if (gen !== holeGeneration) return forget(info.token);
    const r = await api.get<SelectionInfo & { rows: number[] }>(`/stats/selection/${encodeURIComponent(info.token)}`);
    if (gen !== holeGeneration) return forget(info.token);
    sampleSelectionStore.set({ hole: { key, token: info.token, table_id: info.table_id, rows: sortedIds(r.rows) } });
  } catch {
    /* no marks of the hole: the table is not an interval one, the core is away */
  }
}

/** The samples of the hole are read again (after an edit of its set). */
export function refreshHoleSamples(): void {
  const h = sampleSelectionStore.get().hole;
  if (!h) return;
  holeGeneration += 1;
  forget(h.token);
  sampleSelectionStore.set({ hole: null });
}
