/**
 * The chart «Отклонение по глубине» of «План / факт» (ТЗ 7.35 Д-34 ч. 2): the distance d between the actual path and the planned one (the vertical axis,
 * m) at every depth (the horizontal axis, m), one line, the largest deviation marked. SVG of a fixed size on the axes of Э13 (`charts/core`, not
 * changed); the colours are tokens of the theme (the line — the accent, the mark — the highlight). Equal paths give a line on 0.
 */
import { XAxis, YAxis } from "../../charts/core/Axes";
import css from "../../charts/core/Chart.module.css";
import { domainOf, formatNumber, makeScale } from "../../charts/core/axisScale";
import { plotBox, PlotFrame } from "../../charts/core/PlotFrame";
import type { CompareAnswer } from "./plannedApi";
import { chartPoints } from "./plannedModel";

export const CHART_W = 520;
export const CHART_H = 250;
const BOX = plotBox(CHART_W, CHART_H, { left: 58, right: 18, top: 14, bottom: 46 });

export function PlannedChart({ answer }: { answer: CompareAnswer }) {
  const points = chartPoints(answer.rows);
  const dx = domainOf(points.map((p) => p.x), "linear", 0.02);
  const dy = domainOf(points.map((p) => p.y).concat(0), "linear", 0.1, 0);
  if (!dx || !dy) {
    return (
      <p className={css.muted} data-testid="pl-chart" data-empty="1">
        Нечего рисовать: траектории не пересекаются по глубине.
      </p>
    );
  }
  const sx = makeScale("linear", dx, BOX.x0, BOX.x1);
  const sy = makeScale("linear", dy, BOX.y1, BOX.y0);
  const px = (v: number) => sx.map(v) ?? BOX.x0;
  const py = (v: number) => sy.map(v) ?? BOX.y1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${px(p.x).toFixed(1)} ${py(p.y).toFixed(1)}`).join("");
  const peak = answer.max !== null && answer.max_depth !== null ? { x: answer.max_depth, y: answer.max } : null;
  return (
    <PlotFrame box={BOX} label={`Отклонение факта от плана по глубине: «${answer.hole}»`} xTitle="Глубина, м" yTitle="Отклонение d, м">
      <YAxis scale={sy} box={BOX} count={6} />
      <XAxis scale={sx} box={BOX} count={8} />
      <path d={d} stroke="var(--accent)" strokeWidth={2} fill="none" data-testid="pl-chart" data-points={points.length} />
      {peak && (
        <g data-mark="max">
          <circle className={css.ring} cx={px(peak.x)} cy={py(peak.y)} r={5} />
          <text className={css.markLabel} x={Math.min(px(peak.x) + 8, BOX.x1 - 90)} y={Math.max(py(peak.y) - 8, BOX.y0 + 11)}>
            {`max ${formatNumber(peak.y, 3)} м`}
          </text>
        </g>
      )}
    </PlotFrame>
  );
}
