/**
 * Swath plot: its settings (kept in the project tree, section «Аудит»), the request to the server
 * and what the 3D view draws: the cuboid cut into swaths and the plot standing over them.
 */
import { blockMeta, type Dataset, type SwathRequest, type SwathResult } from "../api";
import { niceTicks } from "../viz/geometry";
import { formatNumber, parseNumber } from "./chartMath";

export type SwathDirection = "x" | "y" | "z" | "azimuth";
export const DIRECTION_TITLES: Record<SwathDirection, string> = { x: "По X", y: "По Y", z: "По Z", azimuth: "По азимуту" };

interface SeriesLook {
  /** a key of the series in the list (other series point at it) */
  id: string;
  color: string;
  /** the line of means and the bars of what is in each swath (volume of blocks, number of samples) */
  line: boolean;
  bars: boolean;
}

export interface SwathBlockSeries extends SeriesLook {
  kind: "blocks";
  modelId: number;
  /** key of a numeric attribute of the model */
  field: string;
  weight: "volume" | "tonnage" | "none";
  density: string | null;
  /** only the blocks with these values of a category field (e.g. the domain) */
  filterField: string | null;
  filterLabels: string[];
}

export interface SwathSampleSeries extends SeriesLook {
  kind: "samples";
  setId: number;
  /** the set's sample table ("assay" or a table's id); null: the default one */
  table: string | null;
  /** "comp:AU", "attr:..." */
  field: string;
  weight: "none" | "length" | "field" | "length_field";
  weightField: string | null;
  negatives: "keep" | "exclude" | "half_abs";
  topCut: string;
  /** box: every sample in the cuboid of the swaths; blocks: only inside the blocks of a model series */
  within: "box" | "blocks";
  /** id of that block model series; null: the first one */
  withinSeries: string | null;
}

export type SwathSeries = SwathBlockSeries | SwathSampleSeries;

export interface SwathSettings {
  direction: SwathDirection;
  /** degrees clockwise from north, for the direction "azimuth" */
  azimuth: string;
  /** width of a swath, m; empty: whole parent blocks chosen by the server */
  width: string;
  /** a boundary between swaths passes through this coordinate; empty: the first swath starts at the cuboid */
  anchor: string;
  series: SwathSeries[];
  /** swaths shown (numbers from 0, both ends in); null: all */
  range: [number, number] | null;
  /** the plot drawn over the swaths in the 3D view, and its height as a share of the cuboid's length */
  chart3d: boolean;
  height3d: number;
}

/** Colours of the series in the order they are added (told apart on dark and light backgrounds). */
export const SERIES_COLORS = ["#3b8eea", "#e8662c", "#22a37a", "#c466b6", "#d9a106", "#4dc3d6", "#8f7ae8", "#9aa3b5"];

let seq = 0;
const newId = () => `s${Date.now().toString(36)}${(seq++).toString(36)}`;
const nextColor = (series: SwathSeries[]) => SERIES_COLORS.find((c) => !series.some((s) => s.color === c)) ?? SERIES_COLORS[series.length % SERIES_COLORS.length];

/** A grade of a model to start with: the first one of its report, else the first number that is not a coordinate. */
function defaultBlockField(d: Dataset): string | null {
  const meta = blockMeta(d);
  const numbers = meta.attributes.filter((a) => a.kind === "number");
  const grade = (meta.report?.grades ?? []).find((k) => numbers.some((a) => a.key === k));
  const density = meta.report?.density;
  return grade ?? numbers.find((a) => a.key !== density && a.unit)?.key ?? numbers.find((a) => a.key !== density)?.key ?? null;
}

export function blockSeries(d: Dataset, series: SwathSeries[], field?: string | null): SwathBlockSeries | null {
  const key = field ?? defaultBlockField(d);
  if (!key) return null;
  return {
    id: newId(), kind: "blocks", modelId: d.id, field: key, weight: "volume", density: blockMeta(d).report?.density ?? null,
    filterField: null, filterLabels: [], color: nextColor(series), line: true, bars: !series.some((s) => s.kind === "blocks" && s.bars),
  };
}

/** The component of a set that a block field was estimated from: AU for AU_CUT or AU_OK, else the first one. */
export function matchComponent(d: Dataset, blockField: string | null): string | null {
  const names = (d.components ?? []).map((c) => c.name);
  if (!names.length) return null;
  const f = (blockField ?? "").toUpperCase();
  const exact = names.find((n) => n.toUpperCase() === f);
  const prefix = names.filter((n) => f.startsWith(n.toUpperCase())).sort((a, b) => b.length - a.length)[0];
  return exact ?? prefix ?? names[0];
}

