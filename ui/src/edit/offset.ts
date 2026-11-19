/**
 * Copy with an offset (ADR 0015 §3, §7; BF `edit/ops.ts` offsetPoints / offsetLine): a mitred parallel line in
 * the line's own plane or in a given one. A line with a vertex without a height is offset in plan (or in the
 * given horizontal plane) and its missing heights stay missing; in a tilted or vertical plane it cannot be.
 */
import type { Plane } from "./coords";
import { bestFitPlane, leastSquaresPlane } from "./measure";
import { lineById } from "./model";
import type { EditLayer, Vec3 } from "./model";

export const DEFAULT_MITRE_LIMIT = 4;

/**
 * The geometry of `offsetLine`. The line is projected to the plane, moved there and lifted back, every
 * vertex keeping its own distance from the plane.
 *  - closed line: distance > 0 is outward, < 0 inward, whatever the direction of the line;
 *  - open line: distance > 0 is to the right of the direction of travel as seen from the side the
 *    plane normal points to (in plan, seen from above: to the right).
 * Corners are mitred; a corner sharper than the mitre limit (mitre length / distance) is bevelled.
 * Null when the line degenerates (fewer than 2 distinct points, a polygon without area, an inward
 * offset that collapses the polygon). Self-intersections of the result are not removed.
 */
