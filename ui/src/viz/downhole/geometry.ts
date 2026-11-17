import type { DownholeData, DownholeInterval, TrajectoryHole } from "./types";
import { categoryColors, classIndex, classLabel, fieldRange, type MarkerShape, type StripLogSettings, type Track } from "../../charts/striplog/model";
import { formatNumber, parseNumber } from "../util/numbers";
import { gradientCss, gradientRgb } from "./gradients";

/**
 * Geometry of the downhole data in the 3D view (pure numbers, no three.js, so it is testable).
 *
 * Positions are relative to the scene origin. Panels (bars, lines, colour ribbons) hang beside the hole:
 * a vertex is a point of the hole plus `amp` metres in the *side* direction, which the shader finds from
 * the camera — across the hole, in the plane of the screen — so a panel always faces the viewer, in 3D
 * and in sections alike.
 */

export interface TubeLayer {
  trackId: string;
  /** px */
  width: number;
  opacity: number;
  /** pairs of points, x y z each, and one colour per point */
  positions: number[];
  colors: number[];
}

/** Triangles and segments with a side offset: position, hole direction, metres to the side, colour (r g b a). */
export interface SideMesh {
  positions: number[];
  tangents: number[];
  amps: number[];
  colors: number[];
  /** triangles (index into the vertices); empty for segments */
  index: number[];
}

/** Round points beside the hole (the "points" kind, the dots of a line). */
export interface SidePoints {
  trackId: string;
  /** px */
  size: number;
  opacity: number;
  positions: number[];
  tangents: number[];
  amps: number[];
  colors: number[];
}

export interface Label {
  /** point on the hole, relative to the origin */
  position: [number, number, number];
  tangent: [number, number, number];
  /** to the side, m: where the text starts */
  amp: number;
  text: string;
  /** length of the interval along the hole, m */
  length: number;
  trackId: string;
  /** shift to the right of the point on screen, px (labels of event markers) */
  px?: number;
  /** the text ends at the point and grows to the left (columns on the left of the hole) */
  right?: boolean;
  /** "#rrggbb" and px of the text (not set: the default label font) */
  color?: string;
  size?: number;
}

/** Markers of events on the holes: one point each. */
export interface MarkerLayer {
  trackId: string;
  shape: MarkerShape;
  /** px */
  size: number;
  opacity: number;
  positions: number[];
  colors: number[];
}

/**
 * Where a display starts when others of the same set are shown before it: the room already taken on each
 * side of the hole (in default columns plus metres) and the tubes already drawn.
 */
export interface StackStart {
  units: number;
  tubes: number;
  m?: number;
  leftUnits?: number;
  leftM?: number;
}

export interface LegendEntry {
  trackId: string;
  title: string;
  /** label and colour of categories or classes */
  items: [string, string][];
  /** intervals drawn in each item */
  counts: number[];
  /** the range of bars or of a colour scale */
  range: [number, number] | null;
  unit: string;
  /** css of the gradient of a continuous scale */
  gradient?: string;
}

export interface DownholeBuild {
  tubes: TubeLayer[];
  panels: SideMesh;
  /** frames of panels and graph lines (segments) */
  lines: SideMesh;
  points: SidePoints[];
  labels: Label[];
  markers: MarkerLayer[];
  legend: LegendEntry[];
  /** width of the default panel column, m */
  panelWidth: number;
  holes: number;
  intervals: number;
}

export const MAX_LABELS = 2500;
/** The label of an event shows at the zoom where an interval of this length would get its label, m. */
export const EVENT_LABEL_M = 4;
export const holeKey = (id: string) => String(id).trim().toUpperCase();
export const FRAME_COLOR = "#8a93a6";
export const NO_DATA_COLOR = "#9aa3b5";
/** Thicknesses a tube sized by the value is drawn in. */
export const RADIUS_STEPS = 6;

export const emptyMesh = (): SideMesh => ({ positions: [], tangents: [], amps: [], colors: [], index: [] });

export type Rgb = [number, number, number];
export type Vec = [number, number, number];

