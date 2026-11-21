/**
 * The handles of an anisotropy ellipsoid (ТЗ 7.35 Д-14; ADR 0022 §9; образец — `viz/swath/drag.ts`, Д-01): where
 * the six handles are, which one is under the cursor and what a drag does to the model. Pure, float64, project
 * coordinates (metres, Z up, no exaggeration) — vitest without three.js (ТД14-1, ТД14-2).
 *
 * - ang1 — a ring in the horizontal plane through the centre; its angle is the azimuth of the point (clockwise from
 *   north), [0; 360);
 * - ang2 — a half circle in the vertical plane of the major axis, −90° (down) … +90° (up): its angle is the
 *   elevation of a1, u1z = sin ang2 (the plunge shown to the user is −ang2);
 * - ang3 — a circle about the major axis at its end: its angle is the turn t of u2, u2 = cos t·P + sin t·Q
 *   (P, Q — the axes a2, a3 at t = 0, rows of 8.6.3);
 * - a1, a2, a3 — knobs at the ends of the axes: the point of the axis nearest to the ray of the cursor → the range.
 *
 * A drag changes only its own parameter: an angle in whole steps of 1° (`Shift` — 5°) from the one at the press; a
 * range by the move along its axis, three significant digits, not less than the minimum (L / 2); with `Ctrl` all three
 * ranges in one ratio. A plane seen edge-on or an axis along the ray gives no value (null — the drag holds).
 */
import { add, dot, scale, sub, type Vec3 } from "../core/vec3";
import { projectToScreen, type Mat4 } from "../pick/ray";
import type { Ray } from "../section/math";
import { sinCosDegCore } from "../section/plane";
import { atan2Deg } from "../swath/frame";
import { frameOf, normalizeAngles, type Angles, type Ranges } from "./frame";

export type AngleKind = "ang1" | "ang2" | "ang3";
export type RangeKind = "a1" | "a2" | "a3";
export type HandleKind = AngleKind | RangeKind;
export const HANDLE_KINDS: readonly HandleKind[] = ["ang1", "ang2", "ang3", "a1", "a2", "a3"];

/** A circle handle: the ring of ang1, the arcs of ang2 and ang3. */
export interface ArcHandle {
  kind: AngleKind;
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
  /** the angle now: the knob of the arc */
  at: number;
}

/** A knob at the end of an axis. */
export interface KnobHandle {
  kind: RangeKind;
  /** the centre of the ellipsoid: the axis runs from it */
  centre: Vec3;
  /** the unit axis u1, u2 or u3 */
  axis: Vec3;
  /** the range now */
  range: number;
  /** the knob: centre + range·axis */
  at: Vec3;
}

export type Handle = ArcHandle | KnobHandle;
export const isArc = (h: Handle): h is ArcHandle => h.kind === "ang1" || h.kind === "ang2" || h.kind === "ang3";

/** The model of one structure as the handles see it. */
export interface Model {
  angles: Angles;
  ranges: Ranges;
}

/** A handle is hit within this many px of its line or knob (ADR 0022 §9, as Д-01). */
export const HIT_PX = 8;
/** |cos| of the ray and the normal of a handle below it: the plane is seen edge-on, the angle is unstable — none. */
export const EDGE_ON = 0.02;
/** The default least range, metres (the window passes L / 2). */
export const MIN_RANGE = 1e-3;

const z0 = (x: number) => x + 0;
const RANGE_OF: Record<RangeKind, keyof Ranges> = { a1: "a1", a2: "a2", a3: "a3" };

