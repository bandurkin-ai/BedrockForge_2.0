import type { BlockAttribute, BlockModelMeta } from "../api";
import { classLabel, type ColorClass } from "../charts/stripLog";
import { ColorScale } from "./colorScale";
import { gradientCss, gradientRgb, hexToRgb } from "./downhole/gradients";
import type { PaletteBody } from "./downhole/styles";
import { parseNumber } from "../stats/chartMath";

/** A condition of the restriction: a field compared with a value (a number, or a category label). */
export type BlockOp = "eq" | "ne" | "gt" | "ge" | "lt" | "le" | "empty" | "filled";
export interface BlockCondition {
  /** attribute key */
  field: string;
  op: BlockOp;
  value: string;
}

/** The restriction of a model: only the blocks that meet the conditions are shown (after Micromine's filter). */
export interface BlockFilter {
  on: boolean;
  /** all the conditions, or any of them */
  join: "and" | "or";
  /** show the blocks that do not pass */
  invert: boolean;
  conditions: BlockCondition[];
}

export const OP_TITLES: Record<BlockOp, string> = {
  eq: "равно", ne: "не равно", gt: "больше", ge: "больше или равно", lt: "меньше", le: "меньше или равно", empty: "пусто", filled: "не пусто",
};
/** Operators of a field by its kind: categories are only compared for equality. */
export const opsFor = (kind: BlockAttribute["kind"] | undefined): BlockOp[] =>
  kind === "category" ? ["eq", "ne", "empty", "filled"] : ["eq", "ne", "gt", "ge", "lt", "le", "empty", "filled"];
export const opNeedsValue = (op: BlockOp) => op !== "empty" && op !== "filled";

/** How a block model is shown: which blocks, their colour by a field, and how they are drawn. */
export interface BlockStyle {
  /** attribute key; null: one colour */
  attribute: string | null;
  /** the colour of a model shown in one colour */
  color: string;
  /** a number: a continuous scale, or grade classes */
  colorBy: "scale" | "classes";
  /** the scale: its gradient and range ("" = automatic, 5–95th percentile) */
  gradient: string;
  reverse: boolean;
  min: string;
  max: string;
  log: boolean;
  classes: ColorClass[];
  /** colour of empty values and of values outside the classes */
  noDataColor: string;
  /** colours of categories set by hand (label -> colour); the others keep the automatic ones */
  catColors: Record<string, string>;
  /** categories not shown */
  hidden: string[];
  opacity: number;
  filter: BlockFilter;
  /** "shell": only the blocks seen from outside; "all": every block that passes */
  view: "shell" | "all";
  /** in a clipped section: the cut of the blocks by the section plane instead of the blocks themselves */
  slice: boolean;
  /** outlines of the blocks */
  edges: boolean;
  edgeColor: string;
  /** px */
  edgeWidth: number;
  /** where the colouring was taken from (a palette of the library or a display of drillholes) */
  palette?: { id: number | null; name: string };
}

export const CATEGORY_COLORS = ["#4f9dde", "#e8873a", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a",
  "#2f6db5", "#b5651d", "#3c8d4a", "#8a4fa8", "#a89020", "#a83a4a", "#2a8f8a", "#6b6355"];
const ONE_COLOR = "#b09070";
const EMPTY_COLOR = "#969eac";
export const NO_FILTER: BlockFilter = { on: false, join: "and", invert: false, conditions: [] };

const BASE: Omit<BlockStyle, "attribute" | "log"> = {
  color: ONE_COLOR, colorBy: "scale", gradient: "classic", reverse: false, min: "", max: "", classes: [], noDataColor: EMPTY_COLOR,
  catColors: {}, hidden: [], opacity: 1, filter: NO_FILTER, view: "shell", slice: true, edges: false, edgeColor: "#1c2230", edgeWidth: 1,
};

const isLogUnit = (a: BlockAttribute | undefined) => a?.unit === "g/t" || a?.unit === "ppm" || a?.unit === "ppb";

