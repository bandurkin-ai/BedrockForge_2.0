/**
 * Line and point layers for the 3D window (ТЗ 7.18, ADR 0011 §3–§4): the manifest of a layer
 * (`GET /viz/lines/{id}`, BFA1 `line_layer`), the buffers of one part at one level of detail
 * (`/viz/lines/{id}/parts/{k}?lod=`, BFA1 `lines`; the encoder is core/bf_core/viz/lines.py) and the choice of
 * the level by the distance. Pure: no three.js, no DOM — the buffers are built in the Worker.
 *
 * A part: `offsets u4` (n + 1), `xyz f4×3` from the integer origin of the part, `line u4` — the line of the
 * layer (the row of its attributes) of every polyline, `closed u1`; a layer of points has only `xyz` and
 * `line`. A closed line repeats its first vertex, so its segments close by themselves. A polyline of one
 * vertex is a point (a marker). An empty height comes as NaN and is never drawn at 0: it takes the lowest
 * height of its part, and a part without any height is flat at local 0 — the layer puts it under the lowest
 * object of the scene, as a raster without an elevation (ТЗ 7.19).
 */
import { chooseLevel, stepBoundaries } from "../core/lod";
import type { Box3 } from "../core/bounds";
import type { Vec3 } from "../core/vec3";
import { bfa1Array, decodeBfa1 } from "../format/bfa1";

/** [xmin, ymin, zmin, xmax, ymax, zmax], absolute float64; null — no vertex has this coordinate (a height). */
export type Bbox6 = (number | null)[];

export interface LinesPart {
  k: number;
  /** polylines (points) of the part at level 0 */
  n: number;
  /** vertices of the part at level 0 */
  points: number;
  bbox: Bbox6 | null;
  /** absolute integer origin of the part's float32 points */
  origin: [number, number, number];
  /** ETag of the part at every level */
  etags: string[];
}

export interface LinesManifest {
  layerId: number;
  name: string;
  fileId: number | null;
  version: number;
  pointsOnly: boolean;
  /** "#rrggbb"; null — the layer has no colour of its own */
  color: string | null;
  lines: number;
  points: number;
  bbox: Bbox6 | null;
  /** tolerance of every level, m: 0 — every vertex */
  tols: number[];
  /** the layer has texts (DXF TEXT / MTEXT, MIF Text) */
  labels: boolean;
  parts: LinesPart[];
}

interface ManifestHeader {
  kind: string;
  layer: { id: number; name: string; file_id: number | null };
  version: number;
  points_only: boolean;
  color: string | null;
  lines: number;
  points: number;
  bbox: Bbox6 | null;
  lods: { lod: number; tol: number }[];
  labels: boolean;
  parts: LinesPart[];
}

export function readLinesManifest(buf: ArrayBuffer): LinesManifest {
  const h = decodeBfa1<ManifestHeader>(buf).header;
  if (h.kind !== "line_layer" || !Array.isArray(h.parts) || !Array.isArray(h.lods)) throw new Error("lines: the answer is not a manifest of a line layer");
  return {
    layerId: h.layer.id,
    name: h.layer.name,
    fileId: h.layer.file_id ?? null,
    version: h.version,
    pointsOnly: h.points_only === true,
    color: typeof h.color === "string" ? h.color : null,
    lines: h.lines,
    points: h.points,
    bbox: h.bbox ?? null,
    tols: h.lods.map((l) => l.tol),
    labels: h.labels === true,
    parts: h.parts,
  };
}

/** Two manifests describe the same parts (a rename or a new colour): what is built stays while it reloads. */
export function sameLayout(a: LinesManifest, b: LinesManifest): boolean {
  if (a.pointsOnly !== b.pointsOnly || a.parts.length !== b.parts.length || a.tols.join() !== b.tols.join()) return false;
  return a.parts.every((p, i) => {
    const q = b.parts[i];
    return p.n === q.n && p.points === q.points && p.origin.join() === q.origin.join() && JSON.stringify(p.bbox) === JSON.stringify(q.bbox);
  });
}

