/**
 * The core's API of the charts (ADR 0019 §10; contract of 13.15 in `docs/progress/e13-api.md`): fields of a set's
 * tables, the calculations `POST /stats/<kind>` (body — the chart's settings), the saved charts `/charts`.
 * `createCalculator` sends the newest settings 300 ms after the last edit and drops an answer that a newer
 * request has overtaken (ADR 0019 §11 «Отклик»).
 */
import { api } from "../../shell/api";
import type { ChartKind, HistogramSettings } from "./settings";

// ---- fields of the set's tables: GET /stats/fields?set_id=&table= ----

export interface StatsTable {
  /** `name_key` of the table (what the settings keep) */
  key: string;
  name: string;
  kind: string;
  rows: number;
}

export interface NumericField {
  name: string;
  /** a component, a numeric attribute or `length` — the interval length */
  kind: "component" | "attribute" | "length";
  unit: string | null;
  /** rows with a value */
  count: number;
}

export interface TextField {
  name: string;
  /** distinct values (≤ 300 listed) */
  count: number;
  values: string[];
}

export interface StatsFields {
  tables: StatsTable[];
  /** the table the fields belong to (the asked one or the set's sample table); null — the set has none */
  table: string | null;
  numeric: NumericField[];
  text: TextField[];
}

export function loadFields(setId: number, table?: string | null): Promise<StatsFields> {
  const q = new URLSearchParams({ set_id: String(setId) });
  if (table) q.set("table", table);
  return api.get<StatsFields>(`/stats/fields?${q}`);
}

/** The default field: the first component, else the first numeric field but the length. */
export function defaultField(fields: Pick<StatsFields, "numeric">): string | null {
  return (fields.numeric.find((f) => f.kind === "component") ?? fields.numeric.find((f) => f.kind !== "length"))?.name ?? null;
}

// ---- calculations ----

/** Rows left out and changed by the selection of values (ADR 0019 §3, «Пропуски»). */
export interface SelectCounts {
  rows: number;
  missing: number;
  bad_weight: number;
  negative: number;
  zero_excluded: number;
  bottom_capped: number;
  bottom_excluded: number;
  capped: number;
  top_excluded: number;
  used: number;
  /** values ≤ 0 not shown on a log scale */
  nonpositive_log?: number;
}

/** Descriptive statistics (ADR 0019 §4); a value that cannot be computed is null, never 0. */
export interface Describe {
  count: number;
  weight_sum: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  std: number | null;
  variance: number | null;
  cv: number | null;
  skewness: number | null;
  /** keys "5", "10", "25", "50", "75", "90", "95", "97.5", "99" */
  percentiles: Record<string, number | null>;
  /** by c > 0 */
  log: { count: number; geometric_mean: number | null; mean_ln: number | null; variance_ln: number | null } | null;
}

export interface HistogramBins {
  /** n + 1 edges, [a; b), the last bin closed */
  edges: number[];
  counts: number[];
  weights: number[];
  percent: number[];
  /** after each bin, values below the range included */
  cumulative_percent: number[];
  width: number;
  below: { count: number; weight: number };
  above: { count: number; weight: number };
}

export interface HistogramResult {
  field: string;
  log: boolean;
  weighted: boolean;
  counts: SelectCounts;
  stats: Describe;
  /** the same without weights (null when not weighted) */
  stats_unweighted: Describe | null;
  bins: HistogramBins;
  /** the conventions in Russian (percentiles, variance) */
  conventions: string;
}

/** Body — the settings without `view` (the core ignores `view` of stored settings too). */
export const computeHistogram = (body: Omit<HistogramSettings, "view">): Promise<HistogramResult> => api.post<HistogramResult>("/stats/histogram", body);

// ---- saved charts: /charts (ADR 0019 §2, §10) ----

export interface ChartRow {
  id: number;
  /** null — a swath (an object of the project) */
  set_id: number | null;
  kind: ChartKind | "striplog" | "swath";
  name: string;
  /** the name follows the settings until renamed by hand */
  auto_name: boolean;
  settings: Record<string, unknown>;
  version: number;
}

export function listCharts(setId?: number | null, kind?: string): Promise<{ charts: ChartRow[] }> {
  const q = new URLSearchParams();
  if (setId !== undefined && setId !== null) q.set("set_id", String(setId));
  if (kind) q.set("kind", kind);
  const qs = q.toString();
  return api.get<{ charts: ChartRow[] }>(`/charts${qs ? `?${qs}` : ""}`);
}

export const createChart = (body: { set_id: number | null; kind: string; name: string; settings: object; auto_name: boolean }): Promise<ChartRow> => api.post<ChartRow>("/charts", body);

/** `auto_name` — the name after the new settings while the flag stands; `name` — renamed by hand (the flag drops). */
export const updateChart = (id: number, body: { settings?: object; auto_name?: string; name?: string; expected_version: number }): Promise<ChartRow> => api.put<ChartRow>(`/charts/${id}`, body);

export const deleteChart = (id: number): Promise<unknown> => api.del(`/charts/${id}`);

/** The calculation of a saved chart by its settings and the current data (`chart.open`, the assistant). */
export const computeSaved = <T>(chartId: number): Promise<T> => api.post<T>(`/stats/chart/${chartId}`, {});

// ---- the newest answer only ----

export const CALC_DELAY_MS = 300;

export type Clock = { set: (fn: () => void, ms: number) => unknown; clear: (t: unknown) => void };
const REAL_CLOCK: Clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };

export interface Calculator<B> {
  /** New settings: sent `delay` ms after the last call; the same settings twice in a row are sent once. */
  request(body: B): void;
  /** No more answers are applied. */
  stop(): void;
}

/**
 * A chart's calculation (ADR 0019 §11): the newest settings go `delay` ms after the last edit; an answer that a
 * newer request has overtaken is dropped, as is everything after `stop`. `busy` tells a request is on its way.
 */
export function createCalculator<B, T>(load: (body: B) => Promise<T>, apply: (answer: T) => void, fail: (e: unknown) => void, busy: (on: boolean) => void = () => undefined, delay = CALC_DELAY_MS, clock: Clock = REAL_CLOCK): Calculator<B> {
  let seq = 0;
  let alive = true;
  let timer: unknown = null;
  let last = "";
  return {
    request(body) {
      const key = JSON.stringify(body);
      if (key === last) return;
      last = key;
      const mine = ++seq;
      if (timer !== null) clock.clear(timer);
      busy(true);
      timer = clock.set(() => {
        timer = null;
        load(body).then(
          (answer) => {
            if (!alive || mine !== seq) return;
            busy(false);
            apply(answer);
          },
          (e: unknown) => {
            if (!alive || mine !== seq) return;
            busy(false);
            last = ""; // the same settings may be asked again after a failure
            fail(e);
          },
        );
      }, delay);
    },
    stop() {
      alive = false;
      if (timer !== null) clock.clear(timer);
    },
  };
}
