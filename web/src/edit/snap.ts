/**
 * Snapping targets with a screen-space spatial index.
 *
 * Candidates stay in project coordinates (float64, exactly as given); the index holds only their
 * screen positions for the current view: a uniform grid over the viewport, filled by a counting sort
 * into typed arrays. It is rebuilt lazily after the view or a source changes; a query looks at the
 * few cells under the cursor.
 */
import type { Plane, Ray } from "./coords";
import type { Vec3 } from "./model";

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
const RANK_OF: Record<SnapKind, number> = {
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
const KIND_CODE = new Map<SnapKind, number>(SNAP_KINDS.map((k, i) => [k, i]));
const KIND_RANK = Uint8Array.from(SNAP_KINDS.map((k) => RANK_OF[k]));
const MIDPOINT_CODE = KIND_CODE.get("midpoint") as number;

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

/** Fine grid cell, px. */
const CELL = 16;
/** A segment crossing more fine cells than this goes to the coarse grid. */
const FINE_SPAN = 12;
/** The coarse grid has at most this many cells along the longer side of the viewport. */
const COARSE_CELLS = 24;

/** Cells crossed by a segment that lies inside the grid (Amanatides–Woo); returns their number. */
function walkCells(x0: number, y0: number, x1: number, y1: number, cs: number, cols: number, rows: number, out: Int32Array): number {
  let cx = Math.min(cols - 1, Math.max(0, Math.floor(x0 / cs)));
  let cy = Math.min(rows - 1, Math.max(0, Math.floor(y0 / cs)));
  const ex = Math.min(cols - 1, Math.max(0, Math.floor(x1 / cs)));
  const ey = Math.min(rows - 1, Math.max(0, Math.floor(y1 / cs)));
  const stepX = ex > cx ? 1 : ex < cx ? -1 : 0;
  const stepY = ey > cy ? 1 : ey < cy ? -1 : 0;
  const dx = x1 - x0;
  const dy = y1 - y0;
  let tx = stepX !== 0 ? ((cx + (stepX > 0 ? 1 : 0)) * cs - x0) / dx : Infinity;
  let ty = stepY !== 0 ? ((cy + (stepY > 0 ? 1 : 0)) * cs - y0) / dy : Infinity;
  const ddx = stepX !== 0 ? cs / Math.abs(dx) : Infinity;
  const ddy = stepY !== 0 ? cs / Math.abs(dy) : Infinity;
  let n = 0;
  out[n++] = cx + cy * cols;
  while (cx !== ex || cy !== ey) {
    if (cy === ey || (cx !== ex && tx < ty)) {
      cx += stepX;
      tx += ddx;
    } else {
      cy += stepY;
      ty += ddy;
    }
    out[n++] = cx + cy * cols;
  }
  return n;
}

interface Range {
  /** first global index */
  base: number;
  source: SnapSource;
  /** midpoints of the segments of the source instead of its points */
  mid: boolean;
}

function findRange(ranges: Range[], g: number): Range {
  let lo = 0;
  let hi = ranges.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ranges[mid].base <= g) lo = mid;
    else hi = mid - 1;
  }
  return ranges[lo];
}

const EMPTY_F32 = new Float32Array(0);
const EMPTY_I32 = new Int32Array(0);
const EMPTY_U8 = new Uint8Array(0);

export class SnapIndex {
  private sources = new Map<string, SnapSource>();
  private view: ViewTransform | null = null;
  private dirty = true;

  // Point-like candidates (points and midpoints): screen position, kind, grid.
  private pCount = 0;
  private pX = EMPTY_F32;
  private pY = EMPTY_F32;
  private pKind = EMPTY_U8;
  private pCell = EMPTY_I32;
  private pStart = EMPTY_I32;
  private pItems = EMPTY_I32;
  private pRanges: Range[] = [];

