/**
 * The forms of the tab «Статистика» (ТЗ 7.29; ADR 0019 §7; перенос `StatsPage.tsx` BF): the defaults, the bodies
 * of `POST /stats/tab/*` from the texts of the fields (a decimal comma is accepted; empty — the default of the
 * core), the facts of the answers, their CSV and the short texts for the assistant. A missing number is «—»,
 * never 0. Pure.
 */
import { parseNumber } from "../../charts/core/axisScale";
import { csvText } from "../../charts/core/csv";
import { defaultField, type StatsFields } from "../charts/api";
import { STATS_BUTTONS, type StatsAnalysis } from "./commands";
import type { BacktrBody, BootstrapAnswer, BootStatistic, DeclusAnswer, DeclusMethod, DeclusParams, LowerTail, NscoreAnswer, TabBody, TabCounts, UpperTail } from "./api";

/** The analyses of the tab: declus, nscore, bootstrap (Э13); variogram, map, local (Э16, `formsVario.ts`). */
export type TabAnalysis = StatsAnalysis;

export const isTabAnalysis = (a: unknown): a is TabAnalysis => STATS_BUTTONS.some((b) => b.analysis === a);

/** The title of an analysis (the ribbon's label). */
export const analysisTitle = (a: StatsAnalysis): string => STATS_BUTTONS.find((b) => b.analysis === a)?.label ?? a;

/** Why an analysis cannot be computed now: its stage (none since Э16); null — it can. */
export function analysisStage(a: StatsAnalysis): string | null {
  const stage = STATS_BUTTONS.find((b) => b.analysis === a)?.stage;
  return stage ? `Появится на этапе ${stage}` : null;
}

// ---- the forms ----

export interface DeclusForm {
  method: DeclusMethod;
  cmin: string;
  cmax: string;
  ncell: string;
  noff: string;
  minmax: "min" | "max";
  nx: string;
  ny: string;
  /** X from, X to, Y from, Y to; all empty — the extent of the points */
  bounds: [string, string, string, string];
}

export interface NscoreForm {
  weighted: boolean;
  declus: DeclusForm;
  zmin: string;
  zmax: string;
}

export type TailChoice = "linear" | "power" | "hyperbolic" | "none";

export interface BacktrForm {
  /** normal scores separated by «;» or spaces */
  scores: string;
  ltail: Exclude<TailChoice, "hyperbolic">;
  utail: TailChoice;
  ltpar: string;
  utpar: string;
}

export interface BootForm {
  nBoot: string;
  confidence: string;
  /** empty — a new one (the answer tells it) */
  seed: string;
  weighted: boolean;
  declus: DeclusForm;
  /** the statistic whose realizations the histogram shows */
  statistic: BootStatistic;
}

export interface StatsForms {
  declus: DeclusForm;
  nscore: NscoreForm;
  backtr: BacktrForm;
  bootstrap: BootForm;
}

/** As BF and Э12: cells 2–50 % of the extent (empty — the core's), 20 sizes, 8 offsets, the least mean. */
export const DEFAULT_DECLUS: DeclusForm = { method: "cell", cmin: "", cmax: "", ncell: "20", noff: "8", minmax: "min", nx: "", ny: "", bounds: ["", "", "", ""] };

export const DEFAULT_FORMS: StatsForms = {
  declus: DEFAULT_DECLUS,
  nscore: { weighted: false, declus: DEFAULT_DECLUS, zmin: "", zmax: "" },
  backtr: { scores: "-2; -1; 0; 1; 2", ltail: "linear", utail: "linear", ltpar: "2", utpar: "2" },
  // the interval 2,5–97,5 % (ADR 0019 §4)
  bootstrap: { nBoot: "1000", confidence: "0.95", seed: "", weighted: false, declus: DEFAULT_DECLUS, statistic: "mean" },
};

export const CONFIDENCES: readonly [string, string][] = [
  ["0.8", "80 %"],
  ["0.9", "90 %"],
  ["0.95", "95 %"],
  ["0.99", "99 %"],
];

