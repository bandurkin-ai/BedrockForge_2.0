/**
 * The client of the simulation (Э18б; ТЗ 8.8.1, Д-23; docs/progress/e18-api.md §9): every form is that of the real answers
 * (`core/tests/data/simulate/answers.json`), errors are `{code, message, hint, param?}` in Russian (`param` — a path of the body:
 * «samples.field», «method.ik.thresholds», «secondary.rho»).
 *
 * - `GET/POST /simulations`, `GET/PUT/DELETE /simulations/{id}` — the rows «Моделирование» (`PUT {expected_version, …}`; another version
 *   stored — 409 `version_conflict {version}`; the same name — 409 `name_taken`);
 * - `POST /simulate/run {simulation | simulation_id, expected_version?}` → `{source, id}` of the job `simulate.run`;
 * - `GET /simulations/{id}/summary` — the summary of the realizations (409 `no_realizations` before the first run);
 * - `POST /simulations/{id}/post {expected_version (of the block model), prefix?, stats?, thresholds?, replace?}` → `{source, id}` of the
 *   job `simulate.post` (409 `field_exists {field}`, `version_conflict`).
 *
 * A gap of an answer stays `null` — never 0 (8.0.1). The realizations are an object of their own (ADR 0024, № 82): only the post-processing
 * writes to the block model. No React.
 */
import { api } from "../../shell/api";
import { refusalOf, type Refusal } from "../blocks/report/api";
import { GEOSTAT_API, isNum, numOrNull, obj, str } from "../estimation/api";
import { startedOf, waitJob, type Started } from "../qaqc/setupApi";

export { refusalOf, startedOf, waitJob };
export type { Refusal, Started };

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const nums = (v: unknown): number[] => (Array.isArray(v) ? v.filter(isNum) : []);

// ---- the kinds ----

export type SimKind = "sgs" | "sis" | "cosgs";
export const SIM_KINDS: readonly SimKind[] = ["sgs", "sis", "cosgs"];
export const KIND_TITLES: Record<SimKind, string> = {
  sgs: "SGS — последовательное гауссово моделирование",
  sis: "SIS — последовательное индикаторное моделирование",
  cosgs: "Ко-SGS — SGS со вторичной переменной",
};
/** The short name of a kind (the default name of a row, the badge in the tree). */
export const KIND_SHORT: Record<SimKind, string> = { sgs: "SGS", sis: "SIS", cosgs: "Ко-SGS" };
export const kindOf = (v: unknown): SimKind => ((SIM_KINDS as readonly unknown[]).includes(v) ? (v as SimKind) : "sgs");

// ---- the rows ----

export interface SimLastRun {
  at: string;
  nodes: number;
  realizations: number;
}

export interface SimulationRow {
  id: number;
  name: string;
  kind: SimKind;
  model_id: number;
  label: string | null;
  field: string;
  n_real: number;
  seed: number;
  version: number;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  last_run: SimLastRun | null;
  created_at: string | null;
  updated_at: string | null;
}