  // Segment candidates: the visible piece on screen, kind, two grids (fine and coarse).
  private sCount = 0;
  private sX0 = EMPTY_F32;
  private sY0 = EMPTY_F32;
  private sX1 = EMPTY_F32;
  private sY1 = EMPTY_F32;
  private sKind = EMPTY_U8;
  /** -1: not visible, 0: fine grid, 1: coarse grid */
  private sLevel = new Int8Array(0);
  private fStart = EMPTY_I32;
  private fItems = EMPTY_I32;
  private cStart = EMPTY_I32;
  private cItems = EMPTY_I32;
  private sRanges: Range[] = [];

  private cols = 0;
  private rows = 0;
  private coarse = CELL * 8;
  private ccols = 0;
  private crows = 0;

  private readonly seg = new Float64Array(8);
  private readonly mask = new Uint8Array(SNAP_KINDS.length);

  /** Adds a source or replaces the one with the same key. */
  setSource(source: SnapSource): void {
    this.sources.set(source.key, source);
    this.dirty = true;
  }

  removeSource(key: string): void {
    if (this.sources.delete(key)) this.dirty = true;
  }

  clear(): void {
    if (this.sources.size) this.dirty = true;
    this.sources.clear();
  }

  /** Marks the screen index dirty; it is rebuilt on the next query. */
  setView(view: ViewTransform): void {
    this.view = view;
    this.dirty = true;
  }

  /** Candidates held: points, segments and midpoints of all sources. */
  get size(): number {
    let n = 0;
    for (const s of this.sources.values()) {
      if (s.points) n += Math.floor(s.points.xyz.length / 3);
      if (s.segments) n += Math.floor(s.segments.xyz.length / 6) * (s.segments.midpoints ? 2 : 1);
    }
    return n;
  }

