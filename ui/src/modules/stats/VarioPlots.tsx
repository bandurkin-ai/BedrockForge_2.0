/**
 * The charts of the Э16 analyses of the tab «Статистика» on the base of the chart windows (`charts/core`, ADR 0019
 * §11; ADR 0022 §11): the experimental variogram — the points of each direction sized by their pairs (hollow — fewer
 * than `min_pairs`; a lag without pairs is not drawn, never 0) with the curve of the model and the level σ²; a
 * grid in plan — the variogram map by (ΔX, ΔY) and the maps of `local` by (X, Y): north up, east to the right, one
 * scale on both axes; an empty cell (null) is hatched and «пусто», never painted as 0. The cells of a grid are one
 * raster layer (`charts/core/PointsCanvas`, as `charts/variogram/VarMapChart`: a map of n = 100 is 40 401 cells) under
 * the vector axes, the cell under the cursor told by its coordinates. SVG with the export panel.
 */
import { useId, useMemo, useRef, useState, type MouseEvent as RMouseEvent } from "react";
import { XAxis, YAxis } from "../../charts/core/Axes";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { makeScale, type Domain } from "../../charts/core/axisScale";
import { toViewBox } from "../../charts/core/lasso";
import { rampColor, rgba32, seriesColor } from "../../charts/core/palette";
import { PlotFrame, plotBox, type PlotBox } from "../../charts/core/PlotFrame";
import { RasterLayer } from "../../charts/core/PointsCanvas";
import css from "../../charts/core/Chart.module.css";
import type { VarioPoint } from "./formsVario";
import { fmt } from "./forms";
import own from "./Stats.module.css";

export const VPLOT_W = 560;
export const VPLOT_H = 320;

export interface VarioSeries {
  label: string;
  points: readonly VarioPoint[];
  /** the curve of the model along the direction */
  curve?: { h: readonly number[]; gamma: readonly number[] } | null;
}

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

/** Radius of a point by its pairs: 2,5 px … 6 px by √(N / Nmax). */
export const pairRadius = (n: number, max: number): number => (max > 0 ? 2.5 + 3.5 * Math.sqrt(Math.max(0, n) / max) : 2.5);

export interface VariogramPlotProps {
  label: string;
  series: readonly VarioSeries[];
  level: { value: number; label: string } | null;
  yTitle: string;
  fileName: string;
  csv: () => string | null;
}

export function VariogramPlot({ label, series, level, yTitle, fileName, csv }: VariogramPlotProps) {
  const ref = useRef<SVGSVGElement | null>(null);
  const box = plotBox(VPLOT_W, VPLOT_H, { left: 62, right: 16, top: 14, bottom: 46 });
  let hMax = 0;
  let gMax = level?.value ?? 0;
  let nMax = 0;
  for (const s of series) {
    for (const p of s.points) [hMax, gMax, nMax] = [Math.max(hMax, p.h), Math.max(gMax, p.gamma), Math.max(nMax, p.n)];
    s.curve?.h.forEach((h, i) => {
      hMax = Math.max(hMax, h);
      if (finite(s.curve!.gamma[i])) gMax = Math.max(gMax, s.curve!.gamma[i]);
    });
  }
  const any = series.some((s) => s.points.length || s.curve?.h.length);
  const sx = any && hMax > 0 ? makeScale("linear", { min: 0, max: hMax * 1.04 }, box.x0, box.x1) : null;
  const sy = any ? makeScale("linear", { min: 0, max: (gMax > 0 ? gMax : 1) * 1.08 }, box.y1, box.y0) : null;
  const ly = level && sy ? sy.map(level.value) : null;
  return (
    <figure className={own.figure}>
      <PlotFrame ref={ref} box={box} label={label} xTitle="h, м" yTitle={yTitle}>
        {sx && sy ? (
          <>
            <XAxis scale={sx} box={box} count={6} />
            <YAxis scale={sy} box={box} count={5} />
            {ly !== null && (
              <g data-testid="vario-level">
                <line className={css.mark} strokeDasharray="6 4" x1={box.x0} x2={box.x1} y1={ly} y2={ly} />
                <text className={css.markLabel} x={box.x1 - 4} y={ly - 4} textAnchor="end">
                  {level!.label}
                </text>
              </g>
            )}
            {series.map((s, k) => {
              const color = seriesColor(k);
              const d = s.curve ? s.curve.h.map((h, i) => [sx.map(h), sy.map(s.curve!.gamma[i])] as const).filter(([x, y]) => x !== null && y !== null).map(([x, y], i) => `${i ? "L" : "M"}${x!.toFixed(2)} ${y!.toFixed(2)}`).join("") : "";
              return (
                <g key={s.label} data-series={s.label}>
                  {d && <path data-testid="vario-model" d={d} fill="none" stroke={color} strokeWidth={1.6} />}
                  {s.points.map((p, i) => {
                    const x = sx.map(p.h);
                    const y = sy.map(p.gamma);
                    if (x === null || y === null) return null;
                    return (
                      <circle key={i} data-few={p.few || undefined} cx={x} cy={y} r={pairRadius(p.n, nMax)} fill={p.few ? "none" : color} stroke={color} strokeWidth={1.4}>
                        <title>{`${s.label}: h ${fmt(p.h, 4)} м, γ ${fmt(p.gamma, 4)}, пар ${p.n.toLocaleString("ru-RU")}${p.few ? " — мало пар" : ""}`}</title>
                      </circle>
                    );
                  })}
                </g>
              );
            })}
          </>
        ) : (
          <text className={css.tick} x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
            Нет лагов с парами
          </text>
        )}
      </PlotFrame>
      {series.length > 0 && (
        <div className={own.legend}>
          {series.map((s, k) => (
            <span key={s.label}>
              <i style={{ background: seriesColor(k) }} />
              {s.label}
            </span>
          ))}
          <span>○ — пар меньше порога</span>
        </div>
      )}
      <ChartToolbar svgRef={ref} fileName={fileName} csv={csv} disabled={!sx || !sy} />
    </figure>
  );
}

