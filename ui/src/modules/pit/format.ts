/**
 * The words and the numbers of the pit optimisation for people and for the assistant (ТЗ 8.11): the text of a finished run and of a template check,
 * a warning, the unit of a price. Russian numbers — a decimal comma; a gap is «нет значения», never 0. The table and the chart of the result are the
 * panel's (21.18): the interface shows the text of the core as it is. Pure.
 */
import { fmt, fmtCount, NO_VALUE } from "../blocks/report/format";
import { DEFAULT_PRICE_LABELS, type CheckSummary, type RunSummary, type Warning } from "./answers";
import { unitOfField } from "./forms";

export { fmt, fmtCount, NO_VALUE };

export const warningText = (w: Pick<Warning, "message" | "hint">): string => (w.hint ? `${w.message} ${w.hint}` : w.message);

/** The unit of the price of an element: by its unit (the one of the row, else the field's); no unit — «за единицу металла». */
export function priceLabel(unit: string | null, fieldUnit: string | null, labels: Record<string, string> = DEFAULT_PRICE_LABELS): string {
  const code = unit ?? unitOfField(fieldUnit);
  return code ? (labels[code] ?? "за единицу металла") : "за единицу металла";
}

const degrees = (v: number | null): string => (v === null ? NO_VALUE : `${fmt(v)}°`);

/** The text of a finished run for the log and for the assistant. */
export function runText(r: RunSummary): string {
  const lines = [`Сценарий «${r.name}» рассчитан${r.solver ? ` (решатель ${r.solver.toUpperCase()})` : ""}: оболочек ${fmtCount(r.shells.length)}${r.seconds === null ? "" : `, за ${fmt(r.seconds)} с`}.`];
  const last = r.shells[r.shells.length - 1];
  if (last) lines.push(`Последняя оболочка (RF ${fmt(last.rf)}): блоков ${fmtCount(last.blocks)}, руда ${fmt(last.ore_t)} т, пустая ${fmt(last.waste_t)} т, ценность ${fmt(last.value)} $.`);
  const t = r.totals;
  if (t.ore_t !== null || t.cf !== null) lines.push(`Итог (RF = 1): руда ${fmt(t.ore_t)} т, пустая ${fmt(t.waste_t)} т, вскрыша ${t.strip === null ? "не определена (руды нет)" : fmt(t.strip)}, CF ${fmt(t.cf)} $.`);
  if (r.template.k !== null) lines.push(`Шаблон: K = ${r.template.k}, ошибка угла до ${degrees(r.template.error_max)}.`);
  if (r.objective_error !== null) lines.push(`Ошибка целевой функции из-за округления до целых — не больше ${fmt(r.objective_error)} $.`);
  for (const w of r.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(w)}`);
  if (r.warnings.length > 5) lines.push(`…и ещё предупреждений: ${r.warnings.length - 5}.`);
  return lines.join("\n");
}

/** The text of a template check (`pit.check`). */
export function checkText(c: CheckSummary): string {
  const lines = [`Шаблон проверен: K = ${c.k === null ? NO_VALUE : c.k}${c.k_auto ? " (подобран автоматически)" : ""}, допуск ${degrees(c.tolerance)}.`];
  for (const d of c.domains) lines.push(`${d.label ?? "Все блоки"}: смещений ${d.n_offsets === null ? NO_VALUE : fmtCount(d.n_offsets)}, ошибка угла до ${degrees(d.error_max)} (в среднем ${degrees(d.error_mean)})${d.warning ? " — больше допуска" : ""}.`);
  for (const w of c.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(w)}`);
  return lines.join("\n");
}
