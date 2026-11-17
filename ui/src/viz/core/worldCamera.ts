/**
 * The camera of a saved view (ТЗ 7.35 Д-09, ADR 0008): absolute coordinates, float64, Z not stretched — so a
 * view does not depend on the scene origin of the session nor on the exaggeration. Rendered space: relative
 * to the scene origin, Z of the target stretched by the exaggeration; the eye keeps its offset from the target
 * (as `changeVexag` does). Pure.
 */
import type { Origin } from "../origin";
import type { CameraState } from "./views";
import type { Vec3 } from "./vec3";

export interface WorldCamera {
  position: Vec3;
  target: Vec3;
  up: Vec3;
  /** half the height of the view at the target, metres (the orthographic scale) */
  zoom: number;
  ortho: boolean;
  fov: number;
}

export function toWorld(s: CameraState, o: Origin, vexag: number, ortho: boolean, fov: number): WorldCamera {
  const target: Vec3 = [s.target[0] + o.x, s.target[1] + o.y, s.target[2] / vexag + o.z];
  const position: Vec3 = [target[0] + (s.position[0] - s.target[0]), target[1] + (s.position[1] - s.target[1]), target[2] + (s.position[2] - s.target[2])];
  return { position, target, up: [s.up[0], s.up[1], s.up[2]], zoom: s.half, ortho, fov };
}

export function fromWorld(c: WorldCamera, o: Origin, vexag: number, depth: { near: number; far: number }): CameraState {
  const target: Vec3 = [c.target[0] - o.x, c.target[1] - o.y, (c.target[2] - o.z) * vexag];
  const position: Vec3 = [target[0] + (c.position[0] - c.target[0]), target[1] + (c.position[1] - c.target[1]), target[2] + (c.position[2] - c.target[2])];
  return { position, target, up: [c.up[0], c.up[1], c.up[2]], half: c.zoom, near: depth.near, far: depth.far };
}
