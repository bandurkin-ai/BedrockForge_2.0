/** «Декластеризация» (ТЗ 8.2.1; ADR 0018 §7): BF cells and polygons in plan, GSLIB declus in 3D with anisy, anisz;
 * the weights — the field `DECL_W` and a report «размер → среднее». Stub of 12.25. */
import type { ToolSpec } from "../params";

const cell3d = (v: Record<string, unknown>) => v.method === "cell3d";
const cells = (v: Record<string, unknown>) => v.method !== "polygon";

export const declus: ToolSpec = {
  id: "declus",
  command: "composite.declus",
  title: "Декластеризация",
  endpoint: "/declus",
  preview: null,
  source: { table: "one", fields: "one", domain: false },
  params: [
    {
      key: "method",
      label: "Метод",
      kind: "choice",
      default: "cell",
      choices: [
        { value: "cell", label: "ячейки в плане" },
        { value: "polygon", label: "полигоны в плане" },
        { value: "cell3d", label: "ячейки в 3D" },
      ],
    },
    { key: "cmin", label: "Размер ячейки от", kind: "number", unit: "м", min: 0.01, when: cells },
    { key: "cmax", label: "Размер ячейки до", kind: "number", unit: "м", min: 0.01, when: cells },
    { key: "ncell", label: "Число размеров", kind: "integer", min: 1, max: 500, default: 20, when: cells },
    { key: "noff", label: "Сдвигов начала", kind: "integer", min: 1, max: 64, default: 8, when: cells },
    {
      key: "minmax",
      label: "Критерий",
      kind: "choice",
      default: "min",
      when: cells,
      choices: [
        { value: "min", label: "минимум среднего" },
        { value: "max", label: "максимум среднего" },
      ],
    },
    { key: "anisy", label: "Анизотропия Y", kind: "number", min: 0.01, max: 100, default: 1, when: cell3d },
    { key: "anisz", label: "Анизотропия Z", kind: "number", min: 0.01, max: 1e6, default: 1, when: cell3d },
  ],
  required: ["set_id", "table", "field", "method"],
  writes: "fields",
  note: "Веса — поле DECL_W (сумма весов = число проб); нет координат или значения — пусто.",
  finish: (body) => (typeof body.cmin === "number" && typeof body.cmax === "number" && body.cmin > body.cmax ? "«Размер ячейки от» больше «до»" : body),
};
