/**
 * The discs of a structural trend (ТЗ 7.35 Д-15, 8.9.2; ADR 0026 решение 27) as numbers for the GPU: one disc per
 * measurement (or per face of the supporting wireframe) — a circle in the plane of the measurement, its upper unit
 * normal, centred at the point. The input is the answer of the core (`GET /trends/{id}/discs`: world float64 columns);
 * the output — the instance matrices over the unit circle (z = 1 is the normal) as float32 relative to an integer origin
 * near the centre of the points (float32 only for display, ТЗ 5.4, 6.8: `viz/origin.ts`). A disc whose point or normal
 * is not a number (or the normal is zero) is left out and counted — a gap is never a disc at 0. Pure: no three.js.
 */
import { integerOrigin, type Origin } from "../origin";

/** The columns of the discs, world coordinates float64 (the form of `GET /trends/{id}/discs`). */
export interface DiscColumns {
  x: ArrayLike<number>;
  y: ArrayLike<number>;
  z: ArrayLike<number>;
  nx: ArrayLike<number>;
  ny: ArrayLike<number>;
  nz: ArrayLike<number>;
}

export interface DiscBuild {
  origin: Origin;
  /** discs drawn */
  count: number;
  /** discs left out: a point or a normal that is not a number, a normal of zero length */
  skipped: number;
  /** `count` × 16, column-major (THREE.Matrix4.elements): u·r, v·r, n, centre − origin */
  matrices: Float32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax], the discs' extent included; null — nothing drawn */
  bbox: number[] | null;
  /** the radius of a disc, m */
  radius: number;
}

/** The smallest radius of a drawn disc, m (a size of zero would draw nothing). */
export const MIN_RADIUS = 0.01;

/** The unit vectors u, v with u × v = n (n unit), u horizontal when the plane is not horizontal: the strike of the plane. */
export function planeFrame(nx: number, ny: number, nz: number): { u: [number, number, number]; v: [number, number, number] } {
  // a helper axis away from n: z unless the plane is nearly horizontal, then x
  const ax = Math.abs(nz) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  // u = a × n (normalised), v = n × u
  let ux = ax[1] * nz - ax[2] * ny;
  let uy = ax[2] * nx - ax[0] * nz;
  let uz = ax[0] * ny - ax[1] * nx;
  const ul = Math.hypot(ux, uy, uz);
  ux /= ul;
  uy /= ul;
  uz /= ul;
  return { u: [ux, uy, uz], v: [ny * uz - nz * uy, nz * ux - nx * uz, nx * uy - ny * ux] };
}

/** The discs of the answer of the core with the radius `radius` (m): origin, matrices, extent; a count of those left out. */
export function buildDiscs(d: DiscColumns, radius: number): DiscBuild {
  const r = Number.isFinite(radius) && radius > MIN_RADIUS ? radius : MIN_RADIUS;
  const n = Math.min(d.x.length, d.y.length, d.z.length, d.nx.length, d.ny.length, d.nz.length);
  const keep: number[] = [];
  const xyz = new Float64Array(n * 3);
  for (let i = 0; i < n; i++) {
    const p = [d.x[i], d.y[i], d.z[i]];
    const len = Math.hypot(d.nx[i], d.ny[i], d.nz[i]);
    if (!p.every(Number.isFinite) || !Number.isFinite(len) || len < 1e-12) continue;
    xyz.set(p, keep.length * 3);
    keep.push(i);
  }
  const used = xyz.subarray(0, keep.length * 3);
  const origin = integerOrigin(used);
  const matrices = new Float32Array(keep.length * 16);
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  keep.forEach((i, k) => {
    const len = Math.hypot(d.nx[i], d.ny[i], d.nz[i]);
    const nx = d.nx[i] / len;
    const ny = d.ny[i] / len;
    const nz = d.nz[i] / len;
    const { u, v } = planeFrame(nx, ny, nz);
    const o = k * 16;
    matrices[o] = u[0] * r;
    matrices[o + 1] = u[1] * r;
    matrices[o + 2] = u[2] * r;
    matrices[o + 4] = v[0] * r;
    matrices[o + 5] = v[1] * r;
    matrices[o + 6] = v[2] * r;
    matrices[o + 8] = nx;
    matrices[o + 9] = ny;
    matrices[o + 10] = nz;
    matrices[o + 12] = used[k * 3] - origin.x;
    matrices[o + 13] = used[k * 3 + 1] - origin.y;
    matrices[o + 14] = used[k * 3 + 2] - origin.z;
    matrices[o + 15] = 1;
    for (let a = 0; a < 3; a++) {
      lo[a] = Math.min(lo[a], used[k * 3 + a]);
      hi[a] = Math.max(hi[a], used[k * 3 + a]);
    }
  });
  const bbox = keep.length ? [lo[0] - r, lo[1] - r, lo[2] - r, hi[0] + r, hi[1] + r, hi[2] + r] : null;
  return { origin, count: keep.length, skipped: n - keep.length, matrices, bbox, radius: r };
}
