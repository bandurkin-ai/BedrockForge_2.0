/**
 * Settings of the charts of Э13 (ADR 0019 §2, §9): JSON `v: 1` kept in `charts.settings`, sent as the body of
 * `POST /stats/<kind>` (the core reads the data keys and ignores `view`). Keys are the parameters of the commands
 * `chart.<kind>` — the arguments of a command are the settings. The table is its `name_key`, fields are names (a
 * reference survives a replaced table and `.bfggis`). The core checks kind, version, size, lists and ids; the
 * shape of the rest is ours: `normalizeSettings` keeps only valid values over the defaults (as `striplog`).
 * `chartName` — the name a chart follows until it is renamed by hand (ТЗ 7.28). Pure.
 */

export type ChartKind = "histogram" | "qq" | "dendrogram" | "scatter" | "box" | "probplot" | "contact" | "ternary" | "domains";
export const CHART_KINDS: readonly ChartKind[] = ["histogram", "qq", "dendrogram", "scatter", "box", "probplot", "contact", "ternary", "domains"];
export const isChartKind = (v: unknown): v is ChartKind => typeof v === "string" && (CHART_KINDS as readonly string[]).includes(v);

export type WeightMode = "none" | "length" | "field" | "length_field";
export type Negatives = "keep" | "half_abs" | "exclude";
export type CutMode = "exclude" | "cap";

/** Selection of values (ADR 0019 §4, `stats/select.py`): weight, values < 0, zeros, bottom and top cuts. */
export interface SampleFilter {
  weight: WeightMode;
  weight_field: string | null;
  negatives: Negatives;
  exclude_zero: boolean;
  bottom_cut: number | null;
  bottom_mode: CutMode;
  top_cut: number | null;
  top_mode: CutMode;
}

/** Interval lengths differ: length weighting is the usual choice for grades; the top cut caps (ТЗ 7.25). */
export const DEFAULT_FILTER: SampleFilter = { weight: "length", weight_field: null, negatives: "keep", exclude_zero: false, bottom_cut: null, bottom_mode: "exclude", top_cut: null, top_mode: "cap" };

/** The weight needs a field and has none: nothing can be computed yet. */
export const needsWeightField = (f: Pick<SampleFilter, "weight" | "weight_field">): boolean => (f.weight === "field" || f.weight === "length_field") && !f.weight_field;

interface Base {
  v: 1;
  set_id: number;
  /** `name_key` of the table; null — the set's sample table */
  table: string | null;
}

export type HistogramY = "percent" | "weight" | "count";

export interface HistogramView {
  y: HistogramY;
  cumulative: boolean;
  marks: { mean: boolean; median: boolean; p90: boolean; p975: boolean; cuts: boolean };
  color: string;
}

export interface HistogramSettings extends Base, SampleFilter {
  field: string | null;
  log: boolean;
  /** bin width (log10 on a log scale); with `bins` null both — auto */
  bin_width: number | null;
  bins: number | null;
  start: number | null;
  end: number | null;
  view: HistogramView;
}

export interface QQView {
  /** one scale on both axes (the 1:1 line is the diagonal) */
  equal: boolean;
  diagonal: boolean;
  /** marks P10, P50, P90 */
  labels: boolean;
  color: string;
}

/**
 * Q-Q (ТЗ 7.26): side X — `set_id`, `table`, `field`, the selection of values, `holes`; against a distribution or
 * (`distribution: "sample"`) side Y: `y_set_id` null — the same set, `y_table` null — the sample table of set Y,
 * `y_field` null — the field of X, `y_holes` empty — all holes. The selection of values (weight too; its field
 * from the table of Y) is common to both sides (core 13.15).
 */
export interface QQSettings extends Base, SampleFilter {
  field: string | null;
  distribution: "lognormal" | "normal" | "sample";
  y_set_id: number | null;
  y_table: string | null;
  y_field: string | null;
  holes: string[];
  y_holes: string[];
  /** quantiles of two samples at k / (n + 1), 3…999 */
  quantiles: number;
  /** logarithmic axes */
  log: boolean;
  view: QQView;
}

export interface DendrogramView {
  matrix: boolean;
  /** r in the cells of the matrix */
  values: boolean;
}

