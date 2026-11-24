/**
 * The words and the numbers of the report «Содержание–тоннаж» for people and for the assistant (ТЗ 7.31): the table's
 * cells (a gap is «нет значения», never 0; a metal of an unknown unit is «—» with the reason), the lines of what was not
 * counted, the density used, the text of a finished report, the CSV file's name. Russian numbers — a decimal comma.
 * Pure (tested in node).
 */
import type { Excluded, GradeCell, ReportResult, ReportRow } from "./api";

export const NO_VALUE = "нет значения";
export const NO_VALUE_HINT = "В блоках строки нет значения этого содержания: это не 0, а пропуск — блоки не входят в среднее и в металл.";

/** A number for a table: the digits follow the size (a thousand — whole), null / not finite — «нет значения». */
export function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NO_VALUE;
  const a = Math.abs(v);
  const digits = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : a === 0 ? 0 : 4;
  return v.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

/** A count (blocks) as whole thousands-grouped number. */
export const fmtCount = (v: number): string => v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });

/** A cut-off as typed: no trailing zeros, up to 6 digits. */
export const fmtCutoff = (v: number): string => v.toLocaleString("ru-RU", { maximumFractionDigits: 6 });

/** A cell of the table: the text, whether it is a gap (the table draws it apart), the explanation for the tooltip. */
export interface Cell {
  text: string;
  missing: boolean;
  title?: string;
}

const gap = (): Cell => ({ text: NO_VALUE, missing: true, title: NO_VALUE_HINT });

export function gradeCell(c: GradeCell | undefined): Cell {
  if (!c || c.grade === null) return gap();
  return { text: fmt(c.grade), missing: false };
}

export function metalCell(c: GradeCell | undefined): Cell {
  if (!c || c.grade === null) return gap();
  if (c.metal === null) return { text: "—", missing: false, title: `Единица «${c.unit ?? "не задана"}» не распознана: металл считается для г/т, ppm, % и ppb.` };
  return { text: fmt(c.metal), missing: false };
}

/** «3 бл. · 12,5 т» — the blocks of the row without the grade; none — «0». */
export function gapCell(c: GradeCell | undefined): Cell {
  if (!c || c.missing_blocks === 0) return { text: "0", missing: false };
  return { text: `${fmtCount(c.missing_blocks)} бл. · ${fmt(c.missing_tonnes)} т`, missing: false, title: "Блоки без этого содержания: их тоннаж в тоннаже строки, а в среднее и металл они не входят." };
}

export const groupTitle = (g: string | null): string => (g === null ? "Всего" : g);

/** The grades of the table (the keys of the first row, in the order of the core). */
export const gradeNames = (r: ReportResult): string[] => Object.keys(r.rows[0]?.grades ?? {});

/** The unit of a column «содержание» («г/т»), or empty. */
export function unitOf(r: ReportResult, name: string): string {
  return r.rows.find((x) => x.grades[name]?.unit)?.grades[name]?.unit ?? "";
}

export function metalUnitOf(r: ReportResult, name: string): string {
  return r.rows.find((x) => x.grades[name]?.metal_unit)?.grades[name]?.metal_unit ?? "";
}

/** The rows of a group (null — the whole model). */
export const rowsOf = (r: ReportResult, group: string | null): ReportRow[] => r.rows.filter((x) => x.group === group);

/** The groups of the table in the order of the core (the whole model first when it has rows). */
export function groupsOf(r: ReportResult): (string | null)[] {
  return [...new Set(r.rows.map((x) => x.group))];
}

const EXCLUDED_TEXT: Record<keyof Excluded, (field: string) => string> = {
  filtered: () => "отсеяны ограничителем, фильтром или отбором категорий",
  no_volume: () => "без объёма (пустое поле доли FRAC)",
  no_density: () => "без плотности (нет значения или плотность меньше нуля)",
  no_cutoff: (f) => `без значения поля борта «${f}»`,
};

/** What was not counted, one line each: «без плотности …: 12 блоков»; none when everything was counted. */
export function excludedLines(e: Excluded, cutoffField: string): string[] {
  return (Object.keys(EXCLUDED_TEXT) as (keyof Excluded)[]).filter((k) => e[k] > 0).map((k) => `${EXCLUDED_TEXT[k](cutoffField)}: ${fmtCount(e[k])} ${blocksWord(e[k])}`);
}

/** «1 блок», «2 блока», «5 блоков» — the word after a number. */
export function blocksWord(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "блок";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "блока";
  return "блоков";
}

/** «поле SG» / «2,7 т/м³»: the density the report used. */
export const densityText = (r: ReportResult): string => (r.density.field ? `поле «${r.density.field}»` : r.density.value !== null ? `${fmtCutoff(r.density.value)} т/м³` : "не задана");

/** The file's name of the CSV: «Содержание-тоннаж Модель» without the characters a file name may not have. */
export function csvName(model: string): string {
  const clean = model.replace(/[\\/:*?"<>|\r\n\t]+/g, " ").replace(/\s+/g, " ").trim();
  return `Содержание-тоннаж${clean ? ` ${clean}` : ""}`;
}

/** The text of a finished report for the log and for the assistant: the model, the density, the rows, what was not counted. */
export function summaryText(r: ReportResult, model: string, maxRows = 12): string {
  const names = gradeNames(r);
  const lines = [`Отчёт «Содержание–тоннаж», модель «${model}»: плотность — ${densityText(r)}, борт по полю «${r.cutoff_field}», строк в таблице ${r.rows.length}.`];
  for (const row of r.rows.slice(0, maxRows)) {
    const grades = names
      .map((n) => {
        const c = row.grades[n];
        if (!c) return `${n}: ${NO_VALUE}`;
        const base = c.grade === null ? `${n}: ${NO_VALUE}` : `${n} ${fmt(c.grade)}${c.unit ? ` ${c.unit}` : ""}${c.metal !== null ? `, металл ${fmt(c.metal)} ${c.metal_unit ?? ""}`.trimEnd() : ""}`;
        return c.missing_blocks > 0 ? `${base} (без значения ${fmtCount(c.missing_blocks)} ${blocksWord(c.missing_blocks)})` : base;
      })
      .join("; ");
    lines.push(`${row.group === null ? "" : `[${row.group}] `}борт ${fmtCutoff(row.cutoff)}: ${fmtCount(row.blocks)} ${blocksWord(row.blocks)}, объём ${fmt(row.volume)} ${r.units.volume}, тоннаж ${fmt(row.tonnes)} ${r.units.tonnes}; ${grades}`);
  }
  if (r.rows.length > maxRows) lines.push(`… и ещё ${r.rows.length - maxRows} строк (полная таблица — в окне отчёта).`);
  if (r.curve) lines.push(`Кривая: ${r.curve.cutoffs.length} точек по полю «${r.curve.field}».`);
  const out = excludedLines(r.excluded, r.cutoff_field);
  if (out.length) lines.push(`Не вошли в расчёт: ${out.join("; ")}.`);
  for (const w of r.warnings) lines.push(`Предупреждение: ${w}`);
  return lines.join("\n");
}
