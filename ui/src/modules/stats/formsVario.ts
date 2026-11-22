/**
 * The forms of the Э16 analyses of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11, решение 21; перенос `StatsPage.tsx`
 * BF): the experimental variogram in plan (omni or 0°, 45°, 90°, 135°) with the model of BF (nugget + up to 4
 * structures `it, cc, hmaj, hmin, azi`), its curve without data, the variogram map by the shifts (ΔX, ΔY) and the
 * uncertainty map of IDW. The bodies of `POST /stats/tab/{variogram|variogram-model|map|local}` from the texts of
 * the fields (a decimal comma is accepted; empty — the default of the core; the model — numbers only), the facts,
 * the CSV and the short texts for the assistant. An empty lag, cell or node is «—» / not drawn, never 0. Pure.
 */
import { csvText } from "../../charts/core/csv";
import type { TabDirection, TabDirections, TabLocalAnswer, TabLocalBody, TabMapAnswer, TabMapBody, TabModel, TabModelAnswer, TabModelBody, TabModelType, TabVariogramAnswer, TabVariogramBody, VarioBody } from "./api";
import { Bad, build, countsText, fmt, int, num, type Built, type Fact, type TabContext } from "./forms";

export interface StructureForm {
  it: TabModelType;
  cc: string;
  hmaj: string;
  /** empty — as `hmaj` (isotropic) */
  hmin: string;
  azi: string;
}

export interface VariogramForm {
  lag: string;
  nlag: string;
  lagTol: string;
  directions: TabDirections;
  minPairs: string;
  standardize: boolean;
  /** the model is drawn over the points (and sent with them) */
  withModel: boolean;
  nugget: string;
  structures: StructureForm[];
  /** the length of the curve of the model without data; empty — 1,5 × the largest range */
  hMax: string;
}

export interface MapForm {
  cell: string;
  n: string;
  minPairs: string;
  standardize: boolean;
}

export type LocalShow = "mean" | "sd" | "p10" | "p90" | "prob";

export interface LocalForm {
  nx: string;
  ny: string;
  k: string;
  power: string;
  radius: string;
  realizations: string;
  /** empty — a new one (the answer tells it) */
  seed: string;
  /** empty — no map of the probability */
  threshold: string;
  show: LocalShow;
}

export interface VarioForms {
  variogram: VariogramForm;
  map: MapForm;
  local: LocalForm;
}

export const MAX_STRUCTURES = 4;

export const MODEL_TYPES: readonly [TabModelType, string][] = [
  ["spherical", "сферическая"],
  ["exponential", "экспоненциальная"],
  ["gaussian", "гауссова"],
];

export const NEW_STRUCTURE: StructureForm = { it: "spherical", cc: "1", hmaj: "100", hmin: "", azi: "0" };

/** As BF (16.10): the lag — a round step of the extent / 20 (empty), 10 lags, 30 pairs; the map — 10 cells each way by 5 pairs; local — 30 × 30, k 8, power 2, 50 realizations, the radius 25 % of the extent. */
export const DEFAULT_VARIO_FORMS: VarioForms = {
  variogram: { lag: "", nlag: "10", lagTol: "", directions: "omni", minPairs: "30", standardize: false, withModel: false, nugget: "0", structures: [NEW_STRUCTURE], hMax: "" },
  map: { cell: "", n: "10", minPairs: "5", standardize: false },
  local: { nx: "30", ny: "30", k: "8", power: "2", radius: "", realizations: "50", seed: "", threshold: "", show: "mean" },
};

export const LOCAL_MAPS: readonly [LocalShow, string][] = [
  ["mean", "Среднее"],
  ["sd", "σ"],
  ["p10", "P10"],
  ["p90", "P90"],
  ["prob", "Вероятность ≥ порога"],
];

export const localTitle = (s: LocalShow): string => LOCAL_MAPS.find(([id]) => id === s)?.[1] ?? s;

// ---- the bodies ----

const vcommon = (c: TabContext): VarioBody => ({ set_id: c.setId, table: c.table, field: c.field });

const positive = (text: string, label: string, opt: { whole?: boolean; max?: number } = {}): number | null => {
  const v = num(text, label, opt);
  if (v !== null && !(v > 0)) throw new Bad(`«${label}» — больше 0`);
  return v;
};

