/**
 * The words of the results of the operations on the fields (what the log, the assistant and the window read): the field
 * written, the counts, a gap said as a gap (never 0). Russian, short. No React.
 */
import type { OpResult } from "./api";
import type { Op } from "./state";

const fmt = (v: number): string => v.toLocaleString("ru-RU", { maximumFractionDigits: 6 });

/** A number, or «нет значения» for a gap. */
export const numText = (v: number | null): string => (v === null ? "нет значения" : fmt(v));

/** «блоков 24» — counts are written without declension: the line stays right for any number. */
const part = (label: string, v: number | null): string | null => (v === null ? null : `${label}: ${fmt(v)}`);

const join = (parts: (string | null)[]): string => parts.filter((p): p is string => p !== null).join(", ");

/** The range of the new field: «от 0 до 10, среднее 2,68» (a field with no value at all — «значений нет»). */
export function rangeText(r: OpResult): string | null {
  const s = r.stats;
  if (!s || s.count === 0 || s.min === null || s.max === null) return s ? "значений нет" : null;
  return `от ${fmt(s.min)} до ${fmt(s.max)}${s.mean !== null ? `, среднее ${fmt(s.mean)}` : ""}`;
}

/** The counts of the categories: «Измеренные — 1; Выявленные — 5»; labels in the order of the rules. */
export function countsText(r: OpResult): string {
  return r.counts ? Object.entries(r.counts).map(([k, v]) => `${k} — ${fmt(v)}`).join("; ") : "";
}

/** The line of the log for a finished run. */
export function summaryText(op: Op, r: OpResult, model: string): string {
  const where = `в модель «${model}»`;
  const version = r.version !== null ? `; версия модели ${r.version}` : "";
  if (op === "classify" || (op === "rebuild" && r.counts)) {
    const cats = countsText(r);
    const nums = join([part("блоков", r.rows), part("прошло ограничитель", r.passed), part("без категории", r.unclassified), part("без оценки", r.noEstimate)]);
    return `Категории «${r.field}» записаны ${where}: ${nums}${cats ? `; ${cats}` : ""}${version}`;
  }
  if (op === "density" || (op === "rebuild" && r.fields.length > 1)) {
    const src = r.fields[1] ? ` и источник «${r.fields[1]}»` : "";
    return `Плотность «${r.field}»${src} записана ${where}: ${join([part("блоков", r.rows), part("прошло ограничитель", r.passed), part("подставлено по умолчанию", r.defaulted), part("без плотности", r.empty)])}${version}`;
  }
  const range = rangeText(r);
  return `Поле «${r.field}» записано ${where}: ${join([part("блоков", r.rows), part("прошло ограничитель", r.passed), part("без значения", r.missing)])}${range ? `; ${range}` : ""}${version}`;
}

/** The title of a run as the window shows it while it goes. */
export function runTitle(op: Op | null, field: string, model: string): string {
  const what = op === "density" ? "Плотность" : op === "classify" ? "Категории" : op === "rebuild" ? "Перестройка поля" : "Калькулятор";
  return `${what} «${field}» · ${model}`;
}
