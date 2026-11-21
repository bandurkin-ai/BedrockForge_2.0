/**
 * «Нормальные оценки» of the tab (ТЗ 7.29; ADR 0019 §7; GSLIB nscore / backtr, перенос BF): the table of the
 * normal score transform (with the weights of declustering, if asked; the trimming limits of the tails), its
 * curve and rows, the scores of the points; the back-transform of the given scores by the same table with the
 * chosen tails («—» where a tail has no number).
 */
import { saveFile } from "../../bridge";
import { backtrBody, backtrCsv, countsText, fmt, nscoreBody, nscoreCsv, nscoreFacts, type BacktrForm, type TabContext } from "./forms";
import { DeclusFields, Facts, Field, NumberInput, ProblemBox, RunButton, useWhole } from "./Parts";
import { LinePlot } from "./Plots";
import { runAnalysis, setForm, statsStore } from "./state";
import css from "./Stats.module.css";

/** Rows of the transform table shown; all of them go to the CSV. */
export const TABLE_SHOWN = 300;

export function NscorePanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.nscore;
  const back = s.forms.backtr;
  const run = s.runs.nscore;
  const bt = s.runs.backtr;
  const r = run.result;
  const reason = nscoreBody(ctx, form).reason ?? null;
  const backReason = backtrBody(ctx, form, back).reason ?? null;
  const setBack = (p: Partial<BacktrForm>) => setForm("backtr", p);
  return (
    <div className={css.panel} data-testid="stats-nscore">
      <div className={css.form}>
        <label className={css.check}>
          <input type="checkbox" checked={form.weighted} onChange={(e) => setForm("nscore", { weighted: e.target.checked })} />С весами декластеризации
        </label>
        {form.weighted && <DeclusFields form={form.declus} onChange={(p) => setForm("nscore", { declus: { ...form.declus, ...p } })} />}
        <Field label="Хвост снизу до" hint="Нижняя граница для обратного преобразования; пусто — наименьшее значение">
          <NumberInput label="Хвост снизу до" value={form.zmin} onChange={(zmin) => setForm("nscore", { zmin })} placeholder="мин." />
        </Field>
        <Field label="сверху до" hint="Верхняя граница для обратного преобразования; пусто — наибольшее значение">
          <NumberInput label="Хвост сверху до" value={form.zmax} onChange={(zmax) => setForm("nscore", { zmax })} placeholder="макс." />
        </Field>
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("nscore").catch(() => undefined)} />
      </div>
      {reason && <p className={css.hint}>{reason}</p>}
      {run.error && <ProblemBox problem={run.error} />}
      {r && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <div className={css.column}>
            <Facts label="Итоги нормального преобразования" rows={nscoreFacts(r)} />
            <p className={css.note}>{countsText(r.counts)}</p>
          </div>
          <LinePlot label={`Таблица преобразования ${r.field}`} xs={r.values} ys={r.scores} xTitle={r.field} yTitle="Нормальная оценка" fileName={`Нормальные оценки ${r.table} ${r.field}`} csv={() => nscoreCsv(r)} />
          <div className={css.tableBox}>
            <table className={css.grid} aria-label="Таблица преобразования">
              <thead>
                <tr>
                  <th>{r.field}</th>
                  <th>Оценка</th>
                </tr>
              </thead>
              <tbody>
                {r.values.slice(0, TABLE_SHOWN).map((v, i) => (
                  <tr key={i}>
                    <td>{fmt(v)}</td>
                    <td>{fmt(r.scores[i], 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {r.values.length > TABLE_SHOWN && <p className={css.note}>Показаны первые {TABLE_SHOWN} из {r.values.length.toLocaleString("ru-RU")} строк; полностью — CSV.</p>}
          </div>
        </div>
      )}
      <fieldset className={css.group}>
        <legend>Обратное преобразование</legend>
        <div className={css.form}>
          <Field label="Нормальные оценки" hint="Числа через «;» или пробел, до 20 000">
            <input type="text" aria-label="Нормальные оценки" value={back.scores} style={{ width: 220 }} onChange={(e) => setBack({ scores: e.target.value })} />
          </Field>
          <Field label="Нижний хвост">
            <select aria-label="Нижний хвост" value={back.ltail} onChange={(e) => setBack({ ltail: e.target.value as BacktrForm["ltail"] })}>
              <option value="linear">линейный</option>
              <option value="power">степенной</option>
              <option value="none">нет (крайнее значение)</option>
            </select>
          </Field>
          {back.ltail === "power" && (
            <Field label="Показатель снизу">
              <NumberInput label="Показатель снизу" value={back.ltpar} onChange={(ltpar) => setBack({ ltpar })} width={52} />
            </Field>
          )}
          <Field label="Верхний хвост">
            <select aria-label="Верхний хвост" value={back.utail} onChange={(e) => setBack({ utail: e.target.value as BacktrForm["utail"] })}>
              <option value="linear">линейный</option>
              <option value="power">степенной</option>
              <option value="hyperbolic">гиперболический</option>
              <option value="none">нет (крайнее значение)</option>
            </select>
          </Field>
          {(back.utail === "power" || back.utail === "hyperbolic") && (
            <Field label="Показатель сверху" hint={back.utail === "hyperbolic" ? "Не меньше 1" : "Больше 0"}>
              <NumberInput label="Показатель сверху" value={back.utpar} onChange={(utpar) => setBack({ utpar })} width={52} />
            </Field>
          )}
          <RunButton busy={bt.busy} reason={backReason} onClick={() => void runAnalysis("backtr").catch(() => undefined)}>
            Преобразовать
          </RunButton>
        </div>
        {backReason && <p className={css.hint}>{backReason}</p>}
        <p className={css.note}>По таблице данных с теми же весами и границами хвостов, что и выше.</p>
        {bt.error && <ProblemBox problem={bt.error} />}
        {bt.result && (
          <div className={`${css.tableBox} ${bt.busy ? css.stale : ""}`}>
            <table className={css.grid} aria-label="Обратное преобразование">
              <thead>
                <tr>
                  <th>Оценка</th>
                  <th>{bt.result.field}</th>
                </tr>
              </thead>
              <tbody>
                {bt.result.scores.slice(0, TABLE_SHOWN).map((y, i) => (
                  <tr key={i}>
                    <td>{fmt(y, 4)}</td>
                    <td>{fmt(bt.result!.values[i])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {bt.result.scores.length > TABLE_SHOWN && <p className={css.note}>Показаны первые {TABLE_SHOWN} из {bt.result.scores.length.toLocaleString("ru-RU")}.</p>}
            <button type="button" className={css.btn} onClick={() => void saveFile(`Обратное преобразование ${bt.result!.field}.csv`, "csv", backtrCsv(bt.result!.field, bt.result!.scores, bt.result!.values)).catch(() => undefined)}>
              CSV
            </button>
          </div>
        )}
      </fieldset>
    </div>
  );
}
