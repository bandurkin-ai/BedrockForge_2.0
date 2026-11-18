/**
 * Where a raster stands in the scene (ТЗ 7.19, ADR 0011 §4): the four corners of the image in float64 —
 * top left, top right, bottom right, bottom left (`rasters.georef.corners3d` of the core) — and their
 * float32 copy from an integer origin for the video card (ТЗ 5.4: never UTM in a buffer). A raster in plan
 * lies at its elevation; without one — under the lowest object, never at 0. A vertical raster keeps the
 * heights of its corners; under clipping it is a backdrop. Also here: the copy of the image to show
 * (≤ 4096 or ≤ 1024 px) by the size on the screen, and the click on a raster. Pure, no three.js.
 */
import { chooseLevel } from "../core/lod";
import type { Vec3 } from "../core/vec3";
import { integerOrigin, toRelativeFloat32, type Origin } from "../origin";
import type { Ray } from "../section/math";

/** A raster without an elevation lies this far under the lowest object, m (BF `placeRaster`). */
export const UNDER_M = 1;

export interface RasterGeoref {
  /** 4 × [x, y, z]: top left, top right, bottom right, bottom left; z null — no height */
  corners3d: readonly (readonly (number | null)[])[] | null | undefined;
  /** the level of a plan raster; null — under the lowest object */
  elevation: number | null;
  vertical: boolean;
}

export interface RasterPlacement {
  /** absolute float64 corners, 4 × 3 */
  corners: Float64Array;
  origin: Origin;
  /** float32 corners from `origin` */
  local: Float32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] */
  bbox: Float64Array;
  /** the height came from the floor: the raster moves when the lowest object changes */
  floating: boolean;
}

/** `floor` — the level for a raster without a height (`floorLevel`). Null — the raster has no georeference. */
export function rasterPlacement(g: RasterGeoref, floor: number): RasterPlacement | null {
  const c = g.corners3d;
  if (!Array.isArray(c) || c.length !== 4) return null;
  const corners = new Float64Array(12);
  let floating = false;
  for (let i = 0; i < 4; i++) {
    const [x, y, z] = c[i] ?? [];
    if (typeof x !== "number" || typeof y !== "number" || !Number.isFinite(x) || !Number.isFinite(y)) return null;
    let level = typeof z === "number" && Number.isFinite(z) ? z : g.elevation;
    if (typeof level !== "number" || !Number.isFinite(level)) {
      level = floor;
      floating = true;
    }
    corners.set([x, y, level], i * 3);
  }
  const origin = integerOrigin(corners);
  const bbox = Float64Array.of(Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity);
  for (let i = 0; i < 12; i++) {
    const k = i % 3;
    bbox[k] = Math.min(bbox[k], corners[i]);
    bbox[3 + k] = Math.max(bbox[3 + k], corners[i]);
  }
  return { corners, origin, local: toRelativeFloat32(corners, origin), bbox, floating };
}

/**
 * The level of what has no height of its own (a raster without an elevation, lines without Z): a metre under
 * the lowest visible object; hidden objects count only when nothing is visible; 0 in an empty project.
 */
export function floorLevel(items: Iterable<{ zmin: number; visible: boolean }>): number {
  let shown = Infinity;
  let all = Infinity;
  for (const it of items) {
    if (!Number.isFinite(it.zmin)) continue;
    all = Math.min(all, it.zmin);
    if (it.visible) shown = Math.min(shown, it.zmin);
  }
  const z = Number.isFinite(shown) ? shown : all;
  return Number.isFinite(z) ? z - UNDER_M : 0;
}

/** Under clipping a vertical raster is a backdrop: drawn first, out of the clipping planes, without depth. */
export function isBackdrop(vertical: boolean, clipping: boolean): boolean {
  return vertical && clipping;
}

/**
 * The copy to show: `sizes` — the longer side of copy 0 (≤ 4096 px) and copy 1 (≤ 1024 px). The small copy
 * is enough until it is stretched over more screen pixels than it has; 20 % hysteresis about the current.
 */
export function textureLevel(screenPx: number, sizes: readonly number[], current: number | null): number {
  if (sizes.length < 2 || !(sizes[0] > sizes[1])) return sizes.length - 1;
  return chooseLevel(screenPx, [sizes[1]], current);
}

/** The longer edge of the raster on the screen, px: `ppu(at)` — pixels per unit at a rendered-space point. */
export function screenExtent(local: ArrayLike<number>, offset: Vec3, vexag: number, ppu: (at: Vec3) => number): number {
  const p = (i: number): Vec3 => [local[i * 3] + offset[0], local[i * 3 + 1] + offset[1], (local[i * 3 + 2] + offset[2]) * vexag];
  const c = [p(0), p(1), p(2), p(3)];
  const len = (a: Vec3, b: Vec3) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const centre: Vec3 = [(c[0][0] + c[2][0]) / 2, (c[0][1] + c[2][1]) / 2, (c[0][2] + c[2][2]) / 2];
  let scale = ppu(centre);
  for (const q of c) scale = Math.max(scale, ppu(q)); // a near corner of a large raster in perspective
  return Math.max(len(c[0], c[1]), len(c[0], c[3])) * (Number.isFinite(scale) ? scale : 0);
}

function hitTriangle(o: Vec3, d: Vec3, a: Vec3, b: Vec3, c: Vec3): number | null {
  const e1: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const e2: Vec3 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const p: Vec3 = [d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0]];
  const det = e1[0] * p[0] + e1[1] * p[1] + e1[2] * p[2];
  if (Math.abs(det) < 1e-12) return null;
  const s: Vec3 = [o[0] - a[0], o[1] - a[1], o[2] - a[2]];
  const u = (s[0] * p[0] + s[1] * p[1] + s[2] * p[2]) / det;
  if (u < 0 || u > 1) return null;
  const q: Vec3 = [s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0]];
  const w = (d[0] * q[0] + d[1] * q[1] + d[2] * q[2]) / det;
  if (w < 0 || u + w > 1) return null;
  const t = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) / det;
  return t > 0 ? t : null;
}

/**
 * A ray of the screen against the quad of a raster (rendered space: `local` + `offset`, Z stretched): the
 * distance along the ray and the place, or null. Float64.
 */
export function pickQuad(local: ArrayLike<number>, offset: Vec3, vexag: number, ray: Ray): { t: number; point: Vec3 } | null {
  const p = (i: number): Vec3 => [local[i * 3] + offset[0], local[i * 3 + 1] + offset[1], (local[i * 3 + 2] + offset[2]) * vexag];
  const [a, b, c, d] = [p(0), p(1), p(2), p(3)];
  const t = hitTriangle(ray.origin, ray.dir, a, b, c) ?? hitTriangle(ray.origin, ray.dir, a, c, d);
  if (t === null) return null;
  return { t, point: [ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t, ray.origin[2] + ray.dir[2] * t] };
}
