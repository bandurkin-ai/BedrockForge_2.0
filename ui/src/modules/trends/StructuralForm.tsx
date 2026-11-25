/**
 * The source of a structural trend in the window «Тренд» (ТЗ 7.35 Д-15, 8.9.2; ADR 0026 решение 27): «Замеры» — a set
 * of holes, a table, the fields α and Aп, the depth (empty — the middle of FROM–TO) — or «Опорный каркас» (and the
 * radius of averaging of its normal r_n); R_тр — the radius of influence of the measurements. The lists are read from
 * the core (the newest read only is applied); the fields are the numeric ones of the table. A source that is gone is
 * said so, with what to do. «Диски в 3D» of a stored trend — `trend.show`.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { loadFields, loadSets, loadWireframes, type FieldRow, type SetRow, type TableRow, type WireframeRow } from "../composites/api";
import { loadOrientTables } from "../intercepts/api";
import type { Trend } from "./api";
import { DISCS_NOTE, RADIUS_RANGE, sourceText, type CheckedSource, type SourceType, type StructuralValues } from "./structural";

/** The newest read of a list; null while it is read or when it is not needed. */
function useList<T>(load: (() => Promise<T>) | null, deps: unknown[], fail: (e: unknown) => void): T | null {
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

function Pick({ label, value, onChange, disabled, testId, children }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean; testId: string; children: ReactNode }) {
  return (
    <label className={css.field}>
      <span>{label}</span>
      <select className={css.input} value={value} disabled={disabled} data-testid={testId} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </label>
  );
}

const fieldOptions = (fields: readonly FieldRow[] | null, keep: string) => {
  const list = (fields ?? []).filter((f) => f.kind === "number");
  const known = list.some((f) => f.name === keep);
  // the field of a stored trend is kept in the list; «нет в таблице» only once the fields are read
  return [
    ...(keep && !known ? [<option key="keep" value={keep}>{fields ? `${keep} (нет в таблице)` : keep}</option>] : []),
    ...list.map((f) => (
      <option key={f.key} value={f.name}>
        {f.name}
      </option>
    )),
  ];
};

export interface StructuralFormProps {
  winId: string;
  value: StructuralValues;
  onChange: (patch: Partial<StructuralValues>) => void;
  /** the check of the source (the first thing wrong is marked) */
  checked: CheckedSource;
  /** the stored trend (its source names, a source that is gone, the measurements counted) */
  stored: Trend | null;
  /** «Диски в 3D»: shown or not, and the switch (a stored trend only) */
  discs: { shown: boolean; onToggle: (on: boolean) => void; reason: string | null; /** «Показано N из M» */ info: string | null };
}

