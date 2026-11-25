/**
 * The data of a calculation of the window «Вариография» (ТЗ 8.6.4; ADR 0022 §15, left panel): the set, the interval
 * table (samples, composites), the numeric field, the domain (a field of the same or another interval table and its
 * values), the weights (DECL_W when the table has it, none, or a field), the transform (none / normal scores / the indicator of a threshold t). Once
 * the data is complete, `GET /variography/defaults` is read (the newest read only) and given to `onDefaults`: the
 * counts of points, σ², the median length and the defaults of the form. Tables named by the assistant become keys
 * once the list is read.
 */
import { useEffect, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import own from "../composites/Composites.module.css";
import { loadFields, loadSets, loadTables, resolveTable, type FieldRow, type SetRow, type TableRow } from "../composites/api";
import { loadCodes, type CodeList } from "../intercepts/api";
import { INDICATOR, isIndicator, loadDefaults, type DefaultsAnswer } from "./api";
import { dataBody, type DataState, type WeightsChoice } from "./state";

/** The pause before `/defaults` after a change (the field list is typed through quickly). */
export const DEFAULTS_DELAY_MS = 200;

/** A read applied only while it is the newest one of its kind. */
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

const f = (x: number) => x.toLocaleString("ru-RU");
const g = (x: number | null) => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));

/** «Точек: 1 196 из 1 200 (пропуск значения 4)»; the parts that are zero are not written. */
export function countsText(d: DefaultsAnswer): string {
  const c = d.counts;
  const parts = [
    [c.missing, "пропуск значения"],
    [c.no_position, "нет положения"],
    [c.outside_domain, "вне домена"],
    [c.no_weight, "без веса"],
  ]
    .filter(([n]) => (n as number) > 0)
    .map(([n, t]) => `${t} ${f(n as number)}`);
  return `Точек: ${f(c.used)} из ${f(c.rows)}${parts.length ? ` (${parts.join(", ")})` : ""}`;
}

const unitOf = (d: DefaultsAnswer | null) => d?.unit ?? "";

/** «Индикатор: порог 0,5, доля z ≤ t — 0,42»; "" — not an indicator. */
export function indicatorText(d: DefaultsAnswer): string {
  return d.indicator ? `индикатор: порог ${g(d.indicator.threshold)}, доля точек z ≤ t — ${g(d.indicator.p)}` : "";
}

/** σ², the median length, the weights — one line under the form. */
export function summaryText(d: DefaultsAnswer): string {
  const unit = d.unit ? ` (${d.unit})²` : "";
  const s2 = d.sigma2_z !== null && d.sigma2 !== d.sigma2_z ? `σ² = ${g(d.sigma2)}, σ²(Z) = ${g(d.sigma2_z)}${unit}` : `σ² = ${g(d.sigma2)}${unit}`;
  const med = d.median_length === null ? "медиана длин не определена" : `медиана длин ${g(d.median_length)} м`;
  return `${[s2, indicatorText(d), med].filter(Boolean).join("; ")}; веса: ${d.weights ?? "нет"}`;
}

