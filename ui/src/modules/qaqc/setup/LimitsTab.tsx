/**
 * «Настройка QAQC → Пороги» (ADR 0020 §3; ТЗ 8.4.2): every threshold with its range of the ТЗ (σ 1–5, серия 5–20,
 * k_б 1–50, k_д 0–50, группа 5–51 нечётная, HARD 1–100 %, доля 50–100 %), a value out of range marked at its field
 * and never sent, «По умолчанию» — the values of the ТЗ. «Сохранить» runs `qaqc.config` with the thresholds.
 */
import css from "../../../shell/PropsParts.module.css";
import { checkLimits, DEFAULT_LIMITS, LIMIT_SPECS, limitsText, rangeOf, type LimitTexts } from "./forms";
import { FormFooter, FormNotes } from "./FormParts";
import own from "./Setup.module.css";
import { useBaseForm } from "./useBaseForm";

export function LimitsTab({ onDirty }: { onDirty: (on: boolean) => void }) {
  const f = useBaseForm<LimitTexts>("config", (s) => (s.config ? limitsText(s.config.limits) : null), onDirty);
  if (!f.form) return <FormNotes error={f.st.error} loading />;
  const form = f.form;
  const { values, errors } = checkLimits(form);
  const serverError = f.st.configError;
  const errorOf = (key: string) => errors[key as keyof LimitTexts] ?? (serverError?.param === key ? serverError.message : null);
  const groups = [...new Set(LIMIT_SPECS.map((s) => s.group))];
  const save = () => values && f.save("qaqc.config", Object.fromEntries(LIMIT_SPECS.map((s) => [s.key, values[s.key]])));
  return (
    <div className={css.win} data-testid="qaqc-setup-limits">
      <div className={css.winBody}>
        <FormNotes conflict={f.conflict} outside={f.outside} error={serverError && !serverError.param ? serverError.message : null} />
        {groups.map((g) => (
          <fieldset key={g} className={css.group}>
            <legend>{g}</legend>
            <div className={own.limits}>
              {LIMIT_SPECS.filter((s) => s.group === g).map((s) => {
                const err = errorOf(s.key);
                return (
                  <div key={s.key} style={{ display: "contents" }}>
                    <label htmlFor={`qaqc-limit-${s.key}`}>{s.title}</label>
                    <input id={`qaqc-limit-${s.key}`} className={css.input} inputMode="decimal" value={form[s.key]} aria-invalid={!!err} data-testid={`qaqc-limit-${s.key}`} onChange={(e) => f.setForm({ ...form, [s.key]: e.target.value })} />
                    <span className={own.range}>{rangeOf(s)}</span>
                    {err && <span className={own.err}>{err}</span>}
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
        <p className={css.note}>Классы смещения 5 и 10 %, окно скользящего среднего 10 проб и минимум 50 пар для Томпсона–Ховарта — постоянные ТЗ.</p>
      </div>
      <FormFooter dirty={f.dirty} canSave={!!values} reason={f.app.registry.reason("qaqc.config")} onUndo={f.undo} onSave={save}>
        <button type="button" className={css.btn} onClick={() => f.setForm(limitsText(DEFAULT_LIMITS))} data-testid="qaqc-limits-default">
          По умолчанию
        </button>
      </FormFooter>
    </div>
  );
}
