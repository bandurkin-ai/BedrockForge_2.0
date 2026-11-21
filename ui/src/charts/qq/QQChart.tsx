/**
 * Quantile against quantile (ТЗ 7.26; перенос `QQChart` BF): linear or log10 axes, one common scale on request,
 * the 1:1 line, the P10 / P50 / P90 marks and the tooltip of a point. SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useState } from "react";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { labelledPoints, qqDomains, type QQPoint } from "./model";

export const QQ_W = 640;
export const QQ_H = 520;
const BOX = plotBox(QQ_W, QQ_H, { left: 66, right: 22, top: 18, bottom: 50 });

export interface QQChartProps {
  points: readonly QQPoint[];
  log: boolean;
  equal: boolean;
  diagonal: boolean;
  labels: boolean;
  color: string;
  xLabel: string;
  yLabel: string;
}

export const QQChart = forwardRef<SVGSVGElement, QQChartProps>(function QQChart({ points, log, equal, diagonal, labels, color, xLabel, yLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const d = qqDomains(points, log, equal);
  if (!d) return null;
  const kind = log ? "log" : "linear";
  const sx = makeScale(kind, d.x, BOX.x0, BOX.x1);
  const sy = makeScale(kind, d.y, BOX.y1, BOX.y0);
  const px = (v: number) => sx.map(v) ?? BOX.x0;
  const py = (v: number) => sy.map(v) ?? BOX.y1;
  // the 1:1 line over the part where both axes overlap
  const lo = Math.max(d.x.min, d.y.min);
  const hi = Math.min(d.x.max, d.y.max);
  const r = points.length > 400 ? 2 : 3.2;
  const h = hover !== null ? points[hover] : null;
  return (
    <PlotFrame ref={ref} box={BOX} label={`Q-Q: ${yLabel} против ${xLabel}`} xTitle={xLabel} yTitle={yLabel} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} count={7} />
      <XAxis scale={sx} box={BOX} count={7} />
      {diagonal && hi > lo && <line className={css.diagonal} data-line="1:1" x1={px(lo)} y1={py(lo)} x2={px(hi)} y2={py(hi)} />}
      <g data-points={points.length}>
        {points.map((q, i) => (
          <circle key={i} cx={px(q.x)} cy={py(q.y)} r={hover === i ? r + 2 : r} fill={color} opacity={0.85} onPointerEnter={() => setHover(i)} />
        ))}
      </g>
      {labels &&
        labelledPoints(points).map(({ p, index }) => (
          <g key={p} data-mark={`P${p * 100}`}>
            <circle className={css.ring} cx={px(points[index].x)} cy={py(points[index].y)} r={r + 3} />
            <text className={css.markLabel} x={px(points[index].x) + 8} y={py(points[index].y) - 6}>
              P{p * 100}
            </text>
          </g>
        ))}
      {h && (
        <g className={css.tip} transform={`translate(${Math.min(px(h.x) + 10, BOX.x1 - 190)} ${Math.max(py(h.y) - 64, BOX.y0 + 4)})`}>
          <rect width={184} height={58} rx={4} />
          <text x={8} y={16}>P{formatNumber(h.p * 100, 3)}</text>
          <text x={8} y={32}>X: {formatNumber(h.x, 5)}</text>
          <text x={8} y={48}>Y: {formatNumber(h.y, 5)}</text>
        </g>
      )}
    </PlotFrame>
  );
});
