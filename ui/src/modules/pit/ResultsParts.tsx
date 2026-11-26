/**
 * The parts the windows of the results of the pit optimisation share (ТЗ 8.11.5; ADR 0028 решения 25–27): the choice of the scenario, the line of a quick job
 * (the stage and the progress from the list of the jobs, the first refusal in words with its hint), the list of the warnings. Presentation only.
 */
import type { App } from "../../shell/app";
import css from "../../shell/PropsParts.module.css";
import { jobsStore } from "../jobs/state";
import type { ScenarioDetail, Warning } from "./answers";
import { warningText } from "./format";
import { useSel } from "./PitParts";
import mod from "./Results.module.css";
import type { JobRun } from "./resultsState";
import { pitStore, selectScenario } from "./state";

/** Why a window cannot build now, or null: no scenario, none read yet, no result, a project open for reading, a job runs. */
export function buildReason(app: App, id: number | null, scenario: ScenarioDetail | null, busy: boolean): string | null {
  if (app.store.get().projectReadOnly) return "Проект открыт только для чтения";
  if (id === null) return "Выберите сценарий карьера";
  if (!scenario || scenario.id !== id) return "Сценарий загружается: подождите секунду";
  if (scenario.state === "running") return "Сценарий считается: дождитесь конца расчёта";
  if (!scenario.has_result) return "У сценария нет результата: запустите расчёт";
  return busy ? "Построение идёт" : null;
}

/** The scenario the window works on: the scenarios of the project (those without a result are marked); a choice reads the scenario and its result. */
export function ScenarioPick({ id, disabled = false, testId }: { id: number | null; disabled?: boolean; testId: string }) {
  const rows = useSel(pitStore, (s) => s.scenarios);
  return (
    <label>
      <span>Сценарий</span>
      <select className={mod.select} value={id ?? ""} disabled={disabled} aria-label="Сценарий" data-testid={testId} onChange={(e) => void selectScenario(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— выберите —</option>
        {rows.map((r) => (
          <option key={r.id} value={r.id}>
            №{r.id} · {r.name}
            {r.has_result ? "" : " (нет результата)"}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The course of a quick job: the stage and the progress while it runs, the refusal in words when it failed, a word on a cancel. */
export function JobLine({ run, testId }: { run: JobRun<unknown>; testId: string }) {
  const job = useSel(jobsStore, (s) => (run.job ? (s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) ?? null) : null));
  if (run.phase === "running") {
    return (
      <p className={css.note} role="status" data-testid={`${testId}-running`}>
        {job?.message ?? "Строю…"}{" "}
        <progress className={mod.progress} max={1} value={job ? job.progress : undefined} aria-label="Ход задания" />
      </p>
    );
  }
  if (run.phase === "failed" && run.error) {
    return (
      <p className={css.error} role="alert" data-testid={`${testId}-error`} data-code={run.error.code}>
        {run.error.message} {run.error.hint}
      </p>
    );
  }
  if (run.phase === "cancelled") {
    return (
      <p className={css.note} data-testid={`${testId}-cancelled`}>
        Отменено.
      </p>
    );
  }
  return null;
}

export function WarningList({ warnings, testId }: { warnings: readonly Warning[]; testId: string }) {
  if (!warnings.length) return null;
  return (
    <ul className={mod.notes} data-testid={testId}>
      {warnings.map((w, i) => (
        <li key={`${w.code}-${i}`} data-code={w.code}>
          {warningText(w)}
        </li>
      ))}
    </ul>
  );
}
