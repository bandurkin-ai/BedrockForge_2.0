/**
 * Editing operations of the string editor (ADR 0015 §3; BF `edit/ops.ts`; the offset is in `offset.ts`).
 * All of them are pure: (layer, …) => a new layer that shares the untouched lines with the old one. An
 * operation that cannot be applied (unknown line, index out of range, nothing to change) returns the SAME
 * layer object, so History.push makes no step for it.
 *
 * `src` (the stored line a line comes from) follows the geometry: a copy, a part of a broken line and the
 * joined line keep it (the core carries the attributes by it and gives the second line a new `lid`); a new
 * line and a line put from another layer have none (−1). A vertex without a height (NaN Z) keeps it; where
 * a distance decides (Douglas–Peucker, densify, join), a missing height makes it a distance in plan.
 */
import { lineById, NEW_LINE } from "./model";
import type { AttrValue, EditLayer, EditLine, Vec3, VertexRef } from "./model";

export { DEFAULT_MITRE_LIMIT, offsetLine, offsetPoints } from "./offset";

const copy = (p: Vec3): Vec3 => [p[0], p[1], p[2]];
/** Bit-for-bit the same point (NaN equals NaN, as a vertex without a height). */
const same = (a: Vec3, b: Vec3): boolean => Object.is(a[0], b[0]) && Object.is(a[1], b[1]) && Object.is(a[2], b[2]);
/** No shift: zero, or not a shift at all (a non-finite component). */
const isZero = (d: Vec3): boolean => (d[0] === 0 && d[1] === 0 && d[2] === 0) || !d.every(Number.isFinite);
const hasZ = (p: Vec3): boolean => Number.isFinite(p[2]);
/** 3D distance; in plan when a height is missing. */
const dist = (a: Vec3, b: Vec3): number =>
  hasZ(a) && hasZ(b) ? Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) : Math.hypot(a[0] - b[0], a[1] - b[1]);

/** Replaces one line (or removes it when `fn` returns null); the same layer when `fn` returns the same line. */
function mapLine(layer: EditLayer, id: number, fn: (line: EditLine) => EditLine | null): EditLayer {
  const at = layer.lines.findIndex((l) => l.id === id);
  if (at < 0) return layer;
  const next = fn(layer.lines[at]);
  if (next === layer.lines[at]) return layer;
  const lines = layer.lines.slice();
  if (next) lines[at] = next;
  else lines.splice(at, 1);
  return { lines, nextId: layer.nextId };
}

/** A new line. A closed line needs at least 3 points (fewer: it is stored open); no points: nothing is added and id is -1. */
export function addLine(
  layer: EditLayer,
  points: Vec3[],
  closed = false,
  attrs: Record<string, AttrValue> = {},
): { layer: EditLayer; id: number } {
  if (points.length === 0) return { layer, id: -1 };
  const id = layer.nextId;
  const line: EditLine = { id, points: points.map(copy), closed: closed && points.length >= 3, attrs: { ...attrs }, src: NEW_LINE };
  return { layer: { lines: [...layer.lines, line], nextId: id + 1 }, id };
}

/** Continues a line from its end (or from its start). */
export function appendPoint(layer: EditLayer, lineId: number, p: Vec3, atStart = false): EditLayer {
  return mapLine(layer, lineId, (line) => ({ ...line, points: atStart ? [copy(p), ...line.points] : [...line.points, copy(p)] }));
}

/** Closing needs at least 3 points. */
export function setClosed(layer: EditLayer, lineId: number, closed: boolean): EditLayer {
  return mapLine(layer, lineId, (line) => {
    if (line.closed === closed || (closed && line.points.length < 3)) return line;
    return { ...line, closed };
  });
}

export function moveVertex(layer: EditLayer, ref: VertexRef, p: Vec3): EditLayer {
  return mapLine(layer, ref.line, (line) => {
    const old = line.points[ref.index];
    if (!old || same(old, p)) return line;
    const points = line.points.slice();
    points[ref.index] = copy(p);
    return { ...line, points };
  });
}

function groupByLine(refs: VertexRef[]): Map<number, Set<number>> {
  const map = new Map<number, Set<number>>();
  for (const r of refs) {
    let set = map.get(r.line);
    if (!set) map.set(r.line, (set = new Set()));
    set.add(r.index);
  }
  return map;
}

export function moveVertices(layer: EditLayer, refs: VertexRef[], delta: Vec3): EditLayer {
  if (refs.length === 0 || isZero(delta)) return layer;
  const byLine = groupByLine(refs);
  let changed = false;
  const lines = layer.lines.map((line) => {
    const set = byLine.get(line.id);
    if (!set) return line;
    let touched = false;
    const points = line.points.map((p, i): Vec3 => {
      if (!set.has(i)) return p;
      touched = true;
      return [p[0] + delta[0], p[1] + delta[1], p[2] + delta[2]];
    });
    if (!touched) return line;
    changed = true;
    return { ...line, points };
  });
  return changed ? { lines, nextId: layer.nextId } : layer;
}

