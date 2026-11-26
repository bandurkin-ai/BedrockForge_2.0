/**
 * The facts of the properties of a saved report (ТЗ 8.10; ADR 0027 решения 14, 16): what the report was made of (the model, the cut-off field and the cut-offs, the elements
 * and their units, the density, the keys of the groups, the template) and what it says (the rows, the total of the first cut-off), the date and the files written. The
 * numbers are the core's; a gap is «нет значения», never 0. Pure.
 */
import type { ReportListRow, ReportResult } from "./answers";
import { dateText } from "./compareModel";
import { fmt, fmtCount, fmtCutoff, firstTotal, rowTitle } from "./format";

export interface Fact {
  key: string;
  label: string;
  value: string;
}

/** The cut-offs of the request as they were asked: «без борта; 1; 2». The core keeps them as a list of numbers and `null`. */
export function cutoffsText(params: Record<string, unknown>): string {
  const c = params.cutoffs;
  if (!Array.isArray(c) || !c.length) return "без борта";
  return c.map((x) => (typeof x === "number" ? fmtCutoff(x) : "без борта")).join("; ");
}

/** The density of the request: «поле SG» or «2,7 т/м³»; the density of the model is the one the core chose (`density.field`). */
export function densityText(r: ReportResult): string {
  if (r.density.field) return `поле ${r.density.field}`;
  return r.density.value === null ? "нет значения" : `${fmt(r.density.value)} т/м³`;
}

/** The keys of the groups in the order of the table: «CLASS; Уступ». */
export function keysText(r: ReportResult): string {
  return r.keys.length ? r.keys.map((k) => (k.kind === "bench" ? "Уступ" : (k.field ?? "?"))).join("; ") : "без групп";
}

export function reportFacts(r: ReportResult, row?: ReportListRow): Fact[] {
  const t = firstTotal(r);
  const facts: Fact[] = [
    { key: "model", label: "Модель", value: `«${r.model.name}»${r.model.version !== null ? `, версия ${r.model.version}` : ""}` },
    { key: "version", label: "Версия отчёта", value: String(r.version) },
    { key: "cutoff_field", label: "Поле борта", value: r.cutoff_field },
    { key: "cutoffs", label: "Борта", value: cutoffsText(r.params) },
    { key: "elements", label: "Элементы", value: r.elements.length ? r.elements.map((e) => `${e.field}${e.unit_label ? `, ${e.unit_label}` : ""}`).join("; ") : "нет" },
    { key: "density", label: "Плотность", value: densityText(r) },
    { key: "keys", label: "Группы", value: keysText(r) },
    { key: "template", label: "Шаблон формы", value: r.form?.template_name || (typeof r.params.template_id === "string" ? r.params.template_id : "") || "нет" },
    { key: "rows", label: "Строк", value: fmtCount(r.rows_total) },
  ];
  if (t) {
    facts.push({ key: "total", label: `Итог, ${rowTitle(t)}`, value: `блоков ${fmtCount(t.blocks)}, тоннаж ${fmt(t.tonnes)} т` });
    for (const e of r.elements) {
      const c = t.elements[e.field];
      facts.push({ key: `total-${e.field}`, label: e.field, value: c && c.grade !== null ? `${fmt(c.grade)} ${e.unit_label}, металл ${c.metal === null ? "не считается (единица не распознана)" : `${fmt(c.metal)} ${e.metal_base ?? ""}`}`.replace(/\s+,/g, ",").trim() : "нет значения" });
    }
  }
  const created = r.created_at ?? row?.created_at ?? "";
  if (created) facts.push({ key: "created", label: "Создан", value: dateText(created) || created });
  if (r.files.length) facts.push({ key: "files", label: "Файлы", value: r.files.map((f) => `${f.format.toUpperCase()}: ${f.path}`).join("\n") });
  return facts;
}
