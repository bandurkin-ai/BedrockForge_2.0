/**
 * The client of the results of the pit optimisation (ТЗ 8.11.5; ADR 0028 решения 25–27; docs/progress/e21-api.md §9–§11; core — `mine/routes_extra.py`):
 *
 * - `POST /pit/scenarios/{id}/surfaces {shells?}` → the job `pit.surfaces` (quick); 404, 409 `pit_no_result`, `pit_running`; 422 `bad_param {shells.i}`; 423;
 * - `POST /pit/scenarios/{id}/solid {shell, name?}` → the job `pit.solid` (quick); 422 `bad_param {shell}`, `pit_solid_failed`;
 * - `POST /pit/compare {a, b}` → the comparison (synchronous; works in a project open for reading); 404, 409 `pit_no_result`, 422 `bad_param {a, b}`.
 *
 * The results of the jobs are read by `loadJob` of `api.ts`. No React.
 */
import { api, CoreError } from "../../shell/api";
import { startedOf, type Started } from "../qaqc/setupApi";
import { loadJob } from "./api";
import { readCompare, readSolid, readSurfaces, type CompareAnswer, type SolidResult, type SurfacesResult } from "./resultsAnswers";

const bad = (what: string) => new CoreError(0, "bad_answer", `Ядро вернуло ${what} в непонятной форме.`, "Обновите программу.");

/** The job `pit.surfaces`: `shells` — the numbers 1…N; none — all the shells of the result. */
export async function startSurfaces(id: number, shells?: readonly number[]): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/pit/scenarios/${id}/surfaces`, shells ? { shells: [...shells] } : {}));
}

/** The job `pit.solid`: the shell `shell` (1…N) and the name of the solid (none — the core names it). */
export async function startSolid(id: number, shell: number, name?: string): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/pit/scenarios/${id}/solid`, { shell, ...(name ? { name } : {}) }));
}

export async function loadSurfacesResult(job: Started): Promise<SurfacesResult | null> {
  return readSurfaces((await loadJob(job)).result);
}

export async function loadSolidResult(job: Started): Promise<SolidResult | null> {
  return readSolid((await loadJob(job)).result);
}

export async function compareScenarios(a: number, b: number): Promise<CompareAnswer> {
  const r = readCompare(await api.post("/pit/compare", { a, b }));
  if (!r) throw bad("сравнение сценариев");
  return r;
}
