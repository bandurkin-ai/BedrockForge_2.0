/**
 * The classes of ore intervals and of their samples (ТЗ 7.35 Д-26 «Результат»; ADR 0018 §8, решения 20–21): the
 * colour of every class from the brand palette (ADR 0006, docs/brand/README.md — Copper Orange is оруденение;
 * the tints are mixes of the brand colours, nothing else), the Russian titles of the legend, the class of a
 * sample as the column «Классы» draws it (И1, сл. 10–16: элементарные рудные, некондиционные, включённые и
 * исключённые прослои, безрудные), the rows of the final column (NESTED = 0; `frame` hidden unless asked for),
 * and lanes for spans that overlap (прирезки of one stage share samples). A missing value has no colour. Pure.
 */

export type OreClass = "ore" | "subgrade" | "waste_in" | "interlayer" | "waste" | "frame";

/** In the order of the core (`ore_units.CLASSES`). */
export const ORE_CLASSES: readonly OreClass[] = ["ore", "subgrade", "waste_in", "interlayer", "waste", "frame"];

/** The brand palette (docs/brand/README.md). */
export const BRAND = {
  anthracite: "#1b222b",
  cobalt: "#1f6bff",
  copper: "#f46a1a",
  stone: "#e6e9ee",
  slate: "#8a99a8",
} as const;

function rgb(c: string): [number, number, number] {
  return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
}

/** `a` → `b` by `t` ∈ [0; 1]: a tint made of two brand colours. */
export function mix(a: string, b: string, t: number): string {
  const [x, y] = [rgb(a), rgb(b)];
  const k = Math.min(1, Math.max(0, t));
  return `#${x.map((v, i) => Math.round(v + (y[i] - v) * k).toString(16).padStart(2, "0")).join("")}`;
}

export interface ClassStyle {
  /** «кондиционный рудный» — the legend and the tooltips */
  title: string;
  /** the short word of a protocol cell */
  short: string;
  color: string;
}

export const CLASS_STYLE: Readonly<Record<OreClass, ClassStyle>> = {
  ore: { title: "кондиционный рудный", short: "руда", color: BRAND.copper },
  subgrade: { title: "некондиционный рудный", short: "некондиц.", color: mix(BRAND.copper, BRAND.stone, 0.55) },
  waste_in: { title: "включённый прослой", short: "вкл. прослой", color: mix(BRAND.slate, BRAND.stone, 0.3) },
  interlayer: { title: "исключённый прослой", short: "искл. прослой", color: BRAND.cobalt },
  waste: { title: "безрудный", short: "безрудный", color: mix(BRAND.anthracite, BRAND.slate, 0.45) },
  frame: { title: "обрамление", short: "обрамление", color: mix(BRAND.slate, BRAND.stone, 0.65) },
};

/** The colour of a прирезка (step 2) beside the column: Cobalt, the accent of the data. */
export const TRIM_COLOR = BRAND.cobalt;

export const isOreClass = (v: unknown): v is OreClass => typeof v === "string" && (ORE_CLASSES as readonly string[]).includes(v);

/** A class by its name or by its code (0…5 of the core); anything else — null. */
export function oreClass(v: unknown): OreClass | null {
  if (isOreClass(v)) return v;
  if (typeof v === "number" && Number.isInteger(v) && v >= 0 && v < ORE_CLASSES.length) return ORE_CLASSES[v];
  return null;
}

export const classColor = (v: unknown): string | null => {
  const c = oreClass(v);
  return c ? CLASS_STYLE[c].color : null;
};

export const classTitle = (v: unknown): string => {
  const c = oreClass(v);
  return c ? CLASS_STYLE[c].title : "вне линий опробования";
};

/** White or Anthracite — whichever reads on `color` (relative luminance). */
export function textOn(color: string): string {
  const [r, g, b] = rgb(color).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? BRAND.anthracite : "#ffffff";
}

/** The legend of the window: the classes that occur, `frame` only when shown. */
export function legendOf(present: Iterable<unknown>, showFrame: boolean): { cls: OreClass; title: string; color: string }[] {
  const seen = new Set<OreClass>();
  for (const v of present) {
    const c = oreClass(v);
    if (c) seen.add(c);
  }
  return ORE_CLASSES.filter((c) => seen.has(c) && (c !== "frame" || showFrame)).map((c) => ({ cls: c, title: CLASS_STYLE[c].title, color: CLASS_STYLE[c].color }));
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * The class of every sample as the column «Классы» draws it: ORE_CLS of the core; a sample of an `ore` interval
 * below Сб is a включённый прослой (the core may name the interval, not the sample); a sample without a value or
 * outside the sampling lines — null (hatched, never «безрудный»: a gap is not zero).
 */
export function sampleClasses(cls: readonly unknown[], grade: readonly unknown[], cutoff: number | null): (OreClass | null)[] {
  return cls.map((v, i) => {
    const c = oreClass(v);
    const g = grade[i];
    if (!finite(g)) return null;
    if (c === "ore" && cutoff !== null && g < cutoff * (1 - 1e-9)) return "waste_in";
    return c;
  });
}

export interface ResultRows {
  from: readonly (number | null)[];
  to: readonly (number | null)[];
  values: Record<string, readonly (number | string | null)[]>;
}

export interface FinalRow {
  index: number;
  from: number;
  to: number;
  cls: OreClass;
  nested: boolean;
  label: string;
}

/**
 * The rows of the final column: NESTED = 0 rows by depth, then the nested ones (`waste_in` over its `ore`,
 * `frame` beside it) — `frame` only when `showFrame` (решение 20: hidden by default). A row of no known class
 * or with TO ≤ FROM is left out. LABEL — only of `ore` (решение 20).
 */
export function finalRows(rows: ResultRows, showFrame: boolean): FinalRow[] {
  const cls = rows.values.CLASS ?? [];
  const nested = rows.values.NESTED ?? [];
  const labels = rows.values.LABEL ?? [];
  const out: FinalRow[] = [];
  for (let i = 0; i < rows.from.length; i++) {
    const a = rows.from[i];
    const b = rows.to[i];
    const c = oreClass(cls[i]);
    if (!c || !finite(a) || !finite(b) || b <= a) continue;
    if (c === "frame" && !showFrame) continue;
    const isNested = nested[i] === 1 || c === "waste_in" || c === "frame";
    const label = c === "ore" && typeof labels[i] === "string" ? (labels[i] as string) : "";
    out.push({ index: i, from: a, to: b, cls: c, nested: isNested, label });
  }
  return out.sort((x, y) => Number(x.nested) - Number(y.nested) || x.from - y.from || x.index - y.index);
}

export interface Span {
  row0: number;
  row1: number;
}

/**
 * Lanes for spans of rows that may overlap: each span gets the first lane free at its first row (greedy by
 * first row — the fewest lanes for intervals). Returns the lane of every span, in the order given.
 */
export function assignLanes(spans: readonly Span[]): number[] {
  const order = spans.map((_, i) => i).sort((a, b) => spans[a].row0 - spans[b].row0 || spans[a].row1 - spans[b].row1 || a - b);
  const ends: number[] = [];
  const lane = new Array<number>(spans.length).fill(0);
  for (const i of order) {
    const s = spans[i];
    let k = ends.findIndex((end) => end < s.row0);
    if (k < 0) {
      k = ends.length;
      ends.push(s.row1);
    } else ends[k] = s.row1;
    lane[i] = k;
  }
  return lane;
}

export const laneCount = (lanes: readonly number[]): number => (lanes.length ? Math.max(...lanes) + 1 : 0);