/** Default: the first grade of the report (or the first number), on a log scale for g/t and ppm. */
export function defaultBlockStyle(meta: BlockModelMeta): BlockStyle {
  const numbers = meta.attributes.filter((a) => a.kind === "number");
  const key = meta.report?.grades?.[0] ?? numbers.find((a) => a.unit)?.key ?? numbers[0]?.key ?? meta.attributes[0]?.key ?? null;
  return { ...BASE, attribute: key, log: isLogUnit(meta.attributes.find((x) => x.key === key)) };
}

const HEX = /^#[0-9a-f]{6}$/i;
const OPS = new Set<string>(Object.keys(OP_TITLES));

/** A style from stored state (the workspace): unknown or broken values fall back to the defaults. */
export function normalizeBlockStyle(raw: unknown): BlockStyle {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof BlockStyle, unknown>>;
  const bool = (v: unknown, fallback: boolean) => (typeof v === "boolean" ? v : fallback);
  const hex = (v: unknown, fallback: string) => (typeof v === "string" && HEX.test(v) ? v : fallback);
  const text = (v: unknown, fallback = "") => (typeof v === "string" ? v.slice(0, 200) : fallback);
  const num = (v: unknown, lo: number, hi: number, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback);
  const f = (r.filter && typeof r.filter === "object" ? r.filter : {}) as Partial<Record<keyof BlockFilter, unknown>>;
  const classes = (Array.isArray(r.classes) ? r.classes : []).slice(0, 40).flatMap((c): ColorClass[] => {
    const x = (c && typeof c === "object" ? c : {}) as Partial<ColorClass>;
    if (typeof x.color !== "string" || !HEX.test(x.color)) return [];
    const from = typeof x.from === "number" && Number.isFinite(x.from) ? x.from : null;
    return [{ from, color: x.color, ...(typeof x.label === "string" && x.label ? { label: x.label.slice(0, 80) } : {}), ...(x.hidden === true ? { hidden: true } : {}) }];
  });
  const p = (r.palette && typeof r.palette === "object" ? r.palette : null) as { id?: unknown; name?: unknown } | null;
  return {
    attribute: typeof r.attribute === "string" ? r.attribute : null,
    color: hex(r.color, BASE.color),
    colorBy: r.colorBy === "classes" && classes.length ? "classes" : "scale",
    gradient: text(r.gradient, BASE.gradient) || BASE.gradient,
    reverse: bool(r.reverse, false),
    min: text(r.min), max: text(r.max),
    log: bool(r.log, false),
    classes,
    noDataColor: hex(r.noDataColor, BASE.noDataColor),
    catColors: Object.fromEntries(Object.entries(r.catColors && typeof r.catColors === "object" ? (r.catColors as Record<string, unknown>) : {})
      .filter(([, v]) => typeof v === "string" && HEX.test(v)).slice(0, 2000)) as Record<string, string>,
    hidden: Array.isArray(r.hidden) ? r.hidden.filter((h): h is string => typeof h === "string").slice(0, 2000) : [],
    opacity: num(r.opacity, 0.1, 1, 1),
    filter: {
      on: bool(f.on, false),
      join: f.join === "or" ? "or" : "and",
      invert: bool(f.invert, false),
      conditions: (Array.isArray(f.conditions) ? f.conditions : []).slice(0, 20).flatMap((c): BlockCondition[] => {
        const x = (c && typeof c === "object" ? c : {}) as Partial<Record<keyof BlockCondition, unknown>>;
        return typeof x.field === "string" && typeof x.op === "string" && OPS.has(x.op) ? [{ field: x.field, op: x.op as BlockOp, value: text(x.value) }] : [];
      }),
    },
    view: r.view === "all" ? "all" : "shell",
    slice: bool(r.slice, BASE.slice),
    edges: bool(r.edges, false),
    edgeColor: hex(r.edgeColor, BASE.edgeColor),
    edgeWidth: num(r.edgeWidth, 0.5, 8, BASE.edgeWidth),
    ...(p && typeof p.name === "string" ? { palette: { id: typeof p.id === "number" ? p.id : null, name: p.name.slice(0, 120) } } : {}),
  };
}

