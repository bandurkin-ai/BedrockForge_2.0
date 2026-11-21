/**
 * The core's API of the charts of 8.3 (ADR 0019 §10; core 13.16, 13.17 — `stats/routes_more.py`,
 * `stats/routes_contact.py`): `POST /stats/scatter`, `/box`, `/probplot`, `/contact`, `/ternary`, `/domains`. The
 * body is the chart's settings without `view` (as `charts.settings`); an undefined number in an answer is null
 * (never NaN or 0). The shapes below are the contract the windows are written against (the report of 13.26–13.28).
 */
import { api } from "../../shell/api";
import type { Describe, SelectCounts } from "./api";
import type { BoxSettings, ContactSettings, DomainsSettings, ProbPlotSettings, ScatterSettings, TernarySettings } from "./settings";

type Body<S> = Omit<S, "view">;
export type Num = number | null;

/** The body each answer was computed from: a frame on its chart sends these settings back (13.33, `charts/selection`). */
const BODIES = new WeakMap<object, Record<string, unknown>>();
const remember =
  <T extends object>(body: object) =>
  (answer: T): T => {
    if (answer && typeof answer === "object") BODIES.set(answer, { ...body });
    return answer;
  };
/** The settings (without `view`) of the request whose answer `result` is; undefined — not from here. */
export const bodyOf = (result: object): Record<string, unknown> | undefined => BODIES.get(result);

/** Rows of the charts by domains: the selection's counts and those without a domain code. */
export type DomainCounts = SelectCounts & { no_domain: number };

/** A colour field: numbers — a continuous scale min…max; text — codes 0… into `labels` (≤ 300). */
export type ColorInfo = { kind: "number"; min: Num; max: Num } | { kind: "text"; labels: string[] };

// ---- scatter: POST /stats/scatter (ТЗ 8.3; ADR 0019 §4) ----

/** A line y = a + b·x in the space of the axes (log10 of a log axis). */
export interface Fit {
  a: Num;
  b: Num;
}

export interface ScatterCounts {
  /** rows of the table X (after `holes`) */
  rows: number;
  missing_x: number;
  missing_y: number;
  /** `y_table`: rows of X without a sample of the same key in the table Y */
  unmatched: number;
  bad_weight: number;
  negative: number;
  zero_excluded: number;
  /** the pairs a cut changed or left out (both axes; a pair counts once) */
  bottom_capped?: number;
  bottom_excluded?: number;
  capped?: number;
  top_excluded?: number;
  /** ≤ 0 on a log axis: counted, not drawn, not in the fits */
  nonpositive_log: number;
  used: number;
}

export interface ScatterDensity {
  nx: number;
  ny: number;
  /** the ends of the grid in data units; the cells are equal in the space of the axes */
  x: [number, number];
  y: [number, number];
  /** nx × ny counts, row-major from the bottom left: index = iy · nx + ix */
  counts: number[];
  max: number;
}

export interface ScatterResult {
  set_id: number;
  x_field: string;
  y_field: string;
  color_field: string | null;
  log_x: boolean;
  log_y: boolean;
  weighted: boolean;
  counts: ScatterCounts;
  /** ≤ 200 000 pairs — the points; more — the density grid 100 × 100 */
  mode: "points" | "density";
  /** points: data units, `row` — ids of `dh_rows` (the selection of 13.33), `c` — the colour value or code */
  x?: number[];
  y?: number[];
  row?: number[];
  c?: Num[] | null;
  color?: ColorInfo | null;
  density?: ScatterDensity;
  /** r over the pairs drawn (in the space of the axes); `spearman` — of ranks */
  pearson: Num;
  spearman: Num;
  n: number;
  /** all three fits; `weighted` — null without a weight */
  fits: { ols: Fit | null; rma: Fit | null; weighted: Fit | null };
  x_stats: { mean: Num; std: Num; min: Num; max: Num };
  y_stats: { mean: Num; std: Num; min: Num; max: Num };
  conventions?: string;
}

export const computeScatter = (body: Body<ScatterSettings>): Promise<ScatterResult> => api.post<ScatterResult>("/stats/scatter", body).then(remember<ScatterResult>(body));

// ---- box plot: POST /stats/box ----

/** A group of the charts by domains: a domain, the samples without a code, all samples (no domain field). */
export type GroupRole = "domain" | "none" | "all";

/** The head of the answers by domains (`_head` of the core). */
export interface DomainHead {
  set_id: number;
  field: string;
  domain: string | null;
  /** the table of the domain field when it is not the table of the samples (by the middle of a sample); null — the same */
  domain_table?: string | null;
  /** samples whose middle lies in no interval of `domain_table` */
  domain_outside?: number;
}

/** One box (a domain; `label` null — samples without a code, shown last). On a log scale the quartiles and the
 * whiskers are of the logarithms, given back in data units; `mean` is the weighted mean of the values. */
