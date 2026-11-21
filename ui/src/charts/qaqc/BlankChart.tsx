/**
 * The charts of blanks (ТЗ 8.4.2; ADR 0020 §11): `BlankChart` — the blanks of a laboratory by date or by order
 * with the line k_б·DL, points coloured by status (norm — Cobalt, failure — red, «не оценён» — Slate Grey);
 * `CarryChart` — the carry-over scatter «предыдущая проба партии — бланк» with the same line and colours, r and the
 * number of pairs in the title. Up to 5000 points are SVG circles with a tooltip each; more go to the canvas layer
 * (`charts/core/PointsCanvas`). A click picks the nearest point (its card and its batch shown); the points of the
 * batch shown are ringed in Copper Orange. SVG of a fixed size: the PNG is twice it.
 */
import { forwardRef, useMemo, type PointerEvent as RPointerEvent } from "react";
import { BRAND } from "../column/classes";
import { makeScale, type Scale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { toViewBox } from "../core/lasso";
import { rgba32 } from "../core/palette";
import { plotBox, PlotFrame, type PlotBox } from "../core/PlotFrame";
import { axisMap, RASTER_FROM, rasterPoints, RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { BLANK_STATUS_COLORS, BLANK_STATUS_TITLES, blankBatchPoints, blankPixels, nearestBlank, ownLimits, type BlankLine, type BlankModel, type BlankPoints, type BlankSeries } from "./blankModel";
import { dateTicks, paintOrder } from "./controlModel";

export const BLANK_W = 900;
export const BLANK_H = 340;
export const BLANK_BOX = plotBox(BLANK_W, BLANK_H, { left: 66, right: 60, top: 22, bottom: 46 });
export const CARRY_W = 460;
export const CARRY_H = 340;
export const CARRY_BOX = plotBox(CARRY_W, CARRY_H, { left: 66, right: 60, top: 22, bottom: 46 });
const BATCH = rgba32(BRAND.copper, 1);

/** The statuses of the legend: norm, failure and «не оценён» always, a warning only when there is one. */
export const legendStatuses = (byStatus: readonly number[]): number[] => [0, 1, 2, 3].filter((s) => s !== 1 || byStatus[1] > 0);

interface LayerProps {
  box: PlotBox;
  width: number;
  height: number;
  points: BlankPoints;
  series: BlankSeries;
  sx: Scale;
  sy: Scale;
  lines: BlankLine[];
  batch: string | null;
  picked: number | null;
  onPick?: (seriesIndex: number | null) => void;
  /** the tooltip of a point */
  title: (i: number, j: number) => string;
  /** the width of an entry of the legend */
  step?: number;
  /** the own threshold k_б·DL of each point drawn (the blanks of a lab with different DL): a dash at each point */
  own?: Float64Array | null;
}

/** The line of the threshold, the points (SVG or canvas), the batch shown, the point picked and the legend. */
function Layer({ box, width, height, points, series, sx, sy, lines, batch, picked, onPick, title, step = 140, own = null }: LayerProps) {
  const order = useMemo(() => paintOrder(points.status), [points]);
  const raster = points.n > RASTER_FROM;
  const pixels = useMemo(() => (raster ? blankPixels(points) : null), [points, raster]);
  const inBatch = useMemo(() => blankBatchPoints(points, series, batch), [points, series, batch]);
  const rasterKey = useMemo(() => ({}), [points, pixels, inBatch, sx, sy]); // eslint-disable-line react-hooks/exhaustive-deps
  const px = (j: number) => sx.map(points.xs[j]) ?? box.x0;
  const py = (j: number) => sy.map(points.ys[j]) ?? box.y1;
  const pick = (e: RPointerEvent<SVGRectElement>) => {
    if (!onPick) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), width, height);
    const j = nearestBlank(points, sx, sy, p.x, p.y);
    onPick(j < 0 ? null : points.idx[j]);
  };
  const pickedJ = picked === null ? -1 : points.idx.indexOf(picked);
  const batchSet = raster ? null : new Set(inBatch);
  return (
    <>
      {lines.map((l) => {
        const y = sy.map(l.y);
        if (y === null) return null;
        return (
          <g key={l.key} data-line={l.key} data-y={l.y}>
            <line x1={box.x0} x2={box.x1} y1={y} y2={y} stroke={BLANK_STATUS_COLORS[2]} strokeWidth={1.2} strokeDasharray="6 4" />
            <text className={css.tick} x={box.x1 + 4} y={y} dominantBaseline="middle">
              {l.label}
            </text>
          </g>
        );
      })}
      <rect x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} fill="transparent" style={{ cursor: onPick ? "pointer" : undefined }} onPointerDown={pick} />
      {raster ? (
        <g data-points={points.n} data-raster="1" data-batch={inBatch.length || undefined} pointerEvents="none">
          <RasterLayer
            box={box}
            version={rasterKey}
            draw={(buf, w, h) => {
              const mx = axisMap(sx, box.x0);
              const my = axisMap(sy, box.y0);
              rasterPoints(buf, w, h, points.xs, points.ys, mx, my, pixels ?? 0, 4, order);
              if (inBatch.length) rasterPoints(buf, w, h, points.xs, points.ys, mx, my, BATCH, 7, inBatch);
            }}
          />
        </g>
      ) : (
        <g data-points={points.n} data-batch={inBatch.length || undefined}>
          {Array.from(order, (j) => {
            const i = points.idx[j];
            const on = batchSet?.has(j);
            return (
              <circle key={i} cx={px(j)} cy={py(j)} r={on ? 4.5 : 3.2} fill={BLANK_STATUS_COLORS[points.status[j]]} stroke={on ? BRAND.copper : undefined} strokeWidth={on ? 2 : undefined} data-status={points.status[j]} pointerEvents="none">
                <title>{title(i, j)}</title>
              </circle>
            );
          })}
        </g>
      )}
      {own && (
        <g data-own-limits={own.length} pointerEvents="none">
          {Array.from({ length: Math.min(points.n, 5000) }, (_, j) => {
            const y = Number.isFinite(own[j]) ? sy.map(own[j]) : null;
            return y === null ? null : <line key={j} data-own-limit={own[j]} x1={px(j) - 5} x2={px(j) + 5} y1={y} y2={y} stroke={BLANK_STATUS_COLORS[2]} strokeWidth={1.2} />;
          })}
        </g>
      )}
      {pickedJ >= 0 && <circle className={css.ring} data-picked={picked ?? undefined} cx={px(pickedJ)} cy={py(pickedJ)} r={8} pointerEvents="none" />}
      <g data-legend="status" transform={`translate(${box.x0 + 6} ${box.y0 - 8})`} pointerEvents="none">
        {legendStatuses(points.byStatus).map((s, k) => (
          <g key={s} data-legend-status={s} transform={`translate(${k * step} 0)`}>
            <circle cx={4} cy={-3} r={4} fill={BLANK_STATUS_COLORS[s]} />
            <text className={css.tick} x={12} y={0}>
              {`${BLANK_STATUS_TITLES[s]}: ${points.byStatus[s].toLocaleString("ru-RU")}`}
            </text>
          </g>
        ))}
      </g>
    </>
  );
}