/** 5th and 95th percentiles of the finite values (a sample of at most 50 000 for large models). */
export function percentileRange(values: Float32Array, positive = false): [number, number] | null {
  const step = Math.max(1, Math.floor(values.length / 50000));
  const sample: number[] = [];
  for (let i = 0; i < values.length; i += step) {
    const v = values[i];
    if (Number.isFinite(v) && (!positive || v > 0)) sample.push(v);
  }
  if (!sample.length) return null;
  sample.sort((a, b) => a - b);
  const at = (p: number) => sample[Math.min(sample.length - 1, Math.max(0, Math.round(p * (sample.length - 1))))];
  let lo = at(0.05);
  let hi = at(0.95);
  if (!(hi > lo)) {
    lo = sample[0];
    hi = sample[sample.length - 1];
  }
  if (!(hi > lo)) hi = lo === 0 ? 1 : lo * 10;
  return [lo, hi];
}

/** The finite values of a field, sorted (every k-th of a large model): for the histogram of the class editor. */
export function sampleValues(values: Float32Array, limit = 200_000): Float64Array {
  const step = Math.max(1, Math.ceil(values.length / limit));
  const out: number[] = [];
  for (let i = 0; i < values.length; i += step) if (Number.isFinite(values[i])) out.push(values[i]);
  return Float64Array.from(out).sort();
}

/** What the legend of a model shows. */
export type BlockLegendItems =
  | { kind: "scale"; min: number; max: number; log: boolean; css: string }
  | { kind: "swatches"; items: [label: string, color: string][] }
  | null;

export interface BlockColors {
  /** rgb per block, 0–255 */
  colors: Uint8Array;
  /** 1: shown */
  visible: Uint8Array;
  shown: number;
  scale: ColorScale | null;
  legend: BlockLegendItems;
}

/** The colour of every category of a field: set by hand, or by its code. */
export function categoryColor(style: Pick<BlockStyle, "catColors">, label: string, code: number): string {
  return style.catColors[label] ?? CATEGORY_COLORS[code % CATEGORY_COLORS.length];
}

