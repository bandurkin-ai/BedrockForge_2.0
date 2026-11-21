/**
 * «Урезка» (ТЗ 8.2.2; ADR 0018 §7, decision 8): `<F>_CUT = min(g, T)` and `<F>_CUT_F` in the source table, T — a value
 * or the percentile P weighted by length, by domain. The threshold is never applied by itself: the analysis
 * (`POST /composites/topcut/analysis`, synchronous) only shows the curves «порог → среднее, CV, потеря металла,
 * число урезанных» for P 90…99,9 %, the shares of metal by deciles and by percentiles of the top decile with the
 * marks (40 % / 10 %, set in the window), the quantiles of the log-probability curve; a pick on a curve changes the
 * threshold of the window only — «Применить» is the job. Pure: the answer made safe, the forecast of the report
 * «до/после» by the curve, the thresholds by domain as text, the axis of probabilities.
 */
import type { ResultRow } from "../api";
import { numberText, parseNumber, sourceBody, type FormValues, type SourceState, type ToolSpec } from "../params";

const by = (v: FormValues) => v.by;

export const topcut: ToolSpec = {
  id: "topcut",
  command: "composite.topcut",
  title: "Урезка",
  endpoint: "/topcut",
  preview: () => "topcut",
  source: { table: "one", fields: "one", domain: true },
  params: [
    {
      key: "by",
      label: "Порог",
      kind: "choice",
      default: "percentile",
      formOnly: true,
      choices: [
        { value: "percentile", label: "перцентиль" },
        { value: "value", label: "значение" },
        { value: "thresholds", label: "по доменам" },
      ],
    },
    { key: "percentile", label: "Перцентиль P", kind: "number", unit: "%", min: 90, max: 99.99, default: 99, when: (v) => by(v) === "percentile" },
    { key: "value", label: "Значение T", kind: "number", min: 0, when: (v) => by(v) === "value" },
    { key: "thresholds", label: "Пороги по доменам", kind: "text", when: (v) => by(v) === "thresholds", hint: "КОД=значение; …" },
  ],
  fromArgs: (args, values) => ({ ...values, by: args.value !== undefined ? "value" : args.thresholds !== undefined ? "thresholds" : "percentile" }),
  required: ["set_id", "table", "field"],
  writes: "fields",
  note: "Поля ‹F›_CUT и ‹F›_CUT_F пишутся в таблицу источника; пусто остаётся пустым.",
};

// ---- the answer of the analysis (core `composites/topcut.py::topcut_analysis`) ----

export type Num = number | null;

export interface Curves {
  p: Num[];
  threshold: Num[];
  mean: Num[];
  cv: Num[];
  loss_pct: Num[];
  n_cut: Num[];
}

/** A slice of cumulative length (`from`, `to` — % of the length): grades, metal and its share of the whole. */
export interface Slice {
  from: number;
  to: number;
  min: Num;
  max: Num;
  mean: Num;
  metal: Num;
  share_pct: Num;
}

export interface DomainAnalysis {
  /** "" — the rows without a domain */
  domain: string;
  n: number;
  length: number;
  mean: Num;
  cv: Num;
  min: Num;
  max: Num;
  metal: Num;
  curves: Curves;
  deciles: Slice[];
  top: Slice[];
  top_decile_pct: Num;
  top_percentile_pct: Num;
  quantiles: { p: Num[]; z: Num[]; value: Num[] };
}

export interface TopcutAnalysis {
  field: string;
  domains: DomainAnalysis[];
}

const num = (v: unknown): Num => (typeof v === "number" && Number.isFinite(v) ? v : null);
const nums = (v: unknown): Num[] => (Array.isArray(v) ? v.map(num) : []);
const slices = (v: unknown): Slice[] =>
  Array.isArray(v)
    ? v.map((r: Record<string, unknown>) => ({ from: num(r?.from) ?? 0, to: num(r?.to) ?? 0, min: num(r?.min), max: num(r?.max), mean: num(r?.mean), metal: num(r?.metal), share_pct: num(r?.share_pct) }))
    : [];

/** The answer made safe to draw: a part the core leaves out (a domain without samples) is empty, never zero. */
export function toAnalysis(raw: Record<string, unknown>): TopcutAnalysis {
  const domains = Array.isArray(raw.domains) ? (raw.domains as Record<string, unknown>[]) : [];
  return {
    field: String(raw.field ?? ""),
    domains: domains.map((d) => {
      const c = (d.curves ?? {}) as Record<string, unknown>;
      const q = (d.quantiles ?? {}) as Record<string, unknown>;
      return {
        domain: typeof d.domain === "string" ? d.domain : d.domain === null || d.domain === undefined ? "" : String(d.domain),
        n: num(d.n) ?? 0,
        length: num(d.length) ?? 0,
        mean: num(d.mean),
        cv: num(d.cv),
        min: num(d.min),
        max: num(d.max),
        metal: num(d.metal),
        curves: { p: nums(c.p), threshold: nums(c.threshold), mean: nums(c.mean), cv: nums(c.cv), loss_pct: nums(c.loss_pct), n_cut: nums(c.n_cut) },
        deciles: slices(d.deciles),
        top: slices(d.top),
        top_decile_pct: num(d.top_decile_pct),
        top_percentile_pct: num(d.top_percentile_pct),
        quantiles: { p: nums(q.p), z: nums(q.z), value: nums(q.value) },
      };
    }),
  };
}

