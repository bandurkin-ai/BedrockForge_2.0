/**
 * The form of the selection of values (ТЗ 7.25 «Вес», «Значения < 0», «Отсечки»; ADR 0019 §4), shared by the
 * histogram, Q-Q and the sample series of a swath: weight, its field, values < 0, zeros, bottom and top cuts with
 * «исключить» / «заменить». `NumberInput` keeps what is typed: an empty field gives null, a bad number is marked
 * and never sent.
 */
import { useEffect, useState } from "react";
import { numberText, parseNumber } from "./core/axes";
import type { NumericField } from "../modules/charts/api";
import type { CutMode, Negatives, SampleFilter, WeightMode } from "../modules/charts/settings";
import css from "./core/Chart.module.css";

export function NumberInput({ label, value, onChange, placeholder = "нет", title, integer }: { label: string; value: number | null; onChange: (v: number | null) => void; placeholder?: string; title?: string; integer?: boolean }) {
  const [text, setText] = useState(numberText(value));
  // a value set from outside (reset, another window) replaces the text unless the text already means it
  useEffect(() => {
    if (parseNumber(text) !== value) setText(numberText(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const n = parseNumber(text);
  const bad = text.trim() !== "" && (n === null || (integer && !Number.isInteger(n)));
  return (
    <input
      value={text}
      inputMode="decimal"
      aria-label={label}
      aria-invalid={bad || undefined}
      placeholder={placeholder}
      title={bad ? "Введите число" : title}
      onChange={(e) => {
        setText(e.target.value);
        const v = parseNumber(e.target.value);
        if (e.target.value.trim() === "") onChange(null);
        else if (v !== null && (!integer || Number.isInteger(v))) onChange(v);
      }}
    />
  );
}

export const WEIGHTS: [WeightMode, string][] = [
  ["none", "Без веса"],
  ["length", "Длина интервала"],
  ["field", "Поле…"],
  ["length_field", "Длина × поле (тоннаж)"],
];

function CutInput({ label, value, mode, below, onChange }: { label: string; value: number | null; mode: CutMode; below?: boolean; onChange: (value: number | null, mode: CutMode) => void }) {
  const beyond = below ? "ниже" : "выше";
  return (
    <div className={css.pair}>
      <label>
        {label}
        <NumberInput label={label} value={value} onChange={(v) => onChange(v, mode)} title={mode === "cap" ? `Значения ${beyond} заменяются этим` : `Пробы ${beyond} не учитываются`} />
      </label>
      <label>
        Режим
        <select value={mode} aria-label={`${label}: режим`} onChange={(e) => onChange(value, e.target.value as CutMode)}>
          <option value="exclude">исключить</option>
          <option value="cap">{below ? "поднять до значения" : "урезать до значения"}</option>
        </select>
      </label>
    </div>
  );
}

export function SampleFilterForm({ value, fields, onChange, legend = "Отбор значений" }: { value: SampleFilter; fields: readonly NumericField[]; onChange: (patch: Partial<SampleFilter>) => void; legend?: string }) {
  const weightFields = fields.filter((f) => f.kind !== "length");
  const withField = value.weight === "field" || value.weight === "length_field";
  return (
    <fieldset>
      <legend>{legend}</legend>
      <label>
        Вес
        <select value={value.weight} aria-label="Вес" onChange={(e) => onChange({ weight: e.target.value as WeightMode })}>
          {WEIGHTS.map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </select>
      </label>
      {withField && (
        <label>
          Поле веса
          <select value={value.weight_field ?? ""} aria-label="Поле веса" aria-invalid={!value.weight_field || undefined} onChange={(e) => onChange({ weight_field: e.target.value || null })}>
            <option value="">— выберите —</option>
            {weightFields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Значения &lt; 0
        <select value={value.negatives} aria-label="Отрицательные значения" title="Лаборатории пишут «<0,01» как −0,01" onChange={(e) => onChange({ negatives: e.target.value as Negatives })}>
          <option value="keep">оставить как есть</option>
          <option value="half_abs">половина предела (|x| / 2)</option>
          <option value="exclude">исключить</option>
        </select>
      </label>
      <label className={css.check}>
        <input type="checkbox" checked={value.exclude_zero} onChange={(e) => onChange({ exclude_zero: e.target.checked })} /> Исключить нули
      </label>
      <CutInput label="Отсечка снизу" value={value.bottom_cut} mode={value.bottom_mode} below onChange={(bottom_cut, bottom_mode) => onChange({ bottom_cut, bottom_mode })} />
      <CutInput label="Отсечка сверху" value={value.top_cut} mode={value.top_mode} onChange={(top_cut, top_mode) => onChange({ top_cut, top_mode })} />
    </fieldset>
  );
}
