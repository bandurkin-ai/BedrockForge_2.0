/**
 * A point cloud in the 3D window (ADR 0014 §2): what the manifest says about the cloud, the colour modes,
 * the palette 256×1 by `value u1` of a part (the ASPRS class code, or the value of `h` / `dz` in steps of
 * 0.02 m from −2.5 m, 255 — none), the visibility of classes as the alpha of the palette (a hidden class is
 * neither drawn nor picked), the levels of detail (the cell of a level ≤ 2 px on screen) and the point budget
 * of the frame. Pure: no three.js, no DOM.
 */
import { applyTriangleBudget, type BudgetPart } from "../core/budget";
import { stepBoundaries } from "../core/lod";

/** The modes of colour: «Класс», «Цвет слоя», «Высота над землёй», «Отклонение от ЦТМ». */
export type CloudColorMode = "class" | "layer" | "h" | "dz";
/** What a part is asked with (`?color=`): the layer colour draws the classes (their visibility holds). */
export type CloudValueMode = "class" | "h" | "dz";

export const CLOUD_MODES: readonly CloudColorMode[] = ["class", "layer", "h", "dz"];

export interface CloudValueScale {
  step: number;
  min: number;
  max: number;
  /** the code of «no value» */
  empty: number;
}

export interface CloudInfo {
  /** points of every class code in the cloud */
  classes: Record<number, number>;
  /** the value modes the core can answer (`class` always, `h` after the ground filter, `dz` after a DTM) */
  modes: CloudValueMode[];
  /** the column of a value mode by its content (`modes[].key` of the core): another key — other values */
  keys: Partial<Record<CloudValueMode, string>>;
  value: CloudValueScale;
  source: { kind?: string; every?: number } | null;
  ground: unknown;
  dz: { wireframe_id?: number; name?: string } | null;
}

/** The style of a cloud kept by the project (the proposed view key `cloud_styles`, see the report of 7.1.13). */
export interface CloudStyle {
  color?: CloudColorMode;
  /** hidden class codes */
  hidden?: readonly number[];
}

export const DEFAULT_VALUE_SCALE: CloudValueScale = { step: 0.02, min: -2.5, max: 2.5, empty: 255 };

interface CloudHeader {
  classes?: Record<string, number> | null;
  modes?: { mode: string; key?: string }[] | null;
  value?: Partial<CloudValueScale> | null;
  source?: { kind?: string; every?: number } | null;
  ground?: unknown;
  dz?: { wireframe_id?: number; name?: string } | null;
}

/** `cloud` of a manifest (null — the layer is an ordinary layer of points). */
export function readCloudInfo(h: CloudHeader | null | undefined): CloudInfo | null {
  if (!h || typeof h !== "object") return null;
  const classes: Record<number, number> = {};
  for (const [code, n] of Object.entries(h.classes ?? {})) {
    const c = Number(code);
    if (Number.isInteger(c) && c >= 0 && c <= 255 && typeof n === "number" && n > 0) classes[c] = n;
  }
  const modes = (h.modes ?? []).map((m) => m?.mode).filter((m): m is CloudValueMode => m === "class" || m === "h" || m === "dz");
  if (!modes.includes("class")) modes.unshift("class");
  const keys: CloudInfo["keys"] = {};
  for (const m of h.modes ?? []) if ((m?.mode === "class" || m?.mode === "h" || m?.mode === "dz") && typeof m.key === "string") keys[m.mode] = m.key;
  const v = h.value ?? {};
  const num = (x: unknown, d: number) => (typeof x === "number" && Number.isFinite(x) ? x : d);
  const value = { step: num(v.step, DEFAULT_VALUE_SCALE.step), min: num(v.min, DEFAULT_VALUE_SCALE.min), max: num(v.max, DEFAULT_VALUE_SCALE.max), empty: num(v.empty, DEFAULT_VALUE_SCALE.empty) };
  return { classes, modes, keys, value, source: h.source ?? null, ground: h.ground ?? null, dz: h.dz ?? null };
}

