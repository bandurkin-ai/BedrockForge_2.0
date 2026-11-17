/**
 * Smooth turn of the view (BF `scene3d.ts` turnTo 2078–2115; ТЗ 7.13 gizmo): the view direction turns
 * by a quaternion slerp over 260 ms with an ease-out cubic, about the view target, keeping the centre and
 * the scale. A new turn interrupts the one under way (a token). Time is a parameter (ms), no globals.
 */
import { angle, distance, type Vec3 } from "./vec3";
import { placeOn, roomBehind, verticalSafe, viewDirection, type CameraState } from "./views";
import type { Box3 } from "./bounds";

export const TURN_MS = 260;

export type Quat = [number, number, number, number]; // x, y, z, w

export function easeOutCubic(k: number): number {
  return 1 - (1 - k) ** 3;
}

/** The rotation taking unit vector `a` to unit vector `b` (as `THREE.Quaternion.setFromUnitVectors`). */
export function quatFromUnitVectors(a: Vec3, b: Vec3): Quat {
  let r = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + 1;
  let q: Quat;
  if (r < Number.EPSILON) {
    // Opposite vectors: half a turn about any axis across `a`.
    r = 0;
    q = Math.abs(a[0]) > Math.abs(a[2]) ? [-a[1], a[0], 0, r] : [0, -a[2], a[1], r];
  } else {
    q = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0], r];
  }
  const l = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / l, q[1] / l, q[2] / l, q[3] / l];
}

/** Slerp from the identity to `q` (as `THREE.Quaternion.identity().slerp(q, t)`). */
export function slerpFromIdentity(q: Quat, t: number): Quat {
  if (t <= 0) return [0, 0, 0, 1];
  if (t >= 1) return [...q];
  let [x, y, z, w] = q;
  let cosHalf = w;
  if (cosHalf < 0) {
    [x, y, z, w] = [-x, -y, -z, -w];
    cosHalf = -cosHalf;
  }
  if (cosHalf >= 1) return [0, 0, 0, 1];
  const sqrSin = 1 - cosHalf * cosHalf;
  if (sqrSin <= Number.EPSILON) {
    const s = 1 - t;
    const out: Quat = [t * x, t * y, t * z, s + t * w];
    const l = Math.hypot(...out) || 1;
    return [out[0] / l, out[1] / l, out[2] / l, out[3] / l];
  }
  const sinHalf = Math.sqrt(sqrSin);
  const halfTheta = Math.atan2(sinHalf, cosHalf);
  const ra = Math.sin((1 - t) * halfTheta) / sinHalf;
  const rb = Math.sin(t * halfTheta) / sinHalf;
  return [x * rb, y * rb, z * rb, ra + w * rb];
}

/** `v` turned by the unit quaternion `q` (as `THREE.Vector3.applyQuaternion`). */
export function rotate(q: Quat, v: Vec3): Vec3 {
  const [qx, qy, qz, qw] = q;
  const tx = 2 * (qy * v[2] - qz * v[1]);
  const ty = 2 * (qz * v[0] - qx * v[2]);
  const tz = 2 * (qx * v[1] - qy * v[0]);
  return [v[0] + qw * tx + qy * tz - qz * ty, v[1] + qw * ty + qz * tx - qx * tz, v[2] + qw * tz + qx * ty - qy * tx];
}

interface Plan {
  token: number;
  camera: CameraState;
  start: Vec3;
  end: Vec3;
  turn: Quat;
  t0: number;
}

export interface TurnOptions {
  /** orthographic camera: far is widened so nothing is cut while turning */
  ortho?: boolean;
  /** the content (scene coordinates) and the exaggeration, for that room */
  box?: Box3 | null;
  vexag?: number;
}

export class TurnController {
  private token = 0;
  private plan: Plan | null = null;

  get active(): boolean {
    return this.plan !== null;
  }

  /**
   * Starts a turn to look along `from` (rendered space; from its end toward the view centre) at time
   * `now`. Gives the token of the turn, or null when the view already looks that way (the turn under
   * way, if any, goes on).
   */
  begin(camera: CameraState, from: Vec3, now: number, opts: TurnOptions = {}): number | null {
    const end = verticalSafe(from);
    const start = viewDirection(camera);
    if (angle(start, end) < 1e-3) return null;
    const cam: CameraState = { ...camera, target: [...camera.target] };
    if (opts.ortho) {
      const dist = Math.max(distance(camera.position, camera.target), 1);
      cam.near = 0.1;
      cam.far = Math.max(camera.far, dist * 2 + roomBehind(opts.box ?? null, opts.vexag ?? 1) * 2);
    }
    this.token += 1;
    this.plan = { token: this.token, camera: cam, start, end, turn: quatFromUnitVectors(start, end), t0: now };
    return this.token;
  }

  /**
   * The camera of turn `token` at time `now`; `done` on its last frame. Null when that turn was
   * interrupted by a newer one or cancelled: its frame callback stops.
   */
  frame(token: number, now: number): { camera: CameraState; done: boolean } | null {
    const p = this.plan;
    if (!p || p.token !== token) return null;
    const k = Math.min(Math.max((now - p.t0) / TURN_MS, 0), 1);
    const dir = k === 1 ? p.end : rotate(slerpFromIdentity(p.turn, easeOutCubic(k)), p.start);
    const done = k === 1;
    if (done) this.plan = null;
    return { camera: placeOn(p.camera, dir), done };
  }

  /** Stops the turn under way (a drag of the mouse takes the view). */
  cancel(): void {
    this.token += 1;
    this.plan = null;
  }
}
