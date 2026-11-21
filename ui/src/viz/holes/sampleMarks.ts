/**
 * The selected samples in 3D (ТЗ 8.3, № 237; ADR 0019 §8): a scene layer (`Scene.addLayer`) of the intervals of the
 * selection — one fat segment per sample along its hole, over the traces, in Copper Orange of the brand; clipped by
 * the section window with the rest. The data — BFA1 `selection_segments` of the core (`GET /stats/selection/{token}/
 * segments`): the ends of the intervals `a`, `b` float32 from the integer `origin` of the header (float64 never goes
 * to the GPU). `count` — the segments drawn: as many as the samples of the selection with a position (the E2E check).
 */
import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { bfa1Array, decodeBfa1, type Bfa1 } from "../format/bfa1";
import type { Origin } from "../origin";
import type { SceneLayer } from "../sceneTypes";
import { SELECT_COLOR } from "./colors";

export interface SelectionSegments {
  token: string;
  setId: number;
  tableId: number;
  rev: number;
  method: string;
  /** samples of the selection; `segments` of them have a position, `noPosition` — a hole without a trajectory */
  n: number;
  segments: number;
  noPosition: number;
  /** integer metres: the ends are from here */
  origin: Origin;
  bbox: number[] | null;
  /** dh_rows.id and dh_holes.id of each segment; FROM, TO */
  row: ArrayLike<number>;
  hole: Uint32Array;
  from: Float64Array;
  to: Float64Array;
  /** the ends on the trajectory, xyz × segments, from `origin` */
  a: Float32Array;
  b: Float32Array;
}

interface Header {
  kind?: string;
  token: string;
  set_id: number;
  table_id: number;
  rev: number;
  method: string;
  n: number;
  segments: number;
  no_position: number;
  origin: [number, number, number];
  bbox: number[] | null;
}

function rowIds(doc: Bfa1<unknown>, count: number): ArrayLike<number> {
  return doc.dtypes.row === "<f8" ? bfa1Array(doc, "row", "<f8", count) : bfa1Array(doc, "row", "<u4", count);
}

/** The answer of the core as segments (a damaged one — a VizError in Russian). */
export function readSelectionSegments(buf: ArrayBufferLike | ArrayBufferView): SelectionSegments {
  const doc = decodeBfa1<Header>(buf);
  const h = doc.header;
  const k = h.segments;
  const [x, y, z] = h.origin;
  return {
    token: h.token,
    setId: h.set_id,
    tableId: h.table_id,
    rev: h.rev,
    method: h.method,
    n: h.n,
    segments: k,
    noPosition: h.no_position,
    origin: { x, y, z },
    bbox: h.bbox && h.bbox.length === 6 ? [...h.bbox] : null,
    row: rowIds(doc, k),
    hole: bfa1Array(doc, "hole", "<u4", k),
    from: bfa1Array(doc, "from", "<f8", k),
    to: bfa1Array(doc, "to", "<f8", k),
    a: bfa1Array(doc, "a", "<f4", k * 3),
    b: bfa1Array(doc, "b", "<f4", k * 3),
  };
}

/** The pairs of ends for `LineSegmentsGeometry`: a₀ b₀ a₁ b₁ …, xyz each. */
export function segmentPositions(s: Pick<SelectionSegments, "segments" | "a" | "b">): Float32Array {
  const out = new Float32Array(s.segments * 6);
  for (let i = 0; i < s.segments; i++) {
    out.set(s.a.subarray(i * 3, i * 3 + 3), i * 6);
    out.set(s.b.subarray(i * 3, i * 3 + 3), i * 6 + 3);
  }
  return out;
}

export interface SampleMarksContext {
  /** the shared clipping planes of the scene (the section window) */
  clip: THREE.Plane[];
  /** the size of the view in px (the `resolution` of the LineMaterial) */
  resolution: THREE.Vector2;
}

/** Copper Orange (as the selected hole), wider than its line: the samples stand out on it too. */
export const SAMPLE_MARK_COLOR = SELECT_COLOR;
export const SAMPLE_MARK_WIDTH = 7;

export class SampleMarks implements SceneLayer {
  readonly object = new THREE.Group();
  readonly origin: Origin;
  readonly bbox: number[] | null;
  /** the segments drawn */
  readonly count: number;
  private readonly material: LineMaterial | null = null;
  private readonly geometry: LineSegmentsGeometry | null = null;

  constructor(
    readonly data: SelectionSegments,
    ctx: SampleMarksContext,
  ) {
    this.origin = data.origin;
    this.bbox = data.bbox;
    this.count = data.segments;
    if (!data.segments) return;
    this.geometry = new LineSegmentsGeometry().setPositions(segmentPositions(data));
    this.material = new LineMaterial({ color: new THREE.Color(SAMPLE_MARK_COLOR).getHex(), linewidth: SAMPLE_MARK_WIDTH, worldUnits: false });
    this.material.uniforms.resolution.value = ctx.resolution;
    this.material.clippingPlanes = ctx.clip;
    const lines = new LineSegments2(this.geometry, this.material);
    // over the traces and the selected hole (renderOrder 1)
    lines.renderOrder = 3;
    this.object.add(lines);
  }

  dispose(): void {
    this.geometry?.dispose();
    this.material?.dispose();
  }
}