export function sampleSeries(d: Dataset, series: SwathSeries[], field?: string | null): SwathSampleSeries {
  const model = series.find((s) => s.kind === "blocks");
  return {
    id: newId(), kind: "samples", setId: d.id, table: null, field: field ?? "", weight: "length", weightField: null,
    negatives: "keep", topCut: "", within: model ? "blocks" : "box", withinSeries: null,
    color: nextColor(series), line: true, bars: false,
  };
}

/** A new plot: the first block model against the samples of the active (or first) drillhole set. */
export function defaultSwath(datasets: Dataset[], activeSet: number | null = null): SwathSettings {
  const series: SwathSeries[] = [];
  const model = datasets.find((d) => d.kind === "blocks");
  const block = model ? blockSeries(model, series) : null;
  if (block) series.push(block);
  const sets = datasets.filter((d) => d.kind === "drillholes" && (d.components?.length ?? 0) > 0);
  const set = sets.find((d) => d.id === activeSet) ?? sets[0];
  if (set) {
    const blockName = model && block ? blockMeta(model).attributes.find((a) => a.key === block.field)?.name ?? null : null;
    const component = matchComponent(set, blockName);
    if (component) series.push(sampleSeries(set, series, `comp:${component}`));
  }
  return { direction: "x", azimuth: "0", width: "", anchor: "", series, range: null, chart3d: true, height3d: 0.35 };
}

const oneOf = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T => (allowed.includes(value as T) ? (value as T) : fallback);
const text = (value: unknown, fallback = ""): string => (typeof value === "string" ? value.slice(0, 64) : typeof value === "number" && Number.isFinite(value) ? String(value) : fallback);

/** Settings read from the tree: anything missing or of a wrong shape gets its default. */
export function normalizeSwath(raw: unknown): SwathSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<SwathSettings>;
  const series: SwathSeries[] = [];
  for (const item of Array.isArray(r.series) ? (r.series as unknown[]).slice(0, 12) : []) {
    if (!item || typeof item !== "object") continue;
    const s = item as Record<string, unknown>;
    const str = (key: string): string | null => (typeof s[key] === "string" && s[key] ? (s[key] as string) : null);
    const look = {
      id: str("id") ?? newId(),
      color: typeof s.color === "string" && /^#[0-9a-f]{6}$/i.test(s.color) ? s.color : nextColor(series),
      line: s.line !== false, bars: s.bars === true,
    };
    if (s.kind === "blocks" && Number.isInteger(s.modelId) && typeof s.field === "string") {
      series.push({
        ...look, kind: "blocks", modelId: s.modelId as number, field: s.field,
        weight: oneOf(s.weight, ["volume", "tonnage", "none"] as const, "volume"),
        density: str("density"), filterField: str("filterField"),
        filterLabels: Array.isArray(s.filterLabels) ? s.filterLabels.filter((l): l is string => typeof l === "string").slice(0, 500) : [],
      });
    } else if (s.kind === "samples" && Number.isInteger(s.setId) && typeof s.field === "string") {
      series.push({
        ...look, kind: "samples", setId: s.setId as number, table: str("table"), field: s.field,
        weight: oneOf(s.weight, ["none", "length", "field", "length_field"] as const, "length"),
        weightField: str("weightField"),
        negatives: oneOf(s.negatives, ["keep", "exclude", "half_abs"] as const, "keep"),
        topCut: text(s.topCut), within: oneOf(s.within, ["box", "blocks"] as const, "box"),
        withinSeries: str("withinSeries"),
      });
    }
  }
  const range = Array.isArray(r.range) && r.range.length === 2 && r.range.every((v) => Number.isInteger(v) && v >= 0) && r.range[0] <= r.range[1]
    ? ([r.range[0], r.range[1]] as [number, number]) : null;
  const height = typeof r.height3d === "number" && Number.isFinite(r.height3d) ? Math.min(1, Math.max(0.1, r.height3d)) : 0.35;
  return {
    direction: oneOf(r.direction, ["x", "y", "z", "azimuth"] as const, "x"),
    azimuth: text(r.azimuth, "0"), width: text(r.width), anchor: text(r.anchor),
    series, range, chart3d: r.chart3d !== false, height3d: height,
  };
}