export function offsetPoints(points: Vec3[], closed: boolean, distance: number, plane?: Plane, mitreLimit = DEFAULT_MITRE_LIMIT): Vec3[] | null {
  if (!Number.isFinite(distance) || points.length === 0) return null;
  const flat = points.some((p) => !Number.isFinite(p[2]));
  let pl: Plane | null;
  if (flat) {
    // Without every height only the plan is known: offset there and give the heights back as they were.
    if (plane && !(plane.normal[0] === 0 && plane.normal[1] === 0)) return null;
    pl = { point: [points[0][0], points[0][1], 0], normal: [0, 0, plane && plane.normal[2] < 0 ? -1 : 1] };
  } else pl = plane ?? (closed ? bestFitPlane(points) : leastSquaresPlane(points));
  if (!pl) return null;
  const n = pl.normal;
  const o = pl.point;
  // Basis (u, v) of the plane with u × v = n: seen from the side of the normal, u is right and v is up.
  const ax: Vec3 = Math.abs(n[2]) > 0.9 ? [0, 1, 0] : [0, 0, 1];
  let u: Vec3 = [ax[1] * n[2] - ax[2] * n[1], ax[2] * n[0] - ax[0] * n[2], ax[0] * n[1] - ax[1] * n[0]];
  const ul = Math.hypot(u[0], u[1], u[2]);
  u = [u[0] / ul, u[1] / ul, u[2] / ul];
  const v: Vec3 = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]];

  const xs: number[] = [];
  const ys: number[] = [];
  const hs: number[] = [];
  let extent = 0;
  for (const p of points) {
    const dx = p[0] - o[0];
    const dy = p[1] - o[1];
    const dz = flat ? 0 : p[2] - o[2];
    const x = dx * u[0] + dy * u[1] + dz * u[2];
    const y = dx * v[0] + dy * v[1] + dz * v[2];
    extent = Math.max(extent, Math.abs(x), Math.abs(y));
    xs.push(x);
    ys.push(y);
    hs.push(flat ? p[2] : dx * n[0] + dy * n[1] + dz * n[2]);
  }
  // Points that coincide in the plane give no direction: drop the repeats.
  const eps = extent * 1e-12;
  const keep: number[] = [];
  for (let i = 0; i < xs.length; i++) {
    const last = keep[keep.length - 1];
    if (last === undefined || Math.hypot(xs[i] - xs[last], ys[i] - ys[last]) > eps) keep.push(i);
  }
  if (closed && keep.length > 1) {
    const first = keep[0];
    const last = keep[keep.length - 1];
    if (Math.hypot(xs[first] - xs[last], ys[first] - ys[last]) <= eps) keep.pop();
  }
  const m = keep.length;
  if (m < 2 || (closed && m < 3)) return null;
  const X = keep.map((i) => xs[i]);
  const Y = keep.map((i) => ys[i]);
  const Hh = keep.map((i) => hs[i]);

  const area2 = (px: number[], py: number[]): number => {
    let s = 0;
    for (let i = 0; i < px.length; i++) {
      const j = (i + 1) % px.length;
      s += (px[i] - px[0]) * (py[j] - py[0]) - (px[j] - px[0]) * (py[i] - py[0]);
    }
    return s;
  };

  let d = distance;
  let turn = 0;
  if (closed) {
    turn = area2(X, Y);
    if (!(Math.abs(turn) > extent * extent * 1e-12)) return null;
    // The right-hand side is outside for a counter-clockwise polygon.
    if (turn < 0) d = -d;
  }

  const segs = closed ? m : m - 1;
  const dirX: number[] = new Array(segs);
  const dirY: number[] = new Array(segs);
  for (let k = 0; k < segs; k++) {
    const j = (k + 1) % m;
    const len = Math.hypot(X[j] - X[k], Y[j] - Y[k]);
    dirX[k] = (X[j] - X[k]) / len;
    dirY[k] = (Y[j] - Y[k]) / len;
  }

  const outX: number[] = [];
  const outY: number[] = [];
  const outH: number[] = [];
  // For every vertex: the index of its first and last output point.
  const firstOf: number[] = new Array(m);
  const lastOf: number[] = new Array(m);
  const emit = (k: number, x: number, y: number) => {
    outX.push(x);
    outY.push(y);
    outH.push(Hh[k]);
  };
  for (let k = 0; k < m; k++) {
    firstOf[k] = outX.length;
    const prev = closed ? (k + m - 1) % m : k - 1;
    const next = closed ? k : k < segs ? k : -1;
    if (prev < 0 || next < 0) {
      const s = prev < 0 ? next : prev;
      emit(k, X[k] + d * dirY[s], Y[k] - d * dirX[s]);
    } else {
      // Right-hand normals of the two segments: r = (dy, -dx).
      const r1x = dirY[prev];
      const r1y = -dirX[prev];
      const r2x = dirY[next];
      const r2y = -dirX[next];
      const c = 1 + r1x * r2x + r1y * r2y;
      const cross = dirX[prev] * dirY[next] - dirY[prev] * dirX[next];
      const outer = cross * d > 0;
      const ratio = c > 1e-12 ? Math.sqrt(2 / c) : Infinity;
      if (ratio <= mitreLimit || (!outer && Number.isFinite(ratio) && ratio < 1e6)) {
        emit(k, X[k] + (d * (r1x + r2x)) / c, Y[k] + (d * (r1y + r2y)) / c);
      } else {
        emit(k, X[k] + d * r1x, Y[k] + d * r1y);
        emit(k, X[k] + d * r2x, Y[k] + d * r2y);
      }
    }
    lastOf[k] = outX.length - 1;
  }

  // An offset segment that runs backwards means the neighbours have passed through each other.
  let reversed = 0;
  for (let k = 0; k < segs; k++) {
    const from = lastOf[k];
    const to = firstOf[(k + 1) % m];
    if ((outX[to] - outX[from]) * dirX[k] + (outY[to] - outY[from]) * dirY[k] <= 0) reversed++;
  }
  if (reversed === segs) return null;
  if (closed) {
    const after = area2(outX, outY);
    if (!(after * turn > 0)) return null;
  }

  const out: Vec3[] = new Array(outX.length);
  for (let i = 0; i < outX.length; i++) {
    if (flat) {
      // the plane is horizontal: u, v span X and Y, the height is the vertex's own (NaN stays NaN)
      out[i] = [o[0] + outX[i] * u[0] + outY[i] * v[0], o[1] + outX[i] * u[1] + outY[i] * v[1], outH[i]];
      continue;
    }
    out[i] = [
      o[0] + (outX[i] * u[0] + outY[i] * v[0] + outH[i] * n[0]),
      o[1] + (outX[i] * u[1] + outY[i] * v[1] + outH[i] * n[1]),
      o[2] + (outX[i] * u[2] + outY[i] * v[2] + outH[i] * n[2]),
    ];
  }
  return out;
}

/**
 * A parallel copy of a line in its own best-fit plane (or in the given plane); see `offsetPoints`.
 * The copy is added to the end of the layer with the attributes of the original.
 */
export function offsetLine(
  layer: EditLayer,
  lineId: number,
  distance: number,
  plane?: Plane,
  mitreLimit = DEFAULT_MITRE_LIMIT,
): { layer: EditLayer; id: number } | null {
  const line = lineById(layer, lineId);
  if (!line) return null;
  const closed = line.closed && line.points.length >= 3;
  const points = offsetPoints(line.points, closed, distance, plane, mitreLimit);
  if (!points) return null;
  const id = layer.nextId;
  // the copy keeps `src`: the core carries the attributes of the original to it under a new `lid`
  return { layer: { lines: [...layer.lines, { id, points, closed, attrs: { ...line.attrs }, src: line.src }], nextId: id + 1 }, id };
}
