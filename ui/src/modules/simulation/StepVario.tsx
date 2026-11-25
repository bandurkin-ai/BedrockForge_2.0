/**
 * Step 3 of the wizard «Моделирование» (ТЗ 8.8.1; Д-23, Д-38): the model of variogram and what the kind asks with it. SGS and co-SGS —
 * one stored model in normal scores (a model in the units of the field is not chosen: `variogram_scale`); co-SGS also the secondary
 * variable (a numeric field of the block model) and ρ (computed from the collocated pairs, or typed). SIS — by thresholds of a continuous
 * variable or by categories (the codes of the numeric field of the samples, with their names), with an own model of an indicator for each
 * (the button «Подобрать по порогам» takes the model `indicator:<t>` of the same threshold) or one median model; the models are in the units
 * of the field (a model in normal scores only with its back transform).
 */
import css from "../../shell/PropsParts.module.css";
import { numberText } from "../composites/params";
import { blocksStore } from "../blocks/state";
import { IssueAt } from "../estimation/Parts";
import { thresholdsOf } from "../estimation/form";
import { useSel } from "../estimation/hooks";
import mod from "../estimation/Wizard.module.css";
import type { ModelRow } from "../variography/api";
import { categoriesOf, indicatorOf, isFieldModel, isScoreModel, type SimForm } from "./form";
import { loadVariogramModels, matchIndicatorModels, setForm, simWizardStore } from "./wizardState";

/** The text of an option of the list of models and whether the kind can take the model (Д-38). */
export function modelOption(row: Pick<ModelRow, "name" | "field" | "transform">, hasBack: boolean, score: boolean): { text: string; usable: boolean } {
  const field = row.field ? ` · ${row.field}` : "";
  const t = indicatorOf(row.transform);
  const usable = score ? isScoreModel(row.transform) : isFieldModel(row.transform, hasBack);
  const note = score
    ? usable
      ? " · в нормальных оценках"
      : " · в единицах поля — для SGS не подходит"
    : t !== null
      ? ` · индикатор ${numberText(t)}`
      : row.transform === "nscore"
        ? hasBack
          ? " · Y с обратным преобразованием"
          : " · в нормальных оценках — недоступна"
        : "";
  return { text: `${row.name}${field}${note}`, usable };
}

function ModelSelect({ value, onChange, score, label, testId, empty }: { value: number | null; onChange: (id: number | null) => void; score: boolean; label: string; testId: string; empty: string }) {
  const vario = useSel(simWizardStore, (s) => s.vario);
  return (
    <select className={css.input} aria-label={label} data-testid={testId} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{vario.loaded ? (vario.rows.length ? empty : "нет сохранённых моделей вариограмм") : "Чтение…"}</option>
      {vario.rows.map((r) => {
        const o = modelOption(r, vario.backs[r.id] === true, score);
        return (
          <option key={r.id} value={r.id} disabled={!o.usable && r.id !== value} data-usable={o.usable}>
            {o.text}
          </option>
        );
      })}
    </select>
  );
}

/** SGS and co-SGS: the one model in normal scores. */
function ScoreModel({ f }: { f: SimForm }) {
  const vario = useSel(simWizardStore, (s) => s.vario);
  const none = vario.loaded && !vario.rows.some((r) => isScoreModel(r.transform));
  return (
    <fieldset className={css.group} data-testid="sim-vario">
      <legend>Модель вариограммы в нормальных оценках</legend>
      <label className={css.formRow}>
        <span>Модель</span>
        <ModelSelect value={f.variogramId} onChange={(id) => setForm({ variogramId: id })} score label="Модель вариограммы" testId="sim-variogram" empty="— выберите модель —" />
      </label>
      <IssueAt at="variogram" id="variogram" />
      {none && (
        <p className={css.note} data-testid="sim-vario-none">
          Нет моделей в нормальных оценках. Постройте вариограмму нормальных оценок в окне «Вариография» и сохраните модель.
        </p>
      )}
      <span className={css.note}>Моделирование идёт в нормальных оценках: проба → нормальная оценка → реализация → обратное преобразование (хвосты — на шаге «Реализации»).</span>
    </fieldset>
  );
}

