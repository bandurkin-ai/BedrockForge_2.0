/**
 * What the main 3D draws for a swath plot (ТЗ 7.30 «3D»; ADR 0019 §6; перенос `buildSwath` BF): the cuboid cut
 * into swaths in the frame of its box (outline and boundaries of the swaths shown, the others dimmed), the plot
 * over it in its own (t, h) plane — t along the axis from the centre of the cuboid, h from 0 to 1 up the plot —
 * and the slab of one swath lit (Т7.30-3). `placeChart` stands the plot toward the viewer for a camera: only a
 * matrix per frame, no new geometry. `reach` is the extent «Вписать» and the standard views take (the cuboid and
 * the plot turned any way about its axis). Numbers float64; the buffers float32 from the centre of the cuboid,
 * which sits at `offset` from the integer `origin` of the layer (`viz/origin.ts`). Pure, no three.js.
 */
import { add, cross, dot, length, normalize, scale, type Vec3 } from "../core/vec3";
import { boxCentre, isVertical, type Frame, type Limits } from "./frame";

export interface ChartLabel {
  text: string;
  /** along the axis from the centre of the cuboid, m; up the plot, 0–1 */
  t: number;
  h: number;
  align: "left" | "right" | "center";
  /** shift in label heights (down is positive) */
  rows: number;
  color?: string;
  bold?: boolean;
}

export interface Chart3D {
  /** the height of the plot, a share of the cuboid's largest side */
  height: number;
  lines: { color: string; segments: Float32Array }[];
  points: { color: string; at: Float32Array }[];
  /** t0, t1, h of every bar */
  bars: { color: string; rects: Float32Array }[];
  /** frame, value grid and ticks at the boundaries: t, h pairs of segment ends; its first segment is the base t0 … t1 */
  grid: Float32Array;
  labels: ChartLabel[];
  /** the swaths shown span t0 … t1 */
  t0: number;
  t1: number;
}

export interface SwathBuild {
  /** absolute integer origin of the layer and the centre of the cuboid from it (float64) */
  origin: { x: number; y: number; z: number };
  offset: Vec3;
  /** absolute centre of the cuboid */
  centre: Vec3;
  u: Vec3;
  v: Vec3;
  w: Vec3;
  /** half sizes along u, v, w */
  half: Vec3;
  vertical: boolean;
  /** segment ends from the centre: the outline of the swaths shown, the boundaries between them, the swaths outside the range */
  lines: Float32Array;
  inner: Float32Array;
  dim: Float32Array;
  width: number;
  count: number;
  range: [number, number];
  chart: Chart3D | null;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax]: the cuboid and the plot about it */
  reach: [number, number, number, number, number, number];
}

/** What the build needs of a result of `POST /stats/swath`. */
export interface BuildResult {
  direction: string;
  frame: Pick<Frame, "u" | "v" | "w">;
  box: Limits;
  start: number;
  width: number;
  count: number;
  centres: number[];
  series: { kind: "blocks" | "samples"; name: string; mean: (number | null)[]; count: number[]; weights: (number | null)[]; error?: unknown }[];
}

export interface BuildLook {
  series: readonly { color: string; line: boolean; bars: boolean }[];
  range: [number, number];
  chart3d: boolean;
  height3d: number;
  /** ticks of the value axis between lo and hi (`niceTicks`) */
  ticks: (lo: number, hi: number) => number[];
  /** a number as the plot writes it */
  format: (v: number, digits: number) => string;
}

const BAR_SHARE = 0.3;
const TICK = 0.025;

const at = (u: Vec3, v: Vec3, w: Vec3, a: number, b: number, c: number): Vec3 => [u[0] * a + v[0] * b + w[0] * c, u[1] * a + v[1] * b + w[1] * c, u[2] * a + v[2] * b + w[2] * c];

