/** «Рудные интервалы (ГКЗ)» (Д-26, ТЗ 7.35.4; ADR 0018 §8): кондиционные интервалы по Сб, Мрт, Мпп, МС — a new table
 * «Рудные интервалы» and the fields ORE_ID, ORE_CLS of the samples. Stub of 12.25 (12.30). */
import type { Choice, ParamSpec, ToolSpec } from "../params";

export const ORE_MODES: readonly Choice[] = [
  { value: "strict_excl", label: "строгий, исключить пустую породу" },
  { value: "strict", label: "строгий" },
  { value: "soft", label: "смягчённый" },
  { value: "simple", label: "упрощённый" },
  { value: "contacts", label: "по контактам" },
];

/** Thickness, gaps and mode — shared with «Анализ кондиций». */
export const ORE_COMMON: readonly ParamSpec[] = [
  { key: "mode", label: "Режим", kind: "choice", default: "strict_excl", choices: ORE_MODES },
  { key: "thickness_field", label: "Мощность пробы", kind: "field", numeric: true, optional: true, hint: "пусто — длина по стволу" },
  {
    key: "missing",
    label: "Пропуски",
    kind: "choice",
    default: "break",
    choices: [
      { value: "break", label: "разрывают интервал" },
      { value: "waste", label: "пустая порода до длины" },
    ],
  },
  { key: "missing_max", label: "Наибольший пропуск", kind: "number", unit: "м", min: 0, optional: true, when: (v) => v.missing === "waste" },
];

export const ore: ToolSpec = {
  id: "ore",
  command: "composite.ore_intervals",
  title: "Рудные интервалы (ГКЗ)",
  endpoint: "/ore",
  preview: () => "ore",
  source: { table: "one", fields: "one", domain: true },
  params: [
    { key: "cutoff", label: "Бортовое содержание Сб", kind: "number", min: 0 },
    { key: "m_min", label: "Мин. мощность Мрт", kind: "number", unit: "м", min: 0 },
    { key: "m_waste_max", label: "Макс. мощность прослоя Мпп", kind: "number", unit: "м", min: 0 },
    { key: "mc_min", label: "Мин. метропроцент МС", kind: "number", min: 0, optional: true, hint: "пусто — Сб·Мрт" },
    { key: "c_min", label: "Мин. содержание Смин", kind: "number", min: 0, optional: true, hint: "пусто — выключено (= Сб)" },
    ...ORE_COMMON,
    { key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «Рудные интервалы»" },
  ],
  required: ["set_id", "table", "field", "cutoff", "m_min", "m_waste_max"],
  writes: "table",
  note: "Методика ГКЗ (Д-26): равенство — руда; пропуск не превращается в ноль.",
};
