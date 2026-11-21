/**
 * The handles of a swath box (ТЗ 7.35 Д-01; ADR 0019 §6; лучи и плоскости — перенос `stats/swathBox.ts` BF):
 * where the three handles are, which one is under the cursor and what a drag does to the angles. Pure, float64,
 * project coordinates (metres, Z up, no exaggeration) — vitest without three.js (ТД01-1).
 *
 * - azimuth — a ring in the horizontal plane through the centre of the cuboid; its angle is the azimuth of the
 *   point from the centre (clockwise from north);
 * - dip — a half circle in the vertical plane of the axis of the swaths, from −90° (down) to +90° (up): its angle
 *   is the dip (negative — the axis goes down, as the holes of BF2; question № 29);
 * - roll — a circle about the axis of the swaths at the far end face: its angle is the roll of v from h toward n.
 *
 * A drag: the ray of the cursor ∩ the plane of the handle (perpendicular to its axis of turn) → the angle from the
 * one at the press; the new angle in steps of 1°, with `Shift` of 5°; nothing else changes.
 */
import { add, dot, scale, sub, type Vec3 } from "../core/vec3";
import { projectToScreen, type Mat4 } from "../pick/ray";
import type { Ray } from "../section/math";
import { sinCosDegCore } from "../section/plane";
import { atan2Deg, type Frame } from "./frame";

export type HandleKind = "azimuth" | "dip" | "roll";
export const HANDLE_KINDS: readonly HandleKind[] = ["azimuth", "dip", "roll"];

export interface Angles {
  azimuth: number;
  dip: number;
  roll: number;
}

export interface Handle {
  kind: HandleKind;
  /** absolute centre of the circle */
  centre: Vec3;
  /** the axis of the turn: the normal of the plane of the handle */
  axis: Vec3;
  /** the angle of a point p is atan2((p − centre)·e2, (p − centre)·e1), degrees */
  e1: Vec3;
  e2: Vec3;
  radius: number;
  /** the arc drawn, degrees */
  from: number;
  to: number;
  /** the angle now: the knob */
  at: number;
}

/** A handle is hit within this many px of its line (ADR 0019 §6). */
export const HIT_PX = 8;
/** The plane of a handle seen this edge-on (|cos| of the ray and its axis) gives no angle. */
const GRAZING = 1e-6;

const z0 = (x: number) => x + 0;

/** The three handles of a cuboid: its absolute centre, its half sizes along u, v, w and the angles of its box. */
export function handlesOf(a: Angles, centre: Vec3, half: Vec3): Handle[] {
  const [sa, ca] = sinCosDegCore(a.azimuth);
  const [sd, cd] = sinCosDegCore(a.dip);
  const u0: Vec3 = [sa, ca, 0];
  const h: Vec3 = [ca, -sa, 0];
  const up: Vec3 = [0, 0, 1];
  const n: Vec3 = [-sd * sa, -sd * ca, cd];
  const u: Vec3 = [cd * sa, cd * ca, sd];
  const reach = Math.max(Math.hypot(half[0], half[1], half[2]), 1e-3);
  const end = add(centre, scale(u, half[0]));
  return [
    { kind: "azimuth", centre, axis: up, e1: [0, 1, 0], e2: [1, 0, 0], radius: 1.3 * reach, from: 0, to: 360, at: a.azimuth },
    { kind: "dip", centre, axis: h, e1: u0, e2: up, radius: 1.12 * reach, from: -90, to: 90, at: a.dip },
    { kind: "roll", centre: end, axis: u, e1: h, e2: n, radius: Math.max(1.15 * Math.hypot(half[1], half[2]), 0.25 * reach), from: -180, to: 180, at: a.roll },
  ];
}

/** The point of a handle at an angle, degrees (absolute). */
export function pointAt(h: Handle, deg: number): Vec3 {
  const r = (deg * Math.PI) / 180;
  return add(h.centre, add(scale(h.e1, h.radius * Math.cos(r)), scale(h.e2, h.radius * Math.sin(r))));
}

/** The line of a handle: points every ≤ 4° from `from` to `to`. */
export function polyline(h: Handle): Vec3[] {
  const n = Math.max(2, Math.ceil((h.to - h.from) / 4));
  return Array.from({ length: n + 1 }, (_, i) => pointAt(h, h.from + ((h.to - h.from) * i) / n));
}

/** The view as hit-testing needs it: clip ← project coordinates and the size in px (`Scene.projectView`). */
export interface HitView {
  matrix: Mat4;
  width: number;
  height: number;
}

