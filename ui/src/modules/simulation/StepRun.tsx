/**
 * Step 6 of the wizard «Моделирование» (ТЗ 8.8.1: «… → реализации → задание»): what the row will do, what to look at before the run, and
 * «Выполнить»: the row is saved (when new or changed) and run by `simulate.run` — the job `simulate.run` writes the realizations as an object
 * of its own, the stages («Нормальные оценки», «Реализации k / N», «Запись») are in «Задания» and here, «Отменить» stops it with nothing
 * written. After the run — the journal and the buttons of the summary of the realizations and of the post-processing into the block model.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { blocksStore } from "../blocks/state";
import { numberText } from "../composites/params";
import type { DefaultsAnswer } from "../estimation/api";
import { thresholdsOf } from "../estimation/form";
import { useSel } from "../estimation/hooks";
import { countsText } from "../estimation/StepSamples";
import mod from "../estimation/Wizard.module.css";
import { cancelJob, jobsStore } from "../jobs/state";
import { KIND_SHORT, KIND_TITLES } from "./api";
import { categoriesOf, isCategorical, type SimForm, type VarioInfo } from "./form";
import { journalLines, runStore } from "./runState";
import { simulationStore } from "./state";
import { runWizard, simWizardStore } from "./wizardState";

export interface SummaryCtx {
  model: string | null;
  defaults: DefaultsAnswer | null;
  vario: Pick<VarioInfo, "rows">;
}

/** «Что будет сделано»: the kind, the model, the samples, the models of variogram, the indicators, the secondary variable, the search, the realizations. */
export function summaryRows(f: SimForm, ctx: SummaryCtx): { label: string; value: string }[] {
  const name = (id: number | null) => (id === null ? "не выбрана" : (ctx.vario.rows.find((r) => r.id === id)?.name ?? `№${id}`));
  const rows: { label: string; value: string }[] = [{ label: "Вид", value: KIND_TITLES[f.kind] }];
  rows.push({ label: "Блочная модель", value: `${ctx.model ?? "не выбрана"}${f.domainField && f.label ? `, домен «${f.label}» (${f.domainField})` : ", все блоки"}` });
  const d = ctx.defaults;
  rows.push({ label: "Пробы", value: `${f.field ?? "поле не выбрано"}${d ? `, ${countsText(d).toLowerCase()}` : ""}` });
  if (f.kind === "sis") {
    const items = f.sisBy === "thresholds" ? thresholdsOf(f.thresholds) : categoriesOf(f.categories);
    rows.push({ label: f.sisBy === "thresholds" ? "Пороги" : "Категории", value: Array.isArray(items) ? items.map((x) => numberText(x)).join("; ") : "не заданы" });
    rows.push({ label: "Модели индикаторов", value: f.ikModels === "per" ? (f.variogramIds.length ? f.variogramIds.map(name).join("; ") : "не выбраны") : name(f.variogramId) });
  } else rows.push({ label: "Модель вариограммы", value: name(f.variogramId) });
  if (f.kind === "cosgs") rows.push({ label: "Вторичная переменная", value: `${f.secField || "не выбрана"}, ρ ${f.rhoMode === "value" ? f.rho : "по пробам"}` });
  const from = f.search.from === "manual" ? `вручную: ${f.search.r.map((x) => x || "r1").join(" × ")} м` : "по модели вариограммы";
  rows.push({ label: "Поиск", value: `${from}; радиусы × ${f.factor}, n_max ${f.nMax}` });
  rows.push({ label: "Реализации", value: `${f.nReal}, зерно ${f.seed}, ранее смоделированных узлов ${f.nodes}` });
  return rows;
}

/** What to look at before the run. */
export function wizardWarnings(f: SimForm, ctx: SummaryCtx): string[] {
  const out: string[] = [];
  const d = ctx.defaults;
  if (d && d.samples.counts.used === 0) out.push("В домене нет ни одной пробы: моделировать нечего.");
  if (d?.mapping && f.label && !(d.mapping[f.label]?.length ?? 0)) out.push(`Домену блоков «${f.label}» не нашлось значения в поле домена проб: отметьте значения вручную.`);
  if (isCategorical(f) && f.topcut.mode !== "none") out.push("Для категорий урезка значений не применяется: уберите её.");
  if (f.kind === "cosgs" && f.rhoMode === "samples") out.push("ρ будет посчитан по пробам: нужно не меньше 10 пар проба–узел.");
  return out;
}

