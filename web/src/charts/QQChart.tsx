import { forwardRef, useState } from "react";
import type { QQResult } from "../api";
import { formatNumber, linearScale, ticksFor, type Domain } from "../stats/chartMath";
import { logTicks } from "./HistogramChart";

const W = 640;
const H = 520;
const M = { left: 66, right: 22, top: 18, bottom: 50 };
/** Points whose probability is closest to these get a label. */
export const LABELLED = [10, 50, 90];

/** Axis ranges: each over its own values, or one common range (then the 1:1 line is the diagonal). */
export function qqDomains(points: [number, number, number][], log: boolean, equal: boolean): { x: Domain; y: Domain } | null {
  const xs = points.map((p) => p[0]).filter((v) => Number.isFinite(v) && (!log || v > 0));
  const ys = points.map((p) => p[1]).filter((v) => Number.isFinite(v) && (!log || v > 0));
  if (!xs.length || !ys.length) return null;
  const span = (v: number[]): Domain => {
    let lo = Math.min(...v);
    let hi = Math.max(...v);
    if (log) {
      const a = Math.log10(lo);
      const b = Math.log10(hi);
      const pad = Math.max((b - a) * 0.04, 0.02);
      return { min: 10 ** (a - pad), max: 10 ** (b + pad) };
    }
    if (hi === lo) {
      lo -= Math.abs(lo) * 0.1 || 1;
      hi += Math.abs(hi) * 0.1 || 1;
    }
    const pad = (hi - lo) * 0.04;
    return { min: lo - pad, max: hi + pad };
  };
  const x = span(xs);
  const y = span(ys);
  if (!equal) return { x, y };
  const both = { min: Math.min(x.min, y.min), max: Math.max(x.max, y.max) };
  return { x: both, y: both };
}

/** The point nearest to each labelled probability (for P10, P50, P90 marks). */
export function labelledPoints(points: [number, number, number][]): { p: number; index: number }[] {
  if (!points.length) return [];
  return LABELLED.map((p) => ({
    p,
    index: points.reduce((best, q, i) => (Math.abs(q[2] - p) < Math.abs(points[best][2] - p) ? i : best), 0),
  }));
}

/** Quantile against quantile, with the 1:1 line; linear or logarithmic axes. */
export const QQChart = forwardRef<SVGSVGElement, {
  result: QQResult;
  log: boolean;
  equal: boolean;
  diagonal: boolean;
  labels: boolean;
  color: string;
  xLabel: string;
  yLabel: string;
}>(function QQChart({ result, log, equal, diagonal, labels, color, xLabel, yLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = result.points.filter((p) => !log || (p[0] > 0 && p[1] > 0));
  const d = qqDomains(pts, log, equal);
  if (!d) return <p className="muted small">Нет точек для графика{log ? " (на логарифмических осях — только положительные)" : ""}</p>;
  const t = (v: number) => (log ? Math.log10(v) : v);
  const sx0 = linearScale({ min: t(d.x.min), max: t(d.x.max) }, M.left, W - M.right);
  const sy0 = linearScale({ min: t(d.y.min), max: t(d.y.max) }, H - M.bottom, M.top);
  const sx = (v: number) => sx0(t(v));
  const sy = (v: number) => sy0(t(v));
  const ticks = (dom: Domain) => (log ? logTicks(dom.min, dom.max) : ticksFor(dom, 7).filter((v) => v >= dom.min && v <= dom.max));
  // The 1:1 line over the part of the plot where both axes overlap.
  const lo = Math.max(d.x.min, d.y.min);
  const hi = Math.min(d.x.max, d.y.max);
  const r = pts.length > 400 ? 2 : 3.2;
  const h = hover !== null ? pts[hover] : null;

  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width="100%" className="hist-chart qq-chart" role="img" aria-label={`Q-Q: ${yLabel} против ${xLabel}`} onMouseLeave={() => setHover(null)}>
      <rect x={0} y={0} width={W} height={H} className="chart-bg" />
      {ticks(d.y).map((v) => (
        <g key={`y${v}`}>
          <line className="chart-grid" x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)} />
          <text className="chart-tick" x={M.left - 6} y={sy(v)} textAnchor="end" dominantBaseline="middle">{formatNumber(v)}</text>
        </g>
      ))}
      {ticks(d.x).map((v) => (
        <g key={`x${v}`}>
          <line className="chart-grid" x1={sx(v)} x2={sx(v)} y1={M.top} y2={H - M.bottom} />
          <text className="chart-tick" x={sx(v)} y={H - M.bottom + 15} textAnchor="middle">{formatNumber(v)}</text>
        </g>
      ))}
      {diagonal && hi > lo && <line className="qq-diagonal" x1={sx(lo)} y1={sy(lo)} x2={sx(hi)} y2={sy(hi)} />}
      {pts.map((p, i) => (
        <circle key={i} cx={sx(p[0])} cy={sy(p[1])} r={hover === i ? r + 2 : r} fill={color} opacity={0.85} onMouseEnter={() => setHover(i)} />
      ))}
      {labels && labelledPoints(pts).map(({ p, index }) => (
        <g key={p}>
          <circle cx={sx(pts[index][0])} cy={sy(pts[index][1])} r={r + 3} className="qq-label-ring" fill="none" />
          <text className="chart-tick hist-mark-label" x={sx(pts[index][0]) + 8} y={sy(pts[index][1]) - 6}>P{p}</text>
        </g>
      ))}
      <rect className="chart-frame" x={M.left} y={M.top} width={W - M.left - M.right} height={H - M.top - M.bottom} fill="none" />
      <text className="chart-axis" x={(M.left + W - M.right) / 2} y={H - 10} textAnchor="middle">{xLabel}</text>
      <text className="chart-axis" transform={`translate(15 ${(M.top + H - M.bottom) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
      {h && (
        <g className="hist-tip" transform={`translate(${Math.min(sx(h[0]) + 10, W - M.right - 190)} ${Math.max(sy(h[1]) - 64, M.top + 4)})`}>
          <rect width={184} height={58} rx={4} />
          <text x={8} y={16}>P{formatNumber(h[2], 3)}</text>
          <text x={8} y={32}>X: {formatNumber(h[0], 5)}</text>
          <text x={8} y={48}>Y: {formatNumber(h[1], 5)}</text>
        </g>
      )}
    </svg>
  );
});
