/**
 * The window «Система жил» (Э19б; ТЗ 7.35 Д-39; ADR 0026 решения 15, 22): 2–20 veins of one model, each with a code of the table of intervals, a name of its body, the
 * boundary (the convex hull with an offset; a stored polygon is kept) and t_min. THE ORDER OF THE LIST IS THE AGE: the first vein is the youngest and
 * cuts the older ones. After the build the window shows the table of the pairs (the length of the line of intersection, the volume of the common zone
 * before the cut) and the lines of intersection (layers of strings: the check box shows them in 3D). «Сохранить» runs `implicit.vein_system`.
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { loadCodes, type CodeList } from "../intercepts/api";
import { useLatest, useSel } from "../estimation/hooks";
import { FieldIssue, GroupFrame, TextRow } from "./GroupParts";
import { formOfMeta } from "./form";
import { LineOutputs, VeinPairsTable } from "./StackProps";
import { metaOf } from "./stackModel";
import { readVeinPairs } from "./stackApi";
import { implicitStore } from "./state";
import mod from "./Stack.module.css";
import { moved } from "./stackModel";
import { newVein, veinName, VEINS_MAX, VEINS_MIN, veinSystems, type VeinRow } from "./VeinSystemState";
import { wizardStore } from "./wizardState";
import { g as fmt } from "./WizardParts";

export function VeinSystemForm({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(veinSystems.store, (x) => x);
  const f = s.form;
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const defaults = useSel(wizardStore, (x) => x.defaults.data);
  const model = info ? formOfMeta(info.name, info.meta) : null;
  const meta = metaOf(info);
  const hasData = !!model && model.setId !== null && !!model.table && !!model.field;
  const [error, setError] = useState<string | null>(null);
  const codes = useLatest<CodeList>(hasData ? () => loadCodes(model!.setId!, model!.table!, model!.field!) : null, [model?.setId, model?.table, model?.field, hasData], (e) => setError(errorText(e)));
  const taken = model ? veinSystems.takenBy(model, s.groupId) : new Set<string>();
  const used = new Set(f.veins.map((v) => v.code));
  const free = (codes?.codes ?? []).filter((c) => !taken.has(c) && !used.has(c));
  const limit = defaults?.params.limits.veins ?? VEINS_MAX;
  const set = (veins: VeinRow[]) => veinSystems.setForm({ veins });
  const patch = (i: number, p: Partial<VeinRow>) => set(f.veins.map((v, k) => (k === i ? { ...v, ...p } : v)));
  const move = (i: number, d: number) => {
    const order = moved(
      f.veins.map((_, k) => k),
      i,
      d,
    );
    set(order.map((k) => f.veins[k]));
  };
  const pairs = s.groupId === null ? [] : readVeinPairs(meta).filter((p) => p.group === s.groupId);
  const names = new Map(f.veins.map((v) => [v.code, veinName(v)]));
  const lines = (info?.outputs ?? []).filter((o) => o.kind === "line" && o.group === s.groupId);

  return (
    <GroupFrame rt={veinSystems} winId={winId} intro="Несколько жил одной модели. Порядок в списке — возраст: первая жила самая молодая и режет более древнюю (тело древней минус тело молодой). Новая группа добавляется последней.">
      <fieldset className={css.group} data-testid="grp-name-box">
        <legend>Группа</legend>
        <TextRow rt={veinSystems} label="Название" value={f.name} onChange={(v) => veinSystems.setForm({ name: v })} id="name" at="name" placeholder="Жилы N" />
      </fieldset>

      <fieldset className={css.group} data-testid="vsys-veins-box">
        <legend>
          Жилы по возрасту ({f.veins.length} из {VEINS_MIN}–{limit})
        </legend>
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
        <div className={mod.legend} aria-hidden>
          <span>▲ моложе (режет)</span>
          <span>старше ▼</span>
        </div>
        {f.veins.map((v, i) => (
          <div key={v.code || `new${i}`} className={mod.vein} data-testid={`vsys-vein-${i}`}>
            <div className={mod.head}>
              <span className={mod.no} aria-hidden>
                {i + 1}
              </span>
              <select className={mod.select} value={v.code} aria-label={`Код жилы ${i + 1}`} data-testid={`vsys-code-${i}`} onChange={(e) => patch(i, { code: e.target.value })}>
                {!v.code && <option value="">— выберите код —</option>}
                {v.code && <option value={v.code}>{v.code}</option>}
                {free.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <input className={css.input} value={v.name} placeholder={v.code ? `Жила ${v.code}` : "название тела"} aria-label={`Название жилы ${i + 1}`} data-testid={`vsys-name-${i}`} onChange={(e) => patch(i, { name: e.target.value })} />
              <span className={mod.moves}>
                <button type="button" aria-label={`Выше (моложе): жила ${i + 1}`} title="Выше — моложе" disabled={i === 0} data-testid={`vsys-up-${i}`} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button type="button" aria-label={`Ниже (старше): жила ${i + 1}`} title="Ниже — старше" disabled={i === f.veins.length - 1} data-testid={`vsys-down-${i}`} onClick={() => move(i, 1)}>
                  ↓
                </button>
                <button type="button" aria-label={`Убрать жилу ${i + 1}`} title="Убрать из системы" data-testid={`vsys-remove-${i}`} onClick={() => set(f.veins.filter((_, k) => k !== i))}>
                  ×
                </button>
              </span>
            </div>
            <div className={mod.params}>
              <label>
                <span>Граница</span>
                <select className={mod.select} value={v.bound} aria-label={`Граница жилы ${i + 1}`} data-testid={`vsys-bound-${i}`} onChange={(e) => patch(i, { bound: e.target.value === "polygon" ? "polygon" : "hull" })}>
                  <option value="hull">выпуклая оболочка контактов</option>
                  {v.polygon && <option value="polygon">полигон (u, v): вершин {v.polygon.length}</option>}
                </select>
              </label>
              <label>
                <span>Отступ, м</span>
                <input className={css.input} value={v.offset} placeholder="0" inputMode="decimal" spellCheck={false} disabled={v.bound === "polygon"} aria-label={`Отступ границы жилы ${i + 1}`} data-testid={`vsys-offset-${i}`} onChange={(e) => patch(i, { offset: e.target.value })} />
              </label>
              <label>
                <span>t_min, м</span>
                <input className={css.input} value={v.tMin} placeholder="0" inputMode="decimal" spellCheck={false} aria-label={`Мощность выклинивания жилы ${i + 1}`} data-testid={`vsys-tmin-${i}`} onChange={(e) => patch(i, { tMin: e.target.value })} />
              </label>
            </div>
          </div>
        ))}
        <div className={css.row}>
          <select className={mod.select} style={{ width: "auto", minWidth: 160 }} value="" aria-label="Добавить жилу" data-testid="vsys-add" disabled={!free.length || f.veins.length >= limit} onChange={(e) => e.target.value && set([...f.veins, newVein(e.target.value)])}>
            <option value="">{f.veins.length >= limit ? `не больше ${limit} жил` : free.length ? "+ жила (старше последней)" : codes ? "нет свободных кодов" : hasData ? "Чтение…" : "нет таблицы кодов"}</option>
            {free.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <span className={css.note}>
          Отступ и t_min — как у одиночной жилы (бока — кровля и подошва интервалов кода; выклинивание там, где мощность не больше t_min). Код входит в одну группу модели. Шаг сетки жилы — шаг модели h
          {model?.h.trim() ? `: ${fmt(Number(model.h.replace(",", ".")))} м` : ""}.
        </span>
        <FieldIssue rt={veinSystems} at={["params.veins", "codes"]} id="veins" />
      </fieldset>

      {s.groupId !== null && (
        <fieldset className={css.group} data-testid="vsys-result-box">
          <legend>Пересечения жил (последняя сборка)</legend>
          <VeinPairsTable pairs={pairs} names={names} />
          <LineOutputs lines={lines} run={(id, a) => app.run(id, a)} />
          <p className={css.note}>Объём общей зоны — до того, как молодая жила вырезала её из древней. Общее тело — объединение жил: оно записано отдельным каркасом и повторяет объёмы жил, поэтому в сумму объёмов не складывается.</p>
        </fieldset>
      )}
    </GroupFrame>
  );
}
