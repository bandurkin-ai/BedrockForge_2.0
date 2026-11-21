/** «Объединить таблицы» (ТЗ 8.2.2; ADR 0018 §6): all FROM/TO of a hole over the chosen tables, each sub-interval
 * with the values of the covering interval of every table — a new table «Объединение: …»; no cover — empty (not
 * zero); a field name met in two tables gets the name of its table. Pure. */
import type { ToolSpec } from "../params";

export const merge: ToolSpec = {
  id: "merge",
  command: "composite.merge",
  title: "Объединить таблицы",
  endpoint: "/merge",
  preview: () => "merge",
  source: { table: "many", fields: "none", domain: false },
  params: [{ key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «Объединение: …»" }],
  required: ["set_id", "tables"],
  writes: "table",
  note: "Нет покрытия интервалом таблицы — пусто (не ноль). Совпадающие имена полей получают имя таблицы.",
};

/** «Объединение: Пробы, Литология» (ADR 0018 §2). */
export const mergeName = (names: readonly string[]): string => `Объединение: ${names.length ? names.join(", ") : "…"}`;

export interface MergedField {
  table: string;
  field: string;
  /** the name is met in another chosen table: the core adds the name of the table */
  clash: boolean;
}

/** The fields of the result in the order of the tables; a name met in two tables is marked (case does not matter). */
export function mergedFields(tables: readonly { name: string; fields: readonly string[] }[]): MergedField[] {
  const count = new Map<string, number>();
  for (const t of tables) for (const f of new Set(t.fields.map((x) => x.toLowerCase()))) count.set(f, (count.get(f) ?? 0) + 1);
  return tables.flatMap((t) => t.fields.map((f) => ({ table: t.name, field: f, clash: (count.get(f.toLowerCase()) ?? 0) > 1 })));
}
