import type { MarkerShape } from "../charts/stripLog";

/** How the collars of a set look: the marker and the label with the hole name. */
export interface CollarStyle {
  shape: MarkerShape;
  /** "#rrggbb", or "audit": by the worst audit finding of the hole */
  color: string;
  /** px */
  size: number;
  label: boolean;
  /** px */
  fontSize: number;
  fontFamily: "sans-serif" | "serif" | "monospace";
  bold: boolean;
  labelColor: string;
}

export const DEFAULT_COLLAR_STYLE: CollarStyle = {
  shape: "circle", color: "audit", size: 10, label: false, fontSize: 12, fontFamily: "sans-serif", bold: false, labelColor: "#f3f6fb",
};

const SHAPES: MarkerShape[] = ["circle", "square", "diamond", "triangle", "cross", "star"];
const HEX = /^#[0-9a-f]{6}$/i;

/** A style from stored state: unknown or broken values fall back to the defaults. */
export function normalizeCollarStyle(raw: unknown): CollarStyle {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<CollarStyle>;
  const d = DEFAULT_COLLAR_STYLE;
  const num = (v: unknown, lo: number, hi: number, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback);
  return {
    shape: SHAPES.includes(r.shape as MarkerShape) ? (r.shape as MarkerShape) : d.shape,
    color: r.color === "audit" || (typeof r.color === "string" && HEX.test(r.color)) ? r.color : d.color,
    size: num(r.size, 4, 40, d.size),
    label: r.label === true,
    fontSize: num(r.fontSize, 8, 36, d.fontSize),
    fontFamily: r.fontFamily === "serif" || r.fontFamily === "monospace" ? r.fontFamily : "sans-serif",
    bold: r.bold === true,
    labelColor: typeof r.labelColor === "string" && HEX.test(r.labelColor) ? r.labelColor : d.labelColor,
  };
}
