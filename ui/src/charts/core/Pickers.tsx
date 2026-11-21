/**
 * Pickers of the chart settings (ADR 0019 §2: a table is its `name_key`, a field is its name): the set, the table
 * of the set, a numeric field with its unit, a list of holes; `useStatsFields` — the fields of a table. Shared by the histogram, Q-Q, dendrogram and the charts of 8.3.
 */
import { useEffect, useState } from "react";
import { errorText } from "../../shell/props";
import { loadFields, type NumericField, type StatsFields, type StatsTable } from "../../modules/charts/api";
import { parseList } from "../../modules/charts/settings";

/** «AU, г/т», «Длина интервала, м». */
export function fieldLabel(f: Pick<NumericField, "name" | "unit" | "kind">): string {
  return `${f.kind === "length" ? "Длина интервала" : f.name}${f.unit ? `, ${f.unit}` : ""}`;
}

export function SetSelect({ label, value, sets, onChange }: { label: string; value: number; sets: readonly { id: number; name: string }[]; onChange: (id: number) => void }) {
  const list = sets.some((s) => s.id === value) ? sets : [...sets, { id: value, name: `Набор ${value}` }];
  return (
    <label>
      Набор
      <select value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))}>
        {list.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function TableSelect({ label = "Таблица", value, tables, onChange }: { label?: string; value: string | null; tables: readonly StatsTable[]; onChange: (key: string | null) => void }) {
  return (
    <label>
      Таблица
      <select value={value ?? ""} aria-label={label} onChange={(e) => onChange(e.target.value || null)}>
        {!tables.length && <option value="">нет таблиц проб</option>}
        {tables.map((t) => (
          <option key={t.key} value={t.key}>
            {t.name} ({t.rows.toLocaleString("ru-RU")})
          </option>
        ))}
      </select>
    </label>
  );
}

/** `title` — the caption, `label` — the accessible name (the caption by default); `empty` — an option «none». */
export function FieldSelect({ title = "Поле", label, value, fields, onChange, empty }: { title?: string; label?: string; value: string | null; fields: readonly NumericField[]; onChange: (name: string | null) => void; empty?: string }) {
  return (
    <label>
      {title}
      <select value={value ?? ""} aria-label={label ?? title} onChange={(e) => onChange(e.target.value || null)}>
        {empty !== undefined && <option value="">{empty}</option>}
        {fields.map((f) => (
          <option key={f.name} value={f.name}>
            {fieldLabel(f)}
          </option>
        ))}
      </select>
    </label>
  );
}

/** A list typed as «H1, H2 H3» (commas, semicolons or spaces); empty — `placeholder`. The text is kept as typed. */
export function ListInput({ title, label, value, onChange, placeholder = "все", hint }: { title: string; label: string; value: readonly string[]; onChange: (list: string[]) => void; placeholder?: string; hint?: string }) {
  const [text, setText] = useState(value.join(", "));
  // a list set from outside (another window, the server) replaces the text unless the text already means it
  useEffect(() => {
    if (parseList(text).join("\n") !== value.join("\n")) setText(value.join(", "));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <label>
      {title}
      <input
        value={text}
        aria-label={label}
        placeholder={placeholder}
        title={hint ?? "Через запятую или пробел; пусто — все"}
        onChange={(e) => {
          setText(e.target.value);
          onChange(parseList(e.target.value));
        }}
      />
    </label>
  );
}

/**
 * The fields of a table of a set for a window: null while asked; `error` — the text of a failure. A new set or
 * table asks again; an answer for an older one is dropped.
 */
export function useStatsFields(setId: number | null, table: string | null, texts = false): { fields: StatsFields | null; error: string | null } {
  const [state, setState] = useState<{ key: string; fields: StatsFields | null; error: string | null }>({ key: "", fields: null, error: null });
  const key = `${setId}:${table ?? ""}:${texts}`;
  useEffect(() => {
    if (setId === null) return;
    let alive = true;
    loadFields(setId, table, { texts })
      .then((fields) => alive && setState({ key, fields, error: null }))
      .catch((e: unknown) => alive && setState({ key, fields: null, error: errorText(e) }));
    return () => {
      alive = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return state.key === key ? { fields: state.fields, error: state.error } : { fields: null, error: null };
}

/**
 * The domain of the charts by domains (ADR 0019 решение 19): a text field of the same table or of another interval
 * table of the set (its code goes by the middle of the sample); `empty` — the option «без доменов».
 */
export function DomainSelect({ setId, table, tables, domainTable, domain, onChange, empty = "нет — все пробы" }: { setId: number; table: string | null; tables: readonly StatsTable[]; domainTable: string | null; domain: string | null; onChange: (patch: { domain_table: string | null; domain: string | null }) => void; empty?: string | null }) {
  const own = useStatsFields(setId, domainTable ?? table, true);
  const texts = own.fields?.text ?? [];
  return (
    <>
      <label>
        Таблица домена
        <select value={domainTable ?? ""} aria-label="Таблица домена" title="Другая таблица набора: код берётся по середине пробы" onChange={(e) => onChange({ domain_table: e.target.value || null, domain: null })}>
          <option value="">та же</option>
          {tables.filter((t) => t.key !== table).map((t) => (
            <option key={t.key} value={t.key}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Домен
        <select value={domain ?? ""} aria-label="Домен" onChange={(e) => onChange({ domain_table: domainTable, domain: e.target.value || null })}>
          {empty !== null && <option value="">{empty}</option>}
          {empty === null && !domain && <option value="">— выберите —</option>}
          {texts.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name}
              {f.count !== null ? ` (${f.count})` : ""}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

/** The codes of a text field of a table (≤ 300): the domains A and B of the contact analysis. */
export function useDomainCodes(setId: number, table: string | null, field: string | null): string[] {
  const f = useStatsFields(field ? setId : null, table, true);
  return f.fields?.text.find((t) => t.name === field)?.values ?? [];
}

/** The source of a chart by domains: the table, the field, the domain (`domainEmpty` null — a domain is required). */
export function DomainSource({ setId, value, fields, onChange, domainEmpty }: { setId: number; value: { table: string | null; field: string | null; domain: string | null; domain_table: string | null }; fields: StatsFields | null; onChange: (patch: Partial<{ table: string | null; field: string | null; domain: string | null; domain_table: string | null }>) => void; domainEmpty?: string | null }) {
  const tables = fields?.tables ?? [];
  return (
    <fieldset>
      <legend>Данные</legend>
      <TableSelect value={value.table} tables={tables} onChange={(table) => onChange({ table, domain: value.domain_table ? value.domain : null })} />
      <FieldSelect value={value.field} fields={fields?.numeric ?? []} onChange={(field) => onChange({ field })} />
      <DomainSelect setId={setId} table={value.table ?? fields?.table ?? null} tables={tables} domainTable={value.domain_table} domain={value.domain} onChange={onChange} empty={domainEmpty} />
    </fieldset>
  );
}

/** A field kept only while the table has it (null otherwise). */
export const keepField = (f: StatsFields | null, name: string | null): string | null => (f && name !== null && f.numeric.some((x) => x.name === name) ? name : null);
