/**
 * The client of `/api/v1/geostat/variography` — every form as docs/progress/e16-api.md (the contract; ADR 0022 §14;
 * errors `{code, message, hint, param?}` in Russian, `param` — a key of the body or a path «directions.0.az»):
 *
 * - `GET /defaults?set_id&table&field[&domain_table&domain_field&domain_values&weights&transform]` → the counts, σ²,
 *   the median length, `defaults` and `ranges` of the form (`domain_values` joined by «;»).
 * - `POST /experimental`, `POST /map` → `{source, id}` (jobs, queue `heavy`); the result of the job — the form of
 *   the calculation (`readCalc`); `POST /preview` — the same form synchronously (`key: null`, `sampled`).
 * - `POST /fit {key, type, model, weights, fix, fix_nugget}` → `{model, residual, n, starts, warnings, order, fixed}`;
 *   `POST /back {key, model, ltail?, utail?, ltpar?, utpar?}` → `{model, share, warning, residual, curves}`;
 *   `POST /curve {model, directions?, h?, h_max?, n?}` → `{directions: [{axis, az, plunge, h, gamma, cov}]}`.
 * - models: `GET /models[?set_id]`, `POST /models {name?, model, exp_key, color?, fit?, back?}`, `GET / PUT / DELETE
 *   /models/{id}`, `POST /models/{id}/rebuild`, `GET /models/{id}/export?format[&path]`, `POST /models/import`.
 *
 * An undefined number of an answer stays `null` (a lag without pairs, σ = 0) — never 0.
 */
import { api } from "../../shell/api";
import type { Started } from "../composites/api";
import { readModel, type VModel } from "./model";

