import type { Command } from "../../shell/commands";

/** Charts and analysis (Э6, Э13, Э17). */
export const commands: Command[] = [
  { id: "chart.histogram", title: "Гистограмма", description: "Гистограмма проб активного набора.", params: { field: { type: "string" } }, stage: "Э13" },
  { id: "chart.qq", title: "Q-Q график", description: "Квантили проб против распределения или другой выборки.", params: { field: { type: "string" } }, stage: "Э13" },
  { id: "chart.dendrogram", title: "Дендрограмма", description: "Кластеры полей проб по корреляции.", stage: "Э13" },
  {
    id: "chart.striplog",
    title: "Данные по скважинам (текст)",
    description: "Значения полей таблицы набора по глубинам для скважин рядом (до 30) — текст CSV «Скважина;От;До;поля». Без полей — числовые поля таблицы.",
    params: { holes: { type: "string", description: "Номера скважин через запятую (без — выбранная скважина)" }, table: { type: "string", description: "Название таблицы (без — «Пробы»)" }, fields: { type: "string", description: "Поля через запятую" } },
    stage: "Э6",
  },
  { id: "chart.open", title: "Открыть график", description: "Открыть сохранённый график.", params: { chart_id: { type: "integer" } }, required: ["chart_id"], stage: "Э13" },
  { id: "blocks.report", title: "Отчёт по блочной модели", description: "Тоннаж и содержания по бортам.", params: { model_id: { type: "integer" }, grades: { type: "string" } }, required: ["model_id"], stage: "Э17" },
  { id: "stats.swath", title: "SWATH PLOT", description: "Средние содержания по полосам; сохраняется в разделе «Аудит».", stage: "Э13" },
];
