/** «Координаты проб» (ТЗ 8.2.1; ADR 0018 §6): a copy of the table with XB, YB, ZB, XM, YM, ZM, XE, YE, ZE by the
 * trajectories — a new table «‹Таблица› — координаты». Stub of 12.25. */
import type { ToolSpec } from "../params";

export const coords: ToolSpec = {
  id: "coords",
  command: "composite.coords",
  title: "Координаты проб",
  endpoint: "/coords",
  preview: () => "coords",
  source: { table: "one", fields: "none", domain: false },
  params: [{ key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «‹Таблица› — координаты»" }],
  required: ["set_id", "table"],
  writes: "table",
  note: "Начало, середина и конец каждой пробы по траектории; скважина без траектории — пусто и запись в журнале.",
};
