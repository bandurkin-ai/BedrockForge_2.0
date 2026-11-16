import { useState } from "react";
import { cancelJob, getJob, isExportJob, isFinished, jobDownloadUrl, retryJob, type JobDetail, type JobInfo } from "../api";
import { LogButton } from "./LogButton";
import type { UploadState } from "./useJobs";

const STATUS: Record<JobInfo["status"], string> = {
  queued: "В очереди",
  running: "Выполняется",
  done: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} КБ`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} МБ`;
  return `${(n / 1024 ** 3).toFixed(2)} ГБ`;
}

function duration(job: JobInfo): string {
  if (!job.started_at) return "";
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  const s = Math.max(0, (end - Date.parse(job.started_at)) / 1000);
  return s < 60 ? `${s.toFixed(1)} с` : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`;
}

function Bar({ value, state }: { value: number; state: string }) {
  return (
    <div className={`job-bar ${state}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <div style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
    </div>
  );
}

/** Uploads of this tab and the project's background jobs: progress, errors, journal, cancel. */
export function JobsPanel({ jobs, uploads, onClearUploads }: { jobs: JobInfo[]; uploads: UploadState[]; onClearUploads: () => void }) {
  const [open, setOpen] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const show = async (id: number) => {
    try {
      setOpen(open?.id === id ? null : await getJob(id));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const retry = async (id: number) => {
    try {
      await retryJob(id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const cancel = async (id: number) => {
    try {
      await cancelJob(id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (jobs.length === 0 && uploads.length === 0) {
    return <p className="muted small">Заданий нет. Загрузка каркасов, линий, растров и экспорт в OMF идут фоновыми заданиями: здесь видны ход, ошибки, журнал и файлы для скачивания.</p>;
  }
  return (
    <div className="jobs">
      {error && <div className="alert error small">{error}</div>}
      {uploads.length > 0 && (
        <div className="jobs-group">
          <div className="jobs-head">
            <strong className="small">Передача файлов</strong>
            <span className="spacer" />
            {uploads.some((u) => u.status !== "uploading") && <button className="link small" onClick={onClearUploads}>Очистить</button>}
          </div>
          {uploads.map((u) => (
            <div key={u.key} className="job-row">
              <span className="job-title" title={u.filename}>{u.filename}</span>
              <Bar value={u.total ? u.sent / u.total : 1} state={u.status} />
              <span className="muted small job-info">
                {u.status === "error" ? u.error : `${formatBytes(u.sent)} из ${formatBytes(u.total)}`}
              </span>
              {u.status === "uploading" && <button className="small" onClick={() => u.abort.abort()}>Прервать</button>}
            </div>
          ))}
        </div>
      )}
      <div className="jobs-group">
        {jobs.map((j) => (
          <div key={j.id}>
            <div className={`job-row ${j.status}`}>
              <button className="link job-title" onClick={() => void show(j.id)} title="Журнал задания">{j.title || j.kind}</button>
              <Bar value={j.status === "done" ? 1 : j.progress} state={j.status} />
              <span className="muted small job-info">
                {STATUS[j.status]}
                {j.status === "running" && j.message ? `: ${j.message}` : ""}
                {j.error ? `: ${j.error}` : ""}
                {duration(j) && ` · ${duration(j)}`}
              </span>
              {!isFinished(j) && (
                <button className="small" disabled={j.cancel_requested} onClick={() => void cancel(j.id)}>
                  {j.cancel_requested ? "Отмена…" : "Отменить"}
                </button>
              )}
              {j.status === "done" && isExportJob(j) && j.result?.filename && (
                <a className="job-download small" href={jobDownloadUrl(j.id)} download={j.result.filename}
                  title={`${j.result.filename}${j.result.size ? `, ${formatBytes(j.result.size)}` : ""}${j.result.warnings?.length ? ` · замечаний: ${j.result.warnings.length} (журнал задания)` : ""}`}>
                  Скачать
                </a>
              )}
              <LogButton jobId={j.id} failed={j.status === "failed"} size={13} />
              {(j.status === "failed" || j.status === "cancelled") && (
                <button className="small" onClick={() => void retry(j.id)} title="Запустить снова с теми же файлами (без новой загрузки)">Повторить</button>
              )}
            </div>
            {open?.id === j.id && (
              <ol className="job-log small">
                {open.log.length === 0 && <li className="muted">Журнал пуст</li>}
                {open.log.map((e, i) => (
                  <li key={i} className={e.level}>
                    <span className="muted">{new Date(e.t).toLocaleTimeString("ru-RU")}</span> <pre>{e.text}</pre>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
