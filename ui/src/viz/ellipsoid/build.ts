/**
 * What the main 3D draws for an anisotropy ellipsoid (ТЗ 8.6, 7.35 Д-14; ADR 0022 §9, решения 17–19): the unit
 * sphere under a matrix (`ellipsoidBasis`: columns a1·u1, a2·u2, a3·u3), so an edit of angles or ranges changes only
 * that matrix, never the geometry; the three principal sections (unit circles of the sphere in its planes a1–a2,
 * a2–a3, a3–a1); the disk of the plane a1–a2 (the unit disk of the sphere's plane a1–a2) and the samples of the
 * preview not further than the thickness from that plane and inside the disk, coloured by value. The selected
 * structure — the surface, the sections and the disk; the others — thin sections only. Numbers float64; the buffers
 * float32 from the integer origin of the layer (the centre rounded, `viz/origin.ts`), the centre sitting at `offset`
 * from it. Pure, no three.js (vitest).
 */
import { ColorScale, NO_VALUE_COLOR } from "../color/colorScale";
import type { Vec3 } from "../core/vec3";
import type { Origin } from "../origin";
import { ellipsoidBasis, frameOf, type Angles, type EllipsoidFrame, type Ranges } from "./frame";

/** The brand (docs/brand/README.md, ADR 0006): Cobalt — data and models, Copper Orange — the lit, Slate — secondary. */
export const BRAND = {
  cobalt: "#1f6bff",
  copper: "#f46a1a",
  slate: "#8a99a8",
  stone: "#e6e9ee",
  anthracite: "#1b222b",
} as const;

/** One structure as the layer shows it; `color` — the colour of the model (by default Cobalt). */
export interface StructureView {
  angles: Angles;
  ranges: Ranges;
  color?: string;
}

export interface EllipsoidInput {
  /** absolute centre, float64 (the extent of the points or «Поставить эллипсоид здесь») */
  centre: Vec3;
  structures: readonly StructureView[];
  /** the structure drawn in full; by default the one of the largest a1 (the first of equal ones) */
  selected?: number;
}

export interface StructurePose {
  frame: EllipsoidFrame;
  ranges: Ranges;
  /** the unit sphere → the ellipsoid, 3 × 3 rows (`ellipsoidBasis`) */
  basis: number[];
  color: string;
}

export interface EllipsoidBuild {
  /** absolute integer origin of the layer and the centre from it (float64) */
  origin: Origin;
  offset: Vec3;
  centre: Vec3;
  structures: StructurePose[];
  selected: number;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] of all the ellipsoids */
  reach: [number, number, number, number, number, number];
}

function checkRanges(r: Ranges): void {
  if (![r.a1, r.a2, r.a3].every((x) => Number.isFinite(x) && x > 0)) throw new RangeError("Дальности эллипсоида — положительные числа");
}

/** The structure of the largest a1, the first of equal ones; −1 — none. */
export function defaultSelected(structures: readonly Pick<StructureView, "ranges">[]): number {
  let best = -1;
  structures.forEach((s, i) => {
    if (best < 0 || s.ranges.a1 > structures[best].ranges.a1) best = i;
  });
  return best;
}

/** The pose of a structure: its frame, the matrix over the unit sphere and the colour. */
export function poseOf(s: StructureView): StructurePose {
  checkRanges(s.ranges);
  const frame = frameOf(s.angles);
  return { frame, ranges: { ...s.ranges }, basis: ellipsoidBasis(frame, s.ranges), color: s.color ?? BRAND.cobalt };
}

/** The half extents along X, Y, Z of an ellipsoid of the matrix M: √(Σ_j M_ij²). */
export function halfExtents(basis: readonly number[]): Vec3 {
  const row = (i: number) => Math.hypot(basis[3 * i], basis[3 * i + 1], basis[3 * i + 2]);
  return [row(0), row(1), row(2)];
}

/** Absolute bounds of the ellipsoids about the centre (exact for each, the union of all). */
export function reachOf(centre: Vec3, poses: readonly Pick<StructurePose, "basis">[]): [number, number, number, number, number, number] {
  const h: Vec3 = [0, 0, 0];
  for (const p of poses) {
    const e = halfExtents(p.basis);
    for (let k = 0; k < 3; k++) h[k] = Math.max(h[k], e[k]);
  }
  return [centre[0] - h[0], centre[1] - h[1], centre[2] - h[2], centre[0] + h[0], centre[1] + h[1], centre[2] + h[2]];
}

/** The layer origin: the centre rounded (`viz/origin.ts`). */
export const originOf = (centre: Vec3): Origin => ({ x: Math.round(centre[0]), y: Math.round(centre[1]), z: Math.round(centre[2]) });

export function buildEllipsoid(input: EllipsoidInput): EllipsoidBuild {
  const { centre, structures } = input;
  if (!centre.every(Number.isFinite)) throw new RangeError("Центр эллипсоида — числа");
  if (!structures.length) throw new RangeError("Нет структур для эллипсоида");
  const poses = structures.map(poseOf);
  const selected = input.selected ?? defaultSelected(structures);
  if (!Number.isInteger(selected) || selected < 0 || selected >= poses.length) throw new RangeError("Нет такой структуры");
  const origin = originOf(centre);
  return {
    origin,
    offset: [centre[0] - origin.x, centre[1] - origin.y, centre[2] - origin.z],
    centre: [...centre] as Vec3,
    structures: poses,
    selected,
    reach: reachOf(centre, poses),
  };
}

