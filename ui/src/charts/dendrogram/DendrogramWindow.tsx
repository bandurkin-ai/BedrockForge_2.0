/**
 * The window «Дендрограмма» (ТЗ 7.27; ADR 0019 §11; перенос `DendrogramWindow` BF): fields (every component by
 * default), Pearson or Spearman, logarithms, weight, missing values pairwise or complete rows, < 0 and zeros as in
 * the histogram; distance 1 − r or 1 − |r|, the link (no Ward), the cut that colours the groups; the tree and the
 * matrix in the order of the leaves; PNG, SVG, CSV of the matrix.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import type { DendrogramResult } from "../../modules/charts/api";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_DENDROGRAM_VIEW, type DendrogramSettings, type DendrogramView, type Negatives } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { fieldLabel, TableSelect, useStatsFields } from "../core/Pickers";
import { NumberInput } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { DendrogramChart } from "./DendrogramChart";
import { chosenFields, dendrogramCalculator, dendrogramNotes, dendrogramRequest, LINKAGES, matrixCsv, MIN_FIELDS } from "./model";

export function DendrogramWindow(props: ChartWindowProps<"dendrogram">) {
  const { setId, setName } = props;
  const [s, setS] = useState<DendrogramSettings>(props.settings);
  const [result, setResult] = useState<DendrogramResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<DendrogramSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const setView = (patch: Partial<DendrogramView>) => setS((prev) => ({ ...prev, view: { ...prev.view, ...patch } }));
  const saved = useSavedChart("dendrogram", props, s, setS);
  const { fields, error: fieldsError } = useStatsFields(setId, s.table);
  const numeric = useMemo(() => (fields?.numeric ?? []).filter((f) => f.kind !== "length"), [fields]);

  // fields of another table: back to every component of this one
  useEffect(() => {
    if (fields) setS((prev) => ({ ...prev, table: prev.table ?? fields.table, fields: prev.fields.every((f) => fields.numeric.some((x) => x.name === f)) ? prev.fields : [] }));
  }, [fields]);

  const calc = useMemo(
    () =>
      dendrogramCalculator(
        (r) => {
          setResult(r);
          setError(null);
        },
        (e) => setError(errorText(e)),
        setBusy,
      ),
    [],
  );
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => (fields ? dendrogramRequest(s, numeric) : null), [s, fields, numeric]);
  useEffect(() => {
    if (request) calc.request(request);
  }, [calc, request]);

  const chosen = chosenFields(s, numeric);
  const toggle = (name: string, on: boolean) => set({ fields: on ? [...chosen, name] : chosen.filter((f) => f !== name) });
  const distanceLabel = `Расстояние ${s.distance === "1-r" ? "1 − r" : "1 − |r|"} (${s.method === "pearson" ? "Пирсон" : "Спирмен"}${s.log ? ", ln" : ""})`;
  const fileName = `Дендрограмма ${setName}`.trim();

  const settingsForm = (
    <>
      <fieldset>
        <legend>Поля ({chosen.length})</legend>
        <TableSelect value={s.table} tables={fields?.tables ?? []} onChange={(table) => set({ table, fields: [] })} />
        <div className={css.fieldList} role="group" aria-label="Поля">
          {numeric.map((f) => (
            <label key={f.name} className={css.check}>
              <input type="checkbox" checked={chosen.includes(f.name)} onChange={(e) => toggle(f.name, e.target.checked)} /> {fieldLabel(f)}
            </label>
          ))}
        </div>
        <div className={css.buttons}>
          <button type="button" onClick={() => set({ fields: [] })} title="Все компоненты таблицы">
            Компоненты
          </button>
          <button type="button" onClick={() => set({ fields: numeric.map((f) => f.name) })} title="Все числовые поля">
            Все
          </button>
        </div>
      </fieldset>
      <fieldset>
        <legend>Корреляция</legend>
        <label>
          Коэффициент
          <select value={s.method} aria-label="Коэффициент корреляции" onChange={(e) => set({ method: e.target.value as DendrogramSettings["method"] })}>
            <option value="pearson">Пирсон</option>
            <option value="spearman">Спирмен (ранговый)</option>
          </select>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.log} onChange={(e) => set({ log: e.target.checked })} /> По логарифмам (ln)
        </label>
        <label>
          Вес
          <select value={s.weight === "none" ? "none" : "length"} aria-label="Вес" onChange={(e) => set({ weight: e.target.value === "none" ? "none" : "length" })}>
            <option value="length">Длина интервала</option>
            <option value="none">Без веса</option>
          </select>
        </label>
        <label>
          Пропуски
          <select value={s.missing} aria-label="Пропуски" onChange={(e) => set({ missing: e.target.value as DendrogramSettings["missing"] })}>
            <option value="pairwise">Попарно (общих строк ≥ 3)</option>
            <option value="complete">Только полные строки</option>
          </select>
        </label>
        <label>
          Значения &lt; 0
          <select value={s.negatives} aria-label="Отрицательные значения" title="Лаборатории пишут «<0,01» как −0,01" onChange={(e) => set({ negatives: e.target.value as Negatives })}>
            <option value="keep">оставить как есть</option>
            <option value="half_abs">половина предела (|x| / 2)</option>
            <option value="exclude">исключить</option>
          </select>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.exclude_zero} onChange={(e) => set({ exclude_zero: e.target.checked })} /> Исключить нули
        </label>
      </fieldset>
      <fieldset>
        <legend>Кластеры</legend>
        <label>
          Расстояние
          <select value={s.distance} aria-label="Расстояние" onChange={(e) => set({ distance: e.target.value as DendrogramSettings["distance"] })}>
            <option value="1-r">1 − r (обратная связь — далеко)</option>
            <option value="1-abs">1 − |r| (только сила связи)</option>
          </select>
        </label>
        <label>
          Связь
          <select value={s.linkage} aria-label="Способ связи" title="Ward не предлагается: ему нужны евклидовы расстояния" onChange={(e) => set({ linkage: e.target.value as DendrogramSettings["linkage"] })}>
            {LINKAGES.map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Порог
          <NumberInput label="Порог отсечения" value={s.cut} title="Поля, связанные ниже порога, — одна группа (цвет)" onChange={(cut) => set({ cut: cut !== null && cut >= 0 ? cut : null })} />
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.matrix} onChange={(e) => setView({ matrix: e.target.checked })} /> Матрица корреляций
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.values} onChange={(e) => setView({ values: e.target.checked })} /> Значения в ячейках
        </label>
      </fieldset>
    </>
  );

  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result} csvLabel="CSV матрицы" csv={() => (result ? matrixCsv(result) : null)} onReset={() => setView({ ...DEFAULT_DENDROGRAM_VIEW })} />;
  const empty = !fields ? "Расчёт…" : chosen.length < MIN_FIELDS ? "Нужно 2 и больше поля: отметьте поля или выберите таблицу с компонентами." : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? fieldsError}
      empty={empty}
      note={saved.note}
      chart={result && request && <DendrogramChart ref={svgRef} result={result} cut={s.cut} matrix={s.view.matrix} values={s.view.values} distanceLabel={distanceLabel} />}
      footer={
        result && (
          <>
            <p className={css.muted}>{dendrogramNotes(result, s.cut).join(" · ")}</p>
            <p className={css.muted}>Наведите на ячейку матрицы — r и число общих проб.{result.conventions ? ` ${result.conventions}` : ""}</p>
          </>
        )
      }
    />
  );
}
