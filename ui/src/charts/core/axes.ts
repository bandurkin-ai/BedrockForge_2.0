/**
 * Axes of the charts of Э13 (ADR 0019 §11): linear, log10 and probability (Φ⁻¹) scales, «round» ticks, the
 * number format of axes and tables, parsing of form fields. A value the axis cannot show (≤ 0 on a log axis,
 * P outside (0; 1) on a probability axis, a missing one) maps to null — never to zero. Pure (tested in node).
 */
import { niceTicks } from "../column/scale";

export type AxisKind = "linear" | "log" | "prob";

export interface Domain {
  min: number;
  max: number;
}

export interface Tick {
  value: number;
  label: string;
}

export interface Scale {
  kind: AxisKind;
  domain: Domain;
  /** pixels of `domain.min` and `domain.max` */
  range: [number, number];
  /** pixel of a value, or null when the axis cannot show it */
  map: (v: number | null | undefined) => number | null;
  /** value under a pixel */
  invert: (px: number) => number;
  ticks: (count?: number) => Tick[];
}

/** Compact number for axes and tables: no trailing zeros, exponent for very small or large values, «—» for missing. */
export function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-3) return value.toExponential(Math.max(0, digits - 2)).replace("e+", "e");
  return String(Number(value.toPrecision(digits)));
}

/** A form field: empty or not a number gives null (the core then uses its default); a decimal comma is accepted. */
export function parseNumber(text: string | null | undefined): number | null {
  const t = (text ?? "").trim().replace(/\s+/g, "").replace(",", ".");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** A number for a form field (with a decimal comma, as typed in Russian); null — empty. */
export const numberText = (v: number | null | undefined): string => (v === null || v === undefined || !Number.isFinite(v) ? "" : String(v).replace(".", ","));

/** Ticks of a log10 axis: decades, plus 2 and 5 when the range spans few decades. */
export function logTicks(lo: number, hi: number): number[] {
  if (!(lo > 0) || !(hi > lo) || !Number.isFinite(hi)) return [];
  const out: number[] = [];
  const a = Math.floor(Math.log10(lo));
  const b = Math.ceil(Math.log10(hi));
  const mult = b - a <= 3 ? [1, 2, 5] : [1];
  const every = Math.max(1, Math.ceil((b - a + 1) / 12));
  for (let e = a; e <= b; e++) {
    if ((e - a) % every) continue;
    for (const m of mult) {
      const v = Number((m * 10 ** e).toPrecision(12));
      if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
    }
  }
  return out;
}

// Acklam's rational approximation of Φ⁻¹ (relative error < 1.2e-9) — for drawing; the core computes the statistics.
const A = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
const B = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
const C = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
const D = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];

/** Standard normal distribution function Φ = erfc(−x / √2) / 2 (erfc — Chebyshev fit `erfcc`, error < 1.2e-7: for drawing). */
export function normCdf(x: number): number {
  if (!Number.isFinite(x)) return x > 0 ? 1 : x < 0 ? 0 : Number.NaN;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + 0.5 * z);
  const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
  return x >= 0 ? 1 - r / 2 : r / 2;
}

/** Quantile Φ⁻¹(p) of the standard normal distribution; p outside (0; 1) — NaN (±∞ at the ends). */
export function normInv(p: number): number {
  if (!(p >= 0 && p <= 1)) return Number.NaN;
  if (p === 0) return -Infinity;
  if (p === 1) return Infinity;
  const lowP = 0.02425;
  let x: number;
  if (p < lowP) {
    const q = Math.sqrt(-2 * Math.log(p));
    x = (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1);
  } else if (p <= 1 - lowP) {
    const q = p - 0.5;
    const r = q * q;
    x = ((((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q) / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1);
  } else {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    x = -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1);
  }
  return x;
}

/** Probabilities marked on a probability axis (fractions); those inside the domain are drawn. */
export const PROB_TICKS = [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999];

const percentLabel = (p: number) => `${formatNumber(p * 100, 3)}%`;

/** The value of the axis in its own (linear) space: log10 for log, Φ⁻¹ for probability; null — cannot be shown. */
export function axisValue(kind: AxisKind, v: number | null | undefined): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (kind === "log") return v > 0 ? Math.log10(v) : null;
  if (kind === "prob") return v > 0 && v < 1 ? normInv(v) : null;
  return v;
}

function fromAxis(kind: AxisKind, t: number): number {
  return kind === "log" ? 10 ** t : kind === "prob" ? normCdf(t) : t;
}

/** A scale of the kind over the domain onto pixels `from` → `to` (for Y: bottom pixel first). */
export function makeScale(kind: AxisKind, domain: Domain, from: number, to: number): Scale {
  const lo = axisValue(kind, domain.min);
  const hi = axisValue(kind, domain.max);
  const t0 = lo ?? 0;
  const span = hi !== null && lo !== null && hi !== lo ? hi - lo : 1;
  const map = (v: number | null | undefined) => {
    const t = axisValue(kind, v);
    return t === null ? null : from + ((t - t0) / span) * (to - from);
  };
  const invert = (px: number) => fromAxis(kind, t0 + ((px - from) / (to - from || 1)) * span);
  const ticks = (count = 6): Tick[] => {
    if (kind === "log") return logTicks(domain.min, domain.max).map((value) => ({ value, label: formatNumber(value) }));
    if (kind === "prob") return PROB_TICKS.filter((p) => p >= domain.min * (1 - 1e-9) && p <= domain.max * (1 + 1e-9)).map((value) => ({ value, label: percentLabel(value) }));
    return niceTicks(domain.min, domain.max, count).map((value) => ({ value, label: formatNumber(value) }));
  };
  return { kind, domain, range: [from, to], map, invert, ticks };
}

/**
 * The domain of the finite values the axis can show, padded (in the axis space) so a flat series still has a
 * span; null when there is nothing to show. A log axis takes only values > 0; `floor` bounds a linear minimum.
 */
export function domainOf(values: Iterable<number | null | undefined>, kind: AxisKind = "linear", pad = 0.05, floor?: number): Domain | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    const t = axisValue(kind, v);
    if (t === null) continue;
    if (t < min) min = t;
    if (t > max) max = t;
  }
  if (min === Infinity) return null;
  if (max === min) {
    const d = kind === "linear" ? Math.abs(min) * 0.1 || 1 : kind === "log" ? 0.5 : 0.25;
    min -= d;
    max += d;
  } else {
    const d = (max - min) * pad;
    min -= d;
    max += d;
  }
  const out = { min: fromAxis(kind, min), max: fromAxis(kind, max) };
  if (floor !== undefined && kind === "linear") out.min = Math.max(floor, out.min);
  return out;
}

/** Steps 1, 2, 2.5, 5 × 10ⁿ: the smallest «round» number not below `x` (ADR 0019 §5). */
export function roundUp(x: number): number {
  if (!(x > 0) || !Number.isFinite(x)) return Number.NaN;
  const p = 10 ** Math.floor(Math.log10(x));
  const m = [1, 2, 2.5, 5, 10].find((k) => k * p >= x * (1 - 1e-12)) ?? 10;
  return Number((m * p).toPrecision(12));
}