export const GRID_W = 520;
export const GRID_H = 440;
const BAR = 14;

/** The range of the finite values of a grid; none — null. */
export function gridDomain(values: readonly (readonly (number | null)[])[], extra: readonly (number | null | undefined)[] = []): Domain | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const row of values) for (const v of row) if (finite(v)) [lo, hi] = [Math.min(lo, v), Math.max(hi, v)];
  for (const v of extra) if (finite(v)) [lo, hi] = [Math.min(lo, v), Math.max(hi, v)];
  if (!(lo <= hi)) return null;
  return lo === hi ? { min: lo - 0.5, max: hi + 0.5 } : { min: lo, max: hi };
}

/** The colour of a value on the scale Cobalt → Copper Orange; null / not finite — none (an empty cell is hatched). */
export function cellColor(v: number | null | undefined, d: Domain | null): string | null {
  if (!finite(v) || !d) return null;
  return rampColor((v - d.min) / (d.max - d.min));
}

/**
 * The square plot area of a plan of `spanX × spanY` metres inside `box`: one scale on both axes (a metre east is as
 * long as a metre north), centred.
 */
export function planBox(box: PlotBox, spanX: number, spanY: number): PlotBox {
  const w = box.x1 - box.x0;
  const h = box.y1 - box.y0;
  const k = Math.min(w / spanX, h / spanY);
  const dx = (w - spanX * k) / 2;
  const dy = (h - spanY * k) / 2;
  return { ...box, x0: box.x0 + dx, x1: box.x1 - dx, y0: box.y0 + dy, y1: box.y1 - dy };
}

export interface GridPlotProps {
  label: string;
  /** the centres of the columns (east), ascending */
  xs: readonly number[];
  /** the centres of the rows (north, from the south), ascending */
  ys: readonly number[];
  /** values[j][i]: row j (y), column i (x); null — empty */
  values: readonly (readonly (number | null)[])[];
  xTitle: string;
  yTitle: string;
  valueTitle: string;
  /** the text of a cell under the pointer */
  tip: (i: number, j: number) => string;
  /** marks the centre (0; 0) of the variogram map */
  centre?: boolean;
  fileName: string;
  csv: () => string | null;
}

const step = (c: readonly number[]): number => (c.length > 1 ? (c[c.length - 1] - c[0]) / (c.length - 1) : 1);

/** The empty cells of a grid (null or not finite). */
export function gridEmpty(values: readonly (readonly (number | null)[])[], nx: number, ny: number): number {
  let empty = 0;
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) if (!finite(values[j]?.[i])) empty += 1;
  return empty;
}

/**
 * Paints the cells of a regular grid into a `w × h` buffer (ABGR): column i from the west, row j = 0 at the bottom
 * (south); an empty cell is not painted (the hatching under it shows). Returns the cells painted.
 */
export function rasterGrid(buf: Uint32Array, w: number, h: number, nx: number, ny: number, values: readonly (readonly (number | null)[])[], d: Domain | null): number {
  if (!d || nx < 1 || ny < 1) return 0;
  let painted = 0;
  for (let j = 0; j < ny; j++) {
    const ya = Math.round(((ny - 1 - j) * h) / ny);
    const yb = Math.round(((ny - j) * h) / ny);
    for (let i = 0; i < nx; i++) {
      const col = cellColor(values[j]?.[i], d);
      if (!col) continue;
      const xa = Math.round((i * w) / nx);
      const xb = Math.round(((i + 1) * w) / nx);
      const px = rgba32(col);
      for (let y = ya; y < yb; y++) buf.fill(px, y * w + xa, y * w + xb);
      painted += 1;
    }
  }
  return painted;
}

