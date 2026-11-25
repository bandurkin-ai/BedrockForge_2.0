/**
 * The window «Жила» (ТЗ 8.9.2, 8.9.4; ADR 0026 решение 17): the codes of the vein in the table of the model's data (the hanging wall is the top
 * of their intervals, the foot wall the bottom), the boundary — the convex hull of the contacts with an offset (a polygon in (u, v) of the plane of
 * the vein is kept as it is stored, not drawn here: the contract has no plane), t_min — where the thickness is not above it the vein pinches out.
 * The step h of the grid (u, v) is the step of the model. «Сохранить» runs `implicit.vein`.
 */
import { useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { loadCodes, type CodeList } from "../intercepts/api";
import { DataCheck, FieldIssue, GroupFrame, TextRow } from "./GroupParts";
import { groupRanges } from "./groupBase";
import { veins } from "./groupKinds";
import { formOfMeta } from "./form";
import { useLatest, useSel } from "./hooks";
import { implicitStore } from "./state";
import mod from "./Wizard.module.css";
import type { VeinBound, VeinForm as Form } from "./VeinState";
import { g } from "./WizardParts";
import { wizardStore } from "./wizardState";

export function VeinForm({ winId }: { winId: string }) {
  const s = useSel(veins.store, (x) => x);
  const f = s.form;
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const defaults = useSel(wizardStore, (x) => x.defaults.data);
  const ranges = groupRanges(defaults);
  const model = info ? formOfMeta(info.name, info.meta) : null;
  const hasData = !!model && model.setId !== null && !!model.table && !!model.field;
  const [error, setError] = useState<string | null>(null);
  const codes = useLatest<CodeList>(hasData ? () => loadCodes(model!.setId!, model!.table!, model!.field!) : null, [model?.setId, model?.table, model?.field, hasData], (e) => setError(errorText(e)));
  const taken = model ? veins.takenBy(model, s.groupId) : new Set<string>();
  const free = (codes?.codes ?? []).filter((c) => !taken.has(c) && !f.codes.includes(c));
  const set = (patch: Partial<Form>) => veins.setForm(patch);
  const step = model?.h.trim() ? model.h : null;

  return (
    <GroupFrame rt={veins} winId={winId} intro="Кровля и подошва — верх и низ интервалов кодов жилы; тело строится по сетке (u, v) в плоскости жилы. Новая группа добавляется последней.">
      <fieldset className={css.group} data-testid="grp-name-box">
        <legend>Группа</legend>
        <TextRow rt={veins} label="Название" value={f.name} onChange={(v) => set({ name: v })} id="name" at="name" placeholder="Жила N" />
      </fieldset>

      <fieldset className={css.group} data-testid="grp-codes-box">
        <legend>Коды жилы</legend>
        {error && (
          <p className={css.error} role="alert" data-testid="grp-data-error">
            {error}
          </p>
        )}
        {model && !hasData && (
          <p className={css.warn} role="status" data-testid="grp-no-data">
            В модели не задана таблица кодов: выберите набор, таблицу и поле кода на шаге «Домены литологии» мастера модели.
          </p>
        )}
        <div className={mod.chips} data-testid="grp-codes">
          {f.codes.map((c) => (
            <span key={c} className={mod.chip} data-testid={`grp-code-${c}`}>
              {c}
              <button type="button" aria-label={`Убрать код ${c}`} onClick={() => set({ codes: f.codes.filter((x) => x !== c) })}>
                ×
              </button>
            </span>
          ))}
          <select className={css.input} style={{ width: "auto", minWidth: 120 }} value="" aria-label="Добавить код" data-testid="grp-code-add" disabled={!free.length} onChange={(e) => e.target.value && set({ codes: [...f.codes, e.target.value] })}>
            <option value="">{free.length ? "+ код" : codes ? "нет свободных кодов" : hasData ? "Чтение…" : "нет таблицы кодов"}</option>
            {free.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <span className={css.note}>Код входит в одну группу модели: коды, занятые другими группами, не предлагаются.</span>
        <FieldIssue rt={veins} at="codes" id="codes" />
      </fieldset>

      <fieldset className={css.group} data-testid="grp-bound-box">
        <legend>Граница и выклинивание</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Граница жилы">
          <label>
            <input type="radio" name="grp-bound" checked={f.bound === "hull"} data-testid="grp-bound-hull" onChange={() => set({ bound: "hull" as VeinBound })} />
            Выпуклая оболочка контактов с отступом
          </label>
          {f.polygon && (
            <label>
              <input type="radio" name="grp-bound" checked={f.bound === "polygon"} data-testid="grp-bound-polygon" onChange={() => set({ bound: "polygon" as VeinBound })} />
              Полигон в плоскости жилы (u, v): вершин {f.polygon.length} (задан ранее)
            </label>
          )}
        </div>
        <FieldIssue rt={veins} at={["params.bound", "params.polygon"]} id="bound" />
        {f.bound === "hull" && <TextRow rt={veins} label="Отступ, м" value={f.offset} onChange={(v) => set({ offset: v })} id="offset" at="params.offset" placeholder="0" range={ranges.offset} note="Граница отодвигается от контактов на этот отступ." />}
        <TextRow rt={veins} label="t_min, м" value={f.tMin} onChange={(v) => set({ tMin: v })} id="tmin" at="params.t_min" placeholder="0" range={ranges.t_min} note="Где мощность не больше t_min, жила выклинивается." />
        <p className={css.note} data-testid="grp-step">
          Шаг сетки (u, v) — шаг модели h: {step ? `${g(Number(step.replace(",", ".")))} м` : "по умолчанию (диагональ охвата / 200)"}. Он задаётся в мастере модели, шаг «Граница и шаг».
        </p>
        <DataCheck rt={veins} />
      </fieldset>
    </GroupFrame>
  );
}
