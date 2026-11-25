/**
 * The client of the estimation of grades (ТЗ 8.8; ADR 0024 §11; docs/progress/e18-api.md §3–§8): every form is that of the
 * contract (`core/tests/data/estimate/contract_answers.json` until the routes of 18.13 give real answers), errors are
 * `{code, message, hint, param?}` in Russian (`param` — a path of the body: «samples.soft.dist», «variogram.model_id»).
 *
 * - `GET /geostat/estimate/defaults?model_id&set_id&table&field[&block_domain&domain_table&domain_field&domain_values&weights
 *   &variogram_id&method]` — the counts, the declustered means, the mapping of the labels, the model of variogram, the defaults
 *   and the ranges of the form;
 * - `GET/POST /estimations`, `GET/PUT/DELETE /estimations/{id}` — the rows of «Параметры оценки» (`PUT {expected_version, …}`;
 *   another version stored — 409 `version_conflict {version}`);
 * - `GET/POST /estimation-plans`, `GET/PUT/DELETE /estimation-plans/{id}`;
 * - `POST /estimate/run {plan_id | estimation_ids, expected_version, replace}` → `{source, id}` of the job `estimate.run`
 *   (409 `field_exists {field}` without `replace`).
 *
 * A gap of an answer stays `null` — never 0 (8.0.1). No React.
 */
import { api } from "../../shell/api";
import { startedOf, waitJob, type Started } from "../qaqc/setupApi";
import { refusalOf, type Refusal } from "../blocks/report/api";

export { refusalOf, startedOf, waitJob };
export type { Refusal, Started };

export const GEOSTAT_API = "/geostat";
export type Triple = [number, number, number];

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const numOrNull = (v: unknown): number | null => (isNum(v) ? v : null);
export const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
export const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

// ---- the methods ----

export type MethodKind = "nn" | "idw" | "sk" | "ok" | "ik";
export const METHODS: readonly MethodKind[] = ["nn", "idw", "sk", "ok", "ik"];
export const METHOD_TITLES: Record<MethodKind, string> = {
  nn: "Ближайший сосед (NN)",
  idw: "Обратные расстояния (IDW)",
  sk: "Простой кригинг (SK)",
  ok: "Обычный кригинг (OK)",
  ik: "Индикаторный кригинг (IK)",
};
/** The short name of a method (the default name and the prefix of the fields). */
export const METHOD_SHORT: Record<MethodKind, string> = { nn: "NN", idw: "IDW", sk: "SK", ok: "OK", ik: "IK" };

/** The scale of a model of variogram (Д-38): Z — grades, back — the model of Y with its back transform, Y — normal scores only. */
export type VarioScale = "z" | "back" | "y";

/** The words of the refusal `variogram_scale` (the same as the core's, `spec_parts.variogram_scale`). */
export const VARIOGRAM_SCALE_MESSAGE = "Модель построена в нормальных оценках";
export const VARIOGRAM_SCALE_HINT = "Выполните обратное преобразование модели или выберите модель в исходных единицах";

/** The status codes of a block (Д-27): the subscript is the code. */
export const STATUS_LABELS: readonly string[] = ["оценён", "вне домена", "мало проб", "вырожденная система"];

// ---- the ranges of the form (`ranges` of `defaults`) ----

export interface Range {
  lo: number | null;
  hi: number | null;
  lo_open: boolean;
  integer: boolean;
  unit: string;
}

function readRanges(raw: unknown): Record<string, Range> {
  const out: Record<string, Range> = {};
  for (const [k, r] of Object.entries(obj(raw))) {
    const x = obj(r);
    out[k] = { lo: numOrNull(x.lo), hi: numOrNull(x.hi), lo_open: x.lo_open === true, integer: x.integer === true, unit: str(x.unit) ?? "" };
  }
  return out;
}

// ---- GET /estimate/defaults ----

