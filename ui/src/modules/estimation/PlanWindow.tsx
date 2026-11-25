/**
 * The window «План оценки» (ТЗ 8.8.4; ADR 0024 §10; docs/progress/e18-api.md §4, §6): the plans of a block model, the rows of the
 * plan — domain × field × method — in the order of the run, «Выполнить план» / «Выполнить выбранные» (`estimate.run`), the progress
 * of the job and the journal of the run by the passes. The window is floating, not modal; closing with a plan changed and not
 * saved asks first (an own dialog). The question «Заменить поля …?» is the command's (`bind.ts`).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { blocksStore } from "../blocks/state";
import { cancelJob, jobsStore } from "../jobs/state";
import { useSel } from "./hooks";
import { PlanLog } from "./PlanLog";
import { PlanRows } from "./PlanRows";
import { choosePlan, chooseModel, confirmDiscard, plansOfModel, planModels, planStore, rereadPlan, runArgsPicked, runArgsPlan, setPlanName } from "./planState";
import { runReason } from "./planView";
import mod from "./PlanWindow.module.css";
import { estimationStore, rowById } from "./state";
import { runStore } from "./runState";
import { savePlanByCommand } from "./saveCommands";

export function PlanWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(planStore, (x) => x);
  const run = useSel(runStore, (x) => x);
  useSel(blocksStore, (x) => x.list); // the models and the plans are read from the stores below: a change of the lists redraws the window
  useSel(estimationStore, (x) => x.plans);
  const models = planModels();
  const plans = plansOfModel(s.modelId);
  const list = useSel(estimationStore, (x) => x.rows);
  const jobs = useSel(jobsStore, (x) => x.jobs);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const job = run.job ? jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined;
  const model = models.find((m) => m.id === s.modelId);
  const rows = s.items.flatMap((id) => rowById(id) ?? []);
  const busy = run.phase === "running";
  const opts = { readOnly, busy, modelFound: model !== undefined };
  const planReason = runReason(rows, s.items.length, opts);
  const picked = s.items.filter((id) => s.picked.includes(id));
  const pickedReason = picked.length ? runReason(picked.flatMap((id) => rowById(id) ?? []), picked.length, opts) : "Отметьте строки в таблице";
  const saveReason = readOnly ? "Проект открыт только для чтения" : s.modelId === null ? "Выберите блочную модель" : s.planId !== null && !s.dirty ? "План не изменён" : null;

  // unsaved changes: the window asks before it closes
  useEffect(() => {
    setCloseGuard(winId, async () => !planStore.get().dirty || askConfirm({ title: "Закрыть окно", message: "План оценки изменён и не сохранён. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const go = (fn: () => void) => void confirmDiscard().then((ok) => ok && fn());
  const save = (asNew: boolean) => void savePlanByCommand(app, asNew); // the command `estimate.plan_save`; a refusal stands under the bar
  const start = (args: Record<string, unknown> | null) => args && void app.run("estimate.run", args).catch(() => undefined);
  const conflict = s.issue?.reread ? s.issue : null;
  const defaultName = model ? `План · ${model.name}` : "План оценки";

  return (
    <div className={mod.window} data-testid="estimation-plan">
      <div className={mod.bar}>
        <label>
          <span>Блочная модель</span>
          <select className={mod.select} value={s.modelId ?? ""} aria-label="Блочная модель" data-testid="plan-model" onChange={(e) => go(() => chooseModel(e.target.value ? Number(e.target.value) : null))}>
            <option value="">— выберите —</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>План</span>
          <select className={mod.select} value={s.planId ?? ""} disabled={s.modelId === null} aria-label="План" data-testid="plan-select" onChange={(e) => go(() => choosePlan(e.target.value ? Number(e.target.value) : null))}>
            <option value="">Новый план</option>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.items.length})
              </option>
            ))}
          </select>
        </label>
        <label className={mod.name}>
          <span>Название</span>
          <input className={css.input} value={s.name} placeholder={defaultName} spellCheck={false} aria-label="Название плана" data-testid="plan-name" onChange={(e) => setPlanName(e.target.value)} />
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={s.saving || saveReason !== null} title={saveReason ?? "Сохранить план: порядок и состав строк"} data-testid="plan-save" onClick={() => save(false)}>
            Сохранить
          </button>
          <button type="button" className={css.btn} disabled={s.planId === null || !!app.registry.reason("estimate.plan_delete")} title={app.registry.reason("estimate.plan_delete") ?? "Удалить план (строки останутся)"} data-testid="plan-delete" onClick={() => s.planId !== null && void app.run("estimate.plan_delete", { id: s.planId }).catch(() => undefined)}>
            Удалить план
          </button>
        </div>
      </div>
      <div className={mod.main}>
        {conflict && (
          <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="plan-conflict">
            <span>
              {conflict.message} {conflict.hint}
            </span>
            <button type="button" className={css.btn} data-testid="plan-reread" onClick={() => void rereadPlan()}>
              Перечитать
            </button>
            <button type="button" className={css.btn} data-testid="plan-save-new" onClick={() => save(true)}>
              Сохранить как новый
            </button>
          </p>
        )}
        {s.issue && !conflict && (
          <p className={css.error} role="alert" data-testid="plan-issue" data-code={s.issue.code}>
            {s.issue.message} {s.issue.hint}
          </p>
        )}
        <PlanRows readOnly={readOnly} />
        <PlanLog />
      </div>
      <div className={mod.footer}>
        {busy && (
          <div className={mod.progress} data-testid="plan-progress" role="status">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход оценки" />
            <span className={mod.stage}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={planReason !== null} title={planReason ?? "Выполнить все строки плана по порядку: поля запишутся в модель целиком или никак"} data-testid="plan-run" onClick={() => start(runArgsPlan())}>
            Выполнить план
          </button>
          <button type="button" className={css.btn} disabled={pickedReason !== null} title={pickedReason ?? "Выполнить только отмеченные строки (остальные домены сохранят значения)"} data-testid="plan-run-picked" onClick={() => start(runArgsPicked())}>
            Выполнить выбранные ({picked.length})
          </button>
          {busy && run.job && (
            <button type="button" className={css.btn} data-testid="plan-cancel" title="Отменить: в модель ничего не запишется" onClick={() => run.job && void cancelJob(run.job).catch(() => undefined)}>
              Отменить
            </button>
          )}
          <span className={css.note} data-testid="plan-saved">
            {s.dirty ? "Есть несохранённые изменения" : s.planId !== null ? `План №${s.planId}, версия ${s.version ?? 1}` : ""}
          </span>
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="plan-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
        {list.length === 0 && estimationStore.get().loaded && (
          <p className={css.note} data-testid="plan-no-rows">
            В проекте нет строк «Параметры оценки»: создайте первую в мастере «Оценка содержаний».
          </p>
        )}
      </div>
    </div>
  );
}
