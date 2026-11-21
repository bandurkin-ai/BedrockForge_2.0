/**
 * The scatter plot (ТЗ 8.3; ADR 0019 §4, §11): the request from the settings, the ranges of the axes, the lines of
 * the fits (straight in the space of the axes — log10 of a log axis) and 1:1, the density cells, the colours of the
 * points by a field, the table of r and the fits, the CSV, the notes and the assistant's text. A value that cannot be
 * computed stays «—», never 0. Pure.
 */
import type { Calculator } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import { computeScatter, type Fit, type ScatterDensity, type ScatterResult } from "../../modules/charts/apiMore";
import { needsWeightField, type Regression, type ScatterSettings } from "../../modules/charts/settings";
import { axisValue, domainOf, formatNumber, type AxisKind, type Domain } from "../core/axisScale";
import { csvText } from "../core/csv";
import type { Pt } from "../core/lasso";
import { densityColor, MISSING_COLOR, rampColor, rgba32, seriesColor } from "../core/palette";
import type { AxisMap, RasterRect } from "../core/PointsCanvas";

export type ScatterRequest = Omit<ScatterSettings, "view">;

/** The body of `POST /stats/scatter`; null while it cannot be computed (no fields, no weight field). */
export function scatterRequest(s: ScatterSettings): ScatterRequest | null {
  if (!s.x || !s.y || needsWeightField(s)) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function scatterCalculator(apply: (r: ScatterResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: ScatterRequest) => Promise<ScatterResult> = computeScatter): Calculator<ScatterRequest> {
  return dataCalculator(load, apply, fail, busy);
}

export const axisKinds = (s: Pick<ScatterSettings, "log_x" | "log_y">): { kx: AxisKind; ky: AxisKind } => ({ kx: s.log_x ? "log" : "linear", ky: s.log_y ? "log" : "linear" });

/** The ranges of the axes: over the points, or the ends of the density grid. */
export function scatterDomains(r: ScatterResult, kx: AxisKind, ky: AxisKind): { x: Domain; y: Domain } | null {
  if (r.mode === "density" && r.density) {
    const x = domainOf(r.density.x, kx, 0.02);
    const y = domainOf(r.density.y, ky, 0.02);
    return x && y ? { x, y } : null;
  }
  const x = domainOf(r.x ?? [], kx, 0.04);
  const y = domainOf(r.y ?? [], ky, 0.04);
  return x && y ? { x, y } : null;
}

const fromAxis = (kind: AxisKind, t: number) => (kind === "log" ? 10 ** t : t);

/**
 * The segment of y = a + b·x (in the space of the axes) inside both ranges, in data units; null — no line or it
 * misses the plot.
 */
export function fitSegment(fit: Fit | null | undefined, dx: Domain, dy: Domain, kx: AxisKind, ky: AxisKind): [Pt, Pt] | null {
  if (!fit || fit.a === null || fit.b === null || !Number.isFinite(fit.a) || !Number.isFinite(fit.b)) return null;
  const x0 = axisValue(kx, dx.min);
  const x1 = axisValue(kx, dx.max);
  const y0 = axisValue(ky, dy.min);
  const y1 = axisValue(ky, dy.max);
  if (x0 === null || x1 === null || y0 === null || y1 === null) return null;
  const { a, b } = fit;
  let lo = x0;
  let hi = x1;
  if (b !== 0) {
    // the x where the line crosses the bottom and the top of the plot
    const xa = (y0 - a) / b;
    const xb = (y1 - a) / b;
    lo = Math.max(lo, Math.min(xa, xb));
    hi = Math.min(hi, Math.max(xa, xb));
  } else if (a < y0 || a > y1) return null;
  if (!(hi > lo)) return null;
  return [
    { x: fromAxis(kx, lo), y: fromAxis(ky, a + b * lo) },
    { x: fromAxis(kx, hi), y: fromAxis(ky, a + b * hi) },
  ];
}

/** The 1:1 line y = x inside both ranges (a curve when only one axis is log): points in data units, ≥ 2 or none. */
export function diagonalPoints(dx: Domain, dy: Domain, kx: AxisKind, ky: AxisKind, steps = 64): Pt[] {
  const lo = Math.max(dx.min, dy.min);
  const hi = Math.min(dx.max, dy.max);
  if (!(hi > lo)) return [];
  if (kx === ky) return [{ x: lo, y: lo }, { x: hi, y: hi }];
  const a = axisValue(kx, lo);
  const b = axisValue(kx, hi);
  if (a === null || b === null) return [];
  const out: Pt[] = [];
  for (let i = 0; i <= steps; i++) {
    const v = fromAxis(kx, a + ((b - a) * i) / steps);
    if (axisValue(ky, v) !== null) out.push({ x: v, y: v });
  }
  return out.length >= 2 ? out : [];
}

/** The fits drawn by `regression` and their titles. */
export const FIT_TITLES: Record<Exclude<Regression, "none">, string> = { ols: "МНК", rma: "RMA", weighted: "Взвешенная" };

/** The non-empty density cells as raster rectangles (cells equal in the space of the axes). */
export function densityRects(d: ScatterDensity, mx: AxisMap, my: AxisMap): RasterRect[] {
  const tx0 = axisValue(mx.kind, d.x[0]);
  const tx1 = axisValue(mx.kind, d.x[1]);
  const ty0 = axisValue(my.kind, d.y[0]);
  const ty1 = axisValue(my.kind, d.y[1]);
  if (tx0 === null || tx1 === null || ty0 === null || ty1 === null || !(d.nx > 0) || !(d.ny > 0)) return [];
  const px = (t: number) => (t - mx.t0) * mx.k + mx.p0;
  const py = (t: number) => (t - my.t0) * my.k + my.p0;
  const out: RasterRect[] = [];
  const lut = new Map<number, number>();
  for (let iy = 0; iy < d.ny; iy++) {
    for (let ix = 0; ix < d.nx; ix++) {
      const n = d.counts[iy * d.nx + ix];
      if (!(n > 0)) continue;
      let color = lut.get(n);
      if (color === undefined) lut.set(n, (color = rgba32(densityColor(n, d.max), 0.92)));
      out.push({ x0: px(tx0 + ((tx1 - tx0) * ix) / d.nx), x1: px(tx0 + ((tx1 - tx0) * (ix + 1)) / d.nx), y0: py(ty0 + ((ty1 - ty0) * iy) / d.ny), y1: py(ty0 + ((ty1 - ty0) * (iy + 1)) / d.ny), color });
    }
  }
  return out;
}

/** The colour (hex) of each point: by the colour field (numbers — the ramp, text — the series colours), else one. */
export function pointColors(r: Pick<ScatterResult, "c" | "color">, n: number, color: string): string[] {
  const c = r.c;
  const info = r.color;
  if (!c || !info) return new Array<string>(n).fill(color);
  if (info.kind === "text") return Array.from({ length: n }, (_, i) => (c[i] === null || c[i] === undefined ? MISSING_COLOR : seriesColor(c[i] as number)));
  const lo = info.min ?? 0;
  const span = (info.max ?? lo) - lo || 1;
  // 256 steps of the ramp: one mix per step, not per point
  const lut = Array.from({ length: 256 }, (_, k) => rampColor(k / 255));
  return Array.from({ length: n }, (_, i) => {
    const v = c[i];
    return v === null || v === undefined || !Number.isFinite(v) ? MISSING_COLOR : lut[Math.round(Math.min(1, Math.max(0, (v - lo) / span)) * 255)];
  });
}

/** The same as raster pixels (alpha 0.8) without a string per point: the canvas layer of up to 200 000 points. */
export function pointPixels(r: Pick<ScatterResult, "c" | "color">, n: number, color: string): Uint32Array {
  const out = new Uint32Array(n);
  const c = r.c;
  const info = r.color;
  const missing = rgba32(MISSING_COLOR, 0.8);
  if (!c || !info) return out.fill(rgba32(color, 0.8));
  if (info.kind === "text") {
    const lut = info.labels.map((_, i) => rgba32(seriesColor(i), 0.8));
    for (let i = 0; i < n; i++) {
      const v = c[i];
      out[i] = v === null || v === undefined ? missing : (lut[v] ?? missing);
    }
    return out;
  }
  const lo = info.min ?? 0;
  const k = 255 / ((info.max ?? lo) - lo || 1);
  const lut = Uint32Array.from({ length: 256 }, (_, j) => rgba32(rampColor(j / 255), 0.8));
  for (let i = 0; i < n; i++) {
    const v = c[i];
    out[i] = v === null || v === undefined || !Number.isFinite(v) ? missing : lut[Math.round(Math.min(255, Math.max(0, (v - lo) * k)))];
  }
  return out;
}

/** The legend of the colour field: a ramp min…max, or the codes with their colours. */
export function colorLegend(r: Pick<ScatterResult, "color" | "color_field">): { title: string; items: { label: string; color: string }[] } | null {
  const info = r.color;
  if (!info || !r.color_field) return null;
  if (info.kind === "text") return { title: r.color_field, items: info.labels.slice(0, 20).map((label, i) => ({ label, color: seriesColor(i) })) };
  return { title: r.color_field, items: [{ label: formatNumber(info.min), color: rampColor(0) }, { label: formatNumber(info.max), color: rampColor(1) }] };
}

const n = (v: number) => v.toLocaleString("ru-RU");

/** [title, value] of the table under the chart: n, r of Pearson and Spearman, each fit (b and a). */
export function scatterTable(r: ScatterResult): [string, string][] {
  const fit = (f: Fit | null) => (f ? `b ${formatNumber(f.b, 5)}, a ${formatNumber(f.a, 5)}` : "—");
  return [
    ["Пар в расчёте", n(r.n)],
    ["r Пирсона", formatNumber(r.pearson, 4)],
    ["r Спирмена", formatNumber(r.spearman, 4)],
    ["МНК", fit(r.fits.ols)],
    ["RMA", fit(r.fits.rma)],
    ["Взвешенная", r.weighted ? fit(r.fits.weighted) : "без веса"],
    ["X: среднее, σ", `${formatNumber(r.x_stats.mean, 5)}, ${formatNumber(r.x_stats.std, 5)}`],
    ["Y: среднее, σ", `${formatNumber(r.y_stats.mean, 5)}, ${formatNumber(r.y_stats.std, 5)}`],
  ];
}

/** What was left out, in words (seen in the window: pairs with a missing side are counted, never zero). */
export function scatterNotes(r: ScatterResult): string[] {
  const c = r.counts;
  const out = [`строк ${n(c.rows)}, пар в расчёте ${n(c.used)}`];
  if (c.missing_x) out.push(`без X ${n(c.missing_x)}`);
  if (c.missing_y) out.push(`без Y ${n(c.missing_y)}`);
  if (c.unmatched) out.push(`нет пробы того же интервала в таблице Y: ${n(c.unmatched)}`);
  if (c.bad_weight) out.push(`без веса или вес ≤ 0: ${n(c.bad_weight)}`);
  if (c.negative) out.push(`отрицательных ${n(c.negative)}`);
  if (c.zero_excluded) out.push(`нулей исключено ${n(c.zero_excluded)}`);
  // the cuts of X and Y: pairs
  if (c.bottom_excluded) out.push(`ниже отсечки исключено пар ${n(c.bottom_excluded)}`);
  if (c.bottom_capped) out.push(`поднято до отсечки пар ${n(c.bottom_capped)}`);
  if (c.capped) out.push(`урезано пар ${n(c.capped)}`);
  if (c.top_excluded) out.push(`выше отсечки исключено пар ${n(c.top_excluded)}`);
  if (c.nonpositive_log) out.push(`≤ 0 на логарифмической оси не показаны: ${n(c.nonpositive_log)}`);
  if (r.mode === "density" && r.density) out.push(`больше 200 000 пар — плотность ${r.density.nx} × ${r.density.ny}`);
  if (r.log_x || r.log_y) out.push("регрессии и r — по log10 логарифмических осей");
  return out;
}

/** CSV (ТЗ 8.3 «Экспорт»): the points — row, X, Y, the colour value; the density — the cells with their counts. */
export function scatterCsv(r: ScatterResult, titles: { x: string; y: string }): string {
  if (r.mode === "density" && r.density) {
    const d = r.density;
    const kx: AxisKind = r.log_x ? "log" : "linear";
    const ky: AxisKind = r.log_y ? "log" : "linear";
    const tx = [axisValue(kx, d.x[0]) ?? 0, axisValue(kx, d.x[1]) ?? 0];
    const ty = [axisValue(ky, d.y[0]) ?? 0, axisValue(ky, d.y[1]) ?? 0];
    const ex = (i: number) => fromAxis(kx, tx[0] + ((tx[1] - tx[0]) * i) / d.nx);
    const ey = (i: number) => fromAxis(ky, ty[0] + ((ty[1] - ty[0]) * i) / d.ny);
    const rows: number[][] = [];
    for (let iy = 0; iy < d.ny; iy++) for (let ix = 0; ix < d.nx; ix++) if (d.counts[iy * d.nx + ix] > 0) rows.push([ex(ix), ex(ix + 1), ey(iy), ey(iy + 1), d.counts[iy * d.nx + ix]]);
    return csvText([`${titles.x} от`, `${titles.x} до`, `${titles.y} от`, `${titles.y} до`, "Проб"], rows);
  }
  const xs = r.x ?? [];
  const c = r.c;
  const labels = r.color?.kind === "text" ? r.color.labels : null;
  const header = ["Строка", titles.x, titles.y, ...(r.color_field ? [r.color_field] : [])];
  return csvText(header, xs.map((x, i) => [r.row?.[i] ?? null, x, r.y?.[i] ?? null, ...(r.color_field ? [labels ? (c?.[i] === null || c?.[i] === undefined ? null : labels[c[i] as number]) : (c?.[i] ?? null)] : [])]));
}

/** Rows (ids of `dh_rows`) of the points selected by their indices. */
export const selectedRows = (r: Pick<ScatterResult, "row">, indices: readonly number[]): number[] => indices.map((i) => r.row?.[i]).filter((v): v is number => typeof v === "number");

/** The assistant's text: the name, pairs, r of Pearson and Spearman, the line drawn. */
export function scatterSummary(name: string, r: ScatterResult, regression: Regression): string {
  const f = regression === "none" ? null : r.fits[regression];
  const line = f ? `; ${FIT_TITLES[regression as Exclude<Regression, "none">]}: b ${formatNumber(f.b, 4)}, a ${formatNumber(f.a, 4)}` : "";
  return `${name}: пар ${n(r.n)}, r Пирсона ${formatNumber(r.pearson, 4)}, r Спирмена ${formatNumber(r.spearman, 4)}${line}`;
}
