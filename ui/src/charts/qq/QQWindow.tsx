/**
 * The window «Q-Q график» (ТЗ 7.26; ADR 0019 §11; перенос `QQWindow` BF): against a fitted distribution (normal,
 * lognormal — by default, log axes) or two samples — side Y is any set, table, field, weight and list of holes;
 * one scale, the 1:1 line, P10 / P50 / P90, PPCC or D, the table X / Y / отношение, the export (CSV — the points
 * of the chart). An edit computes again 300 ms later; an overtaken answer is dropped.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { errorText } from "../../shell/props";
import { tablesStore } from "../../modules/tables/state";
import { defaultField, type QQResult, type StatsFields } from "../../modules/charts/api";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_QQ_VIEW, type QQSettings, type QQView, type WeightMode } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { fieldLabel, FieldSelect, ListInput, SetSelect, TableSelect, useStatsFields } from "../core/Pickers";
import { NumberInput, SampleFilterForm, WEIGHTS } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { countsNotes } from "../histogram/model";
import { QQChart } from "./QQChart";
import { axisTitles, qqCalculator, qqCsv, qqNotes, qqPoints, qqRequest, qqTable } from "./model";

function QQTable({ result }: { result: QQResult }) {
  const rows = qqTable(result.x, result.y);
  const two = result.y !== null;
  return (
    <table className={css.stats} data-testid="qq-stats">
      <thead>
        <tr>
          <th />
          <th>{two ? "X" : "Выборка"}</th>
          {two && <th>Y</th>}
          {two && <th>Y / X</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map(([title, a, b, k]) => (
          <tr key={title}>
            <th>{title}</th>
            <td>{a}</td>
            {two && <td>{b}</td>}
            {two && <td>{k}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A field kept only while the table has it (null otherwise). */
const keep = (f: StatsFields, name: string | null) => (name !== null && f.numeric.some((x) => x.name === name) ? name : null);

