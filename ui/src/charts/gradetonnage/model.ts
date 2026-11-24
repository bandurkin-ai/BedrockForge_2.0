/**
 * The grade–tonnage curve (ТЗ 7.31 «кривая содержание–тоннаж»): what the chart draws from the curve of the core —
 * tonnes ABOVE each cut-off (left axis) and the grade or the metal above it (right axis). A point with no grade (no block
 * above the cut-off has the grade) is a gap: the line breaks, it never drops to 0. Data of the chart as CSV. Pure.
 */
import { csvText } from "../core/csv";

export type RightAxis = "grade" | "metal";

/** The curve as the core gives it (`ReportResult.curve`). */
export interface CurveData {
  field: string;
  unit: string | null;
  cutoffs: readonly number[];
  tonnes: readonly number[];
  grade: readonly (number | null)[];
  metal: readonly (number | null)[];
  metal_unit: string | null;
}

/** The series on the right axis. */
export const rightValues = (c: CurveData, kind: RightAxis): readonly (number | null)[] => (kind === "metal" ? c.metal : c.grade);

/** Whether a series has any value (a metal of an unknown unit has none: its button is off). */
export const hasValues = (values: readonly (number | null)[]): boolean => values.some((v) => v !== null && Number.isFinite(v));

/** The title of the right axis: «Содержание, г/т» / «Металл, тыс. унц». */
export function rightTitle(c: CurveData, kind: RightAxis): string {
  const unit = kind === "metal" ? c.metal_unit : c.unit;
  return `${kind === "metal" ? "Металл выше борта" : "Среднее содержание выше борта"}${unit ? `, ${unit}` : ""}`;
}

export const leftTitle = "Тоннаж выше борта, т";

/** Pixels of a polyline split where a value is missing: each run of values is a piece (a lone value is a piece of one point). */
export function pieces(xs: readonly number[], ys: readonly (number | null)[], mx: (v: number) => number | null, my: (v: number) => number | null): [number, number][][] {
  const out: [number, number][][] = [];
  let cur: [number, number][] = [];
  const flush = () => {
    if (cur.length) out.push(cur);
    cur = [];
  };
  for (let i = 0; i < xs.length; i++) {
    const y = ys[i];
    const px = mx(xs[i]);
    const py = y === null || y === undefined || !Number.isFinite(y) ? null : my(y);
    if (px === null || py === null) flush();
    else cur.push([px, py]);
  }
  flush();
  return out;
}

/** «M x,y L x,y …» of a piece. */
export const pathOf = (p: readonly [number, number][]): string => p.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");

/** The area under the tonnes line down to the axis (`base` — the pixel of 0). */
export function areaOf(p: readonly [number, number][], base: number): string {
  if (p.length < 2) return "";
  return `${pathOf(p)}L${p[p.length - 1][0].toFixed(1)},${base.toFixed(1)}L${p[0][0].toFixed(1)},${base.toFixed(1)}Z`;
}

/** The index of the point nearest to the value `x` of the cut-off axis (cut-offs grow). */
export function nearestIndex(cutoffs: readonly number[], x: number): number {
  let best = 0;
  for (let i = 1; i < cutoffs.length; i++) if (Math.abs(cutoffs[i] - x) < Math.abs(cutoffs[best] - x)) best = i;
  return best;
}

/** The data of the chart: «Борт; Тоннаж; Содержание; Металл»; a gap stays an empty cell. */
export function curveCsv(c: CurveData): string {
  const head = [`Борт${c.unit ? `, ${c.unit}` : ""}`, "Тоннаж выше борта, т", `Содержание выше борта${c.unit ? `, ${c.unit}` : ""}`, `Металл выше борта${c.metal_unit ? `, ${c.metal_unit}` : ""}`];
  return csvText(
    head,
    c.cutoffs.map((x, i) => [x, c.tonnes[i], c.grade[i] ?? null, c.metal[i] ?? null]),
  );
}
