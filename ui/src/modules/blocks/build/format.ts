/**
 * The words of the building of a block model (ТЗ 8.7.3, 8.7.5): numbers as the windows write them (a space between the
 * thousands, a decimal comma), the size on the disk, the lines of the preview of the prototype, the stage of a job, the
 * totals before / after a regularization (a gap is a gap: no tonnes without a density, never 0), and the texts the
 * log and Claude read after a run. No React.
 */
import type { FillResult, Preview, PrototypeResult, RegularizeResult, Totals } from "./api";
import { MAX_PARENTS } from "./api";

const NBSP = " ";

/** 24000 → «24 000» (a plain space between the thousands, so that a copied text stays plain). */
export function int(n: number): string {
  return Math.round(n)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** 12345.678 → «12 345,68»; `digits` after the comma, none trailing. */
export function dec(n: number, digits = 2): string {
  const [whole, frac = ""] = Math.abs(n).toFixed(digits).split(".");
  const tail = frac.replace(/0+$/, "");
  return `${n < 0 ? "−" : ""}${int(Number(whole))}${tail ? `,${tail}` : ""}`;
}

export const volumeText = (m3: number): string => `${dec(m3, 1)}${NBSP}м³`;

export const tonnesText = (t: number | null): string => (t === null ? "нет значения" : `${dec(t, 1)}${NBSP}т`);

/** The bytes of a model on the disk, an estimate: «≈ 43 МБ». */
export function diskText(bytes: number): string {
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let v = bytes;
  let k = 0;
  while (v >= 1024 && k < units.length - 1) {
    v /= 1024;
    k++;
  }
  return `≈${NBSP}${k === 0 ? int(v) : dec(v, v < 10 ? 1 : 0)}${NBSP}${units[k]}`;
}

/** «7 × 7 × 6» */
export const countsText = (c: readonly number[]): string => c.map(int).join(" × ");

/** The grade of the limit: how near the number of parents is to 2·10⁹ (the share). */
export const limitShare = (parents: number): number => parents / MAX_PARENTS;

/** The lines under the frame of the prototype (nx · ny · nz, the parents, the disk, the size of the block). */
export function previewLines(p: Preview): { label: string; value: string }[] {
  const g = p.grid;
  return [
    { label: "Блоков по осям nx · ny · nz", value: `${countsText(g.counts)}` },
    { label: "Родителей", value: `${int(p.parents)} из ${int(MAX_PARENTS)}` },
    { label: "Размер блока, м", value: g.size.map((v) => dec(v, 3)).join(" × ") },
    { label: "Субблоков на родителя", value: g.div.map(int).join(" × ") },
    { label: "Поворот вокруг Z", value: `${dec(g.rotation, 3)}°` },
    { label: "На диске", value: diskText(p.disk) },
  ];
}

/** The stage of a job from its `message` among the stages of the operation (-1 — none yet / not a stage). */
export const stageIndex = (stages: readonly string[], message: string | null | undefined): number => (message ? stages.indexOf(message) : -1);

// ---- the totals of the regularization ----

const REL = 1e-9;
export const differs = (a: number | null, b: number | null): boolean => a !== null && b !== null && Math.abs(a - b) > REL * Math.max(Math.abs(a), Math.abs(b), 1);

export interface TotalRow {
  label: string;
  before: string;
  after: string;
  /** the totals differ by more than 1e-9 of their size (a gap in the data is not a zero: the metal can differ) */
  differs: boolean;
}

/** The rows of the table «до / после»: the volume, the tonnes (when a density was given), the metal of every grade. */
export function totalRows(r: RegularizeResult): TotalRow[] {
  const { before, after } = r.totals;
  const rows: TotalRow[] = [{ label: "Объём", before: volumeText(before.volume), after: volumeText(after.volume), differs: differs(before.volume, after.volume) }];
  if (before.tonnes !== null || after.tonnes !== null) rows.push({ label: "Тоннаж", before: tonnesText(before.tonnes), after: tonnesText(after.tonnes), differs: differs(before.tonnes, after.tonnes) });
  const names = [...new Set([...Object.keys(before.metal), ...Object.keys(after.metal)])];
  const basis = r.totals.metal_basis === "tonnes" ? "по тоннажу" : "по объёму";
  for (const n of names) {
    const [b, a] = [before.metal[n] ?? null, after.metal[n] ?? null];
    rows.push({ label: `Металл ${n} (${basis})`, before: b === null ? "нет значения" : dec(b, 1), after: a === null ? "нет значения" : dec(a, 1), differs: differs(b, a) });
  }
  return rows;
}

export const anyDiffers = (r: RegularizeResult): boolean => totalRows(r).some((x) => x.differs);

// ---- the texts of the log ----

/** «Руда — 24 000 м³ (24 блока)»; the code and the volume of a fill / coding. */
export function codeLines(r: FillResult): string[] {
  return Object.keys(r.codes).map((c) => `${c} — ${volumeText(r.volume[c] ?? 0)} (${int(r.codes[c])} ${plural(r.codes[c], "блок", "блока", "блоков")})`);
}

export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  return m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
}

export function prototypeSummary(r: PrototypeResult): string {
  return `Прототип «${r.name}» создан (№${r.model_id}): ${countsText(r.grid.counts)} = ${int(r.parents)} родителей, размер блока ${r.grid.size.map((v) => dec(v, 3)).join(" × ")} м.`;
}

export function fillSummary(r: FillResult, verb: "fill" | "code"): string {
  const head = verb === "fill" ? `Заполнение: модель «${r.name}» (№${r.model_id})` : r.fields.length ? `Кодирование по центру: модель «${r.name}» (№${r.model_id}), версия ${r.version ?? "?"}, поля ${r.fields.join(", ")}` : `Кодирование с дроблением: модель «${r.name}» (№${r.model_id})`;
  const parts = [`${head}: ${int(r.rows)} ${plural(r.rows, "строка", "строки", "строк")}.`, ...codeLines(r)];
  if (r.dropped.length) parts.push(`Удалены поля других операций: ${r.dropped.join(", ")}.`);
  if (r.warnings.length) parts.push(`Предупреждения: ${r.warnings.join(" ")}`);
  return parts.join(" ");
}

export function regularizeSummary(r: RegularizeResult): string {
  const t = (x: Totals) => `${volumeText(x.volume)}${x.tonnes === null ? "" : `, ${tonnesText(x.tonnes)}`}`;
  const parts = [`Регуляризация: модель «${r.name}» (№${r.model_id}), ${int(r.rows)} блоков.`, `До: ${t(r.totals.before)}. После: ${t(r.totals.after)}.`];
  if (anyDiffers(r)) parts.push("Итоги до и после расходятся: пропуски в данных не считаются нулями.");
  if (r.warnings.length) parts.push(`Предупреждения: ${r.warnings.join(" ")}`);
  return parts.join(" ");
}

/** The question before «Перестроить»: what goes away and why (the job lists the real names in `dropped`). */
export function rebuildQuestion(name: string, dropped: readonly string[], stale: string | null): string {
  const lines = [`Модель «${name}» будет построена заново по тем же каркасам и настройкам: строки и поля операции перепишутся.`];
  if (stale) lines.push(`Причина: ${stale}`);
  if (dropped.length) lines.push(`Поля других операций считались по старым строкам и будут удалены: ${dropped.join(", ")}. Их можно построить заново калькулятором или «Перестроить» поле.`);
  else lines.push("Поля других операций у модели не найдены: ничего не удалится.");
  lines.push("Перестроить модель?");
  return lines.join("\n\n");
}