export const STATISTICS: readonly [BootStatistic, string][] = [
  ["mean", "Среднее"],
  ["median", "Медиана"],
  ["std", "σ"],
  ["cv", "CV"],
  ["p10", "P10"],
  ["p90", "P90"],
];

export const statisticTitle = (s: BootStatistic): string => STATISTICS.find(([id]) => id === s)?.[1] ?? s;

// ---- the context: set, table, component ----

export interface TabContext {
  setId: number;
  /** `name_key` of the table; null — the set has none */
  table: string | null;
  field: string | null;
}

/** The table and the component the tab means: the chosen ones, else the first table and its first component. */
export function pickContext(setId: number, fields: StatsFields, component: string | null): TabContext {
  return { setId, table: fields.table, field: component ?? defaultField(fields) };
}

/** The fields offered as the component: the components first, then the other numbers (not the length). */
export function componentChoices(fields: Pick<StatsFields, "numeric">): { name: string; label: string; component: boolean }[] {
  const nums = fields.numeric.filter((f) => f.kind !== "length");
  return [...nums.filter((f) => f.kind === "component"), ...nums.filter((f) => f.kind !== "component")].map((f) => ({ name: f.name, label: f.unit ? `${f.name}, ${f.unit}` : f.name, component: f.kind === "component" }));
}

// ---- the bodies ----

/** A field that is not right: its text for the form. */
export class Bad extends Error {}

export type Built<T> = { body: T; reason?: undefined } | { body?: undefined; reason: string };

export function build<T>(make: () => T): Built<T> {
  try {
    return { body: make() };
  } catch (e) {
    if (e instanceof Bad) return { reason: e.message };
    throw e;
  }
}

/** A number of a field: empty — null; not a number, a fraction where a whole one is asked, out of range — Bad. */
export function num(text: string, label: string, opt: { whole?: boolean; min?: number; max?: number } = {}): number | null {
  if (text.trim() === "") return null;
  const v = parseNumber(text);
  if (v === null) throw new Bad(`«${label}» — не число`);
  if (opt.whole && !Number.isInteger(v)) throw new Bad(`«${label}» — целое число`);
  if (opt.min !== undefined && v < opt.min) throw new Bad(`«${label}» — не меньше ${String(opt.min).replace(".", ",")}`);
  if (opt.max !== undefined && v > opt.max) throw new Bad(`«${label}» — не больше ${String(opt.max).replace(".", ",")}`);
  return v;
}

/** The parameters of declustering of a form (the limits of `composite.declus`, Э12). */
export function declusParams(f: DeclusForm): DeclusParams {
  if (f.method === "polygon") {
    const nx = num(f.nx, "Сетка по X", { whole: true, min: 1, max: 20000 });
    const ny = num(f.ny, "Сетка по Y", { whole: true, min: 1, max: 20000 });
    const labels = ["X от", "X до", "Y от", "Y до"];
    const b = f.bounds.map((t, i) => num(t, labels[i]));
    let bounds: [number, number, number, number] | null = null;
    if (b.some((v) => v !== null)) {
      if (b.some((v) => v === null)) throw new Bad("Границы полигонов: задайте все четыре числа или ни одного");
      const [x0, x1, y0, y1] = b as number[];
      if (!(x1 > x0 && y1 > y0)) throw new Bad("Границы полигонов: «от» меньше «до»");
      bounds = [x0, x1, y0, y1];
    }
    return { method: "polygon", nx, ny, bounds };
  }
  const cmin = num(f.cmin, "Ячейка от", { min: 0.01 });
  const cmax = num(f.cmax, "Ячейка до", { min: 0.01 });
  if (cmin !== null && cmax !== null && cmin > cmax) throw new Bad("«Ячейка от» больше «до»");
  return {
    method: "cell",
    cmin,
    cmax,
    ncell: num(f.ncell, "Размеров", { whole: true, min: 1, max: 500 }),
    noff: num(f.noff, "Сдвигов", { whole: true, min: 1, max: 64 }),
    minmax: f.minmax,
  };
}