export interface PartLabels {
  text: string[];
  /** anchors: float32 xyz from the part origin, one per text */
  xyz: Float32Array;
  /** texts of the part in the core (the answer holds at most 5000) */
  total: number;
}

/** The buffers of one part at one level, as the Worker gives them back. */
export interface BuiltLines {
  k: number;
  lod: number;
  /** polylines (points) of the answer */
  n: number;
  /** vertices of the answer */
  points: number;
  pointsOnly: boolean;
  /** fewer vertices than the part has (a coarser level) */
  simplified: boolean;
  origin: [number, number, number];
  /** 6 floats per segment: start xyz, end xyz (float32 from the part origin) */
  positions: Float32Array;
  segments: number;
  /** n + 1: polyline p owns the segments [segStart[p], segStart[p + 1]) */
  segStart: Uint32Array;
  /** xyz of the markers: the points of a point layer, the one-vertex polylines of a line layer */
  dots: Float32Array;
  /** the polyline of every marker; null — marker i is point i */
  dotPiece: Uint32Array | null;
  /** the line (point) of the layer of every polyline (point): the row of its attributes */
  line: Uint32Array;
  closed: Uint8Array | null;
  /** vertices without a height */
  emptyZ: number;
  /** local Z given to them: the lowest height of the part */
  fill: number;
  /** no vertex of the part has a height: it is flat at local 0 and placed by the layer */
  noHeight: boolean;
  labels: PartLabels | null;
}

export interface PartHeader {
  kind?: string;
  k: number;
  lod: number;
  points_only: boolean;
  origin: [number, number, number];
  bbox: Bbox6 | null;
  n: number;
  points: number;
  simplified?: boolean;
  labels?: { at: number[]; text: string[]; total?: number } | null;
}

export interface PartArrays {
  xyz: Float32Array;
  line: Uint32Array;
  offsets: Uint32Array | null;
  closed: Uint8Array | null;
}

/**
 * The buffers of a part from its decoded arrays. `xyz` is changed in place (empty heights filled) and, for a
 * layer of points, becomes `dots` — nothing is copied.
 */
export function buildLineBuffers(h: PartHeader, a: PartArrays): BuiltLines {
  const { xyz, line, offsets, closed } = a;
  const points = xyz.length / 3;
  const n = offsets ? offsets.length - 1 : points;
  if (!Number.isInteger(points) || n < 0 || line.length !== n || (offsets && (offsets[0] !== 0 || offsets[n] !== points))) throw new Error(`lines of part ${h.k}: offsets, lines and vertices disagree`);
  const zmin = h.bbox ? h.bbox[2] : null;
  const noHeight = zmin === null || zmin === undefined;
  const fill = noHeight ? 0 : Math.fround(zmin - h.origin[2]);
  let emptyZ = 0;
  for (let i = 2; i < xyz.length; i += 3) {
    if (xyz[i] === xyz[i]) continue; // not NaN
    xyz[i] = fill;
    emptyZ++;
  }
  const out: BuiltLines = {
    k: h.k, lod: h.lod, n, points, pointsOnly: !offsets, simplified: h.simplified === true, origin: h.origin,
    positions: new Float32Array(0), segments: 0, segStart: new Uint32Array(n + 1), dots: xyz, dotPiece: null,
    line, closed, emptyZ, fill, noHeight, labels: null,
  };
  if (offsets) {
    let segments = 0;
    let single = 0;
    const ok = (i: number) => Number.isFinite(xyz[i * 3]) && Number.isFinite(xyz[i * 3 + 1]);
    for (let p = 0; p < n; p++) {
      const from = offsets[p];
      const to = offsets[p + 1];
      if (to < from || to > points) throw new Error(`lines of part ${h.k}: offsets are not in order`);
      if (to - from === 1) single++;
      for (let i = from; i + 1 < to; i++) if (ok(i) && ok(i + 1)) segments++;
    }
    const positions = new Float32Array(segments * 6);
    const dots = new Float32Array(single * 3);
    const dotPiece = new Uint32Array(single);
    let s = 0;
    let d = 0;
    for (let p = 0; p < n; p++) {
      const from = offsets[p];
      const to = offsets[p + 1];
      out.segStart[p] = s;
      if (to - from === 1) {
        dots.set(xyz.subarray(from * 3, from * 3 + 3), d * 3);
        dotPiece[d++] = p;
      }
      for (let i = from; i + 1 < to; i++) if (ok(i) && ok(i + 1)) positions.set(xyz.subarray(i * 3, i * 3 + 6), s++ * 6);
    }
    out.segStart[n] = s;
    out.positions = positions;
    out.segments = segments;
    out.dots = dots;
    out.dotPiece = dotPiece;
    // copies of the small arrays: the answer buffer (every vertex once more) is not kept alive by them
    out.line = line.slice();
    out.closed = closed ? closed.slice() : null;
  }
  const lab = h.labels;
  if (lab && Array.isArray(lab.at) && Array.isArray(lab.text)) {
    const text: string[] = [];
    const at: number[] = [];
    lab.at.forEach((p, i) => {
      const v = offsets ? (p >= 0 && p < n && offsets[p + 1] > offsets[p] ? offsets[p] : -1) : p >= 0 && p < points ? p : -1;
      if (v < 0 || typeof lab.text[i] !== "string" || !lab.text[i]) return;
      text.push(lab.text[i]);
      at.push(v);
    });
    const anchors = new Float32Array(at.length * 3);
    at.forEach((v, i) => anchors.set(xyz.subarray(v * 3, v * 3 + 3), i * 3));
    if (text.length) out.labels = { text, xyz: anchors, total: lab.total ?? text.length };
  }
  return out;
}