export const GEOSTAT_API = "/geostat";
export const VARIO_API = `${GEOSTAT_API}/variography`;

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const numOrNull = (v: unknown): number | null => (isNum(v) ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
/** An array of numbers of an answer; anything but a finite number is `null` (empty — not zero). */
export const nums = (v: unknown): (number | null)[] => (Array.isArray(v) ? v.map(numOrNull) : []);
const ints = (v: unknown): number[] => (Array.isArray(v) ? v.map((x) => (isNum(x) ? x : 0)) : []);
const flags = (v: unknown): boolean[] => (Array.isArray(v) ? v.map((x) => x === true) : []);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

// ---- the data of a calculation ----

/** The common part of `experimental`, `preview`, `map` (e16-api.md «Данные расчёта»). */
export interface DataBody {
  set_id: number;
  table: string;
  field: string;
  domain: { table?: string; field: string; values: string[] } | null;
  /** null — DECL_W when the table has it; false — no weights; a field name */
  weights: string | false | null;
  transform: "none" | "nscore";
}

export interface Counts {
  rows: number;
  missing: number;
  no_position: number;
  outside_domain: number;
  used: number;
  no_weight: number;
}

export function readCounts(raw: unknown): Counts {
  const o = obj(raw);
  const n = (k: string) => (isNum(o[k]) ? (o[k] as number) : 0);
  return { rows: n("rows"), missing: n("missing"), no_position: n("no_position"), outside_domain: n("outside_domain"), used: n("used"), no_weight: n("no_weight") };
}

/** A range of the form (`ranges` of `/defaults`): `hi` null — no upper bound. */
export interface RangeInfo {
  lo: number;
  hi: number | null;
  lo_open: boolean;
  integer: boolean;
  inf: boolean;
  unit: string;
}

export interface DefaultsAnswer {
  set_id: number;
  table: string;
  table_name: string;
  field: string;
  unit: string | null;
  counts: Counts;
  sigma2: number | null;
  sigma2_z: number | null;
  median_length: number | null;
  /** the weight field taken (DECL_W by default), null — none */
  weights: string | null;
  has_decl_w: boolean;
  extent: { min: [number, number, number]; max: [number, number, number] } | null;
  /** the defaults of the form: flat keys and `fan`, `map` objects (ТЗ 8.6.4, ADR 0022 §2) */
  defaults: Record<string, unknown>;
  ranges: Record<string, RangeInfo>;
}

/** The query of `GET /defaults` from the data of the form. */
export function defaultsQuery(d: DataBody): string {
  const q = new URLSearchParams({ set_id: String(d.set_id), table: d.table, field: d.field });
  if (d.domain) {
    if (d.domain.table) q.set("domain_table", d.domain.table);
    q.set("domain_field", d.domain.field);
    q.set("domain_values", d.domain.values.join(";"));
  }
  if (d.weights !== null) q.set("weights", d.weights === false ? "" : d.weights);
  if (d.transform !== "none") q.set("transform", d.transform);
  return q.toString();
}

export function readDefaults(raw: unknown): DefaultsAnswer {
  const o = obj(raw);
  const ext = obj(o.extent);
  const v3 = (x: unknown) => (Array.isArray(x) && x.length === 3 && x.every(isNum) ? (x as [number, number, number]) : null);
  const lo = v3(ext.min);
  const hi = v3(ext.max);
  const ranges: Record<string, RangeInfo> = {};
  for (const [k, r] of Object.entries(obj(o.ranges))) {
    const x = obj(r);
    if (isNum(x.lo)) ranges[k] = { lo: x.lo, hi: numOrNull(x.hi), lo_open: x.lo_open === true, integer: x.integer === true, inf: x.inf === true, unit: str(x.unit) ?? "" };
  }
  return {
    set_id: Number(o.set_id),
    table: String(o.table ?? ""),
    table_name: String(o.table_name ?? o.table ?? ""),
    field: String(o.field ?? ""),
    unit: str(o.unit),
    counts: readCounts(o.counts),
    sigma2: numOrNull(o.sigma2),
    sigma2_z: numOrNull(o.sigma2_z),
    median_length: numOrNull(o.median_length),
    weights: str(o.weights),
    has_decl_w: o.has_decl_w === true,
    extent: lo && hi ? { min: lo, max: hi } : null,
    defaults: obj(o.defaults),
    ranges,
  };
}

export const loadDefaults = async (d: DataBody): Promise<DefaultsAnswer> => readDefaults(await api.get(`${VARIO_API}/defaults?${defaultsQuery(d)}`));

// ---- the form of a calculation ----

/** One direction of the answer: a lag without pairs is `null` in every value and `n` 0. */
export interface DirectionCurve {
  /** 1, 2, 3 — an axis of the form `axes`; null — of `directions` */
  axis: 1 | 2 | 3 | null;
  az: number;
  plunge: number;
  az_tol: number | null;
  dip_tol: number | null;
  /** null — ∞ ("inf") */
  band_h: number | null;
  band_v: number | null;
  lags: number[];
  h: (number | null)[];
  n: number[];
  gamma: (number | null)[];
  rho: (number | null)[];
  pairwise: (number | null)[];
  n_pw: number[];
  /** N < min_pairs: a hollow mark, not in the fit */
  few: boolean[];
  /** the fan: the angle in the plane from a1 to a2 */
  theta?: number;
}

export interface DownholeCurve extends Omit<DirectionCurve, "axis" | "az" | "plunge" | "az_tol" | "dip_tol" | "band_h" | "band_v"> {
  lag: number;
  lag_tol: number;
}

export interface Sampled {
  n: number;
  of: number;
  seed: number;
}

export interface CalcResult {
  /** the key of the cached result; null — a preview */
  key: string | null;
  set_id: number | null;
  table: string | null;
  field: string | null;
  unit: string | null;
  transform: "none" | "nscore";
  sigma2: number | null;
  sigma2_z: number | null;
  origin: [number, number, number] | null;
  counts: Counts;
  sampled: Sampled | null;
  params: Record<string, unknown>;
  directions: DirectionCurve[];
  downhole: DownholeCurve | null;
  warnings: string[];
}

const band = (v: unknown): number | null => (v === "inf" ? null : numOrNull(v));

function readCurve(raw: unknown): Omit<DirectionCurve, "axis" | "az" | "plunge" | "az_tol" | "dip_tol" | "band_h" | "band_v"> {
  const o = obj(raw);
  return { lags: ints(o.lags), h: nums(o.h), n: ints(o.n), gamma: nums(o.gamma), rho: nums(o.rho), pairwise: nums(o.pairwise), n_pw: ints(o.n_pw), few: flags(o.few) };
}

export function readDirection(raw: unknown): DirectionCurve {
  const o = obj(raw);
  const axis = o.axis === 1 || o.axis === 2 || o.axis === 3 ? o.axis : null;
  const d: DirectionCurve = { axis, az: Number(o.az ?? 0), plunge: Number(o.plunge ?? 0), az_tol: numOrNull(o.az_tol), dip_tol: numOrNull(o.dip_tol), band_h: band(o.band_h), band_v: band(o.band_v), ...readCurve(o) };
  if (isNum(o.theta)) d.theta = o.theta;
  return d;
}

export function readSampled(raw: unknown): Sampled | null {
  const o = obj(raw);
  return isNum(o.n) && isNum(o.of) ? { n: o.n, of: o.of, seed: isNum(o.seed) ? o.seed : 0 } : null;
}

/** The result of `variography.experimental` (`job.result`) or the answer of `/preview`. */
export function readCalc(raw: unknown): CalcResult {
  const o = obj(raw);
  const dh = o.downhole === null || o.downhole === undefined ? null : obj(o.downhole);
  const origin = Array.isArray(o.origin) && o.origin.length === 3 && o.origin.every(isNum) ? (o.origin as [number, number, number]) : null;
  return {
    key: str(o.key),
    set_id: numOrNull(o.set_id),
    table: str(o.table),
    field: str(o.field),
    unit: str(o.unit),
    transform: o.transform === "nscore" ? "nscore" : "none",
    sigma2: numOrNull(o.sigma2),
    sigma2_z: numOrNull(o.sigma2_z),
    origin,
    counts: readCounts(o.counts),
    sampled: readSampled(o.sampled),
    params: obj(o.params),
    directions: Array.isArray(o.directions) ? o.directions.map(readDirection) : [],
    downhole: dh ? { lag: Number(dh.lag ?? 0), lag_tol: Number(dh.lag_tol ?? 0), ...readCurve(dh) } : null,
    warnings: Array.isArray(o.warnings) ? o.warnings.filter((w): w is string => typeof w === "string") : [],
  };
}

/** «Расчёт по выборке n′ из n проб» (e16-api.md); null — by every point. */
export function sampledText(s: Sampled | null): string | null {
  if (!s) return null;
  const f = (x: number) => x.toLocaleString("ru-RU");
  return `Расчёт по выборке ${f(s.n)} из ${f(s.of)} проб`;
}

export const startExperimental = (body: Record<string, unknown>): Promise<Started> => api.post<Started>(`${VARIO_API}/experimental`, body);
export const startMap = (body: Record<string, unknown>): Promise<Started> => api.post<Started>(`${VARIO_API}/map`, body);
export const previewCalc = async (body: Record<string, unknown>): Promise<CalcResult> => readCalc(await api.post(`${VARIO_API}/preview`, body));

// ---- fit, back, curve ----

export type FitWeights = "n_h" | "n" | "n_g2";
export const FIT_WEIGHTS: Record<FitWeights, string> = { n_h: "N / h̄", n: "N", n_g2: "N / γ²" };

export interface FitBody {
  key: string;
  type: VModel["type"];
  model: Record<string, unknown>;
  weights: FitWeights;
  fix: string[];
  fix_nugget: boolean;
}

export interface FitAnswer {
  /** the structures ascending by a1 */
  model: VModel;
  residual: number | null;
  n: number | null;
  starts: number | null;
  warnings: string[];
  /** the place of each answered structure in the request */
  order: number[];
  /** the paths fixed in the answer (renumbered by `order`) */
  fixed: string[];
}

const texts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((w): w is string => typeof w === "string") : []);

