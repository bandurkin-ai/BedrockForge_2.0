export type StatsAnalysis = "declus" | "nscore" | "variogram" | "map" | "bootstrap" | "local";

export const ANALYSES: [StatsAnalysis, string][] = [
  ["declus", "Декластеризация"],
  ["nscore", "Нормальные оценки"],
  ["variogram", "Вариограмма"],
  ["map", "Карта вариограммы"],
  ["bootstrap", "Бутстрап"],
  ["local", "Карта неопределённости"],
];

/** What the statistics tab shows. */
export interface StatsView {
  analysis: StatsAnalysis;
  /** null = the first component of the table */
  component: string | null;
  /** the set's sample table ("assay" or an assay table's id); null: the default one */
  table?: string | null;
}
