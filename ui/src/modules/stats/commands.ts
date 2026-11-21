/**
 * The bottom tab «Статистика» (ТЗ 7.29; ADR 0019 §7, §9): one command opens it on an analysis. Э13 makes
 * declustering, normal scores and the bootstrap; variograms and maps come on Э16 (their buttons stay
 * unavailable with that stage). `STATS_BUTTONS` — the ribbon group «Анализ → Статистика».
 */
import type { Command } from "../../shell/commands";

export type StatsAnalysis = "declus" | "nscore" | "bootstrap" | "variogram" | "map" | "local";

export const commands: Command[] = [
  {
    id: "stats.open",
    title: "Статистика",
    description:
      "Открыть вкладку «Статистика» нижней панели на анализе: declus — декластеризация (среднее с весами и без, кривая «размер ячейки → среднее»), nscore — нормальные оценки и обратное преобразование, bootstrap — интервалы среднего, медианы, σ, CV, P10, P90. variogram, map, local — на этапе Э16. Больше 20 000 интервалов — отказ: скомпозитируйте пробы. Результаты не сохраняются.",
    params: {
      analysis: { type: "string", enum: ["declus", "nscore", "bootstrap", "variogram", "map", "local"], description: "анализ вкладки" },
      component: { type: "string", description: "компонент (без — первый)" },
      set_id: { type: "integer", description: "номер набора скважин (без — активный)" },
      table: { type: "string", description: "интервальная таблица набора: имя или номер (без — таблица проб)" },
    },
    required: ["analysis"],
    stage: "Э13",
  },
];

export interface StatsButton {
  analysis: StatsAnalysis;
  label: string;
  /** The analysis comes on this stage: the button is unavailable with it (ADR 0019 §7). */
  stage?: string;
}

/** The ribbon group «Анализ → Статистика» (ТЗ 7.0.4, 7.29) without SWATH PLOT (`modules/swath/commands.ts`). */
export const STATS_BUTTONS: readonly StatsButton[] = [
  { analysis: "declus", label: "Декластеризация" },
  { analysis: "nscore", label: "Нормальные оценки" },
  { analysis: "variogram", label: "Вариограмма", stage: "Э16" },
  { analysis: "map", label: "Карта вариограммы", stage: "Э16" },
  { analysis: "bootstrap", label: "Бутстрап" },
  { analysis: "local", label: "Карта неопределённости", stage: "Э16" },
];