/** The cell (i, j) of a grid under the point (x, y) of the plan; outside — null. */
export function gridCellAt(xs: readonly number[], ys: readonly number[], x: number, y: number): { i: number; j: number } | null {
  const dx = step(xs);
  const dy = step(ys);
  if (!xs.length || !ys.length || !(dx > 0) || !(dy > 0)) return null;
  const i = Math.round((x - xs[0]) / dx);
  const j = Math.round((y - ys[0]) / dy);
  return i >= 0 && i < xs.length && j >= 0 && j < ys.length ? { i, j } : null;
}

/** A grid in plan: row j at the height of ys[j] (north up), column i at xs[i] (east right). */
export function GridPlot({ label, xs, ys, values, xTitle, yTitle, valueTitle, tip, centre = false, fileName, csv }: GridPlotProps) {
  const ref = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const hatch = `sthatch${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const outer = plotBox(GRID_W, GRID_H, { left: 70, right: 76, top: 14, bottom: 46 });
  const dx = step(xs);
  const dy = step(ys);
  const ok = xs.length > 0 && ys.length > 0 && dx > 0 && dy > 0;
  const ext = ok ? { x: { min: xs[0] - dx / 2, max: xs[xs.length - 1] + dx / 2 }, y: { min: ys[0] - dy / 2, max: ys[ys.length - 1] + dy / 2 } } : null;
  const box = ext ? planBox(outer, ext.x.max - ext.x.min, ext.y.max - ext.y.min) : outer;
  const sx = ext ? makeScale("linear", ext.x, box.x0, box.x1) : null;
  const sy = ext ? makeScale("linear", ext.y, box.y1, box.y0) : null;
  const dom = useMemo(() => gridDomain(values), [values]);
  const empty = ok ? gridEmpty(values, xs.length, ys.length) : 0;
  const at = (e: RMouseEvent<SVGElement>) => {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg || !sx || !sy) return null;
    const p = toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), GRID_W, GRID_H);
    return gridCellAt(xs, ys, sx.invert(p.x), sy.invert(p.y));
  };
  const tipText = hover ? tip(hover.i, hover.j) : null;
  const barX = outer.x1 + 18;
  const barSteps = 24;
  return (
    <figure className={own.figure} style={{ width: GRID_W }}>
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle}>
        <defs>
          <pattern id={hatch} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1={0} y1={0} x2={0} y2={6} className={css.grid} />
          </pattern>
        </defs>
        {sx && sy ? (
          <>
            <rect data-empty-fill x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} fill={`url(#${hatch})`} />
            <RasterLayer box={box} label="grid" version={values} draw={(buf, w, h) => void rasterGrid(buf, w, h, xs.length, ys.length, values, dom)} />
            <g data-testid="grid-cells" data-cells={xs.length * ys.length} data-empty={empty} />
            <XAxis scale={sx} box={box} grid={false} count={5} />
            <YAxis scale={sy} box={box} grid={false} count={5} />
            {centre && sx.map(0) !== null && sy.map(0) !== null && <circle cx={sx.map(0)!} cy={sy.map(0)!} r={2.5} className={css.ring} />}
            <text className={css.tick} x={box.x1 - 4} y={box.y0 + 12} textAnchor="end">
              С ↑
            </text>
            <rect data-testid="grid-hover" x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} fill="transparent" style={{ cursor: "crosshair" }} onMouseMove={(e) => setHover(at(e))} onMouseLeave={() => setHover(null)} />
            {tipText && (
              <g className={css.tip} pointerEvents="none" transform={`translate(${box.x0 + 6} ${box.y0 + 6})`}>
                <rect width={Math.min(box.x1 - box.x0 - 12, 16 + tipText.length * 6.2)} height={22} rx={4} />
                <text x={8} y={15}>
                  {tipText}
                </text>
              </g>
            )}
            {dom && (
              <g data-testid="grid-bar">
                {Array.from({ length: barSteps }, (_, k) => {
                  const h = (outer.y1 - outer.y0 - 30) / barSteps;
                  return <rect key={k} x={barX} y={outer.y0 + (barSteps - 1 - k) * h} width={BAR} height={h + 0.5} fill={rampColor((k + 0.5) / barSteps)} />;
                })}
                <text className={css.tick} x={barX + BAR + 3} y={outer.y0 + 8}>
                  {fmt(dom.max, 3)}
                </text>
                <text className={css.tick} x={barX + BAR + 3} y={outer.y1 - 30}>
                  {fmt(dom.min, 3)}
                </text>
                <text className={css.tick} x={barX} y={outer.y0 - 4}>
                  {valueTitle}
                </text>
              </g>
            )}
            <rect x={barX} y={outer.y1 - 18} width={BAR} height={BAR} fill={`url(#${hatch})`} className={css.frame} />
            <text className={css.tick} x={barX + BAR + 3} y={outer.y1 - 7}>
              пусто
            </text>
          </>
        ) : (
          <text className={css.tick} x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
            Нет данных
          </text>
        )}
      </PlotFrame>
      <ChartToolbar svgRef={ref} fileName={fileName} csv={csv} disabled={!sx || !dom} />
    </figure>
  );
}
