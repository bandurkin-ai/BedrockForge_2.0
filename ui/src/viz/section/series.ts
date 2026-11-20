/**
 * A series of sections in the 3D window (ТЗ 8.1.2; ADR 0016 §2): its axes, K, s_k, O_k, the windows and their
 * faces, the section of a point and of a line; a polyline section — its segments, development and the 3D region
 * of a segment. The same arithmetic as the core term by term (`sections/geometry.py`, `polyline.py`,
 * `kernels.py`), checked against `core/tests/data/sections/series_cases.json` (1e−12, `exact` — bit for bit).
 * The names of the sections come from the core (`GET /sections/series/{id}/table`), not from here.
 * The parameters are `objects.meta` of the series as the core keeps them (checked there); float64.
 *
 *     seriesGeometry(meta) → SeriesGeometry | PolylineGeometry
 *     SeriesGeometry: plane(k), origin(k), depth(p), local(p, k?), sectionOf(p), sectionOfDepths(a, b), lineSection(xyz)
 *     PolylineGeometry: plane(seg), segmentOf(p), local(p), lineSection(xyz), halfSpaces(seg)
 */
import type { Vec3 } from "../core/vec3";
import { VizError } from "../error";
import { along, axisDot, azimuthCore, halfSpaces, seriesAxes, type SectionPlane } from "./plane";

export type SeriesKind = "vertical" | "plan" | "inclined" | "polyline";

/** `objects.meta` of a `section_series` (ADR 0016 §1): only what the geometry reads. */
export interface SeriesParams {
  type?: SeriesKind;
  p1?: number[];
  p2?: number[] | null;
  az?: number | null;
  phi?: number | null;
  s0?: number | null;
  s1?: number | null;
  step?: number | null;
  towards?: number | null;
  away?: number | null;
  /** own windows of sections, k from 0 */
  overrides?: Record<string, { towards?: number | null; away?: number | null }> | null;
  polyline?: number[][] | null;
  corridor?: number | null;
}

export const MAX_K = 5000;
const K_EPS = 1e-9;
const ABUT_EPS = 1e-9;
const DEFAULT_WINDOW = 25;
const DEFAULT_CORRIDOR = 25;
const MIN_SPAN = 1e-6;
const TURN_EPS = 1e-6;
const UP: Vec3 = [0, 0, 1];

const bad = (message: string, hint: string) => new VizError("bad_series", message, hint);
const or = (v: number | null | undefined, d: number): number => (v === null || v === undefined ? d : v);

/** K = floor((s1 − s0)/Δ + 1e−9) + 1. */
export function seriesCount(s0: number, s1: number, step: number): number {
  return Math.floor((s1 - s0) / step + K_EPS) + 1;
}

export class SeriesGeometry {
  readonly kind: "vertical" | "plan" | "inclined";
  readonly count: number;
  /** the faces of the windows in depth τ: lo_k ≤ τ < hi_k */
  readonly lo: Float64Array;
  readonly hi: Float64Array;
  /** every neighbour abuts */
  readonly partition: boolean;
  private readonly loMin: number;
  private readonly hiMax: number;
  private readonly reach: number;

  constructor(
    kind: "vertical" | "plan" | "inclined",
    readonly p1: Vec3,
    readonly az: number,
    readonly phi: number,
    readonly s: Vec3,
    readonly v: Vec3,
    readonly w: Vec3,
    readonly s0: number,
    readonly step: number,
    /** s_k */
    readonly offs: Float64Array,
    readonly towards: Float64Array,
    readonly away: Float64Array,
  ) {
    this.kind = kind;
    const k = offs.length;
    this.count = k;
    const lo = new Float64Array(k);
    const hi = new Float64Array(k);
    for (let i = 0; i < k; i++) {
      lo[i] = offs[i] - towards[i];
      hi[i] = offs[i] + away[i];
    }
    let partition = true;
    for (let i = 0; i + 1 < k; i++) {
      // the faces from the windows before any is moved (numpy computes `face` first)
      const face = Math.min(offs[i] + away[i], offs[i + 1] - towards[i + 1]);
      if (Math.abs(away[i] + towards[i + 1] - step) <= ABUT_EPS * step) {
        hi[i] = face;
        lo[i + 1] = face;
      } else partition = false;
    }
    this.lo = lo;
    this.hi = hi;
    this.partition = partition;
    let loMin = Infinity;
    let hiMax = -Infinity;
    let reach = -Infinity;
    for (let i = 0; i < k; i++) {
      loMin = Math.min(loMin, lo[i]);
      hiMax = Math.max(hiMax, hi[i]);
      reach = Math.max(reach, towards[i], away[i]);
    }
    this.loMin = loMin;
    this.hiMax = hiMax;
    this.reach = reach;
  }

