/** «Объединить таблицы» (ТЗ 8.2.2; ADR 0018 §6): all FROM/TO of a hole over the chosen tables, each sub-interval
 * with the values of the covering interval of every table — a new table «Объединение: …». Stub of 12.25. */
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
