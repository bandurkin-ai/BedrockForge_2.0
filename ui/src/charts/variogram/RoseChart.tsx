/**
 * The rose of ranges (ТЗ 8.6.4 «Веер»; ADR 0022 §5, decision 8): for each ray of the fan the first distance where
 * γ̂ ≥ q·σ², drawn at theta and theta + 180° in the plane (a1 up, a2 right). A ray whose level is not reached has no
 * range: a dashed spoke to the edge «> h_max» — the outline is broken there, never closed over a zero. A click picks
 * the nearest ray (the direction of the major axis in the plane); the picked one is marked. SVG of `charts/core`.
 */
import { forwardRef, useState, type MouseEvent as RMouseEvent } from "react";
import { formatNumber } from "../core/axisScale";
import { toViewBox } from "../core/lasso";
import { niceTicks } from "../column/scale";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { seriesColor } from "../core/palette";
import css from "../core/Chart.module.css";
import { mod180, nearestRay, polar, roseRadius, roseRuns, thetaOf, type RoseData, type RoseRay } from "./scale";

export const ROSE_SIZE = 360;

export interface RoseChartProps {
  rose: RoseData;
  /** the radius of the chart (the farthest lag); absent — the longest range × 1,15 */
  hMax?: number | null;
  unit?: string;
  title?: string;
  /** the ray picked (theta, °) */
  selected?: number | null;
  onPick?: (theta: number) => void;
  size?: number;
}

export const RoseChart = forwardRef<SVGSVGElement, RoseChartProps>(function RoseChart({ rose, hMax, unit = "м", title, selected, onPick, size = ROSE_SIZE }, ref) {
  const [hover, setHover] = useState<RoseRay | null>(null);
  const box = plotBox(size, size, { left: 26, right: 26, top: 26, bottom: 26 });
  const cx = (box.x0 + box.x1) / 2;
  const cy = (box.y0 + box.y1) / 2;
  const R = (box.x1 - box.x0) / 2;
  const radius = roseRadius(rose.rays, hMax);
  const label = title ?? "Роза дальностей";
  const color = seriesColor(0);
  const at = (theta: number, r: number) => {
    const [dx, dy] = polar(theta, radius ? (Math.min(r, radius) / radius) * R : 0);
    return [cx + dx, cy + dy] as const;
  };
  const pt = (theta: number, r: number) => at(theta, r).map((v) => v.toFixed(1)).join(",");
  const rings = radius ? niceTicks(0, radius, 4).filter((t) => t > 0 && t <= radius * (1 + 1e-9)) : [];
  const { runs, closed } = roseRuns(rose.rays);
  const pickAt = (e: RMouseEvent<SVGElement>): RoseRay | null => {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return null;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), size, size);
    return nearestRay(rose.rays, thetaOf(p.x - cx, p.y - cy));
  };
  const reached = rose.rays.filter((r) => r.range !== null).length;
  return (
    <PlotFrame ref={ref} box={box} label={label} onPointerLeave={() => setHover(null)}>
      <g data-rose-rings={rings.length}>
        {rings.map((t) => (
          <g key={t}>
            <circle className={css.grid} cx={cx} cy={cy} r={(t / (radius as number)) * R} fill="none" />
            <text className={css.tick} x={cx + 3} y={cy - (t / (radius as number)) * R - 2}>
              {formatNumber(t, 3)}
            </text>
          </g>
        ))}
        <line className={css.grid} x1={cx} x2={cx} y1={box.y0} y2={box.y1} />
        <line className={css.grid} x1={box.x0} x2={box.x1} y1={cy} y2={cy} />
        <text className={css.axisTitle} x={cx} y={box.y0 - 8} textAnchor="middle">
          a1
        </text>
        <text className={css.axisTitle} x={box.x1 + 4} y={cy + 4}>
          a2
        </text>
      </g>
      {!radius && (
        <text className={css.tick} data-empty="rose" x={cx} y={cy + 18} textAnchor="middle">
          Уровень q·σ² не достигнут ни по одному направлению
        </text>
      )}
      {radius && (
        <g data-rose="outline" data-runs={runs.length} data-closed={closed || undefined}>
          {runs.map((run, k) =>
            closed ? (
              <polygon key={k} points={run.map((v) => pt(v.theta, v.range)).join(" ")} fill={color} fillOpacity={0.18} stroke={color} strokeWidth={1.6} />
            ) : (
              <polyline key={k} points={run.map((v) => pt(v.theta, v.range)).join(" ")} fill="none" stroke={color} strokeWidth={1.6} />
            ),
          )}
        </g>
      )}
      {radius &&
        rose.rays.map((r, i) =>
          [r.theta, r.theta + 180].map((t, half) => {
            const key = `${i}-${half}`;
            if (r.range === null) {
              const [x, y] = at(t, radius);
              return (
                <g key={key} data-ray={r.theta} data-beyond>
                  <line x1={cx} y1={cy} x2={x} y2={y} stroke={color} strokeOpacity={0.6} strokeDasharray="3 4" />
                  {half === 0 && (
                    <text className={css.tick} x={x} y={y} dx={4} dy={-3}>
                      &gt; h
                    </text>
                  )}
                </g>
              );
            }
            const [x, y] = at(t, r.range);
            return <circle key={key} data-ray={r.theta} cx={x} cy={y} r={3} fill={color} onPointerEnter={() => setHover(r)} />;
          }),
        )}
      {selected !== null && selected !== undefined && radius && (
        <g data-selected={mod180(selected)}>
          {[selected, selected + 180].map((t) => {
            const [x, y] = at(t, radius);
            return <line key={t} className={css.mark} x1={cx} y1={cy} x2={x} y2={y} />;
          })}
        </g>
      )}
      <circle
        cx={cx}
        cy={cy}
        r={R}
        fill="transparent"
        style={{ cursor: onPick ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(pickAt(e))}
        onClick={(e) => {
          const r = pickAt(e);
          if (r && onPick) onPick(mod180(r.theta));
        }}
      />
      <text className={css.tick} x={box.x0 - 18} y={size - 8}>
        {`q = ${formatNumber(rose.q, 3)} · уровень ${formatNumber(rose.level, 4)} · достигнут: ${reached} из ${rose.rays.length}`}
      </text>
      {hover && (
        <g className={css.tip} pointerEvents="none" transform={`translate(${box.x0} ${box.y0})`}>
          <rect width={196} height={40} rx={4} />
          <text x={8} y={15}>θ {formatNumber(hover.theta, 4)}° от a1 к a2</text>
          <text x={8} y={31}>{hover.range === null ? "Дальность > h_max" : `Дальность ${formatNumber(hover.range, 4)} ${unit}`}{onPick ? " · щелчок — ось" : ""}</text>
        </g>
      )}
    </PlotFrame>
  );
});
