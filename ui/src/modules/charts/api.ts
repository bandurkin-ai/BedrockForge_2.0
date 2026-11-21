/**
* The core's API of the charts (ADR 0019 §10): the fields of a set's tables (`GET /stats/fields`, core 13.14), the
 * calculations `POST /stats/<kind>` and `POST /stats/chart/{id}` (core 13.15; body — the chart's settings), the saved charts `/charts`. `POST /stats/<kind>` (body — the chart's settings), the saved charts `/charts`.
 * `createCalculator` sends the newest settings 300 ms after the last edit and drops an answer that a newer
 * request has overtaken (ADR 0019 §11 «Отклик»).
 */
import { api, CoreError } from "../../shell/api";
import type { ChartKind, DendrogramSettings, HistogramSettings, QQSettings } from "./settings";

// ---- fields of the set's tables: GET /stats/fields?set_id=&table= (core 13.14, `stats/source.fields_list`) ----

/** A numeric field of a table as the core gives it: `role` — component, value, attribute, length… */
export interface NumberAnswer {
  name: string;
  unit: string | null;
  role: string;
}

/** A text field: `values` — how many distinct, `labels` — the first ≤ 300 (null both without `table` in the query). */
export interface TextAnswer {
  name: string;
  role: string;
  values: number | null;
  labels: string[] | null;
  more: boolean;
}

export interface TableAnswer {
  id: number;
  name: string;
  name_key: string;
  kind: string;
  rows: number;
  rev: number;
  numbers: NumberAnswer[];
  texts: TextAnswer[];
}

/** The answer of the core: every ready interval table of the set (assay first), or only the asked one. */
export interface FieldsAnswer {
  set_id: number;
  tables: TableAnswer[];
}

export interface StatsTable {
  /** `name_key` of the table (what the settings keep) */
  key: string;
  name: string;
  kind: string;
  rows: number;
  /** numeric fields with the role «component» */
  components: number;
}

export interface NumericField {
  name: string;
  /** a component, another numeric field (attribute, value) or `length` — the interval length */
  kind: "component" | "attribute" | "length";
  unit: string | null;
  /** the role the core gave */
  role: string;
}

export interface TextField {
  name: string;
  /** distinct values; null — not asked */
  count: number | null;
  /** ≤ 300 of them */
  values: string[];
  more: boolean;
}

/** What the windows use: the tables of the set and the fields of the chosen one. */
export interface StatsFields {
  tables: StatsTable[];
  /** `name_key` of the chosen table (the asked one or the set's first — assay first); null — the set has none */
  table: string | null;
  numeric: NumericField[];
  text: TextField[];
}

/** The key of a name as the core's `name_key` (NFC + casefold). */
export const nameKey = (name: string): string => name.trim().normalize("NFC").toLowerCase();

const numericKind = (role: string): NumericField["kind"] => (role === "component" ? "component" : role === "length" ? "length" : "attribute");

/** The table a setting means: its `name_key`, its name or its id; null — the first table of the set. */
export function findTable<T extends Pick<TableAnswer, "id" | "name" | "name_key">>(tables: readonly T[], table: string | number | null | undefined): T | null {
  if (table === null || table === undefined || table === "") return tables[0] ?? null;
  const key = nameKey(String(table));
  return tables.find((t) => t.name_key === key || nameKey(t.name) === key) ?? tables.find((t) => String(t.id) === String(table)) ?? null;
}

/** The answer of the core as the windows use it; `table` — the chosen one (null — the first). */
export function fieldsView(answer: FieldsAnswer, table: string | null | undefined): StatsFields {
  const tables = answer.tables.map((t): StatsTable => ({ key: t.name_key, name: t.name, kind: t.kind, rows: t.rows, components: t.numbers.filter((f) => f.role === "component").length }));
  const t = findTable(answer.tables, table);
  if (!t) return { tables, table: null, numeric: [], text: [] };
  return {
    tables,
    table: t.name_key,
    numeric: t.numbers.map((f) => ({ name: f.name, kind: numericKind(f.role), unit: f.unit, role: f.role })),
    text: t.texts.map((f) => ({ name: f.name, count: f.values, values: f.labels ?? [], more: f.more })),
  };
}

const tableMissing = (table: string) => new CoreError(404, "table_not_found", `В наборе нет таблицы «${table.slice(0, 80)}».`, "Выберите таблицу из списка набора.");

/** Answers of `GET /stats/fields` by set (and table for the text values); dropped on an edit of the data. */
const cache = new Map<string, Promise<FieldsAnswer>>();

