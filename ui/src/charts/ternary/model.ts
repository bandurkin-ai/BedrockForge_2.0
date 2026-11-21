/**
 * The ternary diagram (ТЗ 8.3; ADR 0019 §4): shares (a, b, c) / (a + b + c) from the core → a point of the
 * triangle A at the top, B bottom left, C bottom right (barycentric; the triangle is equilateral in the plot box),
 * the grid of 10 % shares, the colours by a field, the CSV and the assistant's text. Rows with a missing field or a
 * sum ≤ 0 are counted by the core and not drawn. Pure.
 */
import type { Calculator } from "../../modules/charts/api";
import { dataCalculator } from "../../modules/charts/state";
import { computeTernary, type TernaryResult } from "../../modules/charts/apiMore";
import type { TernarySettings } from "../../modules/charts/settings";
import { formatNumber } from "../core/axisScale";
import { csvText } from "../core/csv";
import type { Pt } from "../core/lasso";

export type TernaryRequest = Omit<TernarySettings, "view">;

/** The body of `POST /stats/ternary`; null until three fields are chosen. */
export function ternaryRequest(s: TernarySettings): TernaryRequest | null {
  if (!s.a || !s.b || !s.c) return null;
  const { view: _view, ...data } = s;
  return data;
}

export function ternaryCalculator(apply: (r: TernaryResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: TernaryRequest) => Promise<TernaryResult> = computeTernary): Calculator<TernaryRequest> {
  return dataCalculator(load, apply, fail, busy);
}

/** The triangle inside a box: the side fits the width and the height (√3 / 2 of the side), centred. */
export interface Triangle {
  A: Pt;
  B: Pt;
  C: Pt;
}

export function triangleIn(x0: number, y0: number, x1: number, y1: number): Triangle {
  const side = Math.min(x1 - x0, ((y1 - y0) * 2) / Math.sqrt(3));
  const h = (side * Math.sqrt(3)) / 2;
  const cx = (x0 + x1) / 2;
  const top = y0 + (y1 - y0 - h) / 2;
  return { A: { x: cx, y: top }, B: { x: cx - side / 2, y: top + h }, C: { x: cx + side / 2, y: top + h } };
}

/** The point of shares (a, b, c) — they are normalised again (the core gives sums of 1). */
export function ternaryPoint(t: Triangle, a: number, b: number, c: number): Pt | null {
  const s = a + b + c;
  if (!(s > 0) || !Number.isFinite(s) || a < 0 || b < 0 || c < 0) return null;
  return { x: (a * t.A.x + b * t.B.x + c * t.C.x) / s, y: (a * t.A.y + b * t.B.y + c * t.C.y) / s };
}

/** The shares (a, b, c) of a point of the triangle (the inverse; for the tooltip and the tests). */
export function ternaryShares(t: Triangle, p: Pt): [number, number, number] {
  const det = (t.B.y - t.C.y) * (t.A.x - t.C.x) + (t.C.x - t.B.x) * (t.A.y - t.C.y);
  const a = ((t.B.y - t.C.y) * (p.x - t.C.x) + (t.C.x - t.B.x) * (p.y - t.C.y)) / det;
  const b = ((t.C.y - t.A.y) * (p.x - t.C.x) + (t.A.x - t.C.x) * (p.y - t.C.y)) / det;
  return [a, b, 1 - a - b];
}

/** The grid: for each vertex the lines of equal share 10 %, 20 % … 90 % (pairs of ends). */
export function ternaryGrid(t: Triangle, step = 0.1): { share: number; vertex: "a" | "b" | "c"; from: Pt; to: Pt }[] {
  const out: { share: number; vertex: "a" | "b" | "c"; from: Pt; to: Pt }[] = [];
  for (let k = 1; k * step < 1 - 1e-9; k++) {
    const v = Number((k * step).toFixed(6));
    const r = 1 - v;
    out.push({ share: v, vertex: "a", from: ternaryPoint(t, v, r, 0)!, to: ternaryPoint(t, v, 0, r)! });
    out.push({ share: v, vertex: "b", from: ternaryPoint(t, r, v, 0)!, to: ternaryPoint(t, 0, v, r)! });
    out.push({ share: v, vertex: "c", from: ternaryPoint(t, r, 0, v)!, to: ternaryPoint(t, 0, r, v)! });
  }
  return out;
}

/** CSV of the points: row, the shares of A, B, C (and the colour value). */
export function ternaryCsv(r: TernaryResult): string {
  const [a, b, c] = r.fields;
  const labels = r.color?.kind === "text" ? r.color.labels : null;
  const cv = r.color_values;
  const header = ["Строка", `Доля ${a}`, `Доля ${b}`, `Доля ${c}`, ...(r.color_field ? [r.color_field] : [])];
  return csvText(header, r.a.map((v, i) => [r.row[i] ?? null, v, r.b[i], r.c[i], ...(r.color_field ? [labels ? (cv?.[i] === null || cv?.[i] === undefined ? null : labels[cv[i] as number]) : (cv?.[i] ?? null)] : [])]));
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function ternaryNotes(r: TernaryResult): string[] {
  const c = r.counts;
  const out = [`строк ${n(c.rows)}, точек ${n(c.used)}`];
  if (c.missing) out.push(`с пропуском поля ${n(c.missing)}`);
  if (c.nonpositive_sum) out.push(`сумма ≤ 0: ${n(c.nonpositive_sum)}`);
  if (r.total > r.a.length) out.push(`показано ${n(r.a.length)} из ${n(r.total)} (равномерно прорежено)`);
  return out;
}

/** The mean shares of the points drawn (for the assistant). */
export function meanShares(r: Pick<TernaryResult, "a" | "b" | "c">): [number | null, number | null, number | null] {
  const m = (v: readonly number[]) => (v.length ? v.reduce((s, x) => s + x, 0) / v.length : null);
  return [m(r.a), m(r.b), m(r.c)];
}

export function ternarySummary(name: string, r: TernaryResult): string {
  const [a, b, c] = meanShares(r);
  const pc = (v: number | null) => (v === null ? "—" : `${formatNumber(v * 100, 3)} %`);
  return `${name}: точек ${n(r.counts.used)}; средние доли ${r.fields[0]} ${pc(a)}, ${r.fields[1]} ${pc(b)}, ${r.fields[2]} ${pc(c)}`;
}
