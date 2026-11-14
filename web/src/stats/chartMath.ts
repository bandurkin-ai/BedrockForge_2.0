import { niceTicks } from "../viz/geometry";

export interface Domain {
  min: number;
  max: number;
}

/** Min and max of the finite values, padded so a flat series still has a span; null if nothing is finite. */
export function domainOf(values: (number | null | undefined)[], pad = 0.05, floor?: number): Domain | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v === null || v === undefined || !Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === Infinity) return null;
  if (max === min) {
    const d = Math.abs(min) * 0.1 || 1;
    min -= d;
    max += d;
  } else {
    const d = (max - min) * pad;
    min -= d;
    max += d;
  }
  if (floor !== undefined) min = Math.max(floor, min);
  return { min, max };
}

/** Maps a data domain to pixels; `invert` for the y axis (up is larger). */
export function linearScale(domain: Domain, from: number, to: number): (v: number) => number {
  const span = domain.max - domain.min || 1;
  return (v) => from + ((v - domain.min) / span) * (to - from);
}

export function ticksFor(domain: Domain, target = 5): number[] {
  return niceTicks(domain.min, domain.max, target);
}

/** SVG path through the points, with a gap wherever a value is missing (null / NaN). */
export function linePath(xs: (number | null)[], ys: (number | null)[], sx: (v: number) => number, sy: (v: number) => number): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    const y = ys[i];
    if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`;
    pen = true;
  }
  return d;
}

/** Compact number for axes and tables: no trailing zeros, exponent for very small or large values. */
export function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-3) return value.toExponential(1).replace("e+", "e");
  return String(Number(value.toPrecision(digits)));
}

/** Parses a form field: empty or invalid gives null (the server then uses its default). */
export function parseNumber(text: string): number | null {
  const n = Number(text.trim().replace(",", "."));
  return text.trim() === "" || !Number.isFinite(n) ? null : n;
}