export function hexRgb(hex: string): Rgb {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const v = m ? parseInt(m[1], 16) : 0x9aa3b5;
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

/**
 * The point of a hole path (x, y, z, measured depth) at a depth. Above the collar: the collar.
 * Below the last point the hole goes on straight (a hole without a depth in the collar table still
 * has intervals); a path of one point goes straight down.
 */
export function pointAt(path: number[][], depth: number): [number, number, number] {
  if (path.length === 0) return [0, 0, 0];
  if (depth <= path[0][3]) return [path[0][0], path[0][1], path[0][2]];
  const last = path[path.length - 1];
  if (depth >= last[3]) {
    const over = depth - last[3];
    if (over <= 0) return [last[0], last[1], last[2]];
    const prev = path.length > 1 ? path[path.length - 2] : null;
    const d = prev ? [last[0] - prev[0], last[1] - prev[1], last[2] - prev[2]] : [0, 0, -1];
    const l = Math.hypot(d[0], d[1], d[2]);
    const u = l > 1e-9 ? [d[0] / l, d[1] / l, d[2] / l] : [0, 0, -1];
    return [last[0] + u[0] * over, last[1] + u[1] * over, last[2] + u[2] * over];
  }
  let lo = 0;
  let hi = path.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path[mid][3] <= depth) lo = mid;
    else hi = mid;
  }
  const a = path[lo];
  const b = path[hi];
  const t = b[3] > a[3] ? (depth - a[3]) / (b[3] - a[3]) : 0;
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Points of the hole from one depth to another: both ends and every survey station between. */
export function pathBetween(path: number[][], from: number, to: number): [number, number, number][] {
  const out: [number, number, number][] = [pointAt(path, from)];
  for (const p of path) if (p[3] > from && p[3] < to) out.push([p[0], p[1], p[2]]);
  out.push(pointAt(path, to));
  return out;
}

export function unit(a: [number, number, number], b: [number, number, number]): [number, number, number] {
  const d: [number, number, number] = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const l = Math.hypot(...d);
  return l > 1e-9 ? [d[0] / l, d[1] / l, d[2] / l] : [0, 0, -1];
}

export const valueText = (v: unknown) => {
  const t = typeof v === "number" ? formatNumber(v, 3) : v === null || v === undefined ? "" : String(v);
  return t.length > 18 ? `${t.slice(0, 17)}…` : t;
};

const comma = (n: number, decimals: number | null) => (decimals === null ? String(Number(n.toPrecision(6))) : n.toFixed(decimals)).replace(".", ",");

/** The text of a label from a template: "{AU} г/т", "{from}–{to}", "{len}"; fields are found without regard to case. */
export function labelText(template: string, iv: DownholeInterval, decimals: number | null): string {
  const keys = Object.keys(iv.values);
  const text = template.replace(/\{([^{}]+)\}/g, (_, raw: string) => {
    const name = raw.trim();
    const low = name.toLowerCase();
    if (low === "from") return iv.from === null ? "" : comma(iv.from, null);
    if (low === "to") return iv.to === null ? "" : comma(iv.to, null);
    if (low === "len") return iv.from === null || iv.to === null ? "" : comma(iv.to - iv.from, null);
    const key = keys.find((k) => k.toUpperCase() === name.toUpperCase());
    const v = key === undefined ? null : iv.values[key];
    return typeof v === "number" ? comma(v, decimals) : v === null || v === undefined ? "" : String(v);
  }).trim();
  return text.length > 40 ? `${text.slice(0, 39)}…` : text;
}

/** Fields named in a label template (without the depths). */
export function templateFields(template: string): string[] {
  const out: string[] = [];
  for (const m of template.matchAll(/\{([^{}]+)\}/g)) {
    const name = m[1].trim();
    if (!["from", "to", "len"].includes(name.toLowerCase()) && !out.includes(name)) out.push(name);
  }
  return out;
}

