/**
 * «Перепроверить» of a block model (`blocks.check`; Э17 17.41): the button of the group «Проверки» in «Свойства» (the panel of the
 * tree and the tab of the window of the model), the progress of the job with «Отменить», the refusal in Russian with its hint
 * and «Перечитать» when the model was changed meanwhile, the line of the last run. The checks themselves are drawn by the panels
 * from the details of the model, which the run brings up to date. The hooks are those of the window (`window/hooks.ts`: they
 * draw to static markup in the tests). React.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { jobsStore } from "../jobs/state";
import { cancelCheck, checkStore, rereadModel } from "./check";
import { useRegistryVersion, useStore } from "./window/hooks";

export const CHECK_COMMAND = "blocks.check";

export function CheckControl({ id }: { id: number }) {
  const app = useApp();
  useRegistryVersion(app.registry);
  const run = useStore(checkStore, (s) => s.runs[id]);
  const job = useStore(jobsStore, (s) => (run?.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  const running = run?.phase === "running";
  const reason = app.registry.reason(CHECK_COMMAND);
  const percent = job && job.progress >= 0 ? Math.min(100, Math.round(job.progress * 100)) : null;
  return (
    <div className={css.row} data-testid="props-check" data-phase={run?.phase ?? "idle"}>
      <button
        type="button"
        className={css.btn}
        disabled={reason !== null || running}
        title={reason ? `Перепроверить\nНедоступно: ${reason}` : "Пересчитать проверки модели: повторы, перекрытия, пустые и постоянные поля, объём. Версия модели не меняется."}
        data-testid="props-check-run"
        onClick={() => void app.run(CHECK_COMMAND, { model_id: id })}
      >
        Перепроверить
      </button>
      {running && (
        <>
          <span className={css.note} role="status" data-testid="props-check-progress">
            Проверка{percent !== null ? ` ${percent} %` : ""}
            {job?.message ? ` · ${job.message}` : ""}
          </span>
          <button type="button" className={css.btn} data-testid="props-check-cancel" onClick={() => void cancelCheck(id)}>
            Отменить
          </button>
        </>
      )}
      {run?.phase === "failed" && run.error && (
        <>
          <span className={css.error} role="alert" data-testid="props-check-error" data-code={run.error.code}>
            {run.error.message} {run.error.hint}
          </span>
          {run.conflict && (
            <button type="button" className={css.btn} data-testid="props-check-reread" onClick={() => void rereadModel(id)}>
              Перечитать
            </button>
          )}
        </>
      )}
      {(run?.phase === "done" || run?.phase === "cancelled") && run.note && (
        <span className={css.note} role="status" data-testid="props-check-note">
          {run.note}
        </span>
      )}
      {reason && (
        <span className={css.note} data-testid="props-check-reason">
          Недоступно: {reason}
        </span>
      )}
    </div>
  );
}
