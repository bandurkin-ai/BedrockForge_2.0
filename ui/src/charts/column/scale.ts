/**
 * The scale of a column of intervals along a hole (ТЗ 8.2.3 preview; shared with Э13–Э15): depth → pixels, nice
 * ticks of depth, the colour of a value by a sequential ramp of the brand (Cobalt → Copper Orange, docs/brand), the
 * bars of a column. A missing value has no colour (drawn hatched — never as zero); an interval with TO ≤ FROM or a
 * missing end is not drawn. Depths are float64 as given. Pure.
 */

export interface DepthScale {
  top: number;
  bottom: number;
  /** pixels of the drawn depth range */
  height: number;
  /** pixels above `top` (room for the head of the column) */
  pad: number;
  y: (depth: number) => number;
  depth: (y: number) => number;
}

/** [top; bottom] → [pad; pad + height]; a range of zero length is widened to 1 m. */
export function depthScale(top: number, bottom: number, height: number, pad = 0): DepthScale {
  const lo = Number.isFinite(top) ? top : 0;
  let hi = Number.isFinite(bottom) ? bottom : lo + 1;
  if (hi <= lo) hi = lo + 1;
  const k = height / (hi - lo);
  return { top: lo, bottom: hi, height, pad, y: (d) => pad + (d - lo) * k, depth: (y) => lo + (y - pad) / k };
}

/** Steps 1, 2, 5 × 10ⁿ: about `count` ticks inside [lo; hi]. */
export function niceTicks(lo: number, hi: number, count = 6): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo || count < 1) return [];
  const raw = (hi - lo) / count;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Columns of intervals: FROM, TO and the values of the fields (the shape of `/composites/{op}/preview`). */
export interface Columns {
  from: readonly (number | null)[];
  to: readonly (number | null)[];
  values: Record<string, readonly (number | string | null)[]>;
}

/** The depth range covered by every given column; nothing — null. */
export function columnsRange(...cols: Columns[]): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of cols)
    for (let i = 0; i < c.from.length; i++) {
      const a = c.from[i];
      const b = c.to[i];
      if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) continue;
      if (a < lo) lo = a;
      if (b > hi) hi = b;
    }
  return lo < hi ? [lo, hi] : null;
}

const num = (v: number | string | null | undefined): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The value range of a field over the columns (the same colours in samples and composites). */
export function valueRange(field: string, ...cols: Columns[]): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of cols)
    for (const v of c.values[field] ?? []) {
      const x = num(v);
      if (x === null) continue;
      if (x < lo) lo = x;
      if (x > hi) hi = x;
    }
  return lo <= hi ? [lo, hi] : null;
}

/** Brand ramp: deep slate → Cobalt #1F6BFF → Copper Orange #F46A1A (grades: cool low, ore orange). */
export const GRADE_RAMP: readonly string[] = ["#33475f", "#1f6bff", "#8a7fd0", "#f46a1a"];

function hex(c: string): [number, number, number] {
  return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
}

/** The colour of `t` ∈ [0; 1] on the ramp (linear between the stops). */
export function rampColor(t: number, ramp: readonly string[] = GRADE_RAMP): string {
  const x = Math.min(1, Math.max(0, t)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(x));
  const f = x - i;
  const [a, b] = [hex(ramp[i]), hex(ramp[i + 1])];
  return `#${a.map((v, k) => Math.round(v + (b[k] - v) * f).toString(16).padStart(2, "0")).join("")}`;
}

/** value → colour over [lo; hi]; a missing value — null (hatched, not the colour of zero). */
export function colorOf(range: [number, number] | null, ramp: readonly string[] = GRADE_RAMP): (v: number | string | null | undefined) => string | null {
  return (v) => {
    const x = num(v);
    if (x === null || !range) return null;
    const [lo, hi] = range;
    return rampColor(hi > lo ? (x - lo) / (hi - lo) : 0.5, ramp);
  };
}

export interface Bar {
  index: number;
  from: number;
  to: number;
  y: number;
  h: number;
  value: number | string | null;
  color: string | null;
}

/** The drawable bars of a column for one field: intervals with TO > FROM, at least 1 px high. */
export function columnBars(cols: Columns, field: string | null, scale: DepthScale, color: (v: number | string | null | undefined) => string | null): Bar[] {
  const out: Bar[] = [];
  const values = field ? (cols.values[field] ?? []) : [];
  for (let i = 0; i < cols.from.length; i++) {
    const a = cols.from[i];
    const b = cols.to[i];
    if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) continue;
    const y = scale.y(a);
    const value = field ? (values[i] ?? null) : null;
    out.push({ index: i, from: a, to: b, y, h: Math.max(1, scale.y(b) - y), value, color: color(value) });
  }
  return out;
}

/** «1,545» — a value for a label or a tooltip; missing — «нет значения». */
export function valueText(v: number | string | null | undefined, digits = 3): string {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "нет значения";
  return typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: digits }) : v;
}

export const depthText = (d: number): string => d.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