/** The fields of each table a display needs from the server: drawn, colouring and written in labels. */
export function displayFields(settings: StripLogSettings): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const add = (table: string, field: string | undefined) => {
    if (!field) return;
    const list = (out[table] ??= []);
    if (!list.some((f) => f.toUpperCase() === field.toUpperCase())) list.push(field);
  };
  for (const t of settings.tracks) {
    add(t.table, t.field);
    if (t.colorBy === "field") add(t.table, t.colorField);
    if (t.label?.on) for (const f of templateFields(t.label.template)) add(t.table, f);
  }
  return out;
}

/** The number a layer draws for a raw value: empty codes and non-positive values handled by its filter; null: empty. */
export function cleanValue(t: Track, raw: unknown): number | null {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  const f = t.filter;
  if (!f) return raw;
  if (typeof f.emptyBelow === "number" && raw <= f.emptyBelow) return null;
  if (raw <= 0) {
    if (f.nonPositive === "empty") return null;
    if (typeof f.nonPositive === "object") return f.nonPositive.replace;
  }
  return raw;
}

/** Does a value pass the filter of the layer (the cut-off or the range)? */
export function passesFilter(t: Track, v: number | null): boolean {
  const f = t.filter;
  if (!f || f.mode === "all") return true;
  if (v === null) return false;
  if (f.mode === "min") return typeof f.min !== "number" || v >= f.min;
  return (typeof f.min !== "number" || v >= f.min) && (typeof f.max !== "number" || v <= f.max);
}

/** Position of a value between bounds, not clamped above 1. */
export function positionOf(v: number, lo: number, hi: number, log: boolean): number {
  if (log) {
    const a = Math.log10(Math.max(lo, 1e-12));
    const b = Math.log10(Math.max(hi, 1e-12));
    return Math.max(0, (Math.log10(Math.max(v, 1e-12)) - a) / (b - a || 1));
  }
  return Math.max(0, (v - lo) / (hi - lo || 1));
}

interface Resolved {
  track: Track;
  numeric: boolean;
  range: [number, number] | null;
  unit: string;
  /** colours of the categories of the layer's own text field */
  colors: Map<string, string> | null;
  /** colour of an interval; null: it has none; "hidden": its class or category is switched off */
  color: (iv: DownholeInterval, v: number | null) => Rgb | null | "hidden";
  /** legend */
  items: [string, string][];
  counts: number[];
  itemOf: (iv: DownholeInterval, v: number | null) => number;
  gradient?: string;
}

/** A continuous scale: position of a value by its stretch, colour from the gradient. */
function scaleOf(data: DownholeData, table: string, field: string, t: Track, range: [number, number] | null): ((v: number) => Rgb) | null {
  const g = t.gradient;
  const stretch = g?.stretch ?? (t.log ? "log" : "linear");
  const lo = parseNumber(g?.min ?? "") ?? range?.[0] ?? null;
  const hi = parseNumber(g?.max ?? "") ?? range?.[1] ?? null;
  if (lo === null || hi === null) return null;
  let sorted: number[] | null = null;
  if (stretch === "quantile") {
    sorted = [];
    for (const h of data.holes) {
      for (const iv of h.tables[table] ?? []) {
        const v = iv.values[field];
        if (typeof v === "number" && Number.isFinite(v)) sorted.push(v);
      }
    }
    sorted.sort((a, b) => a - b);
    if (sorted.length < 2) sorted = null;
  }
  // a log scale needs a positive lower bound
  const min = stretch === "log" && lo <= 0 ? Math.max(hi / 1000, 1e-9) : lo;
  const max = hi > min ? hi : min * 10 || 1;
  return (v: number) => {
    let p: number;
    if (sorted) {
      let a = 0;
      let b = sorted.length;
      while (a < b) {
        const m = (a + b) >> 1;
        if (sorted[m] < v) a = m + 1;
        else b = m;
      }
      p = a / (sorted.length - 1);
    } else {
      p = Math.min(1, positionOf(v, min, max, stretch === "log"));
    }
    const c = gradientRgb(g?.name, p, g?.reverse);
    return [c[0] / 255, c[1] / 255, c[2] / 255];
  };
}

