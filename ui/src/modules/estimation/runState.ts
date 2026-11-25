/**
 * The run of the estimation as the windows see it (ТЗ 8.8.4; docs/progress/e18-api.md §6): the job `estimate.run` of the plan or
 * of the rows, followed from the start to its end, and the journal of its result — by the passes, the counts of the blocks by
 * the status, the blocks with a singular system, the averaged samples, the rows that wrote into the same fields. The command
 * `estimate.run` (`bind.ts`) writes here whoever called it (the window of the plan, the tree, the ribbon, the keys, the
 * assistant); the window of the plan shows it. The progress and the stage of the job are in the list of «Задания» (`jobsStore`).
 * One run at a time is shown (the core runs one estimation of a model after another). No React.
 */
import { Store } from "../../shell/store";
import type { Refusal, RunResult, Started } from "./api";

export type RunPhase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface RunState {
  phase: RunPhase;
  /** the job of the core, once it is started (a question «Заменить поля?» comes before it) */
  job: Started | null;
  modelId: number | null;
  modelName: string;
  planId: number | null;
  /** the rows run, in the order asked */
  ids: number[];
  result: RunResult | null;
  /** the line the command answers with («Оценка записана в модель …») */
  summary: string | null;
  error: Refusal | null;
  /** a person said «Нет» to «Заменить поля?», or the run was cancelled: a line, not an error */
  note: string | null;
}

export const IDLE_RUN: RunState = { phase: "idle", job: null, modelId: null, modelName: "", planId: null, ids: [], result: null, summary: null, error: null, note: null };

export const runStore = new Store<RunState>(IDLE_RUN);

export const runBegin = (modelId: number, modelName: string, planId: number | null, ids: number[]): void => runStore.set({ ...IDLE_RUN, phase: "running", modelId, modelName, planId, ids });
export const runJob = (job: Started): void => runStore.set({ job });
export const runDone = (result: RunResult, summary: string): void => runStore.set({ phase: "done", result, summary, error: null, note: null });
export const runCancelled = (note: string): void => runStore.set({ phase: "cancelled", note });
export const runReset = (): void => runStore.set(IDLE_RUN);

/** A refusal of the core or of the form; «Нет» on the question about the fields is not a failure (nothing was started). */
export function runFailed(r: Refusal): void {
  if (r.code === "declined") runStore.set({ phase: "idle", job: null, error: null, note: r.message });
  else runStore.set({ phase: "failed", error: r });
}

export const runBusy = (): boolean => runStore.get().phase === "running";

const SUM = (a: readonly number[]): number => a.reduce((x, y) => x + y, 0);

/** The words about the singular systems by the passes, as the journal gives them: «проход 1 — 3, проход 2 — 1»; "" — there were none. */
export function singularText(singular: readonly number[]): string {
  if (!SUM(singular)) return "";
  return singular.flatMap((n, i) => (n ? [`проход ${i + 1} — ${n.toLocaleString("ru-RU")}`] : [])).join(", ");
}
