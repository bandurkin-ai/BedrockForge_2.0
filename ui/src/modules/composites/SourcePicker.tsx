/**
 * The source of a tool window (ТЗ 8.2.3): the set of holes, the interval table (or several), its fields, the domain —
 * a field of the same or another interval table, or wireframes. Tables given by name (the assistant) are turned into
 * keys once the list is read; «поля» of a new window — every numeric field. The lists are read from the core; the
 * newest read only is applied.
 */
import { useEffect, useRef, useState } from "react";
import { errorText, toggleId } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import own from "./Composites.module.css";
import { loadFields, loadSets, loadTables, loadWireframes, resolveTable, type FieldRow, type SetRow, type TableRow, type WireframeRow } from "./api";
import type { DomainChoice, SourceNeeds, SourceState } from "./params";

/** A read that is applied only while it is the newest one of its kind. */
function useLatest<T>(load: (() => Promise<T>) | null, deps: unknown[], fail: (e: unknown) => void): T | null {
  const [value, setValue] = useState<T | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    setValue(null);
    if (!load) return;
    load().then(
      (v) => mine === seq.current && setValue(v),
      (e: unknown) => mine === seq.current && fail(e),
    );
  }, deps);
  return value;
}

/** The names in `chosen` that are still fields of the table, in the order of the table. */
export function keepFields(fields: readonly FieldRow[], chosen: readonly string[]): string[] {
  return fields.filter((f) => chosen.includes(f.name)).map((f) => f.name);
}

/** The source with table names turned into keys; null — nothing to change. */
export function resolveNames(tables: readonly TableRow[], s: SourceState): SourceState | null {
  let changed = false;
  const key = (t: string): string => {
    const k = resolveTable(tables, t) ?? t;
    if (k !== t) changed = true;
    return k;
  };
  const table = s.table ? key(s.table) : s.table;
  const list = s.tables.map(key);
  const domain: DomainChoice = s.domain.kind === "field" ? { ...s.domain, table: key(s.domain.table || table || "") } : s.domain;
  return changed ? { ...s, table, tables: list, domain } : null;
}

