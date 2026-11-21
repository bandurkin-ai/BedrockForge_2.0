/**
 * The Q-Q plot (ТЗ 7.26; ADR 0019 §4, §11; перенос `QQWindow` / `QQChart` BF; answer of the core 13.15 — points
 * [x, y, P %]): the request from the settings, the points the chart draws (on log axes only those > 0 — the CSV has exactly them), the axis ranges (one common
 * range with «одинаковый масштаб»), the P10 / P50 / P90 marks, the table X / Y / отношение, the notes and the
 * assistant's text. A value that cannot be computed stays «—», never 0. Pure.
 */
import { computeQQ, createCalculator, type Calculator, type Describe, type QQResult, type QQSide, type QQTuple } from "../../modules/charts/api";
import { needsWeightField, type QQSettings } from "../../modules/charts/settings";
import { domainOf, formatNumber, type Domain } from "../core/axisScale";
import { csvText } from "../core/csv";

export type QQRequest = Omit<QQSettings, "view">;

export const QUANTILES_MIN = 3;
export const QUANTILES_MAX = 999;

/** The body of `POST /stats/qq` — the settings without the view; null while it cannot be computed. */
export function qqRequest(s: QQSettings): QQRequest | null {
  if (!s.field || needsWeightField(s)) return null;
  const { view: _view, ...data } = s;
  if (s.distribution !== "sample") return data;
  // side Y without a field compares the same field
  return { ...data, y_field: s.y_field ?? s.field, quantiles: Math.min(QUANTILES_MAX, Math.max(QUANTILES_MIN, Math.round(s.quantiles))) };
}

export function qqCalculator(apply: (r: QQResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: QQRequest) => Promise<QQResult> = computeQQ): Calculator<QQRequest> {
  return createCalculator(load, apply, fail, busy);
}

export interface QQPoint {
  /** probability, 0…1 */
  p: number;
  x: number;
  y: number;
}

/** [x, y, P %] → points with both values finite (and > 0 on log axes). */
function pointsOf(list: readonly QQTuple[] | undefined, log: boolean): QQPoint[] {
  const out: QQPoint[] = [];
  for (const [a, b, pc] of list ?? []) {
    if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) || (log && (a <= 0 || b <= 0))) continue;
    out.push({ p: pc / 100, x: a, y: b });
  }
  return out;
}

/** The points of the chart: both values finite (and > 0 on log axes). */
export const qqPoints = (r: Pick<QQResult, "points">, log: boolean): QQPoint[] => pointsOf(r.points, log);

/** The axis ranges: each over its own values, or one common range — then the 1:1 line is the diagonal. */
export function qqDomains(points: readonly QQPoint[], log: boolean, equal: boolean): { x: Domain; y: Domain } | null {
  const kind = log ? "log" : "linear";
  const x = domainOf(points.map((q) => q.x), kind, 0.04);
  const y = domainOf(points.map((q) => q.y), kind, 0.04);
  if (!x || !y) return null;
  if (!equal) return { x, y };
  const both = { min: Math.min(x.min, y.min), max: Math.max(x.max, y.max) };
  return { x: both, y: { ...both } };
}

/** The probabilities marked on the chart. */
export const LABELLED = [0.1, 0.5, 0.9];

/** The point nearest to each marked probability (P10, P50, P90); none without points. */
export function labelledPoints(points: readonly QQPoint[]): { p: number; index: number }[] {
  if (!points.length) return [];
  return LABELLED.map((p) => ({ p, index: points.reduce((best, q, i) => (Math.abs(q.p - p) < Math.abs(points[best].p - p) ? i : best), 0) }));
}

/** The marks P10, P50, P90: those of the core (exact P), else the nearest points of the chart. */
export function qqMarks(r: Pick<QQResult, "marks">, points: readonly QQPoint[], log: boolean): QQPoint[] {
  const own = pointsOf(r.marks, log);
  return own.length ? own : labelledPoints(points).map(({ p, index }) => ({ ...points[index], p }));
}

const DIST: Record<"lognormal" | "normal", string> = { lognormal: "логнормальное", normal: "нормальное" };

