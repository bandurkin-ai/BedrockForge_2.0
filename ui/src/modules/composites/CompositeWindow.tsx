/**
 * The window «Композиты» (ТЗ 8.2.3; ADR 0018 §6, §10–11): the method as a tab — «По длине», «По доменам», «По
 * уступам»; the source (set, table, fields, domain) and the parameters of 8.2.2 on the left; on the right the
 * preview on the chosen hole — the samples and the composites side by side on one depth scale, the same colours
 * — and the summary «до/после» (число, длина, среднее, металл Σ l·g по полям) of the hole and of the set.
 * The preview follows every edit (≤ 300 ms, the newest answer only); «Применить» → the job; 409 `table_exists` —
 * «Заменить?» in our own window. Shared parts — ToolWindow.tsx; the parameters — tools/composite.ts.
 */
import { useState } from "react";
import { DepthAxis, SampleColumn } from "../../charts/column/SampleColumn";
import { colorOf, columnsRange, depthScale, valueRange } from "../../charts/column/scale";
import css from "../../shell/PropsParts.module.css";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import type { PreviewAnswer } from "./api";
import { SourcePicker } from "./SourcePicker";
import { HolePicker, JobView, ParamForm, SummaryTable, ToolFooter, useTool } from "./ToolWindow";
import { composite, defaultName, metalChange, METHODS, methodOf } from "./tools/composite";

export const PREVIEW_HEIGHT = 320;
const FORM_PARAMS = composite.params.filter((p) => p.key !== "method");

/** The samples and the composites of one hole side by side: one depth scale, one colour scale per field. */
export function CompositePreview({ answer, field, height = PREVIEW_HEIGHT }: { answer: PreviewAnswer; field: string | null; height?: number }) {
  const range = columnsRange(answer.samples, answer.result);
  if (!range) return <p className={css.note}>У скважины {answer.hole} нет интервалов для предпросмотра.</p>;
  const scale = depthScale(range[0], range[1], height, 2);
  const color = colorOf(field ? valueRange(field, answer.samples, answer.result) : null);
  return (
    <div className={own.columns} data-testid="composites-columns">
      <DepthAxis scale={scale} />
      <SampleColumn title="Пробы" cols={answer.samples} field={field} scale={scale} color={color} testId="composites-samples" />
      <SampleColumn title="Композиты" cols={answer.result} field={field} scale={scale} color={color} flagField="FLAG" testId="composites-result" />
    </div>
  );
}

/** «Металл AU: +0,0 % к пробам» — the check of Т8.2-4 shown under the summary; null — not counted. */
export function metalNote(answer: PreviewAnswer | null, field: string | null): string | null {
  if (!answer || !field) return null;
  const d = metalChange(answer.summary, field);
  if (d === null) return null;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "";
  return `Металл ${field}: ${sign}${Math.abs(d).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} % к пробам скважины`;
}

export function CompositeWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(composite, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  const [shownField, setShownField] = useState<string | null>(null);
  const method = methodOf(t.values);
  const field = shownField && t.src.fields.includes(shownField) ? shownField : (t.src.fields[0] ?? null);
  const note = metalNote(t.answer, field);

  if (t.job) return <JobView title="Композиты" job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-composite">
      <div className={css.winBody}>
        <div className={own.tabs} role="tablist" aria-label="Метод композитирования">
          {METHODS.map((m) => (
            <button key={m.value} type="button" role="tab" className={own.tab} aria-selected={method === m.value} data-testid={`composites-tab-${m.value}`} onClick={() => t.setValues({ ...t.values, method: m.value })}>
              {m.label}
            </button>
          ))}
        </div>
        <div className={own.split}>
          <div>
            <p className={css.note}>{composite.note}</p>
            <SourcePicker needs={composite.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
            <ParamForm params={FORM_PARAMS} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} placeholders={{ name: defaultName(t.values) }} />
          </div>
          <fieldset className={css.group} data-testid="composites-preview">
            <legend>Предпросмотр</legend>
            <HolePicker setId={t.src.setId} value={t.hole?.id ?? null} onChange={(id, name) => t.setHole(id === null ? null : { id, name })} />
            {t.src.fields.length > 1 && (
              <label className={css.formRow}>
                <span>Цвет по полю</span>
                <select className={css.input} aria-label="Цвет по полю" value={field ?? ""} onChange={(e) => setShownField(e.target.value)}>
                  {t.src.fields.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {t.checked.reason && <p className={css.note}>Предпросмотр — когда заданы параметры: {t.checked.reason}</p>}
            {t.previewError && <p className={css.warn}>{t.previewError}</p>}
            {t.answer ? <CompositePreview answer={t.answer} field={field} /> : !t.checked.reason && <p className={css.note}>{t.hole ? "Расчёт предпросмотра…" : "Выберите скважину"}</p>}
            {t.answer && t.answer.notes.length > 0 && (
              <ul className={own.notes}>
                {t.answer.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
            <SummaryTable summary={t.answer?.summary ?? null} title={t.answer ? `Скважина ${t.answer.hole}` : "Скважина"} />
            {note && <p className={css.note}>{note}</p>}
            {t.answer && (t.answer.set_summary ? <SummaryTable summary={t.answer.set_summary} title="Набор" /> : <p className={css.note}>Сводка по набору — после расчёта (больше 200 тыс. проб).</p>)}
          </fieldset>
        </div>
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={`Новая таблица «${(typeof t.values.name === "string" && t.values.name.trim()) || defaultName(t.values)}» в наборе; исходная таблица не меняется.`} />
    </div>
  );
}
