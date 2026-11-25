/**
 * Step 3 of the wizard «Оценка содержаний» (ТЗ 8.8.4; Д-38): the method (NN, IDW, SK, OK, IK) with its parameters — the power of
 * IDW, the mean of SK, the thresholds and the models of IK — and the stored model of variogram. A model built in normal scores (Y)
 * without its back transform is not chosen (`variogram_scale`: «Выполните обратное преобразование модели …»); a model with the back
 * transform goes as the model of Z. IK «модель на порог» takes one model for each threshold. NN and IDW use the model only to take
 * the radii and the angles of the search from it.
 */
import css from "../../shell/PropsParts.module.css";
import { loadVariogramModels, scalesOf, setForm, wizardStore } from "./wizardState";
import type { ModelRow } from "../variography/api";
import { METHODS, METHOD_TITLES, VARIOGRAM_SCALE_HINT, VARIOGRAM_SCALE_MESSAGE, type MethodKind, type VarioScale } from "./api";
import { thresholdsOf } from "./form";
import { IssueAt } from "./Parts";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";

/** The text of an option of the list of models: the field it was built for, the scale. */
export function modelOptionText(row: Pick<ModelRow, "name" | "field">, scale: VarioScale): string {
  const field = row.field ? ` · ${row.field}` : "";
  return `${row.name}${field}${scale === "back" ? " · Y с обратным преобразованием" : scale === "y" ? " · в нормальных оценках — недоступна" : ""}`;
}

const NEEDS: Record<MethodKind, string> = {
  nn: "Модель вариограммы не нужна: ближайшая проба. Можно взять из модели радиусы и углы поиска.",
  idw: "Модель вариограммы не нужна: веса — обратные расстояния. Можно взять из модели радиусы и углы поиска.",
  sk: "Простой кригинг: среднее известно и не оценивается; нужна модель вариограммы.",
  ok: "Обычный кригинг: среднее оценивается по окрестности; нужна модель вариограммы.",
  ik: "Индикаторный кригинг: на каждый порог — вероятность не превысить его; результат — вероятности P и E-type.",
};