  /** Rebuilds the screen index now (a query does it by itself when needed). */
  rebuild(): void {
    this.dirty = false;
    const view = this.view;
    this.pRanges = [];
    this.sRanges = [];
    let pTotal = 0;
    let sTotal = 0;
    for (const s of this.sources.values()) {
      const np = s.points ? Math.floor(s.points.xyz.length / 3) : 0;
      const ns = s.segments ? Math.floor(s.segments.xyz.length / 6) : 0;
      if (np > 0) {
        this.pRanges.push({ base: pTotal, source: s, mid: false });
        pTotal += np;
      }
      if (ns > 0) {
        this.sRanges.push({ base: sTotal, source: s, mid: false });
        sTotal += ns;
        if (s.segments?.midpoints) {
          this.pRanges.push({ base: pTotal, source: s, mid: true });
          pTotal += ns;
        }
      }
    }
    this.pCount = pTotal;
    this.sCount = sTotal;
    if (!view || !(view.width > 0) || !(view.height > 0)) {
      this.pCount = 0;
      this.sCount = 0;
      return;
    }
    const W = view.width;
    const H = view.height;
    const vis = view.visible;
    const m = view.matrix;
    const m0 = m[0], m1 = m[1], m2 = m[2], m3 = m[3];
    const m4 = m[4], m5 = m[5], m6 = m[6], m7 = m[7];
    const m8 = m[8], m9 = m[9], m10 = m[10], m11 = m[11];
    const m12 = m[12], m13 = m[13], m14 = m[14], m15 = m[15];
    const cols = Math.max(1, Math.ceil(W / CELL));
    const rows = Math.max(1, Math.ceil(H / CELL));
    const cells = cols * rows;
    this.cols = cols;
    this.rows = rows;

    // ---- point-like candidates
    if (this.pX.length < pTotal) {
      this.pX = new Float32Array(pTotal);
      this.pY = new Float32Array(pTotal);
      this.pKind = new Uint8Array(pTotal);
      this.pCell = new Int32Array(pTotal);
      this.pItems = new Int32Array(pTotal);
    }
    if (this.pStart.length !== cells + 1) this.pStart = new Int32Array(cells + 1);
    else this.pStart.fill(0);
    const pX = this.pX;
    const pY = this.pY;
    const pKind = this.pKind;
    const pCell = this.pCell;
    const pStart = this.pStart;
    for (const range of this.pRanges) {
      const src = range.source;
      const mid = range.mid;
      const xyz = mid ? (src.segments as { xyz: Float64Array }).xyz : (src.points as { xyz: Float64Array }).xyz;
      const stride = mid ? 6 : 3;
      const n = Math.floor(xyz.length / stride);
      const code = mid ? MIDPOINT_CODE : (KIND_CODE.get((src.points as { kind: SnapKind }).kind) ?? 0);
      let g = range.base;
      for (let i = 0; i < n; i++, g++) {
        const k = i * stride;
        let x = xyz[k];
        let y = xyz[k + 1];
        let z = xyz[k + 2];
        if (mid) {
          x += (xyz[k + 3] - x) * 0.5;
          y += (xyz[k + 4] - y) * 0.5;
          z += (xyz[k + 5] - z) * 0.5;
        }
        pKind[g] = code;
        let cell = -1;
        const cw = m3 * x + m7 * y + m11 * z + m15;
        if (cw > 0) {
          const cz = m2 * x + m6 * y + m10 * z + m14;
          if (cz >= -cw && cz <= cw) {
            const sx = ((m0 * x + m4 * y + m8 * z + m12) / cw + 1) * 0.5 * W;
            const sy = (1 - (m1 * x + m5 * y + m9 * z + m13) / cw) * 0.5 * H;
            if (sx >= 0 && sx <= W && sy >= 0 && sy <= H && (!vis || vis(x, y, z))) {
              pX[g] = sx;
              pY[g] = sy;
              const cx = Math.min(cols - 1, (sx / CELL) | 0);
              const cy = Math.min(rows - 1, (sy / CELL) | 0);
              cell = cx + cy * cols;
              pStart[cell + 1]++;
            }
          }
        }
        pCell[g] = cell;
      }
    }
    for (let c = 0; c < cells; c++) pStart[c + 1] += pStart[c];
    {
      // Fill: pStart[c] moves to the end of the cell and is shifted back afterwards.
      const items = this.pItems;
      for (let g = 0; g < pTotal; g++) {
        const cell = pCell[g];
        if (cell >= 0) items[pStart[cell]++] = g;
      }
      for (let c = cells; c > 0; c--) pStart[c] = pStart[c - 1];
      pStart[0] = 0;
    }

    // ---- segments
    const coarse = Math.max(CELL * 8, Math.ceil(Math.max(W, H) / COARSE_CELLS));
    const ccols = Math.max(1, Math.ceil(W / coarse));
    const crows = Math.max(1, Math.ceil(H / coarse));
    const ccells = ccols * crows;
    this.coarse = coarse;
    this.ccols = ccols;
    this.crows = crows;
    if (this.sX0.length < sTotal) {
      this.sX0 = new Float32Array(sTotal);
      this.sY0 = new Float32Array(sTotal);
      this.sX1 = new Float32Array(sTotal);
      this.sY1 = new Float32Array(sTotal);
      this.sKind = new Uint8Array(sTotal);
      this.sLevel = new Int8Array(sTotal);
    }
    if (this.fStart.length !== cells + 1) this.fStart = new Int32Array(cells + 1);
    else this.fStart.fill(0);
    if (this.cStart.length !== ccells + 1) this.cStart = new Int32Array(ccells + 1);
    else this.cStart.fill(0);
    const sX0 = this.sX0;
    const sY0 = this.sY0;
    const sX1 = this.sX1;
    const sY1 = this.sY1;
    const sKind = this.sKind;
    const sLevel = this.sLevel;
    const fStart = this.fStart;
    const cStart = this.cStart;
    const seg = this.seg;
    const scratch = new Int32Array(Math.max(cols + rows, ccols + crows) + 4);
    for (const range of this.sRanges) {
      const segments = range.source.segments as { kind: SnapKind; xyz: Float64Array };
      const xyz = segments.xyz;
      const n = Math.floor(xyz.length / 6);
      const code = KIND_CODE.get(segments.kind) ?? 0;
      let g = range.base;
      for (let i = 0; i < n; i++, g++) {
        const k = i * 6;
        sKind[g] = code;
        sLevel[g] = -1;
        // `visible` is not asked here: a segment may cross the window with both ends outside.
        // The snapped point itself is checked in the query.
        if (!projectSegment(m, W, H, xyz[k], xyz[k + 1], xyz[k + 2], xyz[k + 3], xyz[k + 4], xyz[k + 5], seg)) continue;
        sX0[g] = seg[SEG_X0];
        sY0[g] = seg[SEG_Y0];
        sX1[g] = seg[SEG_X1];
        sY1[g] = seg[SEG_Y1];
        // Read back as float32: the fill pass below must walk exactly the same cells.
        const x0 = sX0[g];
        const y0 = sY0[g];
        const x1 = sX1[g];
        const y1 = sY1[g];
        const span = Math.abs(((x1 / CELL) | 0) - ((x0 / CELL) | 0)) + Math.abs(((y1 / CELL) | 0) - ((y0 / CELL) | 0)) + 1;
        if (span <= FINE_SPAN) {
          sLevel[g] = 0;
          const count = walkCells(x0, y0, x1, y1, CELL, cols, rows, scratch);
          for (let c = 0; c < count; c++) fStart[scratch[c] + 1]++;
        } else {
          sLevel[g] = 1;
          const count = walkCells(x0, y0, x1, y1, coarse, ccols, crows, scratch);
          for (let c = 0; c < count; c++) cStart[scratch[c] + 1]++;
        }
      }
    }
    for (let c = 0; c < cells; c++) fStart[c + 1] += fStart[c];
    for (let c = 0; c < ccells; c++) cStart[c + 1] += cStart[c];
    if (this.fItems.length < fStart[cells]) this.fItems = new Int32Array(fStart[cells]);
    if (this.cItems.length < cStart[ccells]) this.cItems = new Int32Array(cStart[ccells]);
    const fItems = this.fItems;
    const cItems = this.cItems;
    for (let g = 0; g < sTotal; g++) {
      const level = sLevel[g];
      if (level === 0) {
        const count = walkCells(sX0[g], sY0[g], sX1[g], sY1[g], CELL, cols, rows, scratch);
        for (let c = 0; c < count; c++) fItems[fStart[scratch[c]]++] = g;
      } else if (level === 1) {
        const count = walkCells(sX0[g], sY0[g], sX1[g], sY1[g], coarse, ccols, crows, scratch);
        for (let c = 0; c < count; c++) cItems[cStart[scratch[c]]++] = g;
      }
    }
    for (let c = cells; c > 0; c--) fStart[c] = fStart[c - 1];
    fStart[0] = 0;
    for (let c = ccells; c > 0; c--) cStart[c] = cStart[c - 1];
    cStart[0] = 0;
  }

