/**
 * Histogram bars over a linear or log10 value axis (ТЗ 7.25; перенос `HistogramChart` BF), the cumulative curve on
 * a 0–100 % axis on the right, vertical marks (mean, median, percentiles, cuts) and the tooltip of a bar.
 * SVG of a fixed size: the PNG is twice it (Т7.25-3).
 */
import { forwardRef, useState } from "react";
import type { HistogramResult } from "../../modules/charts/api";
import { formatNumber, makeScale } from "../core/axes";
import { XAxis, YAxis } from "../core/Axes";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import type { HistogramY } from "../../modules/charts/settings";
import css from "../core/Chart.module.css";
import { barValues, cumulativeStart, Y_TITLES, type Mark } from "./model";

export const HIST_W = 760;
export const HIST_H = 400;
const BOX = plotBox(HIST_W, HIST_H);

export interface HistogramChartProps {
  result: HistogramResult;
  y: HistogramY;
  cumulative: boolean;
  marks: Mark[];
  color: string;
  xLabel: string;
}

export const HistogramChart = forwardRef<SVGSVGElement, HistogramChartProps>(function HistogramChart({ result, y, cumulative, marks, color, xLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const { edges } = result.bins;
  const values = barValues(result, y);
  if (edges.length < 2) return null;
  const lo = edges[0];
  const hi = edges[edges.length - 1];
  const sx = makeScale(result.log ? "log" : "linear", { min: lo, max: hi }, BOX.x0, BOX.x1);
  const x = (v: number) => sx.map(v) ?? BOX.x0;
  const top = Math.max(...values.filter(Number.isFinite), 1e-12);
  const sy = makeScale("linear", { min: 0, max: top * 1.08 }, BOX.y1, BOX.y0);
  const sc = makeScale("linear", { min: 0, max: 100 }, BOX.y1, BOX.y0);
  const y0 = sy.map(0) ?? BOX.y1;
  const shown = marks.filter((m) => m.value >= lo && m.value <= hi && (!result.log || m.value > 0));
  const cum = result.bins.cumulative_percent;
  const cumPath = [`M${x(lo).toFixed(1)},${(sc.map(cumulativeStart(result)) ?? BOX.y1).toFixed(1)}`, ...cum.map((c, i) => `L${x(edges[i + 1]).toFixed(1)},${(sc.map(c) ?? BOX.y1).toFixed(1)}`)].join("");
  const h = hover;
  return (
    <PlotFrame ref={ref} box={BOX} label={`Гистограмма: ${xLabel}`} xTitle={`${xLabel}${result.log ? " (логарифмическая шкала)" : ""}`} yTitle={Y_TITLES[y]} yTitleRight={cumulative ? "Накопленная доля, %" : undefined} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} />
      <XAxis scale={sx} box={BOX} />
      <g data-bars={values.length}>
        {values.map((v, i) => {
          const top = Number.isFinite(v) ? (sy.map(v) ?? y0) : y0;
          return (
            <rect
              key={i}
              x={x(edges[i]) + 0.5}
              y={top}
              width={Math.max(0.5, x(edges[i + 1]) - x(edges[i]) - 1)}
              height={Math.max(0, y0 - top)}
              fill={color}
              opacity={h === null || h === i ? 0.9 : 0.55}
              onPointerEnter={() => setHover(i)}
            />
          );
        })}
      </g>
      {cumulative && (
        <g data-curve="cumulative">
          <path className={css.curve} d={cumPath} />
          <YAxis scale={sc} box={BOX} side="right" grid={false} ticks={[0, 25, 50, 75, 100].map((v) => ({ value: v, label: `${v}%` }))} />
        </g>
      )}
      {shown.map((m, i) => (
        <g key={m.label} data-mark={m.label}>
          <line className={css.mark} x1={x(m.value)} x2={x(m.value)} y1={BOX.y0} y2={BOX.y1} strokeDasharray={m.dashed ? "5 4" : undefined} />
          <text className={css.markLabel} x={x(m.value) + 4} y={BOX.y0 + 11 + (i % 4) * 12}>
            {m.label}
          </text>
        </g>
      ))}
      {h !== null && h < values.length && (
        <g className={css.tip} transform={`translate(${Math.min(x(edges[h]) + 8, BOX.x1 - 210)} ${BOX.y0 + 6})`}>
          <rect width={204} height={74} rx={4} />
          <text x={8} y={16}>
            {formatNumber(edges[h])} … {formatNumber(edges[h + 1])}
          </text>
          <text x={8} y={32}>
            Проб: {result.bins.counts[h]}
            {result.weighted ? `, вес ${formatNumber(result.bins.weights[h])}` : ""}
          </text>
          <text x={8} y={48}>Доля: {formatNumber(result.bins.percent[h], 3)}%</text>
          <text x={8} y={64}>Накоплено: {formatNumber(cum[h], 3)}%</text>
        </g>
      )}
    </PlotFrame>
  );
});
