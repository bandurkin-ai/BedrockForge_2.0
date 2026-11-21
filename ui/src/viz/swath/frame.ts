/**
 * The frame of the box of a swath plot (ТЗ 7.35 Д-01; ADR 0019 §5) — the mirror of `core/bf_core/stats/
 * swath_frame.py`, read against the shared answers `core/tests/data/swath/frames.json` (ТД01-2; `rules` there say
 * how every number is computed). Angles in degrees: azimuth A of the axis of the swaths clockwise from north,
 * [0; 360); dip D, [−90; +90], negative — the axis goes down; roll R about the axis, (−180; +180].
 *
 *     u₀ = (sin A, cos A, 0)   h = (cos A, −sin A, 0)   u = cos D·u₀ + sin D·z   n = −sin D·u₀ + cos D·z
 *     v = cos R·h + sin R·n    w = −sin R·h + cos R·n   (u = w × v)
 *
 * X = (90; 0; 0), Y = (0; 0; 0), Z = (A; +90; 0). sin and cos are exact at multiples of 90° (`sinCosDegCore`), a
 * projection is the sum of its terms x, y, z from 0.0 with a zero component of the axis skipped (`axisDot`): an
 * azimuth of 90° gives the swaths of X bit for bit (ТД01-5). Float64 throughout; pure (vitest without three.js).
 */
import type { Vec3 } from "../core/vec3";
import { axisDot, sinCosDegCore } from "../section/plane";

export type Direction = "x" | "y" | "z" | "angles";

export interface Frame {
  azimuth: number;
  dip: number;
  roll: number;
  /** across the swaths: the axis of the plot, t = p·u */
  u: Vec3;
  /** along the swaths */
  v: Vec3;
  w: Vec3;
}

/** (min, max) along u, v, w. */
export interface Limits {
  u: readonly [number, number];
  v: readonly [number, number];
  w: readonly [number, number];
}

/** |u_z| from it on: the axis of the swaths is vertical (the azimuth and the roll are one turn). */
export const VERTICAL = 1 - 1e-12;
/** The relative tolerance of the ends of the swaths. */
export const TOL = 1e-9;

const z0 = (x: number) => x + 0; // −0 is written 0
const fmod360 = (x: number) => ((x % 360) + 360) % 360;

/** The angles in their ranges; a dip beyond ±90° is refused (it would be another azimuth), as the core does. */
export function normalizeAngles(azimuth: number, dip = 0, roll = 0): [number, number, number] {
  if (![azimuth, dip, roll].every(Number.isFinite)) throw new RangeError("Углы короба — числа");
  if (Math.abs(dip) > 90) throw new RangeError("Наклон оси полос должен быть от −90° до +90°");
  const a = fmod360(azimuth);
  let r = fmod360(roll);
  if (r > 180) r -= 360;
  return [z0(a), z0(dip), z0(r)];
}

/** The frame of three angles. */
export function frameOf(azimuth: number, dip = 0, roll = 0): Frame {
  const [a, d, r] = normalizeAngles(azimuth, dip, roll);
  const [sa, ca] = sinCosDegCore(a);
  const [sd, cd] = sinCosDegCore(d);
  const [sr, cr] = sinCosDegCore(r);
  const n: Vec3 = [-sd * sa, -sd * ca, cd];
  const u: Vec3 = [z0(cd * sa), z0(cd * ca), z0(sd)];
  const v: Vec3 = [z0(cr * ca + sr * n[0]), z0(-cr * sa + sr * n[1]), z0(sr * n[2])];
  const w: Vec3 = [z0(-sr * ca + cr * n[0]), z0(sr * sa + cr * n[1]), z0(cr * n[2])];
  return { azimuth: a, dip: d, roll: r, u, v, w };
}

/** X, Y — the coordinate axes as angles (90; 0; 0), (0; 0; 0); Z — (azimuth; +90; 0): the azimuth turns the swaths in plan; angles — the three angles. */
export function directionFrame(direction: Direction, azimuth = 0, dip = 0, roll = 0): Frame {
  switch (direction) {
    case "x":
      return frameOf(90);
    case "y":
      return frameOf(0);
    case "z":
      return frameOf(azimuth, 90);
    default:
      return frameOf(azimuth, dip, roll);
  }
}

