/**
 * «Бутстрап» of the tab (ТЗ 7.29; ADR 0019 §4, §7; перенос BF): resampling with replacement (with the weights of
 * declustering, if asked — a point is drawn with the probability of its weight); the intervals of the mean, the
 * median, σ, CV, P10, P90 in one table and the histogram of the realizations of the chosen statistic with the
 * observed value and the bounds of the interval. The seed is told: «Повторить с этим зерном» gives the same
 * realizations.
 */
import type { BootStatistic } from "./api";
import { bootRows, bootstrapBody, bootstrapCsv, CONFIDENCES, countsText, percentText, statisticTitle, STATISTICS, type TabContext } from "./forms";
import { DeclusFields, Field, NumberInput, ProblemBox, RunButton, useWhole } from "./Parts";
import { BarsPlot } from "./Plots";
import { runAnalysis, setForm, statsStore } from "./state";
import css from "./Stats.module.css";

export function BootstrapPanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.bootstrap;
  const run = s.runs.bootstrap;
  const r = run.result;
  const reason = bootstrapBody(ctx, form).reason ?? null;
  const entry = r?.statistics[form.statistic] ?? null;
  const title = statisticTitle(form.statistic);
  return (
    <div className={css.panel} data-testid="stats-bootstrap">
      <div className={css.form}>
        <Field label="Реализаций" hint="Сколько раз выбрать пробы с возвратом (10–5000)">
          <NumberInput label="Реализаций" value={form.nBoot} onChange={(nBoot) => setForm("bootstrap", { nBoot })} width={64} />
        </Field>
        <Field label="Доверие">
          <select aria-label="Доверие" value={form.confidence} onChange={(e) => setForm("bootstrap", { confidence: e.target.value })}>
            {CONFIDENCES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Зерно" hint="Пусто — новое при каждом расчёте; то же зерно даёт те же реализации">
          <NumberInput label="Зерно" value={form.seed} onChange={(seed) => setForm("bootstrap", { seed })} placeholder="случайное" width={96} />
        </Field>
        <label className={css.check} title="Пробы выбираются с вероятностью, пропорциональной весу декластеризации">
          <input type="checkbox" checked={form.weighted} onChange={(e) => setForm("bootstrap", { weighted: e.target.checked })} />С весами декластеризации
        </label>
        {form.weighted && <DeclusFields form={form.declus} onChange={(p) => setForm("bootstrap", { declus: { ...form.declus, ...p } })} />}
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("bootstrap").catch(() => undefined)} />
      </div>
      {reason && <p className={css.hint}>{reason}</p>}
      {run.error && <ProblemBox problem={run.error} />}
      {r && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <div className={css.column}>
            <table className={css.grid} aria-label="Доверительные интервалы">
              <thead>
                <tr>
                  <th>Статистика</th>
                  <th>По данным</th>
                  {r.weighted && <th>С весами</th>}
                  <th>Среднее реализаций</th>
                  <th>σ реализаций</th>
                  <th>Интервал {percentText(r.confidence)}</th>
                </tr>
              </thead>
              <tbody>
                {bootRows(r).map((row) => (
                  <tr key={row.id} aria-selected={row.id === form.statistic} className={row.id === form.statistic ? css.chosen : undefined} onClick={() => setForm("bootstrap", { statistic: row.id })}>
                    <th scope="row">{row.title}</th>
                    <td>{row.observed}</td>
                    {r.weighted && <td>{row.weighted}</td>}
                    <td>{row.mean}</td>
                    <td>{row.std}</td>
                    <td>
                      {row.low} … {row.high}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className={css.note}>
              Интервалов {r.count.toLocaleString("ru-RU")}, реализаций {r.n_boot.toLocaleString("ru-RU")}, зерно {r.seed}.{" "}
              <button type="button" className={css.link} onClick={() => setForm("bootstrap", { seed: String(r.seed) })}>
                Повторить с этим зерном
              </button>
            </p>
            <p className={css.note}>{countsText(r.counts)}</p>
          </div>
          <div className={css.column}>
            <Field label="Гистограмма реализаций">
              <select aria-label="Статистика гистограммы" value={form.statistic} onChange={(e) => setForm("bootstrap", { statistic: e.target.value as BootStatistic })}>
                {STATISTICS.map(([id, l]) => (
                  <option key={id} value={id}>
                    {l}
                  </option>
                ))}
              </select>
            </Field>
            {entry && (
              <BarsPlot
                label={`Бутстрап: ${title} ${r.field}`}
                edges={entry.edges}
                counts={entry.counts}
                xTitle={`${title} ${r.field}`}
                marks={[
                  { value: r.weighted ? entry.observed_weighted : entry.observed, label: r.weighted ? "по данным с весами" : "по данным" },
                  { value: entry.ci_low, label: "нижняя", dashed: true },
                  { value: entry.ci_high, label: "верхняя", dashed: true },
                ]}
                fileName={`Бутстрап ${r.table} ${r.field}`}
                csv={() => bootstrapCsv(r)}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
