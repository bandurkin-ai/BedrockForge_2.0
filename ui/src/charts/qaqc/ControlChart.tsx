/**
 * The control chart of a standard (ТЗ 8.4.2; ADR 0020 §11): values of a group by date or by order, the lines μ,
 * ±k₂σ, ±k₃σ and the actual mean x̄, the moving average (10), points coloured by status (norm — Cobalt, warning —
 * amber, failure — red), marks of a run (a tick above) and of a possible swap (a ring). Up to 5000 points are SVG
 * circles with a tooltip each; more go to the canvas layer (`charts/core/PointsCanvas`). A click picks the nearest
 * point (its card and its batch shown); the points of the batch shown are ringed in Copper Orange.
 * SVG of a fixed size: the PNG is twice it, the canvas layer goes in as an image × 2.
 */
import { forwardRef, useMemo, type PointerEvent as RPointerEvent } from "react";
import type { StandardSeries } from "../../modules/qaqc/api";
import { STATUS_COLORS, STATUS_TITLES } from "../../modules/qaqc/format";
import { BRAND } from "../column/classes";
import { makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { toViewBox } from "../core/lasso";
import { rgba32 } from "../core/palette";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { axisMap, RASTER_FROM, rasterPoints, RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { batchPoints, dateTicks, maPath, markedPoints, nearestPoint, pointLabel, statusPixels, type ControlLine, type ControlModel } from "./controlModel";

export const CONTROL_W = 900;
export const CONTROL_H = 380;
export const CONTROL_BOX = plotBox(CONTROL_W, CONTROL_H, { left: 66, right: 50, top: 22, bottom: 46 });
const BOX = CONTROL_BOX;
const BATCH = rgba32(BRAND.copper, 1);

/** Stroke of a line of the chart: bands in the colours of their statuses, μ in the text tone, x̄ in Copper Orange. */
const LINE_STYLE: Record<ControlLine["key"], { stroke: string; dash?: string; cls?: string }> = {
  fail_hi: { stroke: STATUS_COLORS[2], dash: "6 4" },
  fail_lo: { stroke: STATUS_COLORS[2], dash: "6 4" },
  warn_hi: { stroke: STATUS_COLORS[1], dash: "4 4" },
  warn_lo: { stroke: STATUS_COLORS[1], dash: "4 4" },
  mu: { stroke: "", cls: css.curve },
  mean: { stroke: BRAND.copper, dash: "2 3" },
};

export interface ControlChartProps {
  model: ControlModel;
  series: StandardSeries;
  label: string;
  yTitle: string;
  /** the batch shown (its points are ringed) */
  batch: string | null;
  /** the index in the series of the point picked */
  picked: number | null;
  onPick?: (seriesIndex: number | null) => void;
}

export const ControlChart = forwardRef<SVGSVGElement, ControlChartProps>(function ControlChart({ model, series, label, yTitle, batch, picked, onPick }, ref) {
  const sx = useMemo(() => makeScale("linear", model.xDomain, BOX.x0, BOX.x1), [model]);
  const sy = useMemo(() => makeScale("linear", model.yDomain, BOX.y1, BOX.y0), [model]);
  const raster = model.n > RASTER_FROM;
  const pixels = useMemo(() => (raster ? statusPixels(model) : null), [model, raster]);
  const inBatch = useMemo(() => batchPoints(model, series, batch), [model, series, batch]);
  const marks = useMemo(() => markedPoints(model, series), [model, series]);
  const ma = useMemo(() => maPath(model, sx, sy), [model, sx, sy]);
  const rasterKey = useMemo(() => ({}), [model, pixels, inBatch, sx, sy]); // eslint-disable-line react-hooks/exhaustive-deps
  const px = (j: number) => sx.map(model.xs[j]) ?? BOX.x0;
  const py = (j: number) => sy.map(model.ys[j]) ?? BOX.y1;
  const pick = (e: RPointerEvent<SVGRectElement>) => {
    if (!onPick) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), CONTROL_W, CONTROL_H);
    const j = nearestPoint(model, sx, sy, p.x, p.y);
    onPick(j < 0 ? null : model.idx[j]);
  };
  const pickedJ = picked === null ? -1 : model.idx.indexOf(picked);
  const batchSet = raster ? null : new Set(inBatch);
  return (
    <PlotFrame ref={ref} box={BOX} label={label} xTitle={model.axis === "date" ? "Дата" : "Номер пробы в ряду"} yTitle={yTitle}>
      <YAxis scale={sy} box={BOX} count={6} />
      <XAxis scale={sx} box={BOX} count={8} ticks={model.axis === "date" ? dateTicks(model.xDomain) : undefined} />
      {model.lines.map((l) => {
        const y = sy.map(l.y);
        if (y === null) return null;
        const st = LINE_STYLE[l.key];
        return (
          <g key={l.key} data-line={l.key}>
            <line className={st.cls} x1={BOX.x0} x2={BOX.x1} y1={y} y2={y} stroke={st.stroke || undefined} strokeWidth={1.2} strokeDasharray={st.dash} />
            <text className={css.tick} x={BOX.x1 + 4} y={y} dominantBaseline="middle">
              {l.label}
            </text>
          </g>
        );
      })}
      <rect x={BOX.x0} y={BOX.y0} width={BOX.x1 - BOX.x0} height={BOX.y1 - BOX.y0} fill="transparent" style={{ cursor: onPick ? "pointer" : undefined }} onPointerDown={pick} />
      {raster ? (
        <g data-points={model.n} data-raster="1" data-batch={inBatch.length || undefined} pointerEvents="none">
          <RasterLayer
            box={BOX}
            version={rasterKey}
            draw={(buf, w, h) => {
              const mx = axisMap(sx, BOX.x0);
              const my = axisMap(sy, BOX.y0);
              rasterPoints(buf, w, h, model.xs, model.ys, mx, my, pixels ?? 0, 4);
              if (inBatch.length) rasterPoints(buf, w, h, model.xs, model.ys, mx, my, BATCH, 7, inBatch);
            }}
          />
        </g>
      ) : (
        <g data-points={model.n} data-batch={inBatch.length || undefined}>
          {Array.from({ length: model.n }, (_, j) => {
            const i = model.idx[j];
            const on = batchSet?.has(j);
            return (
              <circle key={i} cx={px(j)} cy={py(j)} r={on ? 4.5 : 3.2} fill={STATUS_COLORS[model.status[j]]} stroke={on ? BRAND.copper : undefined} strokeWidth={on ? 2 : undefined} data-status={model.status[j]} pointerEvents="none">
                <title>{`${series.batch[i] ?? "без партии"} · ${model.ys[j]} · ${pointLabel(series, i)}`}</title>
              </circle>
            );
          })}
        </g>
      )}
      {ma && <path className={css.curve} data-line="ma" d={ma} strokeWidth={1.4} opacity={0.8} pointerEvents="none" />}
      <g data-marks={marks.run.length + marks.swap.length} pointerEvents="none">
        {marks.run.map((j) => (
          <line key={`r${j}`} data-mark="run" x1={px(j)} x2={px(j)} y1={py(j) - 6} y2={py(j) - 11} stroke={STATUS_COLORS[1]} strokeWidth={1.5} />
        ))}
        {marks.swap.map((j) => (
          <circle key={`s${j}`} data-mark="swap" cx={px(j)} cy={py(j)} r={6.5} fill="none" stroke={BRAND.copper} strokeWidth={1.4} />
        ))}
      </g>
      {pickedJ >= 0 && <circle className={css.ring} data-picked={picked ?? undefined} cx={px(pickedJ)} cy={py(pickedJ)} r={8} pointerEvents="none" />}
      <g data-legend="status" transform={`translate(${BOX.x0 + 6} ${BOX.y0 - 8})`} pointerEvents="none">
        {STATUS_TITLES.map((t, s) => (
          <g key={t} transform={`translate(${s * 150} 0)`}>
            <circle cx={4} cy={-3} r={4} fill={STATUS_COLORS[s]} />
            <text className={css.tick} x={12} y={0}>
              {`${t}: ${model.byStatus[s].toLocaleString("ru-RU")}`}
            </text>
          </g>
        ))}
      </g>
    </PlotFrame>
  );
});