export function StructuralForm({ winId, value: v, onChange, checked, stored, discs }: StructuralFormProps) {
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const measurements = v.type === "measurements";
  const sets = useList<SetRow[]>(measurements ? loadSets : null, [measurements], fail);
  const tables = useList<TableRow[]>(measurements && v.setId !== null ? () => loadOrientTables(v.setId!) : null, [measurements, v.setId], fail);
  const fields = useList<FieldRow[]>(measurements && v.setId !== null && v.table ? () => loadFields(v.setId!, v.table) : null, [measurements, v.setId, v.table], fail);
  const wireframes = useList<WireframeRow[]>(!measurements ? loadWireframes : null, [measurements], fail);
  const bad = checked.ok ? null : checked.param;
  const mark = (p: string) => bad === p || undefined;
  const type = (t: SourceType) => onChange({ type: t });
  const lost = stored?.sourceLost === true;
  return (
    <>
      <fieldset className={css.group} data-testid={`trend-source-${winId}`}>
        <legend>Источник осей</legend>
        <div className={css.row} role="radiogroup" aria-label="Источник структурного тренда">
          <label className={css.check}>
            <input type="radio" name={`trend-src-${winId}`} checked={measurements} data-testid={`trend-src-measurements-${winId}`} onChange={() => type("measurements")} />
            Замеры «точка, α, Aп»
          </label>
          <label className={css.check}>
            <input type="radio" name={`trend-src-${winId}`} checked={!measurements} data-testid={`trend-src-mesh-${winId}`} onChange={() => type("mesh")} />
            Опорный каркас
          </label>
        </div>
        {measurements ? (
          <>
            <div className={css.row}>
              <Pick label="Набор скважин" value={v.setId === null ? "" : String(v.setId)} testId={`trend-set-${winId}`} onChange={(x) => onChange({ setId: x ? Number(x) : null, table: "", alpha: "", dipdir: "", depth: "" })}>
                <option value="">{sets ? (sets.length ? "— выберите —" : "нет наборов скважин") : "…"}</option>
                {sets && v.setId !== null && !sets.some((s) => s.id === v.setId) && <option value={v.setId}>{`№${v.setId} (нет в проекте)`}</option>}
                {(sets ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Pick>
              <Pick label="Таблица замеров" value={v.table} disabled={v.setId === null} testId={`trend-table-${winId}`} onChange={(x) => onChange({ table: x, alpha: "", dipdir: "", depth: "" })}>
                <option value="">{tables ? "— выберите —" : v.setId === null ? "сначала набор" : "…"}</option>
                {v.table && tables && !tables.some((t) => t.table === v.table) && <option value={v.table}>{`№${v.table} (нет в наборе)`}</option>}
                {(tables ?? []).map((t) => (
                  <option key={t.table} value={t.table}>
                    {t.name}
                  </option>
                ))}
              </Pick>
            </div>
            <div className={css.row}>
              <Pick label="Поле α (угол падения, °)" value={v.alpha} disabled={!v.table} testId={`trend-alpha-${winId}`} onChange={(x) => onChange({ alpha: x })}>
                <option value="">— выберите —</option>
                {fieldOptions(fields, v.alpha)}
              </Pick>
              <Pick label="Поле Aп (азимут падения, °)" value={v.dipdir} disabled={!v.table} testId={`trend-dipdir-${winId}`} onChange={(x) => onChange({ dipdir: x })}>
                <option value="">— выберите —</option>
                {fieldOptions(fields, v.dipdir)}
              </Pick>
              <Pick label="Глубина замера" value={v.depth} disabled={!v.table} testId={`trend-depth-${winId}`} onChange={(x) => onChange({ depth: x })}>
                <option value="">середина интервала FROM–TO</option>
                {fieldOptions(fields, v.depth)}
              </Pick>
            </div>
          </>
        ) : (
          <div className={css.row}>
            <Pick label="Опорный каркас или поверхность" value={v.wireframeId === null ? "" : String(v.wireframeId)} testId={`trend-wireframe-${winId}`} onChange={(x) => onChange({ wireframeId: x ? Number(x) : null })}>
              <option value="">{wireframes ? (wireframes.length ? "— выберите —" : "в проекте нет каркасов") : "…"}</option>
              {wireframes && v.wireframeId !== null && !wireframes.some((w) => w.id === v.wireframeId) && <option value={v.wireframeId}>{`№${v.wireframeId} (нет в проекте)`}</option>}
              {(wireframes ?? []).map((w) => (
                <option key={w.id} value={w.id}>
                  {w.folder ? `${w.folder} › ${w.name}` : w.name}
                </option>
              ))}
            </Pick>
            <label className={css.field} title="Нормаль осредняется по площади треугольников в радиусе r_n; 0 — нормаль ближайшего треугольника">
              <span>Радиус осреднения нормали r_n, м</span>
              <input className={css.input} inputMode="decimal" value={v.rN} aria-invalid={mark("r_n")} data-testid={`trend-rn-${winId}`} onChange={(e) => onChange({ rN: e.target.value })} />
            </label>
          </div>
        )}
        <div className={css.row}>
          <label className={css.field} title={`Замеры дальше R_тр не влияют на оси в точке; ближе — вес 1 / расстояние. ${RADIUS_RANGE.lo}–${RADIUS_RANGE.hi} м`}>
            <span>Радиус влияния R_тр, м</span>
            <input className={css.input} inputMode="decimal" value={v.radius} aria-invalid={mark("radius")} data-testid={`trend-radius-${winId}`} onChange={(e) => onChange({ radius: e.target.value })} />
          </label>
        </div>
        {stored && (
          <p className={lost ? css.error : css.note} data-testid={`trend-source-text-${winId}`}>
            {sourceText(stored)}
            {stored.measurements !== null ? ` · замеров в расчёте: ${stored.measurements.toLocaleString("ru-RU")}` : ""}
            {lost ? ". Выберите другой источник и сохраните тренд или удалите его." : ""}
          </p>
        )}
        {error && <p className={css.error}>{error}</p>}
      </fieldset>
      <fieldset className={css.group}>
        <legend>Показ в 3D</legend>
        <div className={css.row}>
          <label className={css.check} title={discs.reason ?? DISCS_NOTE}>
            <input type="checkbox" checked={discs.shown} disabled={!!discs.reason} data-testid={`trend-discs-${winId}`} onChange={(e) => discs.onToggle(e.target.checked)} />
            Диски в 3D
          </label>
          {discs.reason && <span className={css.note}>{discs.reason}</span>}
          {!discs.reason && discs.info && <span className={css.note} data-testid={`trend-discs-info-${winId}`}>{discs.info}</span>}
        </div>
      </fieldset>
    </>
  );
}
