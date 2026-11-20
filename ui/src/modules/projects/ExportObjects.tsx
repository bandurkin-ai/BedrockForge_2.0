/**
 * The part «Объекты в файлы» of the window «Экспорт проекта» (Д-17; ADR 0017 §5): the table «тип → формат» from
 * the core (a format the core cannot write now is shown with its reason), the types checked, the folder; then the
 * job `export.objects` followed in the window — its progress and «Отменить» — and its report: the files written,
 * what was skipped and why, «Показать в папке». The job is seen in «Задания» too. With the chosen objects (`ids`,
 * `counts` — how many of each kind) the column «Объектов» counts them only; a format asked for that the core does
 * not write for a type is told above the table (`replacedFormats`).
 */
import { useEffect, useMemo, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { showJobs } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { cancelJob, jobsStore, refresh as refreshJobs, STATUS_TITLES } from "../jobs/state";
import jobsCss from "../jobs/Jobs.module.css";
import {
  choiceReason,
  chosenObjects,
  exportBody,
  exportFolderName,
  initialChoice,
  loadFormats,
  relativePath,
  replacedFormats,
  reportOf,
  reportSummary,
  scopeToIds,
  setFormat,
  sizeText,
  startObjectsExport,
  toggleType,
  typeReason,
  type Choice,
  type FormatsInfo,
} from "./exportObjectsModel";
import css from "./Projects.module.css";

export interface ObjectsInitial {
  formats?: Record<string, string>;
  types?: string[];
  folder?: string;
  ids?: number[];
  /** the chosen objects (`ids`) of each kind */
  counts?: Record<string, number>;
  /** the formats asked for, where `formats` had to replace some (told above the table) */
  wanted?: Record<string, string>;
}

const REPORT_ROWS = 300; // a long report keeps its head in the window; the whole list is in the job log

export function ExportObjects({ winId, project, initial }: { winId: string; project: string; initial?: ObjectsInitial }) {
  const app = useApp();
  const readOnly = useStore(app.store, (s) => s.projectReadOnly);
  const [info, setInfo] = useState<FormatsInfo | null>(null);
  const [choice, setChoice] = useState<Choice>({ formats: {}, types: [] });
  const [folder, setFolder] = useState<string | null>(initial?.folder ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<{ source: string; id: number } | null>(null);
  const live = useStore(jobsStore, (s) => (job ? s.jobs.find((j) => j.source === job.source && j.id === job.id) : undefined));

  useEffect(() => {
    let alive = true;
    loadFormats()
      .then((i) => {
        if (!alive) return;
        const scoped = initial?.counts ? scopeToIds(i, initial.counts) : i;
        setInfo(scoped);
        setChoice(initialChoice(scoped, { formats: initial?.formats, types: initial?.types }));
      })
      .catch((e: unknown) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
    // the table is read once per opening of the window
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reason = info ? (readOnly ? "Проект открыт только для чтения: экспорт недоступен." : choiceReason(info, choice, folder)) : "Читаю форматы…";
  const objects = info ? chosenObjects(info, choice) : 0;
  const replaced = useMemo(() => (info ? replacedFormats(info, initial?.wanted ?? initial?.formats, choice) : []), [info, initial?.wanted, initial?.formats, choice]);
  const report = useMemo(() => (live?.status === "done" ? reportOf(live.result) : null), [live?.status, live?.result]);

  const pick = async () => {
    const p = await bridge()?.pickFolder();
    if (p) setFolder(p);
  };
  const start = async () => {
    if (!info || reason || !folder) return;
    setBusy(true);
    setError(null);
    try {
      const started = await startObjectsExport(exportBody(info, choice, folder, initial?.ids));
      setJob(started);
      showJobs(app);
      await refreshJobs();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (job) {
    const status = live?.status ?? "queued";
    const finished = status === "done" || status === "failed" || status === "cancelled";
    const shown = report?.folder ?? null;
    return (
      <>
        <section className={css.section} data-testid="export-objects-job">
          <h3>Экспорт объектов · задание №{job.id}</h3>
          <div data-testid="export-objects-status">
            {live?.cancel_requested && status === "running" ? "Отмена…" : STATUS_TITLES[status]}
            {status === "running" && ` · ${Math.round((live?.progress ?? 0) * 100)}%${live?.message ? ` · ${live.message}` : ""}`}
          </div>
          {(status === "running" || status === "queued") && (
            <div className={jobsCss.progress} data-testid="export-objects-progress">
              <div style={{ width: `${Math.round((live?.progress ?? 0) * 100)}%` }} />
            </div>
          )}
          {status === "queued" && live?.why_waiting && <div className={css.note}>{live.why_waiting}</div>}
          {status === "failed" && (
            <div className={css.error} role="alert">
              {live?.error ? `${live.error.message} ${live.error.hint}`.trim() : "Задание завершилось ошибкой."}
            </div>
          )}
          {status === "cancelled" && <div className={css.note}>Экспорт отменён: папка экспорта не создана.</div>}
          {report && (
            <div data-testid="export-objects-report">
              <div className={css.title}>{reportSummary(report)}</div>
              {shown && <div className={css.note}>Папка: {shown}</div>}
              {report.files.length > 0 && (
                <table className={css.table} data-testid="export-objects-files">
                  <thead>
                    <tr>
                      <th>Файл</th>
                      <th>Объект</th>
                      <th>Формат</th>
                      <th className={css.num}>Размер</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.files.slice(0, REPORT_ROWS).map((f) => (
                      <tr key={f.path}>
                        <td>{relativePath(f.path, shown)}</td>
                        <td>{f.object}</td>
                        <td>{f.format}</td>
                        <td className={css.num}>{f.size === null ? "—" : sizeText(f.size)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {report.files.length > REPORT_ROWS && <div className={css.note}>…и ещё {count(report.files.length - REPORT_ROWS)}: весь список — в журнале задания.</div>}
              {report.skipped.length > 0 && (
                <ul className={css.warn} data-testid="export-objects-skipped">
                  {report.skipped.map((s, i) => (
                    <li key={i}>
                      {s.object}: {s.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
        <div className={css.footer}>
          {!finished && (
            <button type="button" className={css.btn} disabled={live?.cancel_requested} data-testid="export-objects-cancel" onClick={() => void cancelJob(job).catch((e: unknown) => setError(errorText(e)))}>
              {live?.cancel_requested ? "Отмена…" : "Отменить"}
            </button>
          )}
          {shown && (
            <button type="button" className={`${css.btn} ${css.primary}`} data-testid="export-objects-show" onClick={() => bridge()?.showInFolder(shown)}>
              Показать в папке
            </button>
          )}
          {(status === "failed" || status === "cancelled") && (
            <button type="button" className={css.btn} onClick={() => setJob(null)}>
              Назад
            </button>
          )}
          <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
            {finished ? "Закрыть" : "Скрыть окно"}
          </button>
        </div>
        {error && <div className={css.error}>{error}</div>}
      </>
    );
  }

  return (
    <>
      {initial?.ids?.length ? <div className={css.note}>Только выбранные объекты: {count(initial.ids.length)}.</div> : null}
      {replaced.length > 0 && (
        <ul className={css.warn} data-testid="export-objects-replaced">
          {replaced.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      <table className={css.table} data-testid="export-objects-types">
        <thead>
          <tr>
            <th>Тип объектов</th>
            <th className={css.num}>Объектов</th>
            <th>Формат</th>
          </tr>
        </thead>
        <tbody>
          {(info?.types ?? []).map((t) => {
            const why = typeReason(t);
            const fmt = t.formats.find((f) => f.id === choice.formats[t.kind]);
            return (
              <tr key={t.kind} data-testid={`export-type-${t.kind}`} title={why ?? undefined}>
                <td>
                  <label className={css.title} style={{ fontSize: 13, fontWeight: 400 }}>
                    <input type="checkbox" checked={choice.types.includes(t.kind)} disabled={why !== null} aria-label={`Экспортировать: ${t.title}`} onChange={(e) => setChoice((c) => toggleType(c, t.kind, e.target.checked))} />
                    {t.title}
                  </label>
                  {why && <div className={css.note}>{why}</div>}
                </td>
                <td className={css.num}>{count(t.count)}</td>
                <td>
                  <select className={css.input} value={choice.formats[t.kind] ?? ""} disabled={why !== null} aria-label={`Формат: ${t.title}`} onChange={(e) => setChoice((c) => setFormat(c, t.kind, e.target.value))}>
                    {!fmt && <option value="">—</option>}
                    {t.formats.map((f) => (
                      <option key={f.id} value={f.id} disabled={f.reason !== null}>
                        {f.title}
                        {f.ext ? ` (${f.ext})` : ""}
                        {f.reason ? ` — ${f.reason}` : ""}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <label className={css.row}>
        <span className={css.label}>Папка</span>
        <span style={{ display: "flex", gap: 8, minWidth: 0 }}>
          <input className={css.input} readOnly value={folder ?? ""} placeholder="Папка, в которой создать папку экспорта" aria-label="Папка экспорта" data-testid="export-objects-folder" />
          <button type="button" className={css.btn} onClick={() => void pick()}>
            Выбрать папку…
          </button>
        </span>
      </label>
      <div className={css.note}>
        Будет создана папка «{exportFolderName(project)}» с подпапками по типам; существующие файлы не изменяются. Отмена и сбой не оставляют файлов.
      </div>
      {error && (
        <div className={css.error} role="alert">
          {error}
        </div>
      )}
      <div className={css.footer}>
        <button type="button" className={`${css.btn} ${css.primary}`} disabled={reason !== null || busy} data-testid="export-objects-start" onClick={() => void start()}>
          Экспортировать
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <span className={css.note} style={{ alignSelf: "center" }} data-testid="export-objects-reason">
          {reason ?? `Объектов: ${count(objects)}. Экспорт идёт заданием: ход — здесь и на вкладке «Задания».`}
        </span>
      </div>
    </>
  );
}
