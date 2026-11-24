/**
 * The legend of the shell as a texture (ADR 0023 §11): the core sends the class of every face (`k`), never a
 * colour — the colours are the viewer's, so a new palette is 256·H new texels and nothing is requested. A new
 * field, a new range of the scale, new breaks or a changed set of hidden categories change the classes: that is a
 * new request (`classKey` tells them from the colours).
 *
 * Texel `k` sits at (k mod 256, ⌊k / 256⌋) of a 256 × H texture: 256 buckets of a scale in one row, the codes of
 * the categories in as many rows as the highest code needs. The class 0xFFFF (no value) is not in the texture
 * (it would make it 256 rows tall): the shader paints it with the colour «нет значения» — never the colour of 0.
 * Colours are sRGB; the sampler decodes them (the texture is sRGB).
 */
import * as THREE from "three";
import { ColorScale, NO_VALUE_COLOR } from "../color/colorScale";
import { NO_CLASS, type BlocksColour, type BlocksHeader, type ColourKind } from "./decode";

export const LEGEND_WIDTH = 256;
/** The colour of the faces when nothing is coloured (`kind: "none"`): Cobalt of the brand. */
export const NONE_COLOR = "#1F6BFF";
export { NO_VALUE_COLOR };

/** Categories without own colours: the brand's, then the ramp's, cycled (docs/brand/README.md). */
export const CATEGORY_COLORS: readonly string[] = [
  "#1F6BFF",
  "#F46A1A",
  "#2AA7A0",
  "#F2D43A",
  "#8F5BD6",
  "#8FD15A",
  "#B2182B",
  "#8A99A8",
  "#14B8D4",
  "#D94F9B",
  "#6B8E23",
  "#A0522D",
];

/** What the user chose for the colours; the classes themselves come from the core. */
export interface LegendStyle {
  /** one colour per class, `#rrggbb`; a class without one gets the default of its kind */
  colors?: readonly (string | null)[] | null;
  /** the faces without a value */
  noValue?: string;
  /** the faces of `kind: "none"` */
  none?: string;
}

export type Rgb = [number, number, number];

export function parseColor(css: string): Rgb {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(css.trim());
  if (!m) throw new RangeError(`Цвет «${css}» не в виде #RRGGBB`);
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

const ramp = new ColorScale(0, 1, false);

/** The default colour of class `j` of `n` (the ramp of the project for a scale and breaks, the brand's for codes). */
export function defaultColor(kind: ColourKind, j: number, n: number): Rgb {
  switch (kind) {
    case "scale":
      return ramp.rgb((j + 0.5) / Math.max(1, n));
    case "classes":
      return ramp.rgb(n <= 1 ? 0.5 : j / (n - 1));
    case "categories":
      return parseColor(CATEGORY_COLORS[j % CATEGORY_COLORS.length]);
    default:
      return parseColor(NONE_COLOR);
  }
}

/** Number of texture rows for the highest class that has a value. */
export function legendRows(classes: number, maxClass: number): number {
  const n = Math.max(1, classes, maxClass + 1);
  return Math.ceil(n / LEGEND_WIDTH);
}

export interface LegendBytes {
  width: number;
  height: number;
  /** RGBA8, row-major, sRGB; alpha 255 */
  data: Uint8Array;
}

/**
 * The texels of the legend. `kind` and `classes` come from the header of the answer, `maxClass` from the faces;
 * `style.colors[j]` overrides class j. Texels past the last class stay opaque grey (a class the header did not
 * announce is still drawn, never as nothing).
 */
export function legendBytes(kind: ColourKind, classes: number, maxClass: number, style: LegendStyle = {}): LegendBytes {
  const height = legendRows(classes, maxClass);
  const total = height * LEGEND_WIDTH;
  const data = new Uint8Array(total * 4);
  const n = Math.max(classes, maxClass + 1, 1);
  const own = style.colors ?? [];
  const none = parseColor(style.none ?? NONE_COLOR);
  const missing = parseColor(style.noValue ?? NO_VALUE_COLOR);
  for (let j = 0; j < total; j++) {
    let rgb: Rgb;
    if (kind === "none") rgb = none;
    else if (j >= n) rgb = missing;
    else {
      const chosen = own[j];
      rgb = chosen ? parseColor(chosen) : defaultColor(kind, j, kind === "scale" ? LEGEND_WIDTH : classes || n);
    }
    data[4 * j] = rgb[0];
    data[4 * j + 1] = rgb[1];
    data[4 * j + 2] = rgb[2];
    data[4 * j + 3] = 255;
  }
  return { width: LEGEND_WIDTH, height, data };
}

/** The legend texture (nearest, no mipmaps, sRGB). Refill it in place with `fillLegend`. */
export function legendTexture(bytes: LegendBytes): THREE.DataTexture {
  const t = new THREE.DataTexture(bytes.data, bytes.width, bytes.height, THREE.RGBAFormat, THREE.UnsignedByteType);
  t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/** The same texture with new texels of the same size; false — the size differs, a new texture is needed. */
export function fillLegend(t: THREE.DataTexture, bytes: LegendBytes): boolean {
  if (t.image.width !== bytes.width || t.image.height !== bytes.height) return false;
  (t.image.data as Uint8Array).set(bytes.data);
  t.needsUpdate = true;
  return true;
}

/**
 * The part of the colouring that decides the classes of the faces — a change of it is a new request to the core,
 * a change of the colours is not. Key of the request: field, kind, range, breaks, hidden categories.
 */
export function classKey(field: string | null, colour: BlocksColour): string {
  return JSON.stringify([field, colour.kind, colour.from ?? null, colour.to ?? null, colour.log ?? false, colour.breaks ?? null, colour.hidden ?? null]);
}

/** What the panel of the legend shows: a swatch with its range (scale, breaks) or its label (categories). */
export interface LegendItem {
  /** the class k */
  k: number;
  color: Rgb;
  from: number | null;
  to: number | null;
  label: string | null;
}

/** The entries of the legend of an answer: all buckets of a scale, the classes of the breaks, the categories seen. */
export function legendItems(h: Pick<BlocksHeader, "colour" | "classes" | "bins" | "labels">, maxClass: number, style: LegendStyle = {}): LegendItem[] {
  const kind = h.colour.kind;
  if (kind === "none") return [];
  const n = kind === "categories" ? Math.max(h.classes, maxClass + 1) : h.classes;
  const bytes = legendBytes(kind, h.classes, maxClass, style);
  const out: LegendItem[] = [];
  for (let k = 0; k < n; k++) {
    const bin = h.bins?.[k] ?? null;
    out.push({ k, color: [bytes.data[4 * k], bytes.data[4 * k + 1], bytes.data[4 * k + 2]], from: bin ? bin[0] : null, to: bin ? bin[1] : null, label: h.labels?.[k] ?? null });
  }
  return out;
}

export { NO_CLASS };
