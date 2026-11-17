/**
 * Camera math of the 3D window on float64 (ТЗ 7.13; BF `scene3d.ts` 2016–2205, `fitDepth` 2761):
 * standard views, «В план», «Вписать», the size of the orthographic view, near / far, projection and
 * rays. No three.js: input and output are plain `CameraState` structures in rendered space — relative
 * to the scene origin, Z stretched by the vertical exaggeration.
 */
import { VizError } from "../error";
import { sectionDirection, sectionNormal, type SectionLine } from "../section/math";
import { boundingSphere, boxCenter, boxSize, scaleZ, type Box3 } from "./bounds";
import { add, addScaled, cross, distance, dot, length, normalize, scale, sub, type Vec3 } from "./vec3";

export interface CameraState {
  position: Vec3;
  target: Vec3;
  up: Vec3;
  /**
   * Half the height of the view at the target, scene units: the orthographic `top` at zoom 1, for a
   * perspective camera distance × tan(fov / 2). It is the scale of the view.
   */
  half: number;
  near: number;
  far: number;
}

export interface ViewOptions {
  /** width / height of the window */
  aspect: number;
  perspective: boolean;
  /** vertical field of view of the perspective camera, degrees */
  fov: number;
  vexag: number;
}

export const DEFAULT_FOV = 45;
export type ViewPreset = "top" | "front" | "side" | "iso" | "section";
export const ISO_DIRECTION: Vec3 = [0.9, -1.2, 0.8];
/** Straight up or down the camera stands a hair off the vertical, so the screen keeps Y up. */
export const VERTICAL_NUDGE = 1e-4;
const Z: Vec3 = [0, 0, 1];

/** The section and the scene origin (absolute), to centre a view on the section plane. */
export interface SectionFrame {
  line: SectionLine;
  origin: { x: number; y: number };
}

const tanHalf = (fov: number) => Math.tan(((fov / 2) * Math.PI) / 180);

/** Direction from the target to the camera of a standard view (unit vector). */
export function presetDirection(preset: ViewPreset, line: SectionLine | null = null): Vec3 {
  switch (preset) {
    case "top": // Y up the screen, X to the right
      return normalize([0, -VERTICAL_NUDGE, 1]);
    case "front": // looking north: Z up, X to the right
      return [0, -1, 0];
    case "side": // looking west: Z up, Y to the right
      return [1, 0, 0];
    case "section": // from the viewer's side of the line: Z up, the line runs to the right
      if (!line) throw new VizError("no_section_line", "Линия разреза не задана: сначала section.set", "Проведите линию (S) или задайте её командой section.set.");
      return sectionNormal(line.az);
    default:
      return normalize(ISO_DIRECTION);
  }
}

/**
 * A direction to turn to (gizmo, BF turnTo): straight up or down becomes a hair off the vertical, so
 * the screen keeps Y up from above and from below.
 */
export function verticalSafe(from: Vec3): Vec3 {
  const end = normalize(from);
  if (Math.abs(end[2]) > 0.999) return normalize([0, end[2] > 0 ? -VERTICAL_NUDGE : VERTICAL_NUDGE, Math.sign(end[2])]);
  return end;
}

/** Unit vector from the target to the camera. */
export function viewDirection(cam: CameraState): Vec3 {
  const d = sub(cam.position, cam.target);
  return length(d) > 0 ? normalize(d) : normalize(ISO_DIRECTION);
}

export interface Basis {
  /** screen right */
  x: Vec3;
  /** screen up */
  y: Vec3;
  /** towards the viewer (the camera looks along −z) */
  z: Vec3;
}

/** The camera axes as `THREE.Matrix4.lookAt` builds them from position, target and up. */
export function cameraBasis(cam: CameraState): Basis {
  let z = sub(cam.position, cam.target);
  if (length(z) === 0) z = [0, 0, 1];
  z = normalize(z);
  let x = cross(cam.up, z);
  if (length(x) === 0) {
    z = Math.abs(cam.up[2]) === 1 ? normalize([z[0] + 1e-4, z[1], z[2]]) : normalize([z[0], z[1], z[2] + 1e-4]);
    x = cross(cam.up, z);
  }
  x = normalize(x);
  return { x, y: cross(z, x), z };
}

/**
 * Places the camera on `direction` from the content and sizes the view to hold it (BF lookFrom).
 * `box` is in scene coordinates (Z not stretched). With `section` the view is centred on the section plane
 * and a line of given length is held from end to end with its window.
 */
