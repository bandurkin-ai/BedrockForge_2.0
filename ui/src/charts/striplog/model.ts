import type { DownholeData, IntervalColumn, IntervalTableInfo } from "../../viz/downhole/types";

/**
 * How a column of a downhole log draws its field (as in Leapfrog / Micromine logs):
 * - line: the value as a stepped line; bars: a bar from the left edge (histogram along the hole);
 * - color: the interval filled with a colour (categories, or a colour scale for numbers);
 * - text: the value written in the interval; color_text: both.
 */
export type TrackType = "line" | "bars" | "steps" | "points" | "color" | "text" | "color_text" | "event";

/** A class of a colouring by grade classes: values from `from` (null: everything below the next class) up to the next class. */
export interface ColorClass {
  from: number | null;
  color: string;
  label?: string;
  /** not drawn in the view (the eye of the class) */
  hidden?: boolean;
}

export type TrackSide = "left" | "right" | "both";
export type GradientStretch = "linear" | "log" | "quantile";
export interface TrackGradient { name: string; reverse: boolean; stretch: GradientStretch; min: string; max: string }
export interface TrackFilter {
  mode: "all" | "min" | "range";
  min?: number;
  max?: number;
  /** values that are empty: skipped, or drawn in the "no data" colour */
  empty: "skip" | "nodata";
  /** zero and negative values: as they are, empty, or replaced (half of the detection limit) */
  nonPositive: "asis" | "empty" | { replace: number };
  /** values at or below this are empty (codes like -999) */
  emptyBelow?: number;
}
export interface TrackLabel {
  on: boolean;
  /** text with fields in braces: "{AU} г/т", "{from}–{to}", "{len}" */
  template: string;
  /** null: as in the data */
  decimals: number | null;
  /** only values at or above */
  min?: number;
  /** px */
  size: number;
  /** "class": the colour of the interval, or a colour of its own */
  color: string;
  place: "outside" | "trace";
  /** at most this many labels of the layer (the highest values stay) */
  limit?: number;
}
/** Where the settings of a layer came from: a copy of a style, or a link to it (then the style's settings are used). */
/** A link to a library style by its uid (BF2: survives `.bfggis`; built-in styles are `builtin:<key>`). */
export interface StyleRef { uid: string; name: string; linked: boolean; modified?: boolean; auto?: boolean }

export type MarkerShape = "circle" | "square" | "diamond" | "triangle" | "cross" | "star";
export const MARKER_TITLES: Record<MarkerShape, string> = {
  circle: "Круг", square: "Квадрат", diamond: "Ромб", triangle: "Треугольник", cross: "Крест", star: "Звезда",
};

/** What a saved display of data along the holes shows; each has its own form. */
export type DisplayForm = "grade" | "lith" | "event";
export const FORM_TITLES: Record<DisplayForm, string> = { grade: "Содержания", lith: "Литология", event: "События" };

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
  /**
   * Colour of a number: "single" — the track's colour; "classes" — by grade classes; "scale" — a continuous scale.
   * Not set: bars and lines in one colour, a colour column by the scale.
   */
  colorBy?: "single" | "classes" | "scale" | "field";
  classes?: ColorClass[];
  /** colorBy "field": the field of the same table that gives the colour */
  colorField?: string;
  /** the continuous scale: its gradient, stretch and bounds ("" = as the scale of the layer) */
  gradient?: TrackGradient;
  /** colour of values outside the classes and of empty ones */
  noDataColor?: string;
  /** hidden: not drawn, but kept in the display */
  visible?: boolean;
  /** side of the hole on screen (default right) */
  side?: TrackSide;
  /** width of the column, m (not set: from `width` and the default column) */
  widthM?: number;
  /** distance from the hole, m (not set: stacked after the layers before it) */
  offsetM?: number;
  /** 0..1 */
  opacity?: number;
  /** bars: the part of an interval left empty between bars, 0..0.8 */
  gap?: number;
  /** steps: filled (default) or the outline only */
  fill?: boolean;
  /** line: points at the values; points and dots: their size, px */
  dots?: boolean;
  pointSize?: number;
  /** values below go to one side of the hole, above to the other */
  split?: number | null;
  /** the scale follows the data (its bounds are empty) */
  dynamic?: boolean;
  /** values above the maximum of the scale: clipped, clipped with a mark, or drawn as they are */
  over?: "clip" | "mark" | "asis";
  /** tube: thickness in px, constant (`max`) or by the value */
  radius?: { by: "fixed" | "value"; min: number; max: number; log: boolean };
  /** hidden categories and the order of categories in the legend */
  hidden?: string[];
  catOrder?: string[];
  /** events: a marker of its own for a value */
  catMarkers?: Record<string, MarkerShape>;
  filter?: TrackFilter;
  label?: TrackLabel;
  style?: StyleRef;
  palette?: StyleRef;
  /** colours of categories set by hand (value -> colour); others keep the automatic ones */
  catColors?: Record<string, string>;
  /** events: the marker and its size, px */
  marker?: MarkerShape;
  size?: number;
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
  /** the form this display was made in (displays of the 3D view) */
  form?: DisplayForm;
  /** the display template the layers came from */
  template?: StyleRef;
}

