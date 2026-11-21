/**
 * The scatter plot (ТЗ 8.3; ADR 0019 §11): linear or log10 axes in SVG, the points — SVG circles with a tooltip up to
 * 5000, more on the canvas layer under the lines; more than 200 000 pairs — the density cells 100 × 100; the line of
 * the chosen fit and 1:1; a frame or a lasso in the coordinates of the axes (the selected points in Copper Orange).
 * The frame or the lasso makes the selection of samples of the application (№ 237: 3D, the table); its points and
 * those of the hole selected are marked too, whoever made them; the selection gone — the frame goes.
 * SVG of a fixed size: the PNG is twice it, the canvas layer goes in as an image × 2.
 */
import { forwardRef, useCallback, useMemo, useState } from "react";
import { scatterSelectionBody, useChartSelection, useMarkedRows } from "../../modules/charts/selection";
import { markIndices } from "../../shell/sampleSelection";
import type { ScatterResult } from "../../modules/charts/apiMore";
import type { Regression } from "../../modules/charts/settings";
import { formatNumber, makeScale, type AxisKind } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { useLasso, type AxisPolygon, type SelectTool } from "../core/lasso";
import { BRAND } from "../column/classes";
import { rgba32 } from "../core/palette";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { axisMap, RASTER_FROM, rasterPoints, rasterRects, RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { colorLegend, densityRects, diagonalPoints, fitSegment, pointColors, pointPixels, scatterDomains } from "./model";

export const SCATTER_W = 640;
export const SCATTER_H = 520;
const BOX = plotBox(SCATTER_W, SCATTER_H, { left: 66, right: 22, top: 18, bottom: 50 });

export interface ScatterChartProps {
  result: ScatterResult;
  kx: AxisKind;
  ky: AxisKind;
  regression: Regression;
  diagonal: boolean;
  color: string;
  xLabel: string;
  yLabel: string;
  /** the tool of the pointer: a frame, a lasso; null — the tooltips only */
  tool?: SelectTool | null;
  /** the polygon of the selection (data units) and the indices of the points inside */
  selection?: AxisPolygon | null;
  selected?: readonly number[];
  onSelect?: (poly: AxisPolygon | null) => void;
}

const SELECTED = rgba32(BRAND.copper, 1);

export const ScatterChart = forwardRef<SVGSVGElement, ScatterChartProps>(function ScatterChart(p, ref) {
  const { result: r, kx, ky } = p;
  const [hover, setHover] = useState<number | null>(null);
  const d = scatterDomains(r, kx, ky);
  const scales = useMemo(() => (d ? { sx: makeScale(kx, d.x, BOX.x0, BOX.x1), sy: makeScale(ky, d.y, BOX.y1, BOX.y0) } : null), [d?.x.min, d?.x.max, d?.y.min, d?.y.max, kx, ky]); // eslint-disable-line react-hooks/exhaustive-deps
  const xs = r.x ?? [];
  const ys = r.y ?? [];
  const raster = r.mode !== "density" && xs.length > RASTER_FROM;
  // SVG circles take a colour each; the canvas layer — pixels
  const colors = useMemo(() => (raster ? [] : pointColors(r, xs.length, p.color)), [r, p.color, raster]); // eslint-disable-line react-hooks/exhaustive-deps
  const pixels = useMemo(() => (raster ? pointPixels(r, xs.length, p.color) : null), [r, p.color, raster]); // eslint-disable-line react-hooks/exhaustive-deps
  const onSelect = p.onSelect;
  const link = useChartSelection(useCallback(() => onSelect?.(null), [onSelect]));
  const onDone = useCallback(
    (poly: AxisPolygon | null) => {
      onSelect?.(poly);
      if (poly) void link.select(scatterSelectionBody(r, poly, p.tool ?? null, kx, ky));
    },
    [onSelect, link.select, r, p.tool, kx, ky], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const lasso = useLasso(p.tool ?? null, BOX, scales, onDone);
  // the points of the selection of the application (made here, by the table, by the hole selected)
  const marked = useMarkedRows();
  const markedIdx = useMemo(() => (r.mode !== "density" && r.row ? markIndices(r.row, marked) : []), [r, marked]);
  const allSelected = useMemo(() => (markedIdx.length ? [...new Set([...(p.selected ?? []), ...markedIdx])].sort((a, b) => a - b) : (p.selected ?? [])), [p.selected, markedIdx]);
  // the canvas layer is drawn again only for new data, colours, selection or axes — not while a lasso is drawn
  const rasterKey = useMemo(() => ({}), [r, pixels, allSelected, scales]);
  if (!d || !scales) return null;
  const { sx, sy } = scales;
  const px = (v: number) => sx.map(v) ?? BOX.x0;
  const py = (v: number) => sy.map(v) ?? BOX.y1;
  const density = r.mode === "density" && r.density ? r.density : null;
  const selected = allSelected;
  const isSel = new Set(raster ? [] : selected);
  const fit = p.regression === "none" ? null : fitSegment(r.fits[p.regression], d.x, d.y, kx, ky);
  const diag = p.diagonal ? diagonalPoints(d.x, d.y, kx, ky) : [];
  const legend = colorLegend(r);
  const h = hover !== null && !raster ? hover : null;
  const poly = (pts: { x: number; y: number }[]) => pts.map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(" ");
  return (
    <PlotFrame ref={ref} box={BOX} label={`Рассеяние: ${p.yLabel} против ${p.xLabel}`} xTitle={`${p.xLabel}${kx === "log" ? " (log)" : ""}`} yTitle={`${p.yLabel}${ky === "log" ? " (log)" : ""}`} onPointerLeave={() => setHover(null)}>
      <YAxis scale={sy} box={BOX} count={7} />
      <XAxis scale={sx} box={BOX} count={7} />
      <g {...lasso.handlers} data-tool={p.tool ?? "none"}>
        <rect x={BOX.x0} y={BOX.y0} width={BOX.x1 - BOX.x0} height={BOX.y1 - BOX.y0} fill="transparent" style={{ cursor: p.tool ? "crosshair" : undefined }} />
        {density && (
          <g data-density={`${density.nx}x${density.ny}`}>
            <RasterLayer box={BOX} label="density" version={rasterKey} draw={(buf, w, hh) => rasterRects(buf, w, hh, densityRects(density, axisMap(sx, BOX.x0), axisMap(sy, BOX.y0)))} />
          </g>
        )}
        {raster && (
          <g data-points={xs.length} data-raster="1" data-marked={markedIdx.length || undefined}>
            <RasterLayer
              box={BOX}
              version={rasterKey}
              draw={(buf, w, hh) => {
                const mx = axisMap(sx, BOX.x0);
                const my = axisMap(sy, BOX.y0);
                rasterPoints(buf, w, hh, xs, ys, mx, my, pixels ?? 0, 4);
                if (selected.length) rasterPoints(buf, w, hh, xs, ys, mx, my, SELECTED, 5, selected);
              }}
            />
          </g>
        )}
        {!density && !raster && (
          <g data-points={xs.length} data-marked={markedIdx.length || undefined}>
            {xs.map((x, i) => {
              const cx = sx.map(x);
              const cy = sy.map(ys[i]);
              if (cx === null || cy === null) return null;
              const sel = isSel.has(i);
              return <circle key={i} cx={cx} cy={cy} r={hover === i ? 5 : 3} fill={sel ? BRAND.copper : colors[i]} stroke={sel ? BRAND.anthracite : undefined} opacity={0.85} onPointerEnter={() => setHover(i)} data-selected={sel || undefined} />;
            })}
          </g>
        )}
      </g>
      {diag.length >= 2 && <polyline className={css.diagonal} data-line="1:1" points={poly(diag.map((q) => ({ x: px(q.x), y: py(q.y) })))} />}
      {fit && <line className={css.curve} data-line={p.regression} x1={px(fit[0].x)} y1={py(fit[0].y)} x2={px(fit[1].x)} y2={py(fit[1].y)} />}
      {p.selection && p.selection.x.length >= 3 && <polygon className={css.ring} data-selection={p.selection.x.length} points={poly(p.selection.x.map((x, i) => ({ x: px(x), y: py(p.selection!.y[i]) })))} />}
      {lasso.path && lasso.path.length > 1 && <polygon className={css.ring} strokeDasharray="4 3" points={poly(lasso.path)} />}
      {legend && (
        <g data-legend={legend.title} transform={`translate(${BOX.x1 - 150} ${BOX.y0 + 8})`}>
          <text className={css.tick} x={0} y={0}>
            {legend.title}
          </text>
          {legend.items.map((it, i) => (
            <g key={it.label} transform={`translate(0 ${12 + i * 13})`}>
              <rect width={9} height={9} fill={it.color} />
              <text className={css.tick} x={13} y={8}>
                {it.label}
              </text>
            </g>
          ))}
        </g>
      )}
      {h !== null && (
        <g className={css.tip} transform={`translate(${Math.min(px(xs[h]) + 10, BOX.x1 - 190)} ${Math.max(py(ys[h]) - 64, BOX.y0 + 4)})`}>
          <rect width={184} height={58} rx={4} />
          <text x={8} y={16}>
            {p.xLabel}: {formatNumber(xs[h], 5)}
          </text>
          <text x={8} y={32}>
            {p.yLabel}: {formatNumber(ys[h], 5)}
          </text>
          <text x={8} y={48}>Строка {r.row?.[h] ?? "—"}</text>
        </g>
      )}
    </PlotFrame>
  );
});