  /** O_k = P1 + s_k·v̂. */
  origin(k: number): Vec3 {
    return along(this.p1, this.v, this.offs[this.index(k)]);
  }

  /** The plane of section k with its window. */
  plane(k: number): SectionPlane {
    const i = this.index(k);
    return { origin: this.origin(i), base: [...this.p1], offset: this.offs[i], s: [...this.s], v: [...this.v], w: [...this.w], towards: this.towards[i], away: this.away[i], u: null, u0: 0, seg: null, ends: [] };
  }

  /** τ = (p − P1)·v̂. */
  depth(p: Vec3): number {
    return axisDot(this.diff(p), this.v);
  }

  /** [u, w, t]: t from section k, or the depth τ when k is omitted. */
  local(p: Vec3, k?: number): Vec3 {
    const d = this.diff(p);
    let t = axisDot(d, this.v);
    if (k !== undefined) t = t - this.offs[this.index(k)];
    return [axisDot(d, this.s), axisDot(d, this.w), t];
  }

  /** The section of a point, −1 — none (Т8.1-2: t = w_от — the next one). */
  sectionOf(p: Vec3): number {
    const t = this.depth(p);
    return this.sectionOfDepths(t, t);
  }

  /** The section of a line: all its vertices in one window, −1 — none (an empty line, a NaN that matters). */
  lineSection(xyz: readonly Vec3[]): number {
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of xyz) {
      const t = this.depth(p);
      if (t !== t) return -1;
      lo = Math.min(lo, t);
      hi = Math.max(hi, t);
    }
    return xyz.length ? this.sectionOfDepths(lo, hi) : -1;
  }

  /** The section whose window holds the depths [a, b] (`kernels.find_sections`), −1 — none. */
  sectionOfDepths(a: number, b: number): number {
    const last = this.count - 1;
    if (!(a >= this.loMin && b < this.hiMax && a <= b)) return -1;
    const { lo, hi, offs } = this;
    if (this.partition) {
      const c = Math.floor((a - lo[0]) / this.step);
      let k = Math.min(Math.max(c, 0), last);
      while (k > 0 && a < lo[k]) k--;
      while (k < last && a >= hi[k]) k++;
      return lo[k] <= a && b < hi[k] ? k : -1;
    }
    const r = this.reach + this.step;
    const m = 0.5 * a + 0.5 * b;
    const j0 = Math.max(Math.ceil((b - r - this.s0) / this.step), 0);
    const j1 = Math.min(Math.floor((a + r - this.s0) / this.step), last);
    let best = -1;
    let gap = Infinity;
    for (let j = j0; j <= j1; j++) {
      if (lo[j] <= a && b < hi[j]) {
        const d = Math.abs(m - offs[j]);
        if (d < gap) {
          gap = d;
          best = j;
        }
      }
    }
    return best;
  }

  private diff(p: Vec3): Vec3 {
    return [p[0] - this.p1[0], p[1] - this.p1[1], p[2] - this.p1[2]];
  }

  private index(k: number): number {
    if (!(k >= 0 && k < this.count) || !Number.isInteger(k)) throw new RangeError(`section ${k} of ${this.count}`);
    return k;
  }
}

export class PolylineGeometry {
  readonly kind = "polyline" as const;
  readonly count = 1;
  readonly lengths: Float64Array;
  /** U_j: the start of segment j in the development */
  readonly starts: Float64Array;
  readonly sAxes: Vec3[];
  readonly vAxes: Vec3[];