function orderedCategories(auto: Map<string, string>, t: Track): Map<string, string> {
  if (t.catColors) for (const [k, c] of Object.entries(t.catColors)) if (auto.has(k)) auto.set(k, c);
  if (!t.catOrder?.length) return auto;
  const out = new Map<string, string>();
  for (const k of t.catOrder) if (auto.has(k)) out.set(k, auto.get(k) as string);
  for (const [k, c] of auto) if (!out.has(k)) out.set(k, c);
  return out;
}

export function resolve(data: DownholeData, t: Track): Resolved {
  const table = data.tables.find((x) => x.key === t.table);
  const column = table?.columns.find((c) => c.name === t.field);
  const numeric = column?.kind === "number";
  const auto = numeric ? fieldRange(data, t.table, t.field, t.log) : null;
  const lo = parseNumber(t.min) ?? auto?.[0] ?? null;
  const hi = parseNumber(t.max) ?? auto?.[1] ?? null;
  const range: [number, number] | null = lo !== null && hi !== null && hi > lo ? [lo, hi] : auto;
  const wantsColor = t.type === "color" || t.type === "color_text";
  const one = hexRgb(t.color);
  const base: Resolved = {
    track: t, numeric, range, unit: column?.unit ?? "", colors: null, items: [], counts: [],
    color: () => one, itemOf: () => -1,
  };
  const byCategory = (field: string, own: boolean) => {
    const colors = orderedCategories(categoryColors(data, t.table, field), own ? t : { ...t, catOrder: undefined });
    const keys = [...colors.keys()];
    const index = new Map(keys.map((k, i) => [k, i]));
    const rgb = keys.map((k) => hexRgb(colors.get(k) as string));
    const hidden = new Set(own ? t.hidden ?? [] : []);
    base.colors = colors;
    base.items = [...colors];
    base.counts = keys.map(() => 0);
    const keyOf = (iv: DownholeInterval) => {
      const raw = iv.values[field];
      return raw === null || raw === undefined || raw === "" ? null : String(raw);
    };
    base.itemOf = (iv) => {
      const k = keyOf(iv);
      return k === null ? -1 : index.get(k) ?? -1;
    };
    base.color = (iv) => {
      const k = keyOf(iv);
      if (k === null) return null;
      if (hidden.has(k)) return "hidden";
      const i = index.get(k);
      return i === undefined ? null : rgb[i];
    };
  };
  const mode = t.colorBy ?? (wantsColor && numeric ? "scale" : "single");
  if (mode === "field" && t.colorField) {
    const other = table?.columns.find((c) => c.name.toUpperCase() === t.colorField?.toUpperCase());
    if (other && other.kind !== "number") {
      byCategory(other.name, false);
    } else if (other) {
      const scale = scaleOf(data, t.table, other.name, { ...t, log: false, gradient: t.gradient ? { ...t.gradient, min: "", max: "" } : undefined }, fieldRange(data, t.table, other.name, false));
      base.gradient = gradientCss(t.gradient?.name, t.gradient?.reverse);
      base.color = (iv) => {
        const v = iv.values[other.name];
        return scale && typeof v === "number" && Number.isFinite(v) ? scale(v) : null;
      };
    }
    return base;
  }
  if (!numeric) {
    if (wantsColor || t.type === "event") byCategory(t.field, true);
    return base;
  }
  if (mode === "classes") {
    const classes = t.classes ?? [];
    const rgb = classes.map((c) => hexRgb(c.color));
    base.items = classes.map((c, i) => [classLabel(classes, i), c.color]);
    base.counts = classes.map(() => 0);
    base.itemOf = (_iv, v) => classIndex(classes, v);
    base.color = (_iv, v) => {
      const i = classIndex(classes, v);
      return i < 0 ? null : classes[i].hidden ? "hidden" : rgb[i];
    };
  } else if (mode === "scale") {
    const scale = scaleOf(data, t.table, t.field, t, range);
    base.gradient = gradientCss(t.gradient?.name, t.gradient?.reverse);
    base.color = (_iv, v) => (scale && v !== null ? scale(v) : null);
  } else if (wantsColor) {
    base.color = (_iv, v) => (v === null ? null : one);
  }
  return base;
}