/** What stands in the way of a calculation, or null when the settings can be sent. */
export function swathProblem(s: SwathSettings): string | null {
  if (!s.series.length) return "Добавьте ряд: блочную модель или пробы набора скважин";
  for (const [i, x] of s.series.entries()) {
    if (!x.field) return `Ряд ${i + 1}: выберите поле`;
    if (x.kind === "blocks" && x.weight === "tonnage" && !x.density) return `Ряд ${i + 1}: для веса по тоннажу выберите поле плотности`;
    if (x.kind === "samples" && (x.weight === "field" || x.weight === "length_field") && !x.weightField) return `Ряд ${i + 1}: выберите поле веса`;
    if (x.kind === "samples" && x.within === "blocks" && !s.series.some((b) => b.kind === "blocks")) {
      return `Ряд ${i + 1}: отбор «в блоках модели» требует ряда блочной модели`;
    }
  }
  const width = parseNumber(s.width);
  if (s.width.trim() && (width === null || width <= 0)) return "Ширина полосы — число больше нуля";
  if (s.anchor.trim() && parseNumber(s.anchor) === null) return "Привязка полос — число";
  if (s.direction === "azimuth") {
    const az = parseNumber(s.azimuth);
    if (az === null || az < 0 || az > 360) return "Азимут — от 0 до 360°";
  }
  return null;
}

/** The request for the server; only what changes the numbers (not colours or what is shown). */
export function swathRequest(s: SwathSettings): SwathRequest {
  return {
    direction: s.direction,
    azimuth: s.direction === "azimuth" ? parseNumber(s.azimuth) ?? 0 : 0,
    width: parseNumber(s.width),
    anchor: parseNumber(s.anchor),
    series: s.series.map((x) => {
      if (x.kind === "blocks") {
        return {
          kind: "blocks" as const, model_id: x.modelId, field: x.field, weight: x.weight, density: x.weight === "tonnage" ? x.density : null,
          filters: x.filterField ? [{ key: x.filterField, labels: x.filterLabels }] : [],
        };
      }
      const target = x.withinSeries ? s.series.findIndex((b) => b.id === x.withinSeries && b.kind === "blocks") : -1;
      return {
        kind: "samples" as const, set_id: x.setId, table: x.table, field: x.field, weight: x.weight,
        weight_field: x.weight === "field" || x.weight === "length_field" ? x.weightField : null,
        negatives: x.negatives, top_cut: parseNumber(x.topCut), within: x.within,
        within_series: x.within === "blocks" && target >= 0 ? target : null,
      };
    }),
  };
}

/** The swaths shown, inside what the plot has. */
export function shownRange(s: SwathSettings, count: number): [number, number] {
  if (!s.range || count <= 0) return [0, Math.max(0, count - 1)];
  const from = Math.min(s.range[0], count - 1);
  return [from, Math.max(from, Math.min(s.range[1], count - 1))];
}

/** Title of the axis of the plot and the position of a swath on it. */
export function axisTitle(r: Pick<SwathResult, "direction" | "azimuth">): string {
  return r.direction === "x" ? "X (восток), м" : r.direction === "y" ? "Y (север), м" : r.direction === "z" ? "Z (высота), м"
    : `Расстояние по азимуту ${formatNumber(r.azimuth, 4)}°, м`;
}

/** Where a swath is on the axis: a coordinate, or for an azimuth the distance from the first swath. */
export function axisValue(r: Pick<SwathResult, "direction" | "start">, t: number): number {
  return r.direction === "azimuth" ? t - r.start : t;
}

/** The value axis of the lines shown: from zero (or the lowest negative mean) to a round number above the highest. */
export function valueAxis(result: SwathResult, shown: boolean[], range: [number, number]): { min: number; max: number; ticks: number[] } {
  let lo = 0;
  let hi = -Infinity;
  result.series.forEach((s, i) => {
    if (!shown[i]) return;
    for (let k = range[0]; k <= range[1]; k++) {
      const v = s.mean[k];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  });
  if (hi === -Infinity) hi = 1;
  if (hi <= lo) hi = lo + (Math.abs(lo) || 1);
  const rough = niceTicks(lo, hi, 5);
  const step = rough.length > 1 ? rough[1] - rough[0] : hi - lo;
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let k = 0; min + k * step <= max + step * 1e-6; k++) ticks.push(Number((min + k * step).toPrecision(12)));
  return { min, max, ticks };
}

