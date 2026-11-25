/**
 * The client of the implicit modelling (ТЗ 8.9; ADR 0026 «API»; docs/progress/e19-api.md §3–§8): `/api/v1/implicit`. The forms are those
 * of the contract, written by hand (there is no generation): every answer is read by a reader that keeps a gap as `null` — never 0 (8.0.1) —
 * and takes a missing key as «nothing», not as a made-up number. Errors are `{code, message, hint, param?}` in Russian (`param` — a path
 * of the body: «h», «pou.n_min», «boundary.box.4», «groups.2.codes.0»).
 *
 * - `GET /defaults` → `{params, ranges}` — the defaults and the ranges of the form (8.9.4);
 * - `GET /models`, `GET/PUT/DELETE /models/{id}`, `POST /models` — the models (`PUT {expected_version, …}`; another version stored — 409
 *   `version_conflict {version}`; the keys of the top level replace the stored ones, `groups` — the whole list);
 * - `POST /models/{id}/groups`, `PUT /models/{id}/groups/{gid}` — a group; `POST /models/{id}/points/preview` — the data points of a group;
 * - `POST /models/{id}/build`, `…/update` → `{source, id}` of the job `implicit.build` / `implicit.update`; `POST …/to_wireframe`.
 *
 * No React.
 */
import { api } from "../../shell/api";
import { isNum, numOrNull, obj, refusalOf, startedOf, str, waitJob, type Range, type Refusal, type Started } from "../estimation/api";

export { numOrNull, obj, refusalOf, startedOf, waitJob };
export type { Range, Refusal, Started };

export const IMPLICIT_API = "/implicit";

const num = (v: unknown, dflt: number): number => (isNum(v) ? v : dflt);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const countOf = (v: unknown): number => (Array.isArray(v) ? v.length : num(v, 0));

// ---- the defaults and the ranges (8.9.4) ----

export type KernelKind = "linear" | "cubic" | "wendland";
export const KERNELS: readonly KernelKind[] = ["linear", "cubic", "wendland"];
export const KERNEL_TITLES: Record<KernelKind, string> = {
  linear: "Линейное (−r)",
  cubic: "Кубическое (r³)",
  wendland: "Конечный радиус (Вендланд)",
};

export type SmoothingMode = "manual" | "auto";
export type ClipKind = "none" | "boundary" | "wireframe" | "dtm";
export const CLIP_TITLES: Record<ClipKind, string> = {
  none: "не обрезать",
  boundary: "границей модели",
  wireframe: "каркасом",
  dtm: "ЦТМ",
};

export interface ModelDefaults {
  h_divisor: number;
  levels: number;
  kernel: KernelKind;
  s_contact: number;
  s_grade: number;
  cond_target: number;
  n_patch: number;
  n_min: number;
  delta: number;
  d_max: number;
  step: number;
  v_min: number;
  clip: ClipKind;
  /** the limits of the form: the groups, the codes of a group, the manual layers */
  limits: { groups: number; codes: number; manual: number };
}

export interface DefaultsAnswer {
  params: ModelDefaults;
  ranges: Record<string, Range>;
}

function readRanges(raw: unknown): Record<string, Range> {
  const out: Record<string, Range> = {};
  for (const [k, r] of Object.entries(obj(raw))) {
    const x = obj(r);
    out[k] = { lo: numOrNull(x.lo), hi: numOrNull(x.hi), lo_open: x.lo_open === true, integer: x.integer === true, unit: str(x.unit) ?? "" };
  }
  return out;
}

const kernelOf = (v: unknown): KernelKind => (v === "cubic" || v === "wendland" ? v : "linear");
const clipOf = (v: unknown): ClipKind => (v === "none" || v === "wireframe" || v === "dtm" ? v : "boundary");

/** `GET /defaults`. A key the core did not give stays the number of the table 8.9.4 (they are the same). */
export function readDefaults(raw: unknown): DefaultsAnswer {
  const o = obj(raw);
  const p = obj(o.params);
  const sm = obj(p.smoothing);
  const pou = obj(p.pou);
  const data = obj(p.data);
  const post = obj(p.post);
  const lim = obj(p.limits);
  return {
    params: {
      h_divisor: num(p.h_divisor, 200),
      levels: num(p.levels, 2),
      kernel: kernelOf(p.kernel),
      s_contact: num(sm.s_contact, 0),
      s_grade: num(sm.s_grade, 0.05),
      cond_target: num(sm.cond_target, 1e8),
      n_patch: num(pou.n_patch, 1000),
      n_min: num(pou.n_min, 100),
      delta: num(pou.delta, 0.25),
      d_max: num(data.d_max, 30),
      step: num(data.step, 5),
      v_min: num(post.v_min, 0),
      clip: clipOf(post.clip),
      limits: { groups: num(lim.groups, 50), codes: num(lim.codes, 1000), manual: num(lim.manual, 100) },
    },
    ranges: readRanges(o.ranges),
  };
}

export const loadDefaults = async (): Promise<DefaultsAnswer> => readDefaults(await api.get(`${IMPLICIT_API}/defaults`));

// ---- the models ----

