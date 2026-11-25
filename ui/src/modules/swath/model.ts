/**
 * The settings of a swath plot (ТЗ 7.30, 7.35 Д-01; ADR 0019 §2, §5; перенос `stats/swath.ts` BF): JSON `v: 1`
 * kept in `charts.settings` of an object of the project (`set_id = null`). The direction is X, Y, Z or three
 * angles of the box (azimuth [0; 360) from north clockwise, dip [−90; 90] — negative: the axis goes down, roll
 * (−180; 180]); X = (90; 0; 0), Y = (0; 0; 0), Z = (A; +90; 0). Up to 12 series (13th — not added, Т7.30-1):
 * a field of a block model or of samples of a set; the look of a series (colour, line, bars), the range of
 * swaths shown and the 3D view do not change the numbers and are not sent. `normalizeSwath` keeps only valid
 * values over the defaults; `swathName` — the name a swath follows until renamed by hand. Pure.
 */
import type { Negatives, WeightMode } from "../charts/settings";
import type { SeriesAnswer, SwathResult } from "./api";

export const MAX_SERIES = 12; // ТЗ 7.30
export const MAX_SWATHS = 2000; // ТЗ 7.30: the core refuses more (`too_many_swaths`)

export type SwathDirection = "x" | "y" | "z" | "angles";
export const DIRECTIONS: [SwathDirection, string][] = [["x", "X"], ["y", "Y"], ["z", "Z"], ["angles", "По углам"]];

export type BlockWeight = "volume" | "tonnage" | "none";

interface Look {
  /** `#rrggbb` */
  color: string;
  /** the line of means */
  line: boolean;
  /** the bars: the weight of blocks or the number of samples in each swath */
  bars: boolean;
}

export interface BlockSeries extends Look {
  kind: "blocks";
  /** null — the model is gone (a `.bfggis` without it): «источник удалён» */
  model_id: number | null;
  field: string | null;
  weight: BlockWeight;
  density: string | null;
  /** the blocks of these values of a category field (a domain) */
  filter: { field: string; labels: string[] } | null;
  /** a filter of the library (`objects` kind `filter`, ТЗ 7.35 Д-02): the blocks with f > 0 take part; null — no filter */
  filter_id: number | null;
}

export interface SampleSeries extends Look {
  kind: "samples";
  /** null — the set is gone */
  set_id: number | null;
  /** `name_key` of the table; null — the set's sample table */
  table: string | null;
  field: string | null;
  weight: WeightMode;
  weight_field: string | null;
  negatives: Negatives;
  top_cut: number | null;
  /** box — every sample in the cuboid; blocks — only inside the blocks of a model series (Т7.30-2) */
  within: "box" | "blocks";
  /** the number of that model series in the list (from 0); null — the first one */
  within_series: number | null;
}

export type SwathSeries = BlockSeries | SampleSeries;

export interface Limits {
  u: [number, number];
  v: [number, number];
  w: [number, number];
}

export interface SwathView {
  /** the plot over the swaths in 3D */
  chart3d: boolean;
  /** its height, a share of the cuboid's largest side */
  height3d: number;
}

export interface SwathSettings {
  v: 1;
  direction: SwathDirection;
  azimuth: number;
  dip: number;
  roll: number;
  /** m; null — whole parent blocks of the first model, or a round number (Т7.30-4) */
  width: number | null;
  /** a boundary of the swaths passes through this coordinate along the axis; null — from the cuboid */
  anchor: number | null;
  /** the cuboid by hand (the core takes it as it is); null — around the data */
  limits: Limits | null;
  series: SwathSeries[];
  /** the swaths shown (numbers from 0, both ends in); null — all */
  range: [number, number] | null;
  view: SwathView;
}

/** Colours of the series in the order they come: the brand's Cobalt and Copper first (docs/brand), then apart on dark and light. */
export const SERIES_COLORS = ["#1f6bff", "#f46a1a", "#22a37a", "#c466b6", "#d9a106", "#4dc3d6", "#8f7ae8", "#8a99a8", "#e05d6f", "#5fbf6a", "#8a6d3b", "#46c7c0"];

export const DEFAULT_VIEW: SwathView = { chart3d: true, height3d: 0.35 };

export const nextColor = (series: readonly SwathSeries[]): string => SERIES_COLORS.find((c) => !series.some((s) => s.color === c)) ?? SERIES_COLORS[series.length % SERIES_COLORS.length];