/** The six handles of a structure around its absolute centre: ring, two arcs, three knobs. */
export function handlesOf(angles: Angles, ranges: Ranges, centre: Vec3): Handle[] {
  const f = frameOf(angles);
  const reach = Math.max(ranges.a1, ranges.a2, ranges.a3, 1e-3);
  const [s1, c1] = sinCosDegCore(f.ang1);
  const a = f.ang1 < 270 ? 90 - f.ang1 : 450 - f.ang1;
  const [sa, ca] = sinCosDegCore(a);
  const [sb, cb] = sinCosDegCore(-f.ang2);
  const up: Vec3 = [0, 0, 1];
  const u0: Vec3 = [s1, c1, 0];
  const h: Vec3 = [c1, z0(-s1), 0];
  const p: Vec3 = [z0(-sa), ca, 0];
  const q: Vec3 = [z0(sb * ca), z0(sb * sa), cb];
  const end = add(centre, scale(f.u1, ranges.a1));
  const knob = (kind: RangeKind, axis: Vec3, range: number): KnobHandle => ({ kind, centre, axis, range, at: add(centre, scale(axis, range)) });
  return [
    { kind: "ang1", centre, axis: up, e1: [0, 1, 0], e2: [1, 0, 0], radius: 1.3 * reach, from: 0, to: 360, at: f.ang1 },
    { kind: "ang2", centre, axis: h, e1: u0, e2: up, radius: 1.15 * reach, from: -90, to: 90, at: f.ang2 },
    { kind: "ang3", centre: end, axis: f.u1, e1: p, e2: q, radius: Math.max(0.6 * Math.max(ranges.a2, ranges.a3), 0.2 * reach), from: -180, to: 180, at: f.ang3 },
    knob("a1", f.u1, ranges.a1),
    knob("a2", f.u2, ranges.a2),
    knob("a3", f.u3, ranges.a3),
  ];
}

/** The point of an arc at an angle, degrees (absolute). */
export function pointAt(h: ArcHandle, deg: number): Vec3 {
  const r = (deg * Math.PI) / 180;
  return add(h.centre, add(scale(h.e1, h.radius * Math.cos(r)), scale(h.e2, h.radius * Math.sin(r))));
}