/** The value axis of the lines shown: from 0 (or the lowest negative mean) to a round number above the highest. */
function valueAxis(series: BuildResult["series"], shown: boolean[], from: number, to: number, ticksOf: BuildLook["ticks"]): { min: number; max: number; ticks: number[] } {
  let lo = 0;
  let hi = -Infinity;
  series.forEach((s, i) => {
    if (!shown[i]) return;
    for (let k = from; k <= to; k++) {
      const m = s.mean[k];
      if (m === null || m === undefined || !Number.isFinite(m)) continue;
      lo = Math.min(lo, m);
      hi = Math.max(hi, m);
    }
  });
  if (hi === -Infinity) hi = 1;
  if (hi <= lo) hi = lo + (Math.abs(lo) || 1);
  const rough = ticksOf(lo, hi);
  const step = rough.length > 1 ? rough[1] - rough[0] : hi - lo;
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let k = 0; min + k * step <= max + step * 1e-6; k++) ticks.push(Number((min + k * step).toPrecision(12)));
  return { min, max, ticks };
}

/** The radius of what the plot sweeps about the axis of the cuboid (with room for the names over it). */
export function chartRadius(half: Vec3, height3d: number, names: number): number {
  const h = height3d * 2 * Math.max(...half);
  return Math.hypot(half[1], half[2]) + 0.14 * h + h * (1 + 0.18 * (names + 1));
}

/** The extent of the cuboid and of a cylinder of `radius` about its axis u (absolute). */
export function reachBox(centre: Vec3, u: Vec3, v: Vec3, w: Vec3, half: Vec3, radius: number): SwathBuild["reach"] {
  const out: SwathBuild["reach"] = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  for (let i = 0; i < 3; i++) {
    const box = Math.abs(u[i]) * half[0] + Math.abs(v[i]) * half[1] + Math.abs(w[i]) * half[2];
    const swept = Math.abs(u[i]) * half[0] + radius * Math.sqrt(Math.max(0, 1 - u[i] * u[i]));
    const e = Math.max(box, swept);
    out[i] = centre[i] - e;
    out[i + 3] = centre[i] + e;
  }
  return out;
}

