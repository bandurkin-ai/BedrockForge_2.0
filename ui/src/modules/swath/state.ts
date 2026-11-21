/**
 * What the swath commands, the tree, the windows and the 3D know without asking (ADR 0019 §2, §6): the swaths of
 * the project (`objects(chart)` kind `swath`, `set_id = null`) — read when a project opens, kept by
 * `objects.changed {kind: "chart", chart: "swath"}`; the revision of the data (an edit of samples or of a model
 * computes the open swaths again, Т7.30-5); what the open windows have (their settings as edited and the last
 * answer — the 3D draws them without asking again); the swath under the cursor of a plot (lit in 3D, Т7.30-3)
 * and the swath chosen (its window in focus or its row in the tree — the handles of Д-01). No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { windowStore } from "../../shell/windows";
import { getChart, type ChartRow } from "../charts/api";
import { invalidateSources, listSwaths, type SwathResult } from "./api";
import type { SwathSettings } from "./model";

export interface SwathsState {
  swaths: ChartRow[];
  loaded: boolean;
  /** grows with every edit of data a swath may use */
  rev: number;
  /** the swath plot under the cursor of a window and its swath (from 0) */
  hover: { id: number; index: number } | null;
  /** the swath chosen: its window in focus or its tree row */
  selected: number | null;
}

export const swathsStore = new Store<SwathsState>({ swaths: [], loaded: false, rev: 0, hover: null, selected: null });

/** An open window: its settings as edited (saved 400 ms later) and its last answer (null — none yet). */
export interface LiveSwath {
  settings: SwathSettings;
  result: SwathResult | null;
  /** the last failure of its calculation (a new object every time; null — the last one went well) */
  error?: object | null;
}

export const liveStore = new Store<{ live: Record<number, LiveSwath> }>({ live: {} });

const isSwath = (c: ChartRow): boolean => c.kind === "swath" && c.set_id === null;

/** The swaths by name (as the tree shows them). */
export const swathsByName = (rows: readonly ChartRow[]): ChartRow[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);

export const findSwath = (id: number): ChartRow | undefined => swathsStore.get().swaths.find((c) => c.id === id);

export async function loadSwaths(): Promise<void> {
  try {
    const { charts } = await listSwaths();
    swathsStore.set({ swaths: charts.filter(isSwath), loaded: true });
  } catch {
    swathsStore.set({ swaths: [], loaded: false });
  }
}

export function upsertSwath(row: ChartRow): void {
  if (!isSwath(row)) return;
  swathsStore.set((s) => ({ swaths: [...s.swaths.filter((c) => c.id !== row.id), row] }));
}

export function dropSwath(id: number): void {
  swathsStore.set((s) => ({ swaths: s.swaths.filter((c) => c.id !== id), selected: s.selected === id ? null : s.selected, hover: s.hover?.id === id ? null : s.hover }));
}

/** `objects.changed {kind: "chart", id, chart, deleted?}` of a swath: the one row read again (or dropped). */
export async function swathChanged(event: { id?: unknown; deleted?: unknown; chart?: unknown }): Promise<void> {
  if (typeof event.id !== "number") return loadSwaths();
  if (event.deleted === true) return dropSwath(event.id);
  if (event.chart !== "swath") return;
  try {
    upsertSwath(await getChart(event.id));
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) dropSwath(event.id);
  }
}

/** An edit of data (samples, a model, a set or a model deleted): the sources are read and the swaths computed again. */
export function swathDataChanged(): void {
  invalidateSources();
  swathsStore.set((s) => ({ rev: s.rev + 1 }));
}

export function setLive(id: number, live: LiveSwath | null): void {
  liveStore.set((s) => {
    const next = { ...s.live };
    if (live) next[id] = live;
    else delete next[id];
    return { live: next };
  });
}

export function setHover(id: number, index: number | null): void {
  const now = swathsStore.get().hover;
  if (index === null ? now?.id !== id : now?.id === id && now.index === index) return;
  swathsStore.set({ hover: index === null ? null : { id, index } });
}

/** A handle of Д-01 being dragged: the swath and its angles now (the window shows them, its plot dimmed); null — none. */
export interface SwathDrag {
  id: number;
  azimuth: number;
  dip: number;
  roll: number;
}

export const dragStore = new Store<{ drag: SwathDrag | null }>({ drag: null });

export function setDrag(drag: SwathDrag | null): void {
  dragStore.set({ drag });
}

/** The window of a swath is open (its kind is `SWATH_WINDOW` of `bind.ts`): it computes the swath itself. */
export const swathWindowOpen = (id: number): boolean => windowStore.get().windows.some((w) => w.kind === "swath" && w.props?.swathId === id);

export function selectSwath(id: number | null): void {
  if (swathsStore.get().selected !== id) swathsStore.set({ selected: id });
}

export function resetSwaths(): void {
  swathsStore.set({ swaths: [], loaded: false, hover: null, selected: null });
  liveStore.set({ live: {} });
  dragStore.set({ drag: null });
  invalidateSources();
}

const takers = new Map<number, Set<(s: SwathSettings) => void>>();

/** An open window takes the settings a command gives its swath (it saves them as its own edit). */
export function onPushed(id: number, fn: (s: SwathSettings) => void): () => void {
  const set = takers.get(id) ?? new Set();
  set.add(fn);
  takers.set(id, set);
  return () => {
    set.delete(fn);
    if (!set.size) takers.delete(id);
  };
}

/** New settings of a swath for its open window; false — no window took them. */
export function pushSettings(id: number, s: SwathSettings): boolean {
  const set = takers.get(id);
  if (!set?.size) return false;
  for (const fn of set) fn(s);
  return true;
}