/** Colours and visibility of every block for a style; `values` are the attribute's values (or null). */
export function blockColors(n: number, attribute: BlockAttribute | null, values: Float32Array | null, style: BlockStyle): BlockColors {
  const colors = new Uint8Array(n * 3);
  const visible = new Uint8Array(n).fill(1);
  if (!attribute || !values) {
    const [r, g, b] = hexRgb(style.color);
    for (let i = 0, k = 0; i < n; i++, k += 3) {
      colors[k] = r; colors[k + 1] = g; colors[k + 2] = b;
    }
    return { colors, visible, shown: n, scale: null, legend: null };
  }
  const empty = hexRgb(style.noDataColor);
  let shown = 0;
  if (attribute.kind === "category") {
    const labels = attribute.labels ?? [];
    const off = new Uint8Array(labels.length + 1);
    const palette = new Uint8Array((labels.length + 1) * 3);
    labels.forEach((label, code) => {
      off[code] = style.hidden.includes(label) ? 1 : 0;
      palette.set(hexRgb(categoryColor(style, label, code)), code * 3);
    });
    // The last entry: blocks without a value.
    off[labels.length] = style.hidden.includes("") ? 1 : 0;
    palette.set(empty, labels.length * 3);
    for (let i = 0; i < n; i++) {
      const v = values[i];
      const code = v >= 0 && v < labels.length ? v | 0 : labels.length;
      if (off[code]) {
        visible[i] = 0;
        continue;
      }
      const k = code * 3;
      colors[i * 3] = palette[k]; colors[i * 3 + 1] = palette[k + 1]; colors[i * 3 + 2] = palette[k + 2];
      shown++;
    }
    const items = labels.map((label, code): [string, string] => [label, categoryColor(style, label, code)]).filter(([label]) => !style.hidden.includes(label));
    return { colors, visible, shown, scale: null, legend: { kind: "swatches", items } };
  }
  if (style.colorBy === "classes" && style.classes.length) {
    const classes = style.classes;
    const open = classes[0].from === null;
    const bounds = classes.map((c) => (c.from === null ? -Infinity : c.from));
    const rgb = classes.map((c) => hexRgb(c.color));
    for (let i = 0; i < n; i++) {
      const v = values[i];
      let k = -1;
      if (v === v) {
        // The last class whose lower bound is not above the value (as classIndex of the drillhole displays).
        for (let j = classes.length - 1; j >= 0; j--) {
          if (v >= bounds[j]) {
            k = j;
            break;
          }
        }
        if (k === 0 && !open && v < bounds[0]) k = -1;
      }
      if (k >= 0 && classes[k].hidden) {
        visible[i] = 0;
        continue;
      }
      const c = k >= 0 ? rgb[k] : empty;
      colors[i * 3] = c[0]; colors[i * 3 + 1] = c[1]; colors[i * 3 + 2] = c[2];
      shown++;
    }
    const items = classes.map((c, i): [string, string] => [classLabel(classes, i), c.color]).filter((_, i) => !classes[i].hidden);
    return { colors, visible, shown, scale: null, legend: { kind: "swatches", items } };
  }
  const auto = percentileRange(values, style.log);
  const lo = parseNumber(style.min) ?? auto?.[0] ?? 0;
  const hi = parseNumber(style.max) ?? auto?.[1] ?? 1;
  const scale = new ColorScale(lo, hi, style.log);
  // 256 steps of the gradient: a block's colour is a lookup, not an interpolation.
  const lut = new Uint8Array(256 * 3);
  for (let j = 0; j < 256; j++) lut.set(gradientRgb(style.gradient, j / 255, style.reverse).map(Math.round), j * 3);
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v === v) {
      const k = Math.round(scale.position(v) * 255) * 3;
      colors[i * 3] = lut[k]; colors[i * 3 + 1] = lut[k + 1]; colors[i * 3 + 2] = lut[k + 2];
    } else {
      colors[i * 3] = empty[0]; colors[i * 3 + 1] = empty[1]; colors[i * 3 + 2] = empty[2];
    }
  }
  return { colors, visible, shown: n, scale, legend: { kind: "scale", min: scale.min, max: scale.max, log: scale.log, css: gradientCss(style.gradient, style.reverse) } };
}

/** The conditions of a restriction that can be applied: a known field, and a value where one is needed. */
export function activeConditions(filter: BlockFilter, attributes: BlockAttribute[]): { attribute: BlockAttribute; op: BlockOp; value: number }[] {
  if (!filter.on) return [];
  const out: { attribute: BlockAttribute; op: BlockOp; value: number }[] = [];
  for (const c of filter.conditions) {
    const attribute = attributes.find((a) => a.key === c.field);
    if (!attribute) continue;
    if (!opNeedsValue(c.op)) {
      out.push({ attribute, op: c.op, value: NaN });
    } else if (attribute.kind === "category") {
      // A label that is not in the model: no block has it (code -1 is never stored as a value).
      out.push({ attribute, op: c.op === "ne" ? "ne" : "eq", value: (attribute.labels ?? []).indexOf(c.value) });
    } else {
      const value = parseNumber(c.value);
      if (value !== null) out.push({ attribute, op: c.op, value });
    }
  }
  return out;
}

/**
 * Which blocks pass the restriction: 1 per block, or null when it is off or has no complete condition.
 * `valuesOf` gives a field's values per block (categories: codes, empty: NaN). An empty value passes
 * only «не равно» and «пусто».
 */
