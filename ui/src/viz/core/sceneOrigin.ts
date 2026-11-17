/**
 * The scene origin (ADR 0004 §2): an integer point near the camera target. Every part has its own
 * integer origin (the core puts it at the centre of the part's extent, its points are float32 from it);
 * the part is placed at `offset = part origin − scene origin`, computed in float64 on the CPU, so UTM
 * coordinates never reach the buffers or the matrices. When the target goes farther than 4 km from the
 * origin, the origin moves to it and the camera and offsets follow.
 *
 * Scene coordinates are relative to the scene origin with Z not stretched; rendered space stretches Z by
 * the vertical exaggeration (the root of the scene is scaled, as in BF).
 */
import { integerOrigin, type Origin } from "../origin";
import type { CameraState } from "./views";
import type { Vec3 } from "./vec3";

export const RECENTER_DISTANCE = 4000;

export class SceneOrigin {
  private o: Origin | null = null;

  /** null until the first object */
  get value(): Origin | null {
    return this.o;
  }

  /** The first object fixes the origin: the integer point at the centre of its extent (bbox6, absolute). */
  ensure(bbox: ArrayLike<number>): Origin {
    if (!this.o) this.o = integerOrigin(Float64Array.from({ length: 6 }, (_, i) => bbox[i]));
    return this.o;
  }

  /** Offset of an object whose origin is `part` (absolute, integer): its position in the scene, float64. */
  offset(part: Origin): Vec3 {
    const o = this.require();
    return [part.x - o.x, part.y - o.y, part.z - o.z];
  }

  toScene(abs: Vec3): Vec3 {
    const o = this.require();
    return [abs[0] - o.x, abs[1] - o.y, abs[2] - o.z];
  }

  toAbsolute(scene: Vec3): Vec3 {
    const o = this.require();
    return [scene[0] + o.x, scene[1] + o.y, scene[2] + o.z];
  }

  /** Absolute coordinates of a rendered-space point (Z divided back by the exaggeration). */
  renderedToAbsolute(p: Vec3, vexag: number): Vec3 {
    return this.toAbsolute([p[0], p[1], vexag ? p[2] / vexag : p[2]]);
  }

  /**
   * Follows the camera target (rendered space): farther than 4 km from the origin → the origin moves to
   * the integer point at the target. Gives the shift (new − old, integers, scene units) or null.
   * After a shift: `rebaseCamera`, and every part offset and the clipping planes are recomputed.
   */
  follow(target: Vec3, vexag: number): Vec3 | null {
    if (!this.o) return null;
    const abs = this.renderedToAbsolute(target, vexag);
    const o = this.o;
    if (Math.hypot(abs[0] - o.x, abs[1] - o.y, abs[2] - o.z) <= RECENTER_DISTANCE) return null;
    const next = integerOrigin(Float64Array.of(...abs));
    const delta: Vec3 = [next.x - o.x, next.y - o.y, next.z - o.z];
    this.o = next;
    return delta;
  }

  /** Forgets the origin (the scene emptied). */
  reset(): void {
    this.o = null;
  }

  private require(): Origin {
    if (!this.o) throw new Error("scene origin is not set: ensure() it with the first object");
    return this.o;
  }
}

/** The camera after the origin moved by `delta` (scene units): the same place in the project. */
export function rebaseCamera(cam: CameraState, delta: Vec3, vexag: number): CameraState {
  const d: Vec3 = [delta[0], delta[1], delta[2] * vexag];
  return {
    ...cam,
    position: [cam.position[0] - d[0], cam.position[1] - d[1], cam.position[2] - d[2]],
    target: [cam.target[0] - d[0], cam.target[1] - d[1], cam.target[2] - d[2]],
  };
}
