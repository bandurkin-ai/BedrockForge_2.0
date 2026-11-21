/**
 * The log-probability plot (ТЗ 8.3; ADR 0019 §4): the request from the settings, the points of each domain
 * ([value, P %] of the core, P = (C − w / 2) / W) on a log10 axis of the grade against the probability axis Φ⁻¹(P),
 * the ranges, the point picked by a click (the nearest one on the screen — the threshold of «Урезка»), the
 * arguments of `composite.topcut` (the threshold only goes to the window: it is not applied by itself — ADR 0018,
 * decision 8), the table, the CSV and the assistant's text. Pure.
 */
import { createCalculator, type Calculator } from "../../modules/charts/api";
import { computeProbPlot, type ProbPlotResult, type ProbSeries } from "../../modules/charts/apiMore";
import { needsWeightField, type ProbPlotSettings } from "../../modules/charts/settings";
import { domainOf, formatNumber, type Domain, type Scale } from "../core/axisScale";
import { csvText } from "../core/csv";
import type { Pt } from "../core/lasso";
import { groupLabel } from "../box/model";

export type ProbPlotRequest = Omit<ProbPlotSettings, "view">;

export function probRequest(s: ProbPlotSettings): ProbPlotRequest | null {
  if (!s.field || needsWeightField(s)) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function probCalculator(apply: (r: ProbPlotResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: ProbPlotRequest) => Promise<ProbPlotResult> = computeProbPlot): Calculator<ProbPlotRequest> {
  return createCalculator(load, apply, fail, busy);
}

export interface ProbPoint {
  /** the grade, > 0 */
  x: number;
  /** probability, 0…1 */
  p: number;
}

/** The points of a series the axes can show: a grade > 0, P inside (0; 1). */
export function seriesPoints(s: Pick<ProbSeries, "points">): ProbPoint[] {
  const out: ProbPoint[] = [];
  for (const [x, pc] of s.points) {
    const p = pc / 100;
    if (Number.isFinite(x) && x > 0 && p > 0 && p < 1) out.push({ x, p });
  }
  return out;
}

/** The ranges: the grades (log10) and the probabilities, at least P1…P99, inside 0,05…99,95 %. */
export function probDomains(r: Pick<ProbPlotResult, "series">): { x: Domain; p: Domain } | null {
  const pts = r.series.flatMap(seriesPoints);
  const x = domainOf(pts.map((q) => q.x), "log", 0.04);
  if (!x || !pts.length) return null;
  const lo = Math.min(...pts.map((q) => q.p));
  const hi = Math.max(...pts.map((q) => q.p));
  return { x, p: { min: Math.max(0.0005, Math.min(0.01, lo * 0.8)), max: Math.min(0.9995, Math.max(0.99, 1 - (1 - hi) * 0.8)) } };
}

/** The point nearest to a pixel of the plot (≤ `radius` px), over all series: the threshold picked by a click. */
export function pickPoint(r: Pick<ProbPlotResult, "series">, sx: Scale, sy: Scale, at: Pt, radius = 12): { series: number; point: ProbPoint } | null {
  let best: { series: number; point: ProbPoint; d: number } | null = null;
  r.series.forEach((s, si) => {
    for (const q of seriesPoints(s)) {
      const px = sx.map(q.x);
      const py = sy.map(q.p);
      if (px === null || py === null) continue;
      const d = Math.hypot(px - at.x, py - at.y);
      if (d <= radius && (!best || d < best.d)) best = { series: si, point: q, d };
    }
  });
  const b = best as { series: number; point: ProbPoint; d: number } | null;
  return b ? { series: b.series, point: b.point } : null;
}

/** A threshold as typed in «Урезка»: four significant digits. */
export const roundThreshold = (v: number): number => Number(v.toPrecision(4));

/**
 * The arguments of `composite.topcut` for a picked threshold: the set and the value only — the table and the field
 * are chosen in its window (with all of them the command would start the job at once; the threshold of a chart is
 * never applied by itself).
 */
export function topcutArgs(s: Pick<ProbPlotSettings, "set_id">, value: number): { set_id: number; value: number } {
  return { set_id: s.set_id, value: roundThreshold(value) };
}

const n = (v: number) => v.toLocaleString("ru-RU");

export const PROB_HEADER = ["Домен", "Проб", "≤ 0", "Среднее", "Медиана", "CV", "P90"];

export function probTable(r: ProbPlotResult): string[][] {
  return r.series.map((s) => [groupLabel(s.label, r.domain), n(s.count), n(s.nonpositive), formatNumber(s.stats.mean, 5), formatNumber(s.stats.median, 5), formatNumber(s.stats.cv, 4), formatNumber(s.stats.p90, 5)]);
}

/** CSV of the points: the domain, the grade, P %. */
export function probCsv(r: ProbPlotResult, title: string): string {
  return csvText(["Домен", title, "Вероятность, %"], r.series.flatMap((s) => s.points.map(([x, p]) => [groupLabel(s.label, r.domain), x, p])));
}

export function probSummary(name: string, r: ProbPlotResult): string {
  const parts = r.series.slice(0, 12).map((s) => `${groupLabel(s.label, r.domain)}: проб ${n(s.count)}, медиана ${formatNumber(s.stats.median, 4)}, P90 ${formatNumber(s.stats.p90, 4)}`);
  return `${name}: ${parts.join("; ")}`;
}
