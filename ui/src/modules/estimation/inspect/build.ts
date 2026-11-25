/**
 * What the main 3D draws of the inspector of a block (ТЗ 7.35 Д-30), as numbers: the segments from the centre of the block to the
 * probes taken (the colour — the weight: Cobalt for a positive one, Copper Orange for a negative one), the probes cut off in Slate
 * (a point; a thin line to those cut off by a limit, not by the ellipsoid), the ellipsoid of the pass for `viz/ellipsoid` (the layer
 * of Д-14, as it is). Coordinates float64; the buffers float32 from the integer origin of the layer (the centre rounded,
 * `viz/origin.ts`). Pure, no three.js (vitest).
 */
import type { Vec3 } from "../../../viz/core/vec3";
import type { Origin } from "../../../viz/origin";
import type { StructureView } from "../../../viz/ellipsoid/build";
import type { EllipsoidInfo, InspectResult } from "./api";
import { isSelected, maxWeight, OUTSIDE, SELECTED, takenSet, weightColor, COLORS } from "./view";

const hexRgb = (s: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as [number, number, number];

export interface SegmentsBuild {
  origin: Origin;
  /** the centre of the block from the origin (float64) */
  offset: Vec3;
  /** the probes taken: two points per segment (the centre, the probe), sRGB 0–1 per point */
  taken: { positions: Float32Array; colors: Float32Array; ids: number[] };
  /** the probes cut off by a limit (inside the ellipsoid): a thin Slate line to each */
  cut: { positions: Float32Array; ids: number[] };
  /** the ends: the probes taken (the colour of the weight), then the cut off ones (Slate); `ids` — `Candidate.i` */
  points: { positions: Float32Array; colors: Float32Array; ids: number[]; taken: number };
  /** the largest |λ| (the scale of the colours) */
  maxWeight: number;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] of everything drawn (the centre of the block included) */
  reach: [number, number, number, number, number, number];
}

const round = (v: number): number => Math.round(v);

/** The pose of the ellipsoid of a pass for `buildEllipsoid` (the angles and the ranges of the pass; Cobalt by default). */
export function ellipsoidView(e: Pick<EllipsoidInfo, "ang1" | "ang2" | "ang3" | "r1" | "r2" | "r3">): StructureView {
  return { angles: { ang1: e.ang1, ang2: e.ang2, ang3: e.ang3 }, ranges: { a1: e.r1, a2: e.r2, a3: e.r3 } };
}

/** An ellipsoid can be drawn: the ranges are positive numbers (a pass of a block that was not estimated has them too). */
export const drawable = (e: EllipsoidInfo | null): e is EllipsoidInfo => e !== null && [e.r1, e.r2, e.r3].every((x) => Number.isFinite(x) && x > 0);

/** The segments, the points and the reach of the row for the block with the centre `centre`. */
export function buildSegments(r: Pick<InspectResult, "candidates" | "segments" | "ellipsoid">, centre: Vec3): SegmentsBuild {
  const origin = { x: round(centre[0]), y: round(centre[1]), z: round(centre[2]) };
  const offset: Vec3 = [centre[0] - origin.x, centre[1] - origin.y, centre[2] - origin.z];
  const rel = (p: readonly number[]): [number, number, number] => [p[0] - origin.x, p[1] - origin.y, p[2] - origin.z];
  const max = maxWeight(r.segments);
  const reach: SegmentsBuild["reach"] = [centre[0], centre[1], centre[2], centre[0], centre[1], centre[2]];
  const grow = (p: readonly number[]) => {
    for (let k = 0; k < 3; k++) {
      if (p[k] < reach[k]) reach[k] = p[k];
      if (p[k] > reach[k + 3]) reach[k + 3] = p[k];
    }
  };

  const takenPos: number[] = [];
  const takenCol: number[] = [];
  const takenIds: number[] = [];
  const pointPos: number[] = [];
  const pointCol: number[] = [];
  const pointIds: number[] = [];
  for (const s of r.segments) {
    const col = weightColor(s.lambda, max);
    takenPos.push(...rel(centre), ...rel(s.to));
    takenCol.push(...col, ...col);
    takenIds.push(s.i);
    pointPos.push(...rel(s.to));
    pointCol.push(...col);
    pointIds.push(s.i);
    grow(s.to);
  }
  const takenN = pointIds.length;
  const taken = takenSet(r);
  const slate = hexRgb(COLORS.excluded);
  const cutPos: number[] = [];
  const cutIds: number[] = [];
  for (const c of r.candidates) {
    // taken = has a weight or a segment (`selected` and `hy_capped`); the rest — Slate, among them a probe «selected» of a row whose
    // system was not solved (STATUS 2, 3: no weights, no segments)
    if (isSelected(c, taken) || !c.xyz) continue;
    pointPos.push(...rel(c.xyz));
    pointCol.push(...slate);
    pointIds.push(c.i);
    // a probe the ellipsoid did not let in is only a point; a probe cut off by a limit has a line (it was nearly taken); a probe
    // «selected» without a weight was taken by nothing: a point only
    if (c.reason !== OUTSIDE && c.reason !== SELECTED) {
      cutPos.push(...rel(centre), ...rel(c.xyz));
      cutIds.push(c.i);
    }
    // the reach counts the probes the ellipsoid let in: those outside it would stretch «Вписать» to the whole pool
    if (c.reason !== OUTSIDE) grow(c.xyz);
  }
  return {
    origin,
    offset,
    taken: { positions: new Float32Array(takenPos), colors: new Float32Array(takenCol), ids: takenIds },
    cut: { positions: new Float32Array(cutPos), ids: cutIds },
    points: { positions: new Float32Array(pointPos), colors: new Float32Array(pointCol), ids: pointIds, taken: takenN },
    maxWeight: max,
    reach,
  };
}
