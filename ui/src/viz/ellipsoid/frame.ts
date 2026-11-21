/**
 * The frame of an anisotropy ellipsoid (ТЗ 8.6.3, 7.35 Д-14; ADR 0022 §6, §9) — the mirror of `core/bf_core/geostat/
 * rotation.py`, read against the shared answers `core/tests/data/variography/frames.json` (ТД14-1). GSLIB `setrot`:
 *
 *     a = 90° − ang1 (0 ≤ ang1 < 270°), else 450° − ang1;   b = −ang2;   t = ang3
 *     R = [[  cos b·cos a,                       cos b·sin a,                      −sin b      ],
 *          [ −cos t·sin a + sin t·sin b·cos a,   cos t·cos a + sin t·sin b·sin a,   sin t·cos b ],
 *          [  sin t·sin a + cos t·sin b·cos a,  −sin t·cos a + cos t·sin b·sin a,   cos t·cos b ]]
 *
 * The rows of R are the axes a1, a2, a3 in project coordinates (x east, y north, z up). Ranges: ang1 [0; 360),
 * ang2 [−90; 90] (the plunge shown to the user is p = −ang2), ang3 (−180; 180]. sin and cos are exact at multiples
 * of 90° (`sinCosDegCore`): zero angles give the axes (0; 1; 0), (−1; 0; 0), (0; 0; 1) bit for bit. Float64
 * throughout; pure (vitest without three.js).
 */
import type { Vec3 } from "../core/vec3";
import { sinCosDegCore } from "../section/plane";
import { atan2Deg } from "../swath/frame";

/** The three GSLIB angles of a structure, degrees. */
export interface Angles {
  ang1: number;
  ang2: number;
  ang3: number;
}

/** The three ranges of a structure along a1, a2, a3 (> 0, metres). */
export interface Ranges {
  a1: number;
  a2: number;
  a3: number;
}

export interface EllipsoidFrame extends Angles {
  /** the major axis a1 — row 1 of R */
  u1: Vec3;
  /** the semi-major axis a2 — row 2 */
  u2: Vec3;
  /** the minor axis a3 — row 3 */
  u3: Vec3;
}

/** |u1_z| beyond it: the major axis is vertical; ang1 is kept (the window keeps three numbers, not a matrix). */
export const VERTICAL = 1 - 1e-12;

const z0 = (x: number) => x + 0; // −0 is written 0
const fmod360 = (x: number) => ((x % 360) + 360) % 360;

function checkFinite(values: readonly number[]): void {
  if (!values.every(Number.isFinite)) throw new RangeError("Углы эллипсоида — числа");
}

/** The angles in their ranges: ang1 [0; 360), ang3 (−180; 180]; ang2 beyond ±90° is refused, as the core does. */
export function normalizeAngles(a: Angles): Angles {
  checkFinite([a.ang1, a.ang2, a.ang3]);
  if (Math.abs(a.ang2) > 90) throw new RangeError("Угол ang2 (погружение) должен быть от −90° до +90°");
  const ang1 = fmod360(a.ang1);
  let ang3 = fmod360(a.ang3);
  if (ang3 > 180) ang3 -= 360;
  return { ang1: z0(ang1 >= 360 ? 0 : ang1), ang2: z0(a.ang2), ang3: z0(ang3) };
}

/** R of 8.6.3, rows; the angles normalized first. */
export function rotationRows(angles: Angles): [Vec3, Vec3, Vec3] {
  const { ang1, ang2, ang3 } = normalizeAngles(angles);
  const a = ang1 < 270 ? 90 - ang1 : 450 - ang1;
  const [sa, ca] = sinCosDegCore(a);
  const [sb, cb] = sinCosDegCore(-ang2);
  const [st, ct] = sinCosDegCore(ang3);
  return [
    [z0(cb * ca), z0(cb * sa), z0(-sb)],
    [z0(-ct * sa + st * sb * ca), z0(ct * ca + st * sb * sa), z0(st * cb)],
    [z0(st * sa + ct * sb * ca), z0(-st * ca + ct * sb * sa), z0(ct * cb)],
  ];
}

/** The frame of three angles. */
export function frameOf(angles: Angles): EllipsoidFrame {
  const n = normalizeAngles(angles);
  const [u1, u2, u3] = rotationRows(n);
  return { ...n, u1, u2, u3 };
}