  /**
   * The nearest candidate of the enabled kinds within `radiusPx` of the pixel (px, py).
   * Point-like kinds (vertex, wf-vertex, collar, interval, midpoint) win over "on a segment" kinds
   * (nearest, wf-edge, wf-section, trace) when both are in range. Point hits return the stored
   * coordinates as they are; a segment hit returns the point of the 3D segment closest to the cursor
   * ray (without `ray`: the point under the cursor by the screen parameter).
   */
  query(px: number, py: number, radiusPx: number, enabled: ReadonlySet<SnapKind>, ray?: Ray): SnapHit | null {
    if (this.dirty) this.rebuild();
    const view = this.view;
    if (!view || !(radiusPx >= 0) || (this.pCount === 0 && this.sCount === 0)) return null;
    const mask = this.mask;
    let any = false;
    for (let i = 0; i < SNAP_KINDS.length; i++) {
      mask[i] = enabled.has(SNAP_KINDS[i]) ? 1 : 0;
      if (mask[i]) any = true;
    }
    if (!any) return null;
    const r2 = radiusPx * radiusPx;
    // The best candidate of every rank: squared distance, global index, 0 = point / 1 = segment.
    let d0 = Infinity, d1 = Infinity, d2 = Infinity;
    let g0 = -1, g1 = -1, g2 = -1;
    let seg0 = false, seg1 = false, seg2 = false;

    if (this.pCount > 0) {
      const cols = this.cols;
      const cx0 = Math.max(0, Math.floor((px - radiusPx) / CELL));
      const cx1 = Math.min(cols - 1, Math.floor((px + radiusPx) / CELL));
      const cy0 = Math.max(0, Math.floor((py - radiusPx) / CELL));
      const cy1 = Math.min(this.rows - 1, Math.floor((py + radiusPx) / CELL));
      const pX = this.pX;
      const pY = this.pY;
      const pKind = this.pKind;
      const items = this.pItems;
      const start = this.pStart;
      for (let cy = cy0; cy <= cy1; cy++) {
        for (let cx = cx0; cx <= cx1; cx++) {
          const cell = cx + cy * cols;
          for (let i = start[cell], end = start[cell + 1]; i < end; i++) {
            const g = items[i];
            const kind = pKind[g];
            if (!mask[kind]) continue;
            const dx = pX[g] - px;
            const dy = pY[g] - py;
            const d = dx * dx + dy * dy;
            if (d > r2) continue;
            const rank = KIND_RANK[kind];
            if (rank === 0) {
              if (d < d0) (d0 = d), (g0 = g);
            } else if (rank === 1) {
              if (d < d1) (d1 = d), (g1 = g);
            } else if (d < d2) (d2 = d), (g2 = g);
          }
        }
      }
    }

    if (this.sCount > 0) {
      const sX0 = this.sX0;
      const sY0 = this.sY0;
      const sX1 = this.sX1;
      const sY1 = this.sY1;
      const sKind = this.sKind;
      const vis = view.visible;
      for (let level = 0; level < 2; level++) {
        const cs = level === 0 ? CELL : this.coarse;
        const cols = level === 0 ? this.cols : this.ccols;
        const rows = level === 0 ? this.rows : this.crows;
        const items = level === 0 ? this.fItems : this.cItems;
        const start = level === 0 ? this.fStart : this.cStart;
        const cx0 = Math.max(0, Math.floor((px - radiusPx) / cs));
        const cx1 = Math.min(cols - 1, Math.floor((px + radiusPx) / cs));
        const cy0 = Math.max(0, Math.floor((py - radiusPx) / cs));
        const cy1 = Math.min(rows - 1, Math.floor((py + radiusPx) / cs));
        for (let cy = cy0; cy <= cy1; cy++) {
          for (let cx = cx0; cx <= cx1; cx++) {
            const cell = cx + cy * cols;
            for (let i = start[cell], end = start[cell + 1]; i < end; i++) {
              const g = items[i];
              const kind = sKind[g];
              if (!mask[kind]) continue;
              const x0 = sX0[g];
              const y0 = sY0[g];
              const ex = sX1[g] - x0;
              const ey = sY1[g] - y0;
              const len2 = ex * ex + ey * ey;
              let t = len2 > 0 ? ((px - x0) * ex + (py - y0) * ey) / len2 : 0;
              t = t < 0 ? 0 : t > 1 ? 1 : t;
              const dx = x0 + ex * t - px;
              const dy = y0 + ey * t - py;
              const d = dx * dx + dy * dy;
              if (d > r2) continue;
              const rank = KIND_RANK[kind];
              const best = rank === 0 ? d0 : rank === 1 ? d1 : d2;
              if (d >= best) continue;
              if (vis) {
                // The snapped point itself must be inside the window, not only an end of the segment.
                const p = this.segmentPoint(g, px, py, ray);
                if (!p || !vis(p[0], p[1], p[2])) continue;
              }
              if (rank === 0) (d0 = d), (g0 = g), (seg0 = true);
              else if (rank === 1) (d1 = d), (g1 = g), (seg1 = true);
              else (d2 = d), (g2 = g), (seg2 = true);
            }
          }
        }
      }
    }

    const g = g0 >= 0 ? g0 : g1 >= 0 ? g1 : g2;
    if (g < 0) return null;
    const isSeg = g0 >= 0 ? seg0 : g1 >= 0 ? seg1 : seg2;
    const distancePx = Math.sqrt(g0 >= 0 ? d0 : g1 >= 0 ? d1 : d2);
    if (isSeg) {
      const range = findRange(this.sRanges, g);
      const kind = (range.source.segments as { kind: SnapKind }).kind;
      const point = this.segmentPoint(g, px, py, ray);
      if (!point) return null;
      return { kind, point, label: SNAP_TITLES[kind], source: range.source.key, distancePx };
    }
    const range = findRange(this.pRanges, g);
    const i = g - range.base;
    if (range.mid) {
      const xyz = (range.source.segments as { xyz: Float64Array }).xyz;
      const k = i * 6;
      const point: Vec3 = [
        xyz[k] + (xyz[k + 3] - xyz[k]) * 0.5,
        xyz[k + 1] + (xyz[k + 4] - xyz[k + 1]) * 0.5,
        xyz[k + 2] + (xyz[k + 5] - xyz[k + 2]) * 0.5,
      ];
      return { kind: "midpoint", point, label: SNAP_TITLES.midpoint, source: range.source.key, distancePx };
    }
    const points = range.source.points as { kind: SnapKind; xyz: Float64Array; labels?: (i: number) => string };
    const xyz = points.xyz;
    return {
      kind: points.kind,
      point: [xyz[3 * i], xyz[3 * i + 1], xyz[3 * i + 2]],
      label: points.labels ? points.labels(i) : SNAP_TITLES[points.kind],
      source: range.source.key,
      distancePx,
    };
  }

