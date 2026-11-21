/**
 * The frame and the lasso of the charts (ТЗ 8.3, № 237; ADR 0019 §8): a path drawn by the pointer in pixels of the
 * viewBox becomes a polygon in the data units of the axes; a point is inside by the even-odd rule in the space of
 * the axes (log10 of a log axis — an edge straight on the screen is straight there). The core selects rows by the
 * same polygon in the density mode (13.33). `useLasso` — the pointer handlers of a plot box. Pure but the hook.
 */
import { useCallback, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { axisValue, type AxisKind, type Scale } from "./axisScale";
import type { PlotBox } from "./PlotFrame";

export interface Pt {
  x: number;
  y: number;
}

/** A polygon in data units (vertices; closed implicitly). */
export interface AxisPolygon {
  x: number[];
  y: number[];
}

export type SelectTool = "rect" | "lasso";

/** A pointer position in pixels of the viewBox of an SVG drawn at `rect` (the chart is scaled to its width). */
export function toViewBox(clientX: number, clientY: number, rect: { left: number; top: number; width: number; height: number }, width: number, height: number): Pt {
  return { x: ((clientX - rect.left) * width) / (rect.width || width), y: ((clientY - rect.top) * height) / (rect.height || height) };
}

/** The point kept inside the plot box. */
export const clampToBox = (p: Pt, box: PlotBox): Pt => ({ x: Math.min(box.x1, Math.max(box.x0, p.x)), y: Math.min(box.y1, Math.max(box.y0, p.y)) });

/** The four corners of the frame from two corners. */
export const rectPath = (a: Pt, b: Pt): Pt[] => [a, { x: b.x, y: a.y }, b, { x: a.x, y: b.y }];

/** A drawn path without points closer than `minDist` px to the previous one kept. */
export function simplifyPath(path: readonly Pt[], minDist = 3): Pt[] {
  const out: Pt[] = [];
  for (const p of path) {
    const q = out[out.length - 1];
    if (!q || Math.hypot(p.x - q.x, p.y - q.y) >= minDist) out.push(p);
  }
  return out;
}

/** The most vertices of a polygon the core takes (`POST /stats/selection`; 1001 — 422 `bad_param {param: polygon}`). */
export const MAX_POLYGON = 1000;

/** Douglas–Peucker: the vertices of `path` farther than `tol` px from the chord of what is dropped (the ends kept). */
function douglasPeucker(path: readonly Pt[], tol: number): Pt[] {
  const keep = new Uint8Array(path.length);
  keep[0] = keep[path.length - 1] = 1;
  const stack: [number, number][] = [[0, path.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    const { x: ax, y: ay } = path[a];
    const dx = path[b].x - ax;
    const dy = path[b].y - ay;
    const len = Math.hypot(dx, dy);
    let far = -1;
    let best = tol;
    for (let i = a + 1; i < b; i++) {
      const d = len > 0 ? Math.abs(dy * (path[i].x - ax) - dx * (path[i].y - ay)) / len : Math.hypot(path[i].x - ax, path[i].y - ay);
      if (d > best) {
        best = d;
        far = i;
      }
    }
    if (far < 0) continue;
    keep[far] = 1;
    stack.push([a, far], [far, b]);
  }
  return path.filter((_, i) => keep[i]);
}

/** A drawn path of at most `max` vertices keeping its shape (Douglas–Peucker, the tolerance doubled until it fits). */
export function limitPath(path: readonly Pt[], max = MAX_POLYGON): Pt[] {
  if (path.length <= max) return [...path];
  let out: Pt[] = [...path];
  for (let tol = 0.25; out.length > max; tol *= 2) out = douglasPeucker(path, tol);
  return out;
}

/** Twice the signed area (shoelace); 0 — a degenerate path that selects nothing. */
export function pathArea(path: readonly Pt[]): number {
  let s = 0;
  for (let i = 0, j = path.length - 1; i < path.length; j = i++) s += (path[j].x - path[i].x) * (path[j].y + path[i].y);
  return Math.abs(s) / 2;
}

/** The polygon of a pixel path in the data units of the axes; null — fewer than 3 vertices or no area. */
export function toAxisPolygon(path: readonly Pt[], sx: Scale, sy: Scale): AxisPolygon | null {
  if (path.length < 3 || pathArea(path) < 4) return null;
  return { x: path.map((p) => sx.invert(p.x)), y: path.map((p) => sy.invert(p.y)) };
}

/** Even-odd: a horizontal ray to the right crosses the edges an odd number of times. Polygon in the same space as (x, y). */
export function insidePolygon(x: number, y: number, px: ArrayLike<number>, py: ArrayLike<number>): boolean {
  let inside = false;
  for (let i = 0, j = px.length - 1; i < px.length; j = i++) {
    const yi = py[i];
    const yj = py[j];
    if (yi > y !== yj > y && x < ((px[j] - px[i]) * (y - yi)) / (yj - yi) + px[i]) inside = !inside;
  }
  return inside;
}

/** The polygon in the space of the axes; null when a vertex cannot be shown there. */
export function polygonInAxes(poly: AxisPolygon, kx: AxisKind, ky: AxisKind): { x: Float64Array; y: Float64Array } | null {
  const n = poly.x.length;
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const a = axisValue(kx, poly.x[i]);
    const b = axisValue(ky, poly.y[i]);
    if (a === null || b === null) return null;
    x[i] = a;
    y[i] = b;
  }
  return { x, y };
}

/**
 * Indices of the points inside the polygon (data units) by the even-odd rule in the space of the axes. A point the
 * axes cannot show (missing, ≤ 0 on a log axis) is never inside.
 */
export function selectInside(xs: ArrayLike<number | null>, ys: ArrayLike<number | null>, poly: AxisPolygon, kx: AxisKind = "linear", ky: AxisKind = "linear"): number[] {
  const p = polygonInAxes(poly, kx, ky);
  if (!p || p.x.length < 3) return [];
  let x0 = Infinity;
  let x1 = -Infinity;
  let y0 = Infinity;
  let y1 = -Infinity;
  for (let i = 0; i < p.x.length; i++) {
    x0 = Math.min(x0, p.x[i]);
    x1 = Math.max(x1, p.x[i]);
    y0 = Math.min(y0, p.y[i]);
    y1 = Math.max(y1, p.y[i]);
  }
  const out: number[] = [];
  for (let i = 0; i < xs.length; i++) {
    const a = axisValue(kx, xs[i]);
    const b = axisValue(ky, ys[i]);
    if (a === null || b === null || a < x0 || a > x1 || b < y0 || b > y1) continue;
    if (insidePolygon(a, b, p.x, p.y)) out.push(i);
  }
  return out;
}

/**
 * The pointer handlers of a frame or a lasso over the plot box: `path` — what is drawn now (pixels of the
 * viewBox); on release `onDone` gets the polygon in data units (null — a click or a degenerate path).
 */
export function useLasso(tool: SelectTool | null, box: PlotBox, scales: { sx: Scale; sy: Scale } | null, onDone: (poly: AxisPolygon | null) => void) {
  const [path, setPathState] = useState<Pt[] | null>(null);
  const start = useRef<Pt | null>(null);
  const current = useRef<Pt[] | null>(null);
  const setPath = (p: Pt[] | null) => {
    current.current = p;
    setPathState(p);
  };
  const at = (e: RPointerEvent<SVGElement>): Pt | null => {
    const svg = (e.currentTarget as SVGElement).ownerSVGElement ?? (e.currentTarget as SVGSVGElement);
    const r = svg.getBoundingClientRect();
    const vb = (svg as SVGSVGElement).viewBox?.baseVal;
    return clampToBox(toViewBox(e.clientX, e.clientY, r, vb?.width || box.width, vb?.height || box.height), box);
  };
  const onPointerDown = useCallback(
    (e: RPointerEvent<SVGElement>) => {
      if (!tool || e.button !== 0) return;
      const p = at(e);
      if (!p) return;
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
      start.current = p;
      setPath([p]);
    },
    [tool, box], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const onPointerMove = useCallback(
    (e: RPointerEvent<SVGElement>) => {
      const s = start.current;
      if (!s) return;
      const p = at(e);
      if (!p) return;
      setPath(tool === "rect" ? rectPath(s, p) : simplifyPath([...(current.current ?? []), p], 2));
    },
    [tool, box], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const onPointerUp = useCallback(() => {
    if (!start.current) return;
    start.current = null;
    const drawn = current.current;
    setPath(null);
    // the core takes ≤ 1000 vertices: a long lasso is simplified in px, its shape kept
    onDone(drawn && scales ? toAxisPolygon(limitPath(drawn), scales.sx, scales.sy) : null);
  }, [scales, onDone]);
  return { path, handlers: tool ? { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp } : {} };
}
