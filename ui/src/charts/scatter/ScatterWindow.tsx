/**
 * The window «Рассеяние» (ТЗ 8.3; ADR 0019 §11): fields X and Y of a table (Y — of another table too, rows by the
 * sample key), the colour by a numeric or text field, log axes, the line of a fit (МНК, RMA, weighted) and 1:1, r of
 * Pearson and Spearman and all three fits in the table, the weight and values < 0 and zeros for both fields; more
 * than 200 000 pairs — the density. A frame or a lasso selects the points in the coordinates of the axes (Esc —
 * clears); the link of the selection with 3D and the table — 13.33. An edit computes again 300 ms later.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import { defaultField, type StatsFields } from "../../modules/charts/api";
import type { ScatterResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_SCATTER_VIEW, type Negatives, type Regression, type ScatterSettings, type ScatterView, type WeightMode } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { selectInside, type AxisPolygon, type SelectTool } from "../core/lasso";
import { fieldLabel, FieldSelect, TableSelect, useStatsFields } from "../core/Pickers";
import { inlineRasters } from "../core/PointsCanvas";
import { WEIGHTS } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { ScatterChart } from "./ScatterChart";
import { axisKinds, FIT_TITLES, scatterCalculator, scatterCsv, scatterNotes, scatterRequest, scatterTable } from "./model";

const keep = (f: StatsFields | null, name: string | null) => (f && name !== null && f.numeric.some((x) => x.name === name) ? name : null);

/** The second numeric field for Y: the next component, else any other but the length. */
export function secondField(f: Pick<StatsFields, "numeric">, x: string | null): string | null {
  const other = f.numeric.filter((v) => v.name !== x && v.kind !== "length");
  return (other.find((v) => v.kind === "component") ?? other[0])?.name ?? null;
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function ScatterWindow(props: ChartWindowProps<"scatter">) {
  const { setId, setName } = props;
  const [s, setS] = useState<ScatterSettings>(props.settings);
  const [result, setResult] = useState<ScatterResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tool, setTool] = useState<SelectTool | null>("lasso");
  const [selection, setSelection] = useState<AxisPolygon | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<ScatterSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const setView = (patch: Partial<ScatterView>) => setS((prev) => ({ ...prev, view: { ...prev.view, ...patch } }));
  const saved = useSavedChart("scatter", props, s, setS);

  const x = useStatsFields(setId, s.table, true);
  const y = useStatsFields(s.y_table ? setId : null, s.y_table);
  const yFields = s.y_table ? y.fields : x.fields;
  useEffect(() => {
    const f = x.fields;
    if (f) setS((prev) => {
      const fx = keep(f, prev.x) ?? defaultField(f);
      return { ...prev, table: prev.table ?? f.table, x: fx, y: prev.y_table ? prev.y : (keep(f, prev.y) ?? secondField(f, fx)), weight_field: keep(f, prev.weight_field), color: prev.color && (f.numeric.some((v) => v.name === prev.color) || f.text.some((v) => v.name === prev.color)) ? prev.color : null };
    });
  }, [x.fields]);
  useEffect(() => {
    const f = y.fields;
    if (f && s.y_table) setS((prev) => ({ ...prev, y: keep(f, prev.y) ?? defaultField(f) }));
  }, [y.fields]); // eslint-disable-line react-hooks/exhaustive-deps

  const calc = useMemo(() => scatterCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => scatterRequest(s), [s]);
  useEffect(() => {
    if (x.fields && (!s.y_table || y.fields) && request) calc.request(request);
  }, [calc, x.fields, y.fields, s.y_table, request]);
  // new data or axes: the old polygon means other points
  useEffect(() => setSelection(null), [result, s.log_x, s.log_y]);

  const { kx, ky } = axisKinds(s);
  const selected = useMemo(() => (selection && result?.mode === "points" ? selectInside(result.x ?? [], result.y ?? [], selection, kx, ky) : []), [selection, result, kx, ky]);
  const onSelect = useCallback((poly: AxisPolygon | null) => setSelection(poly), []);

  const label = (f: StatsFields | null, name: string | null) => {
    const info = f?.numeric.find((v) => v.name === name);
    return info ? fieldLabel(info) : (name ?? "");
  };
  const titles = { x: label(x.fields, s.x), y: label(yFields, s.y) };
  const fileName = `Рассеяние ${setName} ${s.x ?? ""} ${s.y ?? ""}`.trim();
  const tables = x.fields?.tables ?? [];
  const colorFields = [...(x.fields?.numeric.filter((f) => f.kind !== "length").map((f) => f.name) ?? []), ...(x.fields?.text.map((f) => f.name) ?? [])];

  const settingsForm = (
    <>
      <fieldset>
        <legend>Данные</legend>
        <TableSelect value={s.table} tables={tables} onChange={(table) => set({ table })} />
        <FieldSelect title="Поле X" value={s.x} fields={x.fields?.numeric ?? []} onChange={(v) => set({ x: v })} />
        <label>
          Таблица Y
          <select value={s.y_table ?? ""} aria-label="Таблица Y" title="Другая таблица набора: строки сводятся по ключу пробы" onChange={(e) => set({ y_table: e.target.value || null, y: null })}>
            <option value="">та же</option>
            {tables.filter((t) => t.key !== (s.table ?? x.fields?.table)).map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <FieldSelect title="Поле Y" value={s.y} fields={yFields?.numeric ?? []} onChange={(v) => set({ y: v })} />
        <label>
          Цвет по полю
          <select value={s.color ?? ""} aria-label="Цвет по полю" onChange={(e) => set({ color: e.target.value || null })}>
            <option value="">нет</option>
            {colorFields.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
      </fieldset>
      <fieldset>
        <legend>Регрессия и оси</legend>
        <label>
          Линия
          <select value={s.regression} aria-label="Линия регрессии" onChange={(e) => set({ regression: e.target.value as Regression })}>
            {(Object.keys(FIT_TITLES) as Exclude<Regression, "none">[]).map((k) => (
              <option key={k} value={k}>
                {FIT_TITLES[k]}
              </option>
            ))}
            <option value="none">нет</option>
          </select>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.log_x} onChange={(e) => set({ log_x: e.target.checked })} /> Логарифмическая ось X
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.log_y} onChange={(e) => set({ log_y: e.target.checked })} /> Логарифмическая ось Y
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.diagonal} onChange={(e) => setView({ diagonal: e.target.checked })} /> Линия 1:1
        </label>
        <label className={css.check}>
          <input type="color" value={s.view.color} aria-label="Цвет точек" onChange={(e) => setView({ color: e.target.value })} /> Цвет точек
        </label>
      </fieldset>
      <fieldset>
        <legend>Отбор значений (X и Y)</legend>
        <label>
          Вес
          <select value={s.weight} aria-label="Вес" onChange={(e) => set({ weight: e.target.value as WeightMode })}>
            {WEIGHTS.map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {(s.weight === "field" || s.weight === "length_field") && <FieldSelect title="Поле веса" value={s.weight_field} fields={x.fields?.numeric.filter((f) => f.kind !== "length") ?? []} empty="— выберите —" onChange={(v) => set({ weight_field: v })} />}
        <label>
          Значения &lt; 0
          <select value={s.negatives} aria-label="Отрицательные значения" onChange={(e) => set({ negatives: e.target.value as Negatives })}>
            <option value="keep">оставить как есть</option>
            <option value="half_abs">половина предела (|x| / 2)</option>
            <option value="exclude">исключить</option>
          </select>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.exclude_zero} onChange={(e) => set({ exclude_zero: e.target.checked })} /> Исключить нули
        </label>
      </fieldset>
    </>
  );

  const selectTools = (
    <span className={css.seg} role="group" aria-label="Выделение">
      {([["rect", "Рамка"], ["lasso", "Лассо"]] as const).map(([k, t]) => (
        <button key={k} type="button" aria-pressed={tool === k} title={`${t}: выделить пробы на графике (Esc — снять)`} onClick={() => setTool(tool === k ? null : k)}>
          {t}
        </button>
      ))}
    </span>
  );
  const toolbar = (
    <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result} prepare={inlineRasters} csvLabel={result?.mode === "density" ? "CSV ячеек" : "CSV точек"} csv={() => (result ? scatterCsv(result, titles) : null)} onReset={() => setView({ ...DEFAULT_SCATTER_VIEW })}>
      {selectTools}
      {selection && (
        <button type="button" onClick={() => setSelection(null)} title="Снять выделение (Esc)">
          Снять выделение
        </button>
      )}
    </ChartToolbar>
  );
  const fieldsError = x.error ?? (s.y_table ? y.error : null);
  const empty = !x.fields ? "Расчёт…" : (x.fields.numeric.length ?? 0) < 1 ? "В таблице нет числовых полей." : !request ? (s.x && s.y ? "Выберите поле веса." : "Выберите поля X и Y.") : result && !result.n ? "Нет пар для графика." : "Расчёт…";
  const selectionNote = !selection ? null : result?.mode === "density" ? "Выделено многоугольником: пробы выберет ядро (связь с 3D и таблицей)." : `Выделено ${n(selected.length)} ${selected.length === 1 ? "проба" : "проб"}.`;
  return (
    <div style={{ height: "100%" }} onKeyDown={(e) => e.key === "Escape" && selection && (e.stopPropagation(), setSelection(null))}>
      <ChartWindow
        settings={settingsForm}
        toolbar={toolbar}
        busy={busy}
        error={error ?? fieldsError}
        empty={empty}
        note={saved.note}
        chart={result && result.n > 0 && <ScatterChart ref={svgRef} result={result} kx={kx} ky={ky} regression={s.regression} diagonal={s.view.diagonal} color={s.view.color} xLabel={titles.x} yLabel={titles.y} tool={tool} selection={selection} selected={selected} onSelect={onSelect} />}
        footer={
          result && (
            <>
              {selectionNote && (
                <p className={css.muted} data-testid="scatter-selected">
                  {selectionNote}
                </p>
              )}
              <table className={css.stats} data-testid="scatter-stats">
                <tbody>
                  {scatterTable(result).map(([t, v]) => (
                    <tr key={t}>
                      <th>{t}</th>
                      <td>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className={css.muted}>{scatterNotes(result).join(" · ")}</p>
              {result.conventions && <p className={css.muted}>{result.conventions}</p>}
            </>
          )
        }
      />
    </div>
  );
}
