/**
 * A quick job of a check (`estimate.kna`, `estimate.global`) followed from its start to its end: the answer of the core, a
 * cancel, a refusal in the form of the errors of the core, a project that changed under it (what was asked of one project
 * does not belong to another). The progress and the stage are in the list of «Задания» (`jobsStore`). No React.
 */
import { refusal } from "../../shell/objectRun";
import { loadJob } from "../blocks/build/api";
import { projectStore } from "../projects/state";
import { refusalOf, waitJob, type Started } from "./api";

export type JobEnd = { kind: "done"; result: unknown } | { kind: "cancelled" } | { kind: "stale" };

export async function followJob(started: Started, failed: { message: string; hint: string }): Promise<JobEnd> {
  const uid = projectStore.get().current?.uid ?? null;
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    const r = refusalOf(e);
    if (r.code === "project_changed") return { kind: "stale" };
    throw refusal(r.code, r.message, r.hint);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) return { kind: "stale" };
  if (end.status === "cancelled") return { kind: "cancelled" };
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? failed.message, end.error?.hint ?? failed.hint);
  try {
    return { kind: "done", result: (await loadJob(started)).result };
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}