export function readRow(raw: unknown): SimulationRow {
  const o = obj(raw);
  const lr = obj(o.last_run);
  return {
    id: Number(o.id),
    name: String(o.name ?? ""),
    kind: kindOf(o.kind),
    model_id: Number(o.model_id),
    label: str(o.label),
    field: String(o.field ?? ""),
    n_real: Number(o.n_real ?? 0),
    seed: Number(o.seed ?? 0),
    version: Number(o.version ?? 1),
    stale: o.stale === true,
    stale_reason: str(o.stale_reason),
    source_lost: o.source_lost === true,
    last_run: Object.keys(lr).length ? { at: String(lr.at ?? ""), nodes: Number(lr.nodes ?? 0), realizations: Number(lr.realizations ?? 0) } : null,
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export async function loadSimulations(): Promise<SimulationRow[]> {
  const r = obj(await api.get(`${GEOSTAT_API}/simulations`));
  return (Array.isArray(r.simulations) ? r.simulations : []).map(readRow);
}

/** `GET /simulations/{id}`: `meta` is the checked parameters as stored (§9, plus `origin`, `inputs`, `last_run`); `file` — the blob of the realizations, null before the first run. */
export interface SimulationInfo {
  id: number;
  name: string;
  version: number;
  meta: Record<string, unknown>;
  file: { role: string; sha: string; rows: number; columns: string[] } | null;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
}

export async function loadSimulation(id: number): Promise<SimulationInfo> {
  const r = obj(await api.get(`${GEOSTAT_API}/simulations/${id}`));
  const f = obj(r.file);
  return {
    id: Number(r.id),
    name: String(r.name ?? ""),
    version: Number(r.version ?? 1),
    meta: obj(r.meta),
    file: Object.keys(f).length ? { role: String(f.role ?? "real"), sha: String(f.sha ?? ""), rows: Number(f.rows ?? 0), columns: strs(f.columns) } : null,
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    source_lost: r.source_lost === true,
  };
}

export interface Saved {
  id: number;
  name: string;
  version: number;
}

const readSaved = (raw: unknown): Saved => {
  const o = obj(raw);
  return { id: Number(o.id), name: String(o.name ?? ""), version: Number(o.version ?? 1) };
};

export const createSimulation = async (body: Record<string, unknown>): Promise<Saved> => readSaved(await api.post(`${GEOSTAT_API}/simulations`, body));
/** The keys of `body` replace the stored ones over the version read (`expected_version`). */
export const replaceSimulation = async (id: number, version: number, body: Record<string, unknown>): Promise<Saved> => readSaved(await api.put(`${GEOSTAT_API}/simulations/${id}`, { ...body, expected_version: version }));
export const deleteSimulationApi = (id: number): Promise<{ deleted: number }> => api.del(`${GEOSTAT_API}/simulations/${id}`);

// ---- what a stored row says about its categories and secondary variable ----

export interface Categories {
  codes: number[];
  labels: string[];
}

/** The categories of a categorical SIS (`meta.method.ik.categories`, the labels or the codes themselves); null — not categorical. */
export function categoriesOf(meta: Record<string, unknown>): Categories | null {
  const ik = obj(obj(meta.method).ik);
  const codes = nums(ik.categories);
  if (!codes.length) return null;
  const given = strs(ik.labels);
  return { codes, labels: codes.map((c, i) => (given[i] ?? "").trim() || String(c)) };
}

/** The thresholds of a continuous SIS (`meta.method.ik.thresholds`); [] — none. */
export const thresholdsOfMeta = (meta: Record<string, unknown>): number[] => nums(obj(obj(meta.method).ik).thresholds);

// ---- the run ----

export interface RunBody {
  simulation?: Record<string, unknown>;
  simulation_id?: number;
  expected_version?: number;
}

export const startRun = async (body: RunBody): Promise<Started> => startedOf(obj(await api.post(`${GEOSTAT_API}/simulate/run`, body)));

export interface CondStat {
  min: number | null;
  mean: number | null;
  max: number | null;
}

export interface SimCounts {
  /** nodes whose system was singular */
  degenerate: number;
  /** nodes with no datum at all (simulated from the prior) */
  free: number;
  /** nodes that fall on a sample */
  on_sample: number;
  /** co-SGS: nodes with a gap of the secondary variable (simulated as SGS) */
  gap: number | null;
}

export interface SecondaryRun {
  field: string;
  rho: number | null;
  rho_used: number | null;
  /** the pairs sample–node ρ was computed from (0 — ρ was given) */
  pairs: number;
  gaps: number;
}

export interface RunResult {
  simulation_id: number;
  nodes: number;
  realizations: number;
  seconds: number | null;
  counts: SimCounts;
  cond: CondStat | null;
  secondary: SecondaryRun | null;
  log: string[];
  /** the seconds of the stages of the run; {} — the core did not tell */
  stages: Record<string, number>;
}

const readCond = (raw: unknown): CondStat | null => {
  const c = obj(raw);
  return Object.keys(c).length ? { min: numOrNull(c.min), mean: numOrNull(c.mean), max: numOrNull(c.max) } : null;
};

function readCounts(raw: unknown): SimCounts {
  const c = obj(raw);
  const n = (k: string) => (isNum(c[k]) ? (c[k] as number) : 0);
  return { degenerate: n("degenerate"), free: n("free"), on_sample: n("on_sample"), gap: isNum(c.gap) ? c.gap : null };
}

export function readRun(raw: unknown): RunResult | null {
  const o = obj(raw);
  if (!isNum(o.simulation_id)) return null;
  const s = obj(o.secondary);
  return {
    simulation_id: o.simulation_id,
    nodes: Number(o.nodes ?? 0),
    realizations: Number(o.realizations ?? 0),
    seconds: numOrNull(o.seconds),
    counts: readCounts(o.counts),
    cond: readCond(obj(o.counts).cond),
    secondary: Object.keys(s).length ? { field: String(s.field ?? ""), rho: numOrNull(s.rho), rho_used: numOrNull(s.rho_used), pairs: Number(s.pairs ?? 0), gaps: Number(s.gaps ?? 0) } : null,
    log: strs(o.log),
    stages: Object.fromEntries(Object.entries(obj(o.stages)).filter(([, v]) => isNum(v))) as Record<string, number>,
  };
}

// ---- the summary of the realizations ----

/** A realization of a continuous variable: n nodes, mean, σ, min, max (a gap — null). */
export interface RealStat {
  n: number;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
}

export interface Summary {
  simulation_id: number;
  nodes: number;
  realizations: number;
  seconds: number | null;
  counts: SimCounts;
  cond: CondStat | null;
  samples: { rows: number; used: number } | null;
  /** continuous: the statistics of each realization; null for categories */
  per: RealStat[] | null;
  all: { mean: number | null; std: number | null; min: number | null; max: number | null } | null;
  /** categories: the shares of every category by realization, in the order of the codes */
  perShares: { n: number; shares: (number | null)[] }[] | null;
  codes: number[] | null;
  shares: (number | null)[] | null;
  at: string | null;
}

export function readSummary(raw: unknown): Summary {
  const o = obj(raw);
  const s = obj(o.summary);
  const all = obj(s.all);
  const per = Array.isArray(s.per) ? s.per.map(obj) : [];
  const cat = Array.isArray(all.codes);
  const sm = obj(o.samples);
  const shares = (v: unknown): (number | null)[] => (Array.isArray(v) ? v.map(numOrNull) : []);
  return {
    simulation_id: Number(o.simulation_id),
    nodes: Number(o.nodes ?? 0),
    realizations: Number(o.realizations ?? 0),
    seconds: numOrNull(o.seconds),
    counts: readCounts(o.counts),
    cond: readCond(o.cond),
    samples: Object.keys(sm).length ? { rows: Number(sm.rows ?? 0), used: Number(sm.used ?? 0) } : null,
    per: cat ? null : per.map((p) => ({ n: Number(p.n ?? 0), mean: numOrNull(p.mean), std: numOrNull(p.std), min: numOrNull(p.min), max: numOrNull(p.max) })),
    all: cat || !Object.keys(all).length ? null : { mean: numOrNull(all.mean), std: numOrNull(all.std), min: numOrNull(all.min), max: numOrNull(all.max) },
    perShares: cat ? per.map((p) => ({ n: Number(p.n ?? 0), shares: shares(p.shares) })) : null,
    codes: cat ? nums(all.codes) : null,
    shares: cat ? shares(all.shares) : null,
    at: str(o.at),
  };
}

export async function loadSummary(id: number): Promise<Summary> {
  return readSummary(await api.get(`${GEOSTAT_API}/simulations/${id}/summary`));
}

// ---- the post-processing ----

export const POST_STATS = ["mean", "std", "p10", "p50", "p90", "prob"] as const;
export const CAT_STATS = ["prob", "mode"] as const;
export type ContStat = (typeof POST_STATS)[number];
export type PostStat = ContStat | (typeof CAT_STATS)[number];

export interface PostBody {
  expected_version: number;
  prefix?: string;
  stats?: string[];
  thresholds?: number[];
  replace?: boolean;
}

export const startPost = async (id: number, body: PostBody): Promise<Started> => startedOf(obj(await api.post(`${GEOSTAT_API}/simulations/${id}/post`, body)));

export interface PostResult {
  simulation_id: number;
  model_id: number;
  version: number;
  prefix: string;
  fields: string[];
  nodes: number;
  realizations: number;
  log: string[];
  seconds: number | null;
}

export function readPost(raw: unknown): PostResult | null {
  const o = obj(raw);
  if (!isNum(o.model_id) || !Array.isArray(o.fields)) return null;
  return { simulation_id: Number(o.simulation_id), model_id: o.model_id, version: Number(o.version ?? 0), prefix: String(o.prefix ?? ""), fields: strs(o.fields), nodes: Number(o.nodes ?? 0), realizations: Number(o.realizations ?? 0), log: strs(o.log), seconds: numOrNull(o.seconds) };
}
