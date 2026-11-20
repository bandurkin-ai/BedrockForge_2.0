/**
 * «Композиты» (ТЗ 8.2.2, 8.2.3; ADR 0018 §6): by length, by domains, by benches — the method is a tab of the window.
 * The parameters of 8.2.2 with their ranges, the name of the new table («Композиты 2 м», «Композиты по доменам»,
 * «Композиты по уступам 10 м»), the summary «до/после» as rows of the window. Pure.
 */
import type { Summary } from "../api";
import { numberText, type Choice, type FormValues, type ToolSpec } from "../params";

export type Method = "length" | "domain" | "bench";

export const METHODS: readonly { value: Method; label: string }[] = [
  { value: "length", label: "По длине" },
  { value: "domain", label: "По доменам" },
  { value: "bench", label: "По уступам" },
];

export const methodOf = (v: FormValues): Method => (v.method === "domain" || v.method === "bench" ? v.method : "length");
const byLength = (v: FormValues) => methodOf(v) !== "bench";
const byBench = (v: FormValues) => methodOf(v) === "bench";

export const REMAINDERS: readonly Choice[] = [
  { value: "attach", label: "остаток — присоединить к последнему" },
  { value: "drop", label: "остаток — отбросить" },
  { value: "equal", label: "равные части" },
];

export const ORIGINS: readonly Choice[] = [
  { value: "domain", label: "от начала участка" },
  { value: "collar", label: "от устья" },
  { value: "first", label: "от кровли первой пробы" },
];

export const composite: ToolSpec = {
  id: "composite",
  command: "composite.run",
  title: "Композиты",
  endpoint: "/run",
  preview: (v) => methodOf(v),
  source: { table: "one", fields: "many", domain: true },
  params: [
    { key: "method", label: "Метод", kind: "choice", default: "length", choices: METHODS },
    { key: "length", label: "Длина L", kind: "number", unit: "м", min: 0.1, max: 100, default: 2, when: byLength },
    { key: "remainder", label: "Правило остатка", kind: "choice", default: "attach", choices: REMAINDERS, when: byLength },
    { key: "f_min", label: "Доля остатка f_min", kind: "number", min: 0, max: 1, default: 0.5, when: (v) => byLength(v) && v.remainder !== "equal", hint: "остаток ≥ f_min·L — отдельный композит" },
    { key: "origin", label: "Отсчёт", kind: "choice", default: "domain", choices: ORIGINS, when: byLength },
    { key: "bench", label: "Высота уступа H", kind: "number", unit: "м", min: 1, max: 50, default: 10, when: byBench },
    { key: "bench_top", label: "Отметка верха Z", kind: "number", unit: "м", optional: true, when: byBench, hint: "пусто — максимум Z устьев, округлённый вверх до H" },
    { key: "p_min", label: "Мин. доля опробованной длины p_min", kind: "number", min: 0, max: 1, default: 0.5 },
    { key: "density", label: "Плотность (вес)", kind: "field", numeric: true, optional: true, hint: "пусто — без взвешивания по плотности" },
    { key: "unsampled", label: "Непробованные участки", kind: "number", optional: true, hint: "пусто — нет значения (не ноль); число — это значение" },
    { key: "name", label: "Имя таблицы", kind: "text", optional: true },
  ],
  required: ["set_id", "table", "method"],
  writes: "table",
  note: "Композит не пересекает смену домена; пропуск не становится нулём.",
  finish: (body) => (body.method === "domain" && !body.domain ? "Метод «По доменам»: выберите домен — поле или каркасы" : body),
};

/** The name the core gives the new table (ADR 0018 §2) — shown as the placeholder of «Имя таблицы». */
export function defaultName(v: FormValues): string {
  const m = methodOf(v);
  if (m === "domain") return "Композиты по доменам";
  const key = m === "bench" ? "bench" : "length";
  const raw = typeof v[key] === "string" ? (v[key] as string).trim() : "";
  const n = Number(raw.replace(",", "."));
  const size = raw && Number.isFinite(n) ? `${numberText(n)} м` : "";
  return m === "bench" ? `Композиты по уступам${size ? ` ${size}` : ""}` : `Композиты${size ? ` ${size}` : ""}`;
}

export interface SummaryRow {
  label: string;
  before: string;
  after: string;
}

const fmt = (v: number | null | undefined, digits = 3): string => (v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits }));

/** «До/после» as rows (ТЗ 8.2.3): число, общая длина, then the mean and the metal Σ l·g of every field. */
export function summaryRows(s: { before: Summary; after: Summary } | null): SummaryRow[] {
  if (!s) return [];
  const rows: SummaryRow[] = [
    { label: "Число интервалов", before: fmt(s.before.count, 0), after: fmt(s.after.count, 0) },
    { label: "Общая длина, м", before: fmt(s.before.length, 2), after: fmt(s.after.length, 2) },
  ];
  const names = [...new Set([...Object.keys(s.before.fields ?? {}), ...Object.keys(s.after.fields ?? {})])];
  for (const f of names) {
    const b = s.before.fields?.[f];
    const a = s.after.fields?.[f];
    rows.push({ label: `${f}: среднее`, before: fmt(b?.mean), after: fmt(a?.mean) });
    rows.push({ label: `${f}: металл Σ l·g`, before: fmt(b?.metal), after: fmt(a?.metal) });
  }
  return rows;
}

/** The difference of the metal before and after, in percent of «до» (a check of 8.2.4 Т8.2-4); null — not counted. */
export function metalChange(s: { before: Summary; after: Summary } | null, field: string): number | null {
  const b = s?.before.fields?.[field]?.metal;
  const a = s?.after.fields?.[field]?.metal;
  if (b === null || b === undefined || a === null || a === undefined || b === 0) return null;
  return (100 * (a - b)) / b;
}
