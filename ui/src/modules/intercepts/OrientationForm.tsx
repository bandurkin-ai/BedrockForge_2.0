/**
 * The block «Ориентировка» (ADR 0021 §4, §11) shared by «Истинная мощность» and «Рудные пересечения»: the switch of
 * four sources, the inputs of the chosen one with the ranges of 8.5.4 (a value out of range is marked and never
 * sent), «Если не найдена — глобальная α, Aп». The lists (tables of the set with events, their fields, wireframes)
 * are read from the core; the newest read only is applied. `optional` — the block can be switched off (TT empty).
 * «Взять с каркаса» (desired, last) fills α and Aп of the global source from the mean plane of wireframes.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { errorText, toggleId } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { loadFields, loadWireframes, resolveTable, type FieldRow, type TableRow, type WireframeRow } from "../composites/api";
import { rangeText, type ParamSpec } from "../composites/params";
import { loadCodes, loadOrientTables } from "./api";
import { DomainGrid } from "./DomainGrid";
import own from "./Intercepts.module.css";
import { PlanePicker } from "./PlanePicker";
import { ALPHA, D_CORE, D_MAX, DIPDIR, ORIENT_SOURCES, R_N, SOURCE_TITLES, type OrientationState } from "./params";

function useRead<T>(load: (() => Promise<T>) | null, deps: unknown[], fail: (e: unknown) => void): T | null {
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

function NumberRow({ p, value, error, onChange, label }: { p: ParamSpec; value: string; error?: string; onChange: (v: string) => void; label?: string }) {
  const range = rangeText(p);
  const name = label ?? p.label;
  return (
    <label className={css.formRow} title={[p.hint, range && `диапазон ${range}`].filter(Boolean).join("; ")} data-testid={`intercepts-orient-${p.key}`}>
      <span>
        {name}
        {p.unit ? `, ${p.unit}` : ""}
      </span>
      <span>
        <input className={`${css.input} ${error ? own.bad : ""}`} aria-label={name} aria-invalid={error ? true : undefined} inputMode="decimal" placeholder={range} value={value} onChange={(e) => onChange(e.target.value)} />
        {error && <span className={own.errText}>{error}</span>}
      </span>
    </label>
  );
}

function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className={css.formRow}>
      <span>{label}</span>
      <select className={css.input} aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </label>
  );
}

const fieldOptions = (fields: readonly FieldRow[] | null, numeric: boolean) =>
  (fields ?? [])
    .filter((f) => !numeric || f.kind === "number")
    .map((f) => (
      <option key={f.key} value={f.name}>
        {f.name}
      </option>
    ));

function Wireframes({ list, ids, onChange, label, max }: { list: readonly WireframeRow[] | null; ids: readonly number[]; onChange: (ids: number[]) => void; label: string; max?: number }) {
  return (
    <div className={own.checklist} role="group" aria-label={label}>
      {(list ?? []).map((w) => (
        <label key={w.id} className={css.check}>
          <input type="checkbox" checked={ids.includes(w.id)} disabled={!ids.includes(w.id) && max !== undefined && ids.length >= max} onChange={(e) => onChange(toggleId(ids, w.id, e.target.checked))} />
          {w.folder ? `${w.folder} › ${w.name}` : w.name}
        </label>
      ))}
      {list && !list.length && <p className={css.note}>В проекте нет каркасов и поверхностей</p>}
      {!list && <p className={css.note}>Чтение…</p>}
    </div>
  );
}

export interface OrientationFormProps {
  setId: number | null;
  /** The key of the source table (its fields — `fields`). */
  table: string | null;
  fields: readonly FieldRow[];
  value: OrientationState;
  errors: Record<string, string>;
  onChange: (v: OrientationState) => void;
  /** «Рудные пересечения»: the block can be switched off. */
  optional?: boolean;
}

