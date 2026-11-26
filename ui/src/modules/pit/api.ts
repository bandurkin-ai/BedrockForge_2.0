/**
 * The client of the pit optimisation (ТЗ 8.11; ADR 0028 «API»; docs/progress/e21-api.md §2–§6; core — `mine/router.py`). The forms of the answers
 * are in `answers.ts` (they are those of `core/tests/data/pit/contract_answers.json`):
 *
 * - `GET /pit/defaults?model_id=` → `{params, choices, limits, ranges, model?}` — the form of a new scenario;
 * - `POST /pit/validate {params}` → `{ready, missing, warnings}` — the one source of «Запустить» (synchronous);
 * - `POST /pit/check {params}` → the job `pit.check` (the template and the angle control);
 * - `GET/POST /pit/scenarios`, `GET/PUT/DELETE /pit/scenarios/{id}` — a scenario (`PUT` — `expected_version`: 409 `version_conflict {version}`);
 * - `POST /pit/scenarios/{id}/run {solver?}` → the job `pit.run`; 409 `pit_running`, `pit_fields_taken`; 422 `pit_not_ready {missing}`,
 *   `variable_grid`, `pit_too_large`, `pit_density_missing`, `pit_empty`, `filter_field_missing`; 423 `project_read_only`.
 *
 * The params of a request are the full canonical params (a `PUT` replaces them, the core takes what is missing from its defaults); a gap is `null`,
 * never 0. No React.
 */
import { api, CoreError } from "../../shell/api";
import { startedOf, waitJob, type Started } from "../qaqc/setupApi";
import { readDefaults, readRunSummary, readScenario, readScenarioRow, readValidate, obj, num, str, list, type Defaults, type PitParams, type RunSummary, type ScenarioDetail, type ScenarioRow, type Solver, type Validate } from "./answers";

export type { Started };
export { refusalOf, type Refusal } from "../blocks/report/api";

const bad = (what: string) => new CoreError(0, "bad_answer", `Ядро вернуло ${what} в непонятной форме.`, "Обновите программу.");

// ---- defaults, readiness ----

export async function loadDefaults(modelId?: number | null): Promise<Defaults> {
  return readDefaults(await api.get(`/pit/defaults${typeof modelId === "number" ? `?model_id=${modelId}` : ""}`));
}

export async function validateParams(params: PitParams): Promise<Validate> {
  return readValidate(await api.post("/pit/validate", { params }));
}

// ---- scenarios ----

export async function listScenarios(): Promise<ScenarioRow[]> {
  const r = obj(await api.get("/pit/scenarios"));
  return list(r.scenarios).map(readScenarioRow).filter((x): x is ScenarioRow => x !== null);
}

export async function loadScenario(id: number): Promise<ScenarioDetail> {
  const s = readScenario(await api.get(`/pit/scenarios/${id}`));
  if (!s) throw bad("сценарий");
  return s;
}

/** `POST /pit/scenarios {name?, params?}` → `{id, version, name}` (a name that is taken gets « (2)»). */
export async function createScenario(body: { name?: string; params?: PitParams }): Promise<{ id: number; version: number; name: string }> {
  const r = obj(await api.post("/pit/scenarios", body));
  const id = num(r.id);
  if (id === null) throw bad("номер сценария");
  return { id, version: num(r.version) ?? 1, name: str(r.name) ?? body.name ?? "" };
}

export interface UpdateBody {
  name?: string;
  params?: PitParams;
  expected_version: number;
}

/** `PUT /pit/scenarios/{id}` → `{id, version, name, params_changed}`; a result that exists is «params_changed» (the run is needed). */
export async function updateScenario(id: number, body: UpdateBody): Promise<{ id: number; version: number; name: string; params_changed: boolean }> {
  const r = obj(await api.put(`/pit/scenarios/${id}`, body));
  return { id: num(r.id) ?? id, version: num(r.version) ?? body.expected_version + 1, name: str(r.name) ?? body.name ?? "", params_changed: r.params_changed === true };
}

export async function deleteScenario(id: number): Promise<void> {
  await api.del(`/pit/scenarios/${id}`);
}

// ---- jobs ----

/** `POST /pit/scenarios/{id}/run {solver?}`: the job `pit.run` (the checks 404, 409, 422, 423 come before it starts). */
export async function startRun(id: number, solver?: Solver): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/pit/scenarios/${id}/run`, solver ? { solver } : {}));
}

/** `POST /pit/check {params}`: the job `pit.check` (a model is needed; the prices and the rocks are not). */
export async function startCheck(params: PitParams): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>("/pit/check", { params }));
}

export interface JobRecord {
  status: string;
  progress: number;
  message: string | null;
  error: { code: string; message: string; hint: string } | null;
  result: unknown;
}

/** The record of a job from the core (`GET /jobs/{source}/{id}`): the status, the stage, the result as it came. */
export async function loadJob(job: Started): Promise<JobRecord> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return { status: str(j.status) ?? "", progress: num(j.progress) ?? 0, message: str(j.message), error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null, result: j.result ?? null };
}

export async function loadRunResult(job: Started): Promise<RunSummary | null> {
  return readRunSummary((await loadJob(job)).result);
}

/** The wait for the end of the job (the list of the «Задания» tab follows the events of the core); `signal` stops the wait only. */
export const waitJobEnd = (job: Started, signal?: AbortSignal): ReturnType<typeof waitJob> => waitJob(job, signal);
