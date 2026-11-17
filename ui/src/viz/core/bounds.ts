/**
 * Extents of the 3D scene (BF `scene3d.ts` 2245–2290): boxes in scene coordinates (float64, relative to
 * the scene origin, Z not stretched), the content to fit, and the test "does a box reach into the window
 * of the section" so that «Вписать» with clipping counts only the objects in the window (17.4.10).
 */
import { sectionNormal, type SectionLine } from "../section/math";
import type { Vec3 } from "./vec3";

export interface Box3 {
  min: Vec3;
  max: Vec3;
}

export function emptyBox(): Box3 {
  return { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
}

export function isEmpty(b: Box3): boolean {
  return !(b.max[0] >= b.min[0] && b.max[1] >= b.min[1] && b.max[2] >= b.min[2]);
}

export function union(a: Box3, b: Box3): Box3 {
  return {
    min: [Math.min(a.min[0], b.min[0]), Math.min(a.min[1], b.min[1]), Math.min(a.min[2], b.min[2])],
    max: [Math.max(a.max[0], b.max[0]), Math.max(a.max[1], b.max[1]), Math.max(a.max[2], b.max[2])],
  };
}

export function expandByPoint(b: Box3, p: Vec3): Box3 {
  return union(b, { min: p, max: p });
}

export function boxCenter(b: Box3): Vec3 {
  return [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
}

export function boxSize(b: Box3): Vec3 {
  return [b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]];
}

export interface Sphere {
  center: Vec3;
  radius: number;
}

/** The sphere around a box (as `THREE.Box3.getBoundingSphere`). */
export function boundingSphere(b: Box3): Sphere {
  const s = boxSize(b);
  return { center: boxCenter(b), radius: Math.hypot(s[0], s[1], s[2]) / 2 };
}

/**
 * A part's `bbox` of the core — [xmin, ymin, zmin, xmax, ymax, zmax], absolute float64 — in scene
 * coordinates: minus the scene origin, in float64.
 */
export function boxFromBbox6(bbox: ArrayLike<number>, origin: { x: number; y: number; z: number } = { x: 0, y: 0, z: 0 }): Box3 {
  return {
    min: [bbox[0] - origin.x, bbox[1] - origin.y, bbox[2] - origin.z],
    max: [bbox[3] - origin.x, bbox[4] - origin.y, bbox[5] - origin.z],
  };
}

/** The box as rendered: Z stretched by the vertical exaggeration (a negative one flips it). */
export function scaleZ(b: Box3, vexag: number): Box3 {
  const z0 = b.min[2] * vexag;
  const z1 = b.max[2] * vexag;
  return { min: [b.min[0], b.min[1], Math.min(z0, z1)], max: [b.max[0], b.max[1], Math.max(z0, z1)] };
}

export type WindowTest = (box: Box3) => boolean;

/**
 * "Does this box reach into the clipping window" (BF windowTest), or null when nothing is clipped.
 * Boxes are in scene coordinates; `origin` is the scene origin (absolute).
 */
export function windowTest(line: SectionLine | null, clip: boolean, origin: { x: number; y: number } | null): WindowTest | null {
  if (!clip || !line || !origin) return null;
  const n = sectionNormal(line.az);
  const offset = n[0] * (line.x0 - origin.x) + n[1] * (line.y0 - origin.y);
  return (box) => {
    let min = Infinity;
    let max = -Infinity;
    for (const x of [box.min[0], box.max[0]]) {
      for (const y of [box.min[1], box.max[1]]) {
        const d = n[0] * x + n[1] * y - offset;
        min = Math.min(min, d);
        max = Math.max(max, d);
      }
    }
    return max >= -line.away && min <= line.towards;
  };
}

export interface Extent {
  box: Box3;
  visible: boolean;
}

/**
 * Everything visible (or everything, when nothing is visible); with a window test, objects entirely
 * outside the window do not count. Null when there is nothing.
 */
export function contentBox(items: Iterable<Extent>, inWindow: WindowTest | null = null): Box3 | null {
  const list = [...items].filter((e) => !isEmpty(e.box));
  const collect = (visibleOnly: boolean): Box3 => {
    let box = emptyBox();
    for (const e of list) {
      if (visibleOnly && !e.visible) continue;
      if (inWindow && !inWindow(e.box)) continue;
      box = union(box, e.box);
    }
    return box;
  };
  let box = collect(true);
  if (isEmpty(box)) box = collect(false);
  return isEmpty(box) ? null : box;
}

/** A box of 100 m around the origin: the view of an empty scene. */
export const DEFAULT_BOX: Box3 = { min: [-50, -50, -50], max: [50, 50, 50] };

/** What «Вписать» holds: the content in the window, else all content, else the default box (BF lookFrom). */
export function fitBox(items: Iterable<Extent>, inWindow: WindowTest | null): Box3 {
  const list = [...items];
  return (inWindow ? contentBox(list, inWindow) : null) ?? contentBox(list) ?? DEFAULT_BOX;
}
