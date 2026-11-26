/**
 * The words and the numbers of the pit optimisation for people and for the assistant (ТЗ 8.11): the text of a finished run and of a template check,
 * a warning, the unit of a price. Russian numbers — a decimal comma; a gap is «—», never 0. The table and the chart of the result are the
 * panel's (21.18): the interface shows the text of the core as it is. Pure.
 */
import { NO_VALUE } from "../blocks/report/format";
import { DEFAULT_PRICE_LABELS, type CheckSummary, type RunSummary, type Warning } from "./answers";
import { unitOfField } from "./forms";
import { formatNumber } from "./reportModel";

export { NO_VALUE };

/** A gap inside a sentence: «руда — т», never «руда нет значения т». */
const GAP = "—";

/**
 * The one writing of a number for the panel, the commands, the tables and the comparison (the one of the core: `reportModel.formatNumber`): a decimal comma, a no-break space
 * between the thousands, a gap is «—» (never 0, never «нет значения» in the middle of a sentence). `whole` — tonnes, money, volumes, counts; `fmt` — two digits (RF, degrees, seconds, per cent).
 */
export const whole = (v: number | null | undefined): string => formatNumber(v, 0);
export const fmtCount = whole;
export const fmt = (v: number | null | undefined): string => formatNumber(v, 2);
/** Seconds of a job: a short job keeps its digits («0,004 с»), a long one does not need them. */
export const secondsText = (v: number | null | undefined): string => formatNumber(v, v !== null && v !== undefined && v >= 10 ? 1 : v !== null && v !== undefined && v >= 1 ? 2 : 3);

export const warningText = (w: Pick<Warning, "message" | "hint">): string => (w.hint ? `${w.message} ${w.hint}` : w.message);

/** The unit of the price of an element: by its unit (the one of the row, else the field's); no unit — «за единицу металла». */
export function priceLabel(unit: string | null, fieldUnit: string | null, labels: Record<string, string> = DEFAULT_PRICE_LABELS): string {
  const code = unit ?? unitOfField(fieldUnit);
  return code ? (labels[code] ?? "за единицу металла") : "за единицу металла";
}

const degrees = (v: number | null): string => (v === null ? GAP : `${fmt(v)}°`);

/** The text of a finished run for the log and for the assistant. */
export function runText(r: RunSummary): string {
  const lines = [`Сценарий «${r.name}» рассчитан${r.solver ? ` (решатель ${r.solver.toUpperCase()})` : ""}: оболочек ${fmtCount(r.shells.length)}${r.seconds === null ? "" : `, за ${secondsText(r.seconds)} с`}.`];
  const last = r.shells[r.shells.length - 1];
  if (last) lines.push(`Последняя оболочка (RF ${fmt(last.rf)}): блоков ${fmtCount(last.blocks)}, руда ${whole(last.ore_t)} т, пустая ${whole(last.waste_t)} т, ценность ${whole(last.value)} $.`);
  const t = r.totals;
  if (t.ore_t !== null || t.cf !== null) lines.push(`Итог (RF = 1): руда ${whole(t.ore_t)} т, пустая ${whole(t.waste_t)} т, вскрыша ${t.strip === null ? "не определена (руды нет)" : fmt(t.strip)}, CF ${whole(t.cf)} $.`);
  if (r.template.k !== null) lines.push(`Шаблон: K = ${r.template.k}, ошибка угла до ${degrees(r.template.error_max)}.`);
  if (r.objective_error !== null) lines.push(`Ошибка целевой функции из-за округления до целых — не больше ${fmt(r.objective_error)} $.`);
  for (const w of r.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(w)}`);
  if (r.warnings.length > 5) lines.push(`…и ещё предупреждений: ${r.warnings.length - 5}.`);
  return lines.join("\n");
}

/** The text of a template check (`pit.check`). */
export function checkText(c: CheckSummary): string {
  const lines = [`Шаблон проверен: K = ${c.k === null ? GAP : c.k}${c.k_auto ? " (подобран автоматически)" : ""}, допуск ${degrees(c.tolerance)}.`];
  for (const d of c.domains) lines.push(`${d.label ?? "Все блоки"}: смещений ${d.n_offsets === null ? GAP : fmtCount(d.n_offsets)}, ошибка угла до ${degrees(d.error_max)} (в среднем ${degrees(d.error_mean)})${d.warning ? " — больше допуска" : ""}.`);
  for (const w of c.warnings.slice(0, 5)) lines.push(`Предупреждение: ${warningText(w)}`);
  return lines.join("\n");
}