export function readFit(raw: unknown): FitAnswer {
  const r = obj(raw);
  return { model: readModel(r.model), residual: numOrNull(r.residual), n: numOrNull(r.n), starts: numOrNull(r.starts), warnings: texts(r.warnings), order: ints(r.order), fixed: texts(r.fixed) };
}

export const fitModel = async (body: FitBody): Promise<FitAnswer> => readFit(await api.post(`${VARIO_API}/fit`, body));

export interface BackAnswer {
  /** the model of Z */
  model: VModel;
  /** Σφ_n² / Var(Z); < 0,95 — `warning` */
  share: number | null;
  warning: string | null;
  residual: number | null;
  curves: { axis: number; h: (number | null)[]; gamma_z: (number | null)[]; gamma_fit: (number | null)[] }[];
  /** the answer as it came (`back` of the save) */
  raw: Record<string, unknown>;
}

export function readBack(raw: unknown): BackAnswer {
  const r = obj(raw);
  const curves = (Array.isArray(r.curves) ? r.curves : []).map((c) => {
    const o = obj(c);
    return { axis: Number(o.axis ?? 0), h: nums(o.h), gamma_z: nums(o.gamma_z), gamma_fit: nums(o.gamma_fit) };
  });
  return { model: readModel(r.model), share: numOrNull(r.share), warning: str(r.warning), residual: numOrNull(r.residual), curves, raw: r };
}

/** The tails of the back-transform (`backtr`; `stats.transforms.check_tails`): absent — linear. */
export interface Tails {
  ltail?: "linear" | "power" | null;
  utail?: "linear" | "power" | "hyperbolic" | null;
  ltpar?: number;
  utpar?: number;
}

export const backTransform = async (key: string, model: Record<string, unknown>, tails: Tails = {}): Promise<BackAnswer> => readBack(await api.post(`${VARIO_API}/back`, { key, model, ...tails }));

export interface CurveAnswer {
  directions: { axis: number | null; az: number; plunge: number; h: (number | null)[]; gamma: (number | null)[]; cov: (number | null)[] }[];
}

