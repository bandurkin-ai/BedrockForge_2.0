/**
 * The plane of a section in its general form (ТЗ 8.1.2; ADR 0016 §2, §4): a vertical line of 7.14, a section
 * of a series (vertical, plan, inclined) or a segment of a polyline section. The formulas are those of the core
 * (`core/bf_core/sections/geometry.py`: `sin_cos_deg`, `azimuth_deg`, `series_axes`, `dot`, `SectionPlane`),
 * checked against `core/tests/data/sections/series_cases.json`. float64 throughout, no three.js.
 *
 *     ŝ = (sin A, cos A, 0)                     along the line, left to right on the screen
 *     v̂ = (−cos A·sin φ, sin A·sin φ, −cos φ)   the direction of view (the viewer on the −v̂ side)
 *     ŵ = (−cos A·cos φ, sin A·cos φ, sin φ)    up on the screen
 *
 * u = u0 + (p − base)·ŝ, w = (p − base)·ŵ, t = (p − base)·v̂ − offset; the window −towards ≤ t ≤ away (closed on
 * the screen, ADR 0016 решение 12). Spaces: project (absolute) → scene (minus the integer scene origin) →
 * rendered (Z stretched by the exaggeration e): a plane n·p + c ≥ 0 of the scene is (n_x, n_y, n_z/e)·p + c in
 * rendered space, normalized when n_z ≠ 0 — a vertical plane is the same in both, bit for bit.
 */
import type { Plane, Vec3 } from "../core/vec3";
import { sinCosDeg, type SectionLine } from "./math";

/** The integer scene origin (absolute); `z` is 0 when not given (a plan point of the 7.14 views). */
export interface PlaneOrigin {
  x: number;
  y: number;
  z?: number;
}

/** One more half-space n·(p − at) ≥ 0, project coordinates (the end of a polyline segment). */
export interface EndFace {
  n: Vec3;
  at: Vec3;
}

export interface SectionPlane {
  /** O = base + offset·v̂: t = 0 passes through it (project coordinates) */
  origin: Vec3;
  /** P1 of the series, P_j of a polyline segment, the start of a line: u, w and t are measured from it */
  base: Vec3;
  /** s_k: the depth of the plane from `base` along v̂ */
  offset: number;
  s: Vec3;
  v: Vec3;
  w: Vec3;
  /** the window on the viewer's side (w_к) and behind the plane (w_от), m */
  towards: number;
  away: number;
  /** limits of u (a line of given length, a polyline segment); null — unlimited */
  u: [number, number] | null;
  /** u at `base`: U_j of a polyline segment, 0 otherwise */
  u0: number;
  /** the segment of a polyline section, null otherwise */
  seg: number | null;
  /** the ends of a polyline segment (bisectors of its bends): they bound it instead of `u` */
  ends: EndFace[];
}

/** sin and cos of degrees as the core: exact on the multiples of 90°, otherwise of d·(π/180), d in [0°; 360°). */
export function sinCosDegCore(deg: number): [number, number] {
  const d = ((deg % 360) + 360) % 360;
  if (d === 0) return [0, 1];
  if (d === 90) return [1, 0];
  if (d === 180) return [0, -1];
  if (d === 270) return [-1, 0];
  const a = d * (Math.PI / 180);
  return [Math.sin(a), Math.cos(a)];
}

/** The azimuth of (dx, dy) in [0°; 360°) as the core (`azimuth_deg`); null — no direction. */
export function azimuthCore(dx: number, dy: number): number | null {
  if (dx === 0 && dy === 0) return null;
  if (dx === 0) return dy > 0 ? 0 : 180;
  if (dy === 0) return dx > 0 ? 90 : 270;
  let a = Math.atan2(dx, dy) * (180 / Math.PI);
  if (a < 0) a += 360;
  return a >= 360 ? 0 : a;
}

export interface Axes {
  s: Vec3;
  v: Vec3;
  w: Vec3;
}

/** (ŝ, v̂, ŵ) of the azimuth `az` and the dip `phi` (degrees), no −0; `sc` — sin and cos of degrees. */
export function seriesAxes(az: number, phi = 90, sc: (deg: number) => [number, number] = sinCosDegCore): Axes {
  const [sa, ca] = sc(az);
  const [sp, cp] = sc(phi);
  return {
    s: [sa + 0, ca + 0, 0],
    v: [-ca * sp + 0, sa * sp + 0, -cp + 0],
    w: [-ca * cp + 0, sa * cp + 0, sp + 0],
  };
}

