/**
 * The form of one series of a swath plot (ТЗ 7.30 «Ряды», «Средние по блокам», «Средние по пробам», «Какие
 * пробы»; перенос `BlockFields`, `SampleFields` BF): a block model — its field, the weight (volume, tonnage with
 * the density field, none), the selection by a category field (a domain); samples — the table and field of a
 * set, the weight, values < 0, the top cut, all samples in the cuboid or only inside the blocks of a model series.
 */
import { useEffect } from "react";
import { fieldLabel, useStatsFields } from "../../charts/core/Pickers";
import { NumberInput, WEIGHTS } from "../../charts/SampleFilterForm";
import type { Negatives, WeightMode } from "../charts/settings";
import type { SourceModel, SwathSources } from "./api";
import type { BlockSeries, BlockWeight, SampleSeries, SwathSeries } from "./model";
import css from "./Swath.module.css";

function BlockForm({ s, model, onChange }: { s: BlockSeries; model: SourceModel; onChange: (next: SwathSeries) => void }) {
  const numbers = model.numbers;
  const category = s.filter ? model.categories.find((c) => c.name === s.filter!.field) : undefined;
  return (
    <>
      <label>
        Поле
        <select value={s.field ?? ""} aria-label="Поле модели" aria-invalid={!s.field || undefined} onChange={(e) => onChange({ ...s, field: e.target.value || null })}>
          {!numbers.some((a) => a.name === s.field) && <option value={s.field ?? ""}>{s.field ? `${s.field} (нет в модели)` : "— выберите —"}</option>}
          {numbers.map((a) => (
            <option key={a.name} value={a.name}>
              {a.name}
              {a.unit ? `, ${a.unit}` : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        Среднее взвешено
        <select value={s.weight} aria-label="Вес блоков" onChange={(e) => onChange({ ...s, weight: e.target.value as BlockWeight, density: s.density ?? model.density })}>
          <option value="volume">по объёму блока</option>
          <option value="tonnage">по тоннажу (объём × плотность)</option>
          <option value="none">без весов (по числу блоков)</option>
        </select>
      </label>
      {s.weight === "tonnage" && (
        <label>
          Плотность
          <select value={s.density ?? ""} aria-label="Поле плотности" aria-invalid={!s.density || undefined} onChange={(e) => onChange({ ...s, density: e.target.value || null })}>
            <option value="">— выберите —</option>
            {numbers.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {model.categories.length > 0 && (
        <label>
          Отбор блоков
          <select value={s.filter?.field ?? ""} aria-label="Поле отбора блоков" onChange={(e) => onChange({ ...s, filter: e.target.value ? { field: e.target.value, labels: [] } : null })}>
            <option value="">все блоки</option>
            {model.categories.map((c) => (
              <option key={c.name} value={c.name}>
                по полю {c.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {s.filter && category && (
        <div className={css.labels} role="group" aria-label={`Значения поля ${category.name}`}>
          {category.labels.map((l) => (
            <label key={l} className={css.check}>
              <input
                type="checkbox"
                checked={s.filter!.labels.includes(l)}
                onChange={(e) => onChange({ ...s, filter: { field: category.name, labels: e.target.checked ? [...s.filter!.labels, l] : s.filter!.labels.filter((x) => x !== l) } })}
              />
              {l}
            </label>
          ))}
          {category.more && <span className={css.muted}>показаны первые {category.labels.length} значений</span>}
          {!s.filter.labels.length && <span className={css.muted}>не выбрано — все блоки</span>}
        </div>
      )}
    </>
  );
}

function SampleForm({ s, all, onChange }: { s: SampleSeries; all: readonly SwathSeries[]; onChange: (next: SwathSeries) => void }) {
  const { fields, error } = useStatsFields(s.set_id, s.table);
  const numeric = fields?.numeric ?? [];
  const values = numeric.filter((f) => f.kind !== "length");
  const models = all.map((x, i) => ({ x, i })).filter(({ x }) => x.kind === "blocks");
  const within = s.within === "blocks" ? String(s.within_series ?? models[0]?.i ?? "") : "box";
  const withField = s.weight === "field" || s.weight === "length_field";
  // a new series (no field yet): the first component of the table
  useEffect(() => {
    if (!fields || s.field || !values.length) return;
    onChange({ ...s, field: (values.find((f) => f.kind === "component") ?? values[0]).name });
  }, [fields, s.field]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      {fields && fields.tables.length > 1 && (
        <label>
          Таблица проб
          <select value={s.table ?? fields.table ?? ""} aria-label="Таблица проб" onChange={(e) => onChange({ ...s, table: e.target.value || null, field: null })}>
            {fields.tables.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name} ({t.rows.toLocaleString("ru-RU")})
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Поле
        <select value={s.field ?? ""} aria-label="Поле проб" aria-invalid={!s.field || undefined} onChange={(e) => onChange({ ...s, field: e.target.value || null })}>
          {!values.some((f) => f.name === s.field) && <option value={s.field ?? ""}>{fields ? (s.field ? `${s.field} (нет в таблице)` : "— выберите —") : error ? "поля не прочитаны" : "загрузка…"}</option>}
          {values.map((f) => (
            <option key={f.name} value={f.name}>
              {fieldLabel(f)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Среднее взвешено
        <select value={s.weight} aria-label="Вес проб" onChange={(e) => onChange({ ...s, weight: e.target.value as WeightMode })}>
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
          <select value={s.weight_field ?? ""} aria-label="Поле веса" aria-invalid={!s.weight_field || undefined} onChange={(e) => onChange({ ...s, weight_field: e.target.value || null })}>
            <option value="">— выберите —</option>
            {values
              .filter((f) => f.name !== s.field)
              .map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                </option>
              ))}
          </select>
        </label>
      )}
      <label>
        Какие пробы
        <select
          value={within}
          aria-label="Отбор проб"
          onChange={(e) => onChange(e.target.value === "box" ? { ...s, within: "box", within_series: null } : { ...s, within: "blocks", within_series: Number(e.target.value) })}
        >
          <option value="box">все в кубоиде полос</option>
          {models.map(({ i }) => (
            <option key={i} value={String(i)}>
              внутри блоков ряда {i + 1}
            </option>
          ))}
        </select>
      </label>
      <div className={css.pair}>
        <label>
          Значения &lt; 0
          <select value={s.negatives} aria-label="Отрицательные значения" onChange={(e) => onChange({ ...s, negatives: e.target.value as Negatives })}>
            <option value="keep">как есть</option>
            <option value="exclude">исключить</option>
            <option value="half_abs">½ модуля</option>
          </select>
        </label>
        <label>
          Урезка сверху
          <NumberInput label="Урезка ураганных значений" value={s.top_cut} onChange={(v) => onChange({ ...s, top_cut: v })} />
        </label>
      </div>
    </>
  );
}

/** The source of a series («Данные») and its own fields; a source that is gone says so and offers another. */
export function SeriesForm({ s, index, all, sources, onChange }: { s: SwathSeries; index: number; all: readonly SwathSeries[]; sources: SwathSources | null; onChange: (next: SwathSeries) => void }) {
  const models = sources?.models ?? [];
  const sets = sources?.sets ?? [];
  const sourceId = s.kind === "blocks" ? s.model_id : s.set_id;
  const value = sourceId === null ? "" : `${s.kind === "blocks" ? "b" : "s"}:${sourceId}`;
  const model = s.kind === "blocks" ? models.find((m) => m.id === s.model_id) : undefined;
  const known = s.kind === "blocks" ? !!model : sets.some((x) => x.id === s.set_id) || (!sources && s.set_id !== null);
  const pick = (v: string) => {
    const id = Number(v.slice(2));
    const look = { color: s.color, line: s.line, bars: s.bars };
    if (v.startsWith("b:")) {
      const m = models.find((x) => x.id === id);
      if (!m) return;
      const field = m.grades.find((g) => m.numbers.some((n) => n.name === g)) ?? m.numbers.find((n) => n.name !== m.density)?.name ?? null;
      onChange({ kind: "blocks", model_id: id, field, weight: "volume", density: m.density, filter: null, ...look });
    } else onChange({ kind: "samples", set_id: id, table: null, field: null, weight: "length", weight_field: null, negatives: "keep", top_cut: null, within: "box", within_series: null, ...look });
  };
  return (
    <>
      <label>
        Данные
        <select value={known ? value : ""} aria-label={`Данные ряда ${index + 1}`} onChange={(e) => pick(e.target.value)}>
          {!known && <option value="">{sourceId === null ? "— источник удалён, выберите другой —" : sources ? "— источник не найден —" : "загрузка…"}</option>}
          {models.length > 0 && (
            <optgroup label="Блочные модели">
              {models.map((m) => (
                <option key={m.id} value={`b:${m.id}`}>
                  {m.name}
                </option>
              ))}
            </optgroup>
          )}
          {sets.length > 0 && (
            <optgroup label="Скважины (пробы)">
              {sets.map((x) => (
                <option key={x.id} value={`s:${x.id}`}>
                  {x.name}
                </option>
              ))}
            </optgroup>
          )}
          {!sources && known && <option value={value}>{s.kind === "samples" ? `Набор ${s.set_id}` : `Модель ${s.model_id}`}</option>}
        </select>
      </label>
      {s.kind === "blocks" && model && <BlockForm s={s} model={model} onChange={onChange} />}
      {s.kind === "samples" && s.set_id !== null && <SampleForm s={s} all={all} onChange={onChange} />}
    </>
  );
}