export interface DomainField {
  name: string;
  kind: "category" | "number";
  /** the labels of a category field */
  labels: string[];
  /** the values of a number field */
  values: number[];
}

export interface Counts {
  rows: number;
  missing: number;
  no_position: number;
  outside_domain: number;
  used: number;
}

export interface MeanPair {
  naive: number | null;
  declus: number | null;
}

export interface DomainValue extends MeanPair {
  value: string;
  n: number;
}

export interface SampleInfo {
  set_id: number;
  table: string;
  table_name: string;
  field: string;
  unit: string | null;
  /** the field of the weights taken (DECL_W by default), null — none */
  weights: string | null;
  counts: Counts;
  mean: MeanPair;
  domain: { table: string; field: string; values: DomainValue[] } | null;
}

export interface VariogramInfo {
  id: number;
  name: string;
  scale: VarioScale;
  share: number | null;
  warnings: string[];
  snapshot: Record<string, unknown> | null;
  sha: string | null;
}

export interface SearchInfo {
  from: string;
  ang1: number | null;
  ang2: number | null;
  ang3: number | null;
  r1: number | null;
  r2: number | null;
  r3: number | null;
}

export interface DefaultsAnswer {
  model: {
    id: number;
    name: string;
    version: number;
    rows: number;
    parents: number;
    /** the model has sub-blocks */
    sub: boolean;
    regular: boolean;
    rotation: number;
    size: Triple | null;
    domain_fields: DomainField[];
  };
  samples: SampleInfo;
  /** the label of a block → the values of the domain of the samples with the same name (no regard of the case) */
  mapping: Record<string, string[]> | null;
  variogram: VariogramInfo | null;
  search: SearchInfo | null;
  /** `spec.defaults_json()` as the core gave it */
  params: Record<string, unknown>;
  ranges: Record<string, Range>;
}

const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? ([v[0], v[1], v[2]] as Triple) : null);

export function readCounts(raw: unknown): Counts {
  const o = obj(raw);
  const n = (k: string) => (isNum(o[k]) ? (o[k] as number) : 0);
  return { rows: n("rows"), missing: n("missing"), no_position: n("no_position"), outside_domain: n("outside_domain"), used: n("used") };
}

const readMeans = (raw: unknown): MeanPair => ({ naive: numOrNull(obj(raw).naive), declus: numOrNull(obj(raw).declus) });

export function readDefaults(raw: unknown): DefaultsAnswer {
  const o = obj(raw);
  const m = obj(o.model);
  const s = obj(o.samples);
  const d = obj(s.domain);
  const v = obj(o.variogram);
  const se = obj(o.search);
  const mapping = o.mapping && typeof o.mapping === "object" && !Array.isArray(o.mapping) ? Object.fromEntries(Object.entries(obj(o.mapping)).map(([k, x]) => [k, strs(x)])) : null;
  return {
    model: {
      id: Number(m.id),
      name: String(m.name ?? ""),
      version: Number(m.version ?? 1),
      rows: Number(m.rows ?? 0),
      parents: Number(m.parents ?? 0),
      sub: m.sub === true,
      regular: m.regular !== false,
      rotation: numOrNull(m.rotation) ?? 0,
      size: triple(m.size),
      domain_fields: (Array.isArray(m.domain_fields) ? m.domain_fields : []).map((f) => {
        const x = obj(f);
        return { name: String(x.name ?? ""), kind: x.kind === "number" ? "number" : "category", labels: strs(x.labels), values: Array.isArray(x.values) ? x.values.filter(isNum) : [] } as DomainField;
      }),
    },
    samples: {
      set_id: Number(s.set_id),
      table: String(s.table ?? ""),
      table_name: String(s.table_name ?? s.table ?? ""),
      field: String(s.field ?? ""),
      unit: str(s.unit),
      weights: str(s.weights),
      counts: readCounts(s.counts),
      mean: readMeans(s.mean),
      domain: Object.keys(d).length
        ? { table: String(d.table ?? ""), field: String(d.field ?? ""), values: (Array.isArray(d.values) ? d.values : []).map((x) => ({ value: String(obj(x).value ?? ""), n: Number(obj(x).n ?? 0), ...readMeans(x) })) }
        : null,
    },
    mapping,
    variogram: Object.keys(v).length
      ? { id: Number(v.id), name: String(v.name ?? ""), scale: v.scale === "back" ? "back" : v.scale === "y" ? "y" : "z", share: numOrNull(v.share), warnings: strs(v.warnings), snapshot: v.snapshot ? obj(v.snapshot) : null, sha: str(v.sha) }
      : null,
    search: Object.keys(se).length ? { from: String(se.from ?? "manual"), ang1: numOrNull(se.ang1), ang2: numOrNull(se.ang2), ang3: numOrNull(se.ang3), r1: numOrNull(se.r1), r2: numOrNull(se.r2), r3: numOrNull(se.r3) } : null,
    params: obj(o.params),
    ranges: readRanges(o.ranges),
  };
}

