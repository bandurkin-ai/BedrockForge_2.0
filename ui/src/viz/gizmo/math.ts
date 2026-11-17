/**
 * The axes gizmo in the lower left corner of the 3D view (ТЗ 7.13; BF `scene3d.ts` AxesGizmo 2897–2987):
 * X, Y, Z projected on the screen in an SVG of viewBox −46 −46 92 92 with radius 33, drawn far to near
 * (axes away from the viewer, view z < −0.2, at opacity 0.45). A click on an axis head (r = 8) turns the
 * view to look along the axis from its positive side, on the small circle opposite (r = 4, at −0.8 of
 * the radius) — from its negative side. With a flipped Z the Z axis is drawn and turned the other way.
 */
import { normalize, type Vec3 } from "../core/vec3";
import { cameraBasis, type CameraState } from "../core/views";

export const GIZMO = {
  /** CSS px, also the viewBox size */
  size: 92,
  left: 10,
  bottom: 26,
  ring: 44,
  radius: 33,
  head: 8,
  back: 4,
  /** the negative circle sits at −0.8 of the axis end */
  backAt: 0.8,
  /** view z below which an axis points away from the viewer */
  awayZ: -0.2,
  awayOpacity: 0.45,
} as const;

export type AxisKey = "x" | "y" | "z";

export const AXIS_COLORS: Readonly<Record<AxisKey, string>> = { x: "#ef4f5f", y: "#3fbf6f", z: "#4c8dff" };

const AXES: readonly [AxisKey, Vec3][] = [
  ["x", [1, 0, 0]],
  ["y", [0, 1, 0]],
  ["z", [0, 0, 1]],
];

export interface GizmoAxis {
  key: AxisKey;
  /** end of the axis and its head, viewBox units (Y down) */
  x: number;
  y: number;
  /** the negative circle */
  backX: number;
  backY: number;
  /** view-space z of the axis: + towards the viewer */
  depth: number;
  away: boolean;
  opacity: number;
}

/** The axes for the current camera, in drawing order: the farthest first, the nearest last (on top). */
export function gizmoAxes(cam: CameraState, flipZ = false): GizmoAxis[] {
  const b = cameraBasis(cam);
  const out = AXES.map(([key, d]): GizmoAxis => {
    const dir: Vec3 = flipZ && key === "z" ? [-d[0], -d[1], -d[2]] : d;
    // transformDirection by the inverse camera matrix: the axis in view space.
    const v = normalize([dir[0] * b.x[0] + dir[1] * b.x[1] + dir[2] * b.x[2], dir[0] * b.y[0] + dir[1] * b.y[1] + dir[2] * b.y[2], dir[0] * b.z[0] + dir[1] * b.z[1] + dir[2] * b.z[2]]);
    const x = v[0] * GIZMO.radius;
    const y = -v[1] * GIZMO.radius;
    const away = v[2] < GIZMO.awayZ;
    return { key, x, y, backX: -x * GIZMO.backAt, backY: -y * GIZMO.backAt, depth: v[2], away, opacity: away ? GIZMO.awayOpacity : 1 };
  });
  return out.sort((a, b) => a.depth - b.depth);
}

/** CSS px inside the gizmo box (w × h) → viewBox coordinates. */
export function toViewBox(px: number, py: number, w: number = GIZMO.size, h: number = GIZMO.size): [number, number] {
  return [(px / w) * GIZMO.size - GIZMO.size / 2, (py / h) * GIZMO.size - GIZMO.size / 2];
}

export interface GizmoHit {
  key: AxisKey;
  /** +1 — the head (positive side), −1 — the circle opposite */
  sign: 1 | -1;
}

/**
 * What a click at (vx, vy) in viewBox units hits: the topmost element as SVG would find it — axes from
 * the nearest, within an axis the head above its negative circle. Null between them.
 */
export function gizmoHit(axes: readonly GizmoAxis[], vx: number, vy: number): GizmoHit | null {
  for (let i = axes.length - 1; i >= 0; i--) {
    const a = axes[i];
    if (Math.hypot(vx - a.x, vy - a.y) <= GIZMO.head) return { key: a.key, sign: 1 };
    if (Math.hypot(vx - a.backX, vy - a.backY) <= GIZMO.back) return { key: a.key, sign: -1 };
  }
  return null;
}

/** The direction to look from (rendered space) for a hit: the axis times its sign, Z reversed when flipped. */
export function gizmoDirection(hit: GizmoHit, flipZ = false): Vec3 {
  const d = AXES.find(([k]) => k === hit.key)![1];
  const k = hit.sign * (flipZ && hit.key === "z" ? -1 : 1);
  return [d[0] * k, d[1] * k, d[2] * k];
}

/** Tooltips of the head and of the circle opposite. */
export function gizmoTitles(key: AxisKey): { head: string; back: string } {
  const axis = key.toUpperCase();
  return { head: `Вид вдоль оси ${axis} (с её положительной стороны)`, back: `Вид с отрицательной стороны оси ${axis}` };
}
