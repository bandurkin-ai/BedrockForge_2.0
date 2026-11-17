/**
 * float64 3-vectors as plain arrays: the camera, section and culling math of the 3D window without
 * three.js (ADR 0004 §1, ADR 0007 §4). Every function returns a new array.
 */

export type Vec3 = [number, number, number];

/**
 * A plane compatible with `THREE.Plane`: signed distance = normal · p + constant; clipping keeps
 * where it is ≥ 0, a frustum plane has its normal pointing inside.
 */
export interface Plane {
  normal: Vec3;
  constant: number;
}

export function add(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

export function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

export function scale(a: Vec3, k: number): Vec3 {
  return [a[0] * k, a[1] * k, a[2] * k];
}

/** a + b·k */
export function addScaled(a: Vec3, b: Vec3, k: number): Vec3 {
  return [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
}

export function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

export function length(a: Vec3): number {
  return Math.hypot(a[0], a[1], a[2]);
}

export function distance(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/** Unit vector; a zero vector stays zero (as `THREE.Vector3.normalize`). */
export function normalize(a: Vec3): Vec3 {
  const l = length(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/** Angle between two vectors, radians (robust near 0 and π). */
export function angle(a: Vec3, b: Vec3): number {
  return Math.atan2(length(cross(a, b)), dot(a, b));
}

export function planeDistance(p: Plane, x: Vec3): number {
  return dot(p.normal, x) + p.constant;
}