/** The line of an arc: points every ≤ 4° from `from` to `to`. */
export function polyline(h: ArcHandle): Vec3[] {
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

/** The distance in px from a canvas point to a handle as drawn: the line of an arc, the knob of an axis (Infinity — off the screen). */
export function screenDistance(h: Handle, view: HitView, px: number, py: number): number {
  const at = (p: Vec3) => projectToScreen(view.matrix, p[0], p[1], p[2], view.width, view.height);
  if (!isArc(h)) {
    const s = at(h.at);
    return s ? Math.hypot(px - s[0], py - s[1]) : Infinity;
  }
  let best = Infinity;
  let prev: number[] | null = null;
  for (const p of polyline(h)) {
    const s = at(p);
    if (s && prev) best = Math.min(best, segmentDistance(px, py, prev, s));
    else if (s) best = Math.min(best, Math.hypot(px - s[0], py - s[1]));
    prev = s;
  }
  return best;
}

/** The handle under a canvas point: the nearest within `tol` px, a knob before an arc at equal distance; null — none (the pointer goes on down the chain). */
export function hitHandle(handles: readonly Handle[], view: HitView, px: number, py: number, tol = HIT_PX): HandleKind | null {
  let best: HandleKind | null = null;
  let dist = Infinity;
  const ordered = [...handles.filter((h) => !isArc(h)), ...handles.filter(isArc)];
  for (const h of ordered) {
    const d = screenDistance(h, view, px, py);
    if (d <= tol && d < dist) {
      dist = d;
      best = h.kind;
    }
  }
  return best;
}

/** Where the ray meets the plane through p with the unit normal n; null — the plane is seen edge-on. */
export function onPlane(p: Vec3, n: Vec3, ray: Ray, edgeOn = EDGE_ON): Vec3 | null {
  const den = dot(n, ray.dir);
  if (Math.abs(den) < edgeOn * Math.hypot(ray.dir[0], ray.dir[1], ray.dir[2])) return null;
  const t = dot(n, sub(p, ray.origin)) / den;
  return add(ray.origin, scale(ray.dir, t));
}

/** The angle of the point of the ray on the plane of an arc, degrees (−180; 180]; null — no point or the centre itself. */
export function angleOn(h: ArcHandle, ray: Ray): number | null {
  const q = onPlane(h.centre, h.axis, ray);
  if (!q) return null;
  const d = sub(q, h.centre);
  const x = dot(d, h.e1);
  const y = dot(d, h.e2);
  if (Math.hypot(x, y) <= 1e-9 * Math.max(1, h.radius)) return null;
  return atan2Deg(y, x);
}

/** The coordinate along the axis of a knob of its point nearest to the ray; null — the axis runs along the ray. */
export function alongAxis(h: KnobHandle, ray: Ray): number | null {
  const u = h.axis;
  const d = ray.dir;
  const w = sub(h.centre, ray.origin);
  const b = dot(u, d);
  const c = dot(d, d);
  const den = c - b * b; // |u| = 1
  if (!(den > 1e-12 * c)) return null;
  return (b * dot(d, w) - c * dot(u, w)) / den;
}

const wrap180 = (x: number) => {
  const r = (((x + 180) % 360) + 360) % 360 - 180;
  return r === -180 ? 180 : r;
};

/** An angle put in its range: ang1 [0; 360), ang2 [−90; 90], ang3 (−180; 180]. */
export function clampAngle(kind: AngleKind, value: number): number {
  if (kind === "ang2") return z0(Math.min(90, Math.max(-90, value)));
  if (kind === "ang3") return z0(wrap180(value));
  const a = ((value % 360) + 360) % 360;
  return z0(a >= 360 ? 0 : a);
}

/** The step of a drag of an angle, degrees: 1°, with `Shift` 5°. */
export const dragStep = (shift: boolean): number => (shift ? 5 : 1);

/** A range in three significant digits (ADR 0022 §9). */
export const roundRange = (x: number): number => Number(x.toPrecision(3));

/**
 * The angles after a drag of the arc `h` from the ray `from` (the press) to the ray `to` (now): its angle turns by
 * the angle of the cursor about the centre, the result in whole steps (a turn of less than half a step keeps the
 * angle); the other two stay. Null — a ray misses the plane of the arc.
 */
export function dragAngle(start: Angles, h: ArcHandle, from: Ray, to: Ray, shift = false): Angles | null {
  const a0 = angleOn(h, from);
  const a1 = angleOn(h, to);
  if (a0 === null || a1 === null) return null;
  const s = normalizeAngles(start);
  const delta = wrap180(a1 - a0);
  const step = dragStep(shift);
  if (Math.abs(delta) < step / 2) return s;
  return { ...s, [h.kind]: clampAngle(h.kind, Math.round((s[h.kind] + delta) / step) * step) };
}

export interface RangeDragOptions {
  /** all three ranges in one ratio */
  ctrl?: boolean;
  /** the least range, metres (L / 2) */
  min?: number;
}

/**
 * The ranges after a drag of the knob `h`: its range moves by the move of the nearest point of its axis from the
 * press to now, in three significant digits, not below `min`; with `ctrl` the other two follow in the ratio of the
 * dragged one (k raised so that none falls below `min`). Null — a ray runs along the axis.
 */
export function dragRange(start: Ranges, h: KnobHandle, from: Ray, to: Ray, opts: RangeDragOptions = {}): Ranges | null {
  const t0 = alongAxis(h, from);
  const t1 = alongAxis(h, to);
  if (t0 === null || t1 === null) return null;
  const min = opts.min ?? MIN_RANGE;
  const key = RANGE_OF[h.kind];
  const was = start[key];
  const next = Math.max(min, roundRange(was + (t1 - t0)));
  if (!opts.ctrl) return { ...start, [key]: next };
  const least = Math.max(min / start.a1, min / start.a2, min / start.a3);
  const k = Math.max(next / was, least);
  const put = (x: number) => Math.max(min, roundRange(x * k));
  return { a1: put(start.a1), a2: put(start.a2), a3: put(start.a3) };
}

export interface DragModifiers {
  shift?: boolean;
  ctrl?: boolean;
  min?: number;
}

/** A drag of any of the six handles: the model with only that parameter changed (or all ranges with `ctrl`); null — no value. */
export function dragModel(start: Model, h: Handle, from: Ray, to: Ray, mods: DragModifiers = {}): Model | null {
  if (isArc(h)) {
    const angles = dragAngle(start.angles, h, from, to, mods.shift);
    return angles && { angles, ranges: { ...start.ranges } };
  }
  const ranges = dragRange(start.ranges, h, from, to, { ctrl: mods.ctrl, min: mods.min });
  return ranges && { angles: normalizeAngles(start.angles), ranges };
}

export const sameModel = (a: Model, b: Model): boolean =>
  a.angles.ang1 === b.angles.ang1 && a.angles.ang2 === b.angles.ang2 && a.angles.ang3 === b.angles.ang3 && a.ranges.a1 === b.ranges.a1 && a.ranges.a2 === b.ranges.a2 && a.ranges.a3 === b.ranges.a3;
