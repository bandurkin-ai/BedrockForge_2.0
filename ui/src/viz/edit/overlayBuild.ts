/**
 * Buffers of the editing layer (ADR 0015 §5), without three.js: the lines of the working copy as segments in
 * one float32 buffer from an integer origin (`viz/origin.ts`), a range per line — a changed line with as many
 * segments is rewritten in place, anything else rebuilds the buffer; the vertex marks of the selected lines and
 * of those near the cursor (≤ 20 000); the rubber band; the box of the layer. A vertex without a height is
 * drawn at `missingZ` (the Z of the panel) and keeps its NaN in the working copy.
 */
import type { EditLayer, EditLine, Selection, Vec3 } from "../../edit/model";
import type { Origin } from "../origin";

/** Vertex marks drawn at most (ADR 0015 §5). */
export const MAX_MARKS = 20_000;
/** A layer that grew this far from the origin of its buffers gets a new origin (float32 keeps < 0.5 mm). */
export const ORIGIN_REACH_M = 4000;

/** Segments a line draws: none for a single point; a closed line of 3+ vertices draws its closing segment. */
export function segmentCount(line: Pick<EditLine, "points" | "closed">): number {
  const n = line.points.length;
  if (n < 2) return 0;
  return line.closed && n > 2 ? n : n - 1;
}

const zOf = (z: number, missingZ: number): number => (Number.isFinite(z) ? z : missingZ);

function put(out: Float32Array, at: number, p: Vec3, o: Origin, missingZ: number): void {
  out[at] = p[0] - o.x;
  out[at + 1] = p[1] - o.y;
  out[at + 2] = zOf(p[2], missingZ) - o.z;
}

/** Writes the segments of a line from float `at`; returns the float after them. */
export function writeLine(out: Float32Array, at: number, line: Pick<EditLine, "points" | "closed">, o: Origin, missingZ: number): number {
  const pts = line.points;
  const segs = segmentCount(line);
  for (let k = 0; k < segs; k++) {
    put(out, at, pts[k], o, missingZ);
    put(out, at + 3, pts[(k + 1) % pts.length], o, missingZ);
    at += 6;
  }
  return at;
}

interface Slot {
  line: EditLine;
  /** first float of the line */
  at: number;
  segments: number;
}

/** What an update wrote: everything, or float ranges [start, count]. */
export interface Written {
  all: boolean;
  ranges: [number, number][];
}

/** Segments of a list of lines in one buffer; `lengths` — for dashed lines, the distance along each segment. */
export class SegmentBuffer {
  positions = new Float32Array(0);
  lengths: Float32Array | null = null;
  /** segments in use */
  count = 0;
  private slots: Slot[] = [];
  private origin: Origin | null = null;
  private missingZ = NaN;

  constructor(private readonly dashed = false) {}

  update(lines: readonly EditLine[], origin: Origin, missingZ: number): Written {
    const same = this.origin === origin && Object.is(this.missingZ, missingZ) && lines.length === this.slots.length;
    if (same) {
      const ranges: [number, number][] = [];
      for (let i = 0; i < lines.length; i++) {
        const slot = this.slots[i];
        const line = lines[i];
        if (slot.line === line) continue;
        if (segmentCount(line) !== slot.segments) return this.rebuild(lines, origin, missingZ);
        writeLine(this.positions, slot.at, line, origin, missingZ);
        if (this.lengths) this.writeLengths(slot.at / 3, line);
        slot.line = line;
        if (slot.segments) ranges.push([slot.at, slot.segments * 6]);
      }
      return { all: false, ranges };
    }
    return this.rebuild(lines, origin, missingZ);
  }

  private rebuild(lines: readonly EditLine[], origin: Origin, missingZ: number): Written {
    let total = 0;
    for (const l of lines) total += segmentCount(l);
    if (total * 6 > this.positions.length) {
      // room to grow: a few new lines do not rebuild the GPU buffer
      const cap = Math.ceil(total * 1.25) + 64;
      this.positions = new Float32Array(cap * 6);
      if (this.dashed) this.lengths = new Float32Array(cap * 2);
    }
    this.slots = [];
    let at = 0;
    for (const line of lines) {
      const segments = segmentCount(line);
      this.slots.push({ line, at, segments });
      writeLine(this.positions, at, line, origin, missingZ);
      if (this.lengths) this.writeLengths(at / 3, line);
      at += segments * 6;
    }
    this.count = total;
    this.origin = origin;
    this.missingZ = missingZ;
    return { all: true, ranges: [[0, total * 6]] };
  }