/** The model of the form: numbers only (the core does not read «0,1» there — the form turns it into 0.1). */
export function modelOf(f: Pick<VariogramForm, "nugget" | "structures">): TabModel {
  const nugget = num(f.nugget, "Самородок", { min: 0 }) ?? 0;
  if (f.structures.length > MAX_STRUCTURES) throw new Bad(`Структур больше ${MAX_STRUCTURES}`);
  const structures = f.structures.map((s, i) => {
    const n = i + 1;
    const cc = num(s.cc, `Структура ${n}: вклад`, { min: 0 });
    const hmaj = positive(s.hmaj, `Структура ${n}: дальность`);
    if (cc === null || hmaj === null) throw new Bad(`Структура ${n}: задайте вклад и дальность`);
    const hmin = positive(s.hmin, `Структура ${n}: дальность поперёк`) ?? hmaj;
    const azi = num(s.azi, `Структура ${n}: азимут`, { min: 0, max: 360 }) ?? 0;
    return { it: s.it, cc, hmaj, hmin, azi };
  });
  return { nugget, structures };
}

export const variogramBody = (c: TabContext, f: VariogramForm): Built<TabVariogramBody> =>
  build(() => {
    const lag = positive(f.lag, "Лаг");
    const lagTol = num(f.lagTol, "Допуск лага", { min: 0 });
    if (lag !== null && lagTol !== null && lagTol > lag) throw new Bad("«Допуск лага» больше лага");
    const body: TabVariogramBody = {
      ...vcommon(c),
      lag,
      nlag: num(f.nlag, "Лагов", { whole: true, min: 1, max: 200 }),
      lag_tol: lagTol,
      directions: f.directions,
      min_pairs: num(f.minPairs, "Пар не меньше", { whole: true, min: 1, max: 1000 }),
      standardize: f.standardize,
    };
    return f.withModel ? { ...body, model: modelOf(f) } : body;
  });

/** The points of the model curve. */
export const CURVE_POINTS = 101;

/**
 * The curve of the model along the directions of the form: up to «Кривая до», else up to the last lag of the
 * data (when there are data), else 1,5 × the largest range (the core's default).
 */
export const modelBody = (f: VariogramForm, data?: Pick<TabVariogramAnswer, "lag" | "nlag" | "lag_tol"> | null): Built<TabModelBody> =>
  build(() => {
    const m = modelOf(f);
    const hMax = positive(f.hMax, "Кривая до") ?? (data ? data.lag * data.nlag + data.lag_tol : null);
    if (hMax === null && !m.structures.length) throw new Bad("Задайте «Кривая до» или хотя бы одну структуру");
    return { ...m, h_max: hMax, n: CURVE_POINTS, directions: f.directions };
  });

export const mapBody = (c: TabContext, f: MapForm): Built<TabMapBody> =>
  build(() => ({
    ...vcommon(c),
    cell: positive(f.cell, "Ячейка"),
    n: num(f.n, "Ячеек в каждую сторону", { whole: true, min: 1, max: 100 }),
    min_pairs: num(f.minPairs, "Пар не меньше", { whole: true, min: 1, max: 1000 }),
    standardize: f.standardize,
  }));

export const localBody = (c: TabContext, f: LocalForm): Built<TabLocalBody> =>
  build(() => {
    const power = num(f.power, "Степень", { max: 10 });
    if (power !== null && !(power > 0)) throw new Bad("«Степень» — больше 0");
    return {
      ...vcommon(c),
      grid: { nx: num(f.nx, "Узлов по X", { whole: true, min: 2, max: 100 }), ny: num(f.ny, "Узлов по Y", { whole: true, min: 2, max: 100 }) },
      k: num(f.k, "Соседей", { whole: true, min: 1, max: 64 }),
      power,
      radius: positive(f.radius, "Радиус"),
      realizations: num(f.realizations, "Реализаций", { whole: true, min: 5, max: 300 }),
      seed: num(f.seed, "Зерно", { whole: true, min: 0 }),
      threshold: num(f.threshold, "Порог"),
    };
  });

// ---- the facts ----

/** The title of a direction: «всенаправленная» or «азимут 45° ± 22,5°». */
export const directionTitle = (d: Pick<TabDirection, "az" | "az_tol" | "omni">): string => (d.omni ? "всенаправленная" : `азимут ${fmt(d.az, 4)}° ± ${fmt(d.az_tol, 3)}°`);

