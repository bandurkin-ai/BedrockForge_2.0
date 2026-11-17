/** A window's operation log follows the job it started to the end (ТЗ 7.9): state, result or error, job log. */
import { linkJob, logError, logStep, type OpLog } from "../../shell/opLog";
import { jobLog, onJobFinished, STATUS_TITLES, type Job } from "./state";

export function followJob(log: OpLog, job: Pick<Job, "source" | "id">): () => void {
  linkJob(log, job);
  const off = onJobFinished((j) => {
    if (j.source !== job.source || j.id !== job.id) return;
    off();
    void (async () => {
      const text = await jobLog(j).catch((e: unknown) => `журнал задания не получен: ${e instanceof Error ? e.message : String(e)}`);
      if (j.status === "failed") {
        const err = j.error;
        logError(log, `Задание №${j.id}: ${err ? `${err.message} ${err.hint}`.trim() : "ошибка"}`, { detail: err ? `код ${err.code}` : undefined, trace: text || undefined });
      } else {
        logStep(log, `Задание №${j.id}: ${STATUS_TITLES[j.status]}${j.result ? `, результат ${JSON.stringify(j.result)}` : ""}`);
        logStep(log, `Журнал задания:\n${text || "(пуст)"}`);
      }
    })();
  });
  return off;
}