  constructor(
    /** (m + 1) points in plan */
    readonly points: readonly [number, number][],
    readonly corridor: number,
  ) {
    const m = points.length - 1;
    this.lengths = new Float64Array(m);
    this.starts = new Float64Array(m);
    this.sAxes = [];
    this.vAxes = [];
    let u = 0;
    for (let j = 0; j < m; j++) {
      const dx = points[j + 1][0] - points[j][0];
      const dy = points[j + 1][1] - points[j][1];
      const len = Math.sqrt(dx * dx + dy * dy);
      if (!(len > MIN_SPAN)) throw bad(`Ломаный разрез: вершины ${j + 1} и ${j + 2} совпадают.`, "Удалите повторную вершину.");
      const sx = dx / len;
      const sy = dy / len;
      this.lengths[j] = len;
      this.sAxes.push([sx + 0, sy + 0, 0]);
      this.vAxes.push([-sy + 0, sx + 0, 0]);
      this.starts[j] = u;
      u += len;
    }
    for (let j = 1; j < m; j++) {
      const a = this.sAxes[j - 1];
      const b = this.sAxes[j];
      if (Math.hypot(a[0] + b[0], a[1] + b[1]) < TURN_EPS) throw bad(`Ломаный разрез поворачивает назад в вершине ${j + 1}.`, "Сдвиньте или удалите вершину: соседние звенья не должны идти по одной линии в обратные стороны.");
    }
  }

  get segments(): number {
    return this.lengths.length;
  }

  /** The length of the development U_m. */
  get total(): number {
    const m = this.segments;
    return this.starts[m - 1] + this.lengths[m - 1];
  }

  /** P_j in 3D (z = 0). */
  vertex(j: number): Vec3 {
    return [this.points[j][0], this.points[j][1], 0];
  }

  /** The plane of segment `seg`: its corridor and its ends (bisectors of the bends, across the free ends). */
  plane(seg = 0): SectionPlane {
    const j = this.segIndex(seg);
    const m = this.segments;
    const s = this.sAxes[j];
    const start = j > 0 ? bisector(this.sAxes[j - 1], s) : [...s];
    const e = j < m - 1 ? bisector(s, this.sAxes[j + 1]) : [...s];
    const end: Vec3 = [-e[0] + 0, -e[1] + 0, 0];
    const base = this.vertex(j);
    const u0 = this.starts[j];
    return { origin: [...base], base, offset: 0, s: [...s], v: [...this.vAxes[j]], w: [...UP], towards: this.corridor, away: this.corridor, u: [u0, u0 + this.lengths[j]], u0, seg: j, ends: [{ n: start as Vec3, at: base }, { n: end, at: this.vertex(j + 1) }] };
  }

  /** 4×[nx, ny, nz, c] of segment `seg`, n·p + c ≥ 0 inside, project coordinates. */
  halfSpaces(seg: number): [number, number, number, number][] {
    return halfSpaces(this.plane(seg));
  }

  /** The nearest segment in plan within the corridor (the smaller on a tie), −1 — outside. */
  segmentOf(p: Vec3): number {
    const px = p[0];
    const py = p[1];
    if (!(Number.isFinite(px) && Number.isFinite(py))) return -1;
    let best = -1;
    let gap = Infinity;
    for (let j = 0; j < this.segments; j++) {
      const dx = px - this.points[j][0];
      const dy = py - this.points[j][1];
      let d2 = dx * dx + dy * dy;
      const ex = px - this.points[j + 1][0];
      const ey = py - this.points[j + 1][1];
      const e2 = ex * ex + ey * ey;
      if (e2 < d2) d2 = e2;
      const s = this.sAxes[j];
      const along = dx * s[0] + dy * s[1];
      if (along > 0 && along < this.lengths[j]) {
        const v = this.vAxes[j];
        const t = dx * v[0] + dy * v[1];
        if (t * t < d2) d2 = t * t;
      }
      if (d2 < gap) {
        gap = d2;
        best = j;
      }
    }
    return gap <= this.corridor * this.corridor ? best : -1;
  }

  /** [u, w, t] by the segment of the point (or `seg`); NaN outside the corridor. */
  local(p: Vec3, seg?: number): Vec3 {
    const j = seg === undefined ? this.segmentOf(p) : this.segIndex(seg);
    if (j < 0) return [NaN, NaN, NaN];
    const dx = p[0] - this.points[j][0];
    const dy = p[1] - this.points[j][1];
    const s = this.sAxes[j];
    const v = this.vAxes[j];
    return [this.starts[j] + (dx * s[0] + dy * s[1]), 0 + p[2], dx * v[0] + dy * v[1]];
  }

  /** 0 — the point is in the corridor, −1 — not. */
  sectionOf(p: Vec3): number {
    return this.segmentOf(p) >= 0 ? 0 : -1;
  }

