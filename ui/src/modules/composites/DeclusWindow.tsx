/**
 * The window «Декластеризация» (ТЗ 8.2.1–8.2.3; ADR 0018 §7): the source (table, field); the method — cells in plan,
 * polygons in plan (BF), cells in 3D with anisy, anisz (GSLIB declus); the cell sizes cmin…cmax, their number, the
 * offsets of the origin, the criterion min / max. «Применить» → the job: the weights `DECL_W` in the source table
 * (the assistant is asked first — `confirm`). The curve «размер → среднее» with the best size and the naive mean,
 * and the facts of the report — of the last declustering of the table and field (`dh_results.report`), read again
 * when a job writes (`composites.changed`). The job runs on the whole set: there is no preview on one hole.
 */
import { useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import type { ResultRow } from "./api";
import { CurvePlot } from "./CurvePlot";
import { numberText } from "./params";
import { staleText } from "./results";
import { SourcePicker } from "./SourcePicker";
import { JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { useSetResults } from "./TopcutWindow";
import { bestPoint, declus, declusFacts, toDeclusReport, type DeclusReport } from "./tools/declus";
import { latestResult } from "./tools/topcut";

/** The curve «размер ячейки → среднее» of a report, the best size marked, the naive mean as the reference. */
export function DeclusCurve({ report, field }: { report: DeclusReport; field: string }) {
  if (report.method === "polygon" || !report.sizes.length) return null;
  const best = report.uniform ? null : bestPoint(report);
  return (
    <CurvePlot
      title={`Размер ячейки → среднее ${field}`}
      xs={report.sizes}
      ys={report.means}
      xLabel="Размер ячейки, м"
      yLabel={report.method === "cell3d" ? "Размер — по X; по Y и Z — с анизотропией" : undefined}
      refY={report.naive_mean === null ? null : { value: report.naive_mean, label: `без весов ${report.naive_mean.toLocaleString("ru-RU", { maximumFractionDigits: 4 })}` }}
      point={best ? { ...best, label: `${numberText(best.x)} м` } : null}
      width={420}
      height={220}
      testId="declus-curve"
    />
  );
}

/** The report of the last declustering: the curve, the facts, the notes of the kernel. */
export function DeclusReportView({ result }: { result: ResultRow }) {
  const report = toDeclusReport(result.report);
  const stale = staleText(result);
  const field = typeof result.params?.field === "string" ? result.params.field : "";
  const when = result.created_at ? new Date(result.created_at).toLocaleString("ru-RU") : "";
  return (
    <fieldset className={css.group} data-testid="declus-report">
      <legend>Последняя декластеризация {field ? `поля ${field}` : ""}</legend>
      {when && <p className={css.note}>{when}. Веса — поле DECL_W.</p>}
      {stale && <p className={css.warn}>{stale}</p>}
      {!report ? (
        <p className={css.note}>Отчёт расчёта не прочитан.</p>
      ) : (
        <>
          <DeclusCurve report={report} field={field} />
          <table className={own.summary}>
            <tbody>
              {declusFacts(report).map((f) => (
                <tr key={f.label}>
                  <th scope="row">{f.label}</th>
                  <td>{f.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.notes.length > 0 && (
            <ul className={own.notes}>
              {report.notes.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </fieldset>
  );
}

export function DeclusWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(declus, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  const results = useSetResults(t.src.setId);
  const field = t.src.fields[0] ?? null;
  const last = latestResult(results, "declus", t.src.table, field);

  if (t.job) return <JobView title={declus.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-declus">
      <div className={css.winBody}>
        <p className={css.note}>{declus.note}</p>
        <div className={own.split}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
            <SourcePicker needs={declus.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
            <ParamForm params={declus.params} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} />
            <p className={css.note}>
              {t.values.method === "polygon"
                ? "Полигоны в плане: вес пробы — площадь её полигона на сетке (как в BF)."
                : t.values.method === "cell3d"
                  ? "Ячейки в 3D: размер по X — от «от» до «до», по Y и Z — умножен на анизотропию (GSLIB declus)."
                  : "Ячейки в плане: для каждого размера — среднее по сдвигам начала; лучший размер — по критерию."}
            </p>
          </div>
          {last ? (
            <DeclusReportView result={last} />
          ) : (
            <fieldset className={css.group} data-testid="declus-report">
              <legend>Размер → среднее</legend>
              <p className={css.note}>Кривая «размер ячейки → среднее» — после «Применить»: расчёт идёт заданием по всему набору.</p>
            </fieldset>
          )}
        </div>
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra="Веса — поле DECL_W в таблице источника; «Отменить» в журнале правок скрывает его." />
    </div>
  );
}