/** «без домена» for the group of rows without one. */
export const domainLabel = (name: string): string => name || "без домена";

/** The body of the analysis: the source of the tool (`set_id`, `table`, `field`, `domain`); a string — what is missing. */
export const analysisBody = (src: SourceState): Record<string, unknown> | string => sourceBody(topcut.source, src);

// ---- the marks 40 % / 10 % (set in the window) ----

export interface Limits {
  decile: number;
  percentile: number;
}

export const LIMITS: Limits = { decile: 40, percentile: 10 };

/** The marks of a domain by the limits of the window: the top decile holds more than `decile` % of the metal, the
 * top percentile — more than `percentile` %; no share — no mark. */
export function marks(d: Pick<DomainAnalysis, "top_decile_pct" | "top_percentile_pct">, limits: Limits): { decile: boolean; percentile: boolean } {
  return { decile: d.top_decile_pct !== null && d.top_decile_pct > limits.decile, percentile: d.top_percentile_pct !== null && d.top_percentile_pct > limits.percentile };
}

// ---- the threshold of the window by domain ----

/** «ОК=30; ПР=12,5; =5» → {ОК: "30", ПР: "12,5", "": "5"} (as typed; "" — the rows without a domain); a part
 * without «=» — a reason. */
export function parseThresholds(text: string): Record<string, string> | string {
  const out: Record<string, string> = {};
  for (const part of text.split(/[;\n]/).map((x) => x.trim()).filter(Boolean)) {
    const at = part.lastIndexOf("=");
    if (at < 0) return `«Пороги по доменам»: «${part}» — нужно «КОД=значение»`;
    out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return out;
}

/** {ОК: "30", ПР: ""} → «ОК=30» (an empty threshold — the domain is not cut). */
export const thresholdsText = (by: Record<string, string>): string =>
  Object.entries(by)
    .filter(([, v]) => v.trim())
    .map(([k, v]) => `${k}=${v.trim()}`)
    .join("; ");

/** How T of a domain is set in the window: the percentile P, the value T, or none (the domain is not cut). */
export type DomainThreshold = { p: number } | { value: number } | null;

export function domainThreshold(values: FormValues, domain: string): DomainThreshold {
  const text = (k: string) => (typeof values[k] === "string" ? (values[k] as string) : "");
  if (values.by === "value") {
    const v = parseNumber(text("value"));
    return v !== null && v > 0 ? { value: v } : null;
  }
  if (values.by === "thresholds") {
    const by = parseThresholds(text("thresholds"));
    const v = typeof by === "string" ? null : parseNumber(by[domain] ?? "");
    return v !== null && v > 0 ? { value: v } : null;
  }
  const p = parseNumber(text("percentile"));
  return p !== null ? { p } : null;
}

/** Where `target` lies in the non-decreasing `xs`: the index i and the fraction f between xs[i] and xs[i + 1];
 * outside the range or no values — null. */
export function locate(xs: readonly Num[], target: number): { i: number; f: number } | null {
  for (let i = 0; i < xs.length; i += 1) {
    const a = xs[i];
    if (a === null) continue;
    if (Math.abs(a - target) <= 1e-9 * Math.max(1, Math.abs(a))) return { i, f: 0 };
    const b = xs[i + 1];
    if (b !== null && b !== undefined && a < target && target < b) return { i, f: (target - a) / (b - a) };
  }
  return null;
}

const at = (ys: readonly Num[], i: number, f: number): Num => {
  const a = ys[i] ?? null;
  if (f === 0) return a;
  const b = ys[i + 1] ?? null;
  return a === null || b === null ? null : a + f * (b - a);
};

export interface Forecast {
  threshold: Num;
  /** the percentile of T on the curve */
  p: Num;
  n_cut: Num;
  cut_pct: Num;
  mean: Num;
  cv: Num;
  loss_pct: Num;
  /** false — between two points of the curve (linear, «≈») */
  exact: boolean;
}

/** The report «после» of a domain for the threshold of the window, read off the curves (P 90…99,9 %): a point of
 * the curve — exactly, between two points — linearly; outside the curves or no threshold — null (the report comes
 * with «Применить»). */
export function forecast(d: DomainAnalysis, t: DomainThreshold): Forecast | null {
  if (!t || !d.n) return null;
  const c = d.curves;
  const hit = "p" in t ? locate(c.p, t.p) : locate(c.threshold, t.value);
  if (!hit) return null;
  const nCut = at(c.n_cut, hit.i, hit.f);
  const n = nCut === null ? null : Math.round(nCut);
  return {
    threshold: "value" in t ? t.value : at(c.threshold, hit.i, hit.f),
    p: "p" in t ? t.p : at(c.p, hit.i, hit.f),
    n_cut: n,
    cut_pct: n === null || !d.n ? null : (100 * n) / d.n,
    mean: at(c.mean, hit.i, hit.f),
    cv: at(c.cv, hit.i, hit.f),
    loss_pct: at(c.loss_pct, hit.i, hit.f),
    exact: hit.f === 0,
  };
}

/** The P of the curves nearest to `p` (a pick on a curve). */
export function nearestP(c: Curves, p: number): number | null {
  let best: number | null = null;
  for (let i = 0; i < c.p.length; i += 1) {
    const v = c.p[i];
    if (v !== null && c.threshold[i] !== null && (best === null || Math.abs(v - p) < Math.abs(best - p))) best = v;
  }
  return best;
}

/**
 * A pick of P on a curve of a domain → the values of the form (nothing is sent, nothing is applied): «перцентиль» —
 * P itself; «значение» — T of the curve at P; «по доменам» — T of this domain at P, the other domains stay.
 */
export function pickValues(values: FormValues, d: DomainAnalysis, p: number): FormValues {
  const near = nearestP(d.curves, p);
  if (near === null) return values;
  const i = d.curves.p.indexOf(near);
  const t = d.curves.threshold[i];
  if (values.by === "value") return t === null ? values : { ...values, value: numberText(t) };
  if (values.by === "thresholds") {
    if (t === null) return values;
    const by = parseThresholds(typeof values.thresholds === "string" ? values.thresholds : "");
    return { ...values, thresholds: thresholdsText({ ...(typeof by === "string" ? {} : by), [d.domain]: numberText(t) }) };
  }
  return { ...values, by: "percentile", percentile: numberText(near) };
}

/** The P of the window to mark on the curves of a domain: P itself, or the P of T on the curve. */
export function markedP(d: DomainAnalysis, t: DomainThreshold): number | null {
  if (!t) return null;
  if ("p" in t) return t.p;
  return forecast(d, t)?.p ?? null;
}

// ---- the log-probability curve: the axis of probabilities ----

/** The normal score of a probability 0 < p < 1 (P. J. Acklam, |ε| < 1,2·10⁻⁹); outside — NaN. */
export function normalZ(p: number): number {
  if (!(p > 0 && p < 1)) return Number.NaN;
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425;
  if (p < lo) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - lo) return -normalZ(1 - p);
  const q = p - 0.5;
  const r = q * q;
  return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/** The ticks of the probability axis, % → z. */
export const PROB_TICKS = [0.1, 1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9].map((p) => ({ value: normalZ(p / 100), label: numberText(p) }));

// ---- the last applied top cut of a table and field (its report «до/после») ----

/** The newest fresh-or-stale result of `op` on the source table `table` (its key) and `field`. */
export function latestResult(rows: readonly ResultRow[], op: string, table: string | null, field: string | null): ResultRow | null {
  if (!table || !field) return null;
  let best: ResultRow | null = null;
  for (const r of rows) {
    if (r.op !== op || String(r.params?.table ?? "") !== table || r.params?.field !== field) continue;
    if (!best || (r.created_at ?? "") > (best.created_at ?? "") || ((r.created_at ?? "") === (best.created_at ?? "") && r.id > best.id)) best = r;
  }
  return best;
}

/** A row of the report of the job (core `topcut.report.domains[]`, `total`). */
export interface ReportRow {
  domain: string | null;
  threshold: Num;
  n: number;
  n_cut: number;
  cut_pct: Num;
  mean_before: Num;
  mean_after: Num;
  cv_before: Num;
  cv_after: Num;
  max_before: Num;
  max_after: Num;
  loss_pct: Num;
}

/** The rows of the report of an applied top cut: the domains, then «Всего» (domain null); none — empty. */
export function reportRows(report: Record<string, unknown> | null | undefined): ReportRow[] {
  const row = (r: Record<string, unknown>, total: boolean): ReportRow => ({
    domain: total ? null : typeof r.domain === "string" ? r.domain : "",
    threshold: num(r.threshold),
    n: num(r.n) ?? 0,
    n_cut: num(r.n_cut) ?? 0,
    cut_pct: num(r.cut_pct) ?? (num(r.n) ? (100 * (num(r.n_cut) ?? 0)) / (num(r.n) as number) : null),
    mean_before: num(r.mean_before),
    mean_after: num(r.mean_after),
    cv_before: num(r.cv_before),
    cv_after: num(r.cv_after),
    max_before: num(r.max_before),
    max_after: num(r.max_after),
    loss_pct: num(r.loss_pct),
  });
  const domains = Array.isArray(report?.domains) ? (report.domains as Record<string, unknown>[]).map((r) => row(r, false)) : [];
  const total = report?.total && typeof report.total === "object" ? row(report.total as Record<string, unknown>, true) : null;
  return total && domains.length > 1 ? [...domains, total] : domains;
}
