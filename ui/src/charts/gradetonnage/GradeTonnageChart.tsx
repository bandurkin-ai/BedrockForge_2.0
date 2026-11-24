/**
 * The grade–tonnage chart of the report «Содержание–тоннаж» (ТЗ 7.31): the tonnes above a cut-off as an area with a line
 * (left axis), the mean grade — or the metal — above it as a line with points (right axis), the cut-offs of the table
 * as dashed marks, a tooltip of the point under the cursor. A point with no grade breaks the line (a gap is not a zero);
 * a series with no value at all is told in words. SVG of the shared frame (`charts/core`), the brand colours.
 */
import { forwardRef, useMemo, useState, type PointerEvent as RPointerEvent } from "react";
import { XAxis, YAxis } from "../core/Axes";
import { domainOf, formatNumber, makeScale } from "../core/axisScale";
import css from "../core/Chart.module.css";
import { seriesColor } from "../core/palette";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { areaOf, hasValues, leftTitle, nearestIndex, pathOf, pieces, rightTitle, rightValues, type CurveData, type RightAxis } from "./model";

export const GT_W = 640;
export const GT_H = 340;

export interface GradeTonnageChartProps {
  curve: CurveData;
  right: RightAxis;
  /** the cut-offs of the table, drawn as dashed marks */
  marks?: readonly number[];
  width?: number;
  height?: number;
}

export const GradeTonnageChart = forwardRef<SVGSVGElement, GradeTonnageChartProps>(function GradeTonnageChart({ curve, right, marks = [], width = GT_W, height = GT_H }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useMemo(() => plotBox(width, height, { left: 72, right: 62, top: 16, bottom: 46 }), [width, height]);
  const values = rightValues(curve, right);
  const xDom = domainOf(curve.cutoffs, "linear", 0);
  const tDom = domainOf([0, ...curve.tonnes], "linear", 0.05, 0);
  const rDom = hasValues(values) ? domainOf(values, "linear", 0.08) : null;
  const label = `Кривая содержание–тоннаж по полю ${curve.field}`;
  const xTitle = `Борт: ${curve.field}${curve.unit ? `, ${curve.unit}` : ""}`;
  if (!xDom || !tDom) {
    return (
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle}>
        <text className={css.tick} data-empty="curve" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          Кривая не построена: нет значений поля борта
        </text>
      </PlotFrame>
    );
  }
  const sx = makeScale("linear", xDom, box.x0, box.x1);
  const st = makeScale("linear", tDom, box.y1, box.y0);
  const sr = rDom ? makeScale("linear", rDom, box.y1, box.y0) : null;
  const mx = (v: number) => sx.map(v);
  const tonnes = pieces(curve.cutoffs, curve.tonnes, mx, (v) => st.map(v));
  const base = st.map(0) ?? box.y1;
  const line = sr ? pieces(curve.cutoffs, values, mx, (v) => sr.map(v)) : [];
  const cT = seriesColor(0);
  const cR = seriesColor(1);
  const pick = (e: RPointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * width;
    setHover(px < box.x0 || px > box.x1 ? null : nearestIndex(curve.cutoffs, sx.invert(px)));
  };
  const tip = hover === null ? null : hover;
  const tipX = tip === null ? 0 : (sx.map(curve.cutoffs[tip]) ?? 0);
  const tipLines =
    tip === null
      ? []
      : [
          `Борт ${formatNumber(curve.cutoffs[tip], 4)}${curve.unit ? ` ${curve.unit}` : ""}`,
          `Тоннаж: ${formatNumber(curve.tonnes[tip], 5)} т`,
          `Содержание: ${curve.grade[tip] === null ? "нет значения" : `${formatNumber(curve.grade[tip], 4)}${curve.unit ? ` ${curve.unit}` : ""}`}`,
          `Металл: ${curve.metal[tip] === null ? "нет значения" : `${formatNumber(curve.metal[tip], 4)}${curve.metal_unit ? ` ${curve.metal_unit}` : ""}`}`,
        ];
  return (
    <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={leftTitle} yTitleRight={rDom ? rightTitle(curve, right) : undefined} onPointerMove={pick} onPointerLeave={() => setHover(null)}>
      <YAxis scale={st} box={box} />
      {sr && <YAxis scale={sr} box={box} side="right" grid={false} />}
      <XAxis scale={sx} box={box} count={7} />
      {marks.map((m) => {
        const x = sx.map(m);
        return x === null || x < box.x0 || x > box.x1 ? null : <line key={m} className={css.diagonal} data-mark={m} x1={x} x2={x} y1={box.y0} y2={box.y1} />;
      })}
      <g data-series="tonnes" fill={cT}>
        {tonnes.map((p, i) => (
          <path key={`a${i}`} d={areaOf(p, base)} opacity={0.16} stroke="none" />
        ))}
        {tonnes.map((p, i) => (
          <path key={`l${i}`} d={pathOf(p)} fill="none" stroke={cT} strokeWidth={2} strokeLinejoin="round" />
        ))}
      </g>
      <g data-series={right} data-pieces={line.length} fill={cR}>
        {line.map((p, i) => (
          <path key={`r${i}`} d={pathOf(p)} fill="none" stroke={cR} strokeWidth={2} strokeLinejoin="round" />
        ))}
        {curve.cutoffs.length <= 80 && line.flatMap((p, i) => p.map(([x, y], k) => <circle key={`${i}-${k}`} cx={x} cy={y} r={2.4} />))}
      </g>
      {!rDom && (
        <text className={css.markLabel} data-empty={right} x={box.x1 - 6} y={box.y0 + 16} textAnchor="end">
          {right === "metal" ? "Металл: нет значения (единица не распознана или нет содержания)" : "Содержание: нет значения ни в одной точке"}
        </text>
      )}
      {tip !== null && (
        <g pointerEvents="none" data-hover={tip}>
          <line className={css.mark} x1={tipX} x2={tipX} y1={box.y0} y2={box.y1} />
          <g className={css.tip} transform={`translate(${Math.min(tipX + 10, box.x1 - 214)} ${box.y0 + 6})`}>
            <rect width={208} height={14 + tipLines.length * 15} rx={4} />
            {tipLines.map((t, i) => (
              <text key={i} x={8} y={16 + i * 15}>
                {t}
              </text>
            ))}
          </g>
        </g>
      )}
    </PlotFrame>
  );
});