  /** A dash pattern restarts at every segment: 0 at its start, its length at its end. */
  private writeLengths(vertex: number, line: EditLine): void {
    const out = this.lengths as Float32Array;
    const pts = line.points;
    const segs = segmentCount(line);
    for (let k = 0; k < segs; k++) {
      const a = pts[k];
      const b = pts[(k + 1) % pts.length];
      const dz = Number.isFinite(a[2]) && Number.isFinite(b[2]) ? b[2] - a[2] : 0;
      out[vertex + 2 * k] = 0;
      out[vertex + 2 * k + 1] = Math.hypot(b[0] - a[0], b[1] - a[1], dz);
    }
  }
}

/** Is the line a tie line (`ROLE = TIE`): drawn dashed. */
export const isTie = (line: EditLine): boolean => line.attrs.ROLE === "TIE";

/** Points as float32 from the origin. */
export function pointsOf(points: readonly Vec3[], o: Origin, missingZ: number): Float32Array {
  const out = new Float32Array(points.length * 3);
  points.forEach((p, i) => put(out, i * 3, p, o, missingZ));
  return out;
}

/** Lines whose vertices are marked: the selected ones, those of selected vertices, the one being drawn, those near the cursor. */
export function markedLines(selection: Selection, drawing: number | null, near: readonly number[]): Set<number> {
  const ids = new Set<number>(selection.lines);
  for (const v of selection.vertices) ids.add(v.line);
  if (drawing !== null) ids.add(drawing);
  for (const id of near) ids.add(id);
  return ids;
}

/** The vertices of the marked lines, at most `limit` (the selected lines come first, in the layer order). */
export function vertexMarks(layer: EditLayer, ids: ReadonlySet<number>, o: Origin, missingZ: number, limit = MAX_MARKS): Float32Array {
  const pts: Vec3[] = [];
  for (const line of layer.lines) {
    if (!ids.has(line.id)) continue;
    for (const p of line.points) {
      if (pts.length >= limit) return pointsOf(pts, o, missingZ);
      pts.push(p);
    }
  }
  return pointsOf(pts, o, missingZ);
}

/** The rubber band: from the growing end of the line being drawn to the cursor, and on to its start for a polygon. */
export function draftPoints(layer: EditLayer, drawing: { line: number; atStart: boolean } | null, polygon: boolean, cursor: Vec3 | null): Vec3[] {
  const line = drawing ? layer.lines.find((l) => l.id === drawing.line) : undefined;
  if (!drawing || !line || !cursor || !line.points.length) return [];
  const end = line.points[drawing.atStart ? 0 : line.points.length - 1];
  const out: Vec3[] = [end, cursor];
  if (polygon && line.points.length > 1) out.push(cursor, line.points[drawing.atStart ? line.points.length - 1 : 0]);
  return out;
}

/** [xmin, ymin, zmin, xmax, ymax, zmax] of the layer (heights of the vertices that have one; none — `missingZ`), or null. */
export function layerBbox(layer: EditLayer, missingZ: number): number[] | null {
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (const line of layer.lines) {
    for (const p of line.points) {
      const z = zOf(p[2], missingZ);
      const v = [p[0], p[1], z];
      for (let k = 0; k < 3; k++) {
        if (!Number.isFinite(v[k])) continue;
        if (v[k] < lo[k]) lo[k] = v[k];
        if (v[k] > hi[k]) hi[k] = v[k];
      }
    }
  }
  if (!(lo[0] <= hi[0] && lo[1] <= hi[1])) return null;
  if (!(lo[2] <= hi[2])) lo[2] = hi[2] = Number.isFinite(missingZ) ? missingZ : 0;
  return [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]];
}

/** The integer centre of a box. */
export function boxOrigin(b: ArrayLike<number>): Origin {
  return { x: Math.round((b[0] + b[3]) / 2), y: Math.round((b[1] + b[4]) / 2), z: Math.round((b[2] + b[5]) / 2) };
}

/** Does the box reach further than `ORIGIN_REACH_M` from the origin (then float32 buffers would lose precision). */
export function outOfReach(o: Origin, b: ArrayLike<number> | null): boolean {
  if (!b) return false;
  const far = (lo: number, hi: number, c: number) => Math.max(Math.abs(lo - c), Math.abs(hi - c)) > ORIGIN_REACH_M;
  return far(b[0], b[3], o.x) || far(b[1], b[4], o.y) || far(b[2], b[5], o.z);
}
