/**
 * «Вариограмма» of the tab (ТЗ 7.29; ADR 0022 §11, решение 21; перенос BF): the experimental variogram in plan —
 * omnidirectional or 0°, 45°, 90°, 135° ± 22,5° — with the number of pairs of every lag; the model of BF (nugget +
 * up to 4 structures: type, contribution, ranges along and across the azimuth) drawn over the points, and its curve
 * without data. A lag without pairs is not drawn and is «—» in the table, never 0. «Открыть в окне «Вариография»» —
 * the 3D variograms without the limit of 20 000 intervals.
 */
import type { TabDirection, TabModelAnswer, TabModelType, TabVariogramAnswer } from "./api";
import { countsText, fmt, type TabContext } from "./forms";
import { directionTitle, MAX_STRUCTURES, MODEL_TYPES, modelBody, modelCsv, NEW_STRUCTURE, variogramBody, variogramCsv, variogramFacts, varianceRef, varioPoints, type StructureForm, type VariogramForm } from "./formsVario";
import { Facts, Field, NumberInput, OpenVarioButton, ProblemBox, RunButton, useWhole } from "./Parts";
import { runAnalysis, runModelCurve, setForm, statsStore } from "./state";
import css from "./Stats.module.css";
import { VariogramPlot, type VarioSeries } from "./VarioPlots";

/** The curve of the model along a direction of the data: omni — the first; else the same azimuth. */
export function curveFor(curve: TabModelAnswer | null, d: Pick<TabDirection, "az" | "omni">): { h: number[]; gamma: number[] } | null {
  if (!curve?.directions.length) return null;
  if (d.omni) return curve.directions[0];
  return curve.directions.find((c) => Math.abs(c.az - d.az) < 1e-9) ?? null;
}

/** The series of the chart: the directions of the data with their model curves, or the model curves alone. */
export function variogramSeries(data: TabVariogramAnswer | null, curve: TabModelAnswer | null): VarioSeries[] {
  if (data) return data.directions.map((d) => ({ label: directionTitle(d), points: varioPoints(d), curve: curveFor(curve, d) }));
  return (curve?.directions ?? []).map((c) => ({ label: curve!.directions.length > 1 ? `модель, азимут ${fmt(c.az, 4)}°` : "модель", points: [], curve: c }));
}

