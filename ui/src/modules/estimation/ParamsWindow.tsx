/**
 * The window «Параметры оценки» (ТЗ 8.8.4: «В проекте: „Параметры оценки“»): a stored row on one page — the same fields as the steps
 * of the wizard (model and target, samples, method and variogram, search and passes, output fields), to look at and to change.
 * «Сохранить» replaces the row over its version (409 — «Перечитать» / «Сохранить как новую»); the run, the preview and the summary
 * are the wizard's (step 6). The window and the wizard are two views of the same form: one row is edited at a time. Floating, not
 * modal; closing with unsaved changes asks first (an own dialog).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { defaultsQueryOf } from "./form";
import { useSel } from "./hooks";
import { StepMethod } from "./StepMethod";
import { StepOutputs } from "./StepOutputs";
import { StepSamples } from "./StepSamples";
import { StepSearch } from "./StepSearch";
import { StepTarget } from "./StepTarget";
import { stateText } from "./planView";
import { estimationStore, rowById } from "./state";
import mod from "./Wizard.module.css";
import { loadVariogramModels, requestDefaults, rereadEstimation, setIssue, wizardStore } from "./wizardState";
import { saveRowByCommand } from "./saveCommands";

export const PARAMS_WINDOW = "estimation-params";
export const PARAMS_TITLE = "Параметры оценки";

export function ParamsWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(wizardStore, (x) => x);
  useSel(estimationStore, (x) => x.rows);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const key = JSON.stringify(defaultsQueryOf(s.form));
  useEffect(() => {
    requestDefaults();
  }, [key]);
  useEffect(() => {
    if (!wizardStore.get().vario.loaded) void loadVariogramModels();
  }, []);
  useEffect(() => {
    setCloseGuard(winId, async () => !wizardStore.get().dirty || askConfirm({ title: "Закрыть окно", message: "Параметры оценки изменены и не сохранены. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const row = s.editing ? rowById(s.editing.id) : undefined;
  const state = row ? stateText(row) : null;
  const reason = readOnly ? "Проект открыт только для чтения" : s.editing === null ? "Откройте сохранённую строку" : null;
  const save = (asNew: boolean) => void saveRowByCommand(app, asNew); // the command `estimate.save`; a refusal stands under its field
  const conflict = s.issue?.reread ? s.issue : null;

  return (
    <div className={mod.window} data-testid="estimation-params">
      <div className={mod.page} data-testid="params-page" style={{ padding: "8px 12px", flex: 1 }}>
        <p className={css.note} data-testid="params-head">
          {s.editing ? `Строка №${s.editing.id} «${s.form.name || row?.name || ""}», версия ${s.editing.version}` : "Строка не открыта: выберите её в разделе «Геостатистика → Параметры оценки» дерева."}
          {state ? ` · ${state}` : ""}
        </p>
        <StepTarget />
        <StepSamples />
        <StepMethod />
        <StepSearch />
        <StepOutputs />
        {s.issue && s.issue.param === null && !conflict && (
          <p className={css.error} role="alert" data-testid="params-issue">
            {s.issue.message} {s.issue.hint}
          </p>
        )}
      </div>
      {conflict && (
        <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="params-conflict">
          <span>
            {conflict.message} {conflict.hint}
          </span>
          <button type="button" className={css.btn} data-testid="params-reread" onClick={() => void rereadEstimation().then(() => setIssue(null))}>
            Перечитать
          </button>
          <button type="button" className={css.btn} data-testid="params-save-new" onClick={() => save(true)}>
            Сохранить как новую
          </button>
        </p>
      )}
      <div className={mod.footer}>
        <span className={mod.grow} data-testid="params-saved">
          {s.dirty ? "Есть несохранённые изменения" : s.saved ? `Сохранено: версия ${s.saved.version}` : ""}
        </span>
        <button type="button" className={css.btn} disabled={!s.editing || s.dirty} data-testid="params-wizard" title={s.dirty ? "Сначала сохраните изменения" : "Открыть эту строку в мастере: предпросмотр блока и запуск"} onClick={() => void app.run("estimate.open", s.editing ? { id: s.editing.id } : {}).catch(() => undefined)}>
          В мастере…
        </button>
        <button type="button" className={css.primary} disabled={s.saving || reason !== null || !s.dirty} title={reason ?? (s.dirty ? "Сохранить строку «Параметры оценки»" : "Параметры не изменены")} data-testid="params-save" onClick={() => save(false)}>
          Сохранить
        </button>
        <button type="button" className={css.btn} data-testid="params-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