/** The mode in force: the asked one when the cloud has it; else «Класс» when there are classes, else «Цвет слоя». */
export function cloudMode(info: CloudInfo, asked: CloudColorMode | undefined): CloudColorMode {
  if (asked === "layer") return "layer";
  if (asked === "class" || ((asked === "h" || asked === "dz") && info.modes.includes(asked))) return asked;
  return Object.keys(info.classes).length ? "class" : "layer";
}

/** What a part is asked for in a mode: the layer colour still draws the classes. */
export const valueMode = (mode: CloudColorMode): CloudValueMode => (mode === "layer" ? "class" : mode);

// --- the palette ------------------------------------------------------------------------------------------

/**
 * Colours of the ASPRS classes (LAS 1.4 R15, table 17): the usual colours of the classes in survey software,
 * noise in magenta, water in Cobalt of the brand. Codes without a colour of their own get one by the golden
 * angle (the same code — the same colour in every window).
 */
export const CLASS_COLORS: Readonly<Record<number, string>> = {
  0: "#8a99a8", // created, never classified — Slate
  1: "#c9ced6", // unclassified
  2: "#b07a3b", // ground
  3: "#a6d96a", // low vegetation
  4: "#4cae5a", // medium vegetation
  5: "#1f7a3a", // high vegetation
  6: "#e0364a", // building
  7: "#ff2bd6", // low point (noise)
  8: "#f2d43a", // reserved (model key point in LAS 1.2)
  9: "#1f6bff", // water — Cobalt
  10: "#7b5a45", // rail
  11: "#5c6670", // road surface
  12: "#f6c28b", // reserved (overlap in LAS 1.2)
  13: "#ffd54f", // wire — guard
  14: "#ffb300", // wire — conductor
  15: "#8d6e63", // transmission tower
  16: "#ffca28", // wire-structure connector
  17: "#9575cd", // bridge deck
  18: "#c2185b", // high noise
};

/** The colour of a value of «no value» in the modes with numbers: dim Slate, never the colour of 0. */
export const EMPTY_VALUE_COLOR = "#59636e";
/** The diverging scale of `h` and `dz`: Cobalt below, Light Stone at 0, Copper Orange above (the brand). */
export const VALUE_SCALE_STOPS = ["#1f6bff", "#e6e9ee", "#f46a1a"] as const;

function rgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function hsl(h: number, s: number, l: number): [number, number, number] {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return [f(0), f(8), f(4)];
}

/** The colour of a class code, 0…255. */
export function classColor(code: number): [number, number, number] {
  const own = CLASS_COLORS[code];
  return own ? rgb(own) : hsl((code * 137.508) % 360, 0.55, 0.55);
}

