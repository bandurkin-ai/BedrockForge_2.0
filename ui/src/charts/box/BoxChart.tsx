/**
 * Box plots by domains (ТЗ 8.3; ADR 0019 §4, §11): a box Q1…Q3 with the median, whiskers to the extreme values
 * inside 1,5·IQR, the outliers as points, the mean as a diamond, n under each box; a linear or log10 value axis (on
 * the log one the box is of the logarithms). SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useState } from "react";
import type { BoxResult } from "../../modules/charts/apiMore";
import { formatNumber, makeScale } from "../core/axisScale";
import { YAxis } from "../core/Axes";
import { seriesColor } from "../core/palette";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { boxDomain, boxPixels, groupLabel } from "./model";

export const BOX_W = 720;
export const BOX_H = 440;
const BOX = plotBox(BOX_W, BOX_H, { left: 66, right: 18, top: 18, bottom: 62 });

export interface BoxChartProps {
  result: BoxResult;
  mean: boolean;
  outliers: boolean;
  /** one colour for all boxes; null — the colours of the domains */
  color: string | null;
  yLabel: string;
}

export const BoxChart = forwardRef<SVGSVGElement, BoxChartProps>(function BoxChart({ result: r, mean, outliers, color, yLabel }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const d = boxDomain(r, r.log);
  if (!d || !r.groups.length) return null;
  const sy = makeScale(r.log ? "log" : "linear", d, BOX.y1, BOX.y0);
  const slot = (BOX.x1 - BOX.x0) / r.groups.length;
  const half = Math.min(28, slot * 0.32);
  const g = hover !== null ? r.groups[hover] : null;
  return (
    <PlotFrame ref={ref} box={BOX} label={`Ящики с усами: ${yLabel}`} yTitle={`${yLabel}${r.log ? " (log)" : ""}`} xTitle={r.domain ?? undefined} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} count={6} />
      <g data-boxes={r.groups.length}>
        {r.groups.map((grp, i) => {
          const cx = BOX.x0 + slot * (i + 0.5);
          const fill = color ?? seriesColor(i);
          const px = boxPixels(grp, sy);
          return (
            <g key={`${grp.label ?? "\u0000"}${i}`} data-box={groupLabel(grp.label, r.domain)} onPointerEnter={() => setHover(i)}>
              <rect x={cx - slot / 2} y={BOX.y0} width={slot} height={BOX.y1 - BOX.y0} fill="transparent" />
              {px && (
                <>
                  {px.high !== null && <line className={css.curve} x1={cx} x2={cx} y1={px.q3} y2={px.high} />}
                  {px.low !== null && <line className={css.curve} x1={cx} x2={cx} y1={px.q1} y2={px.low} />}
                  {px.high !== null && <line className={css.curve} x1={cx - half / 2} x2={cx + half / 2} y1={px.high} y2={px.high} />}
                  {px.low !== null && <line className={css.curve} x1={cx - half / 2} x2={cx + half / 2} y1={px.low} y2={px.low} />}
                  <rect data-q1={px.q1.toFixed(2)} data-q3={px.q3.toFixed(2)} x={cx - half} y={px.q3} width={half * 2} height={Math.max(1, px.q1 - px.q3)} fill={fill} fillOpacity={0.35} stroke={fill} strokeWidth={1.5} />
                  <line data-median={px.median.toFixed(2)} x1={cx - half} x2={cx + half} y1={px.median} y2={px.median} stroke={fill} strokeWidth={2.5} />
                  {mean && px.mean !== null && <path data-mean="1" className={css.mark} d={`M${cx},${px.mean - 5}L${cx + 5},${px.mean}L${cx},${px.mean + 5}L${cx - 5},${px.mean}Z`} fill="none" />}
                  {outliers && (
                    <g data-outliers={px.outliers.length}>
                      {px.outliers.map((y, k) => (
                        <circle key={k} cx={cx} cy={y} r={2.2} fill="none" stroke={fill} />
                      ))}
                    </g>
                  )}
                </>
              )}
              <text className={css.tick} x={cx} y={BOX.y1 + 15} textAnchor="middle">
                {groupLabel(grp.label, r.domain).slice(0, 16)}
              </text>
              <text className={css.tick} x={cx} y={BOX.y1 + 28} textAnchor="middle">
                n {grp.n.toLocaleString("ru-RU")}
              </text>
            </g>
          );
        })}
      </g>
      {g && hover !== null && (
        <g className={css.tip} transform={`translate(${Math.min(BOX.x0 + slot * (hover + 0.5) + 12, BOX.x1 - 200)} ${BOX.y0 + 6})`}>
          <rect width={194} height={106} rx={4} />
          <text x={8} y={16}>{groupLabel(g.label, r.domain)}</text>
          <text x={8} y={32}>n {g.n.toLocaleString("ru-RU")}, среднее {formatNumber(g.mean, 4)}</text>
          <text x={8} y={48}>Q1 {formatNumber(g.q1, 4)}, медиана {formatNumber(g.median, 4)}</text>
          <text x={8} y={64}>Q3 {formatNumber(g.q3, 4)}</text>
          <text x={8} y={80}>усы {formatNumber(g.whisker_low, 4)} … {formatNumber(g.whisker_high, 4)}</text>
          <text x={8} y={96}>выбросов {g.outliers_total.toLocaleString("ru-RU")}</text>
        </g>
      )}
    </PlotFrame>
  );
});
