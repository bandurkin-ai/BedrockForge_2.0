/**
 * What the chart commands and the tree know without asking (ADR 0019 §2, §9, §11): the saved charts of the sets
 * (the kinds of Э13; displays along holes and swath plots are elsewhere) — read when a project opens, kept by
 * `objects.changed {kind: "chart"}` — and the number of components of each set (the most of its tables —
 * «Дендрограмма» needs 2 and more), read for the active set and again after an edit of its data. No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { createCalculator, getChart, invalidateFields, listCharts, loadFields, type Calculator, type ChartRow } from "./api";
import { isChartKind } from "./settings";

export interface ChartsState {
  /** saved charts of the sets (kinds of Э13) */
  charts: ChartRow[];
  loaded: boolean;
  /** set id → the most components among its tables; absent — not read yet */
  components: Record<number, number>;
  /** the revision of the data of all sets: an edit of an unknown set (or a project opened) */
  rev: number;
  /** set id → the revision of its data (`drillholes.changed`): an open chart computes again (ADR 0019 §2 «Данные») */
  revs: Record<number, number>;
}

export const chartsStore = new Store<ChartsState>({ charts: [], loaded: false, components: {}, rev: 0, revs: {} });

const isSetChart = (c: ChartRow): boolean => c.set_id !== null && isChartKind(c.kind);

/** The saved charts of the project (all of them: a set's chart lives under it). */
export async function loadCharts(): Promise<void> {
  try {
    const { charts } = await listCharts();
    chartsStore.set({ charts: charts.filter(isSetChart), loaded: true });
  } catch {
    chartsStore.set({ charts: [], loaded: false });
  }
}

export function upsertChart(row: ChartRow): void {
  if (!isSetChart(row)) return;
  chartsStore.set((s) => ({ charts: [...s.charts.filter((c) => c.id !== row.id), row] }));
}

export function dropChart(id: number): void {
  chartsStore.set((s) => ({ charts: s.charts.filter((c) => c.id !== id) }));
}

export const findChart = (id: number): ChartRow | undefined => chartsStore.get().charts.find((c) => c.id === id);

/** The charts of a set by name (as the tree shows them). */
export const chartsOfSet = (charts: readonly ChartRow[], setId: number): ChartRow[] => charts.filter((c) => c.set_id === setId).sort((a, b) => a.name.localeCompare(b.name, "ru"));

/** `objects.changed {kind: "chart", id, set_id, chart, deleted?}`: the one chart read again (or dropped). */
export async function chartChanged(event: { id?: unknown; deleted?: unknown; chart?: unknown }): Promise<void> {
  if (typeof event.id !== "number") return loadCharts();
  if (event.deleted === true) return dropChart(event.id);
  if (typeof event.chart === "string" && !isChartKind(event.chart)) return;
  try {
    upsertChart(await getChart(event.id));
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) dropChart(event.id);
  }
}

/** Reads the tables of the set (the cache of `loadFields` answers again until an edit). */
export async function readComponents(setId: number): Promise<void> {
  try {
    const f = await loadFields(setId);
    const most = f.tables.reduce((m, t) => Math.max(m, t.components), 0);
    chartsStore.set((s) => ({ components: { ...s.components, [setId]: most } }));
  } catch {
    /* the set is gone or the core is away: nothing known */
  }
}

/** An edit of the data of a set (all sets without one): its fields are read again, its charts computed again. */
export function dataChanged(setId?: number): void {
  invalidateFields(setId);
  chartsStore.set((s) => {
    if (setId === undefined) return { components: {}, rev: s.rev + 1 };
    const { [setId]: _gone, ...rest } = s.components;
    return { components: rest, revs: { ...s.revs, [setId]: (s.revs[setId] ?? 0) + 1 } };
  });
}

/** The revision of the data of the sets of a chart's body (`set_id`, and `y_set_id` of Q-Q). */
export function dataRev(body: { set_id?: unknown; y_set_id?: unknown }): number {
  const { rev, revs } = chartsStore.get();
  const of = (id: unknown) => (typeof id === "number" ? (revs[id] ?? 0) : 0);
  return rev + of(body.set_id) + (body.y_set_id !== body.set_id ? of(body.y_set_id) : 0);
}

/**
 * A chart window's calculation (`createCalculator`) with the revision of its set's data in the key, as the swath's
 * `rev` (ADR 0019 §2 «Данные», Т7.28-1): the same settings after `drillholes.changed` of the set are asked again —
 * the last settings go again by themselves, the window need not ask.
 */
export function dataCalculator<B extends { set_id?: unknown; y_set_id?: unknown }, T>(load: (body: B) => Promise<T>, apply: (answer: T) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void): Calculator<B> {
  const calc = createCalculator<{ body: B; rev: number }, T>((b) => load(b.body), apply, fail, busy);
  let last: B | null = null;
  let rev = 0;
  const off = chartsStore.subscribe(() => {
    if (last === null) return;
    const now = dataRev(last);
    if (now !== rev) {
      rev = now;
      calc.request({ body: last, rev });
    }
  });
  return {
    request(body) {
      last = body;
      rev = dataRev(body);
      calc.request({ body, rev });
    },
    stop() {
      off();
      calc.stop();
    },
  };
}

/** Why «Дендрограмма» cannot be built for the set: fewer than 2 components (unknown — no reason). */
export function componentsReason(setId: number | null): string | null {
  if (setId === null) return null;
  const n = chartsStore.get().components[setId];
  return n !== undefined && n < 2 ? "Нужно 2 и больше компонента" : null;
}
