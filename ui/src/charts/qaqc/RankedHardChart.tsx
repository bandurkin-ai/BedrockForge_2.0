/**
 * The ranked HARD (ТЗ 8.4.2; ADR 0020 §4, §11): HARD of the pairs ascending against 100·i / n — the points as the
 * core answered them (up to 2000), the threshold of the type (a horizontal line), the share the criterion needs (a
 * vertical line) and the share reached at the threshold (a mark on the threshold line; the shares are of all
 * pairs). Points above the threshold are in the colour of a failure. SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useMemo } from "react";
import { pct } from "../../modules/qaqc/format";
import { BRAND } from "../column/classes";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import css from "../core/Chart.module.css";
import { PAIR_COLORS, rankedPath, type RankedModel } from "./pairModel";

export const RANKED_W = 440;
export const RANKED_H = 320;
export const RANKED_BOX = plotBox(RANKED_W, RANKED_H, { left: 58, right: 24, top: 26, bottom: 46 });
const BOX = RANKED_BOX;

export interface RankedHardChartProps {
  model: RankedModel;
  label: string;
  /** «пар 1 234» — under the legend */
  note?: string;
}

export const RankedHardChart = forwardRef<SVGSVGElement, RankedHardChartProps>(function RankedHardChart({ model, label, note }, ref) {
  const sx = useMemo(() => makeScale("linear", { min: 0, max: 100 }, BOX.x0, BOX.x1), []);
  const sy = useMemo(() => makeScale("linear", model.yDomain, BOX.y1, BOX.y0), [model]);
  const path = useMemo(() => rankedPath(model, sx, sy), [model, sx, sy]);
  const yLimit = sy.map(model.limit);
  const xShare = sx.map(model.shareLimit);
  const xAt = model.shareAt === null ? null : sx.map(model.shareAt);
  const ok = model.shareAt !== null && model.shareAt >= model.shareLimit - 1e-9 * Math.max(1, model.shareLimit);
  const lim = formatNumber(model.limit, 3);
  return (
    <PlotFrame ref={ref} box={BOX} label={label} xTitle="Доля пар, %" yTitle="HARD, %">
      <YAxis scale={sy} box={BOX} count={5} />
      <XAxis scale={sx} box={BOX} count={10} />
      {path && <path className={css.curve} data-line="ranked" d={path} strokeWidth={1.2} opacity={0.7} />}
      <g data-points={model.n}>
        {Array.from({ length: model.n }, (_, j) => (
          <circle key={j} cx={sx.map(model.xs[j]) ?? BOX.x0} cy={sy.map(model.ys[j]) ?? BOX.y1} r={2.4} fill={PAIR_COLORS[model.over[j]]} data-rank={j} data-over={model.over[j]}>
            <title>{`${formatNumber(model.xs[j], 4)} % пар · HARD ${formatNumber(model.ys[j], 4)} %`}</title>
          </circle>
        ))}
      </g>
      {yLimit !== null && (
        <g data-mark="limit" data-value={model.limit}>
          <line x1={BOX.x0} x2={BOX.x1} y1={yLimit} y2={yLimit} stroke={PAIR_COLORS[1]} strokeWidth={1.2} strokeDasharray="6 4" />
          <text className={css.tick} x={BOX.x0 + 4} y={yLimit - 4}>
            {`порог HARD ${lim} %`}
          </text>
        </g>
      )}
      {xShare !== null && (
        <g data-mark="share" data-value={model.shareLimit}>
          <line x1={xShare} x2={xShare} y1={BOX.y0} y2={BOX.y1} stroke={BRAND.slate} strokeWidth={1.2} strokeDasharray="4 4" />
          <text className={css.tick} x={xShare - 4} y={BOX.y0 + 12} textAnchor="end">
            {`нужно ${formatNumber(model.shareLimit, 3)} %`}
          </text>
        </g>
      )}
      {xAt !== null && yLimit !== null && (
        <g data-mark="at" data-value={model.shareAt ?? undefined} data-passed={ok ? 1 : 0}>
          <circle cx={xAt} cy={yLimit} r={5} fill="none" stroke={ok ? BRAND.cobalt : PAIR_COLORS[1]} strokeWidth={2} />
          <text className={css.tick} x={Math.min(xAt + 7, BOX.x1 - 60)} y={yLimit + 14}>
            {`${pct(model.shareAt)} пар ≤ ${lim} %`}
          </text>
        </g>
      )}
      {note && (
        <text className={css.tick} x={BOX.x0} y={BOX.y0 - 10}>
          {note}
        </text>
      )}
    </PlotFrame>
  );
});
