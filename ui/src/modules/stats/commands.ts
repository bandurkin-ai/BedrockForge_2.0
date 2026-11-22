/**
 * The bottom tab «Статистика» (ТЗ 7.29; ADR 0019 §7, §9; ADR 0022 §11): one command opens it on an analysis. Э13
 * made declustering, normal scores and the bootstrap; Э16 — the variogram, the variogram map and the uncertainty
 * map in plan. `STATS_BUTTONS` — the ribbon group «Анализ → Статистика».
 */
import type { Command } from "../../shell/commands";

export type StatsAnalysis = "declus" | "nscore" | "bootstrap" | "variogram" | "map" | "local";

export const commands: Command[] = [
  {
    id: "stats.open",
    title: "Статистика",
    description:
      "Открыть вкладку «Статистика» нижней панели на анализе: declus — декластеризация (среднее с весами и без, кривая «размер ячейки → среднее»), nscore — нормальные оценки и обратное преобразование, bootstrap — интервалы среднего, медианы, σ, CV, P10, P90, variogram — экспериментальная вариограмма в плане (всенаправленная или 0°, 45°, 90°, 135°) с числом пар в лагах и моделью, map — карта вариограммы по сдвигам ΔX, ΔY, local — карта неопределённости IDW (среднее, σ, P10, P90, вероятность превысить порог). Больше 20 000 интервалов — отказ: скомпозитируйте пробы или откройте окно «Вариография» (variography.open). Результаты не сохраняются.",
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
  { analysis: "variogram", label: "Вариограмма" },
  { analysis: "map", label: "Карта вариограммы" },
  { analysis: "bootstrap", label: "Бутстрап" },
  { analysis: "local", label: "Карта неопределённости" },
];