const common = (c: TabContext, declus: DeclusParams | null): TabBody => ({ set_id: c.setId, table: c.table, field: c.field, declus });

export const declusBody = (c: TabContext, f: DeclusForm): Built<TabBody> => build(() => common(c, declusParams(f)));

function nscoreParts(c: TabContext, f: NscoreForm) {
  const zmin = num(f.zmin, "Хвост снизу до");
  const zmax = num(f.zmax, "Хвост сверху до");
  if (zmin !== null && zmax !== null && zmin > zmax) throw new Bad("«Хвост снизу до» больше «сверху до»");
  return { ...common(c, f.weighted ? declusParams(f.declus) : null), zmin, zmax };
}

export const nscoreBody = (c: TabContext, f: NscoreForm): Built<TabBody & { zmin: number | null; zmax: number | null }> => build(() => nscoreParts(c, f));

/** At most this many scores in one back-transform (the core's limit, as BF). */
export const MAX_SCORES = 20_000;

/** The scores of the field: numbers separated by «;» or spaces (a decimal comma is a part of a number). */
export function parseScores(text: string): number[] {
  const parts = text.split(/[;\s]+/).filter((p) => p !== "");
  if (!parts.length) throw new Bad("Введите хотя бы одну нормальную оценку");
  if (parts.length > MAX_SCORES) throw new Bad(`Оценок больше ${MAX_SCORES.toLocaleString("ru-RU")}`);
  return parts.map((p) => {
    const v = parseNumber(p);
    if (v === null) throw new Bad(`«${p.slice(0, 20)}» — не число`);
    return v;
  });
}

const tail = <T extends TailChoice>(t: T): Exclude<T, "none"> | null => (t === "none" ? null : (t as Exclude<T, "none">));

/** The back-transform by the table of `nscore` (its weights and tails). */
export const backtrBody = (c: TabContext, n: NscoreForm, f: BacktrForm): Built<BacktrBody> =>
  build(() => {
    const ltail = tail(f.ltail) as LowerTail;
    const utail = tail(f.utail) as UpperTail;
    return {
      ...nscoreParts(c, n),
      scores: parseScores(f.scores),
      ltail,
      utail,
      ltpar: ltail === "power" ? num(f.ltpar, "Показатель снизу") : null,
      utpar: utail === "power" || utail === "hyperbolic" ? num(f.utpar, "Показатель сверху") : null,
    };
  });

export const bootstrapBody = (c: TabContext, f: BootForm): Built<TabBody & { n_boot: number | null; seed: number | null; confidence: number | null }> =>
  build(() => ({
    ...common(c, f.weighted ? declusParams(f.declus) : null),
    n_boot: num(f.nBoot, "Реализаций", { whole: true, min: 10, max: 5000 }),
    seed: num(f.seed, "Зерно", { whole: true, min: 0 }),
    confidence: num(f.confidence, "Доверие"),
  }));

// ---- the facts ----

/** «12,35», «0,00012», «1,2·10⁶»; a missing number — «—». */
export function fmt(v: number | null | undefined, digits = 5): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (v !== 0 && (a >= 1e9 || a < 1e-4)) {
    const e = Math.floor(Math.log10(a));
    const sup = String(e).replace(/[-0-9]/g, (ch) => "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"["-0123456789".indexOf(ch)]);
    return `${Number((v / 10 ** e).toPrecision(3)).toLocaleString("ru-RU")}·10${sup}`;
  }
  return Number(v.toPrecision(digits)).toLocaleString("ru-RU", { maximumFractionDigits: 10 });
}

export const int = (v: number): string => v.toLocaleString("ru-RU");

export type Fact = [string, string];

/** The intervals of the table: what went in and what was left out (never silently). */
export function countsText(c: TabCounts): string {
  return `Интервалов в таблице: ${int(c.rows)}; без значения — ${int(c.missing)}, без положения на скважине — ${int(c.no_position)}; в расчёте — ${int(c.used)}.`;
}