export function StepRun() {
  const app = useApp();
  const f = useSel(simWizardStore, (s) => s.form);
  const defaults = useSel(simWizardStore, (s) => s.defaults.data);
  const vario = useSel(simWizardStore, (s) => s.vario);
  const saving = useSel(simWizardStore, (s) => s.saving);
  const editing = useSel(simWizardStore, (s) => s.editing);
  const details = useSel(blocksStore, (s) => s.details);
  const list = useSel(blocksStore, (s) => s.list);
  const run = useSel(runStore, (s) => s);
  const jobs = useSel(jobsStore, (s) => s.jobs);
  const rows = useSel(simulationStore, (s) => s.rows);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? list.find((m) => m.id === f.modelId));
  const ctx: SummaryCtx = { model: model?.name ?? null, defaults, vario };
  const mine = run.simId === null ? run.phase !== "idle" : run.simId === editing?.id;
  const job = run.job ? jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined;
  const busy = run.phase === "running";
  const reason = readOnly ? "Проект открыт только для чтения" : busy ? "Моделирование уже выполняется" : f.modelId === null ? "Выберите блочную модель" : saving ? "Параметры сохраняются" : null;
  const go = () => void runWizard(app).catch(() => undefined); // a refusal stands under its field, the run — in the store
  const warnings = wizardWarnings(f, ctx);
  const doneId = run.phase === "done" ? run.simId : null;
  const stale = doneId !== null && rows.find((r) => r.id === doneId)?.stale;

  return (
    <>
      <fieldset className={css.group} data-testid="sim-step-6">
        <legend>Что будет сделано</legend>
        <dl className={mod.summary} data-testid="sim-summary">
          {summaryRows(f, ctx).map((r) => (
            <div key={r.label} style={{ display: "contents" }}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
        {warnings.length > 0 && (
          <ul className={mod.warn} role="status" data-testid="sim-warnings">
            {warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="sim-run">
        <legend>Задание</legend>
        <div className={mod.links}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? `Сохранить строку и выполнить моделирование (${KIND_SHORT[f.kind]}): реализации запишутся отдельным объектом`} data-testid="sim-run-go" onClick={go}>
            Выполнить моделирование
          </button>
          {busy && run.job && (
            <button type="button" className={css.btn} data-testid="sim-run-cancel" title="Отменить: реализации не запишутся" onClick={() => run.job && void cancelJob(run.job).catch(() => undefined)}>
              Отменить
            </button>
          )}
        </div>
        {busy && mine && (
          <div className={mod.links} role="status" data-testid="sim-run-progress">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход моделирования" />
            <span className={css.note}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        {mine && run.phase === "failed" && run.error && (
          <p className={css.error} role="alert" data-testid="sim-run-error" data-code={run.error.code}>
            {run.error.message} {run.error.hint}
          </p>
        )}
        {mine && run.note && run.phase !== "done" && (
          <p className={css.note} data-testid="sim-run-note">
            {run.note}
          </p>
        )}
        {mine && run.phase === "done" && run.result && (
          <div data-testid="sim-run-done">
            <p className={mod.verdict}>{run.summary}</p>
            <ul className={mod.warn} style={{ color: "var(--text)" }}>
              {journalLines(run.result).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
            {stale && <p className={css.note}>Параметры изменены после запуска: реализации устарели.</p>}
            <div className={mod.links}>
              <button type="button" className={css.btn} data-testid="sim-open-summary" onClick={() => void app.registry.execute("simulate.summary", { id: run.simId }, "user")}>
                Сводка реализаций…
              </button>
              <button type="button" className={css.btn} data-testid="sim-open-post" onClick={() => void app.registry.execute("simulate.post", { id: run.simId }, "user")}>
                Постобработка…
              </button>
            </div>
          </div>
        )}
      </fieldset>
    </>
  );
}
