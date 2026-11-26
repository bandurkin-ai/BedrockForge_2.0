/**
 * Step 2 of the wizard «Новая модель» (ТЗ 8.9.2, 8.9.4): «Домены литологии». The data — the set of holes, the interval table, the field
 * of the codes, D_max and Δ_т; the groups — the codes of the field in groups by the age (the first is the youngest: it is cut out first,
 * the older ones are what is left under it), «Прочее» — the rest of the boundary, built by the core; the manual layers of strings (a layer
 * of lines as the contacts, the inside or the outside of a group). A group that is not a domain (a shell, a vein, an expansion) is only
 * carried: its window is the command of its own (19.24).
 */
import { useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { loadFields, loadSets, loadTables, resolveTable, type FieldRow, type SetRow, type TableRow } from "../composites/api";
import { loadCodes, type CodeList } from "../intercepts/api";
import { lineLayers, sceneObjects } from "../scene/objects";
import { freeName, isDomain, moveGroup, newDomain, TYPE_TITLES, usedCodes, type GroupForm, type ManualRole } from "./form";
import { useLatest, useSel } from "../estimation/hooks";
import mod from "./Wizard.module.css";
import { IssueAt, NumberRow, nf } from "./WizardParts";
import { setForm, wizardStore } from "./wizardState";

const ROLE_TITLES: Record<ManualRole, string> = { contact: "контакт", inside: "внутри", outside: "снаружи" };

export function WizardDomains() {
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults.data);
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(f.setId === null ? null : () => loadTables(f.setId!), [f.setId], fail);
  const tableKey = f.table && tables ? (resolveTable(tables, f.table) ?? f.table) : null;
  const tableKnown = !!tableKey && !!tables?.some((t) => t.table === tableKey);
  const fields = useLatest<FieldRow[]>(f.setId === null || !tableKnown ? null : () => loadFields(f.setId!, tableKey!), [f.setId, tableKey, tableKnown], fail);
  const fieldKnown = !!f.field && !!fields?.some((x) => x.name === f.field);
  const codes = useLatest<CodeList>(f.setId === null || !tableKnown || !fieldKnown ? null : () => loadCodes(f.setId!, tableKey!, f.field!), [f.setId, tableKey, f.field, tableKnown, fieldKnown], fail);
  const files = useSel(sceneObjects, (s) => s.files);
  const layers = lineLayers({ files });
  const used = usedCodes(f.groups);
  const free = (codes?.codes ?? []).filter((c) => !used.has(c));
  const limits = defaults?.params.limits;

  const patchGroup = (id: number, patch: Partial<GroupForm>) => setForm({ groups: f.groups.map((g) => (g.id === id ? { ...g, ...patch } : g)) });
  const removeGroup = (id: number) => setForm({ groups: f.groups.filter((g) => g.id !== id), manual: f.manual.map((m) => (m.group === id ? { ...m, group: null } : m)) });

  return (
    <>
      <fieldset className={css.group} data-testid="imp-step-2">
        <legend>Данные</legend>
        {error && (
          <p className={css.error} role="alert" data-testid="imp-data-error">
            {error}
          </p>
        )}
        <label className={css.formRow}>
          <span>Набор скважин</span>
          <select className={css.input} aria-label="Набор скважин" data-testid="imp-set" value={f.setId ?? ""} onChange={(e) => setForm({ setId: e.target.value ? Number(e.target.value) : null, table: null, field: null })}>
            <option value="">{sets ? "— без таблицы кодов —" : "Чтение…"}</option>
            {(sets ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="data.set_id" id="set" />
        <label className={css.formRow}>
          <span>Таблица</span>
          <select className={css.input} aria-label="Таблица кодов" data-testid="imp-table" value={tableKnown ? tableKey! : ""} disabled={f.setId === null} onChange={(e) => setForm({ table: e.target.value || null, field: null })}>
            <option value="">{f.setId === null ? "—" : tables ? "— выберите таблицу —" : "Чтение…"}</option>
            {(tables ?? []).map((t) => (
              <option key={t.table} value={t.table}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="data.table" id="table" />
        <label className={css.formRow}>
          <span>Поле кода</span>
          <select className={css.input} aria-label="Поле кода" data-testid="imp-field" value={fieldKnown ? f.field! : ""} disabled={!tableKnown} onChange={(e) => setForm({ field: e.target.value || null })}>
            <option value="">{tableKnown ? (fields ? "— выберите поле —" : "Чтение…") : "—"}</option>
            {(fields ?? []).map((x) => (
              <option key={x.key} value={x.name}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="data.field" id="field" />
        <NumberRow label="D_max, м" field="dMax" rangeKey="d_max" at="data.d_max" id="dmax" placeholder={String(defaults?.params.d_max ?? 30)} note="Расстояние, на котором значения s(t) растут от контакта." />
        <NumberRow label="Шаг точек Δ_т, м" field="step" rangeKey="step" at="data.step" id="step" placeholder={String(defaults?.params.step ?? 5)} />
      </fieldset>

      <fieldset className={css.group} data-testid="imp-groups">
        <legend>Группы по возрасту</legend>
        <span className={css.note}>Первая группа — самая молодая: её тело вырезается первым, старшие занимают то, что осталось. «Прочее» — остальное в границе.</span>
        {codes && (
          <span className={css.note} data-testid="imp-codes-note">
            Кодов в поле: {nf(codes.total)}{codes.truncated ? ` (показаны ${nf(codes.codes.length)})` : ""}. Свободных: {nf(free.length)}.
          </span>
        )}
        <IssueAt at="groups" id="groups" />
        {f.groups.map((g, i) => (
          <div key={g.id} className={mod.group} data-testid={`imp-group-${i}`}>
            <div className={mod.groupHead}>
              <span className={mod.no}>{i + 1}</span>
              {isDomain(g) ? (
                <input className={css.input} value={g.name} aria-label={`Название группы ${i + 1}`} data-testid={`imp-group-name-${i}`} onChange={(e) => patchGroup(g.id, { name: e.target.value })} />
              ) : (
                <span className={mod.grow} data-testid={`imp-group-name-${i}`}>
                  {g.name}
                </span>
              )}
              <span className={mod.kind}>{TYPE_TITLES[g.type] ?? g.type}</span>
              <button type="button" className={css.iconBtn} aria-label="Моложе" title="Сделать моложе (выше)" disabled={i === 0} data-testid={`imp-group-up-${i}`} onClick={() => setForm({ groups: moveGroup(f.groups, i, -1) })}>
                ↑
              </button>
              <button type="button" className={css.iconBtn} aria-label="Старше" title="Сделать старше (ниже)" disabled={i === f.groups.length - 1} data-testid={`imp-group-down-${i}`} onClick={() => setForm({ groups: moveGroup(f.groups, i, 1) })}>
                ↓
              </button>
              <button type="button" className={css.iconBtn} aria-label="Убрать группу" title="Убрать группу" data-testid={`imp-group-del-${i}`} onClick={() => removeGroup(g.id)}>
                ×
              </button>
            </div>
            <IssueAt at={`groups.${i}.name`} id={`group-name-${i}`} />
            <div className={mod.chips}>
              {g.codes.map((c) => (
                <span key={c} className={mod.chip} data-testid={`imp-code-${i}-${c}`}>
                  {c}
                  {isDomain(g) && (
                    <button type="button" aria-label={`Убрать код ${c}`} onClick={() => patchGroup(g.id, { codes: g.codes.filter((x) => x !== c) })}>
                      ×
                    </button>
                  )}
                </span>
              ))}
              {isDomain(g) && (
                <select
                  className={css.input}
                  style={{ width: "auto", minWidth: 120 }}
                  value=""
                  aria-label={`Добавить код в группу ${i + 1}`}
                  data-testid={`imp-code-add-${i}`}
                  disabled={!free.length}
                  onChange={(e) => e.target.value && patchGroup(g.id, { codes: [...g.codes, e.target.value] })}
                >
                  <option value="">{free.length ? "+ код" : codes ? "нет свободных кодов" : "выберите поле кода"}</option>
                  {free.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <IssueAt at={`groups.${i}.codes`} id={`group-codes-${i}`} />
          </div>
        ))}
        <div className={mod.other} data-testid="imp-other">
          Прочее — всё остальное в границе (строится ядром, кодов нет)
        </div>
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={!!limits && f.groups.length >= limits.groups} data-testid="imp-group-add" onClick={() => setForm({ groups: [...f.groups, newDomain(f.groups, freeName(f.groups))] })}>
            Добавить группу
          </button>
          <button
            type="button"
            className={css.btn}
            disabled={!free.length}
            title="По группе на каждый свободный код поля, по порядку кодов"
            data-testid="imp-group-fill"
            onClick={() => setForm({ groups: [...f.groups, ...free.reduce<GroupForm[]>((acc, c) => [...acc, newDomain([...f.groups, ...acc], c, [c])], [])] })}
          >
            Группа на каждый код
          </button>
        </div>
      </fieldset>

      <fieldset className={css.group} data-testid="imp-manual">
        <legend>Ручные данные</legend>
        <span className={css.note}>Слой линий из редактора стрингов: линия — контакт группы, внутри или снаружи неё.</span>
        {f.manual.map((m, i) => (
          <div key={i} className={mod.manual} data-testid={`imp-manual-${i}`}>
            <select className={css.input} aria-label={`Слой ${i + 1}`} value={m.layerId ?? ""} data-testid={`imp-manual-layer-${i}`} onChange={(e) => setForm({ manual: f.manual.map((x, k) => (k === i ? { ...x, layerId: e.target.value ? Number(e.target.value) : null } : x)) })}>
              <option value="">— слой линий —</option>
              {layers.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
            <select className={css.input} aria-label={`Роль слоя ${i + 1}`} value={m.role} data-testid={`imp-manual-role-${i}`} onChange={(e) => setForm({ manual: f.manual.map((x, k) => (k === i ? { ...x, role: e.target.value as ManualRole } : x)) })}>
              {(Object.keys(ROLE_TITLES) as ManualRole[]).map((r) => (
                <option key={r} value={r}>
                  {ROLE_TITLES[r]}
                </option>
              ))}
            </select>
            <select className={css.input} aria-label={`Группа слоя ${i + 1}`} value={m.group ?? ""} data-testid={`imp-manual-group-${i}`} onChange={(e) => setForm({ manual: f.manual.map((x, k) => (k === i ? { ...x, group: e.target.value ? Number(e.target.value) : null } : x)) })}>
              <option value="">— группа —</option>
              {f.groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name || `Группа ${g.id}`}
                </option>
              ))}
            </select>
            <button type="button" className={css.iconBtn} aria-label="Убрать слой" data-testid={`imp-manual-del-${i}`} onClick={() => setForm({ manual: f.manual.filter((_, k) => k !== i) })}>
              ×
            </button>
            <IssueAt at={`manual.${i}`} id={`manual-${i}`} />
          </div>
        ))}
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={!!limits && f.manual.length >= limits.manual} data-testid="imp-manual-add" onClick={() => setForm({ manual: [...f.manual, { layerId: null, role: "contact", group: f.groups[0]?.id ?? null }] })}>
            Добавить слой
          </button>
        </div>
      </fieldset>
    </>
  );
}
