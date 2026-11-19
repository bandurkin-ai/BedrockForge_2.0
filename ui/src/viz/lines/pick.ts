/**
 * Picking of lines and points on the screen (ADR 0011 §4): the nearest segment within 6 px or the nearest
 * point within 8 px over the shown parts, on the CPU without an index (≤ 50 ms: a million segments take a few
 * milliseconds — a segment far from the cursor is dropped by its screen box). A hit carries its share of the
 * tolerance (`score`, 0…1), so a point and a line — also of different layers — compare fairly. What the
 * section window cuts off is not picked, nor a point of a cloud whose class is hidden (ADR 0014 §2). Pure: float32 points of a part + its float64 offset.
 */
import type { Box3 } from "../core/bounds";
import type { Vec3 } from "../core/vec3";
import { boxNear, nearestPoint, nearestSegment } from "../pick/holes";
import { multiply, partModel } from "../pick/ray";
import type { BuiltLines } from "./build";

export const LINE_PICK_PX = 6;
export const POINT_PICK_PX = 8;

export interface PickPart {
  k: number;
  /** float64: part origin − scene origin (scene units, Z not stretched) */
  offset: Vec3;
  /** the extent of the part in its own coordinates; null — always searched */
  box: Box3 | null;
  built: Pick<BuiltLines, "positions" | "segments" | "segStart" | "dots" | "dotPiece" | "line"> & { value?: Uint8Array | null };
  /** a cloud: 1 for every shown `value` code (`cloud.shownCodes`); null — every point */
  shown?: Uint8Array | null;
}

export interface PickView {
  /** clip ← rendered space (projection · view), float64 */
  vp: ArrayLike<number>;
  vexag: number;
  /** the cursor, CSS px from the top left of a width × height window */
  px: number;
  py: number;
  width: number;
  height: number;
  /** is a plan point (rendered space) inside the section window */
  inWindow(x: number, y: number): boolean;
}

export interface LineHit {
  k: number;
  /** the polyline (point) of the part's answer */
  piece: number;
  /** the line (point) of the layer: the row of its attributes */
  line: number;
  on: "line" | "point";
  /** px from the cursor */
  distance: number;
  /** distance / tolerance: the smallest wins */
  score: number;
  /** the picked place in the coordinates of the part (float32 from its origin) */
  at: [number, number, number];
  /** a cloud: the `value` of the picked point (the class code or the code of the mode) */
  value?: number;
}

/** The polyline that owns segment `s` (`segStart` is n + 1 long and not decreasing; empty polylines are skipped). */
export function pieceOfSegment(segStart: ArrayLike<number>, s: number): number {
  let lo = 0;
  let hi = segStart.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (segStart[mid] <= s) lo = mid;
    else hi = mid;
  }
  return lo;
}

export function pickLines(parts: Iterable<PickPart>, v: PickView): LineHit | null {
  let best: LineHit | null = null;
  let score = 1;
  const { px, py, width: w, height: h } = v;
  for (const p of parts) {
    const m = multiply(v.vp, partModel(p.offset, v.vexag));
    if (p.box && !boxNear(m, p.box, px, py, w, h, POINT_PICK_PX)) continue;
    const b = p.built;
    const [ox, oy] = p.offset;
    const dots = b.dots;
    const value = b.value ?? null;
    const shown = value ? (p.shown ?? null) : null;
    if (dots.length) {
      const accept = shown ? (i: number) => shown[value![i]] === 1 && v.inWindow(dots[i * 3] + ox, dots[i * 3 + 1] + oy) : (i: number) => v.inWindow(dots[i * 3] + ox, dots[i * 3 + 1] + oy);
      const hit = nearestPoint(m, dots, dots.length / 3, px, py, w, h, score * POINT_PICK_PX, accept);
      if (hit) {
        const piece = b.dotPiece ? b.dotPiece[hit.index] : hit.index;
        score = hit.distance / POINT_PICK_PX;
        best = { k: p.k, piece, line: b.line[piece], on: "point", distance: hit.distance, score, at: [dots[hit.index * 3], dots[hit.index * 3 + 1], dots[hit.index * 3 + 2]] };
        if (value) best.value = value[hit.index];
      }
    }
    const pos = b.positions;
    if (!b.segments) continue;
    const hit = nearestSegment(m, pos, b.segments, px, py, w, h, score * LINE_PICK_PX, (s, t) => v.inWindow(pos[s * 6] + (pos[s * 6 + 3] - pos[s * 6]) * t + ox, pos[s * 6 + 1] + (pos[s * 6 + 4] - pos[s * 6 + 1]) * t + oy));
    if (hit) {
      const piece = pieceOfSegment(b.segStart, hit.segment);
      const o = hit.segment * 6;
      const t = hit.t;
      score = hit.distance / LINE_PICK_PX;
      best = { k: p.k, piece, line: b.line[piece], on: "line", distance: hit.distance, score, at: [pos[o] + (pos[o + 3] - pos[o]) * t, pos[o + 1] + (pos[o + 4] - pos[o + 1]) * t, pos[o + 2] + (pos[o + 5] - pos[o + 2]) * t] };
    }
  }
  return best;
}