export function StepMethod() {
  const f = useSel(wizardStore, (s) => s.form);
  const vario = useSel(wizardStore, (s) => s.vario);
  const defaults = useSel(wizardStore, (s) => s.defaults);
  const scales = scalesOf(vario);
  const thresholds = f.method === "ik" ? thresholdsOf(f.thresholds) : [];
  const perThreshold = f.method === "ik" && f.ikModels === "per";
  const anyY = vario.rows.some((r) => scales[r.id] === "y");
  const warnings = defaults.data?.variogram?.warnings ?? [];

  const modelSelect = (value: number | null, onChange: (id: number | null) => void, label: string, testId: string, empty: string) => (
    <select className={css.input} aria-label={label} data-testid={testId} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{vario.loaded ? (vario.rows.length ? empty : "нет сохранённых моделей вариограмм") : "Чтение…"}</option>
      {vario.rows.map((r) => {
        const scale = scales[r.id] ?? "z";
        return (
          <option key={r.id} value={r.id} disabled={scale === "y" && r.id !== value} data-scale={scale}>
            {modelOptionText(r, scale)}
          </option>
        );
      })}
    </select>
  );

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-3">
        <legend>Метод</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Метод оценки">
          {METHODS.map((m) => (
            <label key={m}>
              <input type="radio" name="est-method" checked={f.method === m} data-testid={`est-method-${m}`} onChange={() => setForm({ method: m })} />
              {METHOD_TITLES[m]}
            </label>
          ))}
        </div>
        <span className={css.note} data-testid="est-method-note">
          {NEEDS[f.method]}
        </span>
        <IssueAt at="method.kind" id="method" />
        {f.method === "idw" && (
          <>
            <label className={css.formRow}>
              <span>Степень p (0,5–5)</span>
              <input className={css.input} value={f.power} inputMode="decimal" aria-label="Степень IDW" data-testid="est-power" onChange={(e) => setForm({ power: e.target.value })} />
            </label>
            <IssueAt at="method.power" id="power" />
          </>
        )}
        {f.method === "sk" && (
          <>
            <label className={css.formRow}>
              <span>Среднее</span>
              <select className={css.input} aria-label="Среднее SK" data-testid="est-mean-mode" value={f.meanMode} onChange={(e) => setForm({ meanMode: e.target.value === "value" ? "value" : "declus" })}>
                <option value="declus">декластерированное среднее домена</option>
                <option value="value">своё значение</option>
              </select>
            </label>
            {f.meanMode === "value" && (
              <label className={css.formRow}>
                <span>Значение среднего</span>
                <input className={css.input} value={f.meanValue} inputMode="decimal" aria-label="Среднее простого кригинга" data-testid="est-mean-value" onChange={(e) => setForm({ meanValue: e.target.value })} />
              </label>
            )}
            <IssueAt at="method.mean" id="mean" />
          </>
        )}
        {f.method === "ik" && (
          <>
            <label className={css.formRow}>
              <span>Пороги (через «;»)</span>
              <input className={css.input} value={f.thresholds} placeholder="0,5; 1; 2,5" spellCheck={false} aria-label="Пороги индикаторов" data-testid="est-thresholds" onChange={(e) => setForm({ thresholds: e.target.value })} />
            </label>
            <IssueAt at="method.ik.thresholds" id="thresholds" />
            <label className={css.formRow}>
              <span>Модели</span>
              <select className={css.input} aria-label="Модели индикаторов" data-testid="est-ik-models" value={f.ikModels} onChange={(e) => setForm({ ikModels: e.target.value === "median" ? "median" : "per" })}>
                <option value="per">своя модель на каждый порог</option>
                <option value="median">одна модель (медианный индикатор)</option>
              </select>
            </label>
            <IssueAt at="method.ik" id="ik" />
          </>
        )}
      </fieldset>

      <fieldset className={css.group}>
        <legend>{perThreshold ? "Модели вариограмм по порогам" : "Модель вариограммы"}</legend>
        {perThreshold ? (
          Array.isArray(thresholds) && thresholds.length ? (
            thresholds.map((t, i) => (
              <label key={i} className={css.formRow}>
                <span>Порог {t.toLocaleString("ru-RU")}</span>
                {modelSelect(f.variogramIds[i] ?? null, (id) => setForm({ variogramIds: thresholds.map((_, k) => (k === i ? id : (f.variogramIds[k] ?? null))) }), `Модель порога ${t}`, `est-variogram-${i}`, "— выберите модель —")}
              </label>
            ))
          ) : (
            <p className={css.note}>Задайте пороги: для каждого выбирается своя модель.</p>
          )
        ) : (
          <label className={css.formRow}>
            <span>{f.method === "nn" || f.method === "idw" ? "Для поиска" : "Модель"}</span>
            {modelSelect(f.variogramId, (id) => setForm({ variogramId: id }), "Модель вариограммы", "est-variogram", f.method === "nn" || f.method === "idw" ? "— не использовать —" : "— выберите модель —")}
          </label>
        )}
        <IssueAt at={["variogram", "variograms"]} id="variogram" />
        {anyY && (
          <p className={css.note} data-testid="est-variogram-scale-note">
            {VARIOGRAM_SCALE_MESSAGE}: такая модель не выбирается. {VARIOGRAM_SCALE_HINT} (окно «Вариография», «Нормальные оценки»).
          </p>
        )}
        <p className={css.note}>
          <button type="button" className={css.btn} data-testid="est-vario-refresh" title="Прочитать модели вариограмм проекта заново (после сохранения модели в окне «Вариография»)" onClick={() => void loadVariogramModels()}>
            Обновить список
          </button>
        </p>
        {vario.error && (
          <p className={css.error} role="alert">
            {vario.error}{" "}
            <button type="button" className={css.btn} onClick={() => void loadVariogramModels()}>
              Повторить
            </button>
          </p>
        )}
        {warnings.length > 0 && (
          <ul className={mod.warn} role="status" data-testid="est-variogram-warnings">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </fieldset>
    </>
  );
}