/** Is this column a tube along the hole (not a panel beside it)? The first colour column is, unless it says otherwise. */
export function tubeTracks(tracks: Track[]): Set<string> {
  const out = new Set<string>();
  let first = true;
  for (const t of tracks) {
    if (t.visible === false || (t.type !== "color" && t.type !== "color_text")) continue;
    const tube = t.form ? t.form === "tube" : first;
    first = false;
    if (tube) out.add(t.id);
  }
  return out;
}

/** Width of the default panel column: about a twentieth of a typical hole, between 2 and 40 m. */
export function autoPanelWidth(holes: TrajectoryHole[]): number {
  const lengths = holes.map((h) => (h.path.length ? h.path[h.path.length - 1][3] - h.path[0][3] : 0)).filter((l) => l > 0).sort((a, b) => a - b);
  const median = lengths.length ? lengths[lengths.length >> 1] : 100;
  return Math.min(40, Math.max(2, Math.round(median / 20)));
}

/** A length beside the hole: default columns plus metres. */
export interface Span { units: number; m: number }
interface Column { offset: Span; width: Span; sides: number[]; auto: boolean }
const GAP_UNITS = 0.08;

/** The column of every layer that takes room beside the hole, and where the next display starts. */
export function placeColumns(tracks: Track[], start: StackStart): { columns: Map<string, Column>; end: StackStart } {
  const tubes = tubeTracks(tracks);
  const columns = new Map<string, Column>();
  let right: Span = { units: start.units, m: start.m ?? 0 };
  let left: Span = { units: start.leftUnits ?? 0, m: start.leftM ?? 0 };
  for (const t of tracks) {
    if (t.visible === false || t.type === "event") continue;
    const tube = tubes.has(t.id);
    if (tube && t.type === "color") continue; // a tube without text takes no room
    const width: Span = t.widthM && t.widthM > 0 ? { units: 0, m: t.widthM } : { units: Math.max(0.3, t.width / 90), m: 0 };
    if (tube) {
      columns.set(t.id, { offset: { units: 0, m: 0 }, width, sides: [1], auto: false }); // the text of a tube column: beside the tube
      continue;
    }
    const split = typeof t.split === "number";
    const side = split ? "both" : t.side ?? "right";
    const sides = side === "both" ? [1, -1] : side === "left" ? [-1] : [1];
    if (typeof t.offsetM === "number" && t.offsetM >= 0) {
      columns.set(t.id, { offset: { units: -0.15, m: t.offsetM }, width, sides, auto: false });
      continue;
    }
    const offset: Span = side === "both" ? { units: Math.max(left.units, right.units), m: Math.max(left.m, right.m) } : side === "left" ? left : right;
    const after: Span = { units: offset.units + width.units + GAP_UNITS, m: offset.m + width.m };
    columns.set(t.id, { offset, width, sides, auto: true });
    if (side !== "left") right = after;
    if (side !== "right") left = after;
  }
  return { columns, end: { units: right.units, m: right.m, leftUnits: left.units, leftM: left.m, tubes: start.tubes + tubes.size } };
}

/** Where the next display of the set starts after this one: the room and the tubes taken so far. */
export function stackOf(settings: StripLogSettings, start: StackStart = { units: 0, tubes: 0 }): StackStart {
  return placeColumns(settings.tracks, start).end;
}

/** The depths an interval covers inside the depth window of the settings; null when outside it. */
export function clip(iv: DownholeInterval, from: number | null, to: number | null, event = false): [number, number] | null {
  if (event) {
    const d = iv.from ?? iv.to;
    return d === null || d < (from ?? -Infinity) || d > (to ?? Infinity) ? null : [d, d];
  }
  if (iv.from === null || iv.to === null || !(iv.to > iv.from)) return null;
  const a = Math.max(iv.from, from ?? -Infinity);
  const b = Math.min(iv.to, to ?? Infinity);
  return b > a ? [a, b] : null;
}

export interface LabelCandidate { label: Label; value: number }

