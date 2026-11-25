/**
 * The window «Сводка реализаций» (ТЗ 8.8.1; docs/progress/e18-api.md §9): the simulation (a list of the rows), and what the job kept of
 * its realizations — per realization the nodes, the mean, σ, minimum and maximum (for the categories of SIS the share of every category),
 * the conditioning samples of a node, the singular systems, the nodes with no datum. No realizations yet — a line with the way to run;
 * a row «устарел» is told so. «Постобработка…» and «Выполнить заново» are commands of the registry.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { CoreError } from "../../shell/api";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { useLatest, useSel } from "../estimation/hooks";
import mod from "../estimation/Wizard.module.css";
import { categoriesOf, KIND_SHORT, loadSummary, type Categories, type Summary } from "./api";
import { loadDetails, rowsByName, simulationStore } from "./state";
import { chooseSummary, SUMMARY_TITLE, summaryFacts, summaryStore, summaryTable } from "./summaryState";

/** The facts and the table of a summary: the realizations (continuous — mean, σ, min, max; categories — the shares), «Все вместе» at the foot. */
export function SummaryView({ summary, cats }: { summary: Summary; cats: Categories | null }) {
  const table = summaryTable(summary, cats);
  return (
    <>
      <dl className={mod.facts} data-testid="sum-facts">
        {summaryFacts(summary).flatMap(([k, v]) => [<dt key={`${k}-t`}>{k}</dt>, <dd key={`${k}-d`}>{v}</dd>])}
      </dl>
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="sum-table" aria-label="Реализации">
          <thead>
            <tr>
              {table.head.map((h, i) => (
                <th key={h} className={i === 0 ? mod.left : undefined}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr key={r.key} data-testid={`sum-row-${r.key}`} style={r.total ? { fontWeight: 600 } : undefined}>
                {r.cells.map((c, i) => (
                  <td key={i} className={i === 0 ? mod.left : undefined}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function SummaryWindow(_props: { winId: string }) {
  const app = useApp();
  const id = useSel(summaryStore, (s) => s.simId);
  const rows = useSel(simulationStore, (s) => s.rows);
  const details = useSel(simulationStore, (s) => s.details);
  const row = rows.find((r) => r.id === id);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const [error, setError] = useState<{ code: string; text: string } | null>(null);
  const fail = (e: unknown) => setError({ code: e instanceof CoreError ? e.code : "error", text: errorText(e) });
  // the read follows the run: another time of the last run — another summary
  const key = `${id}:${row?.version}:${row?.last_run?.at ?? ""}`;
  const summary = useLatest<Summary>(id === null || !row?.last_run ? null : () => loadSummary(id), [key], fail);
  useEffect(() => setError(null), [key]);
  useEffect(() => {
    if (id !== null && !details[id]) void loadDetails(id).catch(() => undefined);
  }, [id]);
  const info = id === null ? undefined : details[id];
  const cats = info ? categoriesOf(info.meta) : null;

  return (
    <div className={mod.window} data-testid="simulation-summary">
      <div className={mod.page} style={{ padding: "8px 12px", flex: 1 }}>
        <label className={css.formRow}>
          <span>Моделирование</span>
          <select className={css.input} value={id ?? ""} aria-label="Моделирование" data-testid="sum-select" onChange={(e) => chooseSummary(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{rows.length ? "— выберите моделирование —" : "нет моделирований в проекте"}</option>
            {rowsByName(rows).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} · {KIND_SHORT[r.kind]}
              </option>
            ))}
          </select>
        </label>
        {row?.stale && (
          <p className={css.note} data-testid="sum-stale">
            Строка устарела{row.stale_reason ? `: ${row.stale_reason}` : ""}. Реализации посчитаны по прежним данным или параметрам — выполните моделирование заново.
          </p>
        )}
        {row && !row.last_run && (
          <p className={css.note} data-testid="sum-empty">
            У моделирования «{row.name}» нет реализаций. Выполните его командой «Выполнить» в мастере или в меню строки.
          </p>
        )}
        {error && error.code === "no_realizations" && <p className={css.note}>{error.text}</p>}
        {error && error.code !== "no_realizations" && (
          <p className={css.error} role="alert" data-testid="sum-error" data-code={error.code}>
            {error.text}
          </p>
        )}
        {summary && <SummaryView summary={summary} cats={cats} />}
      </div>
      <div className={mod.footer}>
        <span className={mod.grow}>{summary ? `Реализаций: ${summary.realizations}, узлов: ${summary.nodes.toLocaleString("ru-RU")}` : SUMMARY_TITLE}</span>
        <button type="button" className={css.btn} disabled={id === null || readOnly} title={readOnly ? "Проект открыт только для чтения: постобработка пишет поля в блочную модель" : "Записать поля по реализациям в блочную модель"} data-testid="sum-post" onClick={() => void app.registry.execute("simulate.post", { id }, "user")}>
          Постобработка…
        </button>
        <button type="button" className={css.btn} disabled={id === null || readOnly} title={readOnly ? "Проект открыт только для чтения" : "Выполнить моделирование заново: прежние реализации заменятся"} data-testid="sum-run" onClick={() => void app.registry.execute("simulate.run", { id }, "user")}>
          Выполнить заново
        </button>
      </div>
    </div>
  );
}