/** The part of a BFA1 `lines` answer: header and arrays checked against each other. */
export function decodeLinesPart(buffer: ArrayBuffer): { header: PartHeader; arrays: PartArrays } {
  const doc = decodeBfa1<PartHeader>(buffer);
  const h = doc.header;
  if (h.kind !== "lines" || !Array.isArray(h.origin)) throw new Error("lines: the answer is not a part of a line layer");
  const pointsOnly = h.points_only === true;
  return {
    header: h,
    arrays: {
      xyz: bfa1Array(doc, "xyz", "<f4"),
      line: bfa1Array(doc, "line", "<u4"),
      offsets: pointsOnly ? null : bfa1Array(doc, "offsets", "<u4"),
      closed: pointsOnly ? null : bfa1Array(doc, "closed", "<u1"),
    },
  };
}

// --- level of detail ----------------------------------------------------------------------------------

/** A level is coarse enough while its tolerance (Douglas–Peucker, the cell of points) is at most this on screen, px. */
export const LINE_LOD_PX = 1;

/** Boundaries of pixels per metre for the tolerances of the levels (`core/lod.chooseLevel`), descending. */
export function lineBoundaries(tols: readonly number[], px = LINE_LOD_PX): number[] {
  return stepBoundaries(tols, px);
}

/** The level for pixels per metre at a part, with the 20 % hysteresis about the current one. */
export function lineLevel(ppm: number, boundaries: readonly number[], current: number | null): number {
  return chooseLevel(ppm, boundaries, current);
}

/** The point of a box nearest to `p`: where a part is seen largest (the measure of its level). */
export function nearestInBox(box: Box3, p: Vec3): Vec3 {
  return [Math.min(Math.max(p[0], box.min[0]), box.max[0]), Math.min(Math.max(p[1], box.min[1]), box.max[1]), Math.min(Math.max(p[2], box.min[2]), box.max[2])];
}

/** The extent of a part or a layer as numbers: an empty height (null) becomes `level`. */
export function bboxAt(bbox: Bbox6 | null, level: number, fallback: readonly number[]): number[] {
  if (!bbox || bbox[0] === null || bbox[1] === null || bbox[3] === null || bbox[4] === null) return [fallback[0], fallback[1], level, fallback[0], fallback[1], level];
  return [bbox[0], bbox[1], bbox[2] ?? level, bbox[3], bbox[4], bbox[5] ?? level];
}
