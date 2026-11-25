/**
 * The client of the checks of an estimate that need no table of errors (ТЗ 8.8.5; ADR 0024 §8; docs/progress/e18-api.md §8.2–§8.4):
 *
 * - `POST /geostat/estimate/kna {estimation_id, blocks?, vary?}` → the job `estimate.kna` (quick); its result is `contract_answers.kna`;
 * - `POST /geostat/estimate/global {estimation_id}` → the job `estimate.global` (quick); its result is `contract_answers.global`;
 * - `POST /geostat/estimate/qq {estimation_id, weighted?, quantiles?}` — at once (heavy slot): `contract_answers.qq`, the points are [x, y, P %];
 * - `POST /geostat/estimate/swath {estimation_id, direction?}` — the settings of the swath window of Э13 (`contract_answers.swath`).
 *
 * A gap of an answer stays `null` — never 0 (8.0.1). No React.
 */
import { api } from "../../shell/api";
import { normalizeSwath, type SwathSettings } from "../swath/model";
import { GEOSTAT_API, isNum, numOrNull, obj, startedOf, str, type Started, type Triple } from "./api";

const nums = (v: unknown): number[] => (Array.isArray(v) ? v.filter(isNum) : []);
const triple = (v: unknown): Triple => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? ([v[0], v[1], v[2]] as Triple) : [0, 0, 0]);

// ---- KNA (8.2) ----

export type KnaParam = "n_max" | "radius" | "disc" | "size";
export const KNA_PARAMS: readonly KnaParam[] = ["n_max", "radius", "disc", "size"];
export const KNA_TITLES: Record<KnaParam, string> = { n_max: "n_max — число проб", radius: "Радиус поиска", disc: "Дискретизация блока", size: "Размер блока" };
/** The title of the axis X of the charts of a parameter. */
export const KNA_AXIS: Record<KnaParam, string> = { n_max: "n_max, проб", radius: "Множитель радиусов поиска", disc: "Дискретизация d × d × d", size: "Множитель размера блока" };

const nf = (x: number): string => x.toLocaleString("ru-RU", { maximumFractionDigits: 3 });

/** The value of a variant as a person reads it: «16», «×0,75», «3×3×3». */
export function knaValueText(param: KnaParam, value: number): string {
  if (param === "n_max") return nf(value);
  if (param === "disc") return `${value}×${value}×${value}`;
  return `×${nf(value)}`;
}

/** The indicators of a block (null where the block was not estimated: status 1–3). */
export interface KnaIndicators {
  KE: number | null;
  SLOPE: number | null;
  KV: number | null;
  /** the sum of the negative weights */
  SNW: number | null;
  /** the weight of the farthest selected sample */
  w_far: number | null;
  /** the Lagrange multiplier μ */
  LAGR: number | null;
  /** the number of samples chosen */
  NS: number | null;
  status: number;
}

/** A variant of a parameter: `value` is the number (for `disc` — d of d × d × d). */
export interface KnaVariant extends KnaIndicators {
  value: number;
}

export interface KnaBlock {
  at: { row: number; p: number; ijk: Triple };
  centre: Triple;
  size: Triple;
  /** the samples inside the radius of the first pass */
  n_r1: number;
  /** «много» / «средне» / «мало» for the blocks chosen automatically */
  auto: string | null;
  base: KnaIndicators;
  vary: Partial<Record<KnaParam, KnaVariant[]>>;
  /** the n_max the block alone wants; null — none (the best SLOPE is not above 0, or the block is not estimated) */
  recommend: number | null;
}

export interface KnaResult {
  estimation_id: number;
  name: string;
  method: string;
  vary: KnaParam[];
  /** the share of the best SLOPE the recommended n_max reaches (0,95) */
  threshold: number;
  blocks: KnaBlock[];
  /** the common recommendation: the largest n_max of the blocks; null — none; `n_max` was not varied: `recommended` false */
  recommend: number | null;
  recommended: boolean;
  /** the blocks of the row that the first passes can reach (automatic choice) */
  candidates: number | null;
  warnings: string[];
}

function readIndicators(raw: unknown): KnaIndicators {
  const o = obj(raw);
  return { KE: numOrNull(o.KE), SLOPE: numOrNull(o.SLOPE), KV: numOrNull(o.KV), SNW: numOrNull(o.SNW), w_far: numOrNull(o.w_far), LAGR: numOrNull(o.LAGR), NS: numOrNull(o.NS), status: isNum(o.status) ? o.status : 0 };
}

