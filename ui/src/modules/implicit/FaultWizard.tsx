/**
 * The window «Разлом» (Э19б; ТЗ 7.35 Д-24; ADR 0026 решения 19–21): the name, the data of the fault — the codes of the table of the holes OR the layers of strings
 * (the role «контакт» is the trace of the fault; «внутри» / «снаружи» fix the wings) —, the radius of influence R_F (empty — an infinite fault) and the
 * domains the fault cuts. The offset of the wings is the result of the build and is shown read only. «Сохранить» runs `implicit.fault`.
 */
import { useMemo, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { parseNumber } from "../composites/params";
import { errorText } from "../../shell/props";
import { sceneObjects, lineLayers } from "../scene/objects";
import { loadCodes, type CodeList } from "../intercepts/api";
import { useLatest, useSel } from "../estimation/hooks";
import { DataCheck, FieldIssue, GroupFrame, TextRow } from "./GroupParts";
import { groupRanges } from "./groupBase";
import { faults, ROLE_TITLES, type FaultForm } from "./FaultState";
import { formOfMeta, type ManualRole } from "./form";
import { readOffsets, type GroupOffset } from "./stackApi";
import { metaOf, radiusText } from "./stackModel";
import { implicitStore } from "./state";
import mod from "./Stack.module.css";
import wiz from "./Wizard.module.css";
import { g as fmt } from "./WizardParts";
import { wizardStore } from "./wizardState";

const ROLES: readonly ManualRole[] = ["contact", "inside", "outside"];

/** The offset of the wings of a fault, read only: the mean and the greatest by the groups the fault cuts (`stats.by_group[].offset_by_fault`). */
export function OffsetLines({ offsets, fault, names }: { offsets: readonly GroupOffset[]; fault: number; names: ReadonlyMap<number, string> }) {
  const seen = new Set<number>();
  const lines = offsets.flatMap((o) => {
    const x = o.byFault[String(fault)];
    if (!x || seen.has(o.group)) return [];
    seen.add(o.group);
    return [`«${names.get(o.group) ?? `№${o.group}`}»: среднее ${x.mean === null ? "—" : fmt(x.mean)} м, наибольшее ${x.max === null ? "—" : fmt(x.max)} м`];
  });
  if (!lines.length) return null;
  return (
    <p className={mod.offset} data-testid="flt-offset">
      Смещение (результат построения, только чтение) — {lines.join("; ")}.
    </p>
  );
}

export function FaultWizard({ winId }: { winId: string }) {
  const s = useSel(faults.store, (x) => x);
  const f: FaultForm = s.form;
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const defaults = useSel(wizardStore, (x) => x.defaults.data);
  // the files are the snapshot: a selector that builds a new array at every call would never settle (React #185)
  const files = useSel(sceneObjects, (x) => x.files);
  const layersAll = useMemo(() => lineLayers({ files }).filter((l) => !l.cloud), [files]);
  const ranges = groupRanges(defaults);
  const model = info ? formOfMeta(info.name, info.meta) : null;
  const meta = metaOf(info);
  const hasData = !!model && model.setId !== null && !!model.table && !!model.field;
  const [error, setError] = useState<string | null>(null);
  const codes = useLatest<CodeList>(hasData ? () => loadCodes(model!.setId!, model!.table!, model!.field!) : null, [model?.setId, model?.table, model?.field, hasData], (e) => setError(errorText(e)));
  const taken = model ? faults.takenBy(model, s.groupId) : new Set<string>();
  const free = (codes?.codes ?? []).filter((c) => !taken.has(c) && !f.codes.includes(c));
  const domains = model ? model.groups.flatMap((x) => (x.type === "domain" ? [x] : [])) : [];
  const set = (patch: Partial<FaultForm>) => faults.setForm(patch);
  const names = new Map((model?.groups ?? []).map((x) => [x.id, x.name]));
  const offsets = readOffsets(meta);
  const radiusValue = f.radius.trim() === "" ? null : parseNumber(f.radius);

  return (
    <GroupFrame rt={faults} winId={winId} intro="Поверхность разлома строится отдельно; рассекаемые домены получают два крыла — по каждому решается своё поле, на разломе поле имеет разрыв. Новый разлом добавляется последней группой.">
      <fieldset className={css.group} data-testid="grp-name-box">
        <legend>Разлом</legend>
        <TextRow rt={faults} label="Название" value={f.name} onChange={(v) => set({ name: v })} id="name" at="name" placeholder="Разлом N" />
        <TextRow rt={faults} label="Радиус R_F, м" value={f.radius} onChange={(v) => set({ radius: v })} id="radius" at="params.radius" placeholder="пусто — бесконечный" range={ranges.fault_radius} note="Конечный разлом: за радиусом влияния смещение затухает до нуля. Пусто — бесконечный разлом." />
        {(f.radius.trim() === "" || radiusValue !== null) && (
          <p className={css.note} data-testid="flt-radius-words">
            {radiusText(radiusValue)}
          </p>
        )}
      </fieldset>

      <fieldset className={css.group} data-testid="grp-codes-box">
        <legend>Коды разлома в таблице скважин</legend>
        {error && (
          <p className={css.error} role="alert" data-testid="grp-data-error">
            {error}
          </p>
        )}
        {model && !hasData && (
          <p className={css.warn} role="status" data-testid="grp-no-data">
            В модели не задана таблица кодов: данные разлома можно задать слоями линий ниже.
          </p>
        )}
        <div className={wiz.chips} data-testid="grp-codes">
          {f.codes.map((c) => (
            <span key={c} className={wiz.chip} data-testid={`grp-code-${c}`}>
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
        <span className={css.note}>Контакт разлома — смена кода вдоль ствола. Код входит в одну группу модели: коды, занятые другими группами, не предлагаются.</span>
        <FieldIssue rt={faults} at="codes" id="codes" />
        {f.codes.length > 0 && <DataCheck rt={faults} />}
      </fieldset>

      <fieldset className={css.group} data-testid="flt-layers-box">
        <legend>Слои линий (ручные данные)</legend>
        <div className={mod.layers} data-testid="flt-layers">
          {f.layers.map((l, i) => (
            <div key={i} className={mod.layer} data-testid={`flt-layer-${i}`}>
              <select className={mod.select} value={l.layerId ?? ""} aria-label={`Слой линий ${i + 1}`} data-testid={`flt-layer-pick-${i}`} onChange={(e) => set({ layers: f.layers.map((x, k) => (k === i ? { ...x, layerId: e.target.value ? Number(e.target.value) : null } : x)) })}>
                <option value="">— выберите слой —</option>
                {l.layerId !== null && !layersAll.some((x) => x.id === l.layerId) && <option value={l.layerId}>слой №{l.layerId} (не найден)</option>}
                {layersAll.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
              <select className={mod.select} value={l.role} aria-label={`Роль слоя ${i + 1}`} data-testid={`flt-layer-role-${i}`} onChange={(e) => set({ layers: f.layers.map((x, k) => (k === i ? { ...x, role: e.target.value as ManualRole } : x)) })}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_TITLES[r]}
                  </option>
                ))}
              </select>
              <button type="button" className={css.btn} aria-label={`Убрать слой ${i + 1}`} onClick={() => set({ layers: f.layers.filter((_, k) => k !== i) })}>
                ×
              </button>
            </div>
          ))}
          <div className={css.row}>
            <button type="button" className={css.btn} data-testid="flt-layer-add" disabled={!layersAll.length} title={layersAll.length ? "Добавить слой линий" : "В проекте нет слоёв линий: нарисуйте трассу разлома в редакторе стрингов"} onClick={() => set({ layers: [...f.layers, { layerId: null, role: "contact" }] })}>
              + слой линий
            </button>
          </div>
        </div>
        <span className={css.note}>Роль «контакт» — трасса разлома (поле f_F = 0 на ней); «внутри» и «снаружи» — точки по разные стороны. Данные разлома — коды или слои линий: достаточно одного из двух.</span>
        <FieldIssue rt={faults} at="layers" id="layers" />
      </fieldset>

      <fieldset className={css.group} data-testid="flt-domains-box">
        <legend>Рассекает домены</legend>
        {domains.length ? (
          <div className={mod.picks} data-testid="flt-domains">
            {domains.map((d) => (
              <label key={d.id}>
                <input type="checkbox" checked={f.domains.includes(d.id)} data-testid={`flt-domain-${d.id}`} onChange={(e) => set({ domains: e.target.checked ? [...f.domains, d.id] : f.domains.filter((x) => x !== d.id) })} />
                {d.name}
              </label>
            ))}
          </div>
        ) : (
          <p className={css.note}>В модели нет доменов литологии: разлом нечего рассекать.</p>
        )}
        <span className={css.note}>У рассечённого домена два крыла, и каждое решается отдельно. Список можно менять и позже в окне «Стек отношений».</span>
        <FieldIssue rt={faults} at="domains" id="domains" />
        {s.groupId !== null && <OffsetLines offsets={offsets} fault={s.groupId} names={names} />}
        {s.groupId !== null && !offsets.some((o) => o.byFault[String(s.groupId)]) && (
          <p className={css.note} data-testid="flt-no-offset">
            Смещение появится после построения модели; оно считается как разность полей крыльев и не задаётся вручную.
          </p>
        )}
      </fieldset>
    </GroupFrame>
  );
}