export interface VarioPoint {
  h: number;
  gamma: number;
  n: number;
  few: boolean;
}

/** The points of a direction: a lag without pairs (h or γ null) is not a point — never 0. */
export function varioPoints(d: TabDirection): VarioPoint[] {
  const out: VarioPoint[] = [];
  d.n.forEach((n, k) => {
    const h = d.h?.[k];
    const g = d.gamma?.[k];
    if (typeof h === "number" && typeof g === "number" && Number.isFinite(h) && Number.isFinite(g)) out.push({ h, gamma: g, n, few: d.few[k] ?? false });
  });
  return out;
}

/** The reference level of the chart: σ² (or 1 when γ is divided by σ²). */
export const varianceRef = (r: Pick<TabVariogramAnswer, "sigma2" | "standardize">): { value: number; label: string } => (r.standardize ? { value: 1, label: "1 (γ / σ²)" } : { value: r.sigma2, label: `σ² = ${fmt(r.sigma2, 4)}` });

export const modelTitle = (m: Pick<TabModelAnswer, "nugget" | "sill" | "structures">): string =>
  [`C0 ${fmt(m.nugget, 4)}`, ...m.structures.map((s, i) => `${i + 1}: ${MODEL_TYPES.find(([t]) => t === s.it)?.[1] ?? s.it} ${fmt(s.cc, 4)}, ${fmt(s.hmaj, 4)} × ${fmt(s.hmin, 4)} м, ${fmt(s.azi, 4)}°`), `порог ${fmt(m.sill, 4)}`].join("; ");

export function variogramFacts(r: TabVariogramAnswer): Fact[] {
  const pairs = r.directions.reduce((a, d) => a + d.n.reduce((x, y) => x + y, 0), 0);
  const out: Fact[] = [
    ["Интервалов", int(r.count)],
    ["σ²", fmt(r.sigma2)],
    ["Лаг", `${fmt(r.lag)} м ± ${fmt(r.lag_tol)} м`],
    ["Лагов", int(r.nlag)],
    ["Пар в точке не меньше", int(r.min_pairs)],
    ["Направления", r.directions.length === 1 ? "всенаправленная" : r.directions.map((d) => `${fmt(d.az, 4)}°`).join(", ")],
    ["Пар всего", int(pairs)],
  ];
  if (r.standardize) out.push(["γ", "поделена на σ²"]);
  if (r.model) out.push(["Модель", modelTitle(r.model)]);
  return out;
}

export function variogramCsv(r: TabVariogramAnswer): string {
  const rows = r.directions.flatMap((d) => d.n.map((n, k) => [d.omni ? "всенаправленная" : d.az, r.lags[k], d.h?.[k] ?? null, d.gamma?.[k] ?? null, n, d.few[k] ? "мало пар" : "", d.model_gamma?.[k] ?? null]));
  return csvText(["Азимут, °", "Лаг, м", "h, м", "γ", "Пар", "Примечание", "γ модели"], rows);
}

export const modelCsv = (m: TabModelAnswer): string => csvText(["Азимут, °", "h, м", "γ модели", "C(h)"], m.directions.flatMap((d) => d.h.map((h, i) => [d.az, h, d.gamma[i], d.cov[i]])));

/** The cells of the map: filled (γ), empty (fewer pairs than the threshold — null, never 0). */
export function mapFill(r: Pick<TabMapAnswer, "gamma">): { filled: number; empty: number } {
  let filled = 0;
  let empty = 0;
  for (const row of r.gamma) for (const g of row) g === null ? (empty += 1) : (filled += 1);
  return { filled, empty };
}

export function mapFacts(r: TabMapAnswer): Fact[] {
  const { filled, empty } = mapFill(r);
  return [
    ["Интервалов", int(r.count)],
    ["σ²", fmt(r.sigma2)],
    ["Ячейка", `${fmt(r.cell)} м`],
    ["Сетка ΔX × ΔY", `${int(r.u.length)} × ${int(r.v.length)}`],
    ["Пар в ячейке не меньше", int(r.min_pairs)],
    ["Ячеек с γ / пустых", `${int(filled)} / ${int(empty)}`],
    ...(r.standardize ? ([["γ", "поделена на σ²"]] as Fact[]) : []),
  ];
}

