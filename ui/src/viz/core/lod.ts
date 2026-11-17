/**
 * Level of detail of a part (ADR 0004 §3, ADR 0007 §7): levels are numbered from the finest (0) to the
 * coarsest; a measure of the part on screen picks one, with a 20 % hysteresis so that a part on a
 * boundary does not flicker between two levels. Two measures are used:
 *  - wireframes: radius of the bounding sphere on screen, px — the finest level with at most
 *    2 triangles per pixel of its disc (BF chooseLevels);
 *  - displays along holes: pixels per metre — the coarsest level whose merge step (0, 4, 32 m) is at
 *    most 2 px on screen.
 */
import type { Sphere } from "./bounds";
import { distance, type Vec3 } from "./vec3";
import { pixelsPerUnit, type CameraState, type ViewOptions } from "./views";

export const LOD_HYSTERESIS = 0.2;
export const TRIANGLES_PER_PIXEL = 2;
/** Merge steps of the levels of displays along holes, m (ADR 0007 §7). */
export const DOWNHOLE_STEPS = [0, 4, 32] as const;
/** A merged interval at most this long on screen is not seen, px. */
export const MERGE_PX = 2;

/**
 * Boundaries for the screen radius (px): level i is fine enough when its triangles are at most
 * `perPixel` per pixel of the disc, radius ≥ √(triangles / (perPixel · π)). The last level needs nothing.
 */
export function triangleBoundaries(triangles: readonly number[], perPixel = TRIANGLES_PER_PIXEL): number[] {
  return triangles.slice(0, -1).map((t) => Math.sqrt(Math.max(t, 0) / (perPixel * Math.PI)));
}

/**
 * Boundaries for pixels per metre: level i is needed while the step of level i + 1 would be longer than
 * `px` on screen, ppm ≥ px / step(i + 1).
 */
export function stepBoundaries(steps: readonly number[] = DOWNHOLE_STEPS, px = MERGE_PX): number[] {
  return steps.slice(1).map((s) => (s > 0 ? px / s : Infinity));
}

function firstAtLeast(measure: number, boundaries: readonly number[], factor: number): number {
  for (let i = 0; i < boundaries.length; i++) if (measure >= boundaries[i] * factor) return i;
  return boundaries.length;
}

/**
 * The level for `measure` (larger — finer), with hysteresis about the current level: a finer level is
 * taken only when the measure is 20 % above its boundary, a coarser one only 20 % below.
 * `boundaries` are descending, one fewer than the levels. NaN or missing measure keeps the current level.
 */
export function chooseLevel(measure: number, boundaries: readonly number[], current: number | null, hysteresis = LOD_HYSTERESIS): number {
  const coarsest = boundaries.length;
  if (Number.isNaN(measure)) return current ?? coarsest;
  if (current === null || current < 0 || current > coarsest) return firstAtLeast(measure, boundaries, 1);
  const finer = firstAtLeast(measure, boundaries, 1 + hysteresis);
  if (finer < current) return finer;
  const coarser = firstAtLeast(measure, boundaries, 1 - hysteresis);
  if (coarser > current) return coarser;
  return current;
}

/**
 * Radius of a sphere (rendered space) on screen, px. Infinity when a perspective camera is inside it:
 * then the finest level.
 */
export function screenRadius(sphere: Sphere, cam: CameraState, opts: ViewOptions, heightPx: number): number {
  if (opts.perspective && distance(cam.position, sphere.center) < sphere.radius) return Infinity;
  return sphere.radius * pixelsPerUnit(cam, sphere.center, heightPx, opts);
}

/** Pixels per metre at a point (rendered space), for the levels of displays along holes. */
export function pixelsPerMetre(at: Vec3, cam: CameraState, opts: ViewOptions, heightPx: number): number {
  return pixelsPerUnit(cam, at, heightPx, opts);
}

/**
 * What to show while the wanted level is not loaded: the wanted one, else the nearest coarser loaded,
 * else the nearest finer; null when nothing is loaded.
 */
export function shownLevel(want: number, loaded: ReadonlySet<number> | readonly number[]): number | null {
  const has = loaded instanceof Set ? (l: number) => loaded.has(l) : (l: number) => (loaded as readonly number[]).includes(l);
  const all = [...loaded];
  if (has(want)) return want;
  const coarser = all.filter((l) => l > want).sort((a, b) => a - b)[0];
  if (coarser !== undefined) return coarser;
  const finer = all.filter((l) => l < want).sort((a, b) => b - a)[0];
  return finer ?? null;
}

/** A level coarser than full is shown as «упрощено». */
export function isSimplified(level: number): boolean {
  return level > 0;
}
