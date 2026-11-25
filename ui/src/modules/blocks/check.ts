/**
 * «Перепроверить» of a block model (ТЗ 7.31, 8.7.2, Т8.7-5; docs/progress/e17-api.md §11; Э17 17.41): `POST /blocks/{id}/check`
 * starts the job `blocks.check` (queue quick, stage «Проверки»); the core reads the stored model again, writes `meta.checks`
 * and keeps the version. The result is `{model_id, checks, events}`; the job fails with `version_conflict` when the model was
 * changed meanwhile (nothing is written — the person presses «Перечитать» and repeats). The answers are those of the core
 * (`core/tests/data/blocks/check_window_answers.json`, made by `core/tests/test_blocks_check_window_answers.py`).
 *
 * The run is followed (`waitJob`, the progress is the line of the job in «Задания»), can be cancelled, and the checks it
 * brings are put into `blocksStore.details` of the model: «Свойства» (the properties and the window of the model) show them
 * from there — the duplicates, the overlaps, the empty and constant fields, the volume, the examples. A number the core
 * does not know stays `null` (never 0). No React.
 */
import type { App } from "../../shell/app";
import { api, CoreError } from "../../shell/api";
import type { CommandSource } from "../../shell/commands";
import { needWritable, noted, refusal } from "../../shell/objectRun";
import { num } from "../../shell/props";
import { Store } from "../../shell/store";
import { cancelJob } from "../jobs/state";
import { projectStore } from "../projects/state";
import { startedOf, waitJob, type Started } from "../qaqc/setupApi";
import { refusalOf, type Refusal } from "./report/api";
import { modelOf } from "./showCommands";
import { blocksStore, loadBlockDetails, loadBlocks } from "./state";
import { checkLines, readDetails } from "./window/details";

export type { Refusal, Started };

export type Phase = "running" | "done" | "failed" | "cancelled";

/** What the interface shows of a run of one model; a model that was not checked from this window has no run. */
export interface CheckRun {
  phase: Phase;
  job: Started | null;
  /** the refusal of the run, in Russian with its hint */
  error: Refusal | null;
  /** the model was changed while it was checked: «Перечитать» reads it again (409 `version_conflict`) */
  conflict: boolean;
  /** the line of the log, for the finished run */
  note: string | null;
}

export const checkStore = new Store<{ runs: Record<number, CheckRun> }>({ runs: {} });

export const runOf = (id: number): CheckRun | undefined => checkStore.get().runs[id];

export const isChecking = (id: number): boolean => runOf(id)?.phase === "running";

function put(id: number, run: CheckRun | null): void {
  checkStore.set((s) => {
    const runs = { ...s.runs };
    if (run) runs[id] = run;
    else delete runs[id];
    return { runs };
  });
}

export function resetChecks(): void {
  checkStore.set({ runs: {} });
}

export const READ_ONLY = refusal("project_read_only", "Проект открыт только для чтения: проверки модели не записать.", "Закройте проект в другом окне и откройте его заново.");

// ---- the client ----