interface ChartProps<M extends BlankPoints> {
  model: M;
  series: BlankSeries;
  label: string;
  /** the element with its unit */
  yTitle: string;
  batch: string | null;
  picked: number | null;
  onPick?: (seriesIndex: number | null) => void;
}

const tip = (series: BlankSeries, i: number, y: number, status: string) => `${series.batch[i] ?? "без партии"} · ${y} · ${status}`;

export const BlankChart = forwardRef<SVGSVGElement, ChartProps<BlankModel> & { orderTitle?: string }>(function BlankChart({ model, series, label, yTitle, batch, picked, onPick, orderTitle = "Номер бланка в ряду" }, ref) {
  const own = useMemo(() => (model.lines.length ? null : ownLimits(model, series)), [model, series]);
  const sx = useMemo(() => makeScale("linear", model.xDomain, BLANK_BOX.x0, BLANK_BOX.x1), [model]);
  const sy = useMemo(() => makeScale("linear", model.yDomain, BLANK_BOX.y1, BLANK_BOX.y0), [model]);
  return (
    <PlotFrame ref={ref} box={BLANK_BOX} label={label} xTitle={model.axis === "date" ? "Дата" : orderTitle} yTitle={yTitle}>
      <YAxis scale={sy} box={BLANK_BOX} count={6} />
      <XAxis scale={sx} box={BLANK_BOX} count={8} ticks={model.axis === "date" ? dateTicks(model.xDomain) : undefined} />
      <Layer box={BLANK_BOX} width={BLANK_W} height={BLANK_H} points={model} series={series} sx={sx} sy={sy} lines={model.lines} batch={batch} picked={picked} onPick={onPick} own={own} title={(i, j) => tip(series, i, model.ys[j], BLANK_STATUS_TITLES[model.status[j]])} />
    </PlotFrame>
  );
});

export const CarryChart = forwardRef<SVGSVGElement, ChartProps<BlankPoints> & { lines: BlankLine[]; xTitle: string }>(function CarryChart({ model, series, label, xTitle, yTitle, lines, batch, picked, onPick }, ref) {
  const yDomain = useMemo(() => {
    let { min, max } = model.yDomain;
    for (const l of lines) [min, max] = [Math.min(min, l.y), Math.max(max, l.y * 1.04)];
    return { min, max };
  }, [model, lines]);
  const sx = useMemo(() => makeScale("linear", model.xDomain, CARRY_BOX.x0, CARRY_BOX.x1), [model]);
  const sy = useMemo(() => makeScale("linear", yDomain, CARRY_BOX.y1, CARRY_BOX.y0), [yDomain]);
  return (
    <PlotFrame ref={ref} box={CARRY_BOX} label={label} xTitle={xTitle} yTitle={yTitle}>
      <YAxis scale={sy} box={CARRY_BOX} count={6} />
      <XAxis scale={sx} box={CARRY_BOX} count={5} />
      <Layer box={CARRY_BOX} width={CARRY_W} height={CARRY_H} points={model} series={series} sx={sx} sy={sy} lines={lines} batch={batch} picked={picked} onPick={onPick} step={108} title={(i, j) => `${tip(series, i, model.ys[j], BLANK_STATUS_TITLES[model.status[j]])} · предыдущая ${model.xs[j]}`} />
    </PlotFrame>
  );
});
