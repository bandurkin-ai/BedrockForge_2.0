/** «Анализ кондиций» (Д-26, Т8.2-20; ADR 0018 §12): Сб × Мрт × Мпп (до 200 вариантов) тем же ядром — a result
 * without a table, the variants in its report. Stub of 12.25 (12.31). */
import type { ToolSpec } from "../params";
import { ORE_COMMON } from "./ore";

export const MAX_VARIANTS = 200;

export const conditions: ToolSpec = {
  id: "conditions",
  command: "composite.conditions_analysis",
  title: "Анализ кондиций",
  endpoint: "/conditions",
  preview: null,
  source: { table: "one", fields: "one", domain: true },
  params: [
    { key: "cutoffs", label: "Бортовые содержания Сб", kind: "list", min: 0, hint: "через «;», например «0,5; 1; 1,5»" },
    { key: "m_min", label: "Мин. мощности Мрт", kind: "list", unit: "м", min: 0 },
    { key: "m_waste_max", label: "Макс. мощности прослоя Мпп", kind: "list", unit: "м", min: 0 },
    ...ORE_COMMON,
  ],
  required: ["set_id", "table", "field", "cutoffs", "m_min", "m_waste_max"],
  writes: "report",
  note: `Каждый вариант считается как отдельный запуск «Рудные интервалы (ГКЗ)»; не больше ${MAX_VARIANTS} вариантов.`,
  finish: (body) => {
    const n = [body.cutoffs, body.m_min, body.m_waste_max].reduce<number>((k, l) => k * (Array.isArray(l) ? l.length : 1), 1);
    return n > MAX_VARIANTS ? `Вариантов ${n}: не больше ${MAX_VARIANTS}` : body;
  },
};