function fetchFields(setId: number, table?: string): Promise<FieldsAnswer> {
  const q = new URLSearchParams({ set_id: String(setId) });
  if (table) q.set("table", table);
  const key = q.toString();
  let p = cache.get(key);
  if (!p) {
    p = api.get<FieldsAnswer>(`/stats/fields?${key}`);
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return p;
}

/** Forget the fields of a set (all sets without one): `drillholes.changed`, `objects.changed`, another project. */
export function invalidateFields(setId?: number): void {
  if (setId === undefined) return cache.clear();
  for (const k of [...cache.keys()]) if (new URLSearchParams(k).get("set_id") === String(setId)) cache.delete(k);
}

/**
 * The tables of the set and the fields of `table` (null — the first table). With `texts` the text fields carry
 * their values (a second request with the table). A table not in the set — 404 `table_not_found`.
 */
export async function loadFields(setId: number, table?: string | null, opts: { texts?: boolean } = {}): Promise<StatsFields> {
  const all = await fetchFields(setId);
  const view = fieldsView(all, table);
  if (table && view.table === null) throw tableMissing(table);
  if (!opts.texts || view.table === null || !view.text.length) return view;
  const one = await fetchFields(setId, view.table);
  return { ...view, text: fieldsView(one, view.table).text };
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

// ---- Q-Q: POST /stats/qq (ТЗ 7.26; ADR 0019 §4) ----

/** One side of a Q-Q plot as the core read it. */
export interface QQSide {
  set_id: number;
  /** `name_key` of the table read */
  table: string;
  field: string;
  unit: string | null;
  weighted: boolean;
  /** holes of the side: those listed and found, or all */
  holes: number;
  counts: SelectCounts;
  stats: Describe;
}

/** The fitted distribution: weighted mean and σ (of ln for the lognormal one). */
export interface QQFit {
  distribution: "lognormal" | "normal";
  mu: number | null;
  sigma: number | null;
  /** lognormal: values ≤ 0 left out of the logarithms */
  nonpositive: number;
}

export interface QQResult {
  distribution: QQSettings["distribution"];
  x: QQSide;
  /** null against a distribution */
  y: QQSide | null;
  /**
   * The points as columns: `p` — the probability (0…1); against a distribution `x` — the quantile of the fitted
   * distribution in the units of the field (lognormal — exp(μ + σ·Φ⁻¹(P))), `y` — the sample at P = (C − w / 2) / W;
   * two samples — `x`, `y` — the quantiles of X and Y at k / (n + 1). A value that cannot be computed is null.
   */
  points: { p: number[]; x: (number | null)[]; y: (number | null)[] };
  /** points before thinning (the samples used, or the quantiles) */
  total: number;
  /** more than 2000 samples: interpolated at 2000 equal steps of P, the extremes kept */
  thinned: boolean;
  fit: QQFit | null;
  /** against a distribution: the correlation of the points */
  ppcc: number | null;
  /** two samples: Kolmogorov–Smirnov D of the weighted distributions */
  ks: number | null;
  conventions: string;
}

/** Body — the settings without `view`. */
export const computeQQ = (body: Omit<QQSettings, "view">): Promise<QQResult> => api.post<QQResult>("/stats/qq", body);

// ---- dendrogram: POST /stats/dendrogram (ТЗ 7.27) ----

export interface DendrogramResult {
  /** the fields clustered, in the order of the request (indices below point into it) */
  fields: string[];
  units: (string | null)[];
  /** r (n × n, 1 on the diagonal); null — fewer than 3 common rows */
  correlation: (number | null)[][];
  /** common rows of each pair */
  pairs: number[][];
  /** rows of the table; `used` — those that took part (complete rows with `missing: "complete"`) */
  rows: number;
  used: number;
  /** `log`: values ≤ 0 left out of the logarithms */
  nonpositive_log: number;
  /** scipy `linkage`: [a, b, distance, size] per merge (n − 1) */
  linkage: [number, number, number, number][];
  /** the leaves from top to bottom (indices into `fields`) */
  order: number[];
  /** scipy `dendrogram` coordinates: leaves at 5, 15, 25…; distance 0 at the leaves */
  icoord: number[][];
  dcoord: number[][];
  max_distance: number;
  /** groups at `cut` (`fcluster`, criterion «distance»): 1… per field; null — no cut */
  groups: number[] | null;
  conventions: string;
}

export const computeDendrogram = (body: Omit<DendrogramSettings, "view">): Promise<DendrogramResult> => api.post<DendrogramResult>("/stats/dendrogram", body);

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

export const getChart = (id: number): Promise<ChartRow> => api.get<ChartRow>(`/charts/${id}`);

/** The answer of `POST /stats/chart/{id}`: the chart as stored and its calculation by the current data. */
export interface SavedAnswer<T> {
  chart: ChartRow;
  result: T;
}

/** The calculation of a saved chart by its settings and the current data (`chart.open`, the assistant). */
export const computeSaved = <T>(chartId: number): Promise<SavedAnswer<T>> => api.post<SavedAnswer<T>>(`/stats/chart/${chartId}`, {});

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
