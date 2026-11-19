/**
 * Snapping targets (ADR 0015 §4; BF `edit/snap.ts`): kinds and their priority, the projection of points and
 * segments to the screen, the grid node, and the sources built from the working copy and from the data of the
 * core. The screen index over them is `snapIndex.ts`.
 *
 * Candidates stay in project coordinates (float64, exactly as given): a snapped point is the stored point bit
 * for bit, or a point of a stored segment.
 */
import { decodeBfa1 } from "../viz/format/bfa1";
import type { Plane } from "./coords";
import { EditError } from "./io";
import type { EditLayer, Vec3, VertexRef } from "./model";

export type SnapKind = "vertex" | "midpoint" | "nearest" | "wf-vertex" | "wf-edge" | "wf-section" | "collar" | "trace" | "interval" | "grid";

/** Labels shown at the cursor. */
export const SNAP_TITLES: Record<SnapKind, string> = {
  vertex: "вершина",
  midpoint: "середина",
  nearest: "на линии",
  "wf-vertex": "вершина каркаса",
  "wf-edge": "ребро каркаса",
  "wf-section": "сечение каркаса",
  collar: "устье",
  trace: "траектория",
  interval: "граница интервала",
  grid: "узел сетки",
};

export const SNAP_KINDS: SnapKind[] = ["vertex", "midpoint", "nearest", "wf-vertex", "wf-edge", "wf-section", "collar", "trace", "interval", "grid"];

/** Point-like kinds (rank 0) win over "on a segment" kinds (rank 1); grid nodes are the last resort. */
export const RANK_OF: Record<SnapKind, number> = {
  vertex: 0,
  midpoint: 0,
  "wf-vertex": 0,
  collar: 0,
  interval: 0,
  nearest: 1,
  "wf-edge": 1,
  "wf-section": 1,
  trace: 1,
  grid: 2,
};

export interface SnapHit {
  kind: SnapKind;
  point: Vec3;
  label: string;
  source?: string;
  distancePx: number;
}

/** One bunch of candidates in project coordinates. The arrays are referenced, not copied. */
export interface SnapSource {
  /** e.g. "lines:12", "set:3:intervals"; a source with the same key replaces the old one */
  key: string;
  /** x, y, z triples */
  points?: { kind: SnapKind; xyz: Float64Array; labels?: (i: number) => string };
  /** x, y, z pairs: 6 numbers per segment; "nearest point on the segment" candidates of `kind`, plus "midpoint" candidates when midpoints = true */
  segments?: { kind: SnapKind; xyz: Float64Array; midpoints?: boolean };
}

export interface ViewTransform {
  /** column-major 4x4: project coordinates → clip space */
  matrix: ArrayLike<number>;
  width: number;
  height: number;
  /** e.g. the section clipping window */
  visible?: (x: number, y: number, z: number) => boolean;
  /**
   * The height a target without one (NaN Z) is drawn at — the Z of the panel in plan. Only its screen position
   * uses it: the snapped point keeps its missing height.
   */
  missingZ?: number;
}

/** Screen px, py (origin top-left, y down) and depth (NDC z); null when behind the camera or outside clip z. */
export function projectPoint(m: ArrayLike<number>, w: number, h: number, x: number, y: number, z: number): [number, number, number] | null {
  const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
  if (!(cw > 0)) return null;
  const cz = m[2] * x + m[6] * y + m[10] * z + m[14];
  if (cz < -cw || cz > cw) return null;
  const cx = m[0] * x + m[4] * y + m[8] * z + m[12];
  const cy = m[1] * x + m[5] * y + m[9] * z + m[13];
  return [(cx / cw + 1) * 0.5 * w, (1 - cy / cw) * 0.5 * h, cz / cw];
}

/** Output slots of `projectSegment`. */
export const SEG_X0 = 0;
export const SEG_Y0 = 1;
export const SEG_X1 = 2;
export const SEG_Y1 = 3;
/** parameters (0 at a, 1 at b) of the two ends of the visible piece */
export const SEG_T0 = 4;
export const SEG_T1 = 5;
/** clip-space w at the two ends of the visible piece */
export const SEG_W0 = 6;
export const SEG_W1 = 7;

/**
 * Projects the segment a–b and clips it to the view volume (near / far) and to the viewport.
 * Fills `out` (8 numbers, see SEG_*) with the screen ends of the visible piece; false when nothing is visible.
 */
