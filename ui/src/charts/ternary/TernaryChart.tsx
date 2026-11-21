/**
 * The ternary diagram (ТЗ 8.3; ADR 0019 §11): the triangle A (top), B, C with the grid of 10 % shares, the points —
 * SVG circles up to 5000, more on the canvas layer; colours by a field (the ramp or the domains), the legend. SVG of
 * a fixed size: the PNG is twice it, the canvas layer goes in as an image × 2.
 */
import { forwardRef, useMemo, useState } from "react";
import type { TernaryResult } from "../../modules/charts/apiMore";
import { formatNumber, makeScale } from "../core/axisScale";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { axisMap, RASTER_FROM, rasterPoints, RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { colorLegend, pointColors, pointPixels } from "../scatter/model";
import { ternaryGrid, ternaryPoint, triangleIn } from "./model";

export const TERNARY_W = 640;
export const TERNARY_H = 560;
const BOX = plotBox(TERNARY_W, TERNARY_H, { left: 40, right: 40, top: 34, bottom: 40 });

export const TernaryChart = forwardRef<SVGSVGElement, { result: TernaryResult; color: string }>(function TernaryChart({ result: r, color }, ref) {
  const [hover, setHover] = useState<number | null>(null);
  const t = triangleIn(BOX.x0, BOX.y0, BOX.x1, BOX.y1);
  const count = r.a.length;
  const raster = count > RASTER_FROM;
  const cinfo = { c: r.color_values ?? null, color: r.color ?? null };
  // viewBox pixels of the points (NaN — not drawn)
  const pts = useMemo(() => {
    const xs = new Float64Array(count);
    const ys = new Float64Array(count);
    for (let i = 0; i < count; i++) {
      const p = ternaryPoint(t, r.a[i], r.b[i], r.c[i]);
      xs[i] = p ? p.x : Number.NaN;
      ys[i] = p ? p.y : Number.NaN;
    }
    return { xs, ys };
  }, [r]); // eslint-disable-line react-hooks/exhaustive-deps
  const colors = useMemo(() => (raster ? [] : pointColors(cinfo, count, color)), [r, color, raster]); // eslint-disable-line react-hooks/exhaustive-deps
  const pixels = useMemo(() => (raster ? pointPixels(cinfo, count, color) : null), [r, color, raster]); // eslint-disable-line react-hooks/exhaustive-deps
  const legend = colorLegend({ color: r.color ?? null, color_field: r.color_field });
  const [fa, fb, fc] = r.fields;
  const tri = `${t.A.x},${t.A.y} ${t.B.x},${t.B.y} ${t.C.x},${t.C.y}`;
  const h = hover !== null && !raster ? hover : null;
  return (
    <PlotFrame ref={ref} box={{ ...BOX, x0: 0, y0: 0, x1: 0, y1: 0 }} label={`Тернарная: ${fa}, ${fb}, ${fc}`} onPointerLeave={() => setHover(null)}>
      <polygon className={css.frame} points={tri} />
      <g data-grid="10%">
        {ternaryGrid(t).map((g) => (
          <line key={`${g.vertex}${g.share}`} className={css.grid} x1={g.from.x} y1={g.from.y} x2={g.to.x} y2={g.to.y} />
        ))}
      </g>
      {raster ? (
        <g data-points={count} data-raster="1">
          <RasterLayer
            box={BOX}
            version={pixels}
            draw={(buf, w, hh) => {
              const id = (lo: number, hi: number, from: number, to: number) => makeScale("linear", { min: lo, max: hi }, from, to);
              rasterPoints(buf, w, hh, pts.xs, pts.ys, axisMap(id(BOX.x0, BOX.x1, BOX.x0, BOX.x1), BOX.x0), axisMap(id(BOX.y0, BOX.y1, BOX.y0, BOX.y1), BOX.y0), pixels ?? 0, 4);
            }}
          />
        </g>
      ) : (
        <g data-points={count}>
          {Array.from(pts.xs, (x, i) => (Number.isFinite(x) ? <circle key={i} cx={x} cy={pts.ys[i]} r={hover === i ? 5 : 3} fill={colors[i]} opacity={0.85} onPointerEnter={() => setHover(i)} /> : null))}
        </g>
      )}
      <polygon className={css.frame} points={tri} />
      <text className={css.axisTitle} x={t.A.x} y={t.A.y - 10} textAnchor="middle">
        {fa}
      </text>
      <text className={css.axisTitle} x={t.B.x} y={t.B.y + 22} textAnchor="middle">
        {fb}
      </text>
      <text className={css.axisTitle} x={t.C.x} y={t.C.y + 22} textAnchor="middle">
        {fc}
      </text>
      {legend && (
        <g data-legend={legend.title} transform={`translate(${BOX.x1 - 110} ${BOX.y0})`}>
          <text className={css.tick}>{legend.title}</text>
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
        <g className={css.tip} transform={`translate(${Math.min(pts.xs[h] + 10, BOX.x1 - 170)} ${Math.max(pts.ys[h] - 64, 4)})`}>
          <rect width={164} height={58} rx={4} />
          <text x={8} y={16}>
            {fa}: {formatNumber(r.a[h] * 100, 3)} %
          </text>
          <text x={8} y={32}>
            {fb}: {formatNumber(r.b[h] * 100, 3)} %
          </text>
          <text x={8} y={48}>
            {fc}: {formatNumber(r.c[h] * 100, 3)} %
          </text>
        </g>
      )}
    </PlotFrame>
  );
});