/** The largest weight of a swath among the series of one kind: bars of block models share a scale, bars of samples another. */
export function barScale(result: SwathResult, kind: "blocks" | "samples", shown: boolean[], range: [number, number]): number {
  let max = 0;
  result.series.forEach((s, i) => {
    if (s.kind !== kind || !shown[i]) return;
    const values = kind === "samples" ? s.count : s.weights;
    for (let k = range[0]; k <= range[1]; k++) max = Math.max(max, values[k] ?? 0);
  });
  return max;
}

/** What a bar of a series measures. */
export const barValues = (s: SwathResult["series"][number]): (number | null)[] => (s.kind === "samples" ? s.count : s.weights);

// ------------------------------------------------------------------------------------ 3D view

type V3 = [number, number, number];

export interface SwathLabel {
  text: string;
  /** along the axis from the cuboid's centre, m; up the plot, 0–1 */
  t: number;
  h: number;
  align: "left" | "right" | "center";
  /** shift in label heights (down is positive) */
  rows: number;
  color?: string;
  bold?: boolean;
}

export interface SwathChart3D {
  /** height of the plot as a share of the cuboid's largest side */
  height: number;
  /** share of the height taken by the bars */
  barShare: number;
  lines: { color: string; segments: Float32Array }[];
  points: { color: string; at: Float32Array }[];
  /** t0, t1, h of every bar */
  bars: { color: string; rects: Float32Array }[];
  /** frame, value grid and ticks at the swath boundaries: t, h pairs of segment ends */
  grid: Float32Array;
  labels: SwathLabel[];
}

/** What the 3D view draws for a plot, in metres from the centre of the cuboid. */
export interface SwathBuild {
  centre: V3;
  u: V3;
  v: V3;
  w: V3;
  /** half sizes of the cuboid along u, v, w */
  half: V3;
  vertical: boolean;
  /** x, y, z of segment ends from the centre: the outline of the swaths shown, the boundaries between them, the swaths outside the range */
  lines: Float32Array;
  inner: Float32Array;
  dim: Float32Array;
  /** the swath under the cursor of the plot: t0, t1 from the centre */
  highlight: [number, number] | null;
  chart: SwathChart3D | null;
}

const at = (u: V3, v: V3, w: V3, a: number, b: number, c: number): V3 => [u[0] * a + v[0] * b + w[0] * c, u[1] * a + v[1] * b + w[1] * c, u[2] * a + v[2] * b + w[2] * c];