export function projectSegment(
  m: ArrayLike<number>,
  w: number,
  h: number,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  out: Float64Array,
): boolean {
  const aX = m[0] * ax + m[4] * ay + m[8] * az + m[12];
  const aY = m[1] * ax + m[5] * ay + m[9] * az + m[13];
  const aZ = m[2] * ax + m[6] * ay + m[10] * az + m[14];
  const aW = m[3] * ax + m[7] * ay + m[11] * az + m[15];
  const bX = m[0] * bx + m[4] * by + m[8] * bz + m[12];
  const bY = m[1] * bx + m[5] * by + m[9] * bz + m[13];
  const bZ = m[2] * bx + m[6] * by + m[10] * bz + m[14];
  const bW = m[3] * bx + m[7] * by + m[11] * bz + m[15];
  let t0 = 0;
  let t1 = 1;
  // Clip space: w + z >= 0 (near), w - z >= 0 (far), w > 0.
  for (let side = 0; side < 3; side++) {
    const fa = side === 0 ? aW + aZ : side === 1 ? aW - aZ : aW - 1e-12;
    const fb = side === 0 ? bW + bZ : side === 1 ? bW - bZ : bW - 1e-12;
    if (fa < 0 && fb < 0) return false;
    if (fa < 0) t0 = Math.max(t0, fa / (fa - fb));
    else if (fb < 0) t1 = Math.min(t1, fa / (fa - fb));
  }
  if (!(t0 <= t1)) return false;
  const w0 = aW + (bW - aW) * t0;
  const w1 = aW + (bW - aW) * t1;
  if (!(w0 > 0) || !(w1 > 0)) return false;
  const x0 = ((aX + (bX - aX) * t0) / w0 + 1) * 0.5 * w;
  const y0 = (1 - (aY + (bY - aY) * t0) / w0) * 0.5 * h;
  const x1 = ((aX + (bX - aX) * t1) / w1 + 1) * 0.5 * w;
  const y1 = (1 - (aY + (bY - aY) * t1) / w1) * 0.5 * h;
  // Liang–Barsky against the viewport, in the screen parameter s.
  let s0 = 0;
  let s1 = 1;
  const dx = x1 - x0;
  const dy = y1 - y0;
  for (let side = 0; side < 4; side++) {
    const p = side === 0 ? -dx : side === 1 ? dx : side === 2 ? -dy : dy;
    const q = side === 0 ? x0 : side === 1 ? w - x0 : side === 2 ? y0 : h - y0;
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const r = q / p;
      if (p < 0) {
        if (r > s1) return false;
        if (r > s0) s0 = r;
      } else {
        if (r < s0) return false;
        if (r < s1) s1 = r;
      }
    }
  }
  // Screen parameter → parameter along the 3D segment (perspective-correct).
  const q0 = s0 === 0 ? 0 : (s0 * w0) / (w1 * (1 - s0) + s0 * w0);
  const q1 = s1 === 1 ? 1 : (s1 * w0) / (w1 * (1 - s1) + s1 * w0);
  out[SEG_X0] = x0 + dx * s0;
  out[SEG_Y0] = y0 + dy * s0;
  out[SEG_X1] = x0 + dx * s1;
  out[SEG_Y1] = y0 + dy * s1;
  out[SEG_T0] = t0 + (t1 - t0) * q0;
  out[SEG_T1] = t0 + (t1 - t0) * q1;
  out[SEG_W0] = w0 + (w1 - w0) * q0;
  out[SEG_W1] = w0 + (w1 - w0) * q1;
  return true;
}

/**
 * The parameter (0 at a, 1 at b) of the point of the projected piece nearest to the pixel (px, py);
 * `seg` is the output of `projectSegment`.
 */
export function segmentParamAt(seg: Float64Array, px: number, py: number): number {
  const dx = seg[SEG_X1] - seg[SEG_X0];
  const dy = seg[SEG_Y1] - seg[SEG_Y0];
  const len2 = dx * dx + dy * dy;
  let s = len2 > 0 ? ((px - seg[SEG_X0]) * dx + (py - seg[SEG_Y0]) * dy) / len2 : 0;
  s = s < 0 ? 0 : s > 1 ? 1 : s;
  const w0 = seg[SEG_W0];
  const w1 = seg[SEG_W1];
  const q = (s * w0) / (w1 * (1 - s) + s * w0);
  return seg[SEG_T0] + (seg[SEG_T1] - seg[SEG_T0]) * q;
}

