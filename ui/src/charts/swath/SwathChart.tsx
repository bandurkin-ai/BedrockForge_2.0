/**
 * The swath plot (ТЗ 7.30 «График»; перенос `SwathChart` BF): lines of means over the swaths shown, bars of what
 * is in each swath (the weight of blocks, the number of samples — blocks on the right axis), the legend with the
 * means over all swaths, the tooltip of the swath under the cursor (it is lit in 3D too, Т7.30-3). An empty swath
 * breaks its line: no mean is never drawn as 0. SVG of the shared frame (`charts/core`), colours of the theme.
 */
import { forwardRef, type PointerEvent as RPointerEvent } from "react";
import { niceTicks } from "../column/scale";
import { formatNumber } from "../core/axisScale";
import css from "../core/Chart.module.css";
import type { SeriesAnswer, SwathResult } from "../../modules/swath/api";
import { axisTitle, axisValue, barScale, barValues, valueAxis, type SwathSeries } from "../../modules/swath/model";

const W = 860;
const PLOT_H = 290;
const M = { left: 62, right: 86, bottom: 46 };
const BAR_SHARE = 0.4;

const big = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("ru-RU") : formatNumber(v, 4));

export interface SwathChartProps {
  result: SwathResult;
  /** the look of the series (colour, line, bars) by their number */
  look: readonly Pick<SwathSeries, "color" | "line" | "bars">[];
  range: [number, number];
  hover: number | null;
  onHover: (index: number | null) => void;
}