export function readCurveAnswer(raw: unknown): CurveAnswer {
  const r = obj(raw);
  const directions = (Array.isArray(r.directions) ? r.directions : []).map((d) => {
    const o = obj(d);
    return { axis: numOrNull(o.axis), az: Number(o.az ?? 0), plunge: Number(o.plunge ?? 0), h: nums(o.h), gamma: nums(o.gamma), cov: nums(o.cov) };
  });
  return { directions };
}

export const modelCurve = async (body: { model: Record<string, unknown>; directions?: { az: number; plunge: number }[]; h?: number[]; h_max?: number; n?: number }): Promise<CurveAnswer> => readCurveAnswer(await api.post(`${VARIO_API}/curve`, body));

// ---- stored models ----

export interface ModelRow {
  id: number;
  name: string;
  set_id: number | null;
  table: string | null;
  field: string | null;
  transform: string;
  type: string;
  color: string | null;
  sill: number | null;
  structures: number;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export function readModelRow(raw: unknown): ModelRow {
  const o = obj(raw);
  return {
    id: Number(o.id),
    name: String(o.name ?? ""),
    set_id: numOrNull(o.set_id),
    table: o.table === null || o.table === undefined ? null : String(o.table),
    field: str(o.field),
    transform: String(o.transform ?? "none"),
    type: String(o.type ?? "semivariogram"),
    color: str(o.color),
    sill: numOrNull(o.sill),
    structures: isNum(o.structures) ? o.structures : 0,
    stale: o.stale === true,
    stale_reason: str(o.stale_reason),
    source_lost: o.source_lost === true,
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export async function loadModels(setId?: number): Promise<ModelRow[]> {
  const r = obj(await api.get(`${VARIO_API}/models${setId === undefined ? "" : `?set_id=${setId}`}`));
  return (Array.isArray(r.models) ? r.models : []).map(readModelRow);
}

export interface StoredModel {
  id: number;
  name: string;
  color: string | null;
  /** `meta` as stored; `model` — its c0, structures, common_axes, type in the form of the window */
  meta: Record<string, unknown>;
  model: VModel;
  source: { set_id: number | null; table: string | null; field: string | null; domain: unknown; weights: unknown };
  points: CalcResult | null;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
}

export async function loadModel(id: number): Promise<StoredModel> {
  const r = obj(await api.get(`${VARIO_API}/models/${id}`));
  const meta = obj(r.meta);
  const src = obj(meta.source);
  return {
    id: Number(r.id),
    name: String(r.name ?? ""),
    color: str(r.color),
    meta,
    model: readModel(meta),
    source: { set_id: numOrNull(src.set_id), table: src.table === null || src.table === undefined ? null : String(src.table), field: str(src.field), domain: src.domain ?? null, weights: src.weights ?? null },
    points: r.points ? readCalc(r.points) : null,
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    source_lost: r.source_lost === true,
  };
}

export interface SaveBody {
  name?: string;
  model: Record<string, unknown>;
  exp_key: string;
  color?: string;
  /** `{weights, residual, n, fixed}` of the fit; null (PUT) — reset */
  fit?: { weights: FitWeights; residual: number | null; n: number | null; fixed: string[] } | null;
  /** the answer of `/back`; null (PUT) — reset */
  back?: Record<string, unknown> | null;
}

export const saveModel = (body: SaveBody): Promise<{ id: number; name: string }> => api.post(`${VARIO_API}/models`, body);
export const replaceModel = (id: number, body: Partial<SaveBody>): Promise<{ id: number; name: string }> => api.put(`${VARIO_API}/models/${id}`, body);
export const deleteModel = (id: number): Promise<{ deleted: number }> => api.del(`${VARIO_API}/models/${id}`);
export const rebuildModel = (id: number): Promise<Started> => api.post<Started>(`${VARIO_API}/models/${id}/rebuild`, {});

export type ExchangeFormat = "json" | "gslib";

/** Without `path` — the text (`{format, filename, text}`); with it — the file is written (`{format, path, bytes}`). */
export function exportModel(id: number, format: ExchangeFormat, path?: string): Promise<{ format: string; filename?: string; text?: string; path?: string; bytes?: number }> {
  const q = new URLSearchParams({ format });
  if (path) q.set("path", path);
  return api.get(`${VARIO_API}/models/${id}/export?${q.toString()}`);
}

/** The working model of a file or a text (not stored; no `format` — by the content); 422 `bad_file {line?}`. */
export async function importModel(body: { format?: ExchangeFormat; text?: string; path?: string }): Promise<VModel> {
  return readModel(obj(await api.post(`${VARIO_API}/models/import`, body)).model);
}

/** The format of a path by its extension: .json — JSON, else GSLIB. */
export const formatOfPath = (path: string): ExchangeFormat => (/\.json$/i.test(path.trim()) ? "json" : "gslib");
