/**
 * The client of the building of a block model (ТЗ 8.7.3, 8.7.5; ADR 0023 §6, §9, §14, decisions 8, 9, 14; docs/progress/
 * e17-api.md §6, §7, §9, §15; core — `blocks/{routes_ops, fill/routes, jobs, regularize}.py`). The forms are those of the
 * answers of the core (a run through TestClient: `core/tests/data/blocks/build_window_answers.json`,
 * `core/tests/test_blocks_build_window_answers.py`):
 *
 * - `POST /blocks/prototype/preview` → `{grid, parents, disk, extent, frame}` (synchronous, nothing is written);
 * - `POST /blocks/prototype`, `POST /blocks/fill`, `POST /blocks/{id}/code`, `POST /blocks/{id}/regularize`,
 *   `POST /blocks/{id}/rebuild` → `{source, id}` of the job (`blocks.prototype` quick; `blocks.fill`, `blocks.code`,
 *   `blocks.regularize` heavy); the job — `GET /jobs/{source}/{id}` → `{status, progress, message, result, error}`;
 * - the refusals before the job are `{code, message, hint, param?}` in Russian (404 / 409 / 422 / 423).
 *
 * A gap is `null` (`totals.before.tonnes` without density), never 0. No React.
 */
import { api, CoreError } from "../../../shell/api";
import { startedOf, waitJob, type Started } from "../../qaqc/setupApi";
import { refusalOf, type Refusal } from "../report/api";

export { refusalOf, startedOf, waitJob };
export type { Refusal, Started };

export type Triple = [number, number, number];

export const MAX_PARENTS = 2_000_000_000;
export const MAX_LEVEL = 7;
export const MAX_M = 16;

/** The stages of the jobs (the `message` of the job; e17-api.md §15), in the order the core goes through them. */
export const STAGES = {
  prototype: ["Охват", "Запись"],
  fill: ["Каркасы", "Столбцы", "Граничные родители", "Дробление", "Слияние", "Запись"],
  code: ["Каркасы", "Граничные родители", "Дробление", "Запись"],
  regularize: ["Чтение", "Перекрытия", "Запись"],
} as const;

export type Mode = "inside" | "outside" | "both";
export const MODES: readonly Mode[] = ["inside", "outside", "both"];

// ---- the prototype ----

/** The body of `POST /blocks/prototype[/preview]`: exactly one of `object_ids` and `extent`; the rest is the default of the core when absent. */
export interface PrototypeBody {
  size: Triple;
  object_ids?: number[];
  /** [xmin, ymin, zmin, xmax, ymax, zmax] */
  extent?: number[];
  /** a number (all axes) or [x, y, z] */
  pad?: number | Triple;
  /** φ about Z, degrees */
  rotation?: number;
  /** the levels of the sub-blocks L by axes, 0…7 */
  levels?: Triple;
  /** «Все родители заняты» (№ 66): the model has a row for every parent */
  full?: boolean;
  name?: string;
  folder?: string;
}

export interface PreviewGrid {
  corner: Triple;
  counts: Triple;
  size: Triple;
  rotation: number;
  div: Triple;
}

/** `frame` — the 8 corners of the box of the grid (project axes): q ∈ {0, L}³, x fastest, then y, then z. */
export interface Preview {
  grid: PreviewGrid;
  parents: number;
  /** the bytes of the model on the disk without compression (an estimate: «≈») */
  disk: number;
  extent: number[];
  frame: Triple[];
}

export interface PrototypeResult {
  model_id: number;
  name: string;
  grid: PreviewGrid;
  parents: number;
}

// ---- the fill and the coding ----

export interface WireBody {
  id: number;
  code?: string;
  priority?: number;
}

export interface FillBody {
  prototype_id: number;
  wireframes: WireBody[];
  mode: Mode;
  /** null / absent — the `div` of the prototype */
  levels?: Triple;
  /** the points of the sub-grid per axis, 1…16 */
  m: number;
  outside_code?: string;
  code_field?: string;
  frac_field?: string;
  name?: string;
  folder?: string;
}

export interface CodeBody {
  expected_version: number;
  wireframes: WireBody[];
  mode: Mode;
  /** true — a new model with the sub-blocks of the boundary rows; absent — the fields of the same model, by the centre */
  split?: boolean;
  m?: number;
  outside_code?: string;
  code_field?: string;
  frac_field?: string;
  name?: string;
}

/** The result of `blocks.fill` and `blocks.code`: the codes with the rows and the volumes, the seconds of the stages, the warnings. */
export interface FillResult {
  model_id: number;
  name: string;
  rows: number;
  parents: number;
  codes: Record<string, number>;
  /** m³ by the code: Σ V·FRAC */
  volume: Record<string, number>;
  stages: Record<string, number>;
  warnings: string[];
  /** the fields of other makers that «Перестроить» took away */
  dropped: string[];
  /** `blocks.code` by the centre: the new version of the same model and the fields written */
  version: number | null;
  fields: string[];
}

// ---- the regularization ----

export type Weight = "volume" | "tonnes";

export interface RegularizeBody {
  size: Triple;
  grades?: { field: string; weight: Weight }[];
  /** a field of the model or a number, t/m³ */
  density?: string | number;
  categories?: string[];
  name?: string;
  folder?: string;
  expected_version?: number;
}

export interface Totals {
  volume: number;
  /** null — no density: no tonnes */
  tonnes: number | null;
  metal: Record<string, number>;
}

