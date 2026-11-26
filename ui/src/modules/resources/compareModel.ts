/**
 * What the window «Сравнить отчёты» shows (ТЗ 7.35 Д-37; ADR 0027 решение 26; docs/progress/e20-api.md §9.1): the rows of the answer of the core as the
 * cells of a table «A | B | разность» for T, ḡ and M of one element, the head of the versions of both reports (the inputs: name, fingerprint, date —
 * Д-35), the curves of both on one axis, the words of the notes. A row that is only in one report has no difference — «—», not 0; a value no block
 * has stays «нет значения». The numbers are the core's; nothing is added up here. Pure.
 */
import type { CompareAnswer, CompareRow, CompareSide, Curve } from "./answers";
import { fmt, groupText, NO_VALUE, rowTitle } from "./format";

/** The mark of a missing value: the row is not in the report, or its difference has no meaning. */
export const GAP = "—";

export type CellKind = "value" | "absent" | "none";

/** A cell of the table: `absent` — the report has no such row (the whole row is in the other one); `none` — the row has no value (no block has the grade). */
export interface CompareCell {
  text: string;
  kind: CellKind;
}

/** A, B and the difference of one quantity. */
export interface Trio {
  a: CompareCell;
  b: CompareCell;
  d: CompareCell;
}

export interface CompareTableRow {
  index: number;
  group: string;
  title: string;
  total: boolean;
  only: "a" | "b" | null;
  /** «только в A» / «только в B» for the mark of the row */
  note: string | null;
  /** the row has a difference (not 0) in tonnes, grade or metal of the chosen element, or it is only in one report */
  differs: boolean;
  tonnes: Trio;
  grade: Trio;
  metal: Trio;
}

/** A difference with its sign: «+1,5», «−1 000», «0»; a gap — «—». */
export function fmtDelta(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return GAP;
  if (v === 0) return "0";
  return v > 0 ? `+${fmt(v)}` : fmt(v);
}

const value = (v: number | null): CompareCell => (v === null ? { text: NO_VALUE, kind: "none" } : { text: fmt(v), kind: "value" });
const absent = (): CompareCell => ({ text: GAP, kind: "absent" });
const delta = (v: number | null | undefined, has: boolean): CompareCell => (has && v !== null && v !== undefined ? { text: fmtDelta(v), kind: "value" } : { text: GAP, kind: "absent" });

/** The element of the table: the chosen one if the answer has it, else the first. */
export const elementOf = (c: CompareAnswer, chosen: string | null): string | null => (chosen && c.elements.includes(chosen) ? chosen : (c.elements[0] ?? null));

const nonZero = (v: number | null | undefined): boolean => v !== null && v !== undefined && v !== 0;

export function tableRow(r: CompareRow, index: number, element: string | null): CompareTableRow {
  const ea = element && r.a ? (r.a.elements[element] ?? null) : null;
  const eb = element && r.b ? (r.b.elements[element] ?? null) : null;
  const ed = element && r.delta ? (r.delta.elements[element] ?? null) : null;
  const both = r.only_in === null && r.a !== null && r.b !== null && r.delta !== null;
  const side = (row: unknown, v: number | null): CompareCell => (row ? value(v) : absent());
  return {
    index,
    group: groupText(r),
    title: rowTitle(r),
    total: r.total,
    only: r.only_in,
    note: r.only_in === "a" ? "только в A" : r.only_in === "b" ? "только в B" : null,
    differs: r.only_in !== null || nonZero(r.delta?.tonnes) || nonZero(ed?.grade) || nonZero(ed?.metal),
    tonnes: { a: side(r.a, r.a?.tonnes ?? null), b: side(r.b, r.b?.tonnes ?? null), d: delta(r.delta?.tonnes, both) },
    grade: { a: side(r.a, ea?.grade ?? null), b: side(r.b, eb?.grade ?? null), d: delta(ed?.grade, both && ed !== null) },
    metal: { a: side(r.a, ea?.metal ?? null), b: side(r.b, eb?.metal ?? null), d: delta(ed?.metal, both && ed !== null) },
  };
}

/** The rows of the table for the element; `onlyDiff` leaves the rows that differ (only in one report, or a difference that is not 0). */
export function tableRows(c: CompareAnswer, element: string | null, onlyDiff: boolean): CompareTableRow[] {
  const rows = c.rows.map((r, i) => tableRow(r, i, element));
  return onlyDiff ? rows.filter((r) => r.differs) : rows;
}

/** How many rows are only in one report. */
export const onlyCount = (c: CompareAnswer): { a: number; b: number } => ({ a: c.rows.filter((r) => r.only_in === "a").length, b: c.rows.filter((r) => r.only_in === "b").length });

