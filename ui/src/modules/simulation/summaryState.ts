/**
 * The window «Сводка реализаций» (ТЗ 8.8.1; docs/progress/e18-api.md §9): which simulation it shows and the words of its tables. The
 * summary is the one the job kept (`GET /simulations/{id}/summary`): for a continuous variable the nodes, mean, σ, min and max of each
 * realization and of all together; for the categories of SIS the share of every category by realization. A value the core could not
 * give (σ of one node, an empty realization) stays a dash, never 0. No React.
 */
import { Store } from "../../shell/store";
import type { Categories, SimCounts, Summary } from "./api";

export const SUMMARY_WINDOW = "simulation-summary";
export const SUMMARY_TITLE = "Сводка реализаций";

export interface SummaryState {
  simId: number | null;
}

export const summaryStore = new Store<SummaryState>({ simId: null });
export const chooseSummary = (simId: number | null): void => summaryStore.set({ simId });

const nf = (n: number): string => n.toLocaleString("ru-RU");
export const num = (x: number | null, digits = 4): string => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: digits }));
export const pct = (x: number | null): string => (x === null ? "—" : `${(x * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} %`);

export interface TableView {
  head: string[];
  rows: { key: string; cells: string[]; total?: boolean }[];
}

/** The table of the realizations: continuous — n, mean, σ, min, max; categories — n and the share of every category (the labels of the stored row). */
export function summaryTable(s: Summary, cats: Categories | null): TableView {
  if (s.perShares && s.codes) {
    const names = s.codes.map((c) => cats?.labels[cats.codes.indexOf(c)] ?? String(c));
    return {
      head: ["Реализация", "Узлов", ...names],
      rows: [...s.perShares.map((p, i) => ({ key: `r${i + 1}`, cells: [String(i + 1), nf(p.n), ...p.shares.map(pct)] })), { key: "all", total: true, cells: ["Все вместе", "", ...(s.shares ?? []).map(pct)] }],
    };
  }
  const per = s.per ?? [];
  return {
    head: ["Реализация", "Узлов", "Среднее", "σ", "Минимум", "Максимум"],
    rows: [
      ...per.map((p, i) => ({ key: `r${i + 1}`, cells: [String(i + 1), nf(p.n), num(p.mean), num(p.std), num(p.min), num(p.max)] })),
      ...(s.all ? [{ key: "all", total: true, cells: ["Все вместе", "", num(s.all.mean), num(s.all.std), num(s.all.min), num(s.all.max)] }] : []),
    ],
  };
}

/** The facts under the table: the nodes, the conditioning samples of a node, the singular systems, the samples used. */
export function summaryFacts(s: Summary): [string, string][] {
  const c: SimCounts = s.counts;
  const out: [string, string][] = [
    ["Узлов (родителей)", nf(s.nodes)],
    ["Реализаций", nf(s.realizations)],
    ["Время расчёта, с", num(s.seconds, 3)],
  ];
  if (s.samples) out.push(["Проб в расчёте", `${nf(s.samples.used)} из ${nf(s.samples.rows)}`]);
  if (s.cond) out.push(["Условных данных в узле", `${num(s.cond.min)} · ${num(s.cond.mean)} · ${num(s.cond.max)} (мин · среднее · макс)`]);
  out.push(["Вырожденных систем", nf(c.degenerate)], ["Узлов без проб в окрестности", nf(c.free)], ["Узлов на пробах", nf(c.on_sample)]);
  if (c.gap !== null) out.push(["Узлов без вторичной переменной", nf(c.gap)]);
  return out;
}