/** atan2 in degrees, (−180; 180]; exact 0, ±90, 180 when an argument is zero. */
export function atan2Deg(y: number, x: number): number {
  if (y === 0) return x >= 0 ? 0 : 180;
  if (x === 0) return y > 0 ? 90 : -90;
  return Math.atan2(y, x) * (180 / Math.PI);
}

/** (azimuth, dip, roll) of the axes u (any length) and v; a vertical axis gives the roll 0 and the whole turn in the azimuth. */
export function anglesOf(u: Vec3, v: Vec3): [number, number, number] {
  const length = Math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2);
  if (!(length > 0) || !Number.isFinite(length) || !v.every(Number.isFinite)) throw new RangeError("Оси короба: нужны два ненулевых вектора");
  const [ux, uy, uz] = [u[0] / length, u[1] / length, u[2] / length];
  const [vx, vy, vz] = v;
  const rho = Math.hypot(ux, uy);
  const dip = atan2Deg(uz, rho);
  let azimuth: number;
  let roll: number;
  if (Math.abs(uz) < VERTICAL) {
    azimuth = atan2Deg(ux, uy);
    roll = atan2Deg(vz * rho * rho - uz * (vx * ux + vy * uy), vx * uy - vy * ux);
  } else {
    azimuth = atan2Deg(-vy, vx);
    roll = 0;
  }
  if (azimuth < 0) azimuth += 360;
  if (azimuth >= 360) azimuth = 0;
  if (roll <= -180) roll = 180;
  return [z0(azimuth), z0(dip), z0(roll)];
}

export const isVertical = (f: Pick<Frame, "u">): boolean => Math.abs(f.u[2]) >= VERTICAL;

/** p·axis as the core: from 0.0, the terms x, y, z in order, a zero component of the axis skipped. */
export const project = (p: Vec3, axis: Vec3): number => axisDot(p, axis);

/** The coordinates of a point along u, v, w. */
export const toFrame = (f: Pick<Frame, "u" | "v" | "w">, p: Vec3): Vec3 => [project(p, f.u), project(p, f.v), project(p, f.w)];

/** The project point of coordinates along u, v, w: per component, the terms u, v, w in this order from 0.0, zero components of the axes skipped. */
export function toWorld(f: Pick<Frame, "u" | "v" | "w">, c: Vec3): Vec3 {
  const out: Vec3 = [0, 0, 0];
  const axes = [f.u, f.v, f.w];
  for (let j = 0; j < 3; j++) for (let i = 0; i < 3; i++) if (axes[j][i] !== 0) out[i] += c[j] * axes[j][i];
  return out;
}

/** The eight corners of the cuboid: corner c takes the max along u when c & 4, along v when c & 2, along w when c & 1. */
export function corners(f: Pick<Frame, "u" | "v" | "w">, lim: Limits): Vec3[] {
  return Array.from({ length: 8 }, (_, c) => toWorld(f, [lim.u[(c >> 2) & 1], lim.v[(c >> 1) & 1], lim.w[c & 1]]));
}

/** The centre of the cuboid: the middles of the limits in project coordinates. */
export const boxCentre = (f: Pick<Frame, "u" | "v" | "w">, lim: Limits): Vec3 => toWorld(f, [0.5 * (lim.u[0] + lim.u[1]), 0.5 * (lim.v[0] + lim.v[1]), 0.5 * (lim.w[0] + lim.w[1])]);

/** The swath of a coordinate along u (from 0), −1 outside; the far boundary of the last swath is in it, both ends with the tolerance 1e−9·max(1, |start|, width). */
export function swathIndex(t: number, start: number, width: number, count: number): number {
  if (!Number.isFinite(t)) return -1;
  const tol = TOL * Math.max(1, Math.abs(start), width);
  const q = Math.floor((t - start) / width);
  if (q === count && t <= start + count * width + tol) return count - 1;
  if (q === -1 && t >= start - tol) return 0;
  return q >= 0 && q < count ? q : -1;
}
