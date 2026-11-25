/**
 * The palettes of the window «Блочная модель · …» (ТЗ 6.8, 8.7.6; ADR 0023 §11): a palette is a way to fill the colours of
 * the classes of the legend — 256 buckets of a scale, one colour per class of the breaks, one per code of a category.
 * Choosing one writes the colours into the style (`colour.colours`, `colour.categories`) — the layer takes them from
 * there and asks the core for nothing; «Шкала проекта» is no colours at all (the ramp of the project is the default of
 * the legend). Pure: no React, no three.js.
 */
import { ColorScale } from "../../../viz/color/colorScale";
import type { ColourKind } from "../../../viz/blocks/decode";
import { CATEGORY_COLORS } from "../../../viz/blocks/legend";

export type Rgb = readonly [number, number, number];

export interface Palette {
  id: string;
  title: string;
  /** colours of a ramp; null — the palette is the default of the legend or a list of separate colours (`discrete`) */
  stops: readonly Rgb[] | null;
  /** separate colours taken one after another and cycled (the classes and categories only) */
  discrete: readonly string[] | null;
  kinds: readonly ColourKind[];
}

export const PROJECT_PALETTE = "project";
// The ids are not plain words on purpose: the style of a model that no palette was chosen for has the name «viridis» (the
// default of `readStyle`), which is not one of these — it is read as «Шкала проекта».

const SOFT = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];

export const PALETTES: readonly Palette[] = [
  { id: PROJECT_PALETTE, title: "Шкала проекта", stops: null, discrete: null, kinds: ["scale", "classes", "categories"] },
  {
    id: "ramp.viridis",
    title: "Виридис",
    stops: [
      [68, 1, 84],
      [59, 82, 139],
      [33, 145, 140],
      [94, 201, 98],
      [253, 231, 37],
    ],
    discrete: null,
    kinds: ["scale", "classes"],
  },
  {
    id: "ramp.heat",
    title: "Тепловая",
    stops: [
      [255, 255, 204],
      [254, 217, 118],
      [253, 141, 60],
      [227, 26, 28],
      [128, 0, 38],
    ],
    discrete: null,
    kinds: ["scale", "classes"],
  },
  {
    id: "ramp.cool",
    title: "Холодная",
    stops: [
      [247, 251, 255],
      [198, 219, 239],
      [107, 174, 214],
      [33, 113, 181],
      [8, 48, 107],
    ],
    discrete: null,
    kinds: ["scale", "classes"],
  },
  {
    id: "ramp.diverging",
    title: "Сине-красная",
    stops: [
      [33, 102, 172],
      [146, 197, 222],
      [247, 247, 247],
      [244, 165, 130],
      [178, 24, 43],
    ],
    discrete: null,
    kinds: ["scale", "classes"],
  },
  {
    id: "ramp.grey",
    title: "Серая",
    stops: [
      [242, 242, 242],
      [30, 34, 43],
    ],
    discrete: null,
    kinds: ["scale", "classes"],
  },
  { id: "set.brand", title: "Фирменные цвета", stops: null, discrete: CATEGORY_COLORS, kinds: ["classes", "categories"] },
  { id: "set.soft", title: "Мягкие цвета", stops: null, discrete: SOFT, kinds: ["classes", "categories"] },
];

export const paletteById = (id: string): Palette | undefined => PALETTES.find((p) => p.id === id);

/** The palettes that fit a kind of colouring (a ramp does not colour categories). */
export const palettesFor = (kind: ColourKind): Palette[] => PALETTES.filter((p) => p.kinds.includes(kind));

const hex = (c: Rgb): string => `#${c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;

/** A point of a ramp, `t` in [0; 1]. */
function at(stops: readonly Rgb[], t: number): string {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  return hex([0, 1, 2].map((c) => stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f) as unknown as Rgb);
}

/**
 * The colours of `n` classes: a scale takes the middle of each bucket (as the legend does), classes — the ramp from the
 * first colour to the last; a list of separate colours is cycled. Null — the palette gives none (the default).
 */
export function paletteColours(id: string, kind: ColourKind, n: number): string[] | null {
  const p = paletteById(id);
  if (!p || id === PROJECT_PALETTE || !p.kinds.includes(kind) || n < 1) return null;
  if (p.discrete) return Array.from({ length: n }, (_, j) => p.discrete![j % p.discrete!.length]);
  const stops = p.stops!;
  return Array.from({ length: n }, (_, j) => at(stops, kind === "scale" ? (j + 0.5) / n : n <= 1 ? 0.5 : j / (n - 1)));
}

/** A strip of the palette for its entry in a list: the stops of a ramp or the first colours of a list, as a CSS gradient. */
export function paletteCss(id: string): string {
  const p = paletteById(id);
  if (!p || !(p.stops || p.discrete)) return new ColorScale(0, 1, false).gradientCss(); // the ramp of the project
  const colours = p.stops ? p.stops.map(hex) : p.discrete!.slice(0, 6);
  return `linear-gradient(to right, ${colours.join(", ")})`;
}