/** `contract_answers.kna` (the result of the job `estimate.kna`); null — not an answer of the core. */
export function readKna(raw: unknown): KnaResult | null {
  const o = obj(raw);
  if (!isNum(o.estimation_id) || !Array.isArray(o.blocks)) return null;
  const vary = (Array.isArray(o.vary) ? o.vary : []).filter((v): v is KnaParam => (KNA_PARAMS as readonly unknown[]).includes(v));
  const blocks: KnaBlock[] = o.blocks.map((b) => {
    const x = obj(b);
    const at = obj(x.at);
    const series: Partial<Record<KnaParam, KnaVariant[]>> = {};
    for (const p of KNA_PARAMS) {
      const list = obj(x.vary)[p];
      if (!Array.isArray(list)) continue;
      series[p] = list.map((v) => {
        const value = obj(v).value;
        return { ...readIndicators(v), value: Array.isArray(value) ? (numOrNull(value[0]) ?? 0) : (numOrNull(value) ?? 0) };
      });
    }
    return { at: { row: Number(at.row ?? 0), p: Number(at.p ?? 0), ijk: triple(at.ijk) }, centre: triple(x.centre), size: triple(x.size), n_r1: Number(x.n_r1 ?? 0), auto: str(x.auto), base: readIndicators(x.base), vary: series, recommend: numOrNull(obj(x.recommend).n_max) };
  });
  return {
    estimation_id: o.estimation_id,
    name: String(o.name ?? ""),
    method: String(o.method ?? ""),
    vary,
    threshold: numOrNull(o.threshold) ?? 0.95,
    blocks,
    recommend: numOrNull(obj(o.recommend).n_max),
    recommended: vary.includes("n_max"),
    candidates: numOrNull(o.candidates),
    warnings: Array.isArray(o.warnings) ? o.warnings.filter((w): w is string => typeof w === "string") : [],
  };
}

/** A test block of the body: a row of the model, the indices of a parent, or a point (exactly one). */
export type BlockAt = { row: number } | { ijk: Triple } | { xyz: Triple };

export interface KnaBody {
  estimation_id: number;
  /** 1–10 blocks; absent — three automatically (много, средне, мало) */
  blocks?: BlockAt[];
  vary?: KnaParam[];
}

export const startKna = async (body: KnaBody): Promise<Started> => startedOf(obj(await api.post(`${GEOSTAT_API}/estimate/kna`, body)));

// ---- the global check (8.3) ----

/** The count, the mean, the variance and the coefficient of variation of a set of values (null — none). */
export interface Moments {
  n: number;
  mean: number | null;
  var: number | null;
  cv: number | null;
}

/** The blocks of the comparison: the same moments, with the weight (the volume of the blocks, m³). */
export interface BlockMoments extends Moments {
  volume: number | null;
}

export interface GlobalResult {
  estimation_id: number;
  name: string;
  field: string;
  status_field: string;
  method: string;
  sub: string;
  /** the norm of the difference of the means, % (5) */
  limit_pct: number;
  composites: { naive: Moments | null; declus: Moments | null; reference: "declus" | "naive" };
  nn: BlockMoments | null;
  estimate: BlockMoments | null;
  /** 100·(mean of the estimate − mean of the reference)/mean of the reference; null — it cannot be told */
  diff_pct: number | null;
  norm: boolean | null;
  diff_nn_pct: number | null;
  norm_nn: boolean | null;
  block_var: { expected: number | null; estimate: number | null; sill: number | null; ratio: number | null; disc: number[]; size: number[] };
  blocks: { rows: number; by_status: number[]; used: number; empty: number; no_volume: number };
  warnings: string[];
}

function readMoments(raw: unknown): Moments | null {
  const o = obj(raw);
  if (!isNum(o.n)) return null;
  return { n: o.n, mean: numOrNull(o.mean), var: numOrNull(o.var), cv: numOrNull(o.cv) };
}

function readBlockMoments(raw: unknown): BlockMoments | null {
  const m = readMoments(raw);
  return m ? { ...m, volume: numOrNull(obj(raw).volume) } : null;
}

const flag = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