function Structures({ form }: { form: VariogramForm }) {
  const set = (i: number, p: Partial<StructureForm>) => setForm("variogram", { structures: form.structures.map((s, j) => (j === i ? { ...s, ...p } : s)) });
  return (
    <table className={css.structures} aria-label="Структуры модели">
      <thead>
        <tr>
          <th>№</th>
          <th>Тип</th>
          <th title="Вклад структуры в порог">Вклад</th>
          <th title="Дальность по азимуту, м">Дальность, м</th>
          <th title="Дальность поперёк азимута, м; пусто — как по азимуту">Поперёк, м</th>
          <th title="Азимут большой оси, ° по часовой от севера">Азимут, °</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {form.structures.map((s, i) => (
          <tr key={i}>
            <td>{i + 1}</td>
            <td>
              <select aria-label={`Структура ${i + 1}: тип`} value={s.it} onChange={(e) => set(i, { it: e.target.value as TabModelType })}>
                {MODEL_TYPES.map(([t, l]) => (
                  <option key={t} value={t}>
                    {l}
                  </option>
                ))}
              </select>
            </td>
            <td>
              <NumberInput label={`Структура ${i + 1}: вклад`} value={s.cc} onChange={(cc) => set(i, { cc })} width={60} />
            </td>
            <td>
              <NumberInput label={`Структура ${i + 1}: дальность`} value={s.hmaj} onChange={(hmaj) => set(i, { hmaj })} width={64} />
            </td>
            <td>
              <NumberInput label={`Структура ${i + 1}: поперёк`} value={s.hmin} onChange={(hmin) => set(i, { hmin })} placeholder="как вдоль" width={64} />
            </td>
            <td>
              <NumberInput label={`Структура ${i + 1}: азимут`} value={s.azi} onChange={(azi) => set(i, { azi })} width={48} />
            </td>
            <td>
              <button type="button" className={css.link} aria-label={`Удалить структуру ${i + 1}`} onClick={() => setForm("variogram", { structures: form.structures.filter((_, j) => j !== i) })}>
                удалить
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function VariogramPanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.variogram;
  const run = s.runs.variogram;
  const data = run.result?.data ?? null;
  const curve = run.result?.curve ?? null;
  const reason = variogramBody(ctx, form).reason ?? null;
  const curveReason = modelBody(form, null).reason ?? null;
  const set = (p: Partial<VariogramForm>) => setForm("variogram", p);
  const series = variogramSeries(data, curve);
  const name = data ? `${data.table} ${data.field}` : (ctx.field ?? "модель");
  return (
    <div className={css.panel} data-testid="stats-variogram">
      <div className={css.form}>
        <Field label="Лаг, м" hint="Шаг лагов; пусто — круглый шаг 1/20 габарита плана">
          <NumberInput label="Лаг" value={form.lag} onChange={(lag) => set({ lag })} placeholder="авто" />
        </Field>
        <Field label="Лагов" hint="Число лагов (1–200)">
          <NumberInput label="Лагов" value={form.nlag} onChange={(nlag) => set({ nlag })} width={52} />
        </Field>
        <Field label="Допуск лага, м" hint="Не больше лага; пусто — половина лага">
          <NumberInput label="Допуск лага" value={form.lagTol} onChange={(lagTol) => set({ lagTol })} placeholder="лаг / 2" />
        </Field>
        <Field label="Направления">
          <select aria-label="Направления" value={form.directions} onChange={(e) => set({ directions: e.target.value as VariogramForm["directions"] })}>
            <option value="omni">всенаправленная</option>
            <option value="four">0°, 45°, 90°, 135° ± 22,5°</option>
          </select>
        </Field>
        <Field label="Пар не меньше" hint="Точка с меньшим числом пар показана полой (1–1000)">
          <NumberInput label="Пар не меньше" value={form.minPairs} onChange={(minPairs) => set({ minPairs })} width={52} />
        </Field>
        <label className={css.check} title="γ делится на дисперсию σ² значений">
          <input type="checkbox" checked={form.standardize} onChange={(e) => set({ standardize: e.target.checked })} />γ / σ²
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={form.withModel} onChange={(e) => set({ withModel: e.target.checked })} />
          Модель
        </label>
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("variogram").catch(() => undefined)} />
        <OpenVarioButton ctx={ctx} />
      </div>
      {form.withModel && (
        <fieldset className={css.group}>
          <legend>Модель: самородок + до {MAX_STRUCTURES} структур</legend>
          <div className={css.form}>
            <Field label="Самородок C0">
              <NumberInput label="Самородок" value={form.nugget} onChange={(nugget) => set({ nugget })} width={60} />
            </Field>
            <Field label="Кривая до, м" hint="Длина кривой модели без данных; пусто — 1,5 × наибольшая дальность">
              <NumberInput label="Кривая до" value={form.hMax} onChange={(hMax) => set({ hMax })} placeholder="авто" />
            </Field>
            <button type="button" className={css.btn} disabled={form.structures.length >= MAX_STRUCTURES} onClick={() => set({ structures: [...form.structures, NEW_STRUCTURE] })}>
              Добавить структуру
            </button>
            <button type="button" className={css.btn} disabled={run.busy || !!curveReason} title={curveReason ?? "Кривая модели без данных и без расчёта по пробам"} onClick={() => void runModelCurve().catch(() => undefined)}>
              Кривая модели без данных
            </button>
          </div>
          {form.structures.length > 0 ? <Structures form={form} /> : <p className={css.note}>Структур нет: модель — чистый самородок.</p>}
        </fieldset>
      )}
      {reason && <p className={css.hint}>{reason}</p>}
      {run.error && <ProblemBox problem={run.error} vario={ctx} />}
      {(data || curve) && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <div className={css.column}>
            <VariogramPlot
              label={`Вариограмма ${name}`}
              series={series}
              level={data ? varianceRef(data) : curve ? { value: curve.sill, label: `порог ${fmt(curve.sill, 4)}` } : null}
              yTitle={data?.standardize ? "γ / σ²" : "γ(h)"}
              fileName={`Вариограмма ${name}`}
              csv={() => (data ? variogramCsv(data) : curve ? modelCsv(curve) : null)}
            />
          </div>
          <div className={css.column}>
            {data ? (
              <>
                <Facts rows={variogramFacts(data)} label="Итоги вариограммы" />
                <PairsTable r={data} />
                <p className={css.note}>{countsText(data.counts)}</p>
              </>
            ) : (
              <p className={css.note}>Кривая модели без данных: порог {fmt(curve!.sill, 4)}, до {fmt(curve!.h_max)} м.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** γ and the number of pairs of every lag of every direction; a lag without pairs — «—». */
function PairsTable({ r }: { r: TabVariogramAnswer }) {
  return (
    <div className={css.tableBox}>
      <table className={css.grid} aria-label="Число пар в лагах">
        <thead>
          <tr>
            <th>Лаг, м</th>
            {r.directions.map((d) => (
              <th key={d.az} colSpan={2}>
                {d.omni ? "всенаправленная" : `${fmt(d.az, 4)}°`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {r.lags.map((lag, k) => (
            <tr key={k}>
              <th scope="row">{fmt(lag)}</th>
              {r.directions.flatMap((d) => [
                <td key={`g${d.az}`} className={d.few[k] ? css.few : undefined}>
                  {fmt(d.gamma?.[k] ?? null, 4)}
                </td>,
                <td key={`n${d.az}`}>{d.n[k].toLocaleString("ru-RU")}</td>,
              ])}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
