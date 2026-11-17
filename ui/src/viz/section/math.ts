/**
 * Vertical sections (ТЗ 7.14, ADR 0007 §4): the line and its window, profiles, a line by two points or
 * through a hole, the automatic section across the collars, and the clipping planes. float64 throughout,
 * no three.js. Ported from BF `geometry.ts` and `SectionMap.tsx` (windowCorners).
 */
import { normalize, sub, dot, type Plane, type Vec3 } from "../core/vec3";

/** 1-2-5 step giving roughly `target` ticks over `span`. */
export function niceStep(span: number, target = 6): number {
  if (!(span > 0)) return 1;
  const raw = span / Math.max(target, 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * pow;
}

export function niceTicks(min: number, max: number, target = 6): number[] {
  if (!(max > min)) return [];
  const step = niceStep(max - min, target);
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/**
 * A vertical section: a line from (x0, y0) along azimuth `az` and a clipping window around it.
 * The viewer stands to the right of the line and looks at it, so the line runs left to right on screen:
 * `towards` is the window depth on the viewer's side, `away` the depth behind the plane (map units).
 * Coordinates are project (absolute) float64.
 */
export interface SectionLine {
  x0: number;
  y0: number;
  /** degrees clockwise from north, [0; 360) */
  az: number;
  /** null = unlimited */
  length: number | null;
  towards: number;
  away: number;
}

export const DEFAULT_WINDOW = 25;

/** sin and cos of degrees, exact on the multiples of 90° (a north or east line stays exactly on its axis). */
export function sinCosDeg(deg: number): [number, number] {
  const d = ((deg % 360) + 360) % 360;
  if (d === 0) return [0, 1];
  if (d === 90) return [1, 0];
  if (d === 180) return [0, -1];
  if (d === 270) return [-1, 0];
  const a = (d * Math.PI) / 180;
  return [Math.sin(a), Math.cos(a)];
}

/** Unit vector along a section line (azimuth clockwise from north). */
export function sectionDirection(az: number): Vec3 {
  const [s, c] = sinCosDeg(az);
  return [s, c, 0];
}

/** Unit vector to the right of a section line: towards the viewer. */
export function sectionNormal(az: number): Vec3 {
  const [s, c] = sinCosDeg(az);
  return [c, -s, 0];
}

/** u: distance along the section from its origin; d: signed distance to the right of the line. */
export function toSection(x: number, y: number, line: SectionLine): { u: number; d: number } {
  const [s, c] = sinCosDeg(line.az);
  const dx = x - line.x0;
  const dy = y - line.y0;
  return { u: dx * s + dy * c, d: dx * c - dy * s };
}

export function azimuthDeg(dx: number, dy: number): number {
  const deg = (Math.atan2(dx, dy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export function inCorridor(x: number, y: number, line: SectionLine): boolean {
  const { u, d } = toSection(x, y, line);
  // `d` is positive to the right of the line, where the viewer is.
  return d <= line.towards && d >= -line.away && (line.length === null || (u >= -1 && u <= line.length + 1));
}

/**
 * A sensible first section: through the centre of the collars, across their main
 * direction (perpendicular to the strike), so most holes are seen from the side.
 * Points with a missing coordinate are left out.
 */
export function defaultSection(points: readonly (readonly [number, number])[], towards = DEFAULT_WINDOW, away = DEFAULT_WINDOW): SectionLine {
  const pts = points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (pts.length === 0) return { x0: 0, y0: 0, az: 90, length: null, towards, away };
  const n = pts.length;
  const mx = pts.reduce((s, p) => s + p[0], 0) / n;
  const my = pts.reduce((s, p) => s + p[1], 0) / n;
  if (n < 2) return { x0: mx, y0: my, az: 90, length: null, towards, away };
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const [x, y] of pts) {
    sxx += (x - mx) ** 2;
    syy += (y - my) ** 2;
    sxy += (x - mx) * (y - my);
  }
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy); // main axis, counter-clockwise from east
  const strike = 90 - (theta * 180) / Math.PI; // clockwise from north
  return { x0: mx, y0: my, az: (((strike + 90) % 360) + 360) % 360, length: null, towards, away };
}

/** XY of the collars of a BFA1 `collar f8×3` array, for defaultSection. */
export function collarsXY(collar: Float64Array): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i + 2 < collar.length; i += 3) out.push([collar[i], collar[i + 1]]);
  return out;
}

/** The same section moved across itself by `distance` (positive: towards the viewer's side). */
export function shiftSection(line: SectionLine, distance: number): SectionLine {
  const [s, c] = sinCosDeg(line.az);
  return { ...line, x0: line.x0 + c * distance, y0: line.y0 - s * distance };
}

/** Width of a profile step: the whole window, towards + away. */
export function profileStep(line: SectionLine): number {
  return line.towards + line.away;
}

/** `]` (+1): the next profile, one window away from the viewer; `[` (−1): one window towards the viewer. */
export function nextProfile(line: SectionLine, direction: 1 | -1): SectionLine {
  return shiftSection(line, -profileStep(line) * direction);
}

/**
 * A line by two points (project coordinates): from the first, along the azimuth to the second, unlimited.
 * The window is kept from `base` (or 25 / 25 m). Two equal points give null.
 */
export function lineFromPoints(a: { x: number; y: number }, b: { x: number; y: number }, base?: Pick<SectionLine, "towards" | "away"> | null): SectionLine | null {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (!(Math.hypot(dx, dy) > 1e-6)) return null;
  return { x0: a.x, y0: a.y, az: azimuthDeg(dx, dy), length: null, towards: base?.towards ?? DEFAULT_WINDOW, away: base?.away ?? DEFAULT_WINDOW };
}

/**
 * Through a hole: through its collar along its azimuth in plan (`plan_az` of the core, collar to bottom),
 * so the whole hole lies in the plane. A vertical hole (NaN) keeps the azimuth of `base`.
 */
export function lineThroughHole(collar: { x: number; y: number }, planAz: number, base?: SectionLine | null): SectionLine {
  const az = Number.isFinite(planAz) ? ((planAz % 360) + 360) % 360 : base?.az ?? 90;
  return { x0: collar.x, y0: collar.y, az, length: null, towards: base?.towards ?? DEFAULT_WINDOW, away: base?.away ?? DEFAULT_WINDOW };
}

/**
 * The clipping planes of the window, relative to the scene origin (rendered space; the planes are
 * vertical, so the vertical exaggeration does not change them): 2 planes, or 4 with a length.
 * A point is in the window where every distance is ≥ 0.
 */
export function clipPlanes(line: SectionLine, origin: { x: number; y: number }): Plane[] {
  const n = sectionNormal(line.az);
  const along = sectionDirection(line.az);
  const cx = line.x0 - origin.x;
  const cy = line.y0 - origin.y;
  const nc = n[0] * cx + n[1] * cy;
  const ac = along[0] * cx + along[1] * cy;
  const planes: Plane[] = [
    { normal: [n[0], n[1], 0], constant: -nc + line.away },
    { normal: [-n[0], -n[1], 0], constant: nc + line.towards },
  ];
  if (line.length !== null) {
    planes.push({ normal: [along[0], along[1], 0], constant: -ac });
    planes.push({ normal: [-along[0], -along[1], 0], constant: ac + line.length });
  }
  return planes;
}

/** Corners of the section window in plan: along the line, from `away` behind to `towards` in front. */
export function windowCorners(s: SectionLine, reach: number): [number, number][] {
  const along = sectionDirection(s.az);
  const right = sectionNormal(s.az);
  const u0 = s.length === null ? -reach : 0;
  const u1 = s.length ?? reach;
  const at = (u: number, d: number): [number, number] => [s.x0 + along[0] * u + right[0] * d, s.y0 + along[1] * u + right[1] * d];
  return [at(u0, -s.away), at(u1, -s.away), at(u1, s.towards), at(u0, s.towards)];
}

/** Range of `u` (along the line) covered by a plan rectangle: the line from edge to edge of the content. */
export function extentAlong(line: SectionLine, minX: number, minY: number, maxX: number, maxY: number): [number, number] {
  if (line.length !== null) return [0, line.length];
  let from = Infinity;
  let to = -Infinity;
  for (const x of [minX, maxX]) {
    for (const y of [minY, maxY]) {
      const { u } = toSection(x, y, line);
      from = Math.min(from, u);
      to = Math.max(to, u);
    }
  }
  return [from, to];
}

export interface Ray {
  origin: Vec3;
  dir: Vec3;
}

/**
 * A click for drawing a line (rendered space, BF `pickPoint`): on the horizontal plane through the view
 * target, or — for a view from the side (|view.z| ≤ 0.2) — on the vertical plane through it facing the
 * viewer. `view` is the unit vector from the target to the camera. Null when the ray misses the plane.
 */
export function viewPlanePoint(ray: Ray, target: Vec3, view: Vec3): Vec3 | null {
  const normal: Vec3 = Math.abs(view[2]) > 0.2 ? [0, 0, 1] : normalize([view[0], view[1], 0]);
  const denom = dot(normal, ray.dir);
  if (Math.abs(denom) < 1e-12) return null;
  const t = dot(normal, sub(target, ray.origin)) / denom;
  if (!(t >= 0)) return null;
  return [ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t, ray.origin[2] + ray.dir[2] * t];
}
