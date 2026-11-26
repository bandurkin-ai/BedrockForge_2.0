/**
 * What the window «Сверка данных и модели» shows, as pure functions (ТЗ 7.35 Д-33): the cells of the matrix «код данных × код модели» (length
 * in metres or the number of intervals; a gap is «—», never 0), the weight of a cell for its tint, the sums of the rows and of the columns, the
 * words of the facts and of the list of the intervals that do not agree, and the text for the assistant. No React.
 */
import { num } from "../../shell/props";
import type { CompareAnswer, Mismatch } from "./compareApi";

export type Metric = "length" | "count";
export const METRICS: readonly Metric[] = ["length", "count"];
export const METRIC_TITLES: Record<Metric, string> = { length: "Длина, м", count: "Число интервалов" };

/** The value of a cell; null — a gap of the data. */
export const cellValue = (a: CompareAnswer, metric: Metric, row: number, col: number): number | null => (metric === "length" ? (a.length[row]?.[col] ?? null) : (a.count[row]?.[col] ?? null));

export function valueText(v: number | null, metric: Metric): string {
  if (v === null || !Number.isFinite(v)) return "—";
  if (v === 0) return "·"; // nothing there: a true zero, not a gap
  return metric === "length" ? num(v, 2) : num(v, 0);
}

export const isDiagonal = (a: CompareAnswer, row: number, col: number): boolean => a.agree[row]?.[col] === true;

/** The share of the whole length (or of the number) that a cell holds: its tint. */
export function weight(a: CompareAnswer, metric: Metric, row: number, col: number): number {
  const v = cellValue(a, metric, row, col);
  const total = metric === "length" ? (a.coded_length ?? a.total_length ?? 0) : a.count.reduce((s, r) => s + r.reduce((x, y) => x + y, 0), 0);
  return v !== null && total > 0 ? Math.min(1, v / total) : 0;
}

export function rowSum(a: CompareAnswer, metric: Metric, row: number): number {
  return (metric === "length" ? (a.length[row] ?? []) : (a.count[row] ?? [])).reduce<number>((s, v) => s + (v ?? 0), 0);
}

export function colSum(a: CompareAnswer, metric: Metric, col: number): number {
  return a.data_codes.reduce((s, _c, row) => s + (cellValue(a, metric, row, col) ?? 0), 0);
}

/** «99,84 %» of the coded length agrees; a gap — «—». */
export const shareText = (v: number | null): string => (v === null || !Number.isFinite(v) ? "—" : `${num(v * 100, 2)} %`);

export const lengthText = (v: number | null): string => (v === null || !Number.isFinite(v) ? "—" : `${num(v, 2)} м`);

export interface Fact {
  key: string;
  title: string;
  value: string;
  hint: string;
}

export function facts(a: CompareAnswer): Fact[] {
  return [
    { key: "share", title: "Совпадение", value: shareText(a.agree_share), hint: "Длина интервалов, у которых код модели совпал с кодом данных, к длине интервалов с кодом" },
    { key: "agree", title: "Совпало", value: lengthText(a.agree_length), hint: "Длина по диагонали матрицы" },
    { key: "total", title: "Всего", value: lengthText(a.total_length), hint: "Длина всех интервалов таблицы" },
    { key: "mismatch", title: "Не совпало, интервалов", value: num(a.mismatches.total, 0), hint: "Интервалы, у которых код данных не входит в коды группы модели" },
    { key: "intervals", title: "Интервалов, скважин", value: `${num(a.intervals, 0)}; ${num(a.holes, 0)}`, hint: "Сколько интервалов и скважин сверено" },
  ];
}

/** The remarks under the facts: intervals without a code, holes without a trajectory, the notes of the core. */
export function remarks(a: CompareAnswer): string[] {
  const out: string[] = [];
  if (a.no_code.count > 0) out.push(`Интервалов без кода данных: ${num(a.no_code.count, 0)} (${lengthText(a.no_code.length)}) — отдельная строка матрицы, в совпадение не входят.`);
  if (a.no_trajectory.holes > 0) out.push(`Скважин без траектории: ${num(a.no_trajectory.holes, 0)}, интервалов ${num(a.no_trajectory.intervals, 0)} (${lengthText(a.no_trajectory.length)}) — в сверку не вошли.`);
  return [...out, ...a.notes];
}

export const mismatchRange = (m: Mismatch): string => (m.from === null || m.to === null ? "—" : `${num(m.from, 2)}–${num(m.to, 2)}`);

export const pagesOf = (total: number, size: number): number => Math.max(1, Math.ceil(total / Math.max(1, size)));

/** The first mismatches of the page as words, for the assistant. */
export function compareLine(a: CompareAnswer, first = 3): string {
  const head = `Модель «${a.model.name}» и таблица «${a.table.name}» (поле ${a.table.field}): совпадение ${shareText(a.agree_share)} длины (${lengthText(a.agree_length)} из ${lengthText(a.coded_length ?? a.total_length)}); не совпало интервалов ${num(a.mismatches.total, 0)}`;
  const rows = a.mismatches.rows.slice(0, first).map((m) => `${m.hole} ${mismatchRange(m)} м: данные ${m.data_code}, модель ${m.model_code}, ${lengthText(m.length)}`);
  return `${head}${rows.length ? `; самые длинные: ${rows.join("; ")}` : ""}.${a.model.stale_reason ? ` Модель устарела: ${a.model.stale_reason}.` : ""}`;
}
