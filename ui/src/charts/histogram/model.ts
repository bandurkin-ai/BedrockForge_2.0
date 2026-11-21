/**
 * The histogram of samples (ТЗ 7.25; ADR 0019 §4, §11; перенос `HistogramWindow` / `HistogramChart` BF): the
 * request from the settings, the bin mode, the Y values, the marks (mean, median, P90, P97,5, cuts), the CSV of
 * the bins, the counters of what the selection left out, the rows of the statistics with and without weights,
 * the assistant's text. A statistic that cannot be computed stays «—», never 0. Pure.
 */
import { computeHistogram, type Calculator, type Describe, type HistogramResult, type SelectCounts } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import { needsWeightField, type HistogramSettings, type HistogramY } from "../../modules/charts/settings";
import { formatNumber } from "../core/axisScale";
import { csvText } from "../core/csv";

export const Y_TITLES: Record<HistogramY, string> = { percent: "Доля, %", weight: "Сумма весов", count: "Число проб" };

/** The value of every bar for the chosen Y axis. */
export function barValues(r: Pick<HistogramResult, "bins">, y: HistogramY): number[] {
  return y === "percent" ? r.bins.percent : y === "weight" ? r.bins.weights : r.bins.counts;
}

export type BinMode = "auto" | "width" | "count";

export const binModeOf = (s: Pick<HistogramSettings, "bin_width" | "bins">): BinMode => (s.bin_width !== null ? "width" : s.bins !== null ? "count" : "auto");

/** Another bin mode: only its own setting goes to the core (auto — neither); the count starts at 30. */
export function withBinMode(s: HistogramSettings, mode: BinMode, width: number | null = null): HistogramSettings {
  if (mode === "auto") return { ...s, bin_width: null, bins: null };
  if (mode === "width") return { ...s, bins: null, bin_width: width ?? s.bin_width };
  return { ...s, bin_width: null, bins: s.bins ?? 30 };
}

/** The body of `POST /stats/histogram` — the settings without the view; null while it cannot be computed. */
export type HistogramRequest = Omit<HistogramSettings, "view">;

export function histogramRequest(s: HistogramSettings): HistogramRequest | null {
  if (!s.field || needsWeightField(s)) return null;
  const { view: _view, ...data } = s;
  return data;
}

/** The window's calculation: 300 ms after the last edit, an overtaken answer dropped (ADR 0019 §11). */
export function histogramCalculator(apply: (r: HistogramResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: HistogramRequest) => Promise<HistogramResult> = computeHistogram): Calculator<HistogramRequest> {
  return dataCalculator(load, apply, fail, busy);
}

export interface Mark {
  value: number;
  label: string;
  dashed?: boolean;
}

/** Vertical marks of the chart: mean, median, P90, P97,5 and the cuts (ТЗ 7.25 «Вид»). */
export function histogramMarks(r: HistogramResult, s: HistogramSettings): Mark[] {
  const st = r.stats;
  const m = s.view.marks;
  const out: Mark[] = [];
  const add = (on: boolean, value: number | null | undefined, label: string, dashed = false) => {
    if (on && typeof value === "number" && Number.isFinite(value)) out.push({ value, label: `${label} ${formatNumber(value)}`, dashed });
  };
  add(m.mean, st.mean, "Среднее");
  add(m.median, st.median, "Медиана", true);
  add(m.p90, st.percentiles["90"], "P90", true);
  add(m.p975, st.percentiles["97.5"], "P97,5", true);
  add(m.cuts, s.bottom_cut, s.bottom_mode === "cap" ? "Подъём" : "Отсечка");
  add(m.cuts, s.top_cut, s.top_mode === "cap" ? "Урезка" : "Отсечка");
  return out;
}

/** The cumulative percent at the first edge: what lies below the range (the curve starts there). */
export function cumulativeStart(r: Pick<HistogramResult, "bins">): number {
  const { cumulative_percent: cum, percent } = r.bins;
  return cum.length ? Math.max(0, cum[0] - percent[0]) : 0;
}