export function QQWindow(props: ChartWindowProps<"qq">) {
  const { setId, setName } = props;
  const [s, setS] = useState<QQSettings>(props.settings);
  const [result, setResult] = useState<QQResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const getSets = () => tablesStore.get().sets;
  const sets = useSyncExternalStore(tablesStore.subscribe, getSets, getSets);
  const set = (patch: Partial<QQSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const setView = (patch: Partial<QQView>) => setS((prev) => ({ ...prev, view: { ...prev.view, ...patch } }));
  const saved = useSavedChart("qq", props, s, setS);

  const two = s.distribution === "sample";
  const x = useStatsFields(setId, s.table);
  const ySet = s.y_set_id ?? setId;
  const y = useStatsFields(two ? ySet : null, s.y_table ?? (ySet === setId ? s.table : null));

  // a field or weight field not in the table is replaced: X — by the default field, Y — by «as X»
  useEffect(() => {
    const f = x.fields;
    if (f) setS((prev) => ({ ...prev, table: prev.table ?? f.table, field: keep(f, prev.field) ?? defaultField(f), weight_field: keep(f, prev.weight_field) }));
  }, [x.fields]);
  useEffect(() => {
    const f = y.fields;
    if (f) setS((prev) => ({ ...prev, y_field: keep(f, prev.y_field), y_weight_field: keep(f, prev.y_weight_field) }));
  }, [y.fields]);

  const calc = useMemo(
    () =>
      qqCalculator(
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
  const request = useMemo(() => qqRequest(s), [s]);
  useEffect(() => {
    if (x.fields && (!two || y.fields) && request) calc.request(request);
  }, [calc, x.fields, y.fields, two, request]);

  const setTitle = (id: number) => sets.find((d) => d.id === id)?.name ?? `Набор ${id}`;
  const name = (f: StatsFields | null, field: string | null, sid: number) => {
    const info = f?.numeric.find((v) => v.name === field);
    return `${info ? fieldLabel(info) : (field ?? "")}${sid !== setId ? ` · ${setTitle(sid)}` : ""}`;
  };
  const xName = name(x.fields, s.field, setId);
  const yName = name(y.fields, s.y_field ?? s.field, ySet);
  const titles = result ? axisTitles(result, xName, yName) : { x: xName, y: yName };
  const points = useMemo(() => (result ? qqPoints(result, s.log) : []), [result, s.log]);
  const fileName = `Q-Q ${setName} ${s.field ?? ""}${two ? ` ${s.y_field ?? s.field ?? ""}` : ""}`.trim();
  const yWeightFields = y.fields?.numeric ?? [];

  const settingsForm = (
    <>
      <fieldset>
        <legend>Сравнение</legend>
        <div className={css.seg} role="group" aria-label="Режим">
          <button type="button" aria-pressed={!two} title="Выборка против подобранного распределения" onClick={() => set({ distribution: "lognormal", log: true })}>
            Распределение
          </button>
          <button type="button" aria-pressed={two} title="Квантили одной выборки против другой" onClick={() => set({ distribution: "sample" })}>
            Две выборки
          </button>
        </div>
        {two ? (
          <label>
            Квантилей
            <NumberInput label="Число квантилей" integer value={s.quantiles} placeholder="99" title="99 — каждый процент; 19 — каждые 5 %" onChange={(v) => set({ quantiles: v ?? 99 })} />
          </label>
        ) : (
          <label>
            Распределение
            <select value={s.distribution} aria-label="Распределение" onChange={(e) => set({ distribution: e.target.value as QQSettings["distribution"], log: e.target.value === "lognormal" })}>
              <option value="lognormal">Логнормальное</option>
              <option value="normal">Нормальное</option>
            </select>
          </label>
        )}
      </fieldset>
      <fieldset>
        <legend>{two ? "Выборка X" : "Выборка"}</legend>
        <TableSelect value={s.table} tables={x.fields?.tables ?? []} onChange={(table) => set({ table })} />
        <FieldSelect value={s.field} fields={x.fields?.numeric ?? []} onChange={(field) => set({ field })} />
        <ListInput title="Скважины" label="Скважины X" value={s.holes} onChange={(holes) => set({ holes })} />
      </fieldset>
      <SampleFilterForm value={s} fields={x.fields?.numeric ?? []} onChange={set} legend={two ? "Отбор значений (X и Y)" : "Отбор значений"} />
      {two && (
        <fieldset>
          <legend>Выборка Y</legend>
          <SetSelect label="Набор Y" value={ySet} sets={sets} onChange={(id) => set({ y_set_id: id === setId ? null : id, y_table: null, y_field: null, y_weight_field: null })} />
          <TableSelect label="Таблица Y" value={y.fields?.table ?? s.y_table} tables={y.fields?.tables ?? []} onChange={(t) => set({ y_table: t, y_field: null, y_weight_field: null })} />
          <FieldSelect label="Поле Y" value={s.y_field} fields={y.fields?.numeric ?? []} empty="как у X" onChange={(f) => set({ y_field: f })} />
          <label>
            Вес
            <select value={s.y_weight ?? ""} aria-label="Вес Y" onChange={(e) => set({ y_weight: (e.target.value || null) as WeightMode | null })}>
              <option value="">как у X</option>
              {WEIGHTS.map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          {(s.y_weight === "field" || s.y_weight === "length_field") && (
            <FieldSelect title="Поле веса" label="Поле веса Y" value={s.y_weight_field} fields={yWeightFields.filter((f) => f.kind !== "length")} empty="— выберите —" onChange={(f) => set({ y_weight_field: f })} />
          )}
          <ListInput title="Скважины" label="Скважины Y" value={s.y_holes} onChange={(y_holes) => set({ y_holes })} />
        </fieldset>
      )}
      <fieldset>
        <legend>Вид</legend>
        <label className={css.check}>
          <input type="checkbox" checked={s.log} onChange={(e) => set({ log: e.target.checked })} /> Логарифмические оси
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.equal} onChange={(e) => setView({ equal: e.target.checked })} /> Одинаковый масштаб осей
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.diagonal} onChange={(e) => setView({ diagonal: e.target.checked })} /> Линия 1:1
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.labels} onChange={(e) => setView({ labels: e.target.checked })} /> Отметки P10, P50, P90
        </label>
        <label className={css.check}>
          <input type="color" value={s.view.color} aria-label="Цвет точек" onChange={(e) => setView({ color: e.target.value })} /> Цвет точек
        </label>
      </fieldset>
    </>
  );

  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result || !points.length} csvLabel="CSV точек" csv={() => (result ? qqCsv(result, s.log, titles) : null)} onReset={() => setView({ ...DEFAULT_QQ_VIEW })} />;
  const fieldsError = x.error ?? (two ? y.error : null);
  const empty = !x.fields ? "Расчёт…" : !x.fields.numeric.length ? "В таблице нет числовых полей." : !request ? "Выберите поле веса." : result && !points.length ? `Нет точек для графика${s.log ? " (на логарифмических осях — только положительные)" : ""}.` : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? fieldsError}
      empty={empty}
      note={saved.note}
      chart={result && points.length > 0 && <QQChart ref={svgRef} points={points} log={s.log} equal={s.view.equal} diagonal={s.view.diagonal} labels={s.view.labels} color={s.view.color} xLabel={titles.x} yLabel={titles.y} />}
      footer={
        result && (
          <>
            <p className={css.muted}>{qqNotes(result).join(" · ")}</p>
            <QQTable result={result} />
            <p className={css.muted}>
              {two ? "X: " : ""}
              {countsNotes(result.x.counts, s).join(" · ")}
            </p>
            {result.y && <p className={css.muted}>Y: {countsNotes(result.y.counts, s).join(" · ")}</p>}
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
