/**
 * The words of the window «План оценки» and of the section «Параметры оценки» of the tree (ТЗ 8.8.4; docs/progress/e18-api.md §2.2,
 * §4, §6): the state of a row («устарел» with its reason, «источник удалён»), the rows of a plan that write into the same fields,
 * why a plan cannot be run, the journal of a run in lines. Pure.
 */
import { METHOD_SHORT, obj, STATUS_LABELS, str, type EstimationInfo, type EstimationRow, type RunResult, type RunRow } from "./api";
import { singularText } from "./runState";

const nf = (n: number): string => n.toLocaleString("ru-RU");

/** «устарел: изменена таблица «Пробы»»; «источник удалён»; null — the row is up to date. */
export function stateText(r: Pick<EstimationRow, "stale" | "stale_reason" | "source_lost">): string | null {
  if (r.source_lost) return "источник удалён";
  return r.stale ? `устарел${r.stale_reason ? `: ${r.stale_reason}` : ""}` : null;
}

/** The domain of a row: its label, or the whole model when the row has none. */
export const domainText = (r: Pick<EstimationRow, "label">): string => r.label ?? "вся модель";

/** The field of the samples a row estimates («AU»), from its stored parameters; null — they are not read yet. */
export const sampleField = (info: Pick<EstimationInfo, "meta"> | undefined): string | null => (info ? str(obj(info.meta.samples).field) : null);

/** The fields a row writes: the prefix with a star («AU_OK_*»). */
export const fieldsText = (r: Pick<EstimationRow, "prefix">): string => `${r.prefix}*`;

/** «OK · окисл · AU_OK_*» — the row in one line (the tree, the messages). */
export const rowLine = (r: Pick<EstimationRow, "method" | "label" | "prefix">): string => `${METHOD_SHORT[r.method]} · ${domainText(r)} · ${fieldsText(r)}`;

/** «4 марта 2026 …: оценён 1 100, вне домена 60, …» — the last run of a row; null — it was not run. */
export function lastRunText(r: Pick<EstimationRow, "last_run">): string | null {
  const l = r.last_run;
  if (!l) return null;
  const at = l.at ? new Date(l.at).toLocaleString("ru-RU") : "—";
  const by = STATUS_LABELS.flatMap((w, i) => (l.by_status[i] ? [`${w} ${nf(l.by_status[i])}`] : [])).join(", ");
  return by ? `${at}: ${by}` : at;
}

/**
 * The rows of a plan with one prefix write into the same fields and must differ by the domain and agree by the method (§2.2): the
 * words about each such pair that does not; the core refuses such a plan (422 `bad_param`), the window says why before.
 */
export function prefixWarnings(rows: readonly Pick<EstimationRow, "id" | "method" | "label" | "prefix">[]): string[] {
  const by = new Map<string, typeof rows[number][]>();
  for (const r of rows) by.set(r.prefix, [...(by.get(r.prefix) ?? []), r]);
  const out: string[] = [];
  for (const [prefix, group] of by) {
    if (group.length < 2) continue;
    if (new Set(group.map((r) => r.method)).size > 1) out.push(`Строки с префиксом «${prefix}» пишут в одни поля, но методы у них разные (${[...new Set(group.map((r) => METHOD_SHORT[r.method]))].join(", ")}).`);
    const labels = group.map((r) => r.label ?? "");
    if (new Set(labels).size < labels.length) out.push(`Строки с префиксом «${prefix}» пишут в одни поля, а домены у них совпадают: задайте разные домены или другой префикс.`);
  }
  return out;
}

/** Why the rows cannot be run now; null — they can. `rows` are the rows of the plan that are in the list; `want` — how many the plan names. */
export function runReason(rows: readonly EstimationRow[], want: number, opts: { readOnly: boolean; busy: boolean; modelFound: boolean }): string | null {
  if (opts.readOnly) return "Проект открыт только для чтения";
  if (opts.busy) return "Оценка уже выполняется";
  if (!opts.modelFound) return "Выберите блочную модель";
  if (!want) return "В плане нет строк";
  if (rows.length < want) return "Часть строк плана удалена: сохраните план";
  const lost = rows.find((r) => r.source_lost);
  if (lost) return `Строка «${lost.name}»: источник удалён`;
  return null;
}

// ---- the journal ----

/** The blocks by the status of a row: «оценён 1 100 · вне домена 60 · мало проб 35 · вырожденная система 5». */
export function statusText(by: readonly number[]): string {
  return STATUS_LABELS.map((w, i) => `${w} ${nf(by[i] ?? 0)}`).join(" · ");
}

/** The blocks by the pass: «проход 1 — 990, проход 2 — 90»; "" — no pass estimated any. */
export function passText(by: readonly number[]): string {
  return by.flatMap((n, i) => (n ? [`проход ${i + 1} — ${nf(n)}`] : [])).join(", ");
}

/** The words about the samples a row used: «Проб: 880 из 1 196; усреднено в одну точку: 2; пропуск значения: 4». */
export function samplesText(c: RunRow["counts"]): string {
  const s = c.samples;
  if (!s) return "";
  const parts = [`Проб: ${nf(s.used)} из ${nf(s.rows)}`];
  if (s.merged) parts.push(`усреднено в одну точку: ${nf(s.merged)}`);
  if (s.missing) parts.push(`пропуск значения: ${nf(s.missing)}`);
  if (s.no_position) parts.push(`без положения: ${nf(s.no_position)}`);
  if (s.outside_domain) parts.push(`вне домена: ${nf(s.outside_domain)}`);
  if (s.no_weight) parts.push(`без веса: ${nf(s.no_weight)}`);
  return parts.join("; ");
}

/** The lines of the journal of a run: what the core wrote (`log`), then the singular systems by the passes of each row, then the merged rows. */
export function journalLines(r: RunResult, rows: readonly Pick<EstimationRow, "id" | "name">[] = []): string[] {
  const name = (id: number, fallback: string): string => rows.find((x) => x.id === id)?.name ?? fallback;
  const out = [...r.log];
  for (const row of r.rows) {
    const sing = singularText(row.counts.singular);
    if (sing) out.push(`«${name(row.estimation_id, `${row.prefix}${row.label ?? ""}`)}»: вырожденная система — ${sing}.`);
  }
  for (const m of r.merged) out.push(`Строки ${m.estimation_ids.map((id) => `«${name(id, `№${id}`)}»`).join(", ")} пишут в поля «${m.prefix}*»: значения доменов объединены.`);
  return out;
}