/** asin of a unit component as an angle, degrees, with the precision of atan2 near ±1; exact 0 and ±90. */
function elevationDeg(z: number, horizontal: number): number {
  if (z >= 1) return 90;
  if (z <= -1) return -90;
  return atan2Deg(z, horizontal);
}

/**
 * The angles of the axes u1, u2 (rows 1 and 2 of R; u3 = u1 × u2 is implied): ang1 = atan2(u1x, u1y) mod 360,
 * ang2 = asin(u1z) (as atan2 of u1z and the horizontal part — the same angle, without the loss of asin near ±90°),
 * ang3 = atan2(R23, R33). A vertical major axis (|u1z| > 1 − 1e−12): ang1 stays `keepAng1`, ang3 is read from u2
 * against the horizontal axes of that azimuth.
 */
export function anglesOf(u1: Vec3, u2: Vec3, keepAng1 = 0): Angles {
  checkFinite([...u1, ...u2, keepAng1]);
  const l1 = Math.hypot(u1[0], u1[1], u1[2]);
  const l2 = Math.hypot(u2[0], u2[1], u2[2]);
  if (!(l1 > 0) || !(l2 > 0)) throw new RangeError("Оси эллипсоида: нужны два ненулевых вектора");
  const [x1, y1, zz1] = [u1[0] / l1, u1[1] / l1, u1[2] / l1];
  const [x2, y2, z2] = [u2[0] / l2, u2[1] / l2, u2[2] / l2];
  const rho = Math.hypot(x1, y1);
  const ang2 = elevationDeg(zz1, rho);
  let ang1: number;
  let ang3: number;
  if (Math.abs(zz1) <= VERTICAL) {
    ang1 = atan2Deg(x1, y1);
    // R23 = sin t·cos b, R33 = cos t·cos b; R3 = u1 × u2
    const r23 = z2;
    const r33 = x1 * y2 - y1 * x2;
    ang3 = atan2Deg(r23, r33);
  } else {
    ang1 = fmod360(keepAng1);
    // u2 = cos t·P + sin t·Q, P = (−sin a, cos a, 0), Q = sin b·(cos a, sin a, 0) at cos b = 0
    const a = ang1 < 270 ? 90 - ang1 : 450 - ang1;
    const [sa, ca] = sinCosDegCore(a);
    const sb = zz1 > 0 ? -1 : 1; // b = −ang2
    ang3 = atan2Deg(sb * (x2 * ca + y2 * sa), -x2 * sa + y2 * ca);
  }
  if (ang1 < 0) ang1 += 360;
  if (ang1 >= 360) ang1 = 0;
  if (ang3 <= -180) ang3 = 180;
  return { ang1: z0(ang1), ang2: z0(ang2), ang3: z0(ang3) };
}

/** The angles of a frame read back from its axes (the round trip of ТД14-1). */
export const anglesOfFrame = (f: Pick<EllipsoidFrame, "u1" | "u2" | "ang1">): Angles => anglesOf(f.u1, f.u2, f.ang1);

/**
 * The unit sphere → the ellipsoid: the 3 × 3 matrix (rows) whose columns are a1·u1, a2·u2, a3·u3, i.e. Rᵀ·diag(a1,
 * a2, a3). A point of the sphere s goes to centre + M·s; a drag changes only this matrix (ADR 0022 §9).
 */
export function ellipsoidBasis(f: Pick<EllipsoidFrame, "u1" | "u2" | "u3">, r: Ranges): number[] {
  const { u1, u2, u3 } = f;
  return [
    u1[0] * r.a1, u2[0] * r.a2, u3[0] * r.a3,
    u1[1] * r.a1, u2[1] * r.a2, u3[1] * r.a3,
    u1[2] * r.a1, u2[2] * r.a2, u3[2] * r.a3,
  ].map(z0);
}

/** The reduced distance h_r = √((u1·Δ / a1)² + (u2·Δ / a2)² + (u3·Δ / a3)²) (ADR 0022 §6; 1 — on the ellipsoid). */
export function reducedDistance(f: Pick<EllipsoidFrame, "u1" | "u2" | "u3">, r: Ranges, d: Vec3): number {
  const p = (u: Vec3) => u[0] * d[0] + u[1] * d[1] + u[2] * d[2];
  return Math.sqrt((p(f.u1) / r.a1) ** 2 + (p(f.u2) / r.a2) ** 2 + (p(f.u3) / r.a3) ** 2);
}

