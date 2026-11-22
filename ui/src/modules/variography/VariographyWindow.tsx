/**
 * The window «Вариография · ‹поле›» (ТЗ 8.6.4; ADR 0022 §15, decisions 13, 19): on the left — the data (`DataForm`)
 * and the parameters of 8.6.4 (blank — the default of the core, shown greyed); in the centre — the tabs «Оси» (the
 * charts of the three axes of the model and along the holes, `AxisCharts`), «Вдоль скважин», «Веер», «Карта»,
 * «Нормальные оценки»; on the right — the table of the model (`ModelTable`). An edit sends the preview 200 ms later
 * (≤ 20 000 points sampled by the core, the newest answer only), a drag of the ellipsoid waits for the release;
 * «Рассчитать» starts the job `variography.experimental`, then every settled edit starts it again with the previous
 * unfinished one cancelled (`calc.ts`). Its stages, `sampled`, the warnings and the refusals of the core are written
 * under the charts. `renderVariographyWindow` — the windows `variography` for the shell; a closed window drops its
 * working state (`dropWork`), an unsaved model asks before the window closes.
 */
import { createElement, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { focusWindow, setCloseGuard, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { cancelJob, jobsStore, refresh as refreshJobs } from "../jobs/state";
import { tablesStore } from "../tables/state";
import { SaveAsTrendButton } from "../trends/TrendWindow";
import { previewCalc, startExperimental } from "./api";
import { AxisCharts } from "./AxisCharts";
import { VARIO_WINDOW, windowTitle } from "./bind";
import { axesOf, calcNotes, chartType, countsLine, createRunner, INITIAL_RUNNER, pickShown, readJobCalc, readRefusal, type Refusal, type Runner, type RunnerState } from "./calc";
import { DataForm } from "./DataForm";
import { ModelTable } from "./ModelTable";
import { CALC_KEYS, checkValues, dropWork, experimentalBody, RANGES, setEllipsoid, setModel, setWork, spanText, valuesFromDefaults, variographyStore, VIEWS, workOf, type VarioView, type Work } from "./state";
import own from "./Variography.module.css";

export const VIEW_TITLES: Record<VarioView, string> = { axes: "Оси", downhole: "Вдоль скважин", fan: "Веер", map: "Карта", nscore: "Нормальные оценки" };

function ParamsForm({ work, winId }: { work: Work; winId: string }) {
  const defaults = useMemo(() => (work.defaults ? valuesFromDefaults(work.defaults.defaults) : {}), [work.defaults]);
  const checked = checkValues(work.values, CALC_KEYS);
  const set = (k: string, v: string) => setWork(winId, (w) => ({ values: { ...w.values, [k]: v } }));
  const downhole = (work.values.downhole ?? defaults.downhole ?? "true") !== "false";
  return (
    <fieldset className={css.group} data-testid="variography-params">
      <legend>Лаги и допуски</legend>
      {CALC_KEYS.filter((k) => downhole || !k.startsWith("dh_")).map((k) => (
        <label key={k} className={css.formRow} title={`${RANGES[k].label}: ${spanText(RANGES[k])}; пусто — по умолчанию`}>
          <span>{RANGES[k].label}</span>
          <input className={`${css.input}${checked.errors[k] ? ` ${own.bad}` : ""}`} aria-label={RANGES[k].label} aria-invalid={checked.errors[k] ? true : undefined} placeholder={defaults[k] ?? ""} value={work.values[k] ?? ""} onChange={(e) => set(k, e.target.value)} />
        </label>
      ))}
      <label className={css.check}>
        <input type="checkbox" checked={downhole} onChange={(e) => set("downhole", String(e.target.checked))} />
        Вдоль скважин
      </label>
      {Object.values(checked.errors).map((e) => (
        <p key={e} className={css.error}>
          {e}
        </p>
      ))}
    </fieldset>
  );
}

function RefusalView({ r, what }: { r: Refusal; what: string }) {
  return (
    <div className={own.refusal} role="alert">
      <p className={css.error}>
        {what}: {r.text}
      </p>
      {r.counts && <p className={css.note}>{countsLine(r.counts)}</p>}
    </div>
  );
}

const JOB_STATUS: Record<string, string> = { queued: "в очереди", running: "идёт" };

function useWork(winId: string): Work | null {
  const get = () => variographyStore.get().works[winId] ?? null;
  return useSyncExternalStore(variographyStore.subscribe, get, get);
}

export function VariographyWindow({ winId }: { winId: string }) {
  const app = useApp();
  const work = useWork(winId);
  const dragging = useSyncExternalStore(variographyStore.subscribe, () => variographyStore.get().dragging?.win === winId, () => false);
  const [run, setRun] = useState<RunnerState>(INITIAL_RUNNER);
  const [resultFor, setResultFor] = useState<string | null>(null);
  const [jobError, setJobError] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const runner = useMemo<Runner>(() => createRunner({ preview: previewCalc, start: startExperimental, cancel: cancelJob }, setRun), []);
  useEffect(() => () => runner.stop(), [runner]);
  useEffect(() => {
    if (!workOf(winId)) setWork(winId, {}); // a window restored from the layout: a new working state
  }, [winId, work === null]);

  const built = work ? experimentalBody(work.data, work.values, { axes: axesOf(work.model) }) : null;
  const body = built?.body ?? null;
  const cur = body ? JSON.stringify(body) : null;
  useEffect(() => runner.edit(body, dragging), [cur, dragging]);

  // the title follows the field
  const field = work?.data.field ?? null;
  useEffect(() => updateWindow(winId, { title: windowTitle(field) }), [winId, field]);

  // an unsaved model asks before the window closes (ТЗ 17.4.6)
  const dirty = work?.dirty === true && (work.model.structures.length > 0 || work.modelId !== null);
  useEffect(() => {
    setCloseGuard(winId, dirty ? () => askConfirm({ title: "Закрыть окно", message: "Рабочая модель вариограммы не сохранена. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }) : null);
  }, [winId, dirty]);

  // the job of the window: its result becomes the result of the window (the key — for «Подобрать», «Сохранить»)
  const getJob = () => (run.job ? (jobsStore.get().jobs.find((j) => j.source === run.job!.source && j.id === run.job!.id) ?? null) : null);
  const live = useSyncExternalStore(jobsStore.subscribe, getJob, getJob);
  useEffect(() => {
    if (!run.job || !live || !run.jobLive) return;
    if (live.status === "done" && live.result) {
      const r = readJobCalc(live.result);
      setWork(winId, { result: r, expKey: r.key });
      setResultFor(run.jobFor);
      setJobError(null);
      runner.finished(run.job);
    } else if (live.status === "failed") {
      setJobError(readRefusal(live.error ?? { message: "Расчёт не выполнен." }));
      runner.finished(run.job);
    } else if (live.status === "cancelled") runner.finished(run.job);
  }, [live, run.job, run.jobLive]);

  if (!work) return <p className={css.note}>Окно закрывается…</p>;
  const shown = pickShown(cur, work.result, resultFor, run.preview, run.previewFor);
  const type = chartType(work.model, work.data.transform);
  const calc = async () => {
    if (!body || busy) return;
    setBusy(true);
    setJobError(null);
    try {
      await runner.run(body);
      await refreshJobs().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };
  const command = (id: string) => {
    focusWindow(winId); // the commands act on the last active window (or on the window of `model_id`)
    void app.run(id, work.modelId !== null ? { model_id: work.modelId } : {});
  };
  const ellipsoid = () => (work.modelId !== null ? command("variography.show") : setEllipsoid(winId, { visible: !(work.ellipsoid?.visible ?? false) }));
  const view = VIEWS.includes(work.view) ? work.view : "axes";
  const fileName = `Вариография ${work.data.field ?? ""}`.trim();
  const previewRefused = run.previewError && run.previewFor === cur ? run.previewError : null;

  let centre: ReactNode;
  if (view === "fan" || view === "map" || view === "nscore") centre = <p className={own.empty}>Вкладка «{VIEW_TITLES[view]}» готовится — расчёт доступен командой «Рассчитать вариограммы» (what = {view === "nscore" ? "axes с нормальными оценками" : view}).</p>;
  else if (shown.calc) centre = <AxisCharts calc={shown.calc} model={work.model} type={type} fileName={fileName} downholeOnly={view === "downhole"} />;
  else centre = <p className={own.empty}>{body ? (run.previewBusy ? "Предпросмотр…" : "Нет расчёта: нажмите «Рассчитать»") : (built?.reason ?? "Задайте данные расчёта")}</p>;

  return (
    <div className={`${css.win} ${own.window}`} data-testid="variography-window">
      <div className={own.layout}>
        <aside className={own.left}>
          <DataForm value={work.data} activeSet={activeSet} onChange={(data) => setWork(winId, { data })} onDefaults={(defaults) => setWork(winId, { defaults })} />
          <ParamsForm work={work} winId={winId} />
        </aside>
        <main className={own.centre}>
          <div className={own.tabs} role="tablist">
            {VIEWS.map((v) => (
              <button key={v} type="button" role="tab" aria-selected={v === view} className={`${own.tab}${v === view ? ` ${own.on}` : ""}`} onClick={() => setWork(winId, { view: v })}>
                {VIEW_TITLES[v]}
              </button>
            ))}
          </div>
          {centre}
          <div className={own.notes} data-testid="variography-notes">
            {shown.calc && <p className={css.note}>{countsLine(shown.calc.counts)}</p>}
            {shown.calc && shown.kind && calcNotes(shown.calc, shown.kind).map((t) => (
              <p key={t} className={shown.calc?.warnings.includes(t) ? css.warn : css.note}>
                {t}
              </p>
            ))}
            {run.jobLive && live && (
              <p className={css.note} data-testid="variography-job">
                Задание №{live.id} {JOB_STATUS[live.status] ?? live.status}
                {live.message ? `: ${live.message}` : ""}
                {live.progress > 0 ? ` (${Math.round(live.progress * 100)} %)` : ""}{" "}
                <button type="button" className={css.btn} onClick={() => void cancelJob(live).catch(() => undefined)}>
                  Отменить
                </button>
              </p>
            )}
            {previewRefused && <RefusalView r={previewRefused} what="Предпросмотр" />}
            {run.startError && <RefusalView r={run.startError} what="Расчёт не начат" />}
            {jobError && <RefusalView r={jobError} what="Расчёт не выполнен" />}
          </div>
        </main>
        <aside className={own.right}>
          <ModelTable model={work.model} sigma2={shown.calc?.sigma2 ?? work.defaults?.sigma2 ?? null} sigma2Z={shown.calc?.sigma2_z ?? work.defaults?.sigma2_z ?? null} transform={work.data.transform} fix={work.fix} onChange={(m) => setModel(winId, m)} onFix={(fix) => setWork(winId, { fix })} />
        </aside>
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${own.grow}`}>{built?.reason ?? (work.dirty ? "Модель изменена и не сохранена" : "")}</span>
        <button type="button" className={css.primary} data-testid="variography-calc" disabled={!body || busy} onClick={() => void calc()}>
          Рассчитать
        </button>
        <button type="button" className={css.btn} disabled={!work.expKey} title={work.expKey ? "Подбор по осям последнего расчёта" : "Сначала рассчитайте вариограммы осей"} onClick={() => command("variography.fit")}>
          Подобрать
        </button>
        <button type="button" className={css.btn} disabled={!work.model.structures.length} onClick={ellipsoid}>
          Эллипсоид в 3D
        </button>
        <button type="button" className={css.btn} disabled={!work.expKey || app.registry.reason("variography.save") !== null} onClick={() => command("variography.save")}>
          Сохранить
        </button>
        <SaveAsTrendButton winId={winId} />
      </div>
    </div>
  );
}

/** Drops the working state of the windows «Вариография» that are gone (installed once). */
let watching = false;
export function watchClosedWindows(): void {
  if (watching) return;
  watching = true;
  windowStore.subscribe(() => {
    const open = new Set(windowStore.get().windows.map((w) => w.id));
    for (const id of Object.keys(variographyStore.get().works)) if (!open.has(id)) dropWork(id);
  });
}

/** The windows `variography` (the shell's renderer falls through to this). */
export function renderVariographyWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== VARIO_WINDOW) return null;
  watchClosedWindows();
  return createElement(VariographyWindow, { winId: w.id });
}
