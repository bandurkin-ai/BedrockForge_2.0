/**
 * The Thompson–Howard chart (ТЗ 8.4.2; ADR 0020 §4, §11): the points of the groups (the mean m̄ of the pairs of a
 * group — the median |x1 − x2|) and the line med = a + b·m fitted by the core; a, b, K and K·b in the legend. The
 * table P(c) is beside the chart (`ThTable`). With fewer than 50 pairs the core answers `few_pairs` and the tab
 * shows no chart. SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useMemo } from "react";
import type { ThompsonHoward } from "../../modules/qaqc/api";
import { num, pct } from "../../modules/qaqc/format";
import { BRAND } from "../column/classes";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { thFormula, type ThModel } from "./pairModel";

export const TH_W = 440;
export const TH_H = 320;
export const TH_BOX = plotBox(TH_W, TH_H, { left: 62, right: 24, top: 26, bottom: 46 });
const BOX = TH_BOX;

export interface ThChartProps {
  model: ThModel;
  th: ThompsonHoward;
  label: string;
  /** the unit of the element, for the axis titles */
  unit: string | null;
}

export const ThChart = forwardRef<SVGSVGElement, ThChartProps>(function ThChart({ model, th, label, unit }, ref) {
  const sx = useMemo(() => makeScale("linear", model.xDomain, BOX.x0, BOX.x1), [model]);
  const sy = useMemo(() => makeScale("linear", model.yDomain, BOX.y1, BOX.y0), [model]);
  const u = unit ? `, ${unit}` : "";
  const line = model.line;
  return (
    <PlotFrame ref={ref} box={BOX} label={label} xTitle={`Среднее пары m̄${u}`} yTitle={`Медиана |x1 − x2|${u}`}>
      <YAxis scale={sy} box={BOX} count={5} />
      <XAxis scale={sx} box={BOX} count={6} />
      {line && (
        <line data-line="th" x1={sx.map(line[0].x) ?? BOX.x0} y1={sy.map(line[0].y) ?? BOX.y1} x2={sx.map(line[1].x) ?? BOX.x1} y2={sy.map(line[1].y) ?? BOX.y0} stroke={BRAND.copper} strokeWidth={1.6} />
      )}
      <g data-points={model.n}>
        {Array.from({ length: model.n }, (_, j) => (
          <circle key={j} cx={sx.map(model.xs[j]) ?? BOX.x0} cy={sy.map(model.ys[j]) ?? BOX.y1} r={3.6} fill={BRAND.cobalt} data-group={j}>
            <title>{`группа ${j + 1}: m̄ ${formatNumber(model.xs[j], 5)} · медиана ${formatNumber(model.ys[j], 5)}`}</title>
          </circle>
        ))}
      </g>
      <text className={css.tick} data-legend="th" x={BOX.x0 + 6} y={BOX.y0 - 10}>
        {`${thFormula(th)} · пар в группе ${num(th.group, 4)}`}
      </text>
    </PlotFrame>
  );
});

/** The table P(c) = 200·σ(c) / c, %: the values c given by the core (P10, P50, P90 of m and those set); empty — «—». */
export function ThTable({ precision, unit }: { precision: ThompsonHoward["precision"]; unit: string | null }) {
  return (
    <table className={css.stats} aria-label="Точность P(c)">
      <thead>
        <tr>
          <th>c{unit ? `, ${unit}` : ""}</th>
          <th>P(c)</th>
        </tr>
      </thead>
      <tbody>
        {precision.map((r, i) => (
          <tr key={`${r.c}|${i}`}>
            <th>{num(r.c, 5)}</th>
            <td>{pct(r.p, 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
