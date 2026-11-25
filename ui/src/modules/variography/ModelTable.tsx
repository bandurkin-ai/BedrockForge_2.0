/**
 * The table of the working model of the window «Вариография» (ТЗ 8.6.3–8.6.4; ADR 0022 §15): the type of the
 * measure, the nugget C0, up to three structures (type, contribution c, ranges a1–a3, angles ang1–ang3), «общие оси»
 * (the angles of every structure are those of the first), the fixings of the fit (`c0`, `structures.N.c`,
 * `structures.N.a1…a3`), the sill and σ² with the warning above 1,2·σ². A cell takes a value only in its range
 * (8.6.4): out of range it is marked and the model keeps the last good value. Every edit: `markEdit()` then the
 * model (`onChange`) — the charts and the ellipsoid follow on the side of the interface.
 */
import { useEffect, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import type { Transform } from "./api";
import { parseNumber } from "../composites/params";
import { MAX_STRUCTURES, MODEL_TITLES, newStructure, sill, sillWarning, STRUCTURE_TITLES, STRUCTURE_TYPES, type ModelType, type Structure, type StructureType, type VModel } from "./model";
import { checkValue, markEdit } from "./state";
import own from "./Variography.module.css";

const RANGE_MAX = 100_000;

const shown = (v: number) => v.toLocaleString("ru-RU", { maximumSignificantDigits: 6, useGrouping: false });

/** A value of a cell: «» or out of range — an error (Russian); angles by the ranges of 8.6.4. */
export function cellValue(key: "c0" | "c" | "a1" | "a2" | "a3" | "ang1" | "ang2" | "ang3", raw: string): { value: number } | { error: string } {
  if (key === "ang1" || key === "ang2" || key === "ang3") {
    const r = checkValue(key, raw);
    if (!r.ok) return { error: r.error };
    if (typeof r.value !== "number") return { error: "Нужно число" };
    return { value: key === "ang1" && r.value === 360 ? 0 : key === "ang3" && r.value === -180 ? 180 : r.value };
  }
  const v = parseNumber(raw.trim());
  if (v === null) return { error: "Нужно число" };
  if (key === "c0" || key === "c") return v >= 0 ? { value: v + 0 } : { error: `${key === "c0" ? "Самородок" : "Вклад"}: не меньше 0` };
  return v > 0 && v <= RANGE_MAX ? { value: v } : { error: `Дальность ${key}: больше 0 до 100 000 м` };
}

function Cell({ value, label, onValue, check }: { value: number; label: string; onValue: (v: number) => void; check: (raw: string) => { value: number } | { error: string } }) {
  const [text, setText] = useState(shown(value));
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    if (!focus) {
      setText(shown(value));
      setError(null);
    }
  }, [value, focus]);
  return (
    <input
      className={`${css.input} ${own.cell}${error ? ` ${own.bad}` : ""}`}
      aria-label={label}
      aria-invalid={error ? true : undefined}
      title={error ?? undefined}
      value={text}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      onChange={(e) => {
        setText(e.target.value);
        const r = check(e.target.value);
        if ("error" in r) return setError(r.error);
        setError(null);
        if (r.value !== value) onValue(r.value);
      }}
    />
  );
}

function Fix({ path, fix, onFix, label }: { path: string; fix: readonly string[]; onFix: (f: string[]) => void; label: string }) {
  const on = fix.includes(path);
  return (
    <input type="checkbox" className={own.fix} aria-label={`Закрепить: ${label}`} title="Закрепить при подборе" checked={on} onChange={(e) => onFix(e.target.checked ? [...fix, path] : fix.filter((x) => x !== path))} />
  );
}

export interface ModelTableProps {
  model: VModel;
  /** σ² of the data (of Y for normal scores); null — unknown */
  sigma2: number | null;
  /** σ² of Z for normal scores */
  sigma2Z?: number | null;
  transform: Transform;
  fix: readonly string[];
  onChange: (m: VModel) => void;
  onFix: (fix: string[]) => void;
}

const RANGES = ["a1", "a2", "a3"] as const;
const ANGLES = ["ang1", "ang2", "ang3"] as const;

