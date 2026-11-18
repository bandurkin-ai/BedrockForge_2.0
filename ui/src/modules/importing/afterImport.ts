/**
 * After «Импорт файлов» (ADR 0013 §5; ТЗ 7.21 «Проверки»): the wireframes the job `import.objects` stored
 * (`result.wireframe_ids`) are checked in one job `wireframe.check` (`POST /wireframes/check`, 2000 ids a job), so
 * «Свойства › Проверки» fill without «Проверить». A wireframe that is being checked already is not queued
 * again; a job is handled once, whether its end is seen by the list of jobs or by the window that started it.
 * A wireframe deleted meanwhile is left out by the core (404 only when none is left). The same once-per-job
 * end is announced to `onObjectsImported` (the 3D window shows what was loaded when it was not in view:
 * `scene/showLoaded.ts`). Only the imports of the open project count: «Задания» list those of a project closed
 * while its job ran too (their ids belong to that project). No React.
 */
import { jobsStore, onJobFinished, refresh, type Job } from "../jobs/state";
import { projectStore } from "../projects/state";
import { activeCheck, startChecks } from "../wireframes/checks";

export const IMPORT_JOB = "import.objects";
/** ids of one job of checks (the core takes up to 2000) */
const BATCH = 2000;

type Finished = Pick<Job, "source" | "id" | "kind" | "status" | "result" | "finished_at">;

const handled = new Set<string>();
const announced = new Set<string>();
const importedListeners = new Set<(job: Finished) => void>();
const keyOf = (j: Finished): string => `${j.source}:${j.id}:${j.finished_at ?? ""}`;

/** The wireframes a finished import stored; [] — none, or the job did not end well. */
export function importedWireframes(job: Pick<Job, "kind" | "status" | "result">): number[] {
  if (job.kind !== IMPORT_JOB || job.status !== "done") return [];
  const ids = job.result?.wireframe_ids;
  return Array.isArray(ids) ? [...new Set(ids.filter((v): v is number => Number.isInteger(v) && v > 0))] : [];
}

/** The job belongs to the open project (the source of its jobs is the uid of the project). */
export function ofOpenProject(job: Pick<Job, "source">): boolean {
  const uid = projectStore.get().current?.uid;
  return !!uid && job.source === uid;
}

/** Starts the checks of what a finished import of the open project stored; returns how many wireframes were
 * sent (0 — handled already, another project). A batch refused (every wireframe of it deleted meanwhile) is
 * not started again: «Проверить» of «Свойства» stays. */
export async function checkImported(job: Finished, startMany: (ids: number[]) => Promise<unknown> = startChecks): Promise<number> {
  const ids = importedWireframes(job);
  if (!ids.length || !ofOpenProject(job) || handled.has(keyOf(job))) return 0;
  handled.add(keyOf(job));
  const todo = ids.filter((id) => !activeCheck(jobsStore.get().jobs, id));
  let queued = 0;
  for (let i = 0; i < todo.length; i += BATCH) {
    const batch = todo.slice(i, i + BATCH);
    try {
      await startMany(batch);
      queued += batch.length;
    } catch {
      // none of the batch is there any more (deleted, the project closed meanwhile)
    }
  }
  if (queued) void refresh();
  return queued;
}

/** Every import of files that ended well, once — seen by the list of jobs or by the window that started it. */
export function onObjectsImported(fn: (job: Finished) => void): () => void {
  importedListeners.add(fn);
  return () => importedListeners.delete(fn);
}

function announce(job: Finished): void {
  if (job.kind !== IMPORT_JOB || job.status !== "done" || !ofOpenProject(job) || announced.has(keyOf(job))) return;
  announced.add(keyOf(job));
  for (const fn of importedListeners) fn(job);
}

/** Every import that ends well while the interface runs (also a retry from «Задания»). */
export function watchImports(): () => void {
  return onJobFinished((job) => {
    announce(job);
    void checkImported(job);
  });
}

/** The window that started the import: the job may have ended before the list of jobs first saw it. */
export function afterStart(job: Pick<Job, "source" | "id">): void {
  const done = jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id && j.status === "done");
  if (!done) return;
  announce(done);
  void checkImported(done);
}

/** For the tests. */
export function forgetHandled(): void {
  handled.clear();
  announced.clear();
}