/** Dendrogram (ТЗ 7.27): `fields` empty — every component of the table; < 0 and zeros — as in the histogram. */
export interface DendrogramSettings extends Base {
  fields: string[];
  method: "pearson" | "spearman";
  log: boolean;
  /** length or none (ТЗ 7.27) */
  weight: WeightMode;
  negatives: Negatives;
  exclude_zero: boolean;
  missing: "pairwise" | "complete";
  linkage: "average" | "complete" | "single" | "weighted";
  distance: "1-r" | "1-abs";
  cut: number | null;
  view: DendrogramView;
}

export type Regression = "ols" | "rma" | "weighted" | "none";

export interface ScatterView {
  /** the 1:1 line */
  diagonal: boolean;
  /** the colour of the points without a colour field */
  color: string;
}

/**
 * Scatter (ТЗ 8.3): fields `x`, `y` of the table (`y_table` — another table, rows matched by the sample key),
 * `color` — a numeric or text field. The weight and the treatment of values < 0 and zeros go to both fields (a pair
 * with either left out is not drawn); log axes — by log10 (values ≤ 0 counted and not drawn).
 */
/** The selection of values of the scatter: that of the histogram (ТЗ 8.3 «веса и отсечки»), the cuts on both axes. */
export interface ScatterSettings extends Base, SampleFilter {
  x: string | null;
  y: string | null;
  y_table: string | null;
  color: string | null;
  log_x: boolean;
  log_y: boolean;
  /** the line drawn: МНК, RMA, weighted, none (the table lists all three) */
  regression: Regression;
  view: ScatterView;
}

/** Charts by domains (ТЗ 8.3): the field, the domain (a text field of the same or of `domain_table`), the selection of values. */
interface DomainBase extends Base, SampleFilter {
  field: string | null;
  domain: string | null;
  domain_table: string | null;
}

export interface BoxView {
  mean: boolean;
  outliers: boolean;
  color: string;
}

export interface BoxSettings extends DomainBase {
  /** the boxes and whiskers by logarithms on a log10 axis */
  log: boolean;
  view: BoxView;
}

export interface ProbPlotView {
  /** points joined by a line */
  lines: boolean;
}

export interface ProbPlotSettings extends DomainBase {
  view: ProbPlotView;
}
export type DomainsSettings = DomainBase;

export interface ContactView {
  /** n of the steps as bars under the means */
  counts: boolean;
  color: string;
}

/** The blocks beside the samples (Д-28): the model, the field of its estimate, `status_field` (0 — estimated), the domain field of the model (none — that of the samples). */
export interface ContactBlocks {
  model_id: number;
  field: string;
  status_field?: string;
  domain_field?: string;
}

export interface ContactSettings extends DomainBase {
  a: string | null;
  b: string | null;
  distance: number;
  step: number;
  wireframe_id: number | null;
  /** the profile of the blocks at the same contact; null — the samples only */
  blocks: ContactBlocks | null;
  view: ContactView;
}

/** The blocks of stored settings or arguments: the model and the field are needed, the names are text; else undefined. */
export function blocksOf(raw: unknown): ContactBlocks | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const r = raw as Record<string, unknown>;
  const name = (v: unknown): string | undefined => (typeof v === "string" && v.trim() !== "" ? v.trim() : undefined);
  const field = name(r.field);
  if (typeof r.model_id !== "number" || !Number.isInteger(r.model_id) || r.model_id < 1 || !field) return undefined;
  const status = name(r.status_field);
  const domain = name(r.domain_field);
  return { model_id: r.model_id, field, ...(status ? { status_field: status } : {}), ...(domain ? { domain_field: domain } : {}) };
}

export interface TernaryView {
  color: string;
}

export interface TernarySettings extends Base {
  a: string | null;
  b: string | null;
  c: string | null;
  color: string | null;
  view: TernaryView;
}

export interface SettingsOf {
  histogram: HistogramSettings;
  qq: QQSettings;
  dendrogram: DendrogramSettings;
  scatter: ScatterSettings;
  box: BoxSettings;
  probplot: ProbPlotSettings;
  contact: ContactSettings;
  ternary: TernarySettings;
  domains: DomainsSettings;
}
export type ChartSettings = SettingsOf[ChartKind];