  /** The 3D point of segment candidate `g` for the cursor at (px, py). */
  private segmentPoint(g: number, px: number, py: number, ray: Ray | undefined): Vec3 | null {
    const view = this.view as ViewTransform;
    const range = findRange(this.sRanges, g);
    const xyz = (range.source.segments as { xyz: Float64Array }).xyz;
    const k = (g - range.base) * 6;
    const ax = xyz[k], ay = xyz[k + 1], az = xyz[k + 2];
    const bx = xyz[k + 3], by = xyz[k + 4], bz = xyz[k + 5];
    const seg = this.seg;
    if (!projectSegment(view.matrix, view.width, view.height, ax, ay, az, bx, by, bz, seg)) return null;
    let t = NaN;
    if (ray) {
      // Closest approach of the line a + t·u to the ray line o + s·v.
      const ux = bx - ax, uy = by - ay, uz = bz - az;
      const vx = ray.dir[0], vy = ray.dir[1], vz = ray.dir[2];
      const wx = ax - ray.origin[0], wy = ay - ray.origin[1], wz = az - ray.origin[2];
      const uu = ux * ux + uy * uy + uz * uz;
      const uv = ux * vx + uy * vy + uz * vz;
      const vv = vx * vx + vy * vy + vz * vz;
      const uw = ux * wx + uy * wy + uz * wz;
      const vw = vx * wx + vy * wy + vz * wz;
      const denom = uu * vv - uv * uv;
      if (denom > 1e-12 * uu * vv) {
        t = (uv * vw - vv * uw) / denom;
        // Only the piece that is on screen can be snapped to.
        t = t < seg[SEG_T0] ? seg[SEG_T0] : t > seg[SEG_T1] ? seg[SEG_T1] : t;
      }
    }
    if (!(t === t)) t = segmentParamAt(seg, px, py);
    if (t <= 0) return [ax, ay, az];
    if (t >= 1) return [bx, by, bz];
    return [ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t];
  }
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