/** `POST /blocks/{id}/check` → the job (404 `block_model_not_found`, 423 `project_read_only`, 422 `not_ready`). */
export const startCheck = async (id: number): Promise<Started> => startedOf(await api.post<Record<string, unknown>>(`/blocks/${id}/check`, {}));

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** The result of the job: the model and its checks as stored in `meta.checks` (the server's shape, so «Свойства» read it as the details); null — not a result. */
export function readCheckResult(raw: unknown): { modelId: number; checks: Record<string, unknown> } | null {
  if (!isObj(raw) || typeof raw.model_id !== "number" || !isObj(raw.checks)) return null;
  const c = raw.checks;
  if (typeof c.duplicates !== "number" || !isObj(c.overlaps) || !Array.isArray(c.empty) || !Array.isArray(c.constant) || !Array.isArray(c.examples)) return null;
  return { modelId: raw.model_id, checks: c };
}

interface JobAnswer {
  status: string;
  error: Refusal | null;
  result: unknown;
}

async function loadJob(job: Started): Promise<JobAnswer> {
  const j = await api.get<Record<string, unknown>>(`/jobs/${job.source}/${job.id}`);
  const e = isObj(j.error) ? j.error : {};
  return {
    status: typeof j.status === "string" ? j.status : "",
    error: typeof e.code === "string" ? { code: e.code, message: typeof e.message === "string" ? e.message : "", hint: typeof e.hint === "string" ? e.hint : "", param: typeof e.param === "string" ? e.param : null } : null,
    result: j.result,
  };
}

// ---- the words ----

/** The line of the log and of the assistant: what the checks found (the same lines «Свойства» show), the volume; a gap is not a zero. */
export function summaryText(name: string, checks: Record<string, unknown>): string {
  const d = readDetails({ checks });
  const lines = checkLines(d.checks);
  const low = (l: string): string => l.charAt(0).toLowerCase() + l.slice(1);
  const found = !lines || lines.clean ? "замечаний нет" : lines.lines.filter((l) => !l.startsWith("Примеры")).map(low).join("; ");
  const volume = d.checks?.volume;
  const vol = typeof volume === "number" ? `; объём блоков ${num(volume, 1)} м³` : "";
  return `Проверка модели «${name}» выполнена: ${found}${vol}`;
}

/** The refusal of a failed run in Russian with a hint; a conflict tells to read the model again. */
export function conflictHint(r: Refusal): Refusal {
  return r.code === "version_conflict" ? { ...r, hint: `${r.hint} Нажмите «Перечитать», затем повторите проверку.`.trim() } : r;
}

// ---- the run ----

/** Puts the checks of the job into the details of the model (the version is the same: the check does not change it), then reads them again. */
async function bring(id: number, checks: Record<string, unknown>): Promise<void> {
  const d = blocksStore.get().details[id];
  if (d) blocksStore.set((s) => ({ details: { ...s.details, [id]: { ...d, checks } as typeof d } }));
  await loadBlockDetails(id);
}

/** `blocks.check {model_id}`: the job is started, followed to its end and its checks are shown; the answer is the line of the log. */
export async function runBlocksCheck(app: App, a: Record<string, unknown>, _source: CommandSource): Promise<string> {
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  const model = await modelOf(app, a);
  const id = model.id;
  if (isChecking(id)) throw refusal("check_running", `Модель «${model.name}» уже проверяется.`, "Дождитесь конца или отмените проверку кнопкой «Отменить».");
  const uid = projectStore.get().current?.uid ?? null;
  put(id, { phase: "running", job: null, error: null, conflict: false, note: null });
  const fail = (r: Refusal, conflict = false): never => {
    const shown = conflict ? conflictHint(r) : r;
    put(id, { phase: "failed", job: runOf(id)?.job ?? null, error: shown, conflict, note: null });
    throw new CoreError(0, shown.code, shown.message, shown.hint);
  };
  let job: Started;
  try {
    job = await startCheck(id);
  } catch (e) {
    return fail(refusalOf(e));
  }
  put(id, { ...(runOf(id) as CheckRun), job });
  let end;
  try {
    end = await waitJob(job);
  } catch (e) {
    if ((projectStore.get().current?.uid ?? null) !== uid) put(id, null);
    return fail(refusalOf(e));
  }
  if (end.status === "cancelled") {
    const note = `Проверка модели «${model.name}» отменена: прежние результаты не изменены`;
    put(id, { phase: "cancelled", job, error: null, conflict: false, note });
    return note;
  }
  let got: JobAnswer;
  try {
    got = await loadJob(job);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (got.status === "failed" || end.status === "failed") {
    const r: Refusal = got.error ?? { code: "job_failed", message: "Проверка не удалась.", hint: "Повторите проверку.", param: null };
    if (r.code === "version_conflict") await loadBlocks().catch(() => undefined);
    return fail(r, r.code === "version_conflict");
  }
  const result = readCheckResult(got.result);
  if (!result) return fail({ code: "bad_answer", message: "Ядро вернуло проверку без результата.", hint: "Повторите проверку; если повторится — обновите программу.", param: null });
  const note = summaryText(model.name, result.checks);
  await bring(id, result.checks).catch(() => undefined); // the checks are there already; the read only brings the rest up to date
  put(id, { phase: "done", job, error: null, conflict: false, note });
  return note;
}

/** «Отменить»: the job stops between the portions; nothing is written. */
export async function cancelCheck(id: number): Promise<void> {
  const job = runOf(id)?.job;
  if (job && isChecking(id)) await cancelJob(job);
}

/** «Перечитать» after a conflict: the model and its details are read again, the refusal goes away. */
export async function rereadModel(id: number): Promise<void> {
  await loadBlocks();
  await loadBlockDetails(id);
  put(id, null);
}

/** The handler of `blocks.check` (unavailable without a project and in «только просмотр»); a project that changes drops the runs. */
export function bindCheck(app: App): () => void {
  // until the orchestrator names `checkCommands.ts` in `shell/catalog.ts` the command is not declared: nothing to bind
  if (app.registry.get("blocks.check")) app.registry.bind("blocks.check", noted(app, (a, source) => runBlocksCheck(app, a, source)), needWritable(app));
  let uid = projectStore.get().current?.uid ?? null;
  return projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetChecks();
  });
}