/** The bars take the brand's Cobalt (docs/brand). */
export const DEFAULT_COLOR = "#1f6bff";

const base = (setId: number): Base => ({ v: 1, set_id: setId, table: null });
const domainBase = (setId: number): DomainBase => ({ ...base(setId), ...DEFAULT_FILTER, field: null, domain: null, domain_table: null });

export const DEFAULT_HISTOGRAM_VIEW: HistogramView = { y: "percent", cumulative: true, marks: { mean: true, median: true, p90: false, p975: false, cuts: true }, color: DEFAULT_COLOR };
export const DEFAULT_QQ_VIEW: QQView = { equal: true, diagonal: true, labels: true, color: DEFAULT_COLOR };
export const DEFAULT_DENDROGRAM_VIEW: DendrogramView = { matrix: true, values: true };
export const DEFAULT_SCATTER_VIEW: ScatterView = { diagonal: false, color: DEFAULT_COLOR };
export const DEFAULT_BOX_VIEW: BoxView = { mean: true, outliers: true, color: DEFAULT_COLOR };
export const DEFAULT_PROBPLOT_VIEW: ProbPlotView = { lines: false };
export const DEFAULT_CONTACT_VIEW: ContactView = { counts: true, color: DEFAULT_COLOR };
export const DEFAULT_TERNARY_VIEW: TernaryView = { color: DEFAULT_COLOR };

const VIEWS: Partial<Record<ChartKind, object>> = {
  histogram: DEFAULT_HISTOGRAM_VIEW,
  qq: DEFAULT_QQ_VIEW,
  dendrogram: DEFAULT_DENDROGRAM_VIEW,
  scatter: DEFAULT_SCATTER_VIEW,
  box: DEFAULT_BOX_VIEW,
  probplot: DEFAULT_PROBPLOT_VIEW,
  contact: DEFAULT_CONTACT_VIEW,
  ternary: DEFAULT_TERNARY_VIEW,
};

/** The default view of a kind (a fresh copy); null — the kind has none. */
export function defaultView(kind: ChartKind): Record<string, unknown> | null {
  const v = VIEWS[kind];
  return v ? (JSON.parse(JSON.stringify(v)) as Record<string, unknown>) : null;
}

export function defaultSettings<K extends ChartKind>(kind: K, setId: number): SettingsOf[K] {
  const all: SettingsOf = {
    histogram: { ...base(setId), ...DEFAULT_FILTER, field: null, log: false, bin_width: null, bins: null, start: null, end: null, view: defaultView("histogram") as unknown as HistogramView },
    // grades are checked against a lognormal distribution first (ТЗ 7.26: by default, log axes)
    qq: { ...base(setId), ...DEFAULT_FILTER, field: null, distribution: "lognormal", y_set_id: null, y_table: null, y_field: null, holes: [], y_holes: [], quantiles: 99, log: true, view: defaultView("qq") as unknown as QQView },
    // correlations of logarithms by default (ТЗ 7.27); «<0,01» written as −0,01 becomes half the limit (BF)
    dendrogram: { ...base(setId), fields: [], method: "pearson", log: true, weight: "length", negatives: "half_abs", exclude_zero: false, missing: "pairwise", linkage: "average", distance: "1-r", cut: null, view: defaultView("dendrogram") as unknown as DendrogramView },
    scatter: { ...base(setId), x: null, y: null, y_table: null, color: null, log_x: false, log_y: false, ...DEFAULT_FILTER, weight: "none", regression: "ols", view: defaultView("scatter") as unknown as ScatterView },
    box: { ...domainBase(setId), log: false, view: defaultView("box") as unknown as BoxView },
    probplot: { ...domainBase(setId), view: defaultView("probplot") as unknown as ProbPlotView },
    // ТЗ 8.3: D = 20 м, b = 1 м
    contact: { ...domainBase(setId), a: null, b: null, distance: 20, step: 1, wireframe_id: null, blocks: null, view: defaultView("contact") as unknown as ContactView },
    ternary: { ...base(setId), a: null, b: null, c: null, color: null, view: defaultView("ternary") as unknown as TernaryView },
    domains: domainBase(setId),
  };
  return all[kind];
}

