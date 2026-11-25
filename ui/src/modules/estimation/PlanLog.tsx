/**
 * The journal of the last run in the window «План оценки» (ТЗ 8.8.4; docs/progress/e18-api.md §6): the fields written, each row of
 * the plan with the blocks by the status and by the pass, the singular systems, the samples used and averaged, the rows that wrote
 * into the same fields, the lines of the core's log. A refusal or a failure stands with its hint; «Нет» on «Заменить поля?» and a
 * cancelled run are a line, not an error.
 */
import css from "../../shell/PropsParts.module.css";
import { STATUS_LABELS } from "./api";
import { journalLines, passText, samplesText } from "./planView";
import mod from "./PlanWindow.module.css";
import { estimationStore } from "./state";
import { useSel } from "./hooks";
import { runStore } from "./runState";

const nf = (n: number): string => n.toLocaleString("ru-RU");

export function PlanLog() {
  const run = useSel(runStore, (x) => x);
  const list = useSel(estimationStore, (x) => x.rows);
  if (run.phase === "idle" && !run.note) return null;
  if (run.phase === "failed" && run.error) {
    return (
      <p className={css.error} role="alert" data-testid="plan-run-error" data-code={run.error.code}>
        {run.error.message} {run.error.hint}
      </p>
    );
  }
  if (run.phase === "running") return null;
  if (run.phase !== "done" || !run.result) {
    return (
      <p className={css.note} data-testid="plan-run-note">
        {run.note}
      </p>
    );
  }
  const r = run.result;
  const lines = journalLines(r, list);
  return (
    <section className={mod.log} aria-label="Журнал оценки" data-testid="plan-log">
      <h3>
        Оценка записана в модель «{run.modelName}» (версия {r.version})
      </h3>
      <p className={mod.facts} data-testid="plan-log-fields">
        Поля: {r.fields.join(", ")}
      </p>
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="plan-log-rows" aria-label="Итог по строкам плана">
          <thead>
            <tr>
              <th>Строка</th>
              <th className={mod.num}>Блоков</th>
              {STATUS_LABELS.map((w) => (
                <th key={w} className={mod.num}>
                  {w}
                </th>
              ))}
              <th>По проходам</th>
              <th>Пробы</th>
            </tr>
          </thead>
          <tbody>
            {r.rows.map((x) => {
              const name = list.find((l) => l.id === x.estimation_id)?.name ?? `${x.prefix}${x.label ?? ""}`;
              return (
                <tr key={x.estimation_id} data-testid={`plan-log-row-${x.estimation_id}`}>
                  <td>{name}</td>
                  <td className={mod.num}>{nf(x.counts.targets)}</td>
                  {STATUS_LABELS.map((w, i) => (
                    <td key={w} className={mod.num}>
                      {nf(x.counts.by_status[i] ?? 0)}
                    </td>
                  ))}
                  <td>{passText(x.counts.by_pass) || "—"}</td>
                  <td>{samplesText(x.counts) || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {lines.length > 0 && (
        <ul className={mod.lines} data-testid="plan-log-lines">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
