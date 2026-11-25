/**
 * The wizard «Оценка содержаний» (ТЗ 8.8.4): model, restrictor, targets → samples → method and model of variogram → search and
 * passes → output fields → job (summary, preview of one block, run). «Сохранить» writes the row of «Параметры оценки»
 * (`POST` / `PUT /estimations`); a 409 `version_conflict` is answered with «Перечитать» / «Сохранить как новые». The window is
 * floating, not modal; closing with unsaved changes asks first (an own dialog).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { defaultsQueryOf, LAST_EDITED_STEP, STEP_TITLES, stepOfParam } from "./form";
import { loadVariogramModels, requestDefaults, rereadEstimation, saveWizard, setIssue, setStep, wizardStore } from "./wizardState";
import { StepMethod } from "./StepMethod";
import { StepOutputs } from "./StepOutputs";
import { StepRun } from "./StepRun";
import { StepSearch } from "./StepSearch";
import { StepSamples } from "./StepSamples";
import { StepTarget } from "./StepTarget";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";

export function Wizard({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(wizardStore, (x) => x);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const key = JSON.stringify(defaultsQueryOf(s.form));
  // the core is asked for the defaults of the data of the form after a pause of the typing
  useEffect(() => {
    requestDefaults();
  }, [key]);
  useEffect(() => {
    if (!wizardStore.get().vario.loaded) void loadVariogramModels();
  }, []);
  // unsaved changes: the window asks before it closes
  useEffect(() => {
    setCloseGuard(winId, async () => !wizardStore.get().dirty || askConfirm({ title: "Закрыть мастер", message: "Параметры оценки изменены и не сохранены. Закрыть мастер без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const badStep = s.issue ? stepOfParam(s.issue.param) : 0;
  const save = (asNew: boolean) => void saveWizard({ asNew }).catch(() => undefined); // a refusal stands under its field
  const reason = readOnly ? "Проект открыт только для чтения" : null;
  const conflict = s.issue?.reread ? s.issue : null;

  return (
    <div className={mod.window} data-testid="estimation-wizard">
      <div className={mod.body}>
        <ol className={mod.steps} data-testid="est-steps" aria-label="Шаги мастера">
          {STEP_TITLES.map((t, i) => {
            const n = i + 1;
            return (
              <li key={t}>
                <button type="button" aria-current={s.step === n ? "step" : undefined} data-bad={badStep === n} data-testid={`est-step-${n}-tab`} onClick={() => setStep(n)}>
                  <span className={mod.no}>{n}</span>
                  <span>{t}</span>
                </button>
              </li>
            );
          })}
        </ol>
        <div className={mod.page} data-testid="est-page">
          {s.step === 1 && <StepTarget />}
          {s.step === 2 && <StepSamples />}
          {s.step === 3 && <StepMethod />}
          {s.step === 4 && <StepSearch />}
          {s.step === 5 && <StepOutputs />}
          {s.step === 6 && <StepRun />}

          {s.issue && s.issue.param === null && !conflict && (
            <p className={css.error} role="alert" data-testid="est-issue-general">
              {s.issue.message} {s.issue.hint}
            </p>
          )}
        </div>
      </div>
      {conflict && (
        <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="est-conflict">
          <span>
            {conflict.message} {conflict.hint}
          </span>
          <button type="button" className={css.btn} data-testid="est-reread" onClick={() => void rereadEstimation().then(() => setIssue(null))}>
            Перечитать
          </button>
          <button type="button" className={css.btn} data-testid="est-save-new" onClick={() => save(true)}>
            Сохранить как новые
          </button>
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.btn} disabled={s.step <= 1} data-testid="est-back" onClick={() => setStep(s.step - 1)}>
          Назад
        </button>
        <button type="button" className={css.btn} disabled={s.step >= LAST_EDITED_STEP} data-testid="est-next" onClick={() => setStep(s.step + 1)}>
          Далее
        </button>
        <span className={mod.grow} data-testid="est-saved">
          {s.saved && !s.dirty ? `Сохранено: «${s.saved.name}», версия ${s.saved.version}` : s.editing ? `Правка строки №${s.editing.id}` : ""}
        </span>
        <button type="button" className={css.primary} disabled={s.saving || !!reason} title={reason ?? "Сохранить строку в «Геостатистика → Параметры оценки»"} data-testid="est-save" onClick={() => save(false)}>
          Сохранить
        </button>
        <button type="button" className={css.btn} data-testid="est-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