// ---- the head: the versions of the inputs (Д-35) ----

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

/** «26 ноября 2026, 08:09» of an ISO date; an empty or wrong date — an empty text. */
export function dateText(iso: string): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** The fingerprint as a short text: the first 8 signs; none — «—». */
export const fpText = (fp: string | null): string => (fp ? fp.slice(0, 8) : GAP);

export interface HeadLine {
  kind: string;
  name: string;
  fp: string;
  /** the fingerprint of A and of B differ: this input changed between the reports */
  changed: boolean;
}

/** The inputs of both reports side by side by (kind, id): what changed between the two shows. */
export function headLines(c: CompareAnswer): { key: string; a: HeadLine | null; b: HeadLine | null }[] {
  const one = (x: CompareSide["inputs"][number]): HeadLine => ({ kind: x.kind, name: x.name || x.id, fp: fpText(x.fp), changed: false });
  const keys: string[] = [];
  const mapOf = (s: CompareSide) => {
    const m = new Map<string, CompareSide["inputs"][number]>();
    for (const x of s.inputs) {
      const k = `${x.kind}|${x.id}`;
      m.set(k, x);
      if (!keys.includes(k)) keys.push(k);
    }
    return m;
  };
  const ma = mapOf(c.a);
  const mb = mapOf(c.b);
  return keys.map((k) => {
    const x = ma.get(k);
    const y = mb.get(k);
    const changed = !!x && !!y && x.fp !== y.fp;
    return { key: k, a: x ? { ...one(x), changed } : null, b: y ? { ...one(y), changed } : null };
  });
}

/** The name of the kind of an input for people. */
export function kindText(kind: string): string {
  if (kind === "object:block_model") return "модель";
  if (kind === "bm_field") return "поле модели";
  if (kind.startsWith("object:")) return "объект";
  return kind || "вход";
}

/** The state of a report in the graph, in words. */
export function sideState(s: CompareSide): string {
  if (!s.stale) return "актуален";
  return s.stale_reason ? `устарел: ${s.stale_reason}` : "устарел";
}

// ---- the curves of both on one axis ----

export type Metric = "tonnes" | "grade" | "metal";

export const METRIC_TITLES: Record<Metric, string> = { tonnes: "Тоннаж выше борта, т", grade: "Среднее содержание выше борта", metal: "Металл выше борта" };

export interface CompareSeries {
  id: "a" | "b";
  label: string;
  xs: readonly number[];
  ys: readonly (number | null)[];
}

/** The curve of a report for the metric: null — the report has no curve (it was built with 0 points) or the element is not in it. */
export function seriesOf(id: "a" | "b", label: string, curve: Curve | null, metric: Metric, element: string | null): CompareSeries | null {
  if (!curve) return null;
  const n = curve.cutoffs.length;
  const e = element ? curve.elements[element] : undefined;
  const ys = metric === "tonnes" ? curve.tonnes : metric === "grade" ? e?.grade : e?.metal;
  if (!ys || ys.length !== n) return null;
  return { id, label, xs: curve.cutoffs, ys };
}

export const hasAny = (s: CompareSeries | null): boolean => !!s && s.ys.some((v) => v !== null && Number.isFinite(v));

/** What is said when a curve of a report is missing. */
export function noCurveText(c: CompareAnswer): string | null {
  const miss = [c.curves.a ? null : "A", c.curves.b ? null : "B"].filter(Boolean);
  if (!miss.length) return null;
  return miss.length === 2 ? "У обоих отчётов нет кривой: она выключена (0 точек)." : `У отчёта ${miss[0]} нет кривой: она выключена (0 точек), на графике только вторая.`;
}

/** The CSV of the data of the chart: «Борт; A; B» of every report on its own cut-offs (a gap is an empty cell). */
export function seriesCsvRows(series: readonly CompareSeries[]): (string | number | null)[][] {
  const out: (string | number | null)[][] = [];
  for (const s of series) s.xs.forEach((x, i) => out.push([s.id === "a" ? "A" : "B", x, s.ys[i] ?? null]));
  return out;
}

/** The notes of the core and of the window in one list: the grids that differ first. */
export function notes(c: CompareAnswer): { code: string; text: string }[] {
  const out: { code: string; text: string }[] = [];
  if (c.grids_differ) out.push({ code: "grids_differ", text: "Сетки различаются: модели или их блоки разные. Сравниваются только отчёты (числа по строкам), не блоки." });
  for (const w of c.warnings) out.push({ code: w.code, text: w.hint ? `${w.message} ${w.hint}` : w.message });
  return out;
}