/** The cuboid cut into swaths and (with `chart3d`) the plot over it. */
export function buildSwath(r: BuildResult, look: BuildLook): SwathBuild {
  const { u, v, w } = r.frame;
  const centre = boxCentre(r.frame, r.box);
  const origin = { x: Math.round(centre[0]), y: Math.round(centre[1]), z: Math.round(centre[2]) };
  const offset: Vec3 = [centre[0] - origin.x, centre[1] - origin.y, centre[2] - origin.z];
  const half: Vec3 = [0.5 * (r.box.u[1] - r.box.u[0]), 0.5 * (r.box.v[1] - r.box.v[0]), 0.5 * (r.box.w[1] - r.box.w[0])];
  const n = r.count;
  const from = Math.max(0, Math.min(look.range[0], n - 1));
  const to = Math.max(from, Math.min(look.range[1], n - 1));
  // the swaths along u from the low side of the box: the first starts at `start`, the box is on whole swaths
  const t0box = r.start - 0.5 * (r.box.u[0] + r.box.u[1]);
  const tOf = (k: number) => t0box + k * r.width;
  const lines: number[] = [];
  const inner: number[] = [];
  const dim: number[] = [];
  const seg = (out: number[], a: Vec3, b: Vec3) => out.push(a[0], a[1], a[2], b[0], b[1], b[2]);
  const ring = (t: number): Vec3[] => [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sv, sw]) => at(u, v, w, t, sv * half[1], sw * half[2]));
  // a rectangle at every boundary of the swaths, and the four long edges between them
  for (let k = 0; k <= n; k++) {
    const c = ring(tOf(k));
    const where = k < from || k > to + 1 ? dim : k === from || k === to + 1 ? lines : inner;
    for (let j = 0; j < 4; j++) seg(where, c[j], c[(j + 1) % 4]);
    if (k < n) {
      const next = ring(tOf(k + 1));
      for (let j = 0; j < 4; j++) seg(k >= from && k <= to ? lines : dim, c[j], next[j]);
    }
  }
  const vertical = isVertical(r.frame);
  const names = r.series.filter((s, i) => !s.error && (look.series[i]?.line ?? true)).length;
  const build: SwathBuild = {
    origin,
    offset,
    centre,
    u,
    v,
    w,
    half,
    vertical,
    lines: Float32Array.from(lines),
    inner: Float32Array.from(inner),
    dim: Float32Array.from(dim),
    width: r.width,
    count: n,
    range: [from, to],
    chart: null,
    reach: reachBox(centre, u, v, w, half, look.chart3d && n > 0 ? chartRadius(half, look.height3d, names) : 0),
  };
  if (!look.chart3d || n === 0) return build;

  const ok = r.series.map((s) => !s.error);
  const lineShown = r.series.map((_, i) => ok[i] && (look.series[i]?.line ?? true));
  const barShown = r.series.map((_, i) => ok[i] && (look.series[i]?.bars ?? false));
  const axis = valueAxis(r.series, lineShown, from, to, look.ticks);
  const hOf = (value: number) => (value - axis.min) / (axis.max - axis.min || 1);
  const t0 = tOf(from);
  const t1 = tOf(to + 1);
  const chart: Chart3D = { height: look.height3d, lines: [], points: [], bars: [], grid: new Float32Array(0), labels: [], t0, t1 };
  const grid: number[] = [t0, 0, t1, 0, t1, 0, t1, 1, t1, 1, t0, 1, t0, 1, t0, 0];
  for (const tick of axis.ticks) {
    const h = hOf(tick);
    if (h > 1e-6 && h < 1 - 1e-6) grid.push(t0, h, t1, h);
    // swaths along Z: the plot lies on its side, the values run along its foot
    chart.labels.push(vertical ? { text: look.format(tick, 4), t: t0, h, align: "center", rows: 0.8 } : { text: look.format(tick, 4), t: t0, h, align: "right", rows: 0 });
  }
  for (let k = from; k <= to + 1; k++) grid.push(tOf(k), 0, tOf(k), -TICK);
  chart.grid = Float32Array.from(grid);
  const every = Math.max(1, Math.ceil((to - from + 1) / 6));
  for (let k = from; k <= to; k += every) {
    const c = r.centres[k];
    const text = look.format(r.direction === "angles" ? c - r.start : c, 7);
    chart.labels.push(vertical ? { text, t: tOf(k) + r.width / 2, h: -TICK, align: "right", rows: 0 } : { text, t: tOf(k) + r.width / 2, h: -TICK, align: "center", rows: 0.7 });
  }
  const values = (s: BuildResult["series"][number]) => (s.kind === "samples" ? s.count : s.weights);
  const barMax = { blocks: 0, samples: 0 };
  r.series.forEach((s, i) => {
    if (!barShown[i]) return;
    for (let k = from; k <= to; k++) barMax[s.kind] = Math.max(barMax[s.kind], values(s)[k] ?? 0);
  });
  const barSeries = r.series.filter((_, i) => barShown[i]);
  const legend: { text: string; color: string }[] = [];
  r.series.forEach((s, i) => {
    const color = look.series[i]?.color ?? "#8a99a8";
    if (barShown[i] && barMax[s.kind] > 0) {
      const slot = barSeries.indexOf(s);
      const rects: number[] = [];
      const pad = r.width * 0.08;
      const each = (r.width - 2 * pad) / barSeries.length;
      for (let k = from; k <= to; k++) {
        const value = values(s)[k] ?? 0;
        if (value > 0) rects.push(tOf(k) + pad + slot * each, tOf(k) + pad + (slot + 1) * each, (value / barMax[s.kind]) * BAR_SHARE);
      }
      chart.bars.push({ color, rects: Float32Array.from(rects) });
    }
    if (!lineShown[i]) return;
    const segments: number[] = [];
    const points: number[] = [];
    let prev: [number, number] | null = null;
    for (let k = from; k <= to; k++) {
      const m = s.mean[k];
      // an empty swath breaks the line: no mean is never drawn as 0
      if (m === null || m === undefined || !Number.isFinite(m)) {
        prev = null;
        continue;
      }
      const p: [number, number] = [tOf(k) + r.width / 2, hOf(m)];
      points.push(p[0], p[1]);
      if (prev) segments.push(prev[0], prev[1], p[0], p[1]);
      prev = p;
    }
    chart.lines.push({ color, segments: Float32Array.from(segments) });
    chart.points.push({ color, at: Float32Array.from(points) });
    legend.push({ text: s.name, color });
  });
  // the names over the plot, the first series on top: over its left end, or (on its side) over its top
  legend.forEach(({ text, color }, i) => chart.labels.push({ text, t: vertical ? t1 : t0, h: vertical ? 0 : 1, align: "left", rows: -(legend.length - i - 0.3) * 1.15, color, bold: true }));
  build.chart = chart;
  return build;
}

