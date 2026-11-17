/**
 * Picking of holes on the screen (ADR 0007 §7, BF `nearestCollar` 2512): first the nearest collar within
 * 8 px, then the nearest segment of a trace within 6 px, over the parts in view, on the CPU without a BVH.
 * Points come in the local coordinates of a part (float32 from its origin) with the matrix clip ← local in
 * float64. Pure; the loops allocate nothing per point.
 */

export const COLLAR_PICK_PX = 8;
export const TRACE_PICK_PX = 6;

export interface PointHit {
  index: number;
  distance: number;
  x: number;
  y: number;
}

/**
 * The nearest of `count` points (xyz interleaved) to (px, py) within `maxPx`, or null. `accept(i)` drops
 * points (hidden, outside the section window).
 */
export function nearestPoint(
  m: ArrayLike<number>,
  xyz: ArrayLike<number>,
  count: number,
  px: number,
  py: number,
  w: number,
  h: number,
  maxPx: number,
  accept?: (i: number) => boolean,
): PointHit | null {
  let best: PointHit | null = null;
  let bestD = maxPx;
  for (let i = 0; i < count; i++) {
    const x = xyz[i * 3];
    const y = xyz[i * 3 + 1];
    const z = xyz[i * 3 + 2];
    const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (!(cw > 0)) continue;
    const nz = (m[2] * x + m[6] * y + m[10] * z + m[14]) / cw;
    if (nz < -1 || nz > 1) continue;
    const sx = (((m[0] * x + m[4] * y + m[8] * z + m[12]) / cw) * 0.5 + 0.5) * w;
    const sy = ((-(m[1] * x + m[5] * y + m[9] * z + m[13]) / cw) * 0.5 + 0.5) * h;
    const d = Math.hypot(sx - px, sy - py);
    if (d <= bestD && (!accept || accept(i))) {
      bestD = d;
      best = { index: i, distance: d, x: sx, y: sy };
    }
  }
  return best;
}

/** Distance from (px, py) to the segment a–b on the screen and the parameter of the nearest point (0…1). */
export function segmentDistance(px: number, py: number, ax: number, ay: number, bx: number, by: number): { d: number; t: number } {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
  return { d: Math.hypot(ax + dx * t - px, ay + dy * t - py), t };
}

export interface SegmentHit {
  segment: number;
  /** along the segment, 0 at its start */
  t: number;
  distance: number;
}

/**
 * The nearest of `count` segments (6 floats each: start xyz, end xyz) within `maxPx`. A segment with an end
 * behind the camera or outside near / far is skipped. `accept(segment, t)` drops hidden ones and points
 * outside the section window.
 */
export function nearestSegment(
  m: ArrayLike<number>,
  positions: ArrayLike<number>,
  count: number,
  px: number,
  py: number,
  w: number,
  h: number,
  maxPx: number,
  accept?: (segment: number, t: number) => boolean,
): SegmentHit | null {
  let best: SegmentHit | null = null;
  let bestD = maxPx;
  for (let s = 0; s < count; s++) {
    const o = s * 6;
    const ax = positions[o];
    const ay = positions[o + 1];
    const az = positions[o + 2];
    const bx = positions[o + 3];
    const by = positions[o + 4];
    const bz = positions[o + 5];
    const aw = m[3] * ax + m[7] * ay + m[11] * az + m[15];
    const bw = m[3] * bx + m[7] * by + m[11] * bz + m[15];
    if (!(aw > 0) || !(bw > 0)) continue;
    const anz = (m[2] * ax + m[6] * ay + m[10] * az + m[14]) / aw;
    const bnz = (m[2] * bx + m[6] * by + m[10] * bz + m[14]) / bw;
    if ((anz < -1 && bnz < -1) || (anz > 1 && bnz > 1)) continue;
    const sax = (((m[0] * ax + m[4] * ay + m[8] * az + m[12]) / aw) * 0.5 + 0.5) * w;
    const say = ((-(m[1] * ax + m[5] * ay + m[9] * az + m[13]) / aw) * 0.5 + 0.5) * h;
    const sbx = (((m[0] * bx + m[4] * by + m[8] * bz + m[12]) / bw) * 0.5 + 0.5) * w;
    const sby = ((-(m[1] * bx + m[5] * by + m[9] * bz + m[13]) / bw) * 0.5 + 0.5) * h;
    // quick reject by the box of the segment on the screen
    if (px < Math.min(sax, sbx) - bestD || px > Math.max(sax, sbx) + bestD || py < Math.min(say, sby) - bestD || py > Math.max(say, sby) + bestD) continue;
    const { d, t } = segmentDistance(px, py, sax, say, sbx, sby);
    if (d <= bestD && (!accept || accept(s, t))) {
      bestD = d;
      best = { segment: s, t, distance: d };
    }
  }
  return best;
}

/**
 * Does the screen rectangle of a box (8 corners under `m`) come within `maxPx` of (px, py)? Corners behind
 * the camera make it count (the box is around the viewer).
 */
export function boxNear(m: ArrayLike<number>, box: { min: ArrayLike<number>; max: ArrayLike<number> }, px: number, py: number, w: number, h: number, maxPx: number): boolean {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    const x = i & 1 ? box.max[0] : box.min[0];
    const y = i & 2 ? box.max[1] : box.min[1];
    const z = i & 4 ? box.max[2] : box.min[2];
    const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (!(cw > 0)) return true;
    const sx = (((m[0] * x + m[4] * y + m[8] * z + m[12]) / cw) * 0.5 + 0.5) * w;
    const sy = ((-(m[1] * x + m[5] * y + m[9] * z + m[13]) / cw) * 0.5 + 0.5) * h;
    x0 = Math.min(x0, sx);
    y0 = Math.min(y0, sy);
    x1 = Math.max(x1, sx);
    y1 = Math.max(y1, sy);
  }
  return px >= x0 - maxPx && px <= x1 + maxPx && py >= y0 - maxPx && py <= y1 + maxPx;
}
