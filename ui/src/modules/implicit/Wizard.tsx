/**
 * The wizard «Новая модель» (ТЗ 8.9.4): boundary and step → domains of the lithology → interpolation → result. «Сохранить» writes the
 * model (`POST` / `PUT /models`; the buttons run the command `implicit.save`: the registry, the log, the assistant see the same save);
 * «Сохранить и построить» builds it at once. A 409 `version_conflict` is answered with «Перечитать» / «Сохранить как новую». The window
 * is floating, not modal; closing with unsaved changes asks first (an own dialog).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { LAST_STEP, STEP_TITLES, stepOfParam } from "./form";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { WizardBoundary } from "./WizardBoundary";
import { WizardDomains } from "./WizardDomains";
import { WizardInterp } from "./WizardInterp";
import { WizardResult } from "./WizardResult";
import { ensureDefaults, rereadModel, setIssue, setStep, wizardStore } from "./wizardState";

export function Wizard({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(wizardStore, (x) => x);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  useEffect(() => {
    void ensureDefaults();
  }, []);
  // unsaved changes: the window asks before it closes
  useEffect(() => {
    setCloseGuard(winId, async () => !wizardStore.get().dirty || askConfirm({ title: "Закрыть мастер", message: "Модель изменена и не сохранена. Закрыть мастер без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const badStep = s.issue ? stepOfParam(s.issue.param) : 0;
  const save = (asNew: boolean, build: boolean) => void app.run("implicit.save", { ...(asNew ? { as_new: true } : {}), ...(build ? { build: true } : {}) }); // the command; a refusal stands under its field
  const reason = readOnly ? "Проект открыт только для чтения" : null;
  const conflict = s.issue?.reread ? s.issue : null;

  return (
    <div className={mod.window} data-testid="implicit-wizard">
      <div className={mod.body}>
        <ol className={mod.steps} data-testid="imp-steps" aria-label="Шаги мастера">
          {STEP_TITLES.map((t, i) => {
            const n = i + 1;
            return (
              <li key={t}>
                <button type="button" aria-current={s.step === n ? "step" : undefined} data-bad={badStep === n} data-testid={`imp-step-${n}-tab`} onClick={() => setStep(n)}>
                  <span className={mod.no}>{n}</span>
                  <span>{t}</span>
                </button>
              </li>
            );
          })}
        </ol>
        <div className={mod.page} data-testid="imp-page">
          {s.defaults.phase === "error" && (
            <p className={css.error} role="alert" data-testid="imp-defaults-error">
              {s.defaults.error}
            </p>
          )}
          {s.step === 1 && <WizardBoundary />}
          {s.step === 2 && <WizardDomains />}
          {s.step === 3 && <WizardInterp />}
          {s.step === 4 && <WizardResult />}
          {s.issue && s.issue.param === null && !conflict && (
            <p className={css.error} role="alert" data-testid="imp-issue-general">
              {s.issue.message} {s.issue.hint}
            </p>
          )}
        </div>
      </div>
      {conflict && (
        <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="imp-conflict">
          <span>
            {conflict.message} {conflict.hint}
          </span>
          <button type="button" className={css.btn} data-testid="imp-reread" onClick={() => void rereadModel().then(() => setIssue(null))}>
            Перечитать
          </button>
          <button type="button" className={css.btn} data-testid="imp-save-new" onClick={() => save(true, false)}>
            Сохранить как новую
          </button>
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.btn} disabled={s.step <= 1} data-testid="imp-back" onClick={() => setStep(s.step - 1)}>
          Назад
        </button>
        <button type="button" className={css.btn} disabled={s.step >= LAST_STEP} data-testid="imp-next" onClick={() => setStep(s.step + 1)}>
          Далее
        </button>
        <span className={mod.grow} data-testid="imp-saved">
          {s.saved && !s.dirty ? `Сохранено: «${s.saved.name}», версия ${s.saved.version}` : s.editing ? `Правка модели №${s.editing.id}` : ""}
        </span>
        <button type="button" className={css.btn} disabled={s.saving || !!reason} title={reason ?? "Сохранить модель в разделе «Неявные модели»"} data-testid="imp-save" onClick={() => save(false, false)}>
          Сохранить
        </button>
        <button type="button" className={css.primary} disabled={s.saving || !!reason} title={reason ?? "Сохранить и сразу построить поверхности и тела"} data-testid="imp-save-build" onClick={() => save(false, true)}>
          Сохранить и построить
        </button>
        <button type="button" className={css.btn} data-testid="imp-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