/** CSV of the bins (ТЗ 7.25 «Экспорт»): edges, samples, weight, share, cumulative; out of range — rows of their own. */
export function histogramCsv(r: HistogramResult): string {
  const b = r.bins;
  const rows: (number | string | null)[][] = b.counts.map((c, i) => [b.edges[i], b.edges[i + 1], c, b.weights[i], b.percent[i], b.cumulative_percent[i]]);
  if (b.below.count) rows.unshift(["ниже диапазона", b.edges[0], b.below.count, b.below.weight, null, null]);
  if (b.above.count) rows.push([b.edges[b.edges.length - 1], "выше диапазона", b.above.count, b.above.weight, null, null]);
  return csvText(["От", "До", "Проб", "Сумма весов", "Доля, %", "Накоплено, %"], rows);
}

const n = (v: number) => v.toLocaleString("ru-RU");

/** What the selection of values left out or changed, in words (ADR 0019 §3: seen in the window). */
export function countsNotes(c: SelectCounts, s: Pick<HistogramSettings, "negatives">, r?: Pick<HistogramResult, "bins" | "log">): string[] {
  const out = [`строк ${n(c.rows)}, в расчёте ${n(c.used)}`];
  if (c.missing) out.push(`без значения ${n(c.missing)}`);
  if (c.bad_weight) out.push(`без веса или вес ≤ 0: ${n(c.bad_weight)}`);
  if (c.negative) out.push(`отрицательных ${n(c.negative)}${s.negatives === "exclude" ? " (исключены)" : s.negatives === "half_abs" ? " (|x| / 2)" : ""}`);
  if (c.zero_excluded) out.push(`нулей исключено ${n(c.zero_excluded)}`);
  if (c.bottom_excluded) out.push(`ниже отсечки исключено ${n(c.bottom_excluded)}`);
  if (c.bottom_capped) out.push(`поднято до отсечки ${n(c.bottom_capped)}`);
  if (c.capped) out.push(`урезано ${n(c.capped)}`);
  if (c.top_excluded) out.push(`выше отсечки исключено ${n(c.top_excluded)}`);
  if (c.nonpositive_log) out.push(`≤ 0 не показаны на логарифмической шкале: ${n(c.nonpositive_log)}`);
  if (r) {
    const { below, above, counts, width } = r.bins;
    if (below.count + above.count) out.push(`вне диапазона: ниже ${n(below.count)}, выше ${n(above.count)}`);
    out.push(`бинов ${counts.length}, ширина ${formatNumber(width)}${r.log ? " (log10)" : ""}`);
  }
  return out;
}

export type StatRow = [string, (s: Describe) => number | null | undefined];

/** The statistics of ТЗ 7.25: number, Σ weights, min, max, mean, median, σ, variance, CV, skewness, P5–P99, logs. */
export const STAT_ROWS: StatRow[] = [
  ["Число проб", (s) => s.count],
  ["Сумма весов", (s) => s.weight_sum],
  ["Минимум", (s) => s.min],
  ["Максимум", (s) => s.max],
  ["Среднее", (s) => s.mean],
  ["Медиана", (s) => s.median],
  ["Ст. отклонение", (s) => s.std],
  ["Дисперсия", (s) => s.variance],
  ["Коэф. вариации", (s) => s.cv],
  ["Асимметрия", (s) => s.skewness],
  ...["5", "10", "25", "75", "90", "95", "97.5", "99"].map((p): StatRow => [`P${p.replace(".", ",")}`, (s) => s.percentiles[p]]),
  ["Геом. среднее", (s) => s.log?.geometric_mean],
  ["Среднее ln", (s) => s.log?.mean_ln],
  ["Дисперсия ln", (s) => s.log?.variance_ln],
];

/** The rows of the table: [title, with weights (or the only value), without weights]. */
export function statsTable(main: Describe, plain: Describe | null): [string, string, string | null][] {
  return STAT_ROWS.map(([title, f]) => [title, formatNumber(f(main) ?? null, 5), plain ? formatNumber(f(plain) ?? null, 5) : null]);
}

/** The assistant's text (ADR 0019 §9): the name, the number of samples, mean, median, CV. */
export function histogramSummary(name: string, r: HistogramResult): string {
  const st = r.stats;
  return `${name}: проб ${n(st.count)}, среднее ${formatNumber(st.mean, 4)}, медиана ${formatNumber(st.median, 4)}, CV ${formatNumber(st.cv, 3)}${r.weighted ? " (с весом)" : ""}`;
}
