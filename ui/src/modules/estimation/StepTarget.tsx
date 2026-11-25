/**
 * Step 1 of the wizard «Оценка содержаний» (ТЗ 8.8.4): the block model, the domain of the blocks (a field of the model and its value —
 * the «домен D» of 8.8.2), the restrictor (a filter of the library), the sub-blocks and the discretization of the block. The blocks
 * of the model with a variable step are not offered (the core refuses them, `variable_grid`). The labels of the domain are those
 * of «Свойства» of the model.
 */
import { useMemo } from "react";
import css from "../../shell/PropsParts.module.css";
import { blocksStore } from "../blocks/state";
import { filtersByName, filtersStore } from "../blocks/filters/state";
import { chooseModel, estimableModels } from "./bind";
import { defaultName } from "./form";
import { IssueAt, Triple, nf } from "./Parts";
import { useSel } from "./hooks";
import mod from "./Wizard.module.css";
import { setForm, wizardStore } from "./wizardState";

export function StepTarget() {
  const f = useSel(wizardStore, (s) => s.form);
  const list = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const filters = useSel(filtersStore, (s) => s.rows);
  const models = useMemo(() => estimableModels(), [list]);
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? list.find((m) => m.id === f.modelId));
  const fields = (model?.fields ?? []).filter((x) => x.kind === "category" || x.kind === "number");
  const domain = fields.find((x) => x.name === f.domainField);
  const size = model?.grid.size;

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-1">
        <legend>Блочная модель</legend>
        <label className={css.formRow}>
          <span>Имя строки</span>
          <input className={css.input} value={f.name} placeholder={defaultName(f)} aria-label="Имя строки" data-testid="est-name" onChange={(e) => setForm({ name: e.target.value })} />
        </label>
        <IssueAt at="name" id="name" />
        <label className={css.formRow}>
          <span>Модель</span>
          <select
            className={css.input}
            value={f.modelId ?? ""}
            aria-label="Блочная модель"
            data-testid="est-model"
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null;
              setForm({ modelId: id, domainField: "", label: "" });
              chooseModel(id);
            }}
          >
            <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей с блоками"}</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        {model && (
          <span className={css.note} data-testid="est-model-info">
            Блоков {nf(model.cells)}
            {size ? `, размер ${size.map((v) => nf(v)).join(" × ")} м` : ""}.
          </span>
        )}
        <IssueAt at="model_id" id="model" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Домен блоков и ограничитель</legend>
        <label className={css.formRow}>
          <span>Поле домена</span>
          <select className={css.input} value={f.domainField} aria-label="Поле домена модели" data-testid="est-domain-field" onChange={(e) => setForm({ domainField: e.target.value, label: "" })}>
            <option value="">все блоки модели</option>
            {fields.map((x) => (
              <option key={x.name} value={x.name}>
                {x.name}
                {x.kind === "category" ? "" : " (число)"}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="target.domain_field" id="domain-field" />
        {f.domainField && (
          <label className={css.formRow}>
            <span>Домен D</span>
            {domain?.kind === "category" && domain.labels?.length ? (
              <select className={css.input} value={f.label} aria-label="Домен блоков" data-testid="est-label" onChange={(e) => setForm({ label: e.target.value })}>
                <option value="">— выберите значение —</option>
                {domain.labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            ) : (
              <input className={css.input} value={f.label} aria-label="Домен блоков" data-testid="est-label" spellCheck={false} onChange={(e) => setForm({ label: e.target.value })} />
            )}
          </label>
        )}
        <IssueAt at="target.label" id="label" />
        <label className={css.formRow}>
          <span>Ограничитель</span>
          <select className={css.input} value={f.filterId ?? ""} aria-label="Ограничитель" data-testid="est-filter" onChange={(e) => setForm({ filterId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">без ограничителя</option>
            {filtersByName(filters).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <span className={css.note}>Ограничитель — фильтр из библиотеки («Настройки проекта → Фильтры»): оцениваются блоки, которые он пропускает.</span>
        <IssueAt at="target.filter_id" id="filter" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Блок и дискретизация</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Субблоки">
          <label>
            <input type="radio" name="est-sub" checked={f.sub === "parent"} data-testid="est-sub-parent" onChange={() => setForm({ sub: "parent" })} />
            Оценка в центре родителя, субблоки получают её значение
          </label>
          <label>
            <input type="radio" name="est-sub" checked={f.sub === "own"} data-testid="est-sub-own" onChange={() => setForm({ sub: "own" })} />
            Каждый субблок оценивается сам, со своей дискретизацией
          </label>
        </div>
        <IssueAt at="sub" id="sub" />
        <Triple labels={["Точек по X", "Точек по Y", "Точек по Z"]} values={f.disc} onChange={(disc) => setForm({ disc })} testId="est-disc" />
        <span className={css.note}>Блочная оценка по d_x × d_y × d_z точкам блока (1–10 по оси); 1 × 1 × 1 — точечная оценка в центре.</span>
        <IssueAt at="disc" id="disc" />
      </fieldset>
    </>
  );
}
