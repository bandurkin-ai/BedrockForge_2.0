/** Commands jobs.* (ТЗ 7.6). */
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import { setPanel } from "../../shell/workspace";
import { cancelJob, jobsStore, refresh, restartWorkers, retryJob } from "./state";

export function bindJobs(app: App): void {
  app.registry.bind("jobs.list", async () => {
    app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "jobs") }));
    await refresh();
    const jobs = jobsStore.get().jobs;
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
    return active.length ? active.map((j) => `№${j.id} ${j.title}: ${j.status}, ${Math.round(j.progress * 100)}%`).join("; ") : "Активных заданий нет";
  });
  const coreOnline = (ctx: { coreOnline: boolean }) => (ctx.coreOnline ? null : "Ядро недоступно");
  const asText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
  app.registry.bind(
    "jobs.retry",
    async (a) => {
      await refresh();
      const id = Number(a.job_id);
      const job = jobsStore.get().jobs.find((j) => j.id === id && j.status === "failed" && (a.source === undefined || j.source === a.source));
      if (!job) throw new Error(`Задание №${id} не завершилось ошибкой`);
      try {
        await retryJob(job);
      } catch (e) {
        throw new Error(asText(e));
      }
      return `Задание №${id} запущено заново`;
    },
    coreOnline,
  );
  app.registry.bind(
    "jobs.restart_workers",
    async () => {
      try {
        await restartWorkers();
      } catch (e) {
        throw new Error(asText(e));
      }
      return "Исполнители перезапущены";
    },
    coreOnline,
  );
  app.registry.bind(
    "jobs.cancel",
    async (a) => {
      await refresh();
      const id = Number(a.job_id);
      const job = jobsStore.get().jobs.find((j) => j.id === id && (j.status === "queued" || j.status === "running"));
      if (!job) throw new Error(`Задание №${id} не выполняется`);
      try {
        await cancelJob(job);
      } catch (e) {
        throw new Error(e instanceof CoreError ? e.full : String(e));
      }
      return `Отмена задания №${id}`;
    },
    (ctx) => (ctx.coreOnline ? null : "Ядро недоступно"),
  );
}
