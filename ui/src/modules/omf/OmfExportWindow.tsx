/**
 * The window «Экспорт OMF» (ТЗ 7.24; ADR 0017 §4; ported from BF `OmfExportDialog`): wireframes and DTMs, files of
 * lines, block models — each with a check box, an object that cannot be written disabled with its reason; the
 * file («Сохранить как…»); the job `export.omf` followed in the window — its progress and «Отменить» — then what was
 * written, the warnings and «Показать в папке». A file that is there is replaced only after «Заменить?» in our
 * own window (409 `files_exist`). The data — `omfExport.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { bridge } from "../../bridge";
import { askConfirm } from "../../shell/dialogs";
import { errorText, toggleId } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import jobsCss from "../jobs/Jobs.module.css";
import { cancelJob, jobsStore, refresh as refreshJobs, STATUS_TITLES } from "../jobs/state";
import { projectStore } from "../projects/state";
import { overwriteQuestion, startWithOverwrite } from "../rasters/export";
import { followExportable, startOmfExport, type ExportableLists, type OmfExportResult } from "./api";
import { chosenText, exportBody, exportName, exportReason, initialChosen, keepChosen, LIST_KEYS, LIST_TITLES, NONE, resultText, rowNote, type Chosen, type ListKey } from "./omfExport";

export function OmfExportWindow({ winId, ids }: { winId: string; ids?: number[] }) {
  const project = useStore(projectStore, (s) => s.current?.name ?? null);
  const [lists, setLists] = useState<ExportableLists | null>(null);
  const [chosen, setChosen] = useState<Chosen>(NONE);
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<{ source: string; id: number } | null>(null);
  const live = useStore(jobsStore, (s) => (job ? s.jobs.find((j) => j.source === job.source && j.id === job.id) : undefined));

  useEffect(
    () =>
      followExportable(
        (l, first) => {
          setLists(l);
          setChosen((cur) => (first ? initialChosen(l, ids ?? null) : keepChosen(l, cur)));
        },
        (e) => setError(errorText(e)),
      ),
    [ids],
  );

  const empty: ExportableLists = { wireframes: [], line_files: [], block_models: [] };
  const l = lists ?? empty;
  const reason = lists ? exportReason(l, chosen, path) : "Списки объектов читаются…";
  const name = useMemo(() => exportName(l, chosen, project), [l, chosen, project]);

  const choose = async () => {
    const p = await bridge()?.pickSave(name, "omf");
    if (p) setPath(p);
  };
  const setAll = (key: ListKey, on: boolean) => setChosen((c) => ({ ...c, [key]: on ? l[key].filter((o) => o.available).map((o) => o.id) : [] }));
  const start = async () => {
    if (reason || !path) return;
    setBusy(true);
    setError(null);
    try {
      const started = await startWithOverwrite((overwrite) => startOmfExport(exportBody(chosen, path), overwrite), (files) => askConfirm(overwriteQuestion(files)));
      if (started) {
        setJob(started);
        await refreshJobs().catch(() => undefined);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (job) {
    const status = live?.status ?? "queued";
    const finished = status === "done" || status === "failed" || status === "cancelled";
    const result = status === "done" ? (live?.result as Partial<OmfExportResult> | null) : null;
    const file = typeof result?.path === "string" ? result.path : null;
    return (
      <div className={css.win} data-testid="omf-export">
        <div className={css.winBody}>
          <h4 className={css.name}>Экспорт OMF · задание №{job.id}</h4>
          <div data-testid="omf-export-status" data-status={status}>
            {live?.cancel_requested && status === "running" ? "Отмена…" : STATUS_TITLES[status]}
            {status === "running" && ` · ${Math.round((live?.progress ?? 0) * 100)}%${live?.message ? ` · ${live.message}` : ""}`}
          </div>
          {(status === "running" || status === "queued") && (
            <div className={jobsCss.progress} data-testid="omf-export-progress">
              <div style={{ width: `${Math.round((live?.progress ?? 0) * 100)}%` }} />
            </div>
          )}
          {status === "queued" && live?.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
          {status === "failed" && (
            <p className={css.error} role="alert">
              {live?.error ? `${live.error.message} ${live.error.hint}`.trim() : "Задание завершилось ошибкой."}
            </p>
          )}
          {status === "cancelled" && <p className={css.note}>Экспорт отменён: файл не записан.</p>}
          {result && (
            <>
              <p data-testid="omf-export-result">{resultText(result)}</p>
              {file && <p className={css.note}>{file}</p>}
              {(result.warnings ?? []).length > 0 && (
                <ul className={css.list} data-testid="omf-export-warnings">
                  {(result.warnings ?? []).map((w, i) => (
                    <li key={i} className={css.warn}>
                      {w}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
        <div className={css.footer}>
          <span className={css.grow} />
          {!finished && (
            <button type="button" className={css.btn} disabled={live?.cancel_requested} onClick={() => void cancelJob(job).catch((e: unknown) => setError(errorText(e)))}>
              {live?.cancel_requested ? "Отмена…" : "Отменить"}
            </button>
          )}
          {file && (
            <button type="button" className={css.primary} data-testid="omf-export-show" onClick={() => bridge()?.showInFolder(file)}>
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
      </div>
    );
  }

  return (
    <div className={css.win} data-testid="omf-export">
      <div className={css.winBody}>
        <p className={css.note}>
          OMF v1 (Leapfrog OMF 0.9): каркасы и ЦТМ — поверхности, файлы линий — наборы линий, регулярные блочные модели — объёмы с данными в ячейках. Координаты — двойной точности.
        </p>
        {LIST_KEYS.map((key) => {
          const rows = l[key];
          const ready = rows.filter((o) => o.available);
          const all = ready.length > 0 && ready.every((o) => chosen[key].includes(o.id));
          return (
            <fieldset key={key} className={css.group} data-testid={`omf-export-${key}`}>
              <legend>{LIST_TITLES[key]}</legend>
              {rows.length ? (
                <div className={css.layers}>
                  <label className={`${css.check} ${css.file}`}>
                    <input type="checkbox" checked={all} disabled={!ready.length} aria-label={`Все: ${LIST_TITLES[key]}`} onChange={(e) => setAll(key, e.target.checked)} />
                    Все
                    <span className={css.count}>{count(rows.length)}</span>
                  </label>
                  {rows.map((o) => {
                    const why = o.reason ? `${o.reason.message} ${o.reason.hint}`.trim() : null;
                    return (
                      <label key={o.id} className={`${css.check} ${css.layer}`} title={why ?? undefined} data-testid={`omf-export-row-${o.id}`} data-available={o.available ? "yes" : "no"}>
                        <input
                          type="checkbox"
                          checked={chosen[key].includes(o.id)}
                          disabled={!o.available}
                          aria-label={o.name}
                          onChange={(e) => setChosen((c) => ({ ...c, [key]: toggleId(c[key], o.id, e.target.checked) }))}
                        />
                        {o.folder ? `${o.folder} › ${o.name}` : o.name}
                        <span className={css.count}>{why ? `не записывается: ${why}` : rowNote(key, o)}</span>
                      </label>
                    );
                  })}
                </div>
              ) : (
                <p className={css.note}>{lists ? "Нет объектов" : "Чтение…"}</p>
              )}
            </fieldset>
          );
        })}
        <p className={css.note} data-testid="omf-export-chosen">
          {chosenText(chosen)}
        </p>
        <label className={css.formRow}>
          <span>Файл</span>
          <span className={css.inline}>
            <input className={css.input} readOnly value={path ?? ""} placeholder={name} aria-label="Файл OMF" />
            <button type="button" className={css.btn} onClick={() => void choose()}>
              Сохранить как…
            </button>
          </span>
        </label>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{reason ?? "Экспорт идёт заданием: ход — здесь и на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={reason !== null || busy} onClick={() => void start()}>
          Экспортировать
        </button>
      </div>
    </div>
  );
}
