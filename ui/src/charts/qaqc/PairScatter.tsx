/**
 * The scatter of pairs x1 — x2 (ТЗ 8.4.2; ADR 0020 §4, §11): duplicates (the original — the duplicate) and external
 * control (the main lab — the check lab). A square plot (both axes in one range) with the lines 1:1, ±10 %, ±20 %
 * and RMA, Pearson's r and RMA in the legend; points within the HARD threshold of the type in Cobalt, above it in
 * the colour of a failure, drawn over the others (the canvas in two passes, the SVG sorted). Up to 5000 points are SVG
 * circles with a tooltip each; more go to the canvas layer (`charts/core/PointsCanvas`). A click picks the nearest point (the card of its sample).
 * SVG of a fixed size: the PNG is twice it, the canvas layer goes in as an image × 2.
 */
import { forwardRef, useId, useMemo, type PointerEvent as RPointerEvent } from "react";
import type { PairPoints } from "../../modules/qaqc/api";
import { num } from "../../modules/qaqc/format";
import { BRAND } from "../column/classes";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { toViewBox } from "../core/lasso";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { axisMap, RASTER_FROM, rasterPoints, RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { drawOrder, nearestPair, PAIR_COLORS, pairPixels, type PairLineKey, type PairModel } from "./pairModel";

export const PAIR_W = 440;
export const PAIR_H = 420;
export const PAIR_BOX = plotBox(PAIR_W, PAIR_H, { left: 66, right: 30, top: 28, bottom: 46 });
const BOX = PAIR_BOX;

/** 1:1 in the text tone, ±10 % amber, ±20 % red (dashed), RMA in Copper Orange. */
const LINE_STYLE: Record<PairLineKey, { stroke?: string; dash?: string; cls?: string; width: number }> = {
  one: { cls: css.curve, width: 1.2 },
  p10: { stroke: "#f4b13a", dash: "4 4", width: 1.1 },
  m10: { stroke: "#f4b13a", dash: "4 4", width: 1.1 },
  p20: { stroke: "#e5483a", dash: "6 4", width: 1.1 },
  m20: { stroke: "#e5483a", dash: "6 4", width: 1.1 },
  rma: { stroke: BRAND.copper, width: 1.8 },
};

export interface PairScatterProps {
  model: PairModel;
  points: PairPoints;
  label: string;
  xTitle: string;
  yTitle: string;
  /** the HARD threshold of the type, % */
  limit: number;
  pearson: number | null;
  rma: { a: number | null; b: number | null } | null;
  /** the index in the answer of the point picked */
  picked: number | null;
  onPick?: (index: number | null) => void;
}

export const PairScatter = forwardRef<SVGSVGElement, PairScatterProps>(function PairScatter({ model, points, label, xTitle, yTitle, limit, pearson, rma, picked, onPick }, ref) {
  const sx = useMemo(() => makeScale("linear", model.domain, BOX.x0, BOX.x1), [model]);
  const sy = useMemo(() => makeScale("linear", model.domain, BOX.y1, BOX.y0), [model]);
  const raster = model.n > RASTER_FROM;
  const pixels = useMemo(() => (raster ? pairPixels(model) : null), [model, raster]);
  const order = useMemo(() => drawOrder(model), [model]);
  const rasterKey = useMemo(() => ({}), [model, pixels, sx, sy]); // eslint-disable-line react-hooks/exhaustive-deps
  const px = (j: number) => sx.map(model.xs[j]) ?? BOX.x0;
  const py = (j: number) => sy.map(model.ys[j]) ?? BOX.y1;
  const pick = (e: RPointerEvent<SVGRectElement>) => {
    if (!onPick) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), PAIR_W, PAIR_H);
    const j = nearestPair(model, sx, sy, p.x, p.y);
    onPick(j < 0 ? null : model.idx[j]);
  };
  const clip = `qaqc-pair-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const pickedJ = picked === null ? -1 : model.idx.indexOf(picked);
  const lim = formatNumber(limit, 3);
  const legend = [`r = ${num(pearson, 4)}`, rma && rma.a !== null && rma.b !== null ? `RMA: y = ${num(rma.a, 4)} + ${num(rma.b, 4)}·x` : "RMA: —"];
  return (
    <PlotFrame ref={ref} box={BOX} label={label} xTitle={xTitle} yTitle={yTitle}>
      <YAxis scale={sy} box={BOX} count={6} />
      <XAxis scale={sx} box={BOX} count={6} />
      <defs>
        <clipPath id={clip}>
          <rect x={BOX.x0} y={BOX.y0} width={BOX.x1 - BOX.x0} height={BOX.y1 - BOX.y0} />
        </clipPath>
      </defs>
      <rect x={BOX.x0} y={BOX.y0} width={BOX.x1 - BOX.x0} height={BOX.y1 - BOX.y0} fill="transparent" style={{ cursor: onPick ? "pointer" : undefined }} onPointerDown={pick} />
      {raster ? (
        <g data-points={model.n} data-raster="1" pointerEvents="none">
          <RasterLayer
            box={BOX}
            version={rasterKey}
            draw={(buf, w, h) => {
              const mx = axisMap(sx, BOX.x0);
              const my = axisMap(sy, BOX.y0);
              // the norm first, the pairs above the threshold over it
              rasterPoints(buf, w, h, model.xs, model.ys, mx, my, pixels ?? 0, 4, order.low);
              rasterPoints(buf, w, h, model.xs, model.ys, mx, my, pixels ?? 0, 4, order.high);
            }}
          />
        </g>
      ) : (
        <g data-points={model.n}>
          {Array.from(order.all, (j) => {
            const i = model.idx[j];
            return (
              <circle key={i} cx={px(j)} cy={py(j)} r={3} fill={PAIR_COLORS[model.over[j]]} fillOpacity={0.85} data-over={model.over[j]} pointerEvents="none">
                <title>{`x1 ${num(points.x1[i], 6)} · x2 ${num(points.x2[i], 6)} · HARD ${num(points.hard[i], 4)} %`}</title>
              </circle>
            );
          })}
        </g>
      )}
      <g clipPath={`url(#${clip})`} pointerEvents="none">
        {model.lines.map((l) => {
          const st = LINE_STYLE[l.key];
          const [a, b] = l.seg;
          return <line key={l.key} data-line={l.key} className={st.cls} x1={sx.map(a.x) ?? 0} y1={sy.map(a.y) ?? 0} x2={sx.map(b.x) ?? 0} y2={sy.map(b.y) ?? 0} stroke={st.stroke} strokeWidth={st.width} strokeDasharray={st.dash} />;
        })}
      </g>
      {model.lines
        .filter((l) => l.key !== "one" && l.key !== "rma")
        .map((l) => {
          const end = l.seg[1];
          const x = sx.map(end.x);
          const y = sy.map(end.y);
          return x === null || y === null ? null : (
            <text key={`t${l.key}`} className={css.tick} x={Math.min(x, BOX.x1) - 2} y={Math.max(y, BOX.y0) + 10} textAnchor="end" pointerEvents="none">
              {l.label}
            </text>
          );
        })}
      {pickedJ >= 0 && <circle className={css.ring} data-picked={picked ?? undefined} cx={px(pickedJ)} cy={py(pickedJ)} r={8} pointerEvents="none" />}
      <g data-legend="pairs" transform={`translate(${BOX.x0 + 6} ${BOX.y0 - 10})`} pointerEvents="none">
        {[`HARD ≤ ${lim} %: ${model.byClass[0].toLocaleString("ru-RU")}`, `HARD > ${lim} %: ${model.byClass[1].toLocaleString("ru-RU")}`].map((t, k) => (
          <g key={t} transform={`translate(${k * 120} 0)`}>
            <circle cx={4} cy={-3} r={4} fill={PAIR_COLORS[k]} />
            <text className={css.tick} x={12} y={0}>
              {t}
            </text>
          </g>
        ))}
      </g>
      <g data-legend="fit" transform={`translate(${BOX.x0 + 8} ${BOX.y0 + 16})`} pointerEvents="none">
        {legend.map((t, k) => (
          <text key={t} className={css.tick} x={0} y={k * 13}>
            {t}
          </text>
        ))}
      </g>
    </PlotFrame>
  );
});
