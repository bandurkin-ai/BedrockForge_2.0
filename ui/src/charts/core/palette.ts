/**
 * Colours of the data of the charts (docs/brand, ADR 0006): series (domains) start from Cobalt and Copper Orange,
 * then their mixes with Slate and Anthracite; a value scale goes Cobalt → Copper Orange; a missing value is Slate.
 * `rgba32` packs a colour for an `ImageData` buffer seen as Uint32 (little-endian: ABGR). Pure.
 */
import { BRAND, mix } from "../column/classes";

export const SERIES_COLORS: readonly string[] = [
  BRAND.cobalt,
  BRAND.copper,
  "#2fa36b",
  "#a35bd6",
  mix(BRAND.cobalt, BRAND.stone, 0.45),
  mix(BRAND.copper, BRAND.anthracite, 0.35),
  "#d4b106",
  mix(BRAND.cobalt, BRAND.anthracite, 0.4),
  "#e0457b",
  BRAND.slate,
];

/** The colour of series `i` (domains in their order; repeats after ten). */
export const seriesColor = (i: number): string => SERIES_COLORS[((i % SERIES_COLORS.length) + SERIES_COLORS.length) % SERIES_COLORS.length];

export const MISSING_COLOR = BRAND.slate;

/** A value 0…1 on the scale Cobalt → Copper Orange; outside — clamped; not finite — the missing colour. */
export function rampColor(t: number): string {
  if (!Number.isFinite(t)) return MISSING_COLOR;
  return mix(BRAND.cobalt, BRAND.copper, Math.min(1, Math.max(0, t)));
}

/** Density of a cell: light Cobalt for one sample up to Copper Orange for the densest (log of the count). */
export function densityColor(count: number, max: number): string {
  if (!(count > 0) || !(max > 0)) return MISSING_COLOR;
  const t = max > 1 ? Math.log(count) / Math.log(max) : 1;
  return t < 0.5 ? mix(mix(BRAND.cobalt, BRAND.stone, 0.6), BRAND.cobalt, t * 2) : mix(BRAND.cobalt, BRAND.copper, (t - 0.5) * 2);
}

/** «#rrggbb» + alpha 0…1 → a pixel of an `ImageData` buffer seen as Uint32 (ABGR on little-endian). */
export function rgba32(hex: string, alpha = 1): number {
  const v = parseInt(hex.slice(1, 7), 16);
  const r = (v >> 16) & 255;
  const g = (v >> 8) & 255;
  const b = v & 255;
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255);
  return ((a << 24) | (b << 16) | (g << 8) | r) >>> 0;
}