export function lookFrom(direction: Vec3, box: Box3, opts: ViewOptions, section: SectionFrame | null = null): CameraState {
  const b = scaleZ(box, opts.vexag);
  const s = section?.line ?? null;
  // The points to hold: the corners of the box, or — for a line of given length — the corners of its
  // window from the top to the bottom of the content (the line from end to end, not its bounding box).
  let points: Vec3[] = [];
  for (let i = 0; i < 8; i++) points.push([i & 1 ? b.max[0] : b.min[0], i & 2 ? b.max[1] : b.min[1], i & 4 ? b.max[2] : b.min[2]]);
  if (s && s.length !== null && section) {
    const along = sectionDirection(s.az);
    const n = sectionNormal(s.az);
    const p0: Vec3 = [s.x0 - section.origin.x, s.y0 - section.origin.y, 0];
    points = [0, s.length].flatMap((u) => [-s.away, s.towards].flatMap((d) => [b.min[2], b.max[2]].map((z): Vec3 => {
      const p = addScaled(addScaled(p0, along, u), n, d);
      return [p[0], p[1], z];
    })));
    b.min[0] = Math.min(...points.map((e) => e[0]));
    b.max[0] = Math.max(...points.map((e) => e[0]));
    b.min[1] = Math.min(...points.map((e) => e[1]));
    b.max[1] = Math.max(...points.map((e) => e[1]));
  }
  let centre = boxCenter(b);
  if (s && section) {
    // Centre on the section plane, so the clipped window is in the middle of the view.
    const n = sectionNormal(s.az);
    const c: Vec3 = [s.x0 - section.origin.x, s.y0 - section.origin.y, centre[2]];
    centre = sub(centre, scale(n, dot(n, sub(centre, c))));
  }
  let dir = normalize(direction);
  if (Math.hypot(dir[0], dir[1]) < 1e-9) dir = verticalSafe(dir); // exactly vertical: no screen axes otherwise
  const forward = scale(dir, -1);
  const right = normalize(cross(forward, Z));
  const up = normalize(cross(right, forward));
  // Half extents of the content on the screen axes.
  let halfRight = 0;
  let halfUp = 0;
  let radius = 0;
  for (const p of points) {
    const corner = sub(p, centre);
    halfRight = Math.max(halfRight, Math.abs(dot(corner, right)));
    halfUp = Math.max(halfUp, Math.abs(dot(corner, up)));
    radius = Math.max(radius, length(corner));
  }
  radius = Math.max(radius, 10);
  const aspect = opts.aspect > 0 ? opts.aspect : 1;
  const half = Math.max(halfUp, halfRight / aspect, 5) * 1.08;
  if (opts.perspective) {
    const t = tanHalf(opts.fov);
    const dist = half / t + radius;
    return { position: addScaled(centre, dir, dist), target: centre, up: [...Z], half: dist * t, near: Math.max(dist / 500, 0.1), far: dist * 20 + radius * 4 };
  }
  const dist = radius * 4 + 100;
  return { position: addScaled(centre, dir, dist), target: centre, up: [...Z], half, near: 0.1, far: dist * 2 + radius * 4 };
}

/** A standard view of the content; "section" needs the section (error «Линия разреза не задана»). */
export function view(preset: ViewPreset, box: Box3, opts: ViewOptions, section: SectionFrame | null = null): CameraState {
  const dir = presetDirection(preset, section?.line ?? null);
  return lookFrom(dir, box, opts, preset === "section" ? section : null);
}

/** «Вписать»: the content in the window without turning the view. */
export function fit(cam: CameraState, box: Box3, opts: ViewOptions): CameraState {
  return lookFrom(viewDirection(cam), box, opts);
}

/** Room behind the target for everything, however the view turns (fitDepth trims it after). */
export function roomBehind(box: Box3 | null, vexag: number): number {
  if (!box) return 0;
  const s = boxSize(box);
  return Math.hypot(s[0], s[1], s[2]) * Math.max(1, Math.abs(vexag));
}

/**
 * «В план»: straight down (Y up, X to the right) about the point the view is centred on, keeping the
 * scale — the plan shows what was on the screen, not everything. Again on a plan changes nothing.
 */
