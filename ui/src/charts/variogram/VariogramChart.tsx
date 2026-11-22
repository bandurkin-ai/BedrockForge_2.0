/**
 * The experimental variogram with its model (ТЗ 8.6.4; ADR 0022 §15, decisions 4, 19): the points of each curve
 * (an axis, a direction, downhole, a ray of the fan) sized by their number of pairs with the number as a label; a
 * point with N < min_pairs is hollow (not in the fit); a lag without pairs is not drawn — never as 0. The model
 * curve along the curve's direction (the formulas of `modules/variography/model.ts`, on the side of the interface),
 * the sill of the model and the variance σ² as dashed lines. `overlay` gets the scales — the window draws its
 * drag handles there (nugget at h = 0, contribution, range). SVG of `charts/core`, brand colours of the theme.
 */
import { forwardRef, useMemo, useState, type ReactNode } from "react";
import { formatNumber, makeScale, type Scale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { seriesColor } from "../core/palette";
import { plotBox, PlotFrame, type PlotBox } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import type { ModelType, VModel } from "../../modules/variography/model";
import type { Vec3 } from "../../viz/core/vec3";
import { chartHMax, expPoints, measureTitle, modelLine, modelSill, pairsText, pointRadius, segments, valueDomain, varianceLevel, type CurveData, type ExpPoint } from "./scale";

export const VARIO_W = 520;
export const VARIO_H = 340;
const MARGIN = { left: 58, right: 18, top: 16, bottom: 44 };

export interface VariogramSeries {
  label: string;
  curve: CurveData;
  /** the direction of the model curve (`curveDirection`); null / absent — no model curve (downhole) */
  u?: Vec3 | null;
  color?: string;
}

export interface VariogramScales {
  box: PlotBox;
  sx: Scale;
  sy: Scale;
  hMax: number;
}

export interface VariogramChartProps {
  series: readonly VariogramSeries[];
  type: ModelType;
  /** the model; its type is not checked here (the window keeps it the type of the chart) */
  model?: VModel | null;
  /** σ² of the data (of Y for `nscore`) */
  sigma2?: number | null;
  /** the extent of the distance axis; absent — the farthest lag × 1,05 */
  hMax?: number | null;
  title?: string;
  /** the unit of the distances, «м» */
  unit?: string;
  /** the number of pairs above each point (by default — when no curve has more than 30 points) */
  counts?: boolean;
  width?: number;
  height?: number;
  /** handles of the window, drawn above the curves */
  overlay?: (s: VariogramScales) => ReactNode;
}

interface Placed extends ExpPoint {
  x: number;
  y: number;
  r: number;
}

export const VariogramChart = forwardRef<SVGSVGElement, VariogramChartProps>(function VariogramChart(
  { series, type, model, sigma2, hMax: hMaxProp, title, unit = "м", counts, width = VARIO_W, height = VARIO_H, overlay },
  ref,
) {
  const [hover, setHover] = useState<{ s: number; p: Placed } | null>(null);
  const box = useMemo(() => plotBox(width, height, MARGIN), [width, height]);
  const pts = useMemo(() => series.map((s) => expPoints(s.curve, type)), [series, type]);
  const hMax = (hMaxProp !== null && hMaxProp !== undefined && hMaxProp > 0 ? hMaxProp : chartHMax(series.map((s) => s.curve), model)) ?? null;
  const lines = useMemo(() => (model && hMax ? series.map((s) => (s.u ? modelLine(model, s.u, hMax) : null)) : series.map(() => null)), [model, series, hMax]);
  const level = varianceLevel(type, sigma2);
  const sillValue = modelSill(model);
  const yDom = valueDomain(
    pts.flatMap((p) => p.map((q) => q.value)),
    [level?.value, sillValue, ...lines.flatMap((l) => (l ? l.value : []))],
  );
  const label = title ?? `Вариограмма: ${measureTitle(type)}`;
  const xTitle = `h, ${unit}`;
  if (!hMax || !yDom) {
    return (
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={measureTitle(type)}>
        <text className={css.tick} data-empty="variogram" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          Нет точек: ни в одном лаге нет пар
        </text>
      </PlotFrame>
    );
  }
  const sx = makeScale("linear", { min: 0, max: hMax }, box.x0, box.x1);
  const sy = makeScale("linear", yDom, box.y1, box.y0);
  const nMax = Math.max(1, ...pts.flatMap((p) => p.map((q) => q.n)));
  const showCounts = counts ?? pts.every((p) => p.length <= 30);
  const mx = (v: number) => sx.map(v);
  const my = (v: number | null) => sy.map(v);
  const placed = pts.map((p) => p.flatMap((q): Placed[] => {
    const x = sx.map(q.h);
    const y = sy.map(q.value);
    return x === null || y === null ? [] : [{ ...q, x, y, r: pointRadius(q.n, nMax) }];
  }));
  const hLine = (v: number, cls: string, text: string, key: string) => {
    const y = sy.map(v);
    if (y === null) return null;
    return (
      <g key={key} data-line={key} data-value={v}>
        <line className={cls} x1={box.x0} x2={box.x1} y1={y} y2={y} strokeDasharray={key === "sill" ? "2 3" : "6 4"} />
        <text className={css.markLabel} x={box.x1 - 4} y={y - 4} textAnchor="end">
          {text}
        </text>
      </g>
    );
  };
  return (
    <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={measureTitle(type)} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={box} />
      <XAxis scale={sx} box={box} count={6} />
      {level && hLine(level.value, css.diagonal, `${level.label} = ${formatNumber(level.value, 4)}`, "variance")}
      {sillValue !== null && (level === null || Math.abs(sillValue - level.value) > 1e-12 * Math.max(1, Math.abs(sillValue))) && hLine(sillValue, css.mark, `Порог ${formatNumber(sillValue, 4)}`, "sill")}
      {lines.map((l, i) =>
        l ? (
          <g key={`m${i}`} data-model={series[i].label}>
            {segments(l.h, l.value, mx, my).map((d, k) => (
              <polyline key={k} points={d} fill="none" stroke={series[i].color ?? seriesColor(i)} strokeWidth={1.8} />
            ))}
          </g>
        ) : null,
      )}
      {placed.map((p, i) => {
        const color = series[i].color ?? seriesColor(i);
        return (
          <g key={`s${i}`} data-series={series[i].label} data-points={p.length} data-few={p.filter((q) => q.few).length}>
            {p.map((q) => (
              <g key={q.k} data-lag={q.k + 1}>
                <circle cx={q.x} cy={q.y} r={q.r} fill={q.few ? "none" : color} stroke={color} strokeWidth={q.few ? 1.4 : 0.8} opacity={q.few ? 1 : 0.9} data-few={q.few || undefined} onPointerEnter={() => setHover({ s: i, p: q })} />
                {showCounts && (
                  <text className={css.tick} x={q.x} y={q.y - q.r - 3} textAnchor="middle" pointerEvents="none">
                    {pairsText(q.n)}
                  </text>
                )}
              </g>
            ))}
          </g>
        );
      })}
      {series.length > 1 && (
        <g data-legend="curves" transform={`translate(${box.x0 + 8} ${box.y0 + 8})`} pointerEvents="none">
          {series.map((s, i) => (
            <g key={i} transform={`translate(0 ${i * 14})`}>
              <rect width={9} height={9} fill={s.color ?? seriesColor(i)} />
              <text className={css.tick} x={13} y={8}>
                {s.label}
              </text>
            </g>
          ))}
        </g>
      )}
      {overlay?.({ box, sx, sy, hMax })}
      {hover && (
        <g className={css.tip} pointerEvents="none" transform={`translate(${Math.min(hover.p.x + 10, box.x1 - 246)} ${Math.max(hover.p.y - 70, box.y0 + 4)})`}>
          <rect width={240} height={56} rx={4} />
          <text x={8} y={15}>{series[hover.s].label} · лаг {hover.p.k + 1}</text>
          <text x={8} y={31}>h {formatNumber(hover.p.h, 4)} {unit} · {measureTitle(type)} {formatNumber(hover.p.value, 4)}</text>
          <text x={8} y={47}>Пар: {pairsText(hover.p.n)}{hover.p.few ? " — мало, в подбор не входит" : ""}</text>
        </g>
      )}
    </PlotFrame>
  );
});
