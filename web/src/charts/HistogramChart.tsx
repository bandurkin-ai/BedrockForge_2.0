import { forwardRef, useState } from "react";
import type { HistogramResult } from "../api";
import { formatNumber, linearScale, ticksFor } from "../stats/chartMath";

export type YMode = "percent" | "weight" | "count";

export interface Mark {
  value: number;
  label: string;
  dashed?: boolean;
}

const W = 760;
const H = 400;
const M = { left: 62, right: 52, top: 18, bottom: 46 };

/** Ticks of a log10 axis: decades, plus 2 and 5 when the range spans few decades. */
export function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  const a = Math.floor(Math.log10(lo));
  const b = Math.ceil(Math.log10(hi));
  const mult = b - a <= 3 ? [1, 2, 5] : [1];
  for (let e = a; e <= b; e++) for (const m of mult) {
    const v = m * 10 ** e;
    if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
  }
  return out;
}

/** The value of every bar for the chosen Y axis. */
export function barValues(r: HistogramResult, y: YMode): number[] {
  return y === "percent" ? r.bins.percent : y === "weight" ? r.bins.weights : r.bins.counts;
}

export const Y_TITLES: Record<YMode, string> = { percent: "Доля, %", weight: "Сумма весов", count: "Число проб" };

/**
 * Histogram bars over a linear or logarithmic value axis, with the cumulative curve on a 0–100 %
 * axis on the right and vertical marks (mean, median, percentiles).
 */
export const HistogramChart = forwardRef<SVGSVGElement, {
  result: HistogramResult;
  y: YMode;
  cumulative: boolean;
  marks: Mark[];
  color: string;
  xLabel: string;
}>(function HistogramChart({ result, y, cumulative, marks, color, xLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const { edges } = result.bins;
  const values = barValues(result, y);
  const log = result.log;
  const t = (v: number) => (log ? Math.log10(v) : v);
  const lo = edges[0];
  const hi = edges[edges.length - 1];
  const sx = linearScale({ min: t(lo), max: t(hi) }, M.left, W - M.right);
  const x = (v: number) => sx(t(v));
  const top = Math.max(...values, 1e-12);
  const yDomain = { min: 0, max: top * 1.08 };
  const sy = linearScale(yDomain, H - M.bottom, M.top);
  const sc = linearScale({ min: 0, max: 100 }, H - M.bottom, M.top);
  const xticks = log ? logTicks(lo, hi) : ticksFor({ min: lo, max: hi }, 8).filter((v) => v >= lo - 1e-12 && v <= hi + 1e-12);
  const visibleMarks = marks.filter((m) => Number.isFinite(m.value) && m.value >= lo && m.value <= hi && (!log || m.value > 0));
  const cum = result.bins.cumulative_percent;
  const startCum = (result.bins.below.weight / Math.max(result.stats.weight_sum + result.bins.below.weight + result.bins.above.weight, 1e-300)) * 100;
  const cumPath = [`M${x(edges[0]).toFixed(1)},${sc(startCum).toFixed(1)}`, ...cum.map((c, i) => `L${x(edges[i + 1]).toFixed(1)},${sc(c).toFixed(1)}`)].join("");
  const h = hover;

  return (
    <svg
      ref={ref} viewBox={`0 0 ${W} ${H}`} width="100%" className="hist-chart" role="img"
      aria-label={`Гистограмма: ${xLabel}`} onMouseLeave={() => setHover(null)}
    >
      <rect x={0} y={0} width={W} height={H} className="chart-bg" />
      {ticksFor(yDomain, 5).filter((v) => v <= yDomain.max).map((v) => (
        <g key={`y${v}`}>
          <line className="chart-grid" x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)} />
          <text className="chart-tick" x={M.left - 6} y={sy(v)} textAnchor="end" dominantBaseline="middle">{formatNumber(v)}</text>
        </g>
      ))}
      {xticks.map((v) => (
        <g key={`x${v}`}>
          <line className="chart-grid" x1={x(v)} x2={x(v)} y1={M.top} y2={H - M.bottom} />
          <text className="chart-tick" x={x(v)} y={H - M.bottom + 15} textAnchor="middle">{formatNumber(v)}</text>
        </g>
      ))}
      {values.map((v, i) => (
        <rect
          key={i} x={x(edges[i]) + 0.5} y={sy(v)} width={Math.max(0.5, x(edges[i + 1]) - x(edges[i]) - 1)} height={Math.max(0, sy(0) - sy(v))}
          fill={color} opacity={h === null || h === i ? 0.9 : 0.55} onMouseEnter={() => setHover(i)}
        />
      ))}
      {cumulative && (
        <>
          <path d={cumPath} className="hist-cum" fill="none" />
          {[0, 25, 50, 75, 100].map((v) => (
            <text key={`c${v}`} className="chart-tick" x={W - M.right + 6} y={sc(v)} dominantBaseline="middle">{v}%</text>
          ))}
          <text className="chart-axis" transform={`translate(${W - 12} ${(M.top + H - M.bottom) / 2}) rotate(90)`} textAnchor="middle">Накопленная доля, %</text>
        </>
      )}
      {visibleMarks.map((m, i) => (
        <g key={m.label}>
          <line className="hist-mark" x1={x(m.value)} x2={x(m.value)} y1={M.top} y2={H - M.bottom} strokeDasharray={m.dashed ? "5 4" : undefined} />
          <text className="chart-tick hist-mark-label" x={x(m.value) + 4} y={M.top + 11 + (i % 4) * 12}>{m.label}</text>
        </g>
      ))}
      <rect className="chart-frame" x={M.left} y={M.top} width={W - M.left - M.right} height={H - M.top - M.bottom} fill="none" />
      <text className="chart-axis" x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle">{xLabel}{log ? " (логарифмическая шкала)" : ""}</text>
      <text className="chart-axis" transform={`translate(14 ${(M.top + H - M.bottom) / 2}) rotate(-90)`} textAnchor="middle">{Y_TITLES[y]}</text>
      {h !== null && (
        <g className="hist-tip" transform={`translate(${Math.min(x(edges[h]) + 8, W - M.right - 210)} ${M.top + 6})`}>
          <rect width={204} height={74} rx={4} />
          <text x={8} y={16}>{formatNumber(edges[h])} … {formatNumber(edges[h + 1])}</text>
          <text x={8} y={32}>Проб: {result.bins.counts[h]}{result.weighted ? `, вес ${formatNumber(result.bins.weights[h])}` : ""}</text>
          <text x={8} y={48}>Доля: {formatNumber(result.bins.percent[h], 3)}%</text>
          <text x={8} y={64}>Накоплено: {formatNumber(cum[h], 3)}%</text>
        </g>
      )}
    </svg>
  );
});
