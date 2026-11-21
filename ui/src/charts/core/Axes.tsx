/** Axes of a chart in SVG (ADR 0019 §11): grid lines and tick labels of a scale (linear, log10, probability). */
import type { Scale, Tick } from "./axisScale";
import type { PlotBox } from "./PlotFrame";
import css from "./Chart.module.css";

/** Ticks inside the scale's pixels (a tick a hair outside the domain is dropped). */
function placed(scale: Scale, ticks: Tick[]): { px: number; label: string; value: number }[] {
  const [a, b] = scale.range;
  const lo = Math.min(a, b) - 0.5;
  const hi = Math.max(a, b) + 0.5;
  return ticks.flatMap((t) => {
    const px = scale.map(t.value);
    return px !== null && px >= lo && px <= hi ? [{ px, label: t.label, value: t.value }] : [];
  });
}

export function XAxis({ scale, box, count = 8, grid = true, ticks }: { scale: Scale; box: PlotBox; count?: number; grid?: boolean; ticks?: Tick[] }) {
  return (
    <g data-axis="x">
      {placed(scale, ticks ?? scale.ticks(count)).map((t) => (
        <g key={`x${t.value}`}>
          {grid && <line className={css.grid} x1={t.px} x2={t.px} y1={box.y0} y2={box.y1} />}
          <text className={css.tick} x={t.px} y={box.y1 + 15} textAnchor="middle">
            {t.label}
          </text>
        </g>
      ))}
    </g>
  );
}

export function YAxis({ scale, box, count = 5, grid = true, side = "left", ticks }: { scale: Scale; box: PlotBox; count?: number; grid?: boolean; side?: "left" | "right"; ticks?: Tick[] }) {
  const left = side === "left";
  return (
    <g data-axis={left ? "y" : "y2"}>
      {placed(scale, ticks ?? scale.ticks(count)).map((t) => (
        <g key={`y${t.value}`}>
          {grid && <line className={css.grid} x1={box.x0} x2={box.x1} y1={t.px} y2={t.px} />}
          <text className={css.tick} x={left ? box.x0 - 6 : box.x1 + 6} y={t.px} textAnchor={left ? "end" : "start"} dominantBaseline="middle">
            {t.label}
          </text>
        </g>
      ))}
    </g>
  );
}