export function ModelTable({ model, sigma2, sigma2Z, transform, fix, onChange, onFix }: ModelTableProps) {
  const edit = (m: VModel) => {
    markEdit();
    onChange(m);
  };
  const setStructure = (i: number, patch: Partial<Structure>) => {
    const angles = Object.fromEntries(Object.entries(patch).filter(([k]) => k.startsWith("ang")));
    edit({ ...model, structures: model.structures.map((s, k) => (k === i ? { ...s, ...patch } : model.common_axes && i === 0 ? { ...s, ...angles } : s)) });
  };
  const remove = (i: number) => {
    // the fixings of the structures after it move up by one
    const moved = fix.flatMap((p) => {
      const m = /^structures\.(\d+)\.(.+)$/.exec(p);
      if (!m) return [p];
      const k = Number(m[1]);
      return k === i ? [] : [k > i ? `structures.${k - 1}.${m[2]}` : p];
    });
    onFix(moved);
    edit({ ...model, structures: model.structures.filter((_, k) => k !== i) });
  };
  const types: ModelType[] = transform === "nscore" ? ["nscore"] : ["semivariogram", "correlogram", "pairwise"];
  const warn = sillWarning(model, sigma2);
  const g = (x: number | null | undefined) => (x === null || x === undefined ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));
  return (
    <fieldset className={css.group} data-testid="variography-model">
      <legend>Модель</legend>
      <label className={css.formRow}>
        <span>Тип</span>
        <select className={css.input} aria-label="Тип вариограммы" value={transform === "nscore" ? "nscore" : model.type} disabled={transform === "nscore"} onChange={(e) => edit({ ...model, type: e.target.value as ModelType })}>
          {types.map((t) => (
            <option key={t} value={t}>
              {MODEL_TITLES[t]}
            </option>
          ))}
        </select>
      </label>
      <label className={css.formRow}>
        <span>Самородок C0</span>
        <span className={own.withFix}>
          <Cell value={model.c0} label="Самородок C0" check={(r) => cellValue("c0", r)} onValue={(v) => edit({ ...model, c0: v })} />
          <Fix path="c0" fix={fix} onFix={onFix} label="самородок" />
        </span>
      </label>
      <div className={css.tableWrap}>
        <table className={`${css.table} ${own.model}`}>
          <thead>
            <tr>
              <th>№</th>
              <th>Тип</th>
              <th>c</th>
              {RANGES.map((k) => (
                <th key={k}>{k}, м</th>
              ))}
              {ANGLES.map((k) => (
                <th key={k}>{k}, °</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {model.structures.map((s, i) => (
              <tr key={i} data-structure={i + 1}>
                <td>{i + 1}</td>
                <td>
                  <select className={css.input} aria-label={`Структура ${i + 1}: тип`} value={s.type} onChange={(e) => setStructure(i, { type: e.target.value as StructureType })}>
                    {STRUCTURE_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {STRUCTURE_TITLES[t]}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <span className={own.withFix}>
                    <Cell value={s.c} label={`Структура ${i + 1}: вклад c`} check={(r) => cellValue("c", r)} onValue={(v) => setStructure(i, { c: v })} />
                    <Fix path={`structures.${i}.c`} fix={fix} onFix={onFix} label={`вклад структуры ${i + 1}`} />
                  </span>
                </td>
                {RANGES.map((k) => (
                  <td key={k}>
                    <span className={own.withFix}>
                      <Cell value={s[k]} label={`Структура ${i + 1}: дальность ${k}`} check={(r) => cellValue(k, r)} onValue={(v) => setStructure(i, { [k]: v })} />
                      <Fix path={`structures.${i}.${k}`} fix={fix} onFix={onFix} label={`${k} структуры ${i + 1}`} />
                    </span>
                  </td>
                ))}
                {ANGLES.map((k) => (
                  <td key={k}>
                    {model.common_axes && i > 0 ? (
                      <span className={own.same} title="Общие оси: углы первой структуры">
                        {shown(model.structures[0][k])}
                      </span>
                    ) : (
                      <Cell value={s[k]} label={`Структура ${i + 1}: ${k}`} check={(r) => cellValue(k, r)} onValue={(v) => setStructure(i, { [k]: v })} />
                    )}
                  </td>
                ))}
                <td>
                  <button type="button" className={css.iconBtn} aria-label={`Удалить структуру ${i + 1}`} title="Удалить структуру" onClick={() => remove(i)}>
                    ×
                  </button>
                </td>
              </tr>
            ))}
            {!model.structures.length && (
              <tr>
                <td colSpan={10} className={own.emptyRow}>
                  Структур нет: добавьте структуру или подберите модель
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={own.row}>
        <button type="button" className={css.btn} disabled={model.structures.length >= MAX_STRUCTURES} onClick={() => edit({ ...model, structures: [...model.structures, newStructure(model, "spherical", { sigma2 })] })}>
          Добавить структуру
        </button>
        <label className={css.check}>
          <input
            type="checkbox"
            checked={model.common_axes}
            onChange={(e) => {
              const first = model.structures[0];
              edit({ ...model, common_axes: e.target.checked, structures: e.target.checked && first ? model.structures.map((s) => ({ ...s, ang1: first.ang1, ang2: first.ang2, ang3: first.ang3 })) : model.structures });
            }}
          />
          Общие оси
        </label>
      </div>
      <p className={css.note} data-testid="variography-sill">
        Порог {g(sill(model))}; σ² = {g(sigma2)}
        {transform === "nscore" && sigma2Z !== undefined ? `; σ²(Z) = ${g(sigma2Z)}` : ""}
      </p>
      {warn && <p className={css.warn}>{warn}</p>}
    </fieldset>
  );
}
