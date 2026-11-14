export type StatsAnalysis = "declus" | "nscore" | "variogram" | "map";

export const ANALYSES: [StatsAnalysis, string][] = [
  ["declus", "Декластеризация"],
  ["nscore", "Нормальные оценки"],
  ["variogram", "Вариограмма"],
  ["map", "Карта вариограммы"],
];

/** What the statistics tab shows. */
export interface StatsView {
  analysis: StatsAnalysis;
  /** null = the first component of the set */
  component: string | null;
}