// ---- normalisation of stored settings and of command arguments ----

const ENUMS: Record<string, readonly string[]> = {
  weight: ["none", "length", "field", "length_field"],
  negatives: ["keep", "half_abs", "exclude"],
  bottom_mode: ["exclude", "cap"],
  top_mode: ["exclude", "cap"],
  distribution: ["lognormal", "normal", "sample"],
  method: ["pearson", "spearman"],
  missing: ["pairwise", "complete"],
  linkage: ["average", "complete", "single", "weighted"],
  distance: ["1-r", "1-abs"],
  regression: ["ols", "rma", "weighted", "none"],
};
const INTEGER = new Set(["set_id", "y_set_id", "bins", "quantiles", "wireframe_id"]);
const NUMBER = new Set(["bin_width", "start", "end", "bottom_cut", "top_cut", "cut", "distance", "step"]);
/** Lists given to a command as text «a, b, c». */
const LIST_LIMIT: Record<string, number> = { holes: 10_000, y_holes: 10_000, fields: 100 };

/** «H1, H2  H3» → ["H1", "H2", "H3"] (commas, semicolons or spaces). */
export const parseList = (text: string): string[] => [...new Set(text.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean))];

/** A valid value of a key (its type, a known choice, a list), or undefined; null only where the default is null. */
function accept(key: string, value: unknown, fallback: unknown): unknown {
  if (value === undefined) return undefined;
  if (value === null) return fallback === null ? null : undefined;
  if (key === "blocks") return blocksOf(value);
  if (key in LIST_LIMIT) {
    const list = typeof value === "string" ? parseList(value) : Array.isArray(value) ? [...new Set(value.filter((x): x is string => typeof x === "string").map((x) => x.trim()).filter(Boolean))] : null;
    return list ? list.slice(0, LIST_LIMIT[key]) : undefined;
  }
  // `distance` is a choice of the dendrogram and a number of the contact analysis: the default tells which
  if (key in ENUMS && typeof fallback === "string") return typeof value === "string" && ENUMS[key].includes(value) ? value : undefined;
  if (INTEGER.has(key)) return typeof value === "number" && Number.isInteger(value) ? value : undefined;
  if (NUMBER.has(key)) return typeof value === "number" && Number.isFinite(value) ? value : undefined;
  if (typeof fallback === "boolean") return typeof value === "boolean" ? value : undefined;
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

/**
 * Settings of a kind from stored JSON or command arguments: every known key with a valid value replaces the
 * default; unknown keys and bad values are dropped; `set_id` falls back to the given set. `view` of a histogram
 * is merged key by key.
 */
export function normalizeSettings<K extends ChartKind>(kind: K, raw: unknown, setId: number): SettingsOf[K] {
  const out = defaultSettings(kind, setId) as unknown as Record<string, unknown>;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out as unknown as SettingsOf[K];
  const r = raw as Record<string, unknown>;
  for (const key of Object.keys(out)) {
    if (key === "v" || key === "view") continue;
    const got = accept(key, r[key], out[key]);
    if (got !== undefined) out[key] = got;
  }
  const view = defaultView(kind);
  if (view) out.view = normalizeView(view, r.view);
  return out as unknown as SettingsOf[K];
}

/** Choices of the view keys (others: flags, a colour `#rrggbb`, nested objects key by key). */
const VIEW_ENUMS: Record<string, readonly string[]> = { y: ["percent", "weight", "count"] };

/** The view over its defaults: each key of a valid type replaces the default, others are dropped. */
function normalizeView(d: Record<string, unknown>, raw: unknown): Record<string, unknown> {
  const r = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const out: Record<string, unknown> = {};
  for (const [key, def] of Object.entries(d)) {
    const v = r[key];
    if (def && typeof def === "object") out[key] = normalizeView(def as Record<string, unknown>, v);
    else if (typeof def === "boolean") out[key] = typeof v === "boolean" ? v : def;
    else if (key in VIEW_ENUMS) out[key] = typeof v === "string" && VIEW_ENUMS[key].includes(v) ? v : def;
    else out[key] = typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v) ? v.toLowerCase() : def;
  }
  return out;
}