export function DataForm({ value, onChange, onDefaults, activeSet }: { value: DataState; onChange: (d: DataState) => void; onDefaults?: (d: DefaultsAnswer | null) => void; activeSet?: number | null }) {
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(value.setId === null ? null : () => loadTables(value.setId!), [value.setId], fail);
  const tableKnown = !!value.table && !!tables?.some((t) => t.table === value.table);
  const fields = useLatest<FieldRow[]>(value.setId === null || !tableKnown ? null : () => loadFields(value.setId!, value.table!), [value.setId, value.table, tableKnown], fail);
  const dom = value.domain;
  const domTable = dom ? dom.table || value.table : null;
  const domFields = useLatest<FieldRow[]>(value.setId === null || !domTable || !tableKnown ? null : () => loadFields(value.setId!, domTable), [value.setId, domTable, tableKnown], fail);
  const codes = useLatest<CodeList>(value.setId === null || !domTable || !dom?.field || !tableKnown ? null : () => loadCodes(value.setId!, domTable, dom.field), [value.setId, domTable, dom?.field, tableKnown], fail);
  const [defaults, setDefaults] = useState<DefaultsAnswer | null>(null);
  const seq = useRef(0);

  const set = (patch: Partial<DataState>) => onChange({ ...value, ...patch });

  // the set: the given one, else the active one, else the first
  useEffect(() => {
    if (!sets || value.setId !== null || !sets.length) return;
    set({ setId: (sets.find((s) => s.id === activeSet) ?? sets[0]).id });
  }, [sets]);

  // the table: a name → its key; none or unknown — the first (assay first); the domain table likewise
  useEffect(() => {
    if (!tables) return;
    const k = value.table ? resolveTable(tables, value.table) : null;
    const table = k ?? tables[0]?.table ?? null;
    const dk = dom?.table ? (resolveTable(tables, dom.table) ?? dom.table) : "";
    if (table !== value.table || (dom && dk !== dom.table)) set({ table, ...(table !== value.table && !k ? { field: null } : {}), ...(dom ? { domain: { ...dom, table: dk } } : {}) });
  }, [tables]);

  // the field: kept by name; none — the first numeric
  useEffect(() => {
    if (!fields) return;
    const numeric = fields.filter((x) => x.kind === "number");
    const keep = numeric.find((x) => x.name === value.field || x.key === value.field);
    const field = keep?.name ?? numeric[0]?.name ?? null;
    if (field !== value.field) set({ field });
  }, [fields]);

  // the defaults of the complete data (the newest read only)
  const body = dataBody(value).body;
  const bodyKey = body ? JSON.stringify(body) : "";
  useEffect(() => {
    const mine = ++seq.current;
    setDefaults(null);
    onDefaults?.(null);
    if (!body || !tableKnown) return;
    const t = setTimeout(() => {
      loadDefaults(body).then(
        (d) => {
          if (mine !== seq.current) return;
          setError(null);
          setDefaults(d);
          onDefaults?.(d);
        },
        (e: unknown) => mine === seq.current && fail(e),
      );
    }, DEFAULTS_DELAY_MS);
    return () => clearTimeout(t);
  }, [bodyKey, tableKnown]);

  const tableList = tables ?? [];
  const numeric = (fields ?? []).filter((x) => x.kind === "number");
  const weightValue = value.weights.kind === "field" ? `field:${value.weights.field}` : value.weights.kind;
  const setWeights = (v: string) => set({ weights: (v.startsWith("field:") ? { kind: "field", field: v.slice(6) } : { kind: v === "none" ? "none" : "auto" }) as WeightsChoice });
  const toggleValue = (code: string, on: boolean) => dom && set({ domain: { ...dom, values: on ? [...dom.values, code] : dom.values.filter((x) => x !== code) } });

  return (
    <fieldset className={css.group} data-testid="variography-data">
      <legend>Данные</legend>
      <label className={css.formRow}>
        <span>Набор скважин</span>
        <select className={css.input} aria-label="Набор скважин" value={value.setId ?? ""} onChange={(e) => onChange({ ...value, setId: e.target.value ? Number(e.target.value) : null, table: null, field: null, domain: null, weights: { kind: "auto" } })}>
          {value.setId === null && <option value="">{sets ? "—" : "Чтение…"}</option>}
          {(sets ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <label className={css.formRow}>
        <span>Таблица</span>
        <select className={css.input} aria-label="Таблица" value={tableKnown ? value.table! : ""} onChange={(e) => set({ table: e.target.value || null, field: null, domain: null, weights: { kind: "auto" } })}>
          {!tableList.length && <option value="">{tables ? "Нет интервальных таблиц" : "Чтение…"}</option>}
          {tableList.map((t) => (
            <option key={t.table} value={t.table}>
              {t.name}
              {t.origin?.stale ? " (устарел)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className={css.formRow}>
        <span>Поле</span>
        <select className={css.input} aria-label="Поле" value={value.field ?? ""} onChange={(e) => set({ field: e.target.value || null })}>
          {!numeric.length && <option value="">{fields ? "Нет числовых полей" : "Чтение…"}</option>}
          {numeric.map((x) => (
            <option key={x.key} value={x.name}>
              {x.name}
              {x.unit ? `, ${x.unit}` : ""}
            </option>
          ))}
        </select>
      </label>
      <label className={css.formRow}>
        <span>Домен</span>
        <select className={css.input} aria-label="Домен" value={dom ? "field" : "none"} onChange={(e) => set({ domain: e.target.value === "field" ? { table: "", field: "", values: [] } : null })}>
          <option value="none">все точки</option>
          <option value="field">по полю домена</option>
        </select>
      </label>
      {dom && (
        <>
          <label className={css.formRow}>
            <span>Таблица домена</span>
            <select className={css.input} aria-label="Таблица домена" value={dom.table || value.table || ""} onChange={(e) => set({ domain: { table: e.target.value === value.table ? "" : e.target.value, field: "", values: [] } })}>
              {tableList.map((t) => (
                <option key={t.table} value={t.table}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className={css.formRow}>
            <span>Поле домена</span>
            <select className={css.input} aria-label="Поле домена" value={dom.field} onChange={(e) => set({ domain: { ...dom, field: e.target.value, values: [] } })}>
              <option value="">—</option>
              {(domFields ?? []).map((x) => (
                <option key={x.key} value={x.name}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
          {dom.field && (
            <div className={own.checklist} role="group" aria-label="Значения домена">
              {(codes?.codes ?? []).map((c) => (
                <label key={c} className={css.check}>
                  <input type="checkbox" checked={dom.values.includes(c)} onChange={(e) => toggleValue(c, e.target.checked)} />
                  {c}
                </label>
              ))}
              {codes && !codes.codes.length && <p className={css.note}>В поле нет значений</p>}
              {codes?.truncated && <p className={css.note}>Показаны {f(codes.codes.length)} из {f(codes.total)}</p>}
            </div>
          )}
        </>
      )}
      <label className={css.formRow}>
        <span>Веса</span>
        <select className={css.input} aria-label="Веса" value={weightValue} onChange={(e) => setWeights(e.target.value)}>
          <option value="auto">DECL_W, если есть</option>
          <option value="none">без весов</option>
          {numeric
            .filter((x) => x.name !== value.field)
            .map((x) => (
              <option key={x.key} value={`field:${x.name}`}>
                {x.name}
              </option>
            ))}
        </select>
      </label>
      <label className={css.formRow}>
        <span>Преобразование</span>
        <select className={css.input} aria-label="Преобразование" value={isIndicator(value.transform) ? "indicator" : value.transform} onChange={(e) => set({ transform: e.target.value === "nscore" ? "nscore" : e.target.value === "indicator" ? `${INDICATOR}${isIndicator(value.transform) ? value.transform.slice(INDICATOR.length) : ""}` : "none" })}>
          <option value="none">без преобразования</option>
          <option value="nscore">нормальные оценки</option>
          <option value="indicator">Индикатор (порог t)</option>
        </select>
      </label>
      {isIndicator(value.transform) && (
        <label className={css.formRow}>
          <span>Порог t{unitOf(defaults) ? `, ${unitOf(defaults)}` : ""}</span>
          <input className={css.input} aria-label="Порог индикатора" inputMode="decimal" placeholder="i = 1, если z ≤ t" value={value.transform.slice(INDICATOR.length)} onChange={(e) => set({ transform: `${INDICATOR}${e.target.value}` })} />
        </label>
      )}
      {defaults && (
        <p className={css.note} data-testid="variography-counts">
          {countsText(defaults)}. {summaryText(defaults)}
        </p>
      )}
      {!body && tables && <p className={css.note}>{dataBody(value).reason}</p>}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