export function defaultSwath(series: SwathSeries[] = []): SwathSettings {
  return { v: 1, direction: "x", azimuth: 0, dip: 0, roll: 0, width: null, anchor: null, limits: null, series: series.slice(0, MAX_SERIES), range: null, view: { ...DEFAULT_VIEW } };
}

export function blockSeries(series: readonly SwathSeries[], modelId: number | null, field: string | null, density: string | null = null): BlockSeries {
  return { kind: "blocks", model_id: modelId, field, weight: "volume", density, filter: null, filter_id: null, color: nextColor(series), line: true, bars: !series.some((s) => s.kind === "blocks" && s.bars) };
}

/** Samples of a set: by length, inside the blocks of the first model when there is one (BF). */
export function sampleSeries(series: readonly SwathSeries[], setId: number | null, table: string | null, field: string | null): SampleSeries {
  const model = series.some((s) => s.kind === "blocks");
  return { kind: "samples", set_id: setId, table, field, weight: "length", weight_field: null, negatives: "keep", top_cut: null, within: model ? "blocks" : "box", within_series: null, color: nextColor(series), line: true, bars: false };
}

/** The component of a set a block field was estimated from: AU for AU_CUT or AU_OK, else the first one (BF). */
export function matchComponent(components: readonly string[], blockField: string | null): string | null {
  if (!components.length) return null;
  const f = (blockField ?? "").toUpperCase();
  const exact = components.find((n) => n.toUpperCase() === f);
  const prefix = components.filter((n) => f.startsWith(n.toUpperCase())).sort((a, b) => b.length - a.length)[0];
  return exact ?? prefix ?? components[0];
}

// ---- angles ----

const fmod = (a: number, b: number) => ((a % b) + b) % b;

/** The angles in their ranges: azimuth [0; 360), dip clamped to [−90; 90], roll (−180; 180]. */
export function normalizeAngles(a: number, d: number, r: number): { azimuth: number; dip: number; roll: number } {
  const roll = fmod(r, 360);
  return { azimuth: fmod(a, 360) + 0, dip: Math.min(90, Math.max(-90, d)) + 0, roll: (roll > 180 ? roll - 360 : roll) + 0 };
}

/** The angles of the box the settings give: X (90; 0; 0), Y (0; 0; 0), Z (A; +90; 0), or their own. */
export function anglesOf(s: Pick<SwathSettings, "direction" | "azimuth" | "dip" | "roll">): { azimuth: number; dip: number; roll: number } {
  switch (s.direction) {
    case "x":
      return { azimuth: 90, dip: 0, roll: 0 };
    case "y":
      return { azimuth: 0, dip: 0, roll: 0 };
    case "z":
      return { azimuth: s.azimuth, dip: 90, roll: 0 };
    default:
      return { azimuth: s.azimuth, dip: s.dip, roll: s.roll };
  }
}

/** Another direction: «По углам» starts from the angles of the one before; Z keeps an azimuth of angles. */
export function withDirection(s: SwathSettings, d: SwathDirection): SwathSettings {
  if (d === s.direction) return s;
  if (d === "angles") return { ...s, direction: d, ...anglesOf(s), range: null };
  if (d === "z") return { ...s, direction: d, azimuth: s.direction === "angles" ? s.azimuth : 0, range: null };
  return { ...s, direction: d, range: null };
}

/** New angles (a field of the window, a handle, the command): X and Y turned become «По углам» (Д-01); Z keeps its azimuth. */
export function withAngles(s: SwathSettings, patch: Partial<Record<"azimuth" | "dip" | "roll", number>>): SwathSettings {
  const ok = (v: number | undefined) => typeof v === "number" && Number.isFinite(v);
  if (s.direction === "z" && ok(patch.azimuth) && !ok(patch.dip) && !ok(patch.roll)) return { ...s, azimuth: normalizeAngles(patch.azimuth!, 0, 0).azimuth, range: null };
  const now = anglesOf(s);
  const next = normalizeAngles(ok(patch.azimuth) ? patch.azimuth! : now.azimuth, ok(patch.dip) ? patch.dip! : now.dip, ok(patch.roll) ? patch.roll! : now.roll);
  return { ...s, direction: "angles", ...next, range: null };
}

// ---- series ----

/** A new series at the end; the 13th is not added (Т7.30-1): `added` false. */
export function addSeries(s: SwathSettings, series: SwathSeries): { settings: SwathSettings; added: boolean } {
  if (s.series.length >= MAX_SERIES) return { settings: s, added: false };
  return { settings: { ...s, series: [...s.series, series] }, added: true };
}