function Secondary({ f }: { f: SimForm }) {
  const list = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? list.find((m) => m.id === f.modelId));
  const fields = (model?.fields ?? []).filter((x) => x.kind === "number");
  return (
    <fieldset className={css.group} data-testid="sim-secondary">
      <legend>Вторичная переменная</legend>
      <label className={css.formRow}>
        <span>Поле модели</span>
        <select className={css.input} value={f.secField} aria-label="Вторичная переменная" data-testid="sim-sec-field" onChange={(e) => setForm({ secField: e.target.value })}>
          <option value="">{model ? (fields.length ? "— выберите поле —" : "в модели нет числовых полей") : "Выберите блочную модель на шаге 1"}</option>
          {fields.map((x) => (
            <option key={x.name} value={x.name}>
              {x.name}
              {x.unit ? `, ${x.unit}` : ""}
            </option>
          ))}
        </select>
      </label>
      <IssueAt at="secondary.field" id="sec-field" />
      <div className={mod.radios} role="radiogroup" aria-label="Коэффициент корреляции ρ">
        <label>
          <input type="radio" name="sim-rho" checked={f.rhoMode === "samples"} data-testid="sim-rho-samples" onChange={() => setForm({ rhoMode: "samples" })} />
          ρ по пробам: корреляция нормальных оценок в колокированных точках (не меньше 10 пар)
        </label>
        <label>
          <input type="radio" name="sim-rho" checked={f.rhoMode === "value"} data-testid="sim-rho-value" onChange={() => setForm({ rhoMode: "value" })} />
          задать ρ
        </label>
      </div>
      {f.rhoMode === "value" && (
        <label className={css.formRow}>
          <span>ρ от −1 до 1</span>
          <input className={css.input} value={f.rho} inputMode="decimal" aria-label="Коэффициент корреляции ρ" data-testid="sim-rho" onChange={(e) => setForm({ rho: e.target.value })} />
        </label>
      )}
      <IssueAt at="secondary.rho" id="rho" />
      <span className={css.note}>Марковская модель 1: C₁₂(h) = ρ·C₁₁(h). Узел без значения вторичной переменной моделируется как SGS; ρ = 0 даёт SGS.</span>
    </fieldset>
  );
}

