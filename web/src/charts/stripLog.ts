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
  /**
   * In the 3D view a colour column is a "tube" along the hole or a "panel" (a ribbon beside it);
   * bars, lines and text are always panels. Not set: the first colour column is a tube, the rest panels.
   */
  form?: "tube" | "panel";
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
  /** 3D view: every hole of the set (otherwise only `holes`) */
  allHoles?: boolean;
  /** 3D view: thickness of the first tube, px */
  tube?: number;
  /** 3D view: width of a panel column of the default width, m (0 or not set: from the size of the holes) */
  panel?: number;
  /** 3D view: write the values of text columns along the hole */
  labels?: boolean;
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

export const DEFAULT_STRIPLOG: StripLogSettings = {
  holes: [], tracks: [], from: "", to: "", scale: 0, grid: true, allHoles: false, tube: 8, panel: 0, labels: true,
};

/** The saved settings may come from an older version or be empty: fill what is missing. */
export function withDefaults(raw: Partial<StripLogSettings> | null | undefined): StripLogSettings {
  const r = raw ?? {};
  return {
    ...DEFAULT_STRIPLOG, ...r,
    holes: Array.isArray(r.holes) ? r.holes.map(String) : [],
    // tracks saved by an older version may lack fields
    tracks: Array.isArray(r.tracks)
      ? r.tracks.filter((t) => t && typeof t.table === "string" && typeof t.field === "string").map((t, i) => ({
        ...t, id: t.id ?? newTrackId(), type: t.type ?? "bars", width: Number(t.width) || 90, color: t.color ?? LOG_COLORS[i % LOG_COLORS.length],
        min: t.min ?? "", max: t.max ?? "", log: !!t.log, boundaries: !!t.boundaries,
      }))
      : [],
  };
}

/** The saved display a table makes on the holes of its whole set (the 3D view): its default columns. */
export function tableDisplay(table: IntervalTableInfo, fields?: string[]): StripLogSettings {
  // The first text column is a tube with its text, further ones plain colour ribbons; at most two numbers as bars.
  const named = fields?.length ? fields.map((f) => table.columns.find((c) => c.name.toUpperCase() === f.toUpperCase())).filter((c): c is IntervalColumn => !!c) : null;
  const text = named ? named.filter((c) => c.kind === "text") : table.columns.filter((c) => c.kind === "text").slice(0, 2);
  const numbers = named ? named.filter((c) => c.kind === "number") : table.columns.filter((c) => c.kind === "number").slice(0, 2);
  const tracks = [...text, ...numbers].map((c, i) => {
    const t = newTrack(table.key, c, i);
    return c.kind === "text" && i > 0 ? { ...t, type: "color" as const, boundaries: false } : t;
  });
  return { ...DEFAULT_STRIPLOG, allHoles: true, tracks };
}

export const TUBE_RANGE: [number, number] = [2, 24];

export function stripLogName(s: StripLogSettings): string {
  const holes = s.holes.length <= 3 ? s.holes.join(", ") : `${s.holes.slice(0, 2).join(", ")} и ещё ${s.holes.length - 2}`;
  const fields = s.tracks.length ? ` (${s.tracks.map((t) => t.field).slice(0, 3).join(", ")}${s.tracks.length > 3 ? "…" : ""})` : "";
  if (s.allHoles) return `На скважинах${fields || " —"}`;
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
  const values: number[] = [];
  for (const h of data.holes) {
    for (const iv of h.tables[table] ?? []) {
      const v = iv.values[field];
      if (typeof v !== "number" || !Number.isFinite(v) || (log && v <= 0)) continue;
      values.push(v);
    }
  }
  if (values.length === 0) return null;
  values.sort((a, b) => a - b);
  let lo = values[0];
  let hi = values[values.length - 1];
  // A few extreme values must not flatten every bar: with enough data the linear scale ends at the 98th percentile.
  if (!log && values.length >= 50) {
    const p98 = values[Math.floor((values.length - 1) * 0.98)];
    if (p98 > lo) hi = p98;
  }
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