/** The second notation of the plane a1–a2: dip α [0; 90], dip direction Aп [0; 360), pitch of u1 [0; 180) (ADR 0022 §6). */
export interface PlaneAngles {
  dip: number;
  dipDirection: number;
  pitch: number;
}

const acosDeg = (x: number) => (Math.acos(Math.min(1, Math.max(-1, x))) * 180) / Math.PI;

/** The strike line of a plane of dip direction Aп: the horizontal direction 90° anticlockwise of the dip direction. */
function strikeOf(dipDirection: number): Vec3 {
  const [s, c] = sinCosDegCore(dipDirection - 90);
  return [s, c, 0];
}

/** s × n: the down-dip direction in the plane (unit for a unit strike s ⟂ the unit normal n). */
const downDip = (s: Vec3, n: Vec3): Vec3 => [s[1] * n[2] - s[2] * n[1], s[2] * n[0] - s[0] * n[2], s[0] * n[1] - s[1] * n[0]];

/**
 * GSLIB angles → the plane a1–a2: the normal n = u3 turned up (n_z < 0 → −n, ADR 0021 п. 2); α = arccos(n_z),
 * Aп = atan2(n_x, n_y) mod 360 (α < 1e−6° → Aп = 0; a vertical plane, |n_z| ≤ 1e−12 — Aп ∈ [0; 180)); the pitch —
 * the angle in the plane from the strike line (Aп − 90°) toward the down-dip direction to the line of u1, [0; 180).
 */
export function planeAnglesOf(angles: Angles): PlaneAngles {
  const f = frameOf(angles);
  let n = f.u3;
  if (n[2] < 0) n = [-n[0], -n[1], -n[2]];
  const dip = n[2] >= 1 ? 0 : acosDeg(n[2]);
  let dipDirection = 0;
  if (dip >= 1e-6) {
    dipDirection = fmod360(atan2Deg(n[0], n[1]));
    if (Math.abs(n[2]) <= 1e-12 && dipDirection >= 180) {
      dipDirection -= 180;
      n = [-n[0], -n[1], -n[2]]; // the normal of that dip direction: the pitch is read against its down-dip side
    }
  }
  const s = strikeOf(dipDirection);
  const dw = downDip(s, n);
  const cs = f.u1[0] * s[0] + f.u1[1] * s[1] + f.u1[2] * s[2];
  const cd = f.u1[0] * dw[0] + f.u1[1] * dw[1] + f.u1[2] * dw[2];
  let pitch = fmod360(atan2Deg(cd, cs));
  if (pitch >= 180) pitch -= 180;
  if (pitch >= 180 - 1e-12) pitch = 0;
  return { dip: z0(dip), dipDirection: z0(dipDirection === 360 ? 0 : dipDirection), pitch: z0(pitch) };
}

/**
 * The plane a1–a2 → GSLIB angles: n from (α, Aп), u1 at the pitch from the strike line toward the down-dip side, u2 =
 * n × u1, so that u3 = u1 × u2 = n (up). The pitch fixes u1 up to its sign; the one with u1z ≤ 0 (plunging
 * down) is taken, as a pitch in [0; 180) means. `keepAng1` — for a vertical major axis.
 */
export function anglesOfPlane(p: PlaneAngles, keepAng1 = 0): Angles {
  checkFinite([p.dip, p.dipDirection, p.pitch]);
  if (p.dip < 0 || p.dip > 90) throw new RangeError("Падение плоскости должно быть от 0° до 90°");
  const [sd, cd] = sinCosDegCore(p.dip);
  const [sA, cA] = sinCosDegCore(p.dipDirection);
  const n: Vec3 = [sd * sA, sd * cA, cd];
  const s = strikeOf(p.dipDirection);
  const dw = downDip(s, n);
  const [sp, cp] = sinCosDegCore(p.pitch);
  // the pitch from s toward the down-dip direction: u1 = cos·s + sin·dw
  const u1: Vec3 = [cp * s[0] + sp * dw[0], cp * s[1] + sp * dw[1], cp * s[2] + sp * dw[2]];
  const u2: Vec3 = [n[1] * u1[2] - n[2] * u1[1], n[2] * u1[0] - n[0] * u1[2], n[0] * u1[1] - n[1] * u1[0]];
  return normalizeAngles(anglesOf(u1, u2, keepAng1));
}