  /** 0 — all the vertices of the line are in the corridor, −1 — not (or the line is empty). */
  lineSection(xyz: readonly Vec3[]): number {
    if (!xyz.length) return -1;
    for (const p of xyz) if (this.segmentOf(p) < 0) return -1;
    return 0;
  }

  private segIndex(seg: number): number {
    if (!(seg >= 0 && seg < this.segments) || !Number.isInteger(seg)) throw new RangeError(`segment ${seg} of ${this.segments}`);
    return seg;
  }
}

function bisector(a: Vec3, b: Vec3): Vec3 {
  const n: Vec3 = [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const len = Math.sqrt(n[0] * n[0] + n[1] * n[1]);
  return [n[0] / len + 0, n[1] / len + 0, n[2] / len + 0];
}

function windows(k: number, towards: number, away: number, overrides: SeriesParams["overrides"]): [Float64Array, Float64Array] {
  const tw = new Float64Array(k).fill(towards);
  const aw = new Float64Array(k).fill(away);
  for (const [key, win] of Object.entries(overrides ?? {})) {
    const i = /^\d+$/.test(key) ? Number(key) : -1;
    if (!(i >= 0 && i < k) || !win) throw bad(`Своё окно задано несуществующему разрезу «${key.slice(0, 20)}».`, `Номера разрезов серии — от 1 до ${k}.`);
    tw[i] = or(win.towards, towards);
    aw[i] = or(win.away, away);
  }
  return [tw, aw];
}

function direction(meta: SeriesParams, kind: SeriesKind, p1: Vec3): number {
  if (meta.p2 !== null && meta.p2 !== undefined) {
    const az = azimuthCore(meta.p2[0] - p1[0], meta.p2[1] - p1[1]);
    if (az === null || Math.hypot(meta.p2[0] - p1[0], meta.p2[1] - p1[1]) <= MIN_SPAN) throw bad("Точки P1 и P2 совпадают в плане.", "Укажите вторую точку в стороне от P1.");
    return az;
  }
  if (meta.az === null || meta.az === undefined) {
    if (kind !== "plan") throw bad("Направление серии: задайте точку P2 или азимут.", "Укажите что-то одно.");
  }
  const a = ((or(meta.az, 90) % 360) + 360) % 360;
  return a < 360 ? a + 0 : 0;
}

/** The geometry of a series from its parameters (`objects.meta`); VizError `bad_series` for what it cannot take. */
export function seriesGeometry(meta: SeriesParams): SeriesGeometry | PolylineGeometry {
  const kind = meta.type ?? "vertical";
  if (kind === "polyline") {
    const pts = (meta.polyline ?? []).map((p): [number, number] => [p[0] + 0, p[1] + 0]);
    if (pts.length < 2) throw bad("Ломаный разрез: от 2 до 1 000 вершин.", "Укажите вершины ломаной в плане по порядку.");
    return new PolylineGeometry(pts, or(meta.corridor, DEFAULT_CORRIDOR));
  }
  const p = meta.p1;
  if (!p || p.length < 2) throw bad("Не задана точка P1.", "Укажите X, Y и Z начала серии.");
  const p1: Vec3 = [p[0] + 0, p[1] + 0, kind === "vertical" ? 0 : or(p[2], 0) + 0];
  const az = direction(meta, kind, p1);
  const phi = kind === "vertical" ? 90 : kind === "plan" ? 0 : or(meta.phi, 90);
  const towards = or(meta.towards, DEFAULT_WINDOW);
  const away = or(meta.away, DEFAULT_WINDOW);
  const step = or(meta.step, towards + away);
  const s0 = or(meta.s0, 0) + 0;
  const s1 = or(meta.s1, s0);
  if (!(step > 0) || !(s1 >= s0)) throw bad("Пределы и шаг серии: ожидаются числа, шаг больше нуля.", "Проверьте s0, s1 и шаг.");
  const k = seriesCount(s0, s1, step);
  if (k > MAX_K) throw bad(`В серии получается ${k} разрезов — больше 5 000.`, "Увеличьте шаг или сократите пределы серии.");
  const { s, v, w } = seriesAxes(az, phi);
  const offs = new Float64Array(k);
  for (let i = 0; i < k; i++) offs[i] = s0 + i * step + 0;
  const [tw, aw] = windows(k, towards, away, meta.overrides);
  return new SeriesGeometry(kind, p1, az, phi, s, v, w, s0, step, offs, tw, aw);
}