/**
 * Inserts a vertex after point `segment`: segment k joins points k and k + 1, and for a closed line
 * the last segment joins the last point and the first one.
 */
export function insertVertex(layer: EditLayer, lineId: number, segment: number, p: Vec3): EditLayer {
  return mapLine(layer, lineId, (line) => {
    const n = line.points.length;
    const segments = line.closed ? n : n - 1;
    if (!Number.isInteger(segment) || segment < 0 || segment >= segments) return line;
    const points = line.points.slice();
    points.splice(segment + 1, 0, copy(p));
    return { ...line, points };
  });
}

/** A line left with no points disappears; a closed line left with fewer than 3 points opens. */
export function deleteVertices(layer: EditLayer, refs: VertexRef[]): EditLayer {
  if (refs.length === 0) return layer;
  const byLine = groupByLine(refs);
  let changed = false;
  const lines: EditLine[] = [];
  for (const line of layer.lines) {
    const set = byLine.get(line.id);
    if (!set) {
      lines.push(line);
      continue;
    }
    const points = line.points.filter((_, i) => !set.has(i));
    if (points.length === line.points.length) {
      lines.push(line);
      continue;
    }
    changed = true;
    if (points.length > 0) lines.push({ ...line, points, closed: line.closed && points.length >= 3 });
  }
  return changed ? { lines, nextId: layer.nextId } : layer;
}

/** Moves whole lines; with delta = [0, 0, dz] it is the "shift by Z". */
export function translateLines(layer: EditLayer, ids: number[], delta: Vec3): EditLayer {
  if (ids.length === 0 || isZero(delta)) return layer;
  const set = new Set(ids);
  let changed = false;
  const lines = layer.lines.map((line) => {
    if (!set.has(line.id)) return line;
    changed = true;
    return { ...line, points: line.points.map((p): Vec3 => [p[0] + delta[0], p[1] + delta[1], p[2] + delta[2]]) };
  });
  return changed ? { lines, nextId: layer.nextId } : layer;
}

/** Copies go to the end of the layer, in the order of the layer; `ids` are the new lines. */
export function copyLines(layer: EditLayer, ids: number[], delta: Vec3 = [0, 0, 0]): { layer: EditLayer; ids: number[] } {
  if (!delta.every(Number.isFinite)) return { layer, ids: [] };
  const set = new Set(ids);
  const added: EditLine[] = [];
  let nextId = layer.nextId;
  for (const line of layer.lines) {
    if (!set.has(line.id)) continue;
    added.push({
      id: nextId++,
      points: line.points.map((p): Vec3 => [p[0] + delta[0], p[1] + delta[1], p[2] + delta[2]]),
      closed: line.closed,
      attrs: { ...line.attrs },
      src: line.src,
    });
  }
  if (added.length === 0) return { layer, ids: [] };
  return { layer: { lines: [...layer.lines, ...added], nextId }, ids: added.map((l) => l.id) };
}

export function deleteLines(layer: EditLayer, ids: number[]): EditLayer {
  if (ids.length === 0) return layer;
  const set = new Set(ids);
  const lines = layer.lines.filter((l) => !set.has(l.id));
  return lines.length === layer.lines.length ? layer : { lines, nextId: layer.nextId };
}

/**
 * Breaks a line at a vertex. An open line becomes two lines sharing that vertex (the first keeps the id,
 * the second follows it in the layer); a closed line becomes one open line that starts and ends at
 * that vertex. Breaking at an end of an open line changes nothing.
 */
export function breakAt(layer: EditLayer, ref: VertexRef): { layer: EditLayer; ids: number[] } {
  const at = layer.lines.findIndex((l) => l.id === ref.line);
  if (at < 0) return { layer, ids: [] };
  const line = layer.lines[at];
  const n = line.points.length;
  const i = ref.index;
  if (!Number.isInteger(i) || i < 0 || i >= n) return { layer, ids: [line.id] };
  const lines = layer.lines.slice();
  if (line.closed) {
    const points = [...line.points.slice(i), ...line.points.slice(0, i), copy(line.points[i])];
    lines[at] = { ...line, points, closed: false };
    return { layer: { lines, nextId: layer.nextId }, ids: [line.id] };
  }
  if (i === 0 || i === n - 1) return { layer, ids: [line.id] };
  const id = layer.nextId;
  lines[at] = { ...line, points: line.points.slice(0, i + 1) };
  lines.splice(at + 1, 0, { id, points: [copy(line.points[i]), ...line.points.slice(i + 1)], closed: false, attrs: { ...line.attrs }, src: line.src });
  return { layer: { lines, nextId: id + 1 }, ids: [line.id, id] };
}

