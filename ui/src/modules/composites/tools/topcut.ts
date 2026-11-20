/** «Урезка» (ТЗ 8.2.2; ADR 0018 §7): `<F>_CUT = min(g, T)` and `<F>_CUT_F` in the source table, T — a value or the
 * percentile P weighted by length, by domain. The threshold is never applied by itself. Stub of 12.25 (12.28). */
import type { FormValues, ToolSpec } from "../params";

const by = (v: FormValues) => v.by;

export const topcut: ToolSpec = {
  id: "topcut",
  command: "composite.topcut",
  title: "Урезка",
  endpoint: "/topcut",
  preview: () => "topcut",
  source: { table: "one", fields: "one", domain: true },
  params: [
    {
      key: "by",
      label: "Порог",
      kind: "choice",
      default: "percentile",
      formOnly: true,
      choices: [
        { value: "percentile", label: "перцентиль" },
        { value: "value", label: "значение" },
        { value: "thresholds", label: "по доменам" },
      ],
    },
    { key: "percentile", label: "Перцентиль P", kind: "number", unit: "%", min: 90, max: 99.99, default: 99, when: (v) => by(v) === "percentile" },
    { key: "value", label: "Значение T", kind: "number", min: 0, when: (v) => by(v) === "value" },
    { key: "thresholds", label: "Пороги по доменам", kind: "text", when: (v) => by(v) === "thresholds", hint: "КОД=значение; …" },
  ],
  fromArgs: (args, values) => ({ ...values, by: args.value !== undefined ? "value" : args.thresholds !== undefined ? "thresholds" : "percentile" }),
  required: ["set_id", "table", "field"],
  writes: "fields",
  note: "Поля ‹F›_CUT и ‹F›_CUT_F пишутся в таблицу источника; пусто остаётся пустым.",
};