/**
 * The nearest grid node in the drawing plane. A plane that is not vertical: X and Y are rounded to the
 * step and Z is taken from the plane. A vertical plane (a section): the distance along the horizontal
 * direction of the plane, measured from the plane point, and Z are rounded to the step.
 */
export function gridSnap(p: Vec3, step: number, plane: Plane): Vec3 {
  if (!(step > 0)) return [p[0], p[1], p[2]];
  const n = plane.normal;
  const o = plane.point;
  if (Math.abs(n[2]) > 1e-9) {
    const x = Math.round(p[0] / step) * step;
    const y = Math.round(p[1] / step) * step;
    return [x, y, o[2] - ((x - o[0]) * n[0] + (y - o[1]) * n[1]) / n[2]];
  }
  const len = Math.hypot(n[0], n[1]);
  const hx = -n[1] / len;
  const hy = n[0] / len;
  const s = Math.round(((p[0] - o[0]) * hx + (p[1] - o[1]) * hy) / step) * step;
  return [o[0] + s * hx, o[1] + s * hy, Math.round(p[2] / step) * step];
}

// ---- sources

/** A line layer with more vertices takes no part in snapping (ADR 0015 §4: a line in the snap panel says so). */
export const MAX_SNAP_LAYER_VERTICES = 500_000;

/** Segments (6 numbers each) of polylines; a closed one gets its closing segment unless it repeats its first vertex. */
export function polylineSegments(xyz: Float64Array, offsets: ArrayLike<number>, closed: ArrayLike<number>): Float64Array {
  const lines = Math.max(0, offsets.length - 1);
  let n = 0;
  for (let l = 0; l < lines; l++) {
    const c = offsets[l + 1] - offsets[l];
    if (c >= 2) n += c - 1 + (closed[l] && c >= 3 && !repeats(xyz, offsets[l], offsets[l + 1] - 1) ? 1 : 0);
  }
  const out = new Float64Array(n * 6);
  let k = 0;
  for (let l = 0; l < lines; l++) {
    const a = offsets[l];
    const b = offsets[l + 1];
    if (b - a < 2) continue;
    for (let i = a; i < b - 1; i++, k += 6) out.set(xyz.subarray(i * 3, i * 3 + 6), k);
    if (closed[l] && b - a >= 3 && !repeats(xyz, a, b - 1)) {
      out.set(xyz.subarray((b - 1) * 3, b * 3), k);
      out.set(xyz.subarray(a * 3, a * 3 + 3), k + 3);
      k += 6;
    }
  }
  return out;
}

function repeats(xyz: Float64Array, i: number, j: number): boolean {
  return Object.is(xyz[i * 3], xyz[j * 3]) && Object.is(xyz[i * 3 + 1], xyz[j * 3 + 1]) && Object.is(xyz[i * 3 + 2], xyz[j * 3 + 2]);
}

/**
 * The working copy as targets: its vertices, the nearest point of its segments and their midpoints.
 * `exclude` leaves out a vertex being dragged with its two segments, or a whole line (`index` omitted).
 */
export function layerSnapSource(layer: EditLayer, key = "edit", exclude?: { line: number; index?: number } | VertexRef): SnapSource {
  let nv = 0;
  let ns = 0;
  for (const line of layer.lines) {
    const n = line.points.length;
    nv += n;
    ns += Math.max(0, line.closed && n >= 3 ? n : n - 1);
  }
  const pts = new Float64Array(nv * 3);
  const seg = new Float64Array(ns * 6);
  let p = 0;
  let q = 0;
  for (const line of layer.lines) {
    const own = exclude && exclude.line === line.id;
    if (own && exclude.index === undefined) continue;
    const pp = line.points;
    const n = pp.length;
    for (let i = 0; i < n; i++) {
      if (own && i === exclude.index) continue;
      pts[p++] = pp[i][0];
      pts[p++] = pp[i][1];
      pts[p++] = pp[i][2];
    }
    const segs = line.closed && n >= 3 ? n : n - 1;
    for (let k = 0; k < segs; k++) {
      const j = k + 1 < n ? k + 1 : 0;
      if (own && (k === exclude.index || j === exclude.index)) continue;
      const a = pp[k];
      const b = pp[j];
      seg[q++] = a[0];
      seg[q++] = a[1];
      seg[q++] = a[2];
      seg[q++] = b[0];
      seg[q++] = b[1];
      seg[q++] = b[2];
    }
  }
  return {
    key,
    points: { kind: "vertex", xyz: pts.subarray(0, p) },
    segments: { kind: "nearest", xyz: seg.subarray(0, q), midpoints: true },
  };
}