/** The titles of the axes: against a distribution X — its quantiles, Y — the sample. */
export function axisTitles(r: Pick<QQResult, "mode" | "distribution">, xName: string, yName: string): { x: string; y: string } {
  if (r.mode === "sample") return { x: xName, y: yName };
  return { x: `Квантили: ${DIST[r.distribution?.name ?? "lognormal"]} распределение`, y: xName };
}

/** CSV of the points (ТЗ 7.26 «Экспорт»): exactly the points the chart draws. */
export function qqCsv(r: QQResult, log: boolean, titles: { x: string; y: string }): string {
  return csvText(["Вероятность, %", titles.x, titles.y], qqPoints(r, log).map((q) => [Number((q.p * 100).toPrecision(12)), q.x, q.y]));
}

type StatRow = [string, (s: Describe) => number | null | undefined, boolean];

/** The rows of the table X / Y / отношение (BF); the ratio is not taken of the count. */
export const QQ_ROWS: StatRow[] = [
  ["Число проб", (s) => s.count, false],
  ["Среднее", (s) => s.mean, true],
  ["Медиана", (s) => s.median, true],
  ["Ст. отклонение", (s) => s.std, true],
  ["Коэф. вариации", (s) => s.cv, true],
  ["P10", (s) => s.percentiles["10"], true],
  ["P90", (s) => s.percentiles["90"], true],
  ["Минимум", (s) => s.min, true],
  ["Максимум", (s) => s.max, true],
];

/** Y / X; null when either is missing or X is 0 (never a division into 0 or ∞). */
export function ratio(x: number | null | undefined, y: number | null | undefined): number | null {
  if (x === null || x === undefined || y === null || y === undefined || !Number.isFinite(x) || !Number.isFinite(y) || x === 0) return null;
  return y / x;
}

/** [title, X, Y, Y / X] as text; without Y the last two are null. */
export function qqTable(x: QQSide, y: QQSide | null): [string, string, string | null, string | null][] {
  return QQ_ROWS.map(([title, f, withRatio]) => {
    const a = f(x.stats);
    const b = y ? f(y.stats) : null;
    return [title, formatNumber(a ?? null, 5), y ? formatNumber(b ?? null, 5) : null, y ? (withRatio ? formatNumber(ratio(a, b), 4) : "") : null];
  });
}

const n = (v: number) => v.toLocaleString("ru-RU");

/** The fit and the measure of the agreement, in words. */
export function qqNotes(r: QQResult): string[] {
  const out: string[] = [];
  const shown = r.points.length;
  if (r.mode === "theory") {
    const fit = r.distribution;
    const ln = fit?.name === "lognormal";
    if (fit) out.push(`${ln ? "среднее ln" : "среднее"} ${formatNumber(fit.mu, 5)}, ${ln ? "σ ln" : "σ"} ${formatNumber(fit.sigma, 5)}`);
    out.push(`PPCC ${formatNumber(r.ppcc, 5)} (1 — точное совпадение)`);
    if (fit?.dropped_nonpositive) out.push(`≤ 0 не учтены: ${n(fit.dropped_nonpositive)}`);
    const total = r.count ?? shown;
    out.push(total > shown ? `точек ${n(shown)} из ${n(total)} (прорежено, крайние оставлены)` : `точек ${n(shown)}`);
  } else {
    out.push(`квантилей ${n(shown)}`, `D Колмогорова–Смирнова ${formatNumber(r.ks, 4)} (0 — одинаковые распределения)`);
    if (r.extremes) out.push(`X ${formatNumber(r.extremes.x[0])}…${formatNumber(r.extremes.x[1])}, Y ${formatNumber(r.extremes.y[0])}…${formatNumber(r.extremes.y[1])}`);
  }
  return out;
}

/** The assistant's text (ADR 0019 §9): the name, samples, mean, median, CV of X; PPCC or D. */
export function qqSummary(name: string, r: QQResult): string {
  const st = r.x.stats;
  const fit = r.mode === "sample" ? `D ${formatNumber(r.ks, 4)}` : `PPCC ${formatNumber(r.ppcc, 4)}`;
  const y = r.y ? `; Y: проб ${n(r.y.stats.count)}, среднее ${formatNumber(r.y.stats.mean, 4)}` : "";
  return `${name}: проб ${n(st.count)}, среднее ${formatNumber(st.mean, 4)}, медиана ${formatNumber(st.median, 4)}, CV ${formatNumber(st.cv, 3)}${y}; ${fit}`;
}
