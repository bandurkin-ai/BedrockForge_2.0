/**
 * The wizard «Моделирование» (ТЗ 8.8.1; Д-23): kind, model and domain of the blocks → samples → model of variogram (thresholds or
 * categories for SIS, the secondary variable for co-SGS) → search → realizations (number, seed, tails) → job. Steps 1 and 2 are those of the
 * estimation (`StepTarget`, `StepSamples` through the host of `host.ts`). «Сохранить» writes the row of «Геостатистика → Моделирование»
 * (`simulate.save`; a 409 `version_conflict` is answered with «Перечитать» / «Сохранить как новое»). The window is floating, not modal;
 * closing with unsaved changes asks first (an own dialog).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { FormHostContext } from "../estimation/host";
import { useSel } from "../estimation/hooks";
import { StepSamples } from "../estimation/StepSamples";
import { StepTarget } from "../estimation/StepTarget";
import mod from "../estimation/Wizard.module.css";
import { SIM_LAST_STEP, SIM_STEP_TITLES, simStepOfParam } from "./form";
import { simulationHost } from "./host";
import { StepKind } from "./StepKind";
import { StepReal } from "./StepReal";
import { StepRun } from "./StepRun";
import { StepSearch } from "./StepSearch";
import { StepVario } from "./StepVario";
import { defaultsQueryOf, loadVariogramModels, requestDefaults, rereadSimulation, setIssue, setStep, simWizardStore } from "./wizardState";

export function SimWizard({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(simWizardStore, (x) => x);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const key = JSON.stringify(defaultsQueryOf(s.form));
  // the core is asked for the defaults of the data of the form after a pause of the typing
  useEffect(() => {
    requestDefaults();
  }, [key]);
  useEffect(() => {
    if (!simWizardStore.get().vario.loaded) void loadVariogramModels();
  }, []);
  // unsaved changes: the window asks before it closes
  useEffect(() => {
    setCloseGuard(winId, async () => !simWizardStore.get().dirty || askConfirm({ title: "Закрыть мастер", message: "Параметры моделирования изменены и не сохранены. Закрыть мастер без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const badStep = s.issue ? simStepOfParam(s.issue.param) : 0;
  const save = (asNew: boolean) => void app.run("simulate.save", asNew ? { as_new: true } : {}); // the command `simulate.save`; a refusal stands under its field
  const reason = readOnly ? "Проект открыт только для чтения" : null;
  const conflict = s.issue?.reread ? s.issue : null;

  return (
    <FormHostContext.Provider value={simulationHost}>
      <div className={mod.window} data-testid="simulation-wizard">
        <div className={mod.body}>
          <ol className={mod.steps} data-testid="sim-steps" aria-label="Шаги мастера">
            {SIM_STEP_TITLES.map((t, i) => {
              const n = i + 1;
              return (
                <li key={t}>
                  <button type="button" aria-current={s.step === n ? "step" : undefined} data-bad={badStep === n} data-testid={`sim-step-${n}-tab`} onClick={() => setStep(n)}>
                    <span className={mod.no}>{n}</span>
                    <span>{t}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          <div className={mod.page} data-testid="sim-page">
            {s.step === 1 && (
              <>
                <StepKind />
                <StepTarget />
              </>
            )}
            {s.step === 2 && <StepSamples />}
            {s.step === 3 && <StepVario />}
            {s.step === 4 && <StepSearch />}
            {s.step === 5 && <StepReal />}
            {s.step === 6 && <StepRun />}
            {s.issue && s.issue.param === null && !conflict && (
              <p className={css.error} role="alert" data-testid="sim-issue-general">
                {s.issue.message} {s.issue.hint}
              </p>
            )}
          </div>
        </div>
        {conflict && (
          <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="sim-conflict">
            <span>
              {conflict.message} {conflict.hint}
            </span>
            <button type="button" className={css.btn} data-testid="sim-reread" onClick={() => void rereadSimulation().then(() => setIssue(null))}>
              Перечитать
            </button>
            <button type="button" className={css.btn} data-testid="sim-save-new" onClick={() => save(true)}>
              Сохранить как новое
            </button>
          </p>
        )}
        <div className={mod.footer}>
          <button type="button" className={css.btn} disabled={s.step <= 1} data-testid="sim-back" onClick={() => setStep(s.step - 1)}>
            Назад
          </button>
          <button type="button" className={css.btn} disabled={s.step >= SIM_LAST_STEP} data-testid="sim-next" onClick={() => setStep(s.step + 1)}>
            Далее
          </button>
          <span className={mod.grow} data-testid="sim-saved">
            {s.saved && !s.dirty ? `Сохранено: «${s.saved.name}», версия ${s.saved.version}` : s.editing ? `Правка строки №${s.editing.id}` : ""}
          </span>
          <button type="button" className={css.primary} disabled={s.saving || !!reason} title={reason ?? "Сохранить строку в «Геостатистика → Моделирование»"} data-testid="sim-save" onClick={() => save(false)}>
            Сохранить
          </button>
          <button type="button" className={css.btn} data-testid="sim-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </FormHostContext.Provider>
  );
}
