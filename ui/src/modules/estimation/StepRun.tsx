/**
 * Step 6 of the wizard «Оценка содержаний» (ТЗ 8.8.4: «… → выходные поля → задание»): the summary of what the row will do, what to
 * look at before the run, the preview of one block (the parameters as they stand, not saved, ≈ 20 ms: the estimate, the passes, the
 * samples and their weights) and «Выполнить»: the row is saved (when new or changed) and run by `estimate.run`; the question
 * «Заменить поля?» comes from the command when the model has the fields. The stages of the job are in «Задания» and here.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { blocksStore } from "../blocks/state";
import { filtersStore } from "../blocks/filters/state";
import { cancelJob, jobsStore } from "../jobs/state";
import { loadTrends, type Trend } from "../trends/api";
import { useLatest, useSel } from "./hooks";
import { journalLines } from "./planView";
import { PreviewView } from "./PreviewView";
import { Triple } from "./Parts";
import { runStore } from "./runState";
import { estimationStore } from "./state";
import { summaryRows, wizardWarnings, type SummaryCtx } from "./summary";
import mod from "./Wizard.module.css";
import { requestPreview, runWizard, setPreviewAt, wizardStore } from "./wizardState";

/** The centre of the model: the parent in the middle of its grid (the block the preview starts with). */
export const centreOf = (counts: readonly number[] | undefined): [string, string, string] | null => (counts && counts.length === 3 ? (counts.map((n) => String(Math.floor(n / 2))) as [string, string, string]) : null);

export function StepRun() {
  const app = useApp();
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const preview = useSel(wizardStore, (s) => s.preview);
  const saving = useSel(wizardStore, (s) => s.saving);
  const vario = useSel(wizardStore, (s) => s.vario);
  const details = useSel(blocksStore, (s) => s.details);
  const list = useSel(blocksStore, (s) => s.list);
  const filters = useSel(filtersStore, (s) => s.rows);
  const run = useSel(runStore, (s) => s);
  const rows = useSel(estimationStore, (s) => s.rows);
  const jobs = useSel(jobsStore, (s) => s.jobs);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const trends = useLatest<Trend[]>(f.search.trendId === null ? null : loadTrends, [f.search.trendId]);
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? list.find((m) => m.id === f.modelId));
  const ctx: SummaryCtx = {
    model: model ? { name: model.name, cells: model.cells, fields: model.fields.map((x) => x.name) } : null,
    defaults,
    names: {
      filter: (id) => filters.find((x) => x.id === id)?.name ?? null,
      trend: (id) => trends?.find((t) => t.id === id)?.name ?? null,
      variogram: (id) => vario.rows.find((r) => r.id === id)?.name ?? null,
    },
  };
  const centre = centreOf(model?.grid.counts);
  // the block the preview starts with: the middle of the model
  useEffect(() => {
    if (centre && !preview.at[0] && !preview.at[1] && !preview.at[2]) setPreviewAt(centre);
  }, [model?.id]);

  const mine = run.modelId === f.modelId;
  const job = run.job ? jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined;
  const busy = run.phase === "running";
  const reason = readOnly ? "Проект открыт только для чтения" : busy ? "Оценка уже выполняется" : f.modelId === null ? "Выберите блочную модель" : saving ? "Параметры сохраняются" : null;
  const go = () => void runWizard(app).catch(() => undefined); // a refusal stands under its field, the run — in the store
  const warnings = wizardWarnings(f, ctx);

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-6">
        <legend>Что будет сделано</legend>
        <dl className={mod.summary} data-testid="est-summary">
          {summaryRows(f, ctx).map((r, i) => (
            <div key={i} style={{ display: "contents" }}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
        {warnings.length > 0 && (
          <ul className={mod.warn} role="status" data-testid="est-warnings">
            {warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="est-preview">
        <legend>Предпросмотр одного блока</legend>
        <span className={css.note}>Оценка блока по текущим параметрам — без сохранения и запуска: сколько проб и с какими весами, какой проход.</span>
        <Triple labels={["Ячейка i (X)", "Ячейка j (Y)", "Ячейка k (Z)"]} values={preview.at} onChange={setPreviewAt} testId="est-preview-at" />
        <div className={mod.links}>
          <button type="button" className={css.btn} disabled={preview.phase === "loading"} data-testid="est-preview-go" onClick={() => void requestPreview()}>
            Показать
          </button>
          <button type="button" className={css.btn} disabled={!centre} data-testid="est-preview-centre" title="Ячейка в середине модели" onClick={() => centre && setPreviewAt(centre)}>
            Центр модели
          </button>
          {preview.phase === "loading" && <span className={css.note}>Считается…</span>}
        </div>
        {preview.error && (
          <p className={css.error} role="alert" data-testid="est-preview-error" data-code={preview.error.code}>
            {preview.error.message} {preview.error.hint}
          </p>
        )}
        {preview.stale && preview.data && (
          <p className={css.note} data-testid="est-preview-stale">
            Параметры изменились после предпросмотра: нажмите «Показать» ещё раз.
          </p>
        )}
        {preview.data && <PreviewView data={preview.data} unit={defaults?.samples.unit ?? null} />}
      </fieldset>

      <fieldset className={css.group} data-testid="est-run">
        <legend>Задание</legend>
        <div className={mod.links}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Сохранить строку и выполнить оценку: поля запишутся в модель целиком или никак"} data-testid="est-run-go" onClick={go}>
            Выполнить оценку
          </button>
          {busy && run.job && (
            <button type="button" className={css.btn} data-testid="est-run-cancel" title="Отменить: в модель ничего не запишется" onClick={() => run.job && void cancelJob(run.job).catch(() => undefined)}>
              Отменить
            </button>
          )}
        </div>
        {busy && mine && (
          <div className={mod.links} role="status" data-testid="est-run-progress">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход оценки" />
            <span className={css.note}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        {mine && run.phase === "failed" && run.error && (
          <p className={css.error} role="alert" data-testid="est-run-error" data-code={run.error.code}>
            {run.error.message} {run.error.hint}
          </p>
        )}
        {mine && run.note && run.phase !== "done" && (
          <p className={css.note} data-testid="est-run-note">
            {run.note}
          </p>
        )}
        {mine && run.phase === "done" && run.result && (
          <div data-testid="est-run-done">
            <p className={mod.verdict}>{run.summary}</p>
            <ul className={mod.warn} style={{ color: "var(--text)" }}>
              {journalLines(run.result, rows).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </div>
        )}
      </fieldset>
    </>
  );
}