export const TRACK_TITLES: Record<TrackType, string> = {
  event: "События",
  line: "График (линия)",
  bars: "Столбцы",
  steps: "Ступени",
  points: "Точки",
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

export function newTrack(table: string, column: IntervalColumn, index: number, event = false): Track {
  const grade = /^(AU|AG|PT|PD)/i.test(column.name) || /g\/t|ppm|ppb|г\/т/i.test(column.unit);
  // a field of an event table: markers at their depth
  if (event) {
    return {
      id: newTrackId(), table, field: column.name, type: "event", width: 110, color: LOG_COLORS[index % LOG_COLORS.length],
      min: "", max: "", log: false, boundaries: false, marker: "diamond", size: 12,
    };
  }
  return {
    id: newTrackId(), table, field: column.name,
    type: column.kind === "text" ? "color_text" : "bars",
    width: column.kind === "text" ? 110 : 90,
    color: LOG_COLORS[index % LOG_COLORS.length], min: "", max: "", log: grade && column.kind === "number", boundaries: column.kind === "text",
  };
}

/** Default columns from a table: its text fields first (lithology), then up to three numbers. */
export function defaultTracks(table: IntervalTableInfo): Track[] {
  if (table.kind === "event") return table.columns.slice(0, 1).map((c, i) => newTrack(table.key, c, i, true));
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

const CLASS_COLORS = ["#9aa3b5", "#4f9dde", "#5fbf6a", "#e0c040", "#e8873a", "#e0364a", "#b03ad0"];

/** The colour of a value in a colouring by classes; null without classes or for a non-number. */
export function classColor(classes: ColorClass[] | undefined, v: unknown): string | null {
  if (!classes?.length || typeof v !== "number" || !Number.isFinite(v)) return null;
  let color = classes[0].from === null || v >= classes[0].from ? classes[0].color : null;
  for (const c of classes) if (c.from !== null && v >= c.from) color = c.color;
  return color;
}

/** The index of the class a value falls into (a value equal to a bound belongs to the upper class); -1 without one. */
export function classIndex(classes: ColorClass[] | undefined, v: unknown): number {
  if (!classes?.length || typeof v !== "number" || !Number.isFinite(v)) return -1;
  let index = classes[0].from === null || v >= classes[0].from ? 0 : -1;
  for (let i = 0; i < classes.length; i++) {
    const from = classes[i].from;
    if (from !== null && v >= from) index = i;
  }
  return index;
}

/** Classes kept in order: the one without a lower bound first, then ascending bounds (equal bounds dropped). */
export function sortClasses(classes: ColorClass[]): ColorClass[] {
  const open = classes.filter((c) => c.from === null).slice(0, 1);
  const bounded = classes.filter((c): c is ColorClass & { from: number } => c.from !== null && Number.isFinite(c.from)).sort((a, b) => a.from - b.from);
  return [...open, ...bounded.filter((c, i) => i === 0 || c.from !== bounded[i - 1].from)];
}

/** Default classes over a range: round bounds, linear or (for grades) by powers. */
export function defaultClasses(range: [number, number] | null, log: boolean, count = 5): ColorClass[] {
  const [lo, hi] = range ?? [0, 1];
  const bounds: number[] = [];
  if (log && hi > 0) {
    const a = Math.log10(Math.max(lo, hi / 1000, 1e-9));
    const b = Math.log10(hi);
    for (let i = 1; i < count; i++) bounds.push(10 ** (a + ((b - a) * i) / count));
  } else {
    for (let i = 1; i < count; i++) bounds.push(lo + ((hi - lo) * i) / count);
  }
  const round = (v: number) => Number(v.toPrecision(2));
  const out: ColorClass[] = [{ from: null, color: CLASS_COLORS[0] }];
  for (const b of [...new Set(bounds.map(round))]) out.push({ from: b, color: CLASS_COLORS[out.length % CLASS_COLORS.length] });
  return out;
}

/** "< 0.3", "0.3 – 1", "≥ 1": the text of a class in a legend. */
export function classLabel(classes: ColorClass[], i: number): string {
  const c = classes[i];
  if (c.label) return c.label;
  const next = classes[i + 1]?.from;
  const f = (v: number) => String(Number(v.toPrecision(4)));
  if (c.from === null) return next === undefined || next === null ? "все" : `< ${f(next)}`;
  return next === undefined || next === null ? `≥ ${f(c.from)}` : `${f(c.from)} – ${f(next)}`;
}

const KIND_NAMES: Record<TrackType, string> = {
  bars: "столбцы", steps: "ступени", line: "линия", points: "точки", color: "лента", color_text: "лента", text: "текст", event: "значки",
};

/** How a layer is drawn, in a word: "столбцы", "трубка", "лента", "значки". */
export function kindName(t: Track, tube = false): string {
  return (t.type === "color" || t.type === "color_text") && tube ? "трубка" : KIND_NAMES[t.type];
}

/** The name of a display made in a form, from its layers: "AU · столбцы + CU · лента". */
export function displayName(s: StripLogSettings, tableName?: string): string {
  const shown = s.tracks.filter((t) => t.visible !== false);
  if (!shown.length) return FORM_TITLES[s.form ?? "grade"];
  let first = true;
  const parts = shown.slice(0, 3).map((t) => {
    const colour = t.type === "color" || t.type === "color_text";
    const tube = colour && (t.form ? t.form === "tube" : first);
    if (colour) first = false;
    return `${t.field} · ${kindName(t, tube)}`;
  });
  const more = shown.length > 3 ? ` + ещё ${shown.length - 3}` : "";
  return `${parts.join(" + ")}${more}${shown.length === 1 && tableName && tableName !== "Пробы" ? ` (${tableName})` : ""}`;
}

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