export function filterMask(n: number, filter: BlockFilter, attributes: BlockAttribute[], valuesOf: (key: string) => Float32Array | null): Uint8Array | null {
  const conditions = activeConditions(filter, attributes).filter((c) => valuesOf(c.attribute.key));
  if (!conditions.length) return null;
  const and = filter.join === "and";
  const mask = new Uint8Array(n).fill(and ? 1 : 0);
  for (const c of conditions) {
    const v = valuesOf(c.attribute.key) as Float32Array;
    const x = c.value;
    for (let i = 0; i < n; i++) {
      // Blocks already decided are skipped: failed under «и», passed under «или».
      if (mask[i] !== (and ? 1 : 0)) continue;
      const a = v[i];
      let ok: boolean;
      switch (c.op) {
        case "eq": ok = a === x; break;
        case "ne": ok = a !== x; break;
        case "gt": ok = a > x; break;
        case "ge": ok = a >= x; break;
        case "lt": ok = a < x; break;
        case "le": ok = a <= x; break;
        case "empty": ok = a !== a; break;
        default: ok = a === a;
      }
      if (ok !== and) mask[i] = ok ? 1 : 0;
    }
  }
  if (filter.invert) for (let i = 0; i < n; i++) mask[i] ^= 1;
  return mask;
}

/** Most cells of the parent grid the shell is computed for in the browser (a byte, or a byte and a float, per cell). */
const MAX_SHELL_CELLS = 300_000_000;
const MAX_SUBBLOCK_CELLS = 80_000_000;

/**
 * The outer shell of the blocks that are shown: 1 for a block that can be seen from outside, 0 for one
 * closed on every side by shown neighbours (the server's `exposed_mask`, over a part of the model).
 * `render` holds the centre (relative to `renderOrigin`) and the size of every block. Returns null
 * when the grid is too large to work on here.
 */
export function shellMask(render: Float32Array, visible: Uint8Array, grid: { origin: number[]; parent: number[]; counts: number[]; subblocked: boolean }, renderOrigin: number[]): Uint8Array | null {
  const n = visible.length;
  const nx = Math.max(1, grid.counts[0] | 0);
  const ny = Math.max(1, grid.counts[1] | 0);
  const nz = Math.max(1, grid.counts[2] | 0);
  const cells = nx * ny * nz;
  const [px, py, pz] = grid.parent;
  if (!(px > 0 && py > 0 && pz > 0) || cells > (grid.subblocked ? MAX_SUBBLOCK_CELLS : MAX_SHELL_CELLS)) return null;
  const ox = grid.origin[0] - renderOrigin[0];
  const oy = grid.origin[1] - renderOrigin[1];
  const oz = grid.origin[2] - renderOrigin[2];
  // The copy for display is float32: bounds are compared with a tolerance above its rounding.
  const reach = Math.max(Math.abs(ox) + nx * px, Math.abs(oy) + ny * py, Math.abs(oz) + nz * pz);
  const tx = Math.max(px * 1e-3, reach * 5e-7);
  const ty = Math.max(py * 1e-3, reach * 5e-7);
  const tz = Math.max(pz * 1e-3, reach * 5e-7);
  const out = new Uint8Array(n);
  // The cell of every shown block (-1: it crosses its cell or lies outside the grid: always shown).
  const cell = new Int32Array(n).fill(-1);
  const full = new Uint8Array(cells);
  const filled = grid.subblocked ? new Float32Array(cells) : null;
  for (let i = 0, b = 0; i < n; i++, b += 6) {
    if (!visible[i]) continue;
    out[i] = 1;
    const hx = render[b + 3] / 2, hy = render[b + 4] / 2, hz = render[b + 5] / 2;
    const ix = Math.floor((render[b] - hx - ox + tx) / px);
    const iy = Math.floor((render[b + 1] - hy - oy + ty) / py);
    const iz = Math.floor((render[b + 2] - hz - oz + tz) / pz);
    if (ix < 0 || iy < 0 || iz < 0 || ix >= nx || iy >= ny || iz >= nz) continue;
    if (ix !== Math.floor((render[b] + hx - ox - tx) / px) || iy !== Math.floor((render[b + 1] + hy - oy - ty) / py) || iz !== Math.floor((render[b + 2] + hz - oz - tz) / pz)) continue;
    const lin = (ix * ny + iy) * nz + iz;
    cell[i] = lin;
    if (filled) filled[lin] += render[b + 3] * render[b + 4] * render[b + 5];
    else full[lin] = 1;
  }
  if (filled) {
    const enough = px * py * pz * (1 - 1e-4);
    for (let c = 0; c < cells; c++) if (filled[c] >= enough) full[c] = 1;
  }
  const stepX = ny * nz;
  for (let i = 0, b = 0; i < n; i++, b += 6) {
    const lin = cell[i];
    if (lin < 0 || !full[lin]) continue;
    const iz = lin % nz;
    const iy = ((lin - iz) / nz) % ny;
    const ix = (lin - iz - iy * nz) / stepX;
    const hx = render[b + 3] / 2, hy = render[b + 4] / 2, hz = render[b + 5] / 2;
    const x0 = ox + ix * px, y0 = oy + iy * py, z0 = oz + iz * pz;
    // A face is seen when it lies on the boundary of its cell and the cell beyond is not full.
    const seen =
      (Math.abs(render[b] - hx - x0) <= tx && (ix === 0 || !full[lin - stepX])) ||
      (Math.abs(render[b] + hx - x0 - px) <= tx && (ix === nx - 1 || !full[lin + stepX])) ||
      (Math.abs(render[b + 1] - hy - y0) <= ty && (iy === 0 || !full[lin - nz])) ||
      (Math.abs(render[b + 1] + hy - y0 - py) <= ty && (iy === ny - 1 || !full[lin + nz])) ||
      (Math.abs(render[b + 2] - hz - z0) <= tz && (iz === 0 || !full[lin - 1])) ||
      (Math.abs(render[b + 2] + hz - z0 - pz) <= tz && (iz === nz - 1 || !full[lin + 1]));
    if (!seen) out[i] = 0;
  }
  return out;
}