/** d·axis as the core: from 0, the terms x, y, z in order, a term with a zero axis component skipped. */
export function axisDot(d: Vec3, axis: Vec3): number {
  let t = 0;
  if (axis[0] !== 0) t += d[0] * axis[0];
  if (axis[1] !== 0) t += d[1] * axis[1];
  if (axis[2] !== 0) t += d[2] * axis[2];
  return t;
}

/** base + offset·axis, no −0. */
export function along(base: Vec3, axis: Vec3, offset: number): Vec3 {
  return [base[0] + offset * axis[0] + 0, base[1] + offset * axis[1] + 0, base[2] + offset * axis[2] + 0];
}

/** [u, w, t] of a point in the plane (the core `SectionPlane.local`). */
export function planeLocal(p: SectionPlane, xyz: Vec3): Vec3 {
  const d: Vec3 = [xyz[0] - p.base[0], xyz[1] - p.base[1], xyz[2] - p.base[2]];
  return [p.u0 + axisDot(d, p.s), axisDot(d, p.w), axisDot(d, p.v) - p.offset];
}

/** The point of the plane with these u and w (t = 0), project coordinates. */
export function planePoint(p: SectionPlane, u: number, w: number): Vec3 {
  const du = u - p.u0;
  return [p.origin[0] + du * p.s[0] + w * p.w[0], p.origin[1] + du * p.s[1] + w * p.w[1], p.origin[2] + du * p.s[2] + w * p.w[2]];
}

/**
 * A line of 7.14 as a plane: φ = 90°, through (x0, y0, 0), u from 0 to the length. Its clipping planes are those
 * of `clipPlanes` bit for bit (`windowPlanes`), the sin and cos — of `sinCosDeg` as before.
 */
export function planeFromLine(line: SectionLine): SectionPlane {
  const { s, v, w } = seriesAxes(line.az, 90, sinCosDeg);
  const base: Vec3 = [line.x0, line.y0, 0];
  return { origin: [...base], base, offset: 0, s, v, w, towards: line.towards, away: line.away, u: line.length === null ? null : [0, line.length], u0: 0, seg: null, ends: [] };
}

/** Vertical (ŵ = +Z): the plane and its window are the same at any exaggeration. */
export function isVertical(p: SectionPlane): boolean {
  return p.v[2] === 0;
}

/** Neither vertical nor a plan: the exaggeration would distort it (ADR 0016 решение 7 — ×1 while it is active). */
export function isInclined(p: SectionPlane | null): boolean {
  return !!p && p.v[2] !== 0 && Math.abs(p.v[2]) !== 1;
}

/** The exaggeration the view may use with this plane: ×1 for an inclined one. */
export function allowedVexag(p: SectionPlane | null, vexag: number): number {
  return isInclined(p) ? 1 : vexag;
}

/** The two planes have the same direction of view, bit for bit (sections of one series). */
export function parallel(a: SectionPlane, b: SectionPlane): boolean {
  return a.v[0] === b.v[0] && a.v[1] === b.v[1] && a.v[2] === b.v[2];
}

/** How far `b` is from `a` along v̂ (parallel planes): s_k′ − s_k for one base, else v̂·(O_b − O_a). */
export function depthBetween(a: SectionPlane, b: SectionPlane): number {
  const sameBase = a.base[0] === b.base[0] && a.base[1] === b.base[1] && a.base[2] === b.base[2];
  if (sameBase) return b.offset - a.offset;
  return axisDot([b.base[0] - a.base[0], b.base[1] - a.base[1], b.base[2] - a.base[2]], a.v) + (b.offset - a.offset);
}

/**
 * The half-spaces of the window, k×[nx, ny, nz, c] with n·p + c ≥ 0 inside, project coordinates — the core
 * `SectionPlane.half_spaces`: the two faces (−v̂… +v̂), then the ends of a polyline segment.
 */
export function halfSpaces(p: SectionPlane): [number, number, number, number][] {
  const vb = axisDot(p.base, p.v) + p.offset;
  const out: [number, number, number, number][] = [
    [p.v[0] + 0, p.v[1] + 0, p.v[2] + 0, p.towards - vb + 0],
    [-p.v[0] + 0, -p.v[1] + 0, -p.v[2] + 0, vb + p.away + 0],
  ];
  for (const e of p.ends) out.push([e.n[0] + 0, e.n[1] + 0, e.n[2] + 0, -axisDot(e.at, e.n) + 0]);
  return out;
}

/** The normal of the window towards the viewer (−v̂), written as `sectionNormal` of 7.14 for a vertical plane. */
function towardsViewer(v: Vec3): Vec3 {
  return [-v[0] || 0, -v[1], v[2] === 0 ? 0 : -v[2]];
}

const negate = (n: Vec3): Vec3 => [-n[0], -n[1], n[2] === 0 ? 0 : -n[2]];