/** Another visible line layer (`GET /lines/layers/{id}/geometry`, its stored form); null above the limit. */
export function linesSnapSource(key: string, xyz: Float64Array, offsets: ArrayLike<number>, closed: ArrayLike<number>): SnapSource | null {
  if (xyz.length / 3 > MAX_SNAP_LAYER_VERTICES) return null;
  return { key, points: { kind: "vertex", xyz }, segments: { kind: "nearest", xyz: polylineSegments(xyz, offsets, closed), midpoints: true } };
}

/** The section of a wireframe (`GET /viz/wireframes/{id}/section`, BFA1 `section`). */
export function wireframeSectionSource(key: string, xyz: Float64Array, offsets: ArrayLike<number>, closed: ArrayLike<number>): SnapSource {
  return { key, segments: { kind: "wf-section", xyz: polylineSegments(xyz, offsets, closed) } };
}

/** The exact triangle under the cursor (`POST /wireframes/pick`): its vertices and its edges. */
export function wireframeTriangleSource(key: string, tri: ArrayLike<number>): SnapSource {
  const v = Float64Array.from({ length: 9 }, (_, i) => tri[i]);
  const e = new Float64Array(18);
  for (let k = 0; k < 3; k++) {
    const j = (k + 1) % 3;
    e.set(v.subarray(k * 3, k * 3 + 3), k * 6);
    e.set(v.subarray(j * 3, j * 3 + 3), k * 6 + 3);
  }
  return { key, points: { kind: "wf-vertex", xyz: v }, segments: { kind: "wf-edge", xyz: e } };
}

export interface SetSnap {
  sources: SnapSource[];
  holes: number;
  /** too many holes or points: collars only (the snap panel says so) */
  truncated: boolean;
}

const depthText = (d: number): string => (Number.isFinite(d) ? `${Number(d.toFixed(3))} м` : "—");

/** The targets of a drillhole set: BFA1 `snap` of `GET /viz/sets/{id}/snap` — collars, traces, interval bounds. */
export function readSetSnap(key: string, data: ArrayBufferLike | ArrayBufferView): SetSnap {
  const { header, arrays } = decodeBfa1<Record<string, unknown>>(data);
  const collars = arrays.collars;
  if (header.kind !== "snap" || !(collars instanceof Float64Array) || collars.length % 3) {
    throw new EditError("bad_snap", "Цели привязки скважин повреждены.", "Обновите вид. Если ошибка повторяется — сообщите разработчику.");
  }
  const names = Array.isArray(header.names) ? (header.names as unknown[]).map(String) : [];
  const name = (i: number): string => names[i] ?? String(i + 1);
  const sources: SnapSource[] = [{ key: `${key}:collars`, points: { kind: "collar", xyz: collars, labels: (i) => `устье ${name(i)}` } }];
  const { trace, trace_offsets: offsets, bounds, bound_hole: hole, bound_depth: depth } = arrays;
  if (trace instanceof Float64Array && offsets instanceof Uint32Array) {
    sources.push({ key: `${key}:trace`, segments: { kind: "trace", xyz: polylineSegments(trace, offsets, new Uint8Array(offsets.length)) } });
  }
  if (bounds instanceof Float64Array && hole instanceof Uint32Array && depth instanceof Float64Array) {
    sources.push({ key: `${key}:bounds`, points: { kind: "interval", xyz: bounds, labels: (i) => `${name(hole[i])}: ${depthText(depth[i])}` } });
  }
  return { sources, holes: Number(header.holes ?? collars.length / 3), truncated: header.truncated === true };
}

/** Kinds whose targets lie off the section plane and are projected into it when the switch is on (ADR 0015 §4). */
export const PROJECTED_KINDS: ReadonlySet<SnapKind> = new Set<SnapKind>(["collar", "trace", "interval", "wf-vertex", "wf-edge", "wf-section"]);
