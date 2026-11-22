/**
 * The variogram map (ТЗ 8.6.4 «Карта вариограмм»; ADR 0022 §5, §15, decision 8): (2nu + 1) × (2nv + 1) cells of γ
 * in the plane seen from above, u (along a1) up, v (along a2) to the LEFT (at zero angles a2 is west); the cells are
 * a raster layer (`charts/core/
 * PointsCanvas`) under the vector axes. A cell with N < min_pairs is empty: it is not painted (the hatching shows
 * through, «пусто» in the legend) — never the colour of zero. The colour bar 0 … max(γ, σ²) Cobalt → Copper Orange
 * with σ² marked; a click picks the direction of the major axis through the centre (theta from a1 to a2).
 */
import { forwardRef, useId, useMemo, useState, type MouseEvent as RMouseEvent } from "react";
import { formatNumber, makeScale } from "../core/axisScale";
import { XAxis, YAxis } from "../core/Axes";
import { toViewBox } from "../core/lasso";
import { plotBox, PlotFrame } from "../core/PlotFrame";
import { RasterLayer } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { pairsText, cellAt, mapColor, mapDomain, mapExtent, mapTicks, mod180, polar, rasterMap, thetaOf, type VarMapData } from "./scale";

export const MAP_W = 470;
export const MAP_H = 400;
const BAR_W = 12;

export interface VarMapChartProps {
  map: VarMapData;
  sigma2?: number | null;
  unit?: string;
  title?: string;
  /** the direction picked (theta, °): a line through the centre */
  selected?: number | null;
  onPick?: (theta: number) => void;
  /** the world directions of a1 and a2 for the titles of the axes («Аз 30° · Пог 0°») */
  axes?: { a1: string; a2: string };
}