/** `within_series` after the list changed: `map[old]` is the new number of a series (−1 — removed). */
function remap(series: SwathSeries[], map: number[]): SwathSeries[] {
  const models = series.some((x) => x.kind === "blocks");
  return series.map((x) => {
    if (x.kind !== "samples" || x.within !== "blocks") return x;
    if (!models) return { ...x, within: "box", within_series: null };
    if (x.within_series === null) return x;
    const to = map[x.within_series] ?? -1;
    return { ...x, within_series: to >= 0 && series[to]?.kind === "blocks" ? to : null };
  });
}

export function removeSeries(s: SwathSettings, i: number): SwathSettings {
  if (i < 0 || i >= s.series.length) return s;
  const map = s.series.map((_, k) => (k < i ? k : k === i ? -1 : k - 1));
  return { ...s, series: remap(s.series.filter((_, k) => k !== i), map) };
}

export function moveSeries(s: SwathSettings, i: number, by: number): SwathSettings {
  const j = i + by;
  if (i < 0 || j < 0 || i >= s.series.length || j >= s.series.length) return s;
  const order = s.series.map((_, k) => k);
  [order[i], order[j]] = [order[j], order[i]];
  const map: number[] = [];
  order.forEach((old, now) => (map[old] = now));
  return { ...s, series: remap(order.map((k) => s.series[k]), map) };
}

export function replaceSeries(s: SwathSettings, i: number, next: SwathSeries): SwathSettings {
  if (i < 0 || i >= s.series.length) return s;
  const series = s.series.map((x, k) => (k === i ? next : x));
  return { ...s, series: remap(series, series.map((_, k) => k)) };
}

// ---- normalisation of stored settings ----

type Raw = Record<string, unknown>;
const obj = (v: unknown): Raw | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Raw) : null);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const id = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v > 0 ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v.trim().slice(0, 200) : null);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T => (allowed.includes(v as T) ? (v as T) : fallback);
const pair = (v: unknown): [number, number] | null => (Array.isArray(v) && v.length === 2 && finite(v[0]) && finite(v[1]) && v[0] < v[1] ? [v[0], v[1]] : null);