/** The query of `defaults`; null — the data is not complete (model, set, table and field). */
export interface DefaultsQuery {
  model_id: number;
  set_id: number;
  table: string;
  field: string;
  block_domain?: string;
  domain_table?: string;
  domain_field?: string;
  domain_values?: string[];
  /** undefined — DECL_W when there is one; "" — no weights; a field */
  weights?: string;
  variogram_id?: number;
  method?: MethodKind;
}

export function defaultsPath(q: DefaultsQuery): string {
  const p = new URLSearchParams({ model_id: String(q.model_id), set_id: String(q.set_id), table: q.table, field: q.field });
  if (q.block_domain) p.set("block_domain", q.block_domain);
  if (q.domain_table) p.set("domain_table", q.domain_table);
  if (q.domain_field) p.set("domain_field", q.domain_field);
  if (q.domain_values?.length) p.set("domain_values", q.domain_values.join(";"));
  if (q.weights !== undefined) p.set("weights", q.weights);
  if (q.variogram_id !== undefined) p.set("variogram_id", String(q.variogram_id));
  if (q.method) p.set("method", q.method);
  return `${GEOSTAT_API}/estimate/defaults?${p.toString()}`;
}

export const loadDefaults = async (q: DefaultsQuery): Promise<DefaultsAnswer> => readDefaults(await api.get(defaultsPath(q)));

// ---- the rows «Параметры оценки» ----

export interface LastRun {
  at: string;
  /** the counts of blocks by the status 0…3 */
  by_status: number[];
}

export interface EstimationRow {
  id: number;
  name: string;
  model_id: number;
  method: MethodKind;
  prefix: string;
  label: string | null;
  version: number;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  plans: number[];
  last_run: LastRun | null;
  created_at: string | null;
  updated_at: string | null;
}