/**
 * The clipping planes of the window in scene coordinates (relative to the scene origin, Z not stretched): the
 * faces t ≤ away and t ≥ −towards, then the limits of u (a line of given length) or the ends of a polyline
 * segment. A line of 7.14 gives the planes of `clipPlanes` bit for bit. A point is inside where all are ≥ 0.
 */
export function windowPlanes(p: SectionPlane, origin: PlaneOrigin): Plane[] {
  const oz = origin.z ?? 0;
  const c: Vec3 = [p.base[0] - origin.x, p.base[1] - origin.y, p.base[2] - oz];
  const n = towardsViewer(p.v);
  let nc = n[0] * c[0] + n[1] * c[1];
  if (n[2] !== 0) nc += n[2] * c[2];
  const at = nc - p.offset; // n·(O − origin)
  const planes: Plane[] = [
    { normal: n, constant: -at + p.away },
    { normal: negate(n), constant: at + p.towards },
  ];
  if (p.ends.length) {
    for (const e of p.ends) {
      const d: Vec3 = [e.at[0] - origin.x, e.at[1] - origin.y, e.at[2] - oz];
      planes.push({ normal: [...e.n], constant: -(e.n[0] * d[0] + e.n[1] * d[1] + e.n[2] * d[2]) });
    }
  } else if (p.u) {
    const s = p.s;
    let ac = s[0] * c[0] + s[1] * c[1];
    if (s[2] !== 0) ac += s[2] * c[2];
    planes.push({ normal: [s[0], s[1], s[2]], constant: -ac - (p.u[0] - p.u0) });
    planes.push({ normal: negate(s), constant: ac + (p.u[1] - p.u0) });
  }
  return planes;
}

/** A scene plane in rendered space: (n_x, n_y, n_z/e)·p + c, normalized when n_z ≠ 0 (a vertical one is kept). */
export function renderedPlane(pl: Plane, vexag: number): Plane {
  const e = vexag || 1;
  if (pl.normal[2] === 0 || e === 1) return { normal: [...pl.normal], constant: pl.constant };
  const n: Vec3 = [pl.normal[0], pl.normal[1], pl.normal[2] / e];
  const len = Math.hypot(n[0], n[1], n[2]);
  return { normal: [n[0] / len, n[1] / len, n[2] / len], constant: pl.constant / len };
}

/** Is a scene point inside every plane (NaN — not)? */
export function insidePlanes(planes: readonly Plane[], x: number, y: number, z: number): boolean {
  for (const pl of planes) {
    const n = pl.normal;
    let d = n[0] * x + n[1] * y + pl.constant;
    if (n[2] !== 0) d += n[2] * z;
    if (!(d >= 0)) return false;
  }
  return true;
}

/**
 * The camera axes of the plane in rendered space: `dir` from the target to the viewer (−v̂ with Z/e), `up` — ŵ
 * with Z·e made perpendicular to it, `right` — ŝ. Units; exact at ×1 for the vertical and plan planes.
 */
export function renderedAxes(p: SectionPlane, vexag: number): { dir: Vec3; up: Vec3; right: Vec3 } {
  const e = vexag || 1;
  const n = towardsViewer(p.v);
  if (isVertical(p)) return { dir: n, up: [0, 0, e < 0 ? -1 : 1], right: [p.s[0], p.s[1], 0] };
  const dr: Vec3 = [n[0], n[1], n[2] / e];
  const dl = Math.hypot(dr[0], dr[1], dr[2]);
  const dir: Vec3 = e === 1 ? n : [dr[0] / dl, dr[1] / dl, dr[2] / dl];
  let up: Vec3 = e === 1 ? [...p.w] : [p.w[0], p.w[1], p.w[2] * e];
  if (e !== 1) {
    const k = up[0] * dir[0] + up[1] * dir[1] + up[2] * dir[2];
    up = [up[0] - k * dir[0], up[1] - k * dir[1], up[2] - k * dir[2]];
    const ul = Math.hypot(up[0], up[1], up[2]);
    up = [up[0] / ul, up[1] / ul, up[2] / ul];
  }
  // right = up × dir (the screen x of `lookAt`)
  const right: Vec3 = [up[1] * dir[2] - up[2] * dir[1], up[2] * dir[0] - up[0] * dir[2], up[0] * dir[1] - up[1] * dir[0]];
  return { dir, up, right };
}

/** O in rendered space: minus the scene origin, Z stretched. */
export function renderedOrigin(p: SectionPlane, origin: PlaneOrigin, vexag: number): Vec3 {
  return [p.origin[0] - origin.x, p.origin[1] - origin.y, (p.origin[2] - (origin.z ?? 0)) * (vexag || 1)];
}
