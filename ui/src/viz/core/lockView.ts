/**
 * «Зафиксировать вид» (L; ТЗ 8.1; ADR 0016 §4, Т8.1-6): the orthographic camera looks along v̂ of the section with
 * ŵ up the screen; turning is off (the mouse, the gizmo), panning and zooming work. A step of the series moves the
 * camera and its target by (s_k′ − s_k)·v̂, so a point with the same (u, w) stays in its place on the screen and
 * the direction does not change bit for bit; a plane of another direction (a polyline segment, another series, a
 * new exaggeration) is locked anew. Pure float64 on `CameraState` in rendered space (relative to the scene origin,
 * Z stretched); `CameraRig.setLock` holds the camera on `dir` / `up` after every move of OrbitControls.
 */
import { depthBetween, parallel, renderedAxes, renderedOrigin, type PlaneOrigin, type SectionPlane } from "../section/plane";
import { addScaled, distance, dot, sub, type Vec3 } from "./vec3";
import { lookFrom, verticalSafe, viewDirection, type CameraState, type SectionFrame, type ViewOptions } from "./views";
import type { Box3 } from "./bounds";

/** The direction (from the target to the camera) and the screen up the camera keeps, rendered space, units. */
export interface HeldView {
  dir: Vec3;
  up: Vec3;
}

export interface ViewLock extends HeldView {
  /** the plane the view is locked on */
  plane: SectionPlane;
  /** the exaggeration `dir` and `up` were computed for */
  vexag: number;
}

/** The camera at the target on `dir`, at the same distance and scale, with `up`. */
export function holdLock(cam: CameraState, held: HeldView): CameraState {
  const dist = Math.max(distance(cam.position, cam.target), 1e-6);
  return { ...cam, position: addScaled(cam.target, held.dir, dist), up: [...held.up] };
}

/**
 * Locks the view on `plane`: the target goes onto the plane t = 0 along its normal (what is in the centre of the
 * screen stays there), the scale (`half`) is kept, the camera stands on −v̂ with ŵ up.
 */
export function lockOn(cam: CameraState, plane: SectionPlane, origin: PlaneOrigin, vexag: number): { camera: CameraState; lock: ViewLock } {
  const { dir, up } = renderedAxes(plane, vexag);
  const o = renderedOrigin(plane, origin, vexag);
  const target = addScaled(cam.target, dir, -dot(dir, sub(cam.target, o)));
  const lock: ViewLock = { plane, dir, up, vexag };
  return { camera: holdLock({ ...cam, target }, lock), lock };
}

/**
 * The locked view follows the section `next`: a parallel plane (a step of the series) moves the camera and the
 * target by the depth between the planes along v̂ (rendered: Z stretched); otherwise it is locked anew.
 */
export function followSection(cam: CameraState, lock: ViewLock, next: SectionPlane, origin: PlaneOrigin, vexag: number): { camera: CameraState; lock: ViewLock } {
  if (!parallel(lock.plane, next) || lock.vexag !== vexag) return lockOn(cam, next, origin, vexag);
  const d = depthBetween(lock.plane, next);
  const e = vexag || 1;
  const delta: Vec3 = [d * next.v[0], d * next.v[1], d * next.v[2] * e];
  const move = (p: Vec3): Vec3 => [p[0] + delta[0], p[1] + delta[1], p[2] + delta[2]];
  return { camera: { ...cam, position: move(cam.position), target: move(cam.target) }, lock: { ...lock, plane: next } };
}

/** «Вписать» and the «section» view under the lock: the content held along the locked direction, centred on the plane. */
export function lockedView(lock: ViewLock, box: Box3, opts: ViewOptions, frame: SectionFrame | null): CameraState {
  return holdLock(lookFrom(lock.dir, box, opts, frame, lock.up), lock);
}

/** The camera free again: Z up the screen, a hair off the vertical when it looks straight down or up. */
export function freeCamera(cam: CameraState): CameraState {
  const dir = viewDirection(cam);
  const safe = Math.hypot(dir[0], dir[1]) < 1e-3 ? verticalSafe(dir) : dir;
  return { ...cam, position: addScaled(cam.target, safe, Math.max(distance(cam.position, cam.target), 1e-6)), up: [0, 0, 1] };
}
