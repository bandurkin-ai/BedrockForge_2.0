/**
 * Colours of the 3D window (ADR 0007 §1, brand palette ADR 0006): collars by the worst audit finding, the
 * selection and the rubber band in orange, the section line in Cobalt. Vertex colours go to the GPU in the
 * linear working space of three.js (its output converts back to sRGB), so the hex of the theme is exact on
 * the screen. Pure.
 */

/** `audit u1` → colour: 0 no audit, 1 no findings, 2 notes, 3 warnings, 4 errors (BF palette). */
export const AUDIT_COLORS = ["#9aa3b5", "#2e9d57", "#3b82d6", "#e0a030", "#c0392b"] as const;
export const AUDIT_TITLES = ["Аудита нет", "Без замечаний", "Замечания", "Предупреждения", "Ошибки"] as const;

/** Copper Orange of the brand: the selected hole. */
export const SELECT_COLOR = "#f46a1a";
/** The rubber band of a section being drawn (ADR 0007 §4). */
export const DRAFT_COLOR = "#ff8c1a";
/** Cobalt of the brand: the section line and its window. */
export const SECTION_COLOR = "#1f6bff";
/** Trenches by default have their own colour (ADR 0007 §7). */
export const TRENCH_COLOR = "#c9a46a";

export function auditColor(level: number): string {
  return AUDIT_COLORS[level] ?? AUDIT_COLORS[0];
}

/** "#rrggbb" → sRGB 0…1; an unreadable colour gives grey. */
export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const v = m ? parseInt(m[1], 16) : 0x9aa3b5;
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

/** sRGB → linear (as `THREE.ColorManagement`). */
export function srgbToLinear(c: number): number {
  return c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4);
}

export function hexToLinear(hex: string): [number, number, number] {
  const [r, g, b] = hexToRgb(hex);
  return [srgbToLinear(r), srgbToLinear(g), srgbToLinear(b)];
}

/** Linear RGB per collar: by its audit level, or one colour for the set ("#rrggbb"). */
export function collarColors(levels: ArrayLike<number>, color: string): Float32Array {
  const out = new Float32Array(levels.length * 3);
  const one = color === "audit" ? null : hexToLinear(color);
  const table = AUDIT_COLORS.map(hexToLinear);
  for (let i = 0; i < levels.length; i++) {
    const c = one ?? table[levels[i]] ?? table[0];
    out[i * 3] = c[0];
    out[i * 3 + 1] = c[1];
    out[i * 3 + 2] = c[2];
  }
  return out;
}
