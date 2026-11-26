/**
 * The words and the numbers of the resource report for people and for the assistant (ТЗ 8.10): the label of a row, the text of a finished
 * run, the text of a curve, of a drilled cell and of a comparison. Russian numbers — a decimal comma; a gap is «нет значения», never 0.
 * The table of the result and the form are the core's text (`form.sections[].rows[].cells[].text`): the interface shows it as it is. Pure.
 */
import { fmt, fmtCount, fmtCutoff } from "../blocks/report/format";
import type { CompareAnswer, Curve, DrillAnswer, ReportResult, ResultRow, Warning } from "./answers";

export { fmt, fmtCount, fmtCutoff, NO_VALUE } from "../blocks/report/format";

/** The row by its cut-off: «без борта», «≥ 0,5», «балансовые ≥ 1», «забалансовые 0,5…1». */
export function rowTitle(r: Pick<ResultRow, "kind" | "cutoff"> & { cutoff_to?: number | null }): string {
  if (r.kind === "all" || (r.kind === "cutoff" && r.cutoff === null)) return "без борта";
  if (r.kind === "balance") return `балансовые ≥ ${r.cutoff === null ? "?" : fmtCutoff(r.cutoff)}`;
  if (r.kind === "offbalance") return `забалансовые ${r.cutoff === null ? "?" : fmtCutoff(r.cutoff)}…${r.cutoff_to === null || r.cutoff_to === undefined ? "?" : fmtCutoff(r.cutoff_to)}`;
  return `≥ ${fmtCutoff(r.cutoff as number)}`;
}

/** The labels of the keys of a row as one text: «Измеренные / A»; the total — «Итого». */
export const groupText = (r: Pick<ResultRow, "g" | "total">): string => (r.total || !r.g.length ? "Итого" : r.g.map((x) => x ?? "(пусто)").join(" / "));

/** The total of the first cut-off: what the tree and the summary show. */
export function firstTotal(r: ReportResult): ResultRow | null {
  return r.totals ?? r.rows.find((x) => x.total) ?? null;
}

const metalText = (r: ReportResult, field: string, metal: number | null): string => {
  const info = r.elements.find((e) => e.field === field);
  if (metal === null) return info?.metal_family ? "металл — нет значения" : "металл не считается (единица не распознана)";
  return `металл ${fmt(metal)} ${info?.metal_base ?? ""}`.trim();
};

export const warningText = (w: Warning): string => (w.hint ? `${w.message} ${w.hint}` : w.message);

/** The text of a finished run for the log and for the assistant. */
export function summaryText(r: ReportResult): string {
  const head = r.saved ? `Отчёт «${r.name}» №${r.report_id ?? "?"} записан в проект` : `Предпросмотр отчёта по ресурсам (не записан в проект)`;
  const lines = [`${head}: модель «${r.model.name}», строк ${fmtCount(r.rows_total)}${r.truncated ? " (показаны не все: сохраните отчёт, чтобы смотреть все)" : ""}, поле борта ${r.cutoff_field}.`];
  const t = firstTotal(r);
  if (t) {
    const per = Object.entries(t.elements).map(([f, c]) => {
      const u = r.elements.find((e) => e.field === f)?.unit_label ?? "";
      return `${f} ${c.grade === null ? "нет значения" : `${fmt(c.grade)} ${u}`.trim()}, ${metalText(r, f, c.metal)}`;
    });
    lines.push(`Итог (${rowTitle(t)}): блоков ${fmtCount(t.blocks)}, объём ${fmt(t.volume)} м³, тоннаж ${fmt(t.tonnes)} т${per.length ? `; ${per.join("; ")}` : ""}.`);
  }
  const ex = r.excluded;
  if (ex.filtered || ex.no_volume || ex.no_density) lines.push(`Не вошли: по ограничителю ${fmtCount(ex.filtered)}, без объёма ${fmtCount(ex.no_volume)}, без плотности ${fmtCount(ex.no_density)}.`);
  if (r.control?.sum_groups && !r.control.sum_groups.ok) lines.push("Контроль: сумма групп не равна итогу (отклонение больше 1e-12).");
  const all = [...r.warnings, ...(r.control?.warnings ?? []), ...(r.form?.warnings ?? [])];
  for (const w of all.slice(0, 5)) lines.push(`Предупреждение: ${warningText(w)}`);
  if (all.length > 5) lines.push(`…и ещё предупреждений: ${all.length - 5}.`);
  return lines.join("\n");
}

/** A few points of the curve (the first, the last, evenly between) for the log and for the assistant. */
export function curveText(r: ReportResult, curve: Curve, points = 6): string {
  const n = curve.cutoffs.length;
  const idx = [...new Set(Array.from({ length: Math.min(points, n) }, (_, i) => Math.round((i * (n - 1)) / Math.max(1, Math.min(points, n) - 1))))];
  const names = Object.keys(curve.elements);
  const lines = [`Кривая «тоннаж — содержание» отчёта «${r.name}»: ${n} бортов по полю ${curve.field}, от ${fmtCutoff(curve.cutoffs[0])} до ${fmtCutoff(curve.cutoffs[n - 1])}.`];
  for (const i of idx) {
    const per = names.map((f) => `${f} ${fmt(curve.elements[f].grade[i] ?? null)}`).join(", ");
    lines.push(`≥ ${fmtCutoff(curve.cutoffs[i])}: тоннаж ${fmt(curve.tonnes[i] ?? null)} т${per ? `, ${per}` : ""}`);
  }
  return lines.join("\n");
}

/** A drilled cell: its numbers, the warning when the report is out of date. */
export function drillText(d: DrillAnswer, name: string): string {
  const lines = [`Ячейка отчёта «${name}»: блоков ${fmtCount(d.cell.blocks)}, объём ${fmt(d.cell.volume)} м³, тоннаж ${fmt(d.cell.tonnes)} т.`];
  if (d.stale) lines.push(`Отчёт устарел${d.stale_reason ? ` (${d.stale_reason})` : ""}: набор блоков может отличаться от показанного в таблице. Пересчитайте отчёт.`);
  lines.push(`Правила отбора блоков (для blocks.filter, restrict): ${JSON.stringify(d.rules)}`);
  return lines.join("\n");
}

/** The comparison of two reports: the state of the grids and the biggest differences of the tonnes. */
export function compareText(c: CompareAnswer): string {
  const lines = [`Сравнение отчётов «${c.a.name}» (A) и «${c.b.name}» (B): строк ${fmtCount(c.rows.length)}.`];
  if (c.grids_differ) lines.push("Сетки различаются: сравниваются только отчёты, не блоки.");
  const only = c.rows.filter((r) => r.only_in !== null);
  if (only.length) lines.push(`Строки только в одном отчёте: ${fmtCount(only.length)} (у них нет разности — это пропуск, не 0).`);
  const diff = c.rows.filter((r) => r.delta && r.delta.tonnes !== null && r.delta.tonnes !== 0);
  const top = [...diff].sort((x, y) => Math.abs(y.delta?.tonnes ?? 0) - Math.abs(x.delta?.tonnes ?? 0)).slice(0, 5);
  if (!diff.length && !only.length) lines.push("Различий нет: разности тоннажа во всех строках равны нулю.");
  for (const r of top) lines.push(`${groupText(r)}, ${rowTitle(r)}: ΔТ = ${fmt(r.delta?.tonnes ?? null)} т`);
  for (const w of c.warnings) lines.push(`Предупреждение: ${warningText(w)}`);
  return lines.join("\n");
}
