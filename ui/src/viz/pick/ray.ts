/**
 * Matrices for picking on the CPU (ADR 0007 §7): the view and projection of the camera as three.js builds
 * them (`Matrix4.lookAt`, `makeOrthographic`, `makePerspective`; column-major), in float64, and the model of
 * a part — its offset from the scene origin with Z stretched by the vertical exaggeration. No three.js.
 */
import { cameraBasis, type CameraState, type ViewOptions } from "../core/views";
import { dot, type Vec3 } from "../core/vec3";

/** 4×4 column-major, as `THREE.Matrix4.elements`: element (row r, column c) is at c·4 + r. */
export type Mat4 = Float64Array;

export function multiply(a: ArrayLike<number>, b: ArrayLike<number>): Mat4 {
  const out = new Float64Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return out;
}

/** The inverse of the camera's world matrix (world → view). */
export function viewMatrix(cam: CameraState): Mat4 {
  const { x, y, z } = cameraBasis(cam);
  const e = cam.position;
  return Float64Array.of(x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1);
}

export function projectionMatrix(cam: CameraState, opts: ViewOptions): Mat4 {
  const near = cam.near;
  const far = cam.far;
  const aspect = opts.aspect > 0 ? opts.aspect : 1;
  if (opts.perspective) {
    const top = near * Math.tan(((opts.fov / 2) * Math.PI) / 180);
    const x = (2 * near) / (2 * top * aspect);
    const y = (2 * near) / (2 * top);
    const c = -(far + near) / (far - near);
    const d = (-2 * far * near) / (far - near);
    return Float64Array.of(x, 0, 0, 0, 0, y, 0, 0, 0, 0, c, -1, 0, 0, d, 0);
  }
  const w = 1 / (2 * cam.half * aspect);
  const h = 1 / (2 * cam.half);
  const p = 1 / (far - near);
  return Float64Array.of(2 * w, 0, 0, 0, 0, 2 * h, 0, 0, 0, 0, -2 * p, 0, 0, 0, -(far + near) * p, 1);
}

/** projection · view: world (rendered space) → clip coordinates. */
export function viewProjection(cam: CameraState, opts: ViewOptions): Mat4 {
  return multiply(projectionMatrix(cam, opts), viewMatrix(cam));
}

/** The model of a part: its float32 points plus `offset` (scene units), then Z × vexag (the scaled root). */
export function partModel(offset: Vec3, vexag: number): Mat4 {
  return Float64Array.of(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, vexag, 0, offset[0], offset[1], offset[2] * vexag, 1);
}

/**
 * Screen position (CSS px from the top left of a w × h window) and NDC depth of a point under `m`
 * (clip ← local); null behind the camera or outside near / far.
 */
export function projectToScreen(m: ArrayLike<number>, x: number, y: number, z: number, w: number, h: number): [number, number, number] | null {
  const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
  if (!(cw > 0)) return null;
  const nz = (m[2] * x + m[6] * y + m[10] * z + m[14]) / cw;
  if (nz < -1 || nz > 1) return null;
  const nx = (m[0] * x + m[4] * y + m[8] * z + m[12]) / cw;
  const ny = (m[1] * x + m[5] * y + m[9] * z + m[13]) / cw;
  return [(nx * 0.5 + 0.5) * w, (-ny * 0.5 + 0.5) * h, nz];
}

/** CSS px inside a w × h window → normalised device coordinates (−1…1, Y up). */
export function toNdc(px: number, py: number, w: number, h: number): [number, number] {
  return [(px / Math.max(w, 1)) * 2 - 1, -(py / Math.max(h, 1)) * 2 + 1];
}
