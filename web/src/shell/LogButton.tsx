import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { getJob, getJobsQueue, type JobDetail, type JobInfo, type JobsQueue } from "../api";
import { Icon } from "./icons";
import { copyText, logForJob, recentLogs, renderJobOnly, saveText, subscribeLogs, type OpLog } from "./opLog";

/** Text of a journal with the server journals of its jobs (fetched now). */
async function buildText(log: OpLog | null, jobId: number | null): Promise<{ text: string; name: string }> {
  const ids = [...new Set([...(log?.jobIds ?? []), ...(jobId !== null ? [jobId] : [])])];
  const jobs: JobDetail[] = [];
  const failed: string[] = [];
  for (const id of ids) {
    try {
      jobs.push(await getJob(id));
    } catch (e) {
      failed.push(`задание №${id}: ${(e as Error).message}`);
    }
  }
  // A job not finished: the queue as the server sees it (workers alive, the jobs ahead).
  let queue: JobsQueue | null = null;
  if (jobs.some((j) => j.status === "queued" || j.status === "running")) queue = await getJobsQueue().catch(() => null);
  if (log) {
    const text = log.text(jobs, queue);
    return { text: failed.length ? `${text}\nНе получены журналы: ${failed.join("; ")}\n` : text, name: log.fileName() };
  }
  const job = jobs[0];
  if (!job) return { text: `Журнал задания №${jobId} не получен: ${failed.join("; ")}\n`, name: `bedrockforge-job-${jobId}.txt` };
  return { text: renderJobOnly(job, queue), name: `bedrockforge-job-${job.id}.txt` };
}

/** The journal as text in a window: copy to the clipboard, download as .txt, refresh. */
export function LogViewer({ log, jobId = null, onClose }: { log: OpLog | null; jobId?: number | null; onClose: () => void }) {
  const [text, setText] = useState("Журнал собирается…");
  const [name, setName] = useState("bedrockforge-log.txt");
  const [note, setNote] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    void buildText(log, jobId).then((r) => {
      if (!alive) return;
      setText(r.text);
      setName(r.name);
    });
    return () => { alive = false; };
  }, [log, jobId, tick]);

  useEffect(() => {
    // Capture: Escape closes this window, not the dialog under it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const copy = async () => setNote((await copyText(text)) ? "Скопировано в буфер обмена — вставьте в чат с Claude" : "Браузер не дал скопировать: выделите текст (Ctrl+A) и скопируйте");
  const download = () => {
    saveText(text, name);
    setNote(`Файл «${name}» сохранён в загрузки`);
  };

  return createPortal(
    <div className="modal-backdrop log-backdrop" onMouseDown={(e) => { e.stopPropagation(); onClose(); }}>
      <div className="modal log-viewer" role="dialog" aria-label="Журнал операции" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="logFile" size={16} />
          <strong className="grow">Журнал: {log?.title ?? `задание №${jobId}`}</strong>
          <span className="muted small">{text.length.toLocaleString("ru-RU")} симв.</span>
          <button className="icon" onClick={onClose} title="Закрыть (Esc)" aria-label="Закрыть журнал"><Icon name="close" size={14} /></button>
        </header>
        <textarea className="log-text" readOnly value={text} spellCheck={false} aria-label="Текст журнала" onFocus={(e) => e.currentTarget.select()} />
        <footer className="modal-foot">
          <span className="muted small grow">{note ?? "Журнал нужен для поиска ошибок: скопируйте его или скачайте и передайте Claude."}</span>
          <button onClick={() => { setNote(null); setTick((t) => t + 1); }} title="Собрать журнал заново (новые шаги и журналы заданий)"><Icon name="refresh" size={13} /> Обновить</button>
          <button onClick={() => void copy()}><Icon name="copy" size={13} /> Копировать</button>
          <button className="primary" onClick={download}><Icon name="import" size={13} /> Скачать .txt</button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

/**
 * The journal icon for the header of an import or export dialog (or a job row): opens the log as
 * text. Turns red when the journal has errors.
 */
export function LogButton({ log, jobId = null, failed = false, size = 15, label }: {
  log?: OpLog | null;
  jobId?: number | null;
  /** the operation failed outside the journal (a failed job): the icon is red */
  failed?: boolean;
  size?: number;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const own = log ?? (jobId !== null ? logForJob(jobId) ?? null : null);
  // Redraw when the journal gets entries: the error mark follows.
  useSyncExternalStore(subscribeLogs, () => own?.version ?? 0);
  const bad = failed || !!own?.hasErrors;
  return (
    <>
      <button type="button" className={`icon log-button${bad ? " has-errors" : ""}${label ? " with-label" : ""}`} onClick={() => setOpen(true)}
        title={`Журнал операции (txt): скопировать или скачать для поиска ошибок${bad ? " — есть ошибки" : ""}`} aria-label="Журнал операции">
        <Icon name="logFile" size={size} />{label && <span>{label}</span>}
      </button>
      {open && <LogViewer log={own} jobId={jobId} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Journals of imports and exports of this session: a list, and the chosen one as text. */
export function LogsDialog({ jobs = [], onClose }: { jobs?: JobInfo[]; onClose: () => void }) {
  const logs = useSyncExternalStore(subscribeLogs, () => recentLogs().map((l) => `${l.id}:${l.version}`).join());
  const list = recentLogs();
  const [chosen, setChosen] = useState<OpLog | null>(null);
  void logs;

  useEffect(() => {
    if (chosen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chosen, onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wizard logs-dialog" role="dialog" aria-label="Журналы импорта и экспорта" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Icon name="logFile" size={16} />
          <strong className="grow">Журналы импорта и экспорта</strong>
          <span className="muted small">за этот сеанс; после перезагрузки страницы — журналы заданий во вкладке «Задания»</span>
          <button className="icon" onClick={onClose} title="Закрыть (Esc)" aria-label="Закрыть"><Icon name="close" size={16} /></button>
        </header>
        <div className="modal-body wizard-body">
          {list.length === 0 ? <p className="muted small">Импорта и экспорта в этом сеансе ещё не было.</p> : (
            <table className="wizard-table">
              <thead><tr><th>Время</th><th>Операция</th><th>Итог</th><th /></tr></thead>
              <tbody>
                {list.map((l) => {
                  const errors = l.entries.filter((e) => e.level === "error").length;
                  const warnings = l.entries.filter((e) => e.level === "warning").length;
                  const failedJobs = jobs.filter((j) => l.jobIds.includes(j.id) && j.status === "failed").length;
                  return (
                    <tr key={l.id}>
                      <td className="small">{new Date(l.started).toLocaleTimeString("ru-RU")}</td>
                      <td className="name">{l.kind === "import" ? "Импорт" : "Экспорт"}: {l.title}</td>
                      <td className="small">
                        {l.hasErrors ? <span className="error-text">ошибок {errors || "в запросах"}</span> : failedJobs ? null : "без ошибок"}
                        {failedJobs > 0 && <span className="error-text">{l.hasErrors ? " · " : ""}заданий с ошибкой {failedJobs}</span>}
                        {warnings > 0 && ` · предупреждений ${warnings}`}
                        {l.jobIds.length > 0 && ` · заданий ${l.jobIds.length}`}
                        {l.finished === null && " · окно открыто"}
                      </td>
                      <td><button className="small" onClick={() => setChosen(l)}><Icon name="logFile" size={13} /> Открыть</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {chosen && <LogViewer log={chosen} onClose={() => setChosen(null)} />}
    </div>
  );
}
