/**
 * The log-probability plot (ТЗ 8.3; ADR 0019 §11): the grade on a log10 axis against the probability axis Φ⁻¹(P);
 * the domains as series of their colours with a legend (points, or lines when asked or past 5000 points). A click
 * picks the threshold: the nearest point within 12 px, else the grade under the pointer; the threshold is marked.
 * SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useState, type MouseEvent as RMouseEvent } from "react";
import type { ProbPlotResult } from "../../modules/charts/apiMore";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { toViewBox } from "../core/lasso";
import { seriesColor } from "../core/palette";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { groupLabel } from "../box/model";
import { pickPoint, probDomains, seriesPoints } from "./model";

export const PROB_W = 680;
export const PROB_H = 500;
const BOX = plotBox(PROB_W, PROB_H, { left: 62, right: 22, top: 18, bottom: 50 });

export interface ProbPlotChartProps {
  result: ProbPlotResult;
  lines: boolean;
  xLabel: string;
  /** the threshold picked (marked) */
  threshold?: number | null;
  onPick?: (value: number) => void;
}

export const ProbPlotChart = forwardRef<SVGSVGElement, ProbPlotChartProps>(function ProbPlotChart({ result: r, lines, xLabel, threshold, onPick }, ref) {
  const [hover, setHover] = useState<{ series: number; x: number; p: number } | null>(null);
  const d = probDomains(r);
  if (!d) return null;
  const sx = makeScale("log", d.x, BOX.x0, BOX.x1);
  const sy = makeScale("prob", d.p, BOX.y1, BOX.y0);
  const series = r.series.map(seriesPoints);
  const total = series.reduce((s, x) => s + x.length, 0);
  const asLines = lines || total > 5000;
  const at = (e: RMouseEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement;
    return svg ? toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), PROB_W, PROB_H) : null;
  };
  const onClick = (e: RMouseEvent<SVGRectElement>) => {
    const p = at(e);
    if (!p || !onPick) return;
    const hit = pickPoint(r, sx, sy, p);
    onPick(hit ? hit.point.x : sx.invert(p.x));
  };
  const onMove = (e: RMouseEvent<SVGRectElement>) => {
    const p = at(e);
    const hit = p ? pickPoint(r, sx, sy, p) : null;
    setHover(hit ? { series: hit.series, x: hit.point.x, p: hit.point.p } : null);
  };
  const tx = threshold !== null && threshold !== undefined ? sx.map(threshold) : null;
  return (
    <PlotFrame ref={ref} box={BOX} label={`Лог-вероятностный: ${xLabel}`} xTitle={`${xLabel} (log)`} yTitle="Вероятность, %" onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} />
      <XAxis scale={sx} box={BOX} />
      {series.map((pts, i) => {
        const color = seriesColor(i);
        const xy = pts.map((q) => [sx.map(q.x), sy.map(q.p)] as const).filter((v): v is readonly [number, number] => v[0] !== null && v[1] !== null);
        return (
          <g key={i} data-series={groupLabel(r.series[i].label, r.domain)} data-points={xy.length}>
            {asLines ? <polyline points={xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")} fill="none" stroke={color} strokeWidth={1.6} /> : xy.map(([x, y], k) => <circle key={k} cx={x} cy={y} r={2.6} fill={color} opacity={0.85} />)}
          </g>
        );
      })}
      {tx !== null && (
        <g data-threshold={threshold}>
          <line className={css.mark} x1={tx} x2={tx} y1={BOX.y0} y2={BOX.y1} strokeDasharray="5 4" />
          <text className={css.markLabel} x={tx + 4} y={BOX.y0 + 12}>
            Порог {formatNumber(threshold, 4)}
          </text>
        </g>
      )}
      <rect x={BOX.x0} y={BOX.y0} width={BOX.x1 - BOX.x0} height={BOX.y1 - BOX.y0} fill="transparent" style={{ cursor: onPick ? "pointer" : undefined }} onClick={onClick} onMouseMove={onMove} />
      {r.series.length > 1 && (
        <g data-legend="domains" transform={`translate(${BOX.x0 + 10} ${BOX.y0 + 10})`} pointerEvents="none">
          {r.series.map((s, i) => (
            <g key={i} transform={`translate(0 ${i * 14})`}>
              <rect width={9} height={9} fill={seriesColor(i)} />
              <text className={css.tick} x={13} y={8}>
                {groupLabel(s.label, r.domain)}
              </text>
            </g>
          ))}
        </g>
      )}
      {hover && (
        <g className={css.tip} pointerEvents="none" transform={`translate(${Math.min((sx.map(hover.x) ?? BOX.x0) + 10, BOX.x1 - 200)} ${Math.max((sy.map(hover.p) ?? BOX.y1) - 64, BOX.y0 + 4)})`}>
          <rect width={194} height={58} rx={4} />
          <text x={8} y={16}>{groupLabel(r.series[hover.series].label, r.domain)}</text>
          <text x={8} y={32}>{xLabel}: {formatNumber(hover.x, 5)}</text>
          <text x={8} y={48}>P {formatNumber(hover.p * 100, 4)} % · щелчок — порог урезки</text>
        </g>
      )}
    </PlotFrame>
  );
});
