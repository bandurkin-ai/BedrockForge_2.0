import { useEffect } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { showMessage } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { openProject } from "../projects/state";
import { cancelJob, jobLog, jobsStore, refresh, restartWorkers, retryJob, STATUS_TITLES, type Job } from "./state";
import css from "./Jobs.module.css";

/** «Задания» (ТЗ 7.6): state, progress, why it waits; cancel, retry, show in folder, journal. */
export function JobsPanel() {
  const app = useApp();
  const jobs = useStore(jobsStore, (s) => s.jobs);
  const workers = useStore(jobsStore, (s) => s.workers);
  const error = useStore(jobsStore, (s) => s.error);
  useEffect(() => {
    void refresh();
  }, []);

  const noWorker = jobs.some((j) => j.status === "queued" && j.why_waiting?.startsWith("Нет работающего исполнителя"));
  const showLog = async (j: Job) => {
    const text = await jobLog(j).catch((e: unknown) => String(e));
    await showMessage({ title: `Журнал: ${j.title}`, message: `Задание №${j.id} · ${STATUS_TITLES[j.status]}`, log: text || "Журнал пуст" });
  };

  return (
    <div className={css.wrap} data-testid="jobs-panel">
      {(noWorker || workers?.stopped_reason) && (
        <div className={css.bar}>
          <span className={css.warn}>{workers?.stopped_reason ?? "Нет работающего исполнителя заданий."}</span>
          <button type="button" className={css.btn} onClick={() => void restartWorkers()}>
            Перезапустить исполнителей
          </button>
        </div>
      )}
      {error && <div className={css.warn}>{error}</div>}
      {!jobs.length ? (
        <div className={css.empty}>Заданий нет. Здесь появятся импорт, экспорт и долгие расчёты.</div>
      ) : (
        <table className={css.table}>
          <thead>
            <tr>
              <th>№</th>
              <th>Задание</th>
              <th>Состояние</th>
              <th>Ход</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={`${j.source}:${j.id}`} data-testid={`job-${j.kind}`}>
                <td>{j.id}</td>
                <td>
                  <button type="button" className={css.link} title="Журнал задания" onClick={() => void showLog(j)}>
                    {j.title}
                  </button>
                  {j.status === "queued" && j.why_waiting && <div className={css.why}>{j.why_waiting}</div>}
                  {j.status === "failed" && j.error && (
                    <div className={css.failed}>
                      {j.error.message} {j.error.hint}
                    </div>
                  )}
                </td>
                <td className={j.status === "failed" ? css.failed : j.status === "done" ? css.done : ""} data-testid="job-status">
                  {j.cancel_requested && j.status === "running" ? "Отмена…" : STATUS_TITLES[j.status]}
                </td>
                <td>
                  {Math.round(j.progress * 100)}%{j.message && j.status === "running" ? ` · ${j.message}` : ""}
                  {j.status === "running" && (
                    <div className={css.progress}>
                      <div style={{ width: `${Math.round(j.progress * 100)}%` }} />
                    </div>
                  )}
                </td>
                <td>
                  {(j.status === "queued" || j.status === "running") && (
                    <button type="button" className={css.btn} disabled={j.cancel_requested} onClick={() => void cancelJob(j)}>
                      {j.cancel_requested ? "Отмена…" : "Отменить"}
                    </button>
                  )}
                  {j.status === "failed" && (
                    <button type="button" className={css.btn} onClick={() => void retryJob(j)}>
                      Повторить
                    </button>
                  )}
                  {j.status === "done" && j.kind === "export.project" && typeof j.result?.path === "string" && (
                    <button type="button" className={css.btn} onClick={() => bridge()?.showInFolder(String(j.result!.path))}>
                      Показать в папке
                    </button>
                  )}
                  {j.status === "done" && j.kind === "import.project" && typeof j.result?.uid === "string" && (
                    <button type="button" className={css.btn} onClick={() => void openProject(app, String(j.result!.uid))}>
                      Открыть проект
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