export function toPlan(cam: CameraState, box: Box3 | null, opts: ViewOptions): CameraState {
  const dist = Math.max(distance(cam.position, cam.target), 1);
  const down = normalize([0, -VERTICAL_NUDGE, 1]);
  const out: CameraState = { ...cam, position: addScaled(cam.target, down, dist), target: [...cam.target], up: [...Z] };
  if (!opts.perspective) {
    out.near = 0.1;
    out.far = Math.max(cam.far, dist * 2 + roomBehind(box, opts.vexag) * 2);
  }
  return out;
}

/** The camera at `dir` (unit) from its target at the same distance and scale: a step of a smooth turn. */
export function placeOn(cam: CameraState, dir: Vec3): CameraState {
  const dist = Math.max(distance(cam.position, cam.target), 1);
  return { ...cam, position: addScaled(cam.target, dir, dist), target: [...cam.target], up: [...Z] };
}

/** Orthographic frustum sides for the window proportions. */
export function orthoFrustum(cam: CameraState, aspect: number): { left: number; right: number; top: number; bottom: number } {
  return { left: -cam.half * aspect, right: cam.half * aspect, top: cam.half, bottom: -cam.half };
}

/** Pixels per scene unit at a point, for a window `heightPx` high. */
export function pixelsPerUnit(cam: CameraState, at: Vec3, heightPx: number, opts: ViewOptions): number {
  const h = heightPx || 1;
  if (!opts.perspective) return h / (2 * cam.half);
  const forward = scale(viewDirection(cam), -1);
  const depth = Math.max(dot(sub(at, cam.position), forward), 1e-6);
  return h / (2 * depth * tanHalf(opts.fov));
}

/** Normalised device coordinates (−1…1, Y up) and depth along the view of a point; null behind the camera. */
export function project(cam: CameraState, opts: ViewOptions, p: Vec3): { x: number; y: number; depth: number } | null {
  const { x, y, z } = cameraBasis(cam);
  const rel = sub(p, cam.position);
  const depth = -dot(rel, z);
  if (opts.perspective) {
    if (!(depth > 0)) return null;
    const t = tanHalf(opts.fov);
    return { x: dot(rel, x) / (depth * t * opts.aspect), y: dot(rel, y) / (depth * t), depth };
  }
  return { x: dot(rel, x) / (cam.half * opts.aspect), y: dot(rel, y) / cam.half, depth };
}

/** The ray through a point of the screen (NDC), float64 in rendered space. */
export function screenRay(cam: CameraState, opts: ViewOptions, ndcX: number, ndcY: number): { origin: Vec3; dir: Vec3 } {
  const { x, y, z } = cameraBasis(cam);
  const forward = scale(z, -1);
  if (opts.perspective) {
    const t = tanHalf(opts.fov);
    return { origin: [...cam.position], dir: normalize(add(forward, add(scale(x, ndcX * t * opts.aspect), scale(y, ndcY * t)))) };
  }
  return { origin: add(cam.position, add(scale(x, ndcX * cam.half * opts.aspect), scale(y, ndcY * cam.half))), dir: forward };
}

/**
 * Near and far planes that hold everything there is from wherever the camera now looks (every frame).
 * The orthographic camera also steps back out of the content: its distance does not change the picture.
 * `box` — scene coordinates of the content and the grid.
 */
export function fitDepth(cam: CameraState, box: Box3 | null, opts: ViewOptions): CameraState {
  if (!box) return cam;
  const sphere = boundingSphere(scaleZ(box, opts.vexag));
  const radius = Math.max(sphere.radius * 1.15, 10);
  const fwd = sub(cam.target, cam.position);
  if (length(fwd) === 0) return cam;
  const forward = normalize(fwd);
  let depth = dot(sub(sphere.center, cam.position), forward);
  if (!opts.perspective) {
    let position = cam.position;
    if (depth - radius < 1) {
      position = addScaled(cam.position, forward, depth - radius - 1);
      depth = radius + 1;
    }
    return { ...cam, position, near: Math.max(0.1, depth - radius), far: depth + radius };
  }
  const far = Math.max(depth + radius, 1);
  return { ...cam, near: Math.max(0.1, depth - radius, far / 20000), far };
}

/**
 * A new vertical exaggeration: the same data point stays in the centre of the view (its rendered Z
 * scales), the direction and the scale are kept.
 */
export function changeVexag(cam: CameraState, from: number, to: number): CameraState {
  if (from === to || from === 0) return cam;
  const target: Vec3 = [cam.target[0], cam.target[1], (cam.target[2] / from) * to];
  return { ...cam, target, position: add(cam.position, sub(target, cam.target)) };
}
