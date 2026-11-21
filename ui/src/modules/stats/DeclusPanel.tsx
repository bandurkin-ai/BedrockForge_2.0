/**
 * «Декластеризация» of the tab (ТЗ 7.29; ADR 0019 §7; ADR 0018: the kernels of Э12, nothing is written — `DECL_W`
 * stays with «Обработка проб → Декластеризация»): cells or polygons in plan; the mean with the weights and
 * without, the curve «размер ячейки → среднее» with the best size and the plain mean as the reference, the
 * weights, the notes of the kernel and the intervals left out.
 */
import { bestPoint, countsText, declusBody, declusCsv, declusCurve, declusFacts, fmt, type TabContext } from "./forms";
import { DeclusFields, Facts, ProblemBox, RunButton, useWhole } from "./Parts";
import { LinePlot } from "./Plots";
import { runAnalysis, setForm, statsStore } from "./state";
import css from "./Stats.module.css";

export function DeclusPanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.declus;
  const run = s.runs.declus;
  const r = run.result;
  const reason = declusBody(ctx, form).reason ?? null;
  const curve = r ? declusCurve(r) : null;
  const best = r ? bestPoint(r) : null;
  return (
    <div className={css.panel} data-testid="stats-declus">
      <div className={css.form}>
        <DeclusFields form={form} onChange={(p) => setForm("declus", p)} />
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("declus").catch(() => undefined)} />
      </div>
      {reason && <p className={css.hint}>{reason}</p>}
      <p className={css.note}>Расчёт ничего не записывает: веса DECL_W в таблицу пишет «Анализ → Обработка проб → Декластеризация».</p>
      {run.error && <ProblemBox problem={run.error} />}
      {r && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <div className={css.column}>
            <Facts label="Итоги декластеризации" rows={declusFacts(r)} />
            {r.notes.length > 0 && (
              <ul className={css.notes}>
                {r.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
            <p className={css.note}>{countsText(r.counts)}</p>
          </div>
          {curve && curve.xs.length > 0 && (
            <LinePlot
              label={`Размер ячейки → среднее ${r.field}`}
              xs={curve.xs}
              ys={curve.ys}
              xTitle="Размер ячейки, м"
              yTitle={`Среднее ${r.field}`}
              refY={r.naive_mean === null ? null : { value: r.naive_mean, label: `без весов ${fmt(r.naive_mean)}` }}
              point={best ? { ...best, label: `${fmt(best.x)} м` } : null}
              fileName={`Декластеризация ${r.table} ${r.field}`}
              csv={() => declusCsv(r)}
            />
          )}
        </div>
      )}
    </div>
  );
}