function normalizeSeries(raw: unknown, before: SwathSeries[]): SwathSeries | null {
  const s = obj(raw);
  if (!s) return null;
  const look: Look = { color: typeof s.color === "string" && /^#[0-9a-f]{6}$/i.test(s.color) ? s.color.toLowerCase() : nextColor(before), line: s.line !== false, bars: s.bars === true };
  if (s.kind === "blocks") {
    const f = obj(s.filter);
    const labels = f && Array.isArray(f.labels) ? [...new Set(f.labels.filter((l): l is string => typeof l === "string"))].slice(0, 1000) : [];
    return { kind: "blocks", model_id: id(s.model_id), field: text(s.field), weight: oneOf(s.weight, ["volume", "tonnage", "none"] as const, "volume"), density: text(s.density), filter: f && text(f.field) ? { field: text(f.field)!, labels } : null, filter_id: id(s.filter_id), ...look };
  }
  if (s.kind === "samples") {
    const within = oneOf(s.within, ["box", "blocks"] as const, "box");
    const ws = typeof s.within_series === "number" && Number.isInteger(s.within_series) && s.within_series >= 0 ? s.within_series : null;
    return {
      kind: "samples",
      set_id: id(s.set_id),
      table: text(s.table),
      field: text(s.field),
      weight: oneOf(s.weight, ["none", "length", "field", "length_field"] as const, "length"),
      weight_field: text(s.weight_field),
      negatives: oneOf(s.negatives, ["keep", "half_abs", "exclude"] as const, "keep"),
      top_cut: finite(s.top_cut) ? s.top_cut : null,
      within,
      within_series: within === "blocks" ? ws : null,
      ...look,
    };
  }
  return null;
}

/** Settings read from `charts.settings` or the arguments of a command: anything missing or wrong gets its default. */
export function normalizeSwath(raw: unknown): SwathSettings {
  const r = obj(raw) ?? {};
  const out = defaultSwath();
  out.direction = oneOf(r.direction, ["x", "y", "z", "angles"] as const, "x");
  Object.assign(out, normalizeAngles(finite(r.azimuth) ? r.azimuth : 0, finite(r.dip) ? r.dip : 0, finite(r.roll) ? r.roll : 0));
  out.width = finite(r.width) && r.width > 0 ? r.width : null;
  out.anchor = finite(r.anchor) ? r.anchor : null;
  const lim = obj(r.limits);
  const lu = lim && pair(lim.u);
  const lv = lim && pair(lim.v);
  const lw = lim && pair(lim.w);
  out.limits = lu && lv && lw ? { u: lu, v: lv, w: lw } : null;
  for (const item of Array.isArray(r.series) ? r.series : []) {
    if (out.series.length >= MAX_SERIES) break;
    const s = normalizeSeries(item, out.series);
    if (s) out.series.push(s);
  }
  out.series = remap(out.series, out.series.map((_, k) => k));
  const range = r.range;
  out.range = Array.isArray(range) && range.length === 2 && range.every((v) => Number.isInteger(v) && v >= 0) && range[0] <= range[1] ? [range[0], range[1]] : null;
  const view = obj(r.view) ?? {};
  out.view = { chart3d: view.chart3d !== false, height3d: finite(view.height3d) ? Math.min(1, Math.max(0.1, view.height3d)) : DEFAULT_VIEW.height3d };
  return out;
}

/**
 * The arguments of `stats.swath` over the settings (ADR 0019 §9): `direction`, angles (turning X or Y makes it
 * «По углам», Д-01), `width`, `anchor`, `range_from` / `range_to` (numbers of swaths from 1).
 */
export function applyArgs(s: SwathSettings, a: Record<string, unknown>): SwathSettings {
  let out = s;
  if (typeof a.direction === "string" && ["x", "y", "z", "angles"].includes(a.direction)) out = withDirection(out, a.direction as SwathDirection);
  const angles: Partial<Record<"azimuth" | "dip" | "roll", number>> = {};
  for (const k of ["azimuth", "dip", "roll"] as const) if (finite(a[k])) angles[k] = a[k] as number;
  if (Object.keys(angles).length) out = withAngles(out, angles);
  if (finite(a.width) && a.width > 0) out = { ...out, width: a.width, range: null };
  if (a.width === null) out = { ...out, width: null, range: null };
  if (finite(a.anchor)) out = { ...out, anchor: a.anchor, range: null };
  if (a.anchor === null) out = { ...out, anchor: null, range: null };
  const from = finite(a.range_from) ? Math.max(1, Math.round(a.range_from)) : null;
  const to = finite(a.range_to) ? Math.max(1, Math.round(a.range_to)) : null;
  if (from !== null || to !== null) {
    const a0 = (from ?? 1) - 1;
    const b0 = to !== null ? to - 1 : (out.range?.[1] ?? MAX_SWATHS - 1);
    out = { ...out, range: [Math.min(a0, b0), Math.max(a0, b0)] };
  }
  return out;
}

// ---- what is sent: only what changes the numbers ----

/** The settings for `POST /stats/swath`: no colours, no range, no 3D view; angles a direction does not use are 0. */
export function swathRequest(s: SwathSettings): object {
  const a = anglesOf(s);
  const [azimuth, dip, roll] = s.direction === "angles" ? [a.azimuth, a.dip, a.roll] : s.direction === "z" ? [s.azimuth, 0, 0] : [0, 0, 0];
  return {
    v: 1,
    direction: s.direction,
    azimuth,
    dip,
    roll,
    width: s.width,
    anchor: s.anchor,
    limits: s.limits,
    series: s.series.map((x) => {
      if (x.kind === "blocks") return { kind: "blocks", model_id: x.model_id, field: x.field, weight: x.weight, density: x.weight === "tonnage" ? x.density : null, filter: x.filter && x.filter.labels.length ? x.filter : null, ...(x.filter_id !== null ? { filter_id: x.filter_id } : {}) };
      const wf = x.weight === "field" || x.weight === "length_field";
      return { kind: "samples", set_id: x.set_id, table: x.table, field: x.field, weight: x.weight, weight_field: wf ? x.weight_field : null, negatives: x.negatives, top_cut: x.top_cut, within: x.within, within_series: x.within === "blocks" ? x.within_series : null };
    }),
  };
}

/** What stands in the way of a calculation, or null. */
export function swathProblem(s: SwathSettings): string | null {
  if (!s.series.length) return "Добавьте ряд: блочную модель или пробы набора скважин.";
  for (const [i, x] of s.series.entries()) {
    const n = `Ряд ${i + 1}`;
    if ((x.kind === "blocks" ? x.model_id : x.set_id) === null) continue; // the core marks it «источник удалён»
    if (!x.field) return `${n}: выберите поле.`;
    if (x.kind === "blocks" && x.weight === "tonnage" && !x.density) return `${n}: для веса по тоннажу выберите поле плотности.`;
    if (x.kind === "samples" && (x.weight === "field" || x.weight === "length_field") && !x.weight_field) return `${n}: выберите поле веса.`;
    if (x.kind === "samples" && x.within === "blocks" && !s.series.some((b) => b.kind === "blocks")) return `${n}: отбор «внутри блоков» требует ряда блочной модели.`;
  }
  return null;
}

// ---- names ----

const deg = (v: number) => `${String(Number(v.toFixed(2))).replace(".", ",")}°`;
const metres = (v: number) => `${String(Number(v.toPrecision(6))).replace(".", ",")} м`;

/** The direction in words: «X», «Z», «аз. 35°, накл. −20°». */
export function directionText(s: Pick<SwathSettings, "direction" | "azimuth" | "dip" | "roll">): string {
  if (s.direction === "x" || s.direction === "y") return s.direction.toUpperCase();
  if (s.direction === "z") return s.azimuth ? `Z, аз. ${deg(s.azimuth)}` : "Z";
  const parts = [`аз. ${deg(s.azimuth)}`];
  if (s.dip) parts.push(`накл. ${deg(s.dip).replace("-", "−")}`);
  if (s.roll) parts.push(`пов. ${deg(s.roll).replace("-", "−")}`);
  return parts.join(", ");
}

/** The name a swath follows until renamed by hand (ADR 0019 §2): «Swath plot · X · 25 м». */
export const swathName = (s: SwathSettings): string => ["Swath plot", directionText(s), s.width !== null ? metres(s.width) : "ширина авто"].join(" · ");

/** The window title (Д-01): «Swath plot · ‹название›»; a name of its own settings already starts so. */
export const swathTitle = (name: string): string => (name.startsWith("Swath plot") ? name : `Swath plot · ${name}`);

// ---- the answer on screen ----

/** The swaths shown, inside what the plot has. */
export function shownRange(range: SwathSettings["range"], count: number): [number, number] {
  if (!range || count <= 0) return [0, Math.max(0, count - 1)];
  const from = Math.min(range[0], count - 1);
  return [from, Math.max(from, Math.min(range[1], count - 1))];
}

/** The axis of the plot: «X (восток), м» or the distance along the axis of the box. */
export function axisTitle(r: Pick<SwathResult, "direction" | "frame">): string {
  if (r.direction === "x") return "X (восток), м";
  if (r.direction === "y") return "Y (север), м";
  if (r.direction === "z") return "Z (высота), м";
  return `Расстояние вдоль оси полос (${directionText({ direction: "angles", ...r.frame })}), м`;
}

/** A position along the axis: a coordinate for X, Y, Z; the distance from the first swath for angles. */
export const axisValue = (r: Pick<SwathResult, "direction" | "start">, t: number): number => (r.direction === "angles" ? t - r.start : t);

/** The value axis of the lines shown: from zero (or the lowest negative mean) to a round number above the highest. */
export function valueAxis(series: readonly Pick<SeriesAnswer, "mean">[], shown: readonly boolean[], range: [number, number], ticksOf: (lo: number, hi: number) => number[]): { min: number; max: number; ticks: number[] } {
  let lo = 0;
  let hi = -Infinity;
  series.forEach((s, i) => {
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
  const rough = ticksOf(lo, hi);
  const step = rough.length > 1 ? rough[1] - rough[0] : hi - lo;
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let k = 0; min + k * step <= max + step * 1e-6; k++) ticks.push(Number((min + k * step).toPrecision(12)));
  return { min, max, ticks };
}

/** What a bar measures: the number of samples, the weight of blocks. */
export const barValues = (s: Pick<SeriesAnswer, "kind" | "count" | "weights">): (number | null)[] => (s.kind === "samples" ? s.count : s.weights);

/** The largest bar among the series of one kind shown: blocks share a scale, samples another. */
export function barScale(series: readonly SeriesAnswer[], kind: SeriesAnswer["kind"], shown: readonly boolean[], range: [number, number]): number {
  let max = 0;
  series.forEach((s, i) => {
    if (s.kind !== kind || !shown[i]) return;
    const values = barValues(s);
    for (let k = range[0]; k <= range[1]; k++) max = Math.max(max, values[k] ?? 0);
  });
  return max;
}

/** (m − m₁) / m₁ · 100 from the first series; null where either is empty or m₁ = 0 (never 0 for «нет»). */
export function deviation(value: number | null | undefined, base: number | null | undefined): number | null {
  if (value === null || value === undefined || base === null || base === undefined || !Number.isFinite(value) || !Number.isFinite(base) || base === 0) return null;
  return ((value - base) / base) * 100;
}

/** The deviation of a series by swath: the core's when it gave one, else computed here. */
export function deviations(r: Pick<SwathResult, "series">, i: number): (number | null)[] {
  const s = r.series[i];
  if (s.deviation) return s.deviation;
  const base = r.series[0];
  return s.mean.map((m, k) => (i === 0 ? null : deviation(m, base?.mean[k])));
}

/** The rows of the CSV (ADR 0019 §11): swath, from, to, centre; for each series mean, number, weight, deviation. */
export function swathCsv(r: SwathResult, range: [number, number]): { header: string[]; rows: (number | string | null)[][] } {
  const header = ["№", "От, м", "До, м", "Центр, м", ...r.series.flatMap((s, i) => [`${s.name}: среднее`, `${s.name}: число`, `${s.name}: ${s.weight_name}`, ...(i ? [`${s.name}: Δ, %`] : [])])];
  const dev = r.series.map((_, i) => deviations(r, i));
  const rows: (number | string | null)[][] = [];
  for (let k = range[0]; k <= range[1]; k++) {
    rows.push([k + 1, axisValue(r, r.start + k * r.width), axisValue(r, r.start + (k + 1) * r.width), axisValue(r, r.centres[k]), ...r.series.flatMap((s, i) => [s.mean[k] ?? null, s.count[k] ?? null, s.weights[k] ?? null, ...(i ? [dev[i][k]] : [])])]);
  }
  return { header, rows };
}

/** What was left out of a series, in words (BF `seriesNote`). */
export function seriesNote(s: SeriesAnswer): string {
  if (s.error) return `${s.name}: ${[s.error.message, s.error.hint].filter(Boolean).join(" ")}`;
  const c = s.counts ?? {};
  const n = (v: number | undefined) => (v ?? 0).toLocaleString("ru-RU");
  const parts: string[] = [];
  if (s.kind === "blocks") {
    parts.push(`блоков в расчёте ${n(c.used)} из ${n(c.blocks ?? c.rows)}`);
    if (c.filtered_out) parts.push(`отфильтровано ${n(c.filtered_out)}`);
    if (c.outside_box) parts.push(`вне кубоида ${n(c.outside_box)}`);
    if (c.missing) parts.push(`без значения ${n(c.missing)}`);
    if (c.bad_weight) parts.push(`без веса ${n(c.bad_weight)}`);
    return `${s.name}: ${parts.join(", ")}.`;
  }
  parts.push(`проб в расчёте ${n(c.used)} из ${n(c.rows)}`);
  if (c.missing) parts.push(`без значения ${n(c.missing)}`);
  if (c.bad_weight) parts.push(`без веса ${n(c.bad_weight)}`);
  if (c.negative) parts.push(`отрицательных ${n(c.negative)}`);
  if (c.capped) parts.push(`урезано ${n(c.capped)}`);
  if (c.no_position) parts.push(`без положения на скважине ${n(c.no_position)}`);
  if (c.outside_box) parts.push(`вне кубоида ${n(c.outside_box)}`);
  if (c.outside_blocks) parts.push(`вне блоков модели ${n(c.outside_blocks)}`);
  return `${s.name}: ${parts.join(", ")}.`;
}

/** The refusals of a series the card of the series says itself (the other series are counted): a filter that is gone or
 * does not fit the model, samples «внутри блоков» of a series with a turn or sub-blocks. */
export const SERIES_ISSUES = ["filter_not_found", "filter_field_missing", "blocks_unsupported"];

/** The error of a series for its card: «message hint» in Russian, or null. */
export function seriesIssue(s: Pick<SeriesAnswer, "error"> | undefined): string | null {
  const e = s?.error;
  return e && SERIES_ISSUES.includes(e.code) ? [e.message, e.hint].filter(Boolean).join(" ") : null;
}

/** The assistant's text: the name, the swaths, the totals of the series. */
export function swathSummary(name: string, r: SwathResult): string {
  const f = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : String(Number(v.toPrecision(4))).replace(".", ","));
  const totals = r.series.map((s) => (s.error ? `${s.name}: ${s.error.message}` : `${s.name}: среднее ${f(s.total.mean)} (${s.kind === "samples" ? "проб" : "блоков"} ${s.total.count})`));
  return `${name}: ${r.count} полос по ${f(r.width)} м. ${totals.join("; ")}.`;
}
