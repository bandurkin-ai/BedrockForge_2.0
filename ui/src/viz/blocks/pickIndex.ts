/**
 * The index of the faces for a click (ADR 0023 §11 «Выбор»): axis → sorted planes → 2D buckets. The answer of the
 * core is sorted by (side, plane, class, v, u), so the faces of one plane are one run of the arrays; a run is found
 * in one pass over `a` and the coordinate of the plane (an answer in another order still works, it only has more
 * runs). A run of more than `LINEAR` faces gets a grid of buckets over its extent (about two faces a bucket); a face
 * that covers more than `SPREAD` buckets (a big merged rectangle) is kept apart and tried in every bucket query.
 * A ray meets one plane of a run at one point — a bucket gives the few faces to test; the plane is rejected at once
 * when that point is outside the extent of the run. The index lives in the frame of `c` (the local axes minus
 * `origin`, float64 on the CPU) and is built where the answer is decoded (a Worker); it is a plain object over the
 * decoded arrays, nothing of three.js.
 *
 * `ray` gives the point on the face and its normal: the row of the block comes from `POST /viz/blocks/{id}/at`
 * (float64 in the core, a step inside along the normal) — the answer has no rows.
 */
import { FACE_UV, type BlocksData } from "./decode";

/** A run of at most this many faces is scanned, not bucketed. */
export const LINEAR = 32;
/** A face that covers more buckets than this is kept in the list of the big ones of its run. */
export const SPREAD = 16;
const MAX_CELLS = 65536;
const MAX_SIDE = 1024;
/** metres: a hair for a ray through a joint of two faces (half sizes are float32) */
const TOL = 1e-6;

export type Vec3 = [number, number, number];

interface Run {
  /** 2·axis + side of every face of the run */
  a: number;
  /** the coordinate of the plane along the axis, frame of `c` */
  plane: number;
  start: number;
  end: number;
  u0: number;
  u1: number;
  v0: number;
  v1: number;
  gu: number;
  gv: number;
  cu: number;
  cv: number;
  /** buckets of the grid: `cellStart[b]…cellStart[b + 1]` in `items`; null — scan the run */
  cellStart: Uint32Array | null;
  items: Uint32Array | null;
  /** the faces that spread over many buckets */
  big: Uint32Array | null;
}

export interface FaceHit {
  /** the index of the face in the arrays of the answer */
  face: number;
  /** the parameter along the ray (the units of its direction) */
  t: number;
  /** the point on the face, the frame of `c` (local axes minus `origin`), float64 */
  point: Vec3;
  /** the unit normal out of the block (the local axes): −axis for side 0, +axis for side 1 */
  normal: Vec3;
  /** the side `a` and the class `k` of the face */
  a: number;
  k: number;
}

/** The ray hit is taken only where this says yes (the point is inside the section window, the layer is visible…). */
export type Accept = (point: Vec3) => boolean;

export interface IndexStats {
  faces: number;
  runs: number;
  bucketed: number;
  buckets: number;
  items: number;
  bigFaces: number;
  bytes: number;
}

export class PickIndex {
  private constructor(
    private readonly d: Pick<BlocksData, "c" | "h" | "a" | "k" | "faces">,
    private readonly runs: Run[],
  ) {}

  /** One pass for the runs, one more over every run with a grid (two: count, fill). */
  static build(d: Pick<BlocksData, "c" | "h" | "a" | "k" | "faces">): PickIndex {
    const { c, a, faces: n } = d;
    const runs: Run[] = [];
    for (let i = 0; i < n; ) {
      const side = a[i];
      const axis = side >> 1;
      const plane = c[3 * i + axis];
      let j = i + 1;
      while (j < n && a[j] === side && c[3 * j + axis] === plane) j++;
      runs.push(makeRun(d, i, j, side, plane));
      i = j;
    }
    return new PickIndex(d, runs);
  }

  get faces(): number {
    return this.d.faces;
  }

  stats(): IndexStats {
    let bucketed = 0;
    let buckets = 0;
    let items = 0;
    let bigFaces = 0;
    let bytes = 0;
    for (const r of this.runs) {
      if (!r.cellStart || !r.items) continue;
      bucketed++;
      buckets += r.cellStart.length - 1;
      items += r.items.length;
      bigFaces += r.big?.length ?? 0;
      bytes += r.cellStart.byteLength + r.items.byteLength + (r.big?.byteLength ?? 0);
    }
    return { faces: this.d.faces, runs: this.runs.length, bucketed, buckets, items, bigFaces, bytes };
  }