/** The cuboid cut into swaths, and (with `chart`) the plot over it. */
export function buildSwath(result: SwathResult, settings: SwathSettings, highlight: number | null = null): SwathBuild {
  const u = result.frame.u as V3;
  const v = result.frame.v as V3;
  const w = result.frame.w as V3;
  const mid = [0.5 * (result.box.u[0] + result.box.u[1]), 0.5 * (result.box.v[0] + result.box.v[1]), 0.5 * (result.box.w[0] + result.box.w[1])];
  const half: V3 = [0.5 * (result.box.u[1] - result.box.u[0]), 0.5 * (result.box.v[1] - result.box.v[0]), 0.5 * (result.box.w[1] - result.box.w[0])];
  const centre = at(u, v, w, mid[0], mid[1], mid[2]);
  const n = result.count;
  const [from, to] = shownRange(settings, n);
  const tOf = (k: number) => -half[0] + k * result.width;

  const lines: number[] = [];
  const inner: number[] = [];
  const dim: number[] = [];
  const seg = (out: number[], a: V3, b: V3) => out.push(a[0], a[1], a[2], b[0], b[1], b[2]);
  const corners = (t: number): V3[] => [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sv, sw]) => at(u, v, w, t, sv * half[1], sw * half[2]));
  // A rectangle at every boundary between swaths, and the four long edges between them.
  for (let k = 0; k <= n; k++) {
    const c = corners(tOf(k));
    const ring = k < from || k > to + 1 ? dim : k === from || k === to + 1 ? lines : inner;
    for (let j = 0; j < 4; j++) seg(ring, c[j], c[(j + 1) % 4]);
    if (k < n) {
      const next = corners(tOf(k + 1));
      for (let j = 0; j < 4; j++) seg(k >= from && k <= to ? lines : dim, c[j], next[j]);
    }
  }

  const vertical = result.direction === "z";
  const build: SwathBuild = {
    centre, u, v, w, half, vertical,
    lines: Float32Array.from(lines), inner: Float32Array.from(inner), dim: Float32Array.from(dim),
    highlight: highlight !== null && highlight >= 0 && highlight < n ? [tOf(highlight), tOf(highlight + 1)] : null,
    chart: null,
  };
  if (!settings.chart3d) return build;

  const look = result.series.map((_, i) => settings.series[i]);
  const lineShown = look.map((s) => !!s && s.line);
  const barShown = look.map((s) => !!s && s.bars);
  const axis = valueAxis(result, lineShown, [from, to]);
  const hOf = (value: number) => (value - axis.min) / (axis.max - axis.min || 1);
  const t0 = tOf(from);
  const t1 = tOf(to + 1);
  const barShare = 0.3;

  const chart: SwathChart3D = { height: settings.height3d, barShare, lines: [], points: [], bars: [], grid: new Float32Array(0), labels: [] };
  const grid: number[] = [t0, 0, t1, 0, t1, 0, t1, 1, t1, 1, t0, 1, t0, 1, t0, 0];
  for (const tick of axis.ticks) {
    const h = hOf(tick);
    if (h > 1e-6 && h < 1 - 1e-6) grid.push(t0, h, t1, h);
    // Swaths along Z: the plot lies on its side, the values run along its foot.
    chart.labels.push(vertical ? { text: formatNumber(tick, 4), t: t0, h, align: "center", rows: 0.8 } : { text: formatNumber(tick, 4), t: t0, h, align: "right", rows: 0 });
  }
  const tickLen = 0.025;
  for (let k = from; k <= to + 1; k++) grid.push(tOf(k), 0, tOf(k), -tickLen);
  chart.grid = Float32Array.from(grid);
  // The position of a few swaths along the axis, under the plot.
  const every = Math.max(1, Math.ceil((to - from + 1) / 6));
  for (let k = from; k <= to; k += every) {
    const text = formatNumber(axisValue(result, result.centres[k]), 7);
    chart.labels.push(vertical ? { text, t: tOf(k) + result.width / 2, h: -tickLen, align: "right", rows: 0 } : { text, t: tOf(k) + result.width / 2, h: -tickLen, align: "center", rows: 0.7 });
  }

  const barMax = { blocks: barScale(result, "blocks", barShown, [from, to]), samples: barScale(result, "samples", barShown, [from, to]) };
  const barSeries = result.series.filter((_, i) => barShown[i]);
  const names: { text: string; color: string }[] = [];
  result.series.forEach((s, i) => {
    const color = look[i]?.color ?? SERIES_COLORS[i % SERIES_COLORS.length];
    if (barShown[i] && barMax[s.kind] > 0) {
      const slot = barSeries.indexOf(s);
      const rects: number[] = [];
      const values = barValues(s);
      // Bars of several series stand side by side inside the swath.
      const pad = result.width * 0.08;
      const each = (result.width - 2 * pad) / barSeries.length;
      for (let k = from; k <= to; k++) {
        const value = values[k] ?? 0;
        if (value > 0) rects.push(tOf(k) + pad + slot * each, tOf(k) + pad + (slot + 1) * each, (value / barMax[s.kind]) * barShare);
      }
      chart.bars.push({ color, rects: Float32Array.from(rects) });
    }
    if (!lineShown[i]) return;
    const segments: number[] = [];
    const points: number[] = [];
    let prev: [number, number] | null = null;
    for (let k = from; k <= to; k++) {
      const value = s.mean[k];
      if (value === null || value === undefined || !Number.isFinite(value)) {
        prev = null;
        continue;
      }
      const p: [number, number] = [tOf(k) + result.width / 2, hOf(value)];
      points.push(p[0], p[1]);
      if (prev) segments.push(prev[0], prev[1], p[0], p[1]);
      prev = p;
    }
    chart.lines.push({ color, segments: Float32Array.from(segments) });
    chart.points.push({ color, at: Float32Array.from(points) });
    names.push({ text: s.name, color });
  });
  // The names stand over the plot, the first series on top: over its left end, or (on its side) over its top.
  names.forEach(({ text, color }, i) => {
    chart.labels.push({ text, t: vertical ? t1 : t0, h: vertical ? 0 : 1, align: "left", rows: -(names.length - i - 0.3) * 1.15, color, bold: true });
  });
  build.chart = chart;
  return build;
}