/** SIS: thresholds or categories, the models of the indicators. */
function Indicators({ f }: { f: SimForm }) {
  const vario = useSel(simWizardStore, (s) => s.vario);
  const byT = f.sisBy === "thresholds";
  const list = byT ? thresholdsOf(f.thresholds) : categoriesOf(f.categories);
  const items = Array.isArray(list) ? list : [];
  const names = byT ? items.map((t) => `порога ${numberText(t)}`) : items.map((c) => `категории ${c}`);
  const none = vario.loaded && !vario.rows.some((r) => isFieldModel(r.transform, vario.backs[r.id] === true));
  return (
    <>
      <fieldset className={css.group} data-testid="sim-sis">
        <legend>Индикаторы</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Индикаторы SIS">
          <label>
            <input type="radio" name="sim-sis-by" checked={byT} data-testid="sim-by-thresholds" onChange={() => setForm({ sisBy: "thresholds", variogramIds: [] })} />
            пороги непрерывной величины (содержание)
          </label>
          <label>
            <input type="radio" name="sim-sis-by" checked={!byT} data-testid="sim-by-categories" onChange={() => setForm({ sisBy: "categories", variogramIds: [], topcut: { ...f.topcut, mode: "none" } })} />
            категории — коды литологии (числовое поле проб)
          </label>
        </div>
        {byT ? (
          <>
            <label className={css.formRow}>
              <span>Пороги (через «;»)</span>
              <input className={css.input} value={f.thresholds} placeholder="0,7; 1; 1,5" spellCheck={false} aria-label="Пороги индикаторов" data-testid="sim-thresholds" onChange={(e) => setForm({ thresholds: e.target.value })} />
            </label>
            <IssueAt at="method.ik.thresholds" id="thresholds" />
          </>
        ) : (
          <>
            <label className={css.formRow}>
              <span>Коды (через «;»)</span>
              <input className={css.input} value={f.categories} placeholder="1; 2; 3" spellCheck={false} aria-label="Коды категорий" data-testid="sim-categories" onChange={(e) => setForm({ categories: e.target.value })} />
            </label>
            <IssueAt at="method.ik.categories" id="categories" />
            <label className={css.formRow}>
              <span>Названия (через «;»)</span>
              <input className={css.input} value={f.labels} placeholder="Окисленные; Переходные; Первичные" spellCheck={false} aria-label="Названия категорий" data-testid="sim-labels" onChange={(e) => setForm({ labels: e.target.value })} />
            </label>
            <IssueAt at="method.ik.labels" id="labels" />
            <span className={css.note}>Поле кодов — числовое поле проб шага «Пробы» (целые 0–255); пробы с другим кодом не участвуют. От 2 до 20 категорий; названия показывают окна и поле MODE.</span>
          </>
        )}
        <label className={css.formRow}>
          <span>Модели</span>
          <select className={css.input} aria-label="Модели индикаторов" data-testid="sim-ik-models" value={f.ikModels} onChange={(e) => setForm({ ikModels: e.target.value === "median" ? "median" : "per", variogramId: null, variogramIds: [] })}>
            <option value="per">{byT ? "своя модель на каждый порог" : "своя модель на каждую категорию"}</option>
            <option value="median">одна модель (медианный индикатор)</option>
          </select>
        </label>
        <IssueAt at="method.ik" id="ik" />
      </fieldset>

      <fieldset className={css.group} data-testid="sim-vario">
        <legend>{f.ikModels === "per" ? "Модели индикаторов" : "Модель индикатора"}</legend>
        {f.ikModels === "per" ? (
          <>
            {items.length ? (
              items.map((_, i) => (
                <label key={i} className={css.formRow}>
                  <span>Модель {names[i]}</span>
                  <ModelSelect value={f.variogramIds[i] ?? null} onChange={(id) => setForm({ variogramIds: items.map((__, k) => (k === i ? id : (f.variogramIds[k] ?? null))) })} score={false} label={`Модель ${names[i]}`} testId={`sim-variogram-${i}`} empty="— выберите модель —" />
                </label>
              ))
            ) : (
              <p className={css.note}>Задайте {byT ? "пороги" : "категории"}: для каждого выбирается своя модель.</p>
            )}
            {byT && items.length > 0 && (
              <div className={mod.links}>
                <button type="button" className={css.btn} data-testid="sim-match" title="Для каждого порога взять модель индикатора с тем же порогом (преобразование indicator:t)" onClick={() => matchIndicatorModels(items)}>
                  Подобрать по порогам
                </button>
              </div>
            )}
            <IssueAt at="variograms" id="variograms" />
          </>
        ) : (
          <>
            <label className={css.formRow}>
              <span>Модель</span>
              <ModelSelect value={f.variogramId} onChange={(id) => setForm({ variogramId: id })} score={false} label="Модель индикатора" testId="sim-variogram" empty="— выберите модель —" />
            </label>
            <IssueAt at="variogram" id="variogram" />
          </>
        )}
        {none && (
          <p className={css.note} data-testid="sim-vario-none">
            Нет моделей в единицах поля. Постройте индикаторную вариограмму в окне «Вариография» (преобразование «Индикатор») и сохраните модель.
          </p>
        )}
        <span className={css.note}>Модель индикатора — в единицах поля; модель в нормальных оценках берётся только с обратным преобразованием.</span>
      </fieldset>
    </>
  );
}

export function StepVario() {
  const f = useSel(simWizardStore, (s) => s.form);
  const vario = useSel(simWizardStore, (s) => s.vario);
  return (
    <>
      {f.kind === "sis" ? <Indicators f={f} /> : <ScoreModel f={f} />}
      {f.kind === "cosgs" && <Secondary f={f} />}
      {vario.error && (
        <p className={css.error} role="alert">
          {vario.error}{" "}
          <button type="button" className={css.btn} onClick={() => void loadVariogramModels()}>
            Повторить
          </button>
        </p>
      )}
    </>
  );
}