  /**
   * The nearest face the ray meets (t > 0), or null. `o`, `dir` — the ray in the frame of `c`, float64; `dir` need
   * not be a unit vector. Faces whose point `accept` refuses are passed over.
   */
  ray(o: Vec3, dir: Vec3, accept?: Accept): FaceHit | null {
    let best = Infinity;
    let face = -1;
    const p: Vec3 = [0, 0, 0];
    const bestPoint: Vec3 = [0, 0, 0];
    const { c, h } = this.d;
    for (const r of this.runs) {
      const axis = r.a >> 1;
      const den = dir[axis];
      if (Math.abs(den) < 1e-15) continue;
      const t = (r.plane - o[axis]) / den;
      if (!(t > 0) || t >= best) continue;
      const [u, v] = FACE_UV[axis];
      const pu = o[u] + t * dir[u];
      const pv = o[v] + t * dir[v];
      if (pu < r.u0 - TOL || pu > r.u1 + TOL || pv < r.v0 - TOL || pv > r.v1 + TOL) continue;
      p[axis] = r.plane;
      p[u] = pu;
      p[v] = pv;
      const test = (f: number): void => {
        if (Math.abs(pu - c[3 * f + u]) > h[2 * f] + TOL || Math.abs(pv - c[3 * f + v]) > h[2 * f + 1] + TOL) return;
        if (accept && !accept(p)) return;
        best = t;
        face = f;
        bestPoint[0] = p[0];
        bestPoint[1] = p[1];
        bestPoint[2] = p[2];
      };
      if (!r.cellStart || !r.items) {
        for (let f = r.start; f < r.end; f++) {
          test(f);
          if (face >= 0 && best === t) break;
        }
        continue;
      }
      const iu = cellOf(pu, r.u0, r.cu, r.gu);
      const iv = cellOf(pv, r.v0, r.cv, r.gv);
      const b = iu + r.gu * iv;
      for (let q = r.cellStart[b]; q < r.cellStart[b + 1]; q++) {
        test(r.items[q]);
        if (best === t) break;
      }
      if (best === t) continue;
      if (r.big) for (const f of r.big) {
        test(f);
        if (best === t) break;
      }
    }
    if (face < 0) return null;
    const side = this.d.a[face];
    const axis = side >> 1;
    const normal: Vec3 = [0, 0, 0];
    normal[axis] = side & 1 ? 1 : -1;
    return { face, t: best, point: bestPoint, normal, a: side, k: this.d.k[face] };
  }
}

function cellOf(x: number, lo: number, size: number, count: number): number {
  const i = Math.floor((x - lo) / size);
  return i < 0 ? 0 : i >= count ? count - 1 : i;
}

function makeRun(d: Pick<BlocksData, "c" | "h" | "a">, start: number, end: number, a: number, plane: number): Run {
  const { c, h } = d;
  const [u, v] = FACE_UV[a >> 1];
  let u0 = Infinity;
  let u1 = -Infinity;
  let v0 = Infinity;
  let v1 = -Infinity;
  for (let f = start; f < end; f++) {
    const cu = c[3 * f + u];
    const cv = c[3 * f + v];
    const hu = h[2 * f];
    const hv = h[2 * f + 1];
    if (cu - hu < u0) u0 = cu - hu;
    if (cu + hu > u1) u1 = cu + hu;
    if (cv - hv < v0) v0 = cv - hv;
    if (cv + hv > v1) v1 = cv + hv;
  }
  const run: Run = { a, plane, start, end, u0, u1, v0, v1, gu: 1, gv: 1, cu: 1, cv: 1, cellStart: null, items: null, big: null };
  const n = end - start;
  if (n <= LINEAR) return run;
  const U = Math.max(u1 - u0, 1e-9);
  const V = Math.max(v1 - v0, 1e-9);
  const cells = Math.min(MAX_CELLS, Math.max(4, n >> 1));
  run.gu = Math.min(MAX_SIDE, Math.max(1, Math.round(Math.sqrt((cells * U) / V))));
  run.gv = Math.min(MAX_SIDE, Math.max(1, Math.ceil(cells / run.gu)));
  run.cu = U / run.gu;
  run.cv = V / run.gv;
  const total = run.gu * run.gv;
  const cellStart = new Uint32Array(total + 1);
  const big: number[] = [];
  const span = (f: number): [number, number, number, number] => [
    cellOf(c[3 * f + u] - h[2 * f], u0, run.cu, run.gu),
    cellOf(c[3 * f + u] + h[2 * f], u0, run.cu, run.gu),
    cellOf(c[3 * f + v] - h[2 * f + 1], v0, run.cv, run.gv),
    cellOf(c[3 * f + v] + h[2 * f + 1], v0, run.cv, run.gv),
  ];
  for (let f = start; f < end; f++) {
    const [a0, a1, b0, b1] = span(f);
    if ((a1 - a0 + 1) * (b1 - b0 + 1) > SPREAD) {
      big.push(f);
      continue;
    }
    for (let j = b0; j <= b1; j++) for (let i = a0; i <= a1; i++) cellStart[i + run.gu * j + 1]++;
  }
  for (let b = 0; b < total; b++) cellStart[b + 1] += cellStart[b];
  const items = new Uint32Array(cellStart[total]);
  const cursor = cellStart.slice(0, total);
  for (let f = start; f < end; f++) {
    const [a0, a1, b0, b1] = span(f);
    if ((a1 - a0 + 1) * (b1 - b0 + 1) > SPREAD) continue;
    for (let j = b0; j <= b1; j++) for (let i = a0; i <= a1; i++) items[cursor[i + run.gu * j]++] = f;
  }
  run.cellStart = cellStart;
  run.items = items;
  run.big = big.length ? Uint32Array.from(big) : null;
  return run;
}
