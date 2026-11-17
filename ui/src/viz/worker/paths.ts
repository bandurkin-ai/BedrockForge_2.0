/**
 * Line buffers of the hole traces of one part (ADR 0007 §1, §7), built in the Worker from the BFA1 `paths`
 * answer (`core/bf_core/viz/paths.py`): `offsets u4` (n + 1), `xyz f4×3` from the part origin, `md f8`.
 * Segments are grouped by the kind of hole — 0 holes, 1 trenches (their own colour), 2 design holes
 * (dashed: cumulative lengths per segment end, as `computeLineDistances` lays them out) — with the slot of
 * every segment for picking. The polyline itself is kept for the selected hole, the section through a hole
 * and the mini-plan. Pure: no three.js, no DOM.
 */
import { bfa1Array, decodeBfa1 } from "../format/bfa1";

export const KIND_HOLE = 0;
export const KIND_TRENCH = 1;
export const KIND_DESIGN = 2;

export interface PathGroup {
  kind: number;
  /** 6 floats per segment: start xyz, end xyz (part origin, float32) */
  positions: Float32Array;
  /** slot of the hole of every segment */
  slots: Uint16Array;
  /** 2 per segment: cumulative length at its start and end (design holes only) */
  distances: Float32Array | null;
}

export interface BuiltPaths {
  k: number;
  n: number;
  points: number;
  segments: number;
  origin: [number, number, number];
  bbox: number[];
  offsets: Uint32Array;
  xyz: Float32Array;
  md: Float64Array;
  groups: PathGroup[];
}

interface Header {
  k: number;
  n: number;
  points: number;
  origin: [number, number, number];
  bbox: number[];
}

/** `kinds[slot]` — the kind of each hole of the part (manifest); null — all ordinary holes. */
export function buildPaths(buffer: ArrayBuffer, kinds: ArrayLike<number> | null): BuiltPaths {
  const doc = decodeBfa1<Header>(buffer);
  const h = doc.header;
  const offsets = bfa1Array(doc, "offsets", "<u4", h.n + 1);
  const xyz = bfa1Array(doc, "xyz", "<f4");
  const md = bfa1Array(doc, "md", "<f8");
  const points = md.length;
  if (xyz.length !== points * 3 || offsets[h.n] !== points) throw new Error(`paths of part ${h.k}: offsets and points disagree`);
  const kindOf = (slot: number) => {
    const k = kinds ? kinds[slot] : KIND_HOLE;
    return k === KIND_TRENCH || k === KIND_DESIGN ? k : KIND_HOLE;
  };
  const count = [0, 0, 0];
  for (let s = 0; s < h.n; s++) count[kindOf(s)] += Math.max(0, offsets[s + 1] - offsets[s] - 1);
  const groups: PathGroup[] = [];
  const byKind: (PathGroup | null)[] = [null, null, null];
  for (const kind of [KIND_HOLE, KIND_TRENCH, KIND_DESIGN]) {
    if (!count[kind]) continue;
    const g: PathGroup = {
      kind,
      positions: new Float32Array(count[kind] * 6),
      slots: new Uint16Array(count[kind]),
      distances: kind === KIND_DESIGN ? new Float32Array(count[kind] * 2) : null,
    };
    groups.push(g);
    byKind[kind] = g;
  }
  const fill = [0, 0, 0];
  const run = [0, 0, 0];
  for (let s = 0; s < h.n; s++) {
    const kind = kindOf(s);
    const g = byKind[kind];
    if (!g) continue;
    for (let p = offsets[s]; p + 1 < offsets[s + 1]; p++) {
      const at = fill[kind]++;
      g.positions.set(xyz.subarray(p * 3, p * 3 + 6), at * 6);
      g.slots[at] = s;
      if (g.distances) {
        const len = Math.hypot(xyz[p * 3 + 3] - xyz[p * 3], xyz[p * 3 + 4] - xyz[p * 3 + 1], xyz[p * 3 + 5] - xyz[p * 3 + 2]);
        g.distances[at * 2] = run[kind];
        run[kind] += len;
        g.distances[at * 2 + 1] = run[kind];
      }
    }
  }
  return { k: h.k, n: h.n, points, segments: count[0] + count[1] + count[2], origin: h.origin, bbox: h.bbox, offsets, xyz, md, groups };
}

/** The buffers to transfer back to the main thread, each once. */
export function transferables(b: BuiltPaths): ArrayBuffer[] {
  const set = new Set<ArrayBuffer>();
  const add = (a: ArrayBufferView | null) => {
    if (a && a.buffer instanceof ArrayBuffer) set.add(a.buffer);
  };
  add(b.offsets);
  add(b.xyz);
  add(b.md);
  for (const g of b.groups) {
    add(g.positions);
    add(g.slots);
    add(g.distances);
  }
  return [...set];
}

/** Vertices of slot `s` of a part: [first, end) into `xyz` / `md`. */
export function slotRange(b: Pick<BuiltPaths, "offsets">, s: number): [number, number] {
  return [b.offsets[s], b.offsets[s + 1]];
}