export function readRow(raw: unknown): EstimationRow {
  const o = obj(raw);
  const lr = obj(o.last_run);
  return {
    id: Number(o.id),
    name: String(o.name ?? ""),
    model_id: Number(o.model_id),
    method: (METHODS as readonly string[]).includes(String(o.method)) ? (o.method as MethodKind) : "ok",
    prefix: String(o.prefix ?? ""),
    label: str(o.label),
    version: Number(o.version ?? 1),
    stale: o.stale === true,
    stale_reason: str(o.stale_reason),
    source_lost: o.source_lost === true,
    plans: Array.isArray(o.plans) ? o.plans.filter(isNum) : [],
    last_run: Object.keys(lr).length ? { at: String(lr.at ?? ""), by_status: Array.isArray(lr.by_status) ? lr.by_status.map((x) => (isNum(x) ? x : 0)) : [] } : null,
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export async function loadEstimations(): Promise<EstimationRow[]> {
  const r = obj(await api.get(`${GEOSTAT_API}/estimations`));
  return (Array.isArray(r.estimations) ? r.estimations : []).map(readRow);
}

/** `GET /estimations/{id}`: `meta` is the checked parameters as stored (the form of the body, §3, plus `origin`, `inputs`, …). */
export interface EstimationInfo {
  id: number;
  name: string;
  version: number;
  meta: Record<string, unknown>;
  /** the fields of the model the row writes: the name, the key, the type, the unit */
  columns: { name: string; key: string; dtype: string; role: string | null; unit: string | null; description: string | null }[];
  /** the fields of the row that are in the model already */
  fields_exist: string[];
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  plans: number[];
}

export async function loadEstimation(id: number): Promise<EstimationInfo> {
  const r = obj(await api.get(`${GEOSTAT_API}/estimations/${id}`));
  return {
    id: Number(r.id),
    name: String(r.name ?? ""),
    version: Number(r.version ?? 1),
    meta: obj(r.meta),
    columns: (Array.isArray(r.columns) ? r.columns : []).map((c) => {
      const x = obj(c);
      return { name: String(x.name ?? ""), key: String(x.key ?? ""), dtype: String(x.dtype ?? ""), role: str(x.role), unit: str(x.unit), description: str(x.description) };
    }),
    fields_exist: strs(r.fields_exist),
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    source_lost: r.source_lost === true,
    plans: Array.isArray(r.plans) ? r.plans.filter(isNum) : [],
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

export const createEstimation = async (body: Record<string, unknown>): Promise<Saved> => readSaved(await api.post(`${GEOSTAT_API}/estimations`, body));
/** The keys of `body` replace the stored ones over the version read (`expected_version`). */
export const replaceEstimation = async (id: number, version: number, body: Record<string, unknown>): Promise<Saved> => readSaved(await api.put(`${GEOSTAT_API}/estimations/${id}`, { ...body, expected_version: version }));
export const deleteEstimationApi = (id: number): Promise<{ deleted: number; plans: number[] }> => api.del(`${GEOSTAT_API}/estimations/${id}`);

// ---- the plans ----

export interface PlanItem {
  id: number;
  name: string;
  method: MethodKind;
  prefix: string;
  label: string | null;
  stale: boolean;
}

export interface PlanRow {
  id: number;
  name: string;
  model_id: number;
  version: number;
  /** the ids of the rows in the order of the run (`GET /estimation-plans`) */
  items: number[];
}

const readItem = (raw: unknown): PlanItem => {
  const o = obj(raw);
  return { id: Number(o.id), name: String(o.name ?? ""), method: (METHODS as readonly string[]).includes(String(o.method)) ? (o.method as MethodKind) : "ok", prefix: String(o.prefix ?? ""), label: str(o.label), stale: o.stale === true };
};

export async function loadPlans(): Promise<PlanRow[]> {
  const r = obj(await api.get(`${GEOSTAT_API}/estimation-plans`));
  return (Array.isArray(r.plans) ? r.plans : []).map((p) => {
    const o = obj(p);
    return { id: Number(o.id), name: String(o.name ?? ""), model_id: Number(o.model_id), version: Number(o.version ?? 1), items: Array.isArray(o.items) ? o.items.filter(isNum) : [] };
  });
}

/** `GET /estimation-plans/{id}`: the plan with its rows in the order of the run (the form of `contract_answers.plan`). */
export interface PlanDetail {
  id: number;
  name: string;
  model_id: number;
  version: number;
  items: PlanItem[];
}

export function readPlan(raw: unknown): PlanDetail {
  const o = obj(raw);
  return { id: Number(o.id), name: String(o.name ?? ""), model_id: Number(o.model_id), version: Number(o.version ?? 1), items: (Array.isArray(o.items) ? o.items : []).map(readItem) };
}

export async function loadPlan(id: number): Promise<PlanDetail> {
  return readPlan(await api.get(`${GEOSTAT_API}/estimation-plans/${id}`));
}

/** `POST /estimation-plans {name?, model_id, items}` (0–100 rows, no repeats) → `{id, name, version}`. */
export const createPlan = async (body: { name?: string; model_id: number; items: number[] }): Promise<Saved> => readSaved(await api.post(`${GEOSTAT_API}/estimation-plans`, body));
/** `PUT /estimation-plans/{id} {expected_version, name?, items?}`; another version stored — 409 `version_conflict {version}`. */
export const replacePlan = async (id: number, version: number, body: { name?: string; items?: number[] }): Promise<Saved> => readSaved(await api.put(`${GEOSTAT_API}/estimation-plans/${id}`, { ...body, expected_version: version }));
/** The rows stay; only the plan goes. */
export const deletePlanApi = (id: number): Promise<{ deleted: number }> => api.del(`${GEOSTAT_API}/estimation-plans/${id}`);

// ---- the run ----

export interface RunBody {
  plan_id?: number;
  estimation_ids?: number[];
  expected_version: number;
  replace?: boolean;
}

export const startRun = async (body: RunBody): Promise<Started> => startedOf(obj(await api.post(`${GEOSTAT_API}/estimate/run`, body)));

/** The samples of a row as the run counted them (`counts.samples`: what was left out and why, how many were averaged). */
export interface RunSamples {
  rows: number;
  missing: number;
  no_position: number;
  outside_domain: number;
  used: number;
  /** the samples averaged into one (the same place) */
  merged: number;
  topcut: number;
  soft: number;
  no_weight: number;
}

export interface RunCounts {
  targets: number;
  /** the blocks by the status 0…3 (`STATUS_LABELS`) */
  by_status: number[];
  /** the blocks estimated by the pass 1, 2, … */
  by_pass: number[];
  /** the blocks with a singular system by the pass 1, 2, … */
  singular: number[];
  samples: RunSamples | null;
}

export interface RunRow {
  estimation_id: number;
  prefix: string;
  label: string | null;
  counts: RunCounts;
}

/** The rows of a plan that share a prefix: the core wrote them into the same fields (the values of the other domains stay). */
export interface RunMerged {
  prefix: string;
  estimation_ids: number[];
}

export interface RunResult {
  model_id: number;
  version: number;
  plan_id: number | null;
  fields: string[];
  rows: RunRow[];
  merged: RunMerged[];
  log: string[];
}

const nums = (v: unknown): number[] => (Array.isArray(v) ? v.map((n) => (isNum(n) ? n : 0)) : []);

function readSamples(raw: unknown): RunSamples | null {
  const o = obj(raw);
  if (!Object.keys(o).length) return null;
  const n = (k: string) => (isNum(o[k]) ? (o[k] as number) : 0);
  return { rows: n("rows"), missing: n("missing"), no_position: n("no_position"), outside_domain: n("outside_domain"), used: n("used"), merged: n("merged"), topcut: n("topcut"), soft: n("soft"), no_weight: n("no_weight") };
}

export function readRun(raw: unknown): RunResult | null {
  const o = obj(raw);
  if (!isNum(o.model_id) || !Array.isArray(o.fields)) return null;
  return {
    model_id: o.model_id,
    version: Number(o.version ?? 0),
    plan_id: numOrNull(o.plan_id),
    fields: strs(o.fields),
    rows: (Array.isArray(o.rows) ? o.rows : []).map((r) => {
      const x = obj(r);
      const c = obj(x.counts);
      return { estimation_id: Number(x.estimation_id), prefix: String(x.prefix ?? ""), label: str(x.label), counts: { targets: Number(c.targets ?? 0), by_status: nums(c.by_status), by_pass: nums(c.by_pass), singular: nums(c.singular), samples: readSamples(c.samples) } };
    }),
    merged: (Array.isArray(o.merged) ? o.merged : []).map((m) => ({ prefix: String(obj(m).prefix ?? ""), estimation_ids: Array.isArray(obj(m).estimation_ids) ? (obj(m).estimation_ids as unknown[]).filter(isNum) : [] })),
    log: strs(o.log),
  };
}
