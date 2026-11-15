import type { BlockAttribute, BlockModelMeta } from "../api";
import { ColorScale } from "./colorScale";
import { parseNumber } from "../stats/chartMath";

/** How a block model is shown: colour by an attribute, which blocks, how opaque. */
export interface BlockStyle {
  /** attribute key; null: one colour */
  attribute: string | null;
  /** colour range ("" = automatic, 5–95th percentile) */
  min: string;
  max: string;
  log: boolean;
  /** show only blocks with the value in [from, to] ("" = no bound); numbers */
  from: string;
  to: string;
  /** categories not shown */
  hidden: string[];
  opacity: number;
}

export const CATEGORY_COLORS = ["#4f9dde", "#e8873a", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a",
  "#2f6db5", "#b5651d", "#3c8d4a", "#8a4fa8", "#a89020", "#a83a4a", "#2a8f8a", "#6b6355"];
const ONE_COLOR: [number, number, number] = [176, 144, 112];
const EMPTY_COLOR: [number, number, number] = [150, 158, 172];

/** Default: the first grade of the report (or the first number), on a log scale for g/t and ppm. */
export function defaultBlockStyle(meta: BlockModelMeta): BlockStyle {
  const numbers = meta.attributes.filter((a) => a.kind === "number");
  const key = meta.report?.grades?.[0] ?? numbers.find((a) => a.unit)?.key ?? numbers[0]?.key ?? meta.attributes[0]?.key ?? null;
  const a = meta.attributes.find((x) => x.key === key);
  return { attribute: key, min: "", max: "", log: a?.unit === "g/t" || a?.unit === "ppm" || a?.unit === "ppb", from: "", to: "", hidden: [], opacity: 1 };
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

export interface BlockColors {
  /** rgb per block, 0–255 */
  colors: Uint8Array;
  /** 1: shown */
  visible: Uint8Array;
  shown: number;
  scale: ColorScale | null;
}

/** Colours and visibility of every block for a style; `values` are the attribute's values (or null). */
export function blockColors(n: number, attribute: BlockAttribute | null, values: Float32Array | null, style: BlockStyle): BlockColors {
  const colors = new Uint8Array(n * 3);
  const visible = new Uint8Array(n).fill(1);
  let scale: ColorScale | null = null;
  if (!attribute || !values) {
    for (let i = 0; i < n; i++) colors.set(ONE_COLOR, i * 3);
    return { colors, visible, shown: n, scale };
  }
  if (attribute.kind === "category") {
    const labels = attribute.labels ?? [];
    const hidden = new Set(style.hidden.map((h) => labels.indexOf(h)).filter((i) => i >= 0));
    const palette = CATEGORY_COLORS.map(hexRgb);
    let shown = 0;
    for (let i = 0; i < n; i++) {
      const v = values[i];
      const code = Number.isFinite(v) ? v : -1;
      if (hidden.has(code) || (code < 0 && style.hidden.includes(""))) {
        visible[i] = 0;
        continue;
      }
      colors.set(code < 0 ? EMPTY_COLOR : palette[code % palette.length], i * 3);
      shown++;
    }
    return { colors, visible, shown, scale };
  }
  const auto = percentileRange(values, style.log);
  const lo = parseNumber(style.min) ?? auto?.[0] ?? 0;
  const hi = parseNumber(style.max) ?? auto?.[1] ?? 1;
  scale = new ColorScale(lo, hi, style.log);
  const from = parseNumber(style.from);
  const to = parseNumber(style.to);
  let shown = 0;
  for (let i = 0; i < n; i++) {
    const v = values[i];
    const finite = Number.isFinite(v);
    if ((from !== null && !(finite && v >= from)) || (to !== null && !(finite && v <= to))) {
      visible[i] = 0;
      continue;
    }
    colors.set(finite ? scale.rgb(v) : EMPTY_COLOR, i * 3);
    shown++;
  }
  return { colors, visible, shown, scale };
}

export function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