/** The slab of the swath `index` lit (Т7.30-3): 12 triangles of its six faces, its 12 edges, the strip of the plot over it (null — outside the plot). */
export function slabOf(b: SwathBuild, index: number): { faces: Float32Array; rim: Float32Array; strip: [number, number] | null } | null {
  if (index < 0 || index >= b.count) return null;
  const a = -b.half[0] + index * b.width; // the box is on whole swaths from its low side
  const z = a + b.width;
  const c = [a, z].flatMap((t) => [at(b.u, b.v, b.w, t, -b.half[1], -b.half[2]), at(b.u, b.v, b.w, t, b.half[1], -b.half[2]), at(b.u, b.v, b.w, t, b.half[1], b.half[2]), at(b.u, b.v, b.w, t, -b.half[1], b.half[2])]);
  const faces = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
  const tri = new Float32Array(faces.length * 18);
  faces.forEach((f, i) => [f[0], f[1], f[2], f[0], f[2], f[3]].forEach((k, j) => tri.set(c[k], i * 18 + j * 3)));
  const rim: number[] = [];
  for (const [i, j] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]) rim.push(...c[i], ...c[j]);
  const strip: [number, number] | null = b.chart && index >= b.range[0] && index <= b.range[1] ? [a, z] : null;
  return { faces: tri, rim: Float32Array.from(rim), strip };
}

/**
 * Where the plot stands for a view (BF `place`): beside the cuboid across the line of sight, past its outline
 * toward the top of the screen (swaths along Z: toward the right) — in plan beside it, on a section over it. All
 * in the space of the layer before the exaggeration: `base` — the foot of the plot under the centre, `rise` — its
 * full height; `view`, `right`, `up` — the camera in the rendered space (Z stretched by `vexag`).
 */
export function placeChart(b: Pick<SwathBuild, "u" | "v" | "w" | "half" | "vertical" | "chart">, view: Vec3, right: Vec3, up: Vec3, vexag: number): { base: Vec3; rise: Vec3 } {
  const drawn = (a: Vec3): Vec3 => [a[0], a[1], a[2] * vexag];
  const [ur, vr, wr] = [drawn(b.u), drawn(b.v), drawn(b.w)];
  let p = cross(view, normalize(ur));
  if (length(p) < 0.08) p = b.vertical ? right : [0, 0, 1];
  p = normalize(p);
  const toward = b.vertical ? add(right, scale(up, 0.5)) : add(up, scale(right, 0.5));
  if (dot(p, toward) < 0) p = scale(p, -1);
  const outline = Math.abs(dot(p, vr)) * b.half[1] + Math.abs(dot(p, wr)) * b.half[2];
  const height = (b.chart?.height ?? 0) * 2 * Math.max(b.half[0] * length(ur), b.half[1] * length(vr), b.half[2] * length(wr));
  const local: Vec3 = [p[0], p[1], p[2] / vexag];
  return { base: scale(local, outline + 0.14 * height), rise: scale(local, height) };
}