function segmentDistance(px: number, py: number, a: readonly number[], b: readonly number[]): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 > 0 ? Math.min(1, Math.max(0, ((px - a[0]) * dx + (py - a[1]) * dy) / l2)) : 0;
  return Math.hypot(px - a[0] - t * dx, py - a[1] - t * dy);
}

/** The distance in px from a canvas point to the line of a handle as drawn (Infinity — not on the screen). */
export function screenDistance(h: Handle, view: HitView, px: number, py: number): number {
  let best = Infinity;
  let prev: number[] | null = null;
  for (const p of polyline(h)) {
    const s = projectToScreen(view.matrix, p[0], p[1], p[2], view.width, view.height);
    if (s && prev) best = Math.min(best, segmentDistance(px, py, prev, s));
    else if (s) best = Math.min(best, Math.hypot(px - s[0], py - s[1]));
    prev = s;
  }
  return best;
}

/** The handle under a canvas point: the nearest within `tol` px; null — none (the pointer is the scene's). */
export function hitHandle(handles: readonly Handle[], view: HitView, px: number, py: number, tol = HIT_PX): HandleKind | null {
  let best: HandleKind | null = null;
  let dist = tol;
  for (const h of handles) {
    const d = screenDistance(h, view, px, py);
    if (d <= dist) {
      dist = d;
      best = h.kind;
    }
  }
  return best;
}

/** Where the ray meets the plane through p with the normal n; null — the ray runs along the plane. */
export function onPlane(p: Vec3, n: Vec3, ray: Ray): Vec3 | null {
  const den = dot(n, ray.dir);
  if (Math.abs(den) < GRAZING) return null;
  const t = dot(n, sub(p, ray.origin)) / den;
  return add(ray.origin, scale(ray.dir, t));
}

/** The angle of the point of the ray on the plane of a handle, degrees (−180; 180]; null — no point or the centre itself. */
export function angleOn(h: Handle, ray: Ray): number | null {
  const q = onPlane(h.centre, h.axis, ray);
  if (!q) return null;
  const d = sub(q, h.centre);
  const x = dot(d, h.e1);
  const y = dot(d, h.e2);
  if (Math.hypot(x, y) <= 1e-9 * Math.max(1, h.radius)) return null;
  return atan2Deg(y, x);
}

const wrap180 = (x: number) => {
  const r = (((x + 180) % 360) + 360) % 360 - 180;
  return r === -180 ? 180 : r;
};

/** An angle of a handle put in its range: azimuth [0; 360), dip [−90; 90], roll (−180; 180]. */
export function clampAngle(kind: HandleKind, value: number): number {
  if (kind === "dip") return z0(Math.min(90, Math.max(-90, value)));
  if (kind === "roll") return z0(wrap180(value));
  const a = ((value % 360) + 360) % 360;
  return z0(a >= 360 ? 0 : a);
}

/** The step of a drag, degrees: 1°, with `Shift` 5° (ADR 0019 §6). */
export const dragStep = (shift: boolean): number => (shift ? 5 : 1);

/**
 * The angles after a drag of the handle `h` from the ray `from` (the press) to the ray `to` (now): its angle turns
 * by the angle of the cursor about the centre, the result in whole steps (a turn of less than half a step keeps
 * the angle as it was); the other two stay. Null — the ray misses the plane of the handle (seen edge-on).
 */
export function dragAngles(start: Angles, h: Handle, from: Ray, to: Ray, shift = false): Angles | null {
  const a0 = angleOn(h, from);
  const a1 = angleOn(h, to);
  if (a0 === null || a1 === null) return null;
  const delta = wrap180(a1 - a0);
  const step = dragStep(shift);
  const was = start[h.kind];
  if (Math.abs(delta) < step / 2) return { ...start };
  return { ...start, [h.kind]: clampAngle(h.kind, Math.round((was + delta) / step) * step) };
}

export const sameAngles = (a: Angles, b: Angles): boolean => a.azimuth === b.azimuth && a.dip === b.dip && a.roll === b.roll;

/** The turn (3 × 3, rows) that takes the axes of `from` onto those of `to`: R = Σ to_k ⊗ from_k — the cuboid turned about its centre. */
export function rotationBetween(from: Pick<Frame, "u" | "v" | "w">, to: Pick<Frame, "u" | "v" | "w">): number[] {
  const m = new Array<number>(9).fill(0);
  for (const k of ["u", "v", "w"] as const) for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) m[i * 3 + j] += to[k][i] * from[k][j];
  return m;
}

/** R·p. */
export const turn = (m: readonly number[], p: Vec3): Vec3 => [m[0] * p[0] + m[1] * p[1] + m[2] * p[2], m[3] * p[0] + m[4] * p[1] + m[5] * p[2], m[6] * p[0] + m[7] * p[1] + m[8] * p[2]];
