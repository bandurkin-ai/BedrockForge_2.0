/** «Кодировать каркасами» (ТЗ 8.2.2; ADR 0018 §6): the samples of a table coded by closed wireframes — a new table
 * «‹Таблица› — коды»; with the division the samples are cut at the points of entry and exit. Stub of 12.25. */
import type { ToolSpec } from "../params";

export const coding: ToolSpec = {
  id: "coding",
  command: "composite.code",
  title: "Кодировать каркасами",
  endpoint: "/coding",
  preview: () => "coding",
  source: { table: "one", fields: "none", domain: false, wireframes: true },
  params: [
    { key: "split", label: "Делить пробы на границе каркаса", kind: "boolean", default: true },
    { key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «‹Таблица› — коды»" },
  ],
  required: ["set_id", "table", "wireframe_ids"],
  writes: "table",
  note: "Только замкнутые каркасы; при перекрытии тел код берётся по порядку списка.",
};