/** The colouring of a style as a palette of the project's library (the same the drillhole displays keep). */
export function paletteOf(style: BlockStyle, attribute: BlockAttribute): PaletteBody {
  if (attribute.kind === "category") {
    const labels = attribute.labels ?? [];
    return {
      kind: "categories",
      catColors: Object.fromEntries(labels.map((label, code) => [label, categoryColor(style, label, code)])),
      ...(style.hidden.length ? { hidden: style.hidden } : {}),
    };
  }
  if (style.colorBy === "classes" && style.classes.length) return { kind: "classes", classes: style.classes.map((c) => ({ ...c })) };
  return { kind: "gradient", gradient: { name: style.gradient, reverse: style.reverse, stretch: style.log ? "log" : "linear", min: style.min, max: style.max } };
}

/** Does a palette colour a field of this kind? Classes and gradients colour numbers, categories colour text. */
export const paletteFitsBlock = (body: PaletteBody | null | undefined, attribute: BlockAttribute): body is PaletteBody =>
  !!body && (body.kind === "categories") === (attribute.kind === "category") && (body.kind !== "categories" || Object.keys(body.catColors ?? {}).length > 0);

/** The style with the colouring of a palette (a copy of its settings). */
export function withPalette(style: BlockStyle, body: PaletteBody, source: { id: number | null; name: string }): BlockStyle {
  const palette = { id: source.id, name: source.name };
  if (body.kind === "classes") return { ...style, colorBy: "classes", classes: body.classes.map((c) => ({ ...c })), palette };
  if (body.kind === "gradient") {
    const g = body.gradient;
    return { ...style, colorBy: "scale", gradient: g.name, reverse: g.reverse, min: g.min ?? "", max: g.max ?? "", log: g.stretch === "log", palette };
  }
  return { ...style, catColors: { ...body.catColors }, hidden: [...(body.hidden ?? [])], palette };
}

export function hexRgb(hex: string): [number, number, number] {
  return hexToRgb(hex);
}
