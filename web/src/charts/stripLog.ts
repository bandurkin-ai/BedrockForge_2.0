import type { DownholeData, IntervalColumn, IntervalTableInfo } from "../api";

/**
 * How a column of a downhole log draws its field (as in Leapfrog / Micromine logs):
 * - line: the value as a stepped line; bars: a bar from the left edge (histogram along the hole);
 * - color: the interval filled with a colour (categories, or a colour scale for numbers);
 * - text: the value written in the interval; color_text: both.
 */
export type TrackType = "line" | "bars" | "color" | "text" | "color_text";

export interface Track {
  id: string;
  /** "assay" or an interval table id */
  table: string;
  field: string;
  type: TrackType;
  /** px */
  width: number;
  color: string;
  /** scale bounds ("" = from the data) */
  min: string;
  max: string;
  log: boolean;
  /** interval boundaries as thin lines */
  boundaries: boolean;
}

export interface StripLogSettings {
  holes: string[];
  tracks: Track[];
  /** depth range, m ("" = whole hole) */
  from: string;
  to: string;
  /** vertical scale: px per metre; 0 = fit the longest hole into the window */
  scale: number;
  /** holes on one common depth axis (otherwise each from its own top) */
  grid: boolean;
}

export const TRACK_TITLES: Record<TrackType, string> = {
  line: "График (линия)",
  bars: "Столбцы",
  color: "Цветная колонка",
  text: "Текст",
  color_text: "Цвет + текст",
};

export const LOG_COLORS = ["#4f9dde", "#e8873a", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a"];
/** Colours of categories (lithology codes etc.), stable by the order the values are first met. */
export const CATEGORY_COLORS = ["#e0c040", "#8a6d3b", "#5fbf6a", "#4f9dde", "#c06fd8", "#e05d6f", "#46c7c0", "#e8873a",
  "#9a8f7a", "#2f6db5", "#b5651d", "#3c8d4a", "#8a4fa8", "#a89020", "#a83a4a", "#2a8f8a", "#d6d0c4", "#6b6355"];

let nextId = 0;
export const newTrackId = () => `t${Date.now().toString(36)}${(nextId++).toString(36)}`;

export function newTrack(table: string, column: IntervalColumn, index: number): Track {
  const grade = /^(AU|AG|PT|PD)/i.test(column.name) || /g\/t|ppm|ppb|г\/т/i.test(column.unit);
  return {
    id: newTrackId(), table, field: column.name,
    type: column.kind === "text" ? "color_text" : "bars",
    width: column.kind === "text" ? 110 : 90,
    color: LOG_COLORS[index % LOG_COLORS.length], min: "", max: "", log: grade && column.kind === "number", boundaries: column.kind === "text",
  };
}

/** Default columns from a table: its text fields first (lithology), then up to three numbers. */
export function defaultTracks(table: IntervalTableInfo): Track[] {
  const text = table.columns.filter((c) => c.kind === "text").slice(0, 2);
  const numbers = table.columns.filter((c) => c.kind === "number").slice(0, 3);
  return [...text, ...numbers].map((c, i) => newTrack(table.key, c, i));
}

export const DEFAULT_STRIPLOG: StripLogSettings = { holes: [], tracks: [], from: "", to: "", scale: 0, grid: true };

export function stripLogName(s: StripLogSettings): string {
  const holes = s.holes.length <= 3 ? s.holes.join(", ") : `${s.holes.slice(0, 2).join(", ")} и ещё ${s.holes.length - 2}`;
  return `Колонка ${holes || "—"}${s.tracks.length ? ` (${s.tracks.map((t) => t.field).slice(0, 3).join(", ")}${s.tracks.length > 3 ? "…" : ""})` : ""}`;
}

/** Round depth ticks for a range at a scale: about one per 60 px. */
export function depthTicks(top: number, bottom: number, pxPerM: number): number[] {
  const raw = 60 / Math.max(pxPerM, 1e-9);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((k) => k * pow).find((s) => s >= raw) ?? 10 * pow;
  const out: number[] = [];
  for (let v = Math.ceil(top / step) * step; v <= bottom + 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/** Category -> colour for a text field over every hole shown (first met, first coloured). */
export function categoryColors(data: DownholeData, table: string, field: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const h of data.holes) {
    for (const iv of h.tables[table] ?? []) {
      const v = iv.values[field];
      if (v === null || v === undefined || v === "") continue;
      const key = String(v);
      if (!out.has(key)) out.set(key, CATEGORY_COLORS[out.size % CATEGORY_COLORS.length]);
    }
  }
  return out;
}

/** Numeric range of a field over every hole shown (positive values only for a log scale). */
export function fieldRange(data: DownholeData, table: string, field: string, log: boolean): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const h of data.holes) {
    for (const iv of h.tables[table] ?? []) {
      const v = iv.values[field];
      if (typeof v !== "number" || !Number.isFinite(v) || (log && v <= 0)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  if (!Number.isFinite(lo)) return null;
  if (!log && lo > 0) lo = 0; // bars and lines start at zero
  if (hi <= lo) hi = log ? lo * 10 : lo + 1;
  return [lo, hi];
}

/** Position 0..1 of a value between bounds (clamped). */
export function along(v: number, lo: number, hi: number, log: boolean): number {
  if (log) {
    const a = Math.log10(Math.max(lo, 1e-12));
    const b = Math.log10(Math.max(hi, 1e-12));
    return Math.max(0, Math.min(1, (Math.log10(Math.max(v, 1e-12)) - a) / (b - a || 1)));
  }
  return Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
}