export const mapCsv = (r: TabMapAnswer): string => csvText(["ΔX (восток), м", "ΔY (север), м", "γ", "Пар"], r.v.flatMap((v, j) => r.u.map((u, i) => [u, v, r.gamma[j]?.[i] ?? null, r.n[j]?.[i] ?? 0])));

/** The grid of the chosen map; the probability without a threshold — null. */
export const localGrid = (r: TabLocalAnswer, show: LocalShow): (number | null)[][] | null => (show === "prob" ? r.prob : r[show]);

export function localFacts(r: TabLocalAnswer): Fact[] {
  return [
    ["Интервалов", int(r.count)],
    ["Скважин", int(r.holes)],
    ["Реализаций", int(r.realizations)],
    ["Сетка", `${int(r.grid.nx)} × ${int(r.grid.ny)}`],
    ["Соседей, степень", `${int(r.k)}, ${fmt(r.power, 3)}`],
    ["Радиус", `${fmt(r.radius)} м`],
    ["Порог", r.threshold === null ? "—" : fmt(r.threshold)],
    ["Узлов без данных в радиусе", int(r.empty)],
  ];
}

export function localCsv(r: TabLocalAnswer): string {
  const rows = r.grid.y.flatMap((y, j) => r.grid.x.map((x, i) => [x, y, r.mean[j]?.[i], r.sd[j]?.[i], r.p10[j]?.[i], r.p90[j]?.[i], r.prob?.[j]?.[i] ?? null]));
  return csvText(["X, м", "Y, м", "Среднее", "σ", "P10", "P90", r.threshold === null ? "Вероятность (нет порога)" : `Вероятность ≥ ${fmt(r.threshold)}`], rows);
}

// ---- the texts for the assistant ----

const where = (r: { field: string; table: string }) => `${r.field} (${r.table})`;

export function variogramText(r: TabVariogramAnswer): string {
  const dirs = r.directions.map((d) => {
    const pts = varioPoints(d);
    return `${directionTitle(d)}: ${pts.map((p) => `${fmt(p.h, 4)} м → ${fmt(p.gamma, 4)} (${int(p.n)} пар${p.few ? ", мало" : ""})`).join("; ") || "нет лагов с парами"}`;
  });
  return `Вариограмма ${where(r)} в плане: интервалов ${int(r.count)}, σ² ${fmt(r.sigma2)}, лаг ${fmt(r.lag)} м ± ${fmt(r.lag_tol)} м${r.standardize ? ", γ / σ²" : ""}. ${dirs.join(". ")}.${r.model ? ` Модель: ${modelTitle(r.model)}.` : ""} ${countsText(r.counts)}`;
}

export function mapText(r: TabMapAnswer): string {
  const { filled, empty } = mapFill(r);
  return `Карта вариограммы ${where(r)}: интервалов ${int(r.count)}, σ² ${fmt(r.sigma2)}, ячейка ${fmt(r.cell)} м, сдвиги ΔX (восток) и ΔY (север) от −${fmt(r.cell * r.nu)} до ${fmt(r.cell * r.nu)} м; ячеек с γ ${int(filled)}, пустых (пар меньше ${int(r.min_pairs)}) ${int(empty)}. ${countsText(r.counts)}`;
}

function range(g: (number | null)[][] | null): string {
  let lo = Infinity;
  let hi = -Infinity;
  for (const row of g ?? []) for (const v of row) if (v !== null && Number.isFinite(v)) [lo, hi] = [Math.min(lo, v), Math.max(hi, v)];
  return lo <= hi ? `${fmt(lo)} … ${fmt(hi)}` : "—";
}

export function localText(r: TabLocalAnswer): string {
  const prob = r.threshold === null ? "" : `; вероятность ≥ ${fmt(r.threshold)}: ${range(r.prob)}`;
  return `Карта неопределённости ${where(r)} (IDW в плане): интервалов ${int(r.count)}, скважин ${int(r.holes)}, реализаций ${int(r.realizations)}, зерно ${r.seed}, сетка ${int(r.grid.nx)} × ${int(r.grid.ny)}, радиус ${fmt(r.radius)} м; среднее ${range(r.mean)}, σ ${range(r.sd)}${prob}; узлов без данных ${int(r.empty)}. ${countsText(r.counts)}`;
}
