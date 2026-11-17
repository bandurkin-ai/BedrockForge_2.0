import type { ColorClass } from "../../charts/striplog/model";
import { rampColors } from "./gradients";

/** Ways to break the values of a field into classes automatically. */
export type ClassMethod = "quantile" | "equal" | "log" | "progressive" | "double" | "round";

export const CLASS_METHODS: [ClassMethod, string, string][] = [
  ["quantile", "Квантили", "Равное число проб в каждом классе"],
  ["equal", "Равные интервалы", "Равные по величине интервалы от минимума до максимума"],
  ["log", "Логарифмические", "Равные интервалы логарифма: для содержаний с длинным хвостом"],
  ["progressive", "Прогрессивная", "Доли проб убывают: примерно 33 / 27 / 20 / 13 / 7 %"],
  ["double", "Двойная прогрессивная", "Доли проб убывают вдвое: примерно 50 / 25 / 12,5 / 6 / 3 %"],
  ["round", "Круглые значения", "Круглые границы по диапазону значений"],
];

const sig = (v: number, digits = 3) => Number(v.toPrecision(digits));

/** The value at a share 0..1 of sorted values. */
export function quantile(sorted: ArrayLike<number>, q: number): number {
  if (sorted.length === 0) return NaN;
  const x = Math.max(0, Math.min(1, q)) * (sorted.length - 1);
  const i = Math.floor(x);
  const j = Math.min(i + 1, sorted.length - 1);
  return sorted[i] + (sorted[j] - sorted[i]) * (x - i);
}

/** Shares of the classes, from the lowest: equal, falling linearly, or halving. */
function shares(method: ClassMethod, count: number): number[] {
  const w = Array.from({ length: count }, (_, i) => (method === "progressive" ? count - i : method === "double" ? 2 ** (count - 1 - i) : 1));
  if (method === "double") w[count - 1] = w[count - 2] ?? 1; // the last two classes share the remainder
  const sum = w.reduce((a, b) => a + b, 0);
  return w.map((x) => x / sum);
}

/**
 * Bounds between `count` classes of the values (finite numbers in any order): ascending, without repeats,
 * strictly inside the range. Degenerate data (empty, one value, all equal) gives no bounds.
 */
export function classBounds(values: ArrayLike<number>, method: ClassMethod, count: number): number[] {
  const sorted = Array.from(values).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  const n = Math.max(2, Math.min(40, Math.round(count)));
  if (sorted.length < 2) return [];
  const lo = sorted[0];
  const hi = sorted[sorted.length - 1];
  if (!(hi > lo)) return [];
  let raw: number[] = [];
  if (method === "equal") {
    raw = Array.from({ length: n - 1 }, (_, i) => lo + ((hi - lo) * (i + 1)) / n);
  } else if (method === "log" || method === "round") {
    const positive = sorted.filter((v) => v > 0);
    const useLog = method === "log" || (positive.length > 0 && hi / Math.max(positive[0], 1e-12) > 50);
    if (useLog && positive.length >= 2 && hi > positive[0]) {
      const a = Math.log10(Math.max(positive[0], hi / 1e6));
      const b = Math.log10(hi);
      raw = Array.from({ length: n - 1 }, (_, i) => 10 ** (a + ((b - a) * (i + 1)) / n));
    } else {
      raw = Array.from({ length: n - 1 }, (_, i) => lo + ((hi - lo) * (i + 1)) / n);
    }
  } else {
    let cum = 0;
    for (const s of shares(method, n).slice(0, -1)) {
      cum += s;
      raw.push(quantile(sorted, cum));
    }
  }
  const digits = method === "round" ? 1 : 3;
  const out: number[] = [];
  for (const b of raw) {
    let v = sig(b, digits);
    if (!(v > lo && v <= hi)) v = sig(b, 6);
    if (v > lo && v <= hi && (out.length === 0 || v > out[out.length - 1])) out.push(v);
  }
  return out;
}

/** Classes from bounds: one below the first bound, then one from each bound; colours from a gradient. */
export function classesFromBounds(bounds: number[], gradient = "geochem", reverse = false, old: ColorClass[] = []): ColorClass[] {
  const sorted = [...new Set(bounds.filter((b) => Number.isFinite(b)))].sort((a, b) => a - b);
  const colors = rampColors(gradient, sorted.length + 1, reverse);
  return [{ from: null, color: colors[0] }, ...sorted.map((from, i) => ({ from, color: colors[i + 1] }))].map((c, i) => (old[i]?.label ? { ...c, label: old[i].label } : c));
}

/** "0,3 1 3 10" (spaces, semicolons; a comma between digits is a decimal point) -> bounds. */
export function parseBounds(text: string): number[] {
  const out = text.split(/[\s;]+/).map((t) => Number(t.replace(",", "."))).filter((v) => Number.isFinite(v));
  return [...new Set(out)].sort((a, b) => a - b);
}

/** How many of the sorted values fall into each class (a value equal to a bound belongs to the upper class). */
export function classCounts(sorted: ArrayLike<number>, classes: ColorClass[]): number[] {
  const counts = new Array<number>(classes.length).fill(0);
  if (!classes.length) return counts;
  const firstBounded = classes[0].from === null ? 1 : 0;
  let c = firstBounded === 1 ? 0 : -1;
  let next = firstBounded;
  for (let i = 0; i < sorted.length; i++) {
    const v = sorted[i];
    while (next < classes.length && v >= (classes[next].from as number)) c = next++;
    if (c >= 0) counts[c] += 1;
  }
  return counts;
}
