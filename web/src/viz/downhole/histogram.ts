import type { DownholeData } from "../../api";

/** The values of a numeric field over a set, with the lengths of their intervals; sorted by value. */
export interface FieldValues {
  values: Float64Array;
  lengths: Float64Array;
  /** rows of the table, with and without a number */
  rows: number;
}

export function fieldValues(data: DownholeData, table: string, field: string): FieldValues {
  const pairs: [number, number][] = [];
  let rows = 0;
  for (const h of data.holes) {
    for (const iv of h.tables[table] ?? []) {
      rows += 1;
      const v = iv.values[field];
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      pairs.push([v, iv.from !== null && iv.to !== null && iv.to > iv.from ? iv.to - iv.from : 1]);
    }
  }
  pairs.sort((a, b) => a[0] - b[0]);
  return { values: Float64Array.from(pairs, (p) => p[0]), lengths: Float64Array.from(pairs, (p) => p[1]), rows };
}

export interface Histogram {
  /** bins + 1 edges, ascending */
  edges: number[];
  /** weight in each bin: the number of samples, or their length */
  counts: number[];
  total: number;
  min: number;
  max: number;
  log: boolean;
}

/** Bins over sorted values; a log axis keeps only the positive ones. */
export function buildHistogram(v: FieldValues, bins = 48, log = false, byLength = false): Histogram | null {
  let start = 0;
  if (log) while (start < v.values.length && v.values[start] <= 0) start += 1;
  if (start >= v.values.length) return null;
  const min = v.values[start];
  let max = v.values[v.values.length - 1];
  if (!(max > min)) max = log ? min * 10 : min + 1;
  const a = log ? Math.log10(min) : min;
  const b = log ? Math.log10(max) : max;
  const edges = Array.from({ length: bins + 1 }, (_, i) => (log ? 10 ** (a + ((b - a) * i) / bins) : a + ((b - a) * i) / bins));
  const counts = new Array<number>(bins).fill(0);
  let total = 0;
  for (let i = start; i < v.values.length; i++) {
    const x = log ? Math.log10(v.values[i]) : v.values[i];
    const bin = Math.min(bins - 1, Math.max(0, Math.floor(((x - a) / (b - a)) * bins)));
    const w = byLength ? v.lengths[i] : 1;
    counts[bin] += w;
    total += w;
  }
  return { edges, counts, total, min, max, log };
}

/** The share of the weight at or below the upper edge of each bin, 0..1. */
export function cumulative(h: Histogram): number[] {
  let sum = 0;
  return h.counts.map((c) => (sum += c) / (h.total || 1));
}

/** Position 0..1 of a value on the axis of a histogram (clamped). */
export function axisPosition(h: Histogram, value: number): number {
  const a = h.log ? Math.log10(h.min) : h.min;
  const b = h.log ? Math.log10(h.max) : h.max;
  const x = h.log ? Math.log10(Math.max(value, h.min)) : value;
  return Math.max(0, Math.min(1, (x - a) / (b - a || 1)));
}

/** The value at a position 0..1 of the axis. */
export function axisValue(h: Histogram, position: number): number {
  const p = Math.max(0, Math.min(1, position));
  const a = h.log ? Math.log10(h.min) : h.min;
  const b = h.log ? Math.log10(h.max) : h.max;
  return h.log ? 10 ** (a + (b - a) * p) : a + (b - a) * p;
}