export type ModelState = "ready" | "building" | "error";
const stateOf = (v: unknown): ModelState => (v === "building" || v === "error" ? v : "ready");

export interface ModelStats {
  points: number | null;
  patches: number | null;
  seconds: number | null;
}

export interface ModelRow {
  id: number;
  name: string;
  version: number;
  state: ModelState;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  groups: number;
  outputs: number;
  /** null — not built yet */
  stats: ModelStats | null;
  created_at: string | null;
  updated_at: string | null;
}

export function readRow(raw: unknown): ModelRow {
  const r = obj(raw);
  const s = r.stats === null || r.stats === undefined ? null : obj(r.stats);
  return {
    id: num(r.id, 0),
    name: str(r.name) ?? "",
    version: num(r.version, 0),
    state: stateOf(r.state),
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    source_lost: r.source_lost === true,
    groups: countOf(r.groups),
    outputs: countOf(r.outputs),
    stats: s ? { points: numOrNull(s.points), patches: numOrNull(s.patches), seconds: numOrNull(s.seconds) } : null,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

export async function loadModels(): Promise<ModelRow[]> {
  const r = obj(await api.get(`${IMPLICIT_API}/models`));
  return (Array.isArray(r.models) ? r.models : []).map(readRow);
}

/** An output of a model (a surface, a body or — Э19б — a line): the wireframe it wrote. */
export interface OutputInfo {
  wireframe_id: number | null;
  kind: string;
  /** the id of the group; null — «Прочее» */
  group: number | null;
  wing: number | null;
  threshold: number | null;
  name: string;
  volume: number | null;
  area: number | null;
  triangles: number | null;
}

export function readOutput(raw: unknown): OutputInfo {
  const o = obj(raw);
  return {
    wireframe_id: numOrNull(o.wireframe_id),
    kind: str(o.kind) ?? "",
    group: numOrNull(o.group),
    wing: numOrNull(o.wing),
    threshold: numOrNull(o.threshold),
    name: str(o.name) ?? "",
    volume: numOrNull(o.volume),
    area: numOrNull(o.area),
    triangles: numOrNull(o.triangles),
  };
}

const readOutputs = (v: unknown): OutputInfo[] => (Array.isArray(v) ? v.map(readOutput) : []);

export interface ErrorInfo {
  code: string;
  message: string;
  hint: string;
  group: number | null;
}

function readError(raw: unknown): ErrorInfo | null {
  const e = obj(raw);
  return typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "", group: numOrNull(e.group) } : null;
}

export interface ModelInfo {
  id: number;
  name: string;
  version: number;
  /** the parameters as stored (§2: `spec.canonical()` + the results) */
  meta: Record<string, unknown>;
  state: ModelState;
  error: ErrorInfo | null;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  outputs: OutputInfo[];
  created_at: string | null;
  updated_at: string | null;
}

export function readInfo(raw: unknown): ModelInfo {
  const r = obj(raw);
  return {
    id: num(r.id, 0),
    name: str(r.name) ?? "",
    version: num(r.version, 0),
    meta: obj(r.meta),
    state: stateOf(r.state),
    error: readError(r.error),
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    source_lost: r.source_lost === true,
    outputs: readOutputs(r.outputs),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

export const loadModel = async (id: number): Promise<ModelInfo> => readInfo(await api.get(`${IMPLICIT_API}/models/${id}`));

export interface Saved {
  id: number;
  name: string;
  version: number;
}

export function readSaved(raw: unknown): Saved {
  const r = obj(raw);
  return { id: num(r.id, 0), name: str(r.name) ?? "", version: num(r.version, 0) };
}

export const createModel = async (body: Record<string, unknown>): Promise<Saved> => readSaved(await api.post(`${IMPLICIT_API}/models`, body));
export const replaceModel = async (id: number, version: number, body: Record<string, unknown>): Promise<Saved> => readSaved(await api.put(`${IMPLICIT_API}/models/${id}`, { ...body, expected_version: version }));
export const deleteModelApi = (id: number): Promise<{ deleted: number; wireframes: number[] }> => api.del(`${IMPLICIT_API}/models/${id}`);

// ---- the groups ----

/** A group as the core keeps it (§4): the kind-specific parameters stay as they are, the wizard does not touch them. */
export interface GroupMeta {
  id: number | null;
  name: string;
  type: string;
  codes: string[];
  relation: string;
  faults: number[];
  params: Record<string, unknown>;
}

export function readGroup(raw: unknown): GroupMeta {
  const g = obj(raw);
  return {
    id: numOrNull(g.id),
    name: str(g.name) ?? "",
    type: str(g.type) ?? "domain",
    codes: Array.isArray(g.codes) ? g.codes.map((c) => String(c)) : [],
    relation: str(g.relation) ?? "conformable",
    faults: Array.isArray(g.faults) ? g.faults.filter(isNum) : [],
    params: obj(g.params),
  };
}

export interface GroupSaved extends Saved {
  group: GroupMeta;
}

export const addGroup = async (id: number, version: number, group: Record<string, unknown>): Promise<GroupSaved> => {
  const r = obj(await api.post(`${IMPLICIT_API}/models/${id}/groups`, { expected_version: version, group }));
  return { ...readSaved(r), group: readGroup(r.group) };
};

export const replaceGroup = async (id: number, groupId: number, version: number, group: Record<string, unknown>): Promise<GroupSaved> => {
  const r = obj(await api.put(`${IMPLICIT_API}/models/${id}/groups/${groupId}`, { expected_version: version, group }));
  return { ...readSaved(r), group: readGroup(r.group) };
};

// ---- the points of a group (without the solution) ----

export interface PreviewCounts {
  contact: number;
  inside: number;
  outside: number;
  manual: number;
  value: number;
  merged: number;
  dropped_log: number;
  no_position: number;
}

export interface PreviewAnswer {
  group: number | null;
  n: number;
  truncated: boolean;
  counts: PreviewCounts;
  notes: string[];
  /** the columns, in the canonical order; a gap — `null` */
  points: { x: (number | null)[]; y: (number | null)[]; z: (number | null)[]; f: (number | null)[]; kind: number[] };
}

const column = (v: unknown): (number | null)[] => (Array.isArray(v) ? v.map((x) => (isNum(x) ? x : null)) : []);

export function readPreview(raw: unknown): PreviewAnswer {
  const r = obj(raw);
  const c = obj(r.counts);
  const n = (k: string) => num(c[k], 0);
  const p = obj(r.points);
  return {
    group: numOrNull(r.group),
    n: num(r.n, 0),
    truncated: r.truncated === true,
    counts: { contact: n("contact"), inside: n("inside"), outside: n("outside"), manual: n("manual"), value: n("value"), merged: n("merged"), dropped_log: n("dropped_log"), no_position: n("no_position") },
    notes: strs(r.notes),
    points: { x: column(p.x), y: column(p.y), z: column(p.z), f: column(p.f), kind: Array.isArray(p.kind) ? p.kind.filter(isNum) : [] },
  };
}

/** `POST /models/{id}/points/preview {group_id | group, limit?}`: the points of a stored group or of a group not saved yet. */
export const previewPoints = async (id: number, body: { group_id?: number; group?: Record<string, unknown>; limit?: number }): Promise<PreviewAnswer> => readPreview(await api.post(`${IMPLICIT_API}/models/${id}/points/preview`, body));

// ---- the jobs: build, update ----

export const startBuild = async (id: number, version: number, threads?: number): Promise<Started> => startedOf(obj(await api.post(`${IMPLICIT_API}/models/${id}/build`, { expected_version: version, ...(threads ? { threads } : {}) })));
export const startUpdate = async (id: number, version: number, threads?: number): Promise<Started> => startedOf(obj(await api.post(`${IMPLICIT_API}/models/${id}/update`, { expected_version: version, ...(threads ? { threads } : {}) })));

export interface BuildStats {
  points: number | null;
  patches: number | null;
  max_residual: number | null;
  cond_max: number | null;
  eta_max: number | null;
  seconds: number | null;
  reused: { patches: number; bricks: number };
}

export interface BuildResult {
  model_id: number;
  version: number;
  state: ModelState;
  outputs: OutputInfo[];
  stats: BuildStats;
  journal: string[];
  /** a failed checks of 8.9.3 п. 7 (`iso_check`): the job is done, the model is in the state `error`, no wireframes are written */
  error: ErrorInfo | null;
}

export function readBuildResult(raw: unknown): BuildResult | null {
  const r = obj(raw);
  if (!isNum(r.model_id)) return null;
  const s = obj(r.stats);
  const reused = obj(r.reused ?? s.reused);
  return {
    model_id: r.model_id,
    version: num(r.version, 0),
    state: stateOf(r.state),
    outputs: readOutputs(r.outputs),
    stats: { points: numOrNull(s.points), patches: numOrNull(s.patches), max_residual: numOrNull(s.max_residual), cond_max: numOrNull(s.cond_max), eta_max: numOrNull(s.eta_max), seconds: numOrNull(s.seconds), reused: { patches: num(reused.patches, 0), bricks: num(reused.bricks, 0) } },
    journal: strs(r.journal),
    error: readError(r.error),
  };
}

export const toWireframeApi = async (id: number, wireframeId: number, name?: string): Promise<{ wireframe_id: number; name: string }> => {
  const r = obj(await api.post(`${IMPLICIT_API}/models/${id}/to_wireframe`, { wireframe_id: wireframeId, ...(name ? { name } : {}) }));
  return { wireframe_id: num(r.wireframe_id, 0), name: str(r.name) ?? "" };
};

// ---- the trends of the list (a global one — A = trend.matrix, a structural one — A_j; 8.9.2, Д-15) ----

export interface TrendChoice {
  id: number;
  name: string;
  /** `global` / `structural` */
  kind: string;
}

export async function loadTrendChoices(): Promise<TrendChoice[]> {
  const r = obj(await api.get("/geostat/trends"));
  return (Array.isArray(r.trends) ? r.trends : []).map((t) => ({ id: num(obj(t).id, 0), name: str(obj(t).name) ?? "", kind: str(obj(t).kind) ?? "global" }));
}