export const VarMapChart = forwardRef<SVGSVGElement, VarMapChartProps>(function VarMapChart({ map, sigma2, unit = "м", title, selected, onPick, axes }, ref) {
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const hatch = `vmhatch${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const ext = mapExtent(map);
  const dom = useMemo(() => mapDomain(map, sigma2), [map, sigma2]);
  // a square plot box: the cells keep their shape
  const side = Math.min(MAP_W - 62 - 86, MAP_H - 18 - 46);
  const box = plotBox(MAP_W, MAP_H, { left: 62, right: MAP_W - 62 - side, top: 18, bottom: MAP_H - 18 - side });
  const label = title ?? "Карта вариограммы";
  if (!ext) {
    return (
      <PlotFrame ref={ref} box={box} label={label}>
        <text className={css.tick} data-empty="map" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          Карта не рассчитана
        </text>
      </PlotFrame>
    );
  }
  // equal metres on both axes: the longer extent fills the box
  const span = Math.max(ext.u.max - ext.u.min, ext.v.max - ext.v.min);
  const vc = (ext.v.min + ext.v.max) / 2;
  const uc = (ext.u.min + ext.u.max) / 2;
  // v grows to the left: the plan seen from above (a2 = west at zero angles)
  const sx = makeScale("linear", { min: vc - span / 2, max: vc + span / 2 }, box.x1, box.x0);
  const sy = makeScale("linear", { min: uc - span / 2, max: uc + span / 2 }, box.y1, box.y0);
  const rasterBox = { ...box, x0: sx.map(ext.v.max) as number, x1: sx.map(ext.v.min) as number, y0: sy.map(ext.u.max) as number, y1: sy.map(ext.u.min) as number };
  const cells = map.gamma.reduce((a, r) => a + r.length, 0);
  const empty = map.gamma.reduce((a, r) => a + r.filter((g) => g === null).length, 0);
  const at = (e: RMouseEvent<SVGElement>) => {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return null;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), MAP_W, MAP_H);
    return { v: sx.invert(p.x), u: sy.invert(p.y), dx: p.x - (sx.map(0) ?? 0), dy: p.y - (sy.map(0) ?? 0) };
  };
  const barX = box.x1 + 16;
  const barScale = dom ? makeScale("linear", dom, box.y1, box.y0) : null;
  const stops = 24;
  const h = hover ? { g: map.gamma[hover.j]?.[hover.i] ?? null, n: map.n[hover.j]?.[hover.i] ?? 0, u: map.u[hover.i], v: map.v[hover.j] } : null;
  const c0 = [sx.map(0), sy.map(0)] as const;
  return (
    <PlotFrame ref={ref} box={box} label={label} xTitle={`← v (a2${axes ? ` · ${axes.a2}` : ""}), ${unit}`} yTitle={`u (a1${axes ? ` · ${axes.a1}` : ""}), ${unit}`} onPointerLeave={() => setHover(null)}>
      <defs>
        <pattern id={hatch} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1={0} y1={0} x2={0} y2={6} className={css.grid} />
        </pattern>
      </defs>
      <rect data-empty-fill x={rasterBox.x0} y={rasterBox.y0} width={rasterBox.x1 - rasterBox.x0} height={rasterBox.y1 - rasterBox.y0} fill={`url(#${hatch})`} />
      <RasterLayer box={rasterBox} label="varmap" version={map} draw={(buf, w, hh) => void rasterMap(buf, w, hh, map, dom)} />
      <YAxis scale={sy} box={box} grid={false} />
      <XAxis scale={sx} box={box} grid={false} count={6} />
      <g data-cells={cells} data-empty={empty} />
      {selected !== null && selected !== undefined && c0[0] !== null && c0[1] !== null && (
        <g data-selected={mod180(selected)}>
          {(() => {
            const [dx, dy] = polar(selected, (box.x1 - box.x0) / 2);
            return <line className={css.mark} x1={c0[0] - dx} y1={c0[1] - dy} x2={c0[0] + dx} y2={c0[1] + dy} />;
          })()}
        </g>
      )}
      <rect
        x={box.x0}
        y={box.y0}
        width={box.x1 - box.x0}
        height={box.y1 - box.y0}
        fill="transparent"
        style={{ cursor: onPick ? "pointer" : "crosshair" }}
        onMouseMove={(e) => {
          const p = at(e);
          setHover(p ? cellAt(map, p.u, p.v) : null);
        }}
        onClick={(e) => {
          const p = at(e);
          if (p && onPick && (p.dx || p.dy)) onPick(thetaOf(p.dx, p.dy));
        }}
      />
      {dom && barScale && (
        <g data-colorbar transform={`translate(${barX} 0)`} pointerEvents="none">
          {Array.from({ length: stops }, (_, k) => {
            const v0 = dom.min + ((dom.max - dom.min) * k) / stops;
            const v1 = dom.min + ((dom.max - dom.min) * (k + 1)) / stops;
            const y0 = barScale.map(v1) as number;
            const y1 = barScale.map(v0) as number;
            return <rect key={k} x={0} y={y0} width={BAR_W} height={Math.max(0.5, y1 - y0) + 0.3} fill={mapColor((v0 + v1) / 2, dom) ?? "none"} />;
          })}
          <rect className={css.frame} x={0} y={box.y0} width={BAR_W} height={box.y1 - box.y0} />
          {mapTicks(dom).map((t) => (
            <text key={t} className={css.tick} x={BAR_W + 4} y={barScale.map(t) ?? 0} dominantBaseline="middle">
              {formatNumber(t, 3)}
            </text>
          ))}
          {typeof sigma2 === "number" && Number.isFinite(sigma2) && barScale.map(sigma2) !== null && (
            <g data-sigma2={sigma2}>
              <line className={css.mark} x1={-3} x2={BAR_W + 3} y1={barScale.map(sigma2) as number} y2={barScale.map(sigma2) as number} />
              <text className={css.markLabel} x={-5} y={(barScale.map(sigma2) as number) - 3} textAnchor="end">
                σ²
              </text>
            </g>
          )}
          <rect x={0} y={box.y1 + 10} width={BAR_W} height={BAR_W} fill={`url(#${hatch})`} className={css.frame} />
          <text className={css.tick} x={BAR_W + 4} y={box.y1 + 20}>
            пусто
          </text>
        </g>
      )}
      {!dom && (
        <text className={css.tick} data-empty="map" x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
          Во всех ячейках меньше {map.min_pairs} пар
        </text>
      )}
      {h && hover && (
        <g className={css.tip} pointerEvents="none" transform={`translate(${box.x0 + 6} ${box.y0 + 6})`}>
          <rect width={206} height={40} rx={4} />
          <text x={8} y={15}>u {formatNumber(h.u, 4)} · v {formatNumber(h.v, 4)} {unit}</text>
          <text x={8} y={31}>{h.g === null ? `пусто: пар ${pairsText(h.n)} < ${map.min_pairs}` : `γ ${formatNumber(h.g, 4)} · пар ${pairsText(h.n)}`}</text>
        </g>
      )}
    </PlotFrame>
  );
});