export interface RegularizeResult {
  model_id: number;
  name: string;
  rows: number;
  totals: { before: Totals; after: Totals; metal_basis: string };
  warnings: string[];
  stages: Record<string, number>;
}

// ---- the readers of the answers ----

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const num = (v: unknown): number | null => (isNum(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null);
const texts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function record(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, x] of Object.entries(obj(v))) if (isNum(x)) out[k] = x;
  return out;
}

function readGrid(raw: unknown): PreviewGrid | null {
  const g = obj(raw);
  const [corner, counts, size, div] = [triple(g.corner), triple(g.counts), triple(g.size), triple(g.div)];
  return corner && counts && size && div && isNum(g.rotation) ? { corner, counts, size, rotation: g.rotation, div } : null;
}

/** The preview of the core; null — not a preview (no grid, a corner that is not a point). */
export function readPreview(raw: unknown): Preview | null {
  const r = obj(raw);
  const grid = readGrid(r.grid);
  const frame = Array.isArray(r.frame) ? r.frame.map(triple) : [];
  if (!grid || !isNum(r.parents) || !isNum(r.disk) || frame.length !== 8 || frame.some((p) => !p)) return null;
  const extent = Array.isArray(r.extent) ? r.extent.filter(isNum) : [];
  return { grid, parents: r.parents, disk: r.disk, extent, frame: frame as Triple[] };
}

export function readPrototype(raw: unknown): PrototypeResult | null {
  const r = obj(raw);
  const grid = readGrid(r.grid);
  return isNum(r.model_id) && grid && isNum(r.parents) ? { model_id: r.model_id, name: str(r.name) ?? "", grid, parents: r.parents } : null;
}

export function readFill(raw: unknown): FillResult | null {
  const r = obj(raw);
  if (!isNum(r.model_id)) return null;
  return {
    model_id: r.model_id,
    name: str(r.name) ?? "",
    rows: num(r.rows) ?? 0,
    parents: num(r.parents) ?? 0,
    codes: record(r.codes),
    volume: record(r.volume),
    stages: record(r.stages),
    warnings: texts(r.warnings),
    dropped: texts(r.dropped),
    version: num(r.version),
    fields: texts(r.fields),
  };
}

function readTotals(raw: unknown): Totals | null {
  const t = obj(raw);
  return isNum(t.volume) ? { volume: t.volume, tonnes: num(t.tonnes), metal: record(t.metal) } : null;
}

export function readRegularize(raw: unknown): RegularizeResult | null {
  const r = obj(raw);
  const t = obj(r.totals);
  const [before, after] = [readTotals(t.before), readTotals(t.after)];
  if (!isNum(r.model_id) || !before || !after) return null;
  return { model_id: r.model_id, name: str(r.name) ?? "", rows: num(r.rows) ?? 0, totals: { before, after, metal_basis: str(t.metal_basis) ?? "volume" }, warnings: texts(r.warnings), stages: record(r.stages) };
}

// ---- the calls ----

const post = async (path: string, body: unknown): Promise<Started> => startedOf(await api.post<Record<string, unknown>>(path, body));

/** `POST /blocks/prototype/preview`: the grid, the number of parents, the disk, the frame (nothing is written; works in «только просмотр»). */
export async function previewPrototype(body: PrototypeBody): Promise<Preview> {
  const p = readPreview(await api.post<unknown>("/blocks/prototype/preview", body));
  if (!p) throw refusalError("Ядро вернуло предпросмотр без сетки.", "Повторите; если повторится — обновите программу.");
  return p;
}

export const startPrototype = (body: PrototypeBody): Promise<Started> => post("/blocks/prototype", body);
export const startFill = (body: FillBody): Promise<Started> => post("/blocks/fill", body);
export const startCode = (modelId: number, body: CodeBody): Promise<Started> => post(`/blocks/${modelId}/code`, body);
export const startRegularize = (modelId: number, body: RegularizeBody): Promise<Started> => post(`/blocks/${modelId}/regularize`, body);
export const startRebuild = (modelId: number, expectedVersion: number): Promise<Started> => post(`/blocks/${modelId}/rebuild`, { expected_version: expectedVersion });

/** A failure of ours told as the core tells its own: `{code, message, hint}`. */
export const refusalError = (message: string, hint: string): CoreError => new CoreError(0, "bad_answer", message, hint);

/** The record of a job: the status, the stage (`message`), the progress, the error, the result as it is. */
export interface JobRecord {
  status: string;
  progress: number;
  message: string | null;
  error: { code: string; message: string; hint: string } | null;
  result: unknown;
}

export async function loadJob(job: Started): Promise<JobRecord> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return {
    status: str(j.status) ?? "",
    progress: num(j.progress) ?? 0,
    message: str(j.message),
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    result: j.result ?? null,
  };
}

/** The fields of a model with their makers (`GET /blocks/{id}/fields`): what «Перестроить» takes away, and whether the model is out of date. */
export interface ModelFields {
  version: number;
  stale: boolean;
  staleReason: string | null;
  fields: { name: string; op: string | null }[];
}

export async function loadModelFields(id: number): Promise<ModelFields> {
  const r = obj(await api.get(`/blocks/${id}/fields`));
  const fields = (Array.isArray(r.fields) ? r.fields : []).map((f) => {
    const x = obj(f);
    return { name: str(x.name) ?? "", op: str(obj(x.source).op) };
  });
  return { version: num(r.version) ?? 0, stale: r.stale === true, staleReason: str(r.stale_reason), fields: fields.filter((f) => f.name) };
}