/**
 * Joins two open lines by their nearest ends. The direction of `a` is kept, `b` is reversed when
 * needed; the result keeps the id, the place and the attributes of `a` (keys only `b` has are added).
 * End points closer than `tolerance` (0: exactly equal) are not duplicated. Null when the lines
 * cannot be joined (the same line, a closed or an unknown one).
 */
export function joinLines(layer: EditLayer, a: number, b: number, tolerance = 0): { layer: EditLayer; id: number } | null {
  if (a === b) return null;
  const la = lineById(layer, a);
  const lb = lineById(layer, b);
  if (!la || !lb || la.closed || lb.closed || la.points.length === 0 || lb.points.length === 0) return null;
  const pa = la.points;
  const pb = lb.points;
  const a0 = pa[0];
  const a1 = pa[pa.length - 1];
  const b0 = pb[0];
  const b1 = pb[pb.length - 1];
  // [gap, b goes after a, b reversed]
  const options: [number, boolean, boolean][] = [
    [dist(a1, b0), true, false],
    [dist(a1, b1), true, true],
    [dist(a0, b1), false, false],
    [dist(a0, b0), false, true],
  ];
  let best = options[0];
  for (const o of options) if (o[0] < best[0]) best = o;
  const [gap, after, reversed] = best;
  const other = reversed ? pb.slice().reverse() : pb;
  const merge = gap <= tolerance;
  const points = after ? [...pa, ...(merge ? other.slice(1) : other)] : [...(merge ? other.slice(0, -1) : other), ...pa];
  const joined: EditLine = { id: a, points, closed: false, attrs: { ...lb.attrs, ...la.attrs }, src: la.src };
  const lines: EditLine[] = [];
  for (const l of layer.lines) {
    if (l.id === a) lines.push(joined);
    else if (l.id !== b) lines.push(l);
  }
  return { layer: { lines, nextId: layer.nextId }, id: a };
}

/** A closed line keeps its first point. */
export function reverseLine(layer: EditLayer, lineId: number): EditLayer {
  return mapLine(layer, lineId, (line) => {
    if (line.points.length < 2) return line;
    const points = line.closed ? [line.points[0], ...line.points.slice(1).reverse()] : line.points.slice().reverse();
    return { ...line, points };
  });
}

/** Distance from p to the segment a–b in 3D; in plan when one of the three has no height. */
function segmentDistance(p: Vec3, a: Vec3, b: Vec3): number {
  const flat = !(hasZ(p) && hasZ(a) && hasZ(b));
  const ux = b[0] - a[0];
  const uy = b[1] - a[1];
  const uz = flat ? 0 : b[2] - a[2];
  const wx = p[0] - a[0];
  const wy = p[1] - a[1];
  const wz = flat ? 0 : p[2] - a[2];
  const uu = ux * ux + uy * uy + uz * uz;
  let t = uu > 0 ? (wx * ux + wy * uy + wz * uz) / uu : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return Math.hypot(wx - ux * t, wy - uy * t, wz - uz * t);
}

/** Douglas–Peucker over points[from..to] (indices into `points`); marks the points to keep. */
function douglasPeucker(points: Vec3[], from: number, to: number, tolerance: number, keep: Uint8Array): void {
  const stack: number[] = [from, to];
  keep[from] = 1;
  keep[to] = 1;
  while (stack.length) {
    const hi = stack.pop() as number;
    const lo = stack.pop() as number;
    let worst = -1;
    let far = tolerance;
    for (let i = lo + 1; i < hi; i++) {
      const dd = segmentDistance(points[i], points[lo], points[hi]);
      if (dd > far) {
        far = dd;
        worst = i;
      }
    }
    if (worst >= 0) {
      keep[worst] = 1;
      stack.push(lo, worst, worst, hi);
    }
  }
}

/**
 * Douglas–Peucker in 3D: drops the points closer than `tolerance` to the simplified line.
 * An open line keeps its end points; a closed line keeps its first point and the point farthest
 * from it, and never goes below 3 points.
 */
export function simplifyLine(layer: EditLayer, lineId: number, tolerance: number): EditLayer {
  if (!(tolerance >= 0)) return layer;
  return mapLine(layer, lineId, (line) => {
    const pts = line.points;
    const n = pts.length;
    if (n < 3 || (line.closed && n < 4)) return line;
    let points: Vec3[];
    if (!line.closed) {
      const keep = new Uint8Array(n);
      douglasPeucker(pts, 0, n - 1, tolerance, keep);
      points = pts.filter((_, i) => keep[i]);
    } else {
      // The ring is cut at the first point and at the point farthest from it.
      const ring = [...pts, pts[0]];
      let far = 0;
      let split = -1;
      for (let i = 1; i < n; i++) {
        const dd = dist(pts[i], pts[0]);
        if (dd > far) {
          far = dd;
          split = i;
        }
      }
      if (split < 0) return line;
      const keep = new Uint8Array(n + 1);
      douglasPeucker(ring, 0, split, tolerance, keep);
      douglasPeucker(ring, split, n, tolerance, keep);
      points = pts.filter((_, i) => keep[i]);
      if (points.length < 3) return line;
    }
    return points.length === n ? line : { ...line, points };
  });
}

