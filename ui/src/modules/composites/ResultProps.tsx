/**
 * «Свойства» of a result of sample processing (ТЗ 8.0.2, 8.2.3; ADR 0018 §2): what it is and whether it is current
 * («устарел» with the reason), the source (tables with their revisions and fields, collars and surveys, wireframes),
 * the parameters the core ran with, the SHA-256 of the inputs, the version of the algorithm, the time, the summary
 * of the report; the actions of the menu of the tree as buttons. The window `composites-result` reads the result
 * from the results of its set and follows them (`composites.changed`).
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { VariantsTable } from "./ConditionsWindow";
import own from "./Composites.module.css";
import type { ResultRow } from "./api";
import { OreEdit } from "./OreEdit";
import { loadSetResults, paramFacts, recordFacts, reportFacts, resultLabel, resultsStore, sourceFacts, staleText, watchResults, type Fact } from "./results";
import { runResultAction } from "./ResultsTree";
import { toVariants, variantsUnit } from "./tools/conditions";
import { interceptMenu, interceptParamFacts, interceptReportFacts } from "../intercepts/results";
import { isInterceptOp } from "../intercepts/tools";

function FactList({ facts, testId }: { facts: readonly Fact[]; testId?: string }) {
  if (!facts.length) return <p className={css.note}>—</p>;
  return (
    <dl className={css.facts} data-testid={testId}>
      {facts.map((f, i) => (
        <span key={`${f.label}-${i}`} style={{ display: "contents" }}>
          <dt>{f.label}</dt>
          <dd>{f.value}</dd>
        </span>
      ))}
    </dl>
  );
}

/** The body of «Свойства» of one result (no store: the tests render it as is). */
export function ResultFacts({ r }: { r: ResultRow }) {
  // Э15 (`tt`, `intercepts`): the parameters as the windows name them, the orientation and the summary in Russian
  const icpt = isInterceptOp(r.op);
  const rep: ReturnType<typeof reportFacts> = icpt ? { summary: [], facts: interceptReportFacts(r) } : reportFacts(r);
  const stale = staleText(r);
  return (
    <>
      <h4 className={css.name}>{resultLabel(r)}</h4>
      {stale && (
        <p className={css.warn} data-testid="result-props-stale">
          Результат {stale}. «Перестроить» пересчитает его по прежним параметрам.
        </p>
      )}
      <fieldset className={css.group}>
        <legend>Результат</legend>
        <FactList facts={recordFacts(r)} testId="result-props-record" />
      </fieldset>
      <fieldset className={css.group}>
        <legend>Источник</legend>
        <FactList facts={sourceFacts(r)} testId="result-props-source" />
      </fieldset>
      <fieldset className={css.group}>
        <legend>Параметры</legend>
        <FactList facts={icpt ? interceptParamFacts(r) : paramFacts(r)} testId="result-props-params" />
      </fieldset>
      {(rep.summary.length > 0 || rep.facts.length > 0) && (
        <fieldset className={css.group}>
          <legend>Сводка</legend>
          {rep.summary.length > 0 && (
            <table className={own.summary} data-testid="result-props-summary">
              <thead>
                <tr>
                  <th />
                  <th>до</th>
                  <th>после</th>
                </tr>
              </thead>
              <tbody>
                {rep.summary.map((x) => (
                  <tr key={x.label}>
                    <th scope="row">{x.label}</th>
                    <td>{x.before}</td>
                    <td>{x.after}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <FactList facts={rep.facts} />
        </fieldset>
      )}
    </>
  );
}

export function ResultProps({ winId, resultId, setId }: { winId: string; resultId: number; setId: number | null }) {
  const app = useApp();
  const readOnly = useStore(app.store, (s) => s.projectReadOnly === true);
  const rows = useStore(resultsStore, (s) => (setId === null ? null : (s.bySet[setId] ?? null)));
  useEffect(() => {
    watchResults();
    if (setId !== null && resultsStore.get().bySet[setId] === undefined) void loadSetResults(setId);
  }, [setId]);
  const r = rows?.find((x) => x.id === resultId) ?? null;
  if (!r)
    return (
      <div className={css.win} data-testid="result-props">
        <div className={css.winBody}>
          <p className={css.note}>{rows ? `Результат №${resultId} удалён или не найден.` : "Чтение…"}</p>
        </div>
        <div className={css.footer}>
          <span className={css.grow} />
          <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    );
  const items = interceptMenu(r, readOnly).filter((it) => it.action !== "props");
  return (
    <div className={css.win} data-testid="result-props">
      <div className={css.winBody}>
        <ResultFacts r={r} />
        {r.op === "conditions" && (
          <fieldset className={css.group}>
            <legend>Варианты</legend>
            <VariantsTable variants={toVariants(r.report)} unit={variantsUnit(r.report)} field={typeof r.params?.field === "string" ? r.params.field : null} />
          </fieldset>
        )}
        {r.op === "ore" && r.table_id !== null && <OreEdit result={r} readOnly={readOnly} />}
      </div>
      <div className={css.footer}>
        {items.map((it) => (
          <button key={it.action} type="button" className={css.btn} disabled={it.reason !== null} title={it.reason ?? undefined} data-testid={`result-props-${it.action}`} onClick={() => void runResultAction(app, r, it.action)}>
            {it.label}
          </button>
        ))}
        <span className={css.grow} />
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