/** The relative change of the mean by declustering, %; not defined — null. */
export function meanChange(r: Pick<DeclusAnswer, "naive_mean" | "declustered_mean">): number | null {
  const a = r.naive_mean;
  const b = r.declustered_mean;
  return a === null || b === null || a === 0 ? null : ((b - a) / Math.abs(a)) * 100;
}

export function declusFacts(r: DeclusAnswer): Fact[] {
  const change = meanChange(r);
  const out: Fact[] = [
    ["Метод", r.method === "polygon" ? "полигоны в плане" : "ячейки в плане"],
    ["Интервалов", int(r.count)],
    ["Среднее без весов", fmt(r.naive_mean)],
    ["Среднее с весами", fmt(r.declustered_mean)],
    ["Изменение", change === null ? "—" : `${change >= 0 ? "+" : "−"}${fmt(Math.abs(change), 3)} %`],
    ["Вес: от … до", `${fmt(r.weight_min, 4)} … ${fmt(r.weight_max, 4)}`],
  ];
  if (r.method === "cell") out.push(["Лучший размер ячейки", r.uniform || r.best_cell_size === null ? "нет: веса равны 1" : `${fmt(r.best_cell_size)} м`]);
  if (r.method === "polygon" && r.nx !== undefined && r.ny !== undefined) out.push(["Сетка полигонов", `${int(r.nx)} × ${int(r.ny)}`]);
  if (r.zero_weights) out.push(["Нулевых весов", int(r.zero_weights)]);
  return out;
}

/** The curve «размер ячейки → среднее» without size 0 (it holds the plain mean, drawn as the reference). */
export function declusCurve(r: DeclusAnswer): { xs: number[]; ys: (number | null)[] } {
  if (!r.cell_sizes || !r.means) return { xs: [], ys: [] };
  return { xs: r.cell_sizes.slice(1), ys: r.means.slice(1) };
}

/** The point of the best size on the curve; uniform or none — null. */
export function bestPoint(r: DeclusAnswer): { x: number; y: number } | null {
  const best = r.best_cell_size;
  if (r.uniform || best === null) return null;
  const { xs, ys } = declusCurve(r);
  const i = xs.findIndex((s) => Math.abs(s - best) <= 1e-9 * Math.max(1, Math.abs(s)));
  const y = i >= 0 ? ys[i] : r.declustered_mean;
  return y === null || y === undefined ? null : { x: best, y };
}

export const declusCsv = (r: DeclusAnswer): string => {
  const { xs, ys } = declusCurve(r);
  return csvText(["Размер ячейки, м", `Среднее ${r.field}`], xs.map((x, i) => [x, ys[i]]));
};

/** Mean and σ of the scores of the points (≈ 0 and 1 without weights). */
export function scoreMoments(scores: readonly number[]): { mean: number | null; std: number | null } {
  if (!scores.length) return { mean: null, std: null };
  const m = scores.reduce((a, b) => a + b, 0) / scores.length;
  const s = Math.sqrt(scores.reduce((a, b) => a + (b - m) ** 2, 0) / scores.length);
  return { mean: Math.abs(m) < 1e-12 ? 0 : m, std: s };
}

export function nscoreFacts(r: NscoreAnswer): Fact[] {
  const m = scoreMoments(r.sample_scores);
  return [
    ["Интервалов", int(r.count)],
    ["Строк таблицы преобразования", int(r.table_rows)],
    ["Значения: от … до", r.values.length ? `${fmt(r.values[0])} … ${fmt(r.values[r.values.length - 1])}` : "—"],
    ["Хвосты: от … до", `${fmt(r.zmin)} … ${fmt(r.zmax)}`],
    ["Оценки проб: среднее", fmt(m.mean, 3)],
    ["Оценки проб: σ", fmt(m.std, 3)],
    ["Веса декластеризации", r.weighted ? "да" : "нет"],
  ];
}