/** Settings from the arguments of `chart.<kind>` (the same keys); without `set_id` — the active set. */
export function settingsFromArgs<K extends ChartKind>(kind: K, args: Record<string, unknown>, activeSet: number): SettingsOf[K] {
  const given = { ...args, set_id: args.set_id ?? activeSet };
  // the flat arguments of the commands (`ParamSchema` has no objects): the model and the fields of the profile of the blocks
  if (kind === "contact" && args.blocks_model_id !== undefined) Object.assign(given, { blocks: { model_id: args.blocks_model_id, field: args.blocks_field, status_field: args.blocks_status_field, domain_field: args.blocks_domain_field } });
  return normalizeSettings(kind, given, activeSet);
}

// ---- names after the settings (ТЗ 7.28; ADR 0019 §2) ----

const fieldName = (f: string | null | undefined): string => (!f ? "—" : f === "length" ? "длина" : f);

/** Russian plural: 1 поле, 2 поля, 5 полей. */
export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}

export function weightText(f: Pick<SampleFilter, "weight" | "weight_field">): string {
  switch (f.weight) {
    case "none":
      return "без веса";
    case "length":
      return "по длине";
    case "field":
      return `вес ${fieldName(f.weight_field)}`;
    case "length_field":
      return `длина × ${fieldName(f.weight_field)}`;
  }
}

const DISTRIBUTION: Record<QQSettings["distribution"], string> = { lognormal: "логнормальное", normal: "нормальное", sample: "две выборки" };

/** The name a chart follows until renamed by hand: «Гистограмма · AU · по длине», «Q-Q · AU · логнормальное». */
export function chartName<K extends ChartKind>(kind: K, settings: SettingsOf[K]): string {
  const s = settings as ChartSettings;
  switch (kind) {
    case "histogram": {
      const h = s as HistogramSettings;
      return ["Гистограмма", fieldName(h.field), weightText(h), ...(h.log ? ["log"] : [])].join(" · ");
    }
    case "qq": {
      const q = s as QQSettings;
      if (q.distribution !== "sample") return ["Q-Q", fieldName(q.field), DISTRIBUTION[q.distribution]].join(" · ");
      return ["Q-Q", `${fieldName(q.field)} / ${fieldName(q.y_field ?? q.field)}`, q.holes.length || q.y_holes.length ? "часть скважин" : DISTRIBUTION.sample].join(" · ");
    }
    case "dendrogram": {
      const d = s as DendrogramSettings;
      const n = d.fields.length;
      return ["Дендрограмма", n ? `${n} ${plural(n, "поле", "поля", "полей")}` : "все компоненты", d.method === "spearman" ? "Спирмен" : "Пирсон"].join(" · ");
    }
    case "scatter": {
      const c = s as ScatterSettings;
      return ["Рассеяние", `${fieldName(c.x)} / ${fieldName(c.y)}`].join(" · ");
    }
    case "box": {
      const b = s as BoxSettings;
      return ["Ящики с усами", fieldName(b.field), `по ${fieldName(b.domain)}`].join(" · ");
    }
    case "probplot": {
      const p = s as ProbPlotSettings;
      return ["Лог-вероятностный", fieldName(p.field), ...(p.domain ? [`по ${p.domain}`] : [])].join(" · ");
    }
    case "contact": {
      const c = s as ContactSettings;
      return ["Контакт", fieldName(c.field), `${c.a ?? "—"} | ${c.b ?? "—"}`].join(" · ");
    }
    case "ternary": {
      const t = s as TernarySettings;
      return ["Тернарная", [t.a, t.b, t.c].map(fieldName).join(" / ")].join(" · ");
    }
    case "domains": {
      const d = s as DomainsSettings;
      return ["По доменам", fieldName(d.field), fieldName(d.domain)].join(" · ");
    }
  }
  return "График";
}

/** Window title of a chart (ТЗ 7.0.12): «‹имя графика› · ‹набор›». */
export const windowTitle = (name: string, setName: string): string => (setName ? `${name} · ${setName}` : name);
