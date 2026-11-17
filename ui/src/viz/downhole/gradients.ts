/** Ready colour gradients for classes and continuous scales, and interpolation between colours. */

export interface Gradient {
  name: string;
  title: string;
  stops: string[];
}

export const GRADIENTS: Gradient[] = [
  { name: "classic", title: "Синий → красный", stops: ["#2b5fa8", "#2aa7a0", "#8fd15a", "#f2d43a", "#ee7a24", "#b2182b"] },
  { name: "grey-red", title: "Серый → красный", stops: ["#b9bec9", "#d9a79a", "#e8735a", "#c8102e"] },
  { name: "geochem", title: "Геохимия", stops: ["#9aa3b5", "#4f7fd0", "#4cae5a", "#e6d23a", "#e0364a", "#b03ad0"] },
  { name: "viridis", title: "Viridis", stops: ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"] },
  { name: "turbo", title: "Turbo", stops: ["#30123b", "#4686fb", "#1ae4b6", "#a4fc3c", "#fbb938", "#e3440a", "#7a0403"] },
  { name: "heat", title: "Жёлтый → красный", stops: ["#fff3b0", "#f9c74f", "#f3722c", "#c1121f", "#641220"] },
];

export const gradientOf = (name: string | undefined): Gradient => GRADIENTS.find((g) => g.name === name) ?? GRADIENTS[0];

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const v = m ? parseInt(m[1], 16) : 0x9aa3b5;
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export const rgbToHex = (r: number, g: number, b: number): string =>
  `#${[r, g, b].map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, "0")).join("")}`;

/** The colour between two colours, t in 0..1. */
export function mixHex(a: string, b: string, t: number): string {
  const x = hexToRgb(a);
  const y = hexToRgb(b);
  return rgbToHex(x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t);
}

/** The colour of a gradient at a position 0..1 (0..255 per channel). */
export function gradientRgb(name: string | undefined, t: number, reverse = false): [number, number, number] {
  const stops = gradientOf(name).stops;
  const p = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  const x = (reverse ? 1 - p : p) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  const a = hexToRgb(stops[i]);
  const b = hexToRgb(stops[i + 1]);
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

export const gradientHex = (name: string | undefined, t: number, reverse = false): string => rgbToHex(...gradientRgb(name, t, reverse));

/** `count` colours spread over a gradient: the first at its start, the last at its end. */
export function rampColors(name: string | undefined, count: number, reverse = false): string[] {
  return Array.from({ length: count }, (_, i) => gradientHex(name, count > 1 ? i / (count - 1) : 0.5, reverse));
}

export function gradientCss(name: string | undefined, reverse = false): string {
  const stops = gradientOf(name).stops;
  const list = reverse ? [...stops].reverse() : stops;
  return `linear-gradient(to right, ${list.map((c, i) => `${c} ${(i / (list.length - 1)) * 100}%`).join(", ")})`;
}