export const SwathChart = forwardRef<SVGSVGElement, SwathChartProps>(function SwathChart({ result, look, range, hover, onHover }, ref) {
  const [from, to] = range;
  const ok = (i: number) => !result.series[i]?.error;
  const lineShown = result.series.map((_, i) => ok(i) && (look[i]?.line ?? true));
  const barShown = result.series.map((_, i) => ok(i) && (look[i]?.bars ?? false));
  const axis = valueAxis(result.series, lineShown, [from, to], (lo, hi) => niceTicks(lo, hi, 5));
  const legend = result.series.map((s, i) => ({ s, i })).filter(({ i }) => lineShown[i] || barShown[i] || !ok(i));
  const top = 14 + legend.length * 16;
  const H = top + PLOT_H + M.bottom;
  const n = Math.max(1, to - from + 1);
  const bw = (W - M.left - M.right) / n;
  const x0 = (k: number) => M.left + (k - from) * bw;
  const y = (v: number) => top + PLOT_H - ((v - axis.min) / (axis.max - axis.min || 1)) * PLOT_H;
  const barMax = { blocks: barScale(result.series, "blocks", barShown, [from, to]), samples: barScale(result.series, "samples", barShown, [from, to]) };
  const barSeries = result.series.filter((_, i) => barShown[i]);
  const rightKind: SeriesAnswer["kind"] | null = barSeries.some((s) => s.kind === "blocks") ? "blocks" : barSeries.length ? "samples" : null;
  const rightSeries = rightKind ? barSeries.find((s) => s.kind === rightKind)! : null;
  const every = Math.max(1, Math.ceil(n / 12));
  const color = (i: number) => look[i]?.color ?? "#8a99a8";
  const units = [...new Set(result.series.filter((_, i) => lineShown[i]).map((s) => s.unit).filter(Boolean))];

  const pick = (e: RPointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const k = from + Math.min(n - 1, Math.max(0, Math.floor(((e.clientX - box.left) / (box.width || 1)) * n)));
    if (k !== hover) onHover(k);
  };
  const tip = hover !== null && hover >= from && hover <= to ? hover : null;
  const tipLines =
    tip === null
      ? []
      : [
          `Полоса ${tip + 1}: ${formatNumber(axisValue(result, result.start + tip * result.width), 7)} – ${formatNumber(axisValue(result, result.start + (tip + 1) * result.width), 7)} м`,
          ...result.series.filter((s) => !s.error).map((s) => `${s.name}: ${formatNumber(s.mean[tip], 4)} · ${s.kind === "samples" ? `проб ${s.count[tip] ?? 0}` : `${s.weight_name} ${big(s.weights[tip])} ${s.weight_unit}`}`),
        ];
  const tipW = Math.min(420, 16 + Math.max(0, ...tipLines.map((l) => l.length)) * 6.2);
  const tipX = tip === null ? 0 : Math.min(W - M.right - tipW, Math.max(M.left, x0(tip) + bw + 6));

  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className={css.svg} role="img" aria-label="Swath plot: средние по полосам" data-testid="swath-chart">
      <rect className={css.bg} x={0} y={0} width={W} height={H} />
      {legend.map(({ s, i }, row) => (
        <g key={i} transform={`translate(${M.left} ${12 + row * 16})`}>
          {lineShown[i] ? <line x1={0} x2={22} y1={0} y2={0} stroke={color(i)} strokeWidth={2.4} /> : <rect x={4} y={-5} width={14} height={10} fill={color(i)} opacity={0.45} />}
          {lineShown[i] && <circle cx={11} cy={0} r={3} fill={color(i)} />}
          <text className={css.tick} x={28} y={4}>
            {s.error ? `${s.name} — ${s.error.message}` : `${s.name}${s.unit ? `, ${s.unit}` : ""} — среднее ${formatNumber(s.total.mean, 4)} (${s.weight_name}; ${s.kind === "samples" ? "проб" : "блоков"} ${s.total.count.toLocaleString("ru-RU")})`}
          </text>
        </g>
      ))}
      {axis.ticks.map((t) => (
        <g key={t}>
          <line className={css.grid} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
          <text className={css.tick} x={M.left - 6} y={y(t) + 4} textAnchor="end">
            {formatNumber(t, 4)}
          </text>
        </g>
      ))}
      {result.series.map((s, i) => {
        if (!barShown[i] || !(barMax[s.kind] > 0)) return null;
        const slot = barSeries.indexOf(s);
        const pad = bw * 0.1;
        const each = (bw - 2 * pad) / barSeries.length;
        const values = barValues(s);
        return (
          <g key={`b${i}`} fill={color(i)} opacity={0.32}>
            {Array.from({ length: n }, (_, j) => {
              const v = values[from + j] ?? 0;
              const h = (v / barMax[s.kind]) * PLOT_H * BAR_SHARE;
              return v > 0 ? <rect key={j} x={x0(from + j) + pad + slot * each} width={Math.max(each, 0.5)} y={top + PLOT_H - h} height={h} /> : null;
            })}
          </g>
        );
      })}
      {rightSeries &&
        [0, 0.5, 1].map((f) => (
          <text key={f} className={css.tick} x={W - M.right + 6} y={top + PLOT_H - f * PLOT_H * BAR_SHARE + 4}>
            {big(f * barMax[rightKind!])}
          </text>
        ))}
      {result.series.map((s, i) => {
        if (!lineShown[i]) return null;
        let d = "";
        let pen = false;
        for (let k = from; k <= to; k++) {
          const v = s.mean[k];
          if (v === null || v === undefined || !Number.isFinite(v)) {
            pen = false;
            continue;
          }
          d += `${pen ? "L" : "M"}${(x0(k) + bw / 2).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        }
        return (
          <g key={`l${i}`}>
            <path d={d} fill="none" stroke={color(i)} strokeWidth={2.2} strokeLinejoin="round" />
            {n <= 120 &&
              Array.from({ length: n }, (_, j) => {
                const v = s.mean[from + j];
                return v === null || v === undefined || !Number.isFinite(v) ? null : <circle key={j} cx={x0(from + j) + bw / 2} cy={y(v)} r={n > 60 ? 2 : 3} fill={color(i)} />;
              })}
          </g>
        );
      })}
      <rect className={css.frame} x={M.left} y={top} width={W - M.left - M.right} height={PLOT_H} />
      {Array.from({ length: Math.ceil(n / every) }, (_, j) => from + j * every).map((k) => (
        <g key={k}>
          <line className={css.grid} x1={x0(k) + bw / 2} x2={x0(k) + bw / 2} y1={top + PLOT_H} y2={top + PLOT_H + 4} />
          <text className={css.tick} x={x0(k) + bw / 2} y={top + PLOT_H + 16} textAnchor="middle">
            {formatNumber(axisValue(result, result.centres[k]), 7)}
          </text>
        </g>
      ))}
      <text className={css.axisTitle} x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle">
        {axisTitle(result)} · полоса {formatNumber(result.width, 5)} м
      </text>
      <text className={css.axisTitle} transform={`translate(14 ${top + PLOT_H / 2}) rotate(-90)`} textAnchor="middle">
        Среднее{units.length === 1 ? `, ${units[0]}` : ""}
      </text>
      {rightSeries && (
        <text className={css.axisTitle} transform={`translate(${W - 8} ${top + PLOT_H - (PLOT_H * BAR_SHARE) / 2}) rotate(90)`} textAnchor="middle">
          {rightKind === "samples" ? "Число проб" : `${rightSeries.weight_name.slice(0, 1).toUpperCase()}${rightSeries.weight_name.slice(1)}, ${rightSeries.weight_unit}`}
        </text>
      )}
      {tip !== null && <rect x={x0(tip)} y={top} width={bw} height={PLOT_H} fill="var(--highlight)" opacity={0.14} />}
      {tip !== null && (
        <g className={css.tip} transform={`translate(${tipX} ${top + 6})`} pointerEvents="none">
          <rect width={tipW} height={8 + tipLines.length * 14} rx={4} />
          {tipLines.map((line, j) => (
            <text key={j} x={8} y={17 + j * 14}>
              {line}
            </text>
          ))}
        </g>
      )}
      <rect x={M.left} y={top} width={W - M.left - M.right} height={PLOT_H} fill="transparent" onPointerMove={pick} onPointerLeave={() => onHover(null)} />
    </svg>
  );
});