export function SourcePicker({ needs, value, onChange, onFields, activeSet }: { needs: SourceNeeds; value: SourceState; onChange: (s: SourceState) => void; onFields?: (f: FieldRow[]) => void; activeSet?: number | null }) {
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(value.setId === null ? null : () => loadTables(value.setId!), [value.setId], fail);
  const fields = useLatest<FieldRow[]>(value.setId === null || !value.table || needs.table === "many" ? null : () => loadFields(value.setId!, value.table!), [value.setId, value.table, needs.table], fail);
  const domainTable = value.domain.kind === "field" ? value.domain.table : null;
  const domainFields = useLatest<FieldRow[]>(value.setId === null || !domainTable || !needs.domain ? null : () => loadFields(value.setId!, domainTable), [value.setId, domainTable, needs.domain], fail);
  const wantWireframes = needs.wireframes || (needs.domain && value.domain.kind === "wireframes");
  const wireframes = useLatest<WireframeRow[]>(wantWireframes ? loadWireframes : null, [wantWireframes], fail);
  const firstFields = useRef(true);

  // the set: the given one, else the active one, else the first
  useEffect(() => {
    if (!sets || value.setId !== null || !sets.length) return;
    const pick = sets.find((s) => s.id === activeSet) ?? sets[0];
    onChange({ ...value, setId: pick.id });
  }, [sets]);

  // the table: names → keys; nothing given — the first (assay first)
  useEffect(() => {
    if (!tables) return;
    const named = resolveNames(tables, value);
    if (named) return onChange(named);
    if (needs.table === "one" && (!value.table || !tables.some((t) => t.table === value.table)) && tables.length) onChange({ ...value, table: tables[0].table, fields: [] });
  }, [tables]);

  // the fields: kept by name; a new window — every numeric field (one tool field — the first numeric)
  useEffect(() => {
    if (!fields) return;
    onFields?.(fields);
    if (needs.fields === "none") return;
    const numeric = fields.filter((f) => f.kind === "number").map((f) => f.name);
    let next = keepFields(fields, value.fields);
    if (!next.length && (firstFields.current || !value.fields.length)) next = needs.fields === "one" ? numeric.slice(0, 1) : numeric;
    if (needs.fields === "one") next = next.slice(0, 1);
    firstFields.current = false;
    if (JSON.stringify(next) !== JSON.stringify(value.fields)) onChange({ ...value, fields: next });
  }, [fields]);

  const set = (patch: Partial<SourceState>) => onChange({ ...value, ...patch });
  const setDomain = (domain: DomainChoice) => set({ domain });
  const tableList = tables ?? [];
  const numeric = (fields ?? []).filter((f) => f.kind === "number");

  return (
    <fieldset className={css.group} data-testid="composites-source">
      <legend>Источник</legend>
      <label className={css.formRow}>
        <span>Набор скважин</span>
        <select className={css.input} aria-label="Набор скважин" value={value.setId ?? ""} onChange={(e) => onChange({ ...value, setId: e.target.value ? Number(e.target.value) : null, table: null, tables: [], fields: [], domain: { kind: "none" } })}>
          {value.setId === null && <option value="">{sets ? "—" : "Чтение…"}</option>}
          {(sets ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      {needs.table === "one" ? (
        <label className={css.formRow}>
          <span>Таблица</span>
          <select className={css.input} aria-label="Таблица" value={value.table ?? ""} onChange={(e) => set({ table: e.target.value || null, fields: [] })}>
            {!tableList.length && <option value="">{tables ? "Нет интервальных таблиц" : "Чтение…"}</option>}
            {tableList.map((t) => (
              <option key={t.table} value={t.table}>
                {t.name}
                {t.origin?.stale ? " (устарел)" : ""}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className={own.checklist} role="group" aria-label="Таблицы">
          {tableList.map((t) => (
            <label key={t.table} className={css.check}>
              <input type="checkbox" checked={value.tables.includes(t.table)} onChange={(e) => set({ tables: e.target.checked ? [...value.tables, t.table] : value.tables.filter((x) => x !== t.table) })} />
              {t.name}
            </label>
          ))}
        </div>
      )}
      {needs.fields === "one" && (
        <label className={css.formRow}>
          <span>Поле</span>
          <select className={css.input} aria-label="Поле" value={value.fields[0] ?? ""} onChange={(e) => set({ fields: e.target.value ? [e.target.value] : [] })}>
            {!numeric.length && <option value="">{fields ? "Нет числовых полей" : "Чтение…"}</option>}
            {numeric.map((f) => (
              <option key={f.key} value={f.name}>
                {f.name}
                {f.unit ? `, ${f.unit}` : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {needs.fields === "many" && (
        <div className={own.checklist} role="group" aria-label="Поля">
          {(fields ?? []).map((f) => (
            <label key={f.key} className={css.check}>
              <input type="checkbox" checked={value.fields.includes(f.name)} onChange={(e) => set({ fields: keepFields(fields ?? [], e.target.checked ? [...value.fields, f.name] : value.fields.filter((x) => x !== f.name)) })} />
              {f.name}
              {f.unit ? <span className={css.count}>{f.unit}</span> : null}
            </label>
          ))}
          {fields && !fields.length && <p className={css.note}>Нет полей</p>}
        </div>
      )}
      {needs.domain && (
        <>
          <label className={css.formRow}>
            <span>Домен</span>
            <select
              className={css.input}
              aria-label="Домен"
              value={value.domain.kind}
              onChange={(e) => setDomain(e.target.value === "field" ? { kind: "field", table: value.table ?? "", field: "" } : e.target.value === "wireframes" ? { kind: "wireframes", ids: [] } : { kind: "none" })}
            >
              <option value="none">без доменов</option>
              <option value="field">поле интервальной таблицы</option>
              <option value="wireframes">каркасы</option>
            </select>
          </label>
          {value.domain.kind === "field" && (
            <>
              <label className={css.formRow}>
                <span>Таблица домена</span>
                <select className={css.input} aria-label="Таблица домена" value={value.domain.table} onChange={(e) => setDomain({ kind: "field", table: e.target.value, field: "" })}>
                  {tableList.map((t) => (
                    <option key={t.table} value={t.table}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className={css.formRow}>
                <span>Поле домена</span>
                <select className={css.input} aria-label="Поле домена" value={value.domain.field} onChange={(e) => value.domain.kind === "field" && setDomain({ ...value.domain, field: e.target.value })}>
                  <option value="">—</option>
                  {(domainFields ?? []).map((f) => (
                    <option key={f.key} value={f.name}>
                      {f.name}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
        </>
      )}
      {(needs.wireframes || (needs.domain && value.domain.kind === "wireframes")) && (
        <div className={own.checklist} role="group" aria-label="Каркасы">
          {(wireframes ?? []).map((w) => {
            const ids = needs.wireframes ? value.wireframeIds : value.domain.kind === "wireframes" ? value.domain.ids : [];
            const toggle = (on: boolean) => (needs.wireframes ? set({ wireframeIds: toggleId(ids, w.id, on) }) : setDomain({ kind: "wireframes", ids: toggleId(ids, w.id, on) }));
            return (
              <label key={w.id} className={css.check}>
                <input type="checkbox" checked={ids.includes(w.id)} onChange={(e) => toggle(e.target.checked)} />
                {w.folder ? `${w.folder} › ${w.name}` : w.name}
                {ids.includes(w.id) && ids.length > 1 ? <span className={css.count}>приоритет {ids.indexOf(w.id) + 1}</span> : null}
              </label>
            );
          })}
          {wireframes && !wireframes.length && <p className={css.note}>В проекте нет каркасов</p>}
        </div>
      )}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