export const nscoreCsv = (r: NscoreAnswer): string => csvText([r.field, "Нормальная оценка"], r.values.map((v, i) => [v, r.scores[i]]));

export const backtrCsv = (field: string, scores: readonly number[], values: readonly (number | null)[]): string => csvText(["Нормальная оценка", field], scores.map((s, i) => [s, values[i] ?? null]));

export interface BootRow {
  id: BootStatistic;
  title: string;
  observed: string;
  weighted: string;
  mean: string;
  std: string;
  low: string;
  high: string;
  defined: string;
}

export function bootRows(r: BootstrapAnswer): BootRow[] {
  return STATISTICS.map(([id, title]) => {
    const e = r.statistics[id];
    return { id, title, observed: fmt(e?.observed), weighted: fmt(e?.observed_weighted), mean: fmt(e?.mean), std: fmt(e?.std), low: fmt(e?.ci_low), high: fmt(e?.ci_high), defined: e ? int(e.defined) : "—" };
  });
}

export const percentText = (p: number): string => `${fmt(p * 100, 3)} %`;

export function bootstrapCsv(r: BootstrapAnswer): string {
  const head = ["Статистика", "По данным", "С весами", "Среднее реализаций", "σ реализаций", `Нижняя (${percentText(r.confidence)})`, "Верхняя", "Реализаций с числом"];
  return csvText(head, STATISTICS.map(([id, title]) => {
    const e = r.statistics[id];
    return [title, e?.observed, e?.observed_weighted, e?.mean, e?.std, e?.ci_low, e?.ci_high, e?.defined];
  }));
}

// ---- the texts for the assistant ----

const where = (r: { field: string; table: string }) => `${r.field} (${r.table})`;

export function declusText(r: DeclusAnswer): string {
  const best = r.method === "polygon" ? "полигоны в плане" : r.uniform || r.best_cell_size === null ? "лучшего размера ячейки нет: веса равны 1" : `лучший размер ячейки ${fmt(r.best_cell_size)} м`;
  return `Декластеризация ${where(r)}: интервалов ${int(r.count)}; среднее без весов ${fmt(r.naive_mean)}, с весами ${fmt(r.declustered_mean)}; ${best}. ${countsText(r.counts)}`;
}

export function nscoreText(r: NscoreAnswer): string {
  const m = scoreMoments(r.sample_scores);
  return `Нормальные оценки ${where(r)}${r.weighted ? " с весами декластеризации" : ""}: интервалов ${int(r.count)}, строк таблицы ${int(r.table_rows)}, хвосты ${fmt(r.zmin)} … ${fmt(r.zmax)}; оценки проб: среднее ${fmt(m.mean, 3)}, σ ${fmt(m.std, 3)}. ${countsText(r.counts)}`;
}

export function bootstrapText(r: BootstrapAnswer): string {
  const parts = STATISTICS.map(([id, title]) => {
    const e = r.statistics[id];
    return `${title} ${fmt(e?.observed)} [${fmt(e?.ci_low)}; ${fmt(e?.ci_high)}]`;
  });
  return `Бутстрап ${where(r)}${r.weighted ? " с весами декластеризации" : ""}: интервалов ${int(r.count)}, реализаций ${int(r.n_boot)}, зерно ${r.seed}, доверие ${percentText(r.confidence)}: ${parts.join("; ")}. ${countsText(r.counts)}`;
}

// ---- refusals ----

export interface Problem {
  code: string;
  message: string;
  hint: string;
  /** more than 20 000 intervals (Т7.29-2): the tab offers to composite */
  composite: boolean;
}

/** An error of the core ({code, message, hint}) or of the form as the tab shows it. */
export function problemOf(e: unknown): Problem {
  const x = e as { code?: unknown; message?: unknown; hint?: unknown } | null;
  const code = typeof x?.code === "string" ? x.code : "error";
  const message = typeof x?.message === "string" && x.message ? x.message : String(e);
  return { code, message, hint: typeof x?.hint === "string" ? x.hint : "", composite: code === "too_many_samples" };
}