/**
 * The 4 × 4 matrix (rows, as `THREE.Matrix4.set` takes it) of the sphere at the centre: [M | offset]. Float64; the
 * GPU gets it from three.js as float32 of small numbers (the offset < 1, the ranges).
 */
export function poseMatrix(offset: Vec3, basis: readonly number[]): number[] {
  const m = basis;
  return [m[0], m[1], m[2], offset[0], m[3], m[4], m[5], offset[1], m[6], m[7], m[8], offset[2], 0, 0, 0, 1];
}

/** The principal sections of the unit sphere: circles in the planes a1–a2 (z = 0), a2–a3 (x = 0), a3–a1 (y = 0). */
export const SECTION_PLANES = ["a1a2", "a2a3", "a3a1"] as const;

/** One unit circle as segment pairs (x, y, z of both ends), `n` segments, in a plane of the sphere. */
export function unitCircle(plane: (typeof SECTION_PLANES)[number], n = 96): Float32Array {
  const out = new Float32Array(n * 6);
  const at = (i: number): [number, number, number] => {
    const t = (2 * Math.PI * (i % n)) / n;
    const c = Math.cos(t);
    const s = Math.sin(t);
    return plane === "a1a2" ? [c, s, 0] : plane === "a2a3" ? [0, c, s] : [s, 0, c];
  };
  for (let i = 0; i < n; i++) out.set([...at(i), ...at(i + 1)], i * 6);
  return out;
}

/** The three sections at once (3 · n segments). */
export function unitSections(n = 96): Float32Array {
  const out = new Float32Array(3 * n * 6);
  SECTION_PLANES.forEach((p, k) => out.set(unitCircle(p, n), k * n * 6));
  return out;
}

/** The samples of the preview: float64 from an integer origin (`viz/origin.ts`); `values` — null / NaN is no value. */
export interface Samples {
  origin: Origin;
  /** interleaved x, y, z from `origin`, float64 */
  xyz: Float64Array;
  values?: ArrayLike<number | null>;
}

export interface SampleSelection {
  /** the indices of the samples kept, ascending */
  indices: Uint32Array;
  /** x, y, z from the layer origin, float32 (display only) */
  position: Float32Array;
  /** r, g, b 0–1, sRGB (the layer turns them linear for three.js) */
  color: Float32Array;
}

/** The scale of the samples: linear over the finite values (none — null). */
export function sampleScale(values: ArrayLike<number | null> | undefined): ColorScale | null {
  if (!values) return null;
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || !Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!(hi >= lo)) return null;
  return new ColorScale(lo, hi > lo ? hi : lo + 1, false);
}

const hexRgb = (hex: string): [number, number, number] => {
  const v = parseInt(hex.slice(1), 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
};

/**
 * The samples in the tolerance of the disk a1–a2 of a pose about the centre: |u3·Δ| ≤ thickness and inside the
 * disk, (u1·Δ / a1)² + (u2·Δ / a2)² ≤ 1 (Δ — from the centre, float64). A sample of no value is kept, in the colour
 * of «no value» (never the colour of 0). `scale` — by default `sampleScale(values)`.
 */
export function samplesInTolerance(
  layer: Pick<EllipsoidBuild, "origin" | "offset">,
  pose: Pick<StructurePose, "frame" | "ranges">,
  samples: Samples,
  thickness: number,
  scale: Pick<ColorScale, "rgb"> | null = sampleScale(samples.values),
): SampleSelection {
  if (!(thickness >= 0)) throw new RangeError("Толщина слоя у диска — неотрицательное число");
  const { u1, u2, u3 } = pose.frame;
  const { a1, a2 } = pose.ranges;
  // integer differences of the origins are exact; Δ from the centre in float64
  const dx = samples.origin.x - layer.origin.x;
  const dy = samples.origin.y - layer.origin.y;
  const dz = samples.origin.z - layer.origin.z;
  const [ox, oy, oz] = layer.offset;
  const xyz = samples.xyz;
  const n = Math.floor(xyz.length / 3);
  const keep: number[] = [];
  for (let i = 0; i < n; i++) {
    const x = dx + xyz[3 * i] - ox;
    const y = dy + xyz[3 * i + 1] - oy;
    const z = dz + xyz[3 * i + 2] - oz;
    if (!(Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z))) continue;
    const w = u3[0] * x + u3[1] * y + u3[2] * z;
    if (Math.abs(w) > thickness) continue;
    const p = (u1[0] * x + u1[1] * y + u1[2] * z) / a1;
    const q = (u2[0] * x + u2[1] * y + u2[2] * z) / a2;
    if (p * p + q * q <= 1) keep.push(i);
  }
  const indices = Uint32Array.from(keep);
  const position = new Float32Array(keep.length * 3);
  const color = new Float32Array(keep.length * 3);
  const empty = hexRgb(NO_VALUE_COLOR);
  keep.forEach((i, j) => {
    position[3 * j] = dx + xyz[3 * i];
    position[3 * j + 1] = dy + xyz[3 * i + 1];
    position[3 * j + 2] = dz + xyz[3 * i + 2];
    const v = samples.values?.[i];
    const rgb = scale && v !== null && v !== undefined && Number.isFinite(v) ? scale.rgb(v).map((c) => c / 255) : empty;
    color.set(rgb, 3 * j);
  });
  return { indices, position, color };
}
