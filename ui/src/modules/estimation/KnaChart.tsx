/**
 * A chart of the analysis of the neighbourhood (ТЗ 8.8.5): one indicator (SLOPE, KE, KV, NS) of the test blocks by the values of the
 * parameter that was varied — a line per block (the brand's colours, as the series of the swath), the recommended `n_max` as a vertical
 * copper mark, the ring at the point where a block alone would stop. A gap (the block was not estimated at that value) breaks the
 * line, it is never drawn as 0. SVG of a fixed size on the axes of Э13 (`charts/core`, not changed).
 */
import { XAxis, YAxis } from "../../charts/core/Axes";
import css from "../../charts/core/Chart.module.css";
import { domainOf, formatNumber, makeScale, type Tick } from "../../charts/core/axisScale";
import { plotBox, PlotFrame } from "../../charts/core/PlotFrame";
import { SERIES_COLORS } from "../swath/model";
import { KNA_AXIS, knaValueText, type KnaBlock, type KnaIndicators, type KnaParam } from "./checksApi";
import { blockShort } from "./knaState";

export type Indicator = "SLOPE" | "KE" | "KV" | "NS";

export const INDICATORS: { key: Indicator; title: string; hint: string }[] = [
  { key: "SLOPE", title: "Наклон регрессии SLOPE", hint: "Наклон регрессии истинного значения на оценку: 1 — оценка без условного смещения" },
  { key: "KE", title: "Эффективность кригинга KE", hint: "Доля дисперсии блока, объяснённая оценкой: ближе к 1 — лучше" },
  { key: "KV", title: "Дисперсия кригинга KV", hint: "Дисперсия ошибки оценки блока: меньше — лучше" },
  { key: "NS", title: "Число проб в оценке NS", hint: "Сколько проб вошло в систему кригинга" },
];

export const CHART_W = 440;
export const CHART_H = 270;
const BOX = plotBox(CHART_W, CHART_H, { left: 58, right: 18, top: 14, bottom: 46 });

export const blockColor = (i: number): string => SERIES_COLORS[i % SERIES_COLORS.length];

/** The points of a block's line: the value of the parameter and the indicator; a gap is null. */
export function lineOf(b: KnaBlock, param: KnaParam, key: Indicator): { x: number; y: number | null }[] {
  return (b.vary[param] ?? []).map((v) => ({ x: v.value, y: (v as KnaIndicators)[key] }));
}

/** The runs of consecutive points that have a value: a gap breaks the line. */
export function segments<T extends { y: number | null }>(points: readonly T[]): (T & { y: number })[][] {
  const out: (T & { y: number })[][] = [];
  let cur: (T & { y: number })[] = [];
  for (const p of points) {
    if (p.y === null) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push(p as T & { y: number });
  }
  if (cur.length) out.push(cur);
  return out;
}

export interface KnaChartProps {
  param: KnaParam;
  indicator: Indicator;
  blocks: readonly KnaBlock[];
  /** the common recommendation: the vertical mark (only for `n_max`) */
  recommend: number | null;
}

export function KnaChart({ param, indicator, blocks, recommend }: KnaChartProps) {
  const lines = blocks.map((b) => lineOf(b, param, indicator));
  const xs = lines.flatMap((l) => l.map((p) => p.x));
  const ys = lines.flatMap((l) => l.flatMap((p) => (p.y === null ? [] : [p.y])));
  const dx = domainOf(xs, "linear", 0.04);
  const dy = domainOf(indicator === "SLOPE" ? [...ys, 1] : ys, "linear", 0.1, indicator === "NS" ? 0 : undefined);
  const title = INDICATORS.find((i) => i.key === indicator)!.title;
  if (!dx || !dy) {
    return (
      <p className={css.muted} data-testid={`kna-chart-${indicator}`} data-empty="1">
        {title}: нет значений — блоки не оценены ни при одном значении параметра.
      </p>
    );
  }
  const sx = makeScale("linear", dx, BOX.x0, BOX.x1);
  const sy = makeScale("linear", dy, BOX.y1, BOX.y0);
  const px = (v: number) => sx.map(v) ?? BOX.x0;
  const py = (v: number) => sy.map(v) ?? BOX.y1;
  const uniq = [...new Set(xs)].sort((a, b) => a - b);
  const ticks: Tick[] | undefined = uniq.length <= 8 ? uniq.map((value) => ({ value, label: knaValueText(param, value) })) : undefined;
  const mark = param === "n_max" && recommend !== null && recommend >= dx.min && recommend <= dx.max ? recommend : null;
  return (
    <PlotFrame box={BOX} label={`${title} по значениям: ${KNA_AXIS[param]}`} xTitle={KNA_AXIS[param]} yTitle={indicator}>
      <YAxis scale={sy} box={BOX} count={6} />
      <XAxis scale={sx} box={BOX} ticks={ticks} count={8} />
      {indicator === "SLOPE" && dy.min < 1 && dy.max > 1 && <line className={css.diagonal} data-line="ideal" x1={BOX.x0} x2={BOX.x1} y1={py(1)} y2={py(1)} />}
      {mark !== null && (
        <g data-mark="recommend">
          <line className={css.mark} x1={px(mark)} x2={px(mark)} y1={BOX.y0} y2={BOX.y1} />
          <text className={css.markLabel} x={px(mark) + 5} y={BOX.y0 + 11}>
            {`n_max = ${mark}`}
          </text>
        </g>
      )}
      {lines.map((line, i) => {
        const color = blockColor(i);
        const rec = blocks[i].recommend;
        const ring = param === "n_max" && rec !== null ? line.find((p) => p.x === rec) : undefined; // where this block alone would stop
        return (
          <g key={i} data-block={i} data-points={line.filter((p) => p.y !== null).length}>
            {segments(line).map((seg, k) => (
              <path key={k} d={seg.map((p, j) => `${j ? "L" : "M"}${px(p.x).toFixed(1)} ${py(p.y).toFixed(1)}`).join("")} stroke={color} strokeWidth={2} fill="none" />
            ))}
            {line.map((p) =>
              p.y === null ? null : (
                <circle key={p.x} cx={px(p.x)} cy={py(p.y)} r={3} fill={color}>
                  <title>{`${blockShort(blocks[i], i)}: ${knaValueText(param, p.x)} → ${indicator} = ${formatNumber(p.y, 4)}`}</title>
                </circle>
              ),
            )}
            {ring && ring.y !== null && <circle className={css.ring} data-ring={ring.x} cx={px(ring.x)} cy={py(ring.y)} r={6.5} />}
          </g>
        );
      })}
    </PlotFrame>
  );
}