export function OrientationForm({ setId, table, fields, value, errors, onChange, optional }: OrientationFormProps) {
  const radio = useId();
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const set = (patch: Partial<OrientationState>) => onChange({ ...value, ...patch });
  const s = value;
  const tables = useRead<TableRow[]>(setId === null ? null : () => loadOrientTables(setId), [setId], fail);
  useEffect(() => {
    // tables named by the assistant («Литология») → their keys, once the list is read
    if (!tables) return;
    const k = (t: string): string => (t ? (resolveTable(tables, t) ?? t) : t);
    const patch = { domainTable: k(s.domainTable), coreTable: k(s.coreTable) };
    if (patch.domainTable !== s.domainTable || patch.coreTable !== s.coreTable) set(patch);
  }, [tables]);
  const domainTable = s.domainTable || table || "";
  const wantDomainFields = s.enabled && s.source === "domain" && s.domainBy === "field" && Boolean(s.domainTable) && s.domainTable !== table;
  const otherFields = useRead<FieldRow[]>(setId === null || !wantDomainFields ? null : () => loadFields(setId, s.domainTable), [setId, s.domainTable, wantDomainFields], fail);
  const domainFields = wantDomainFields ? otherFields : fields;
  const coreFields = useRead<FieldRow[]>(setId === null || !s.coreTable || s.source !== "core" ? null : () => loadFields(setId, s.coreTable), [setId, s.coreTable, s.source], fail);
  const wantWireframes = s.enabled && (s.source === "mesh" || (s.source === "domain" && s.domainBy === "wireframes"));
  const wireframes = useRead<WireframeRow[]>(wantWireframes ? loadWireframes : null, [wantWireframes], fail);

  const codeField = (domainFields ?? []).find((f) => f.name === s.domainField);
  let loadDomainCodes: (() => Promise<string[]>) | null = null;
  let fillReason = "Выберите поле кода домена";
  if (s.domainBy === "wireframes") {
    const names = (wireframes ?? []).filter((w) => s.domainWireframes.includes(w.id)).map((w) => w.name);
    if (names.length) loadDomainCodes = async () => names;
    else fillReason = "Отметьте каркасы-домены";
  } else if (setId !== null && domainTable && codeField) {
    if (codeField.kind === "text") loadDomainCodes = () => loadCodes(setId, domainTable, codeField.key);
    else fillReason = "Коды числового поля введите вручную";
  }

  return (
    <fieldset className={css.group} data-testid="intercepts-orientation">
      <legend>Ориентировка</legend>
      {optional && (
        <label className={css.check}>
          <input type="checkbox" checked={s.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          Считать истинную мощность (без ориентировки TT пусто)
        </label>
      )}
      {s.enabled && (
        <>
          <div className={own.sources} role="radiogroup" aria-label="Источник ориентировки">
            {ORIENT_SOURCES.map((src) => (
              <label key={src} className={css.check}>
                <input type="radio" name={radio} checked={s.source === src} onChange={() => set({ source: src })} />
                {SOURCE_TITLES[src]}
              </label>
            ))}
          </div>
          {s.source === "global" && (
            <>
              <NumberRow p={ALPHA} value={s.alpha} error={errors.alpha} onChange={(v) => set({ alpha: v })} />
              <NumberRow p={DIPDIR} value={s.dipdir} error={errors.dipdir} onChange={(v) => set({ dipdir: v })} />
              <PlanePicker onPlane={(alpha, dipdir) => set({ alpha, dipdir })} />
            </>
          )}
          {s.source === "domain" && (
            <>
              <Select label="Код домена" value={s.domainBy} onChange={(v) => set({ domainBy: v === "wireframes" ? "wireframes" : "field" })}>
                <option value="field">поле интервальной таблицы</option>
                <option value="wireframes">каркасы-домены</option>
              </Select>
              {s.domainBy === "field" ? (
                <>
                  <Select label="Таблица кода" value={s.domainTable} onChange={(v) => set({ domainTable: v, domainField: "" })}>
                    <option value="">таблица источника</option>
                    {(tables ?? [])
                      .filter((t) => t.kind !== "event" && t.table !== table)
                      .map((t) => (
                        <option key={t.table} value={t.table}>
                          {t.name}
                        </option>
                      ))}
                  </Select>
                  <Select label="Поле кода" value={s.domainField} onChange={(v) => set({ domainField: v })}>
                    <option value="">выберите поле</option>
                    {fieldOptions(domainFields, false)}
                  </Select>
                </>
              ) : (
                <Wireframes list={wireframes} ids={s.domainWireframes} label="Каркасы-домены" onChange={(ids) => set({ domainWireframes: ids })} />
              )}
              {errors.domain && <p className={own.errText}>{errors.domain}</p>}
              <DomainGrid rows={s.attitudes} errors={errors} onChange={(rows) => set({ attitudes: rows })} loadCodes={loadDomainCodes} fillReason={fillReason} />
            </>
          )}
          {s.source === "mesh" && (
            <>
              <Wireframes list={wireframes} ids={s.meshWireframes} label="Каркасы и поверхности" max={16} onChange={(ids) => set({ meshWireframes: ids })} />
              {errors.wireframes && <p className={own.errText}>{errors.wireframes}</p>}
              <NumberRow p={D_MAX} value={s.dMax} error={errors.d_max} onChange={(v) => set({ dMax: v })} />
              <NumberRow p={R_N} value={s.rN} error={errors.r_n} onChange={(v) => set({ rN: v })} />
            </>
          )}
          {s.source === "core" && (
            <>
              <Select label="Таблица замеров" value={s.coreTable} onChange={(v) => set({ coreTable: v, coreAlpha: "", coreBeta: "" })}>
                <option value="">выберите таблицу</option>
                {(tables ?? []).map((t) => (
                  <option key={t.table} value={t.table}>
                    {t.name}
                  </option>
                ))}
              </Select>
              <Select label="Поле α_к (к оси керна)" value={s.coreAlpha} onChange={(v) => set({ coreAlpha: v })}>
                <option value="">выберите поле</option>
                {fieldOptions(coreFields, true)}
              </Select>
              <Select label="Поле β_к (от линии отсчёта)" value={s.coreBeta} onChange={(v) => set({ coreBeta: v })}>
                <option value="">выберите поле</option>
                {fieldOptions(coreFields, true)}
              </Select>
              <Select label="Линия отсчёта" value={s.coreLine} onChange={(v) => set({ coreLine: v === "toh" ? "toh" : "boh" })}>
                <option value="boh">низ керна (BOH)</option>
                <option value="toh">верх керна (TOH)</option>
              </Select>
              {errors.core && <p className={own.errText}>{errors.core}</p>}
              <NumberRow p={D_CORE} value={s.dCore} error={errors.d_core} onChange={(v) => set({ dCore: v })} />
            </>
          )}
          {s.source !== "global" && (
            <>
              <label className={css.check}>
                <input type="checkbox" checked={s.fallback} onChange={(e) => set({ fallback: e.target.checked })} />
                Если не найдена — глобальная α, Aп
              </label>
              {s.fallback && (
                <>
                  <NumberRow p={ALPHA} label="Глобальная α" value={s.fallbackAlpha} error={errors.fallback_alpha} onChange={(v) => set({ fallbackAlpha: v })} />
                  <NumberRow p={DIPDIR} label="Глобальный Aп" value={s.fallbackDipdir} error={errors.fallback_dipdir} onChange={(v) => set({ fallbackDipdir: v })} />
                </>
              )}
            </>
          )}
        </>
      )}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