export function classHex(code: number): string {
  return `#${classColor(code).map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** The colour of the diverging scale at a value, m (cut to the scale). */
export function scaleColor(v: number, scale: CloudValueScale): [number, number, number] {
  const half = Math.max(scale.max, -scale.min) || 1;
  const t = Math.max(-1, Math.min(1, v / half));
  const [a, b] = t < 0 ? [rgb(VALUE_SCALE_STOPS[1]), rgb(VALUE_SCALE_STOPS[0])] : [rgb(VALUE_SCALE_STOPS[1]), rgb(VALUE_SCALE_STOPS[2])];
  const f = Math.abs(t);
  return [Math.round(a[0] + (b[0] - a[0]) * f), Math.round(a[1] + (b[1] - a[1]) * f), Math.round(a[2] + (b[2] - a[2]) * f)];
}

/** The value, m, of a code of a mode with numbers; null — no value. */
export function valueOf(code: number, scale: CloudValueScale): number | null {
  if (code === scale.empty) return null;
  return Math.round((scale.min + code * scale.step) * 1000) / 1000;
}

/**
 * The palette 256×1 RGBA of a mode (texture data). «Класс» and «Цвет слоя»: alpha 0 for a hidden class — its
 * points are neither drawn nor picked. «h» and «dz»: the value carries no class, every point is shown; the
 * codes past the scale and «no value» are dim Slate.
 */
export function cloudPalette(mode: CloudColorMode, hidden: ReadonlySet<number>, layerColor: string, scale: CloudValueScale = DEFAULT_VALUE_SCALE): Uint8Array {
  const out = new Uint8Array(256 * 4);
  const layer = rgb(/^#[0-9a-f]{6}$/i.test(layerColor) ? layerColor : "#8a99a8");
  const empty = rgb(EMPTY_VALUE_COLOR);
  const top = Math.round((scale.max - scale.min) / scale.step);
  for (let code = 0; code < 256; code++) {
    let c: [number, number, number];
    let shown = true;
    if (mode === "class" || mode === "layer") {
      c = mode === "class" ? classColor(code) : layer;
      shown = !hidden.has(code);
    } else c = code === scale.empty || code > top ? empty : scaleColor(scale.min + code * scale.step, scale);
    out.set([c[0], c[1], c[2], shown ? 255 : 0], code * 4);
  }
  return out;
}

/** The codes shown by a palette: 1 — drawn and picked (the alpha of the palette). */
export function shownCodes(palette: Uint8Array): Uint8Array {
  const out = new Uint8Array(256);
  for (let i = 0; i < 256; i++) out[i] = palette[i * 4 + 3] > 0 ? 1 : 0;
  return out;
}

// --- levels and the budget --------------------------------------------------------------------------------

/** A level is coarse enough while its cell is at most this on screen, px (ADR 0014 §2). */
export const CLOUD_LOD_PX = 2;

/** Boundaries of pixels per metre for the cells of the levels of a cloud (0; 0,5; 2; 8; 32 m). */
export function cloudBoundaries(tols: readonly number[]): number[] {
  return stepBoundaries(tols, CLOUD_LOD_PX);
}

export interface CloudBudgetPart {
  key: string;
  /** size on screen, px: the smallest go coarser first */
  screen: number;
  /** points of every level, finest first (`levels` of the manifest) */
  points: readonly number[];
  /** the level chosen by the distance */
  level: number;
}

/**
 * Keeps the points of a frame within `budget` (`POINT_BUDGET`): the rule of `applyTriangleBudget` — the
 * smallest parts on screen go coarser first; `simplified` — the budget coarsened something («упрощено»).
 */
export function applyPointBudget(parts: readonly CloudBudgetPart[], budget: number): { levels: number[]; points: number; simplified: boolean; over: boolean } {
  const input: BudgetPart[] = parts.map((p) => ({ key: p.key, screen: p.screen, triangles: p.points, level: p.level }));
  const r = applyTriangleBudget(input, budget);
  return { levels: r.levels, points: r.triangles, simplified: r.simplified, over: r.over };
}

// --- the style kept by the project ------------------------------------------------------------------------

/** The view key with the styles of clouds: {"<layer id>": {color, hidden}} (to be added to the core's keys). */
export const CLOUD_STYLES_KEY = "cloud_styles";

/** The style of cloud `id` from the value of `cloud_styles` (junk is dropped: an old project never breaks). */
export function cloudStyleOf(styles: unknown, id: number): CloudStyle | null {
  if (!styles || typeof styles !== "object" || Array.isArray(styles)) return null;
  const v = (styles as Record<string, unknown>)[String(id)];
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as { color?: unknown; hidden?: unknown };
  const out: CloudStyle = {};
  if (typeof o.color === "string" && (CLOUD_MODES as readonly string[]).includes(o.color)) out.color = o.color as CloudColorMode;
  if (Array.isArray(o.hidden)) out.hidden = o.hidden.filter((c): c is number => Number.isInteger(c) && c >= 0 && c <= 255);
  return out;
}