/** `contract_answers.global` (the result of the job `estimate.global`); null — not an answer of the core. */
export function readGlobal(raw: unknown): GlobalResult | null {
  const o = obj(raw);
  if (!isNum(o.estimation_id) || !Object.keys(obj(o.composites)).length) return null;
  const c = obj(o.composites);
  const bv = obj(o.block_var);
  const b = obj(o.blocks);
  return {
    estimation_id: o.estimation_id,
    name: String(o.name ?? ""),
    field: String(o.field ?? ""),
    status_field: String(o.status_field ?? ""),
    method: String(o.method ?? ""),
    sub: String(o.sub ?? "parent"),
    limit_pct: numOrNull(o.limit_pct) ?? 5,
    composites: { naive: readMoments(c.naive), declus: readMoments(c.declus), reference: c.reference === "naive" ? "naive" : "declus" },
    nn: readBlockMoments(o.nn),
    estimate: readBlockMoments(o.estimate),
    diff_pct: numOrNull(o.diff_pct),
    norm: flag(o.norm),
    diff_nn_pct: numOrNull(o.diff_nn_pct),
    norm_nn: flag(o.norm_nn),
    block_var: { expected: numOrNull(bv.expected), estimate: numOrNull(bv.estimate), sill: numOrNull(bv.sill), ratio: numOrNull(bv.ratio), disc: nums(bv.disc), size: nums(bv.size) },
    blocks: { rows: Number(b.rows ?? 0), by_status: nums(b.by_status), used: Number(b.used ?? 0), empty: Number(b.empty ?? 0), no_volume: Number(b.no_volume ?? 0) },
    warnings: Array.isArray(o.warnings) ? o.warnings.filter((w): w is string => typeof w === "string") : [],
  };
}

export const startGlobal = async (estimationId: number): Promise<Started> => startedOf(obj(await api.post(`${GEOSTAT_API}/estimate/global`, { estimation_id: estimationId })));

// ---- Q-Q (8.4) ----

/** [x, y, P %] — the form of the points of Э13 (`QQTuple`). */
export type QqPoint = [number | null, number | null, number];

export interface QqAnswer {
  estimation_id: number;
  name: string;
  field: string;
  /** the samples are weighted by the declustering weights (the blocks — by the volume) */
  weighted: boolean;
  points: QqPoint[];
  marks: QqPoint[];
  /** y / x of each quantile */
  ratio: (number | null)[];
  extremes: { x: [number, number]; y: [number, number] } | null;
  /** the Kolmogorov–Smirnov D of the two weighted distributions */
  ks: number | null;
  x: Moments | null;
  y: Moments | null;
}

const point = (v: unknown): QqPoint | null => (Array.isArray(v) && v.length === 3 && isNum(v[2]) ? [numOrNull(v[0]), numOrNull(v[1]), v[2]] : null);
const points = (v: unknown): QqPoint[] => (Array.isArray(v) ? v.map(point).filter((p): p is QqPoint => p !== null) : []);
const pair = (v: unknown): [number, number] | null => (Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1]) ? [v[0], v[1]] : null);

/** `contract_answers.qq`; null — not an answer of the core. */
export function readQq(raw: unknown): QqAnswer | null {
  const o = obj(raw);
  if (!isNum(o.estimation_id) || !Array.isArray(o.points)) return null;
  const ex = obj(o.extremes);
  const xs = pair(ex.x);
  const ys = pair(ex.y);
  return {
    estimation_id: o.estimation_id,
    name: String(o.name ?? ""),
    field: String(o.field ?? ""),
    weighted: o.weighted !== false,
    points: points(o.points),
    marks: points(o.marks),
    ratio: Array.isArray(o.ratio) ? o.ratio.map(numOrNull) : [],
    extremes: xs && ys ? { x: xs, y: ys } : null,
    ks: numOrNull(o.ks),
    x: readMoments(o.x),
    y: readMoments(o.y),
  };
}

export async function loadQq(estimationId: number, weighted: boolean): Promise<QqAnswer> {
  const r = readQq(await api.post(`${GEOSTAT_API}/estimate/qq`, { estimation_id: estimationId, weighted }));
  if (!r) throw new Error("Ядро вернуло Q-Q без точек.");
  return r;
}

// ---- swath (8.4) ----

/** `POST /estimate/swath` → the settings of the window of Э13: the series of the blocks of the estimate and the samples of the row. */
export async function loadSwathSettings(estimationId: number, direction?: "x" | "y" | "z"): Promise<{ name: string; settings: SwathSettings }> {
  const r = obj(await api.post(`${GEOSTAT_API}/estimate/swath`, { estimation_id: estimationId, ...(direction ? { direction } : {}) }));
  return { name: String(r.name ?? ""), settings: normalizeSwath(r) };
}