/** Adds points so that no segment is longer than `step`; the original vertices stay bit-identical. */
export function densifyLine(layer: EditLayer, lineId: number, step: number): EditLayer {
  if (!(step > 0)) return layer;
  return mapLine(layer, lineId, (line) => {
    const pts = line.points;
    const n = pts.length;
    const segs = line.closed && n >= 3 ? n : n - 1;
    const points: Vec3[] = [];
    for (let k = 0; k < segs; k++) {
      const a = pts[k];
      const b = pts[(k + 1) % n];
      points.push(a);
      const len = dist(a, b);
      // Rounding must not turn an exact multiple of the step into one more piece.
      const pieces = Math.min(1_000_000, Math.ceil(len / step - 1e-9));
      for (let i = 1; i < pieces; i++) {
        const t = i / pieces;
        points.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
      }
    }
    if (segs === n - 1 && n > 0) points.push(pts[n - 1]);
    return points.length === n ? line : { ...line, points };
  });
}

/**
 * Chaikin corner cutting: every corner is replaced by two points at 1/4 and 3/4 of the adjacent
 * segments. An open line keeps its end points; a closed line stays closed.
 */
export function smoothLine(layer: EditLayer, lineId: number, iterations = 1): EditLayer {
  const times = Math.min(8, Math.floor(iterations));
  if (!(times >= 1)) return layer;
  return mapLine(layer, lineId, (line) => {
    if (line.points.length < 3) return line;
    const closed = line.closed;
    let pts = line.points;
    for (let it = 0; it < times; it++) {
      const n = pts.length;
      const segs = closed ? n : n - 1;
      const next: Vec3[] = [];
      if (!closed) next.push(pts[0]);
      for (let k = 0; k < segs; k++) {
        const a = pts[k];
        const b = pts[(k + 1) % n];
        if (closed || k > 0) next.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1], 0.75 * a[2] + 0.25 * b[2]]);
        if (closed || k < segs - 1) next.push([0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1], 0.25 * a[2] + 0.75 * b[2]]);
      }
      if (!closed) next.push(pts[n - 1]);
      pts = next;
    }
    return { ...line, points: pts };
  });
}

/** Sets attributes of several lines; `undefined` removes the key. */
export function setAttrs(layer: EditLayer, ids: number[], patch: Record<string, AttrValue | undefined>): EditLayer {
  const keys = Object.keys(patch);
  if (ids.length === 0 || keys.length === 0) return layer;
  const set = new Set(ids);
  let changed = false;
  const lines = layer.lines.map((line) => {
    if (!set.has(line.id)) return line;
    let attrs: Record<string, AttrValue> | null = null;
    for (const key of keys) {
      const value = patch[key];
      const has = Object.prototype.hasOwnProperty.call(line.attrs, key);
      if (value === undefined ? !has : has && line.attrs[key] === value) continue;
      attrs ??= { ...line.attrs };
      if (value === undefined) delete attrs[key];
      else attrs[key] = value;
    }
    if (!attrs) return line;
    changed = true;
    return { ...line, attrs };
  });
  return changed ? { lines, nextId: layer.nextId } : layer;
}

/** Takes lines out of a layer (to move them to another one); `taken` is in the order of the layer. Saving a move is `io.moveBody`. */
export function takeLines(layer: EditLayer, ids: number[]): { layer: EditLayer; taken: EditLine[] } {
  const set = new Set(ids);
  const taken = layer.lines.filter((l) => set.has(l.id));
  if (taken.length === 0) return { layer, taken };
  return { layer: { lines: layer.lines.filter((l) => !set.has(l.id)), nextId: layer.nextId }, taken };
}

/** Adds lines taken from another layer; they get new ids of this layer. */
export function putLines(layer: EditLayer, lines: EditLine[]): { layer: EditLayer; ids: number[] } {
  if (lines.length === 0) return { layer, ids: [] };
  let nextId = layer.nextId;
  const added = lines.map((l): EditLine => ({ id: nextId++, points: l.points, closed: l.closed, attrs: { ...l.attrs }, src: NEW_LINE }));
  return { layer: { lines: [...layer.lines, ...added], nextId }, ids: added.map((l) => l.id) };
}