export interface BoxGroup {
  label: string | null;
  /** "domain" — a domain; "none" — the samples without a code; "all" — one group without a domain field */
  role?: GroupRole;
  n: number;
  weight_sum: number;
  mean: Num;
  /** on a log scale — the geometric mean (the mean of the logarithms back in data units); else null */
  geometric_mean?: Num;
  q1: Num;
  median: Num;
  q3: Num;
  /** the extreme values inside [Q1 − 1,5·IQR; Q3 + 1,5·IQR] */
  whisker_low: Num;
  whisker_high: Num;
  min: Num;
  max: Num;
  /** the outliers drawn (the extremes always among them) */
  outliers: number[];
  /** all outliers of the box: below the lower whisker, above the upper one, together */
  outliers_low?: number;
  outliers_high?: number;
  outliers_total: number;
  /** values ≤ 0 left out on a log scale */
  nonpositive?: number;
}

export interface BoxResult extends DomainHead {
  log: boolean;
  weighted: boolean;
  counts: DomainCounts & { nonpositive_log?: number };
  groups: BoxGroup[];
  /** outliers drawn on the chart (≤ 5000) and in all */
  outliers_shown: number;
  outliers_total: number;
  conventions?: string;
}

export const computeBox = (body: Body<BoxSettings>): Promise<BoxResult> => api.post<BoxResult>("/stats/box", body).then(remember<BoxResult>(body));

// ---- log-probability: POST /stats/probplot ----

export interface ProbSeries {
  label: string | null;
  role?: GroupRole;
  /** [value, P %], P = (C − w / 2) / W; ≤ 2000, the extremes kept */
  points: [number, number][];
  /** samples of the series (> 0) before thinning */
  count: number;
  nonpositive: number;
  /** the points were thinned (more than 2000) */
  thinned?: boolean;
  /** the correlation of ln(value) with the normal quantile of P — how straight the line is; null — not computed */
  r?: Num;
  stats: Pick<Describe, "mean" | "median" | "cv"> & { p90: Num };
}

export interface ProbPlotResult extends DomainHead {
  weighted: boolean;
  counts: DomainCounts;
  series: ProbSeries[];
  conventions?: string;
}

export const computeProbPlot = (body: Body<ProbPlotSettings>): Promise<ProbPlotResult> => api.post<ProbPlotResult>("/stats/probplot", body).then(remember<ProbPlotResult>(body));

// ---- contact analysis: POST /stats/contact (core 13.17) ----

export interface ContactSide {
  /** the domain (A, B) or «снаружи» / «внутри» of the wireframe */
  label: string;
  n: number;
  mean: Num;
}

export interface ContactResult extends DomainHead {
  wireframe_id: number | null;
  distance: number;
  step: number;
  weighted: boolean;
  counts: DomainCounts;
  /** contacts A ↔ B found and the holes with them */
  contacts: number;
  holes: number;
  /** 2·D / b + 1 edges from −D to D; side A — the steps below 0 */
  edges: number[];
  /** the mean of each step (weight — the length; null — no samples) and its samples */
  mean: Num[];
  n: number[];
  a: ContactSide;
  b: ContactSide;
  conventions?: string;
}

export const computeContact = (body: Body<ContactSettings>): Promise<ContactResult> => api.post<ContactResult>("/stats/contact", body);

// ---- ternary: POST /stats/ternary ----

export interface TernaryResult {
  set_id: number;
  fields: [string, string, string];
  color_field: string | null;
  /** `nonpositive_sum` = `negative` (a share below zero) + `zero_sum` (the sum is 0) */
  counts: { rows: number; missing: number; nonpositive_sum: number; negative?: number; zero_sum?: number; used: number };
  /** shares (a, b, c) / (a + b + c) of each point drawn; ≤ 200 000 (more — evenly thinned, `total` all) */
  a: number[];
  b: number[];
  c: number[];
  row: number[];
  total: number;
  color_values?: Num[] | null;
  color?: ColorInfo | null;
}

export const computeTernary = (body: Body<TernarySettings>): Promise<TernaryResult> => api.post<TernaryResult>("/stats/ternary", body);

// ---- statistics by domains: POST /stats/domains ----

export interface DomainRow {
  label: string | null;
  role?: GroupRole;
  n: number;
  length: number;
  mean: Num;
  /** by the field `DECL_W` of the table; null — no such field */
  decl_mean: Num;
  median: Num;
  std: Num;
  cv: Num;
  min: Num;
  max: Num;
  p10: Num;
  p50: Num;
  p90: Num;
}

export interface DomainsResult extends DomainHead {
  weighted: boolean;
  counts: DomainCounts;
  rows: DomainRow[];
  /** all samples together (label «Итого») */
  total: DomainRow;
  /** the declustering weight field used; null — none in the table (`decl_hint` says how to get it) */
  decl_field: string | null;
  decl_hint?: string | null;
  conventions?: string;
}

export const computeDomains = (body: Body<DomainsSettings>): Promise<DomainsResult> => api.post<DomainsResult>("/stats/domains", body);
