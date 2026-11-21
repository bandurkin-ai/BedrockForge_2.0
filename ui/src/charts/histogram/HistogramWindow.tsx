/**
 * The window «Гистограмма» (ТЗ 7.25; ADR 0019 §11; перенос `HistogramWindow` BF): the settings — table and field,
 * the selection of values, bins, the view — the chart, the statistics with and without weights side by side, the
 * counters, the export. An edit computes again 300 ms later; an overtaken answer is dropped; a failure of the
 * core is shown over the dimmed previous chart. The title follows the name of the settings.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import { defaultField, loadFields, type HistogramResult, type StatsFields } from "../../modules/charts/api";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_HISTOGRAM_VIEW, type HistogramSettings, type HistogramView, type HistogramY } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { fieldLabel, FieldSelect, TableSelect } from "../core/Pickers";
import { NumberInput, SampleFilterForm } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { HistogramChart } from "./HistogramChart";
import { binModeOf, countsNotes, histogramCalculator, histogramCsv, histogramMarks, histogramRequest, statsTable, withBinMode, Y_TITLES, type BinMode } from "./model";

function StatsTable({ result }: { result: HistogramResult }) {
  const rows = statsTable(result.stats, result.stats_unweighted);
  const half = Math.ceil(rows.length / 2);
  const plain = result.stats_unweighted !== null;
  const block = (part: typeof rows) => (
    <table className={css.stats}>
      <thead>
        <tr>
          <th />
          <th>{result.weighted ? "С весом" : "Значение"}</th>
          {plain && <th>Без веса</th>}
        </tr>
      </thead>
      <tbody>
        {part.map(([title, main, other]) => (
          <tr key={title}>
            <th>{title}</th>
            <td>{main}</td>
            {plain && <td>{other}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div className={css.tables} data-testid="histogram-stats">
      {block(rows.slice(0, half))}
      {block(rows.slice(half))}
    </div>
  );
}

const MARKS: [keyof HistogramView["marks"], string][] = [
  ["mean", "Среднее"],
  ["median", "Медиана"],
  ["p90", "P90"],
  ["p975", "P97,5"],
  ["cuts", "Отсечки"],
];

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className={css.seg} role="group" aria-label={label}>
      {options.map(([v, t]) => (
        <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {t}
        </button>
      ))}
    </div>
  );
}

export function HistogramWindow(props: ChartWindowProps<"histogram">) {
  const { setId, setName, settings } = props;
  const [s, setS] = useState<HistogramSettings>(settings);
  const [mode, setMode] = useState<BinMode>(binModeOf(settings));
  const [fields, setFields] = useState<StatsFields | null>(null);
  const [result, setResult] = useState<HistogramResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<HistogramSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const setView = (patch: Partial<HistogramView>) => setS((prev) => ({ ...prev, view: { ...prev.view, ...patch } }));

  // fields of the table; a field or weight field not in it is replaced by the default
  useEffect(() => {
    let alive = true;
    loadFields(setId, s.table)
      .then((f) => {
        if (!alive) return;
        setFields(f);
        setS((prev) => {
          const has = (name: string | null) => name !== null && f.numeric.some((x) => x.name === name);
          return { ...prev, table: prev.table ?? f.table, field: has(prev.field) ? prev.field : defaultField(f), weight_field: has(prev.weight_field) ? prev.weight_field : null };
        });
      })
      .catch((e: unknown) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [setId, s.table]);

  const saved = useSavedChart("histogram", props, s, setS);

  const calc = useMemo(
    () =>
      histogramCalculator(
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
  const request = useMemo(() => histogramRequest(s), [s]);
  useEffect(() => {
    if (fields && request) calc.request(request);
  }, [calc, fields, request]);

  const numeric = fields?.numeric ?? [];
  const field = numeric.find((f) => f.name === s.field);
  const xLabel = field ? fieldLabel(field) : (s.field ?? "");
  const fileName = `Гистограмма ${setName} ${s.field ?? ""}`.trim();

  const settingsForm = (
    <>
      <fieldset>
        <legend>Данные</legend>
        <TableSelect value={s.table} tables={fields?.tables ?? []} onChange={(table) => set({ table })} />
        <FieldSelect value={s.field} fields={numeric} onChange={(field) => set({ field })} />
      </fieldset>
      <SampleFilterForm value={s} fields={numeric} onChange={set} />
      <fieldset>
        <legend>Бины</legend>
        <Seg label="Шкала" value={s.log ? "log" : "linear"} options={[["linear", "Линейная"], ["log", "Логарифм."]]} onChange={(v) => set({ log: v === "log", bin_width: null })} />
        <Seg
          label="Бины"
          value={mode}
          options={[["auto", "Авто"], ["width", "Ширина"], ["count", "Число"]]}
          onChange={(m) => {
            setMode(m);
            setS((prev) => withBinMode(prev, m, m === "width" ? (result?.bins.width ?? null) : null));
          }}
        />
        {mode === "width" && (
          <label>
            {s.log ? "Ширина, log10" : "Ширина бина"}
            <NumberInput label="Ширина бина" value={s.bin_width} placeholder="авто" onChange={(v) => set({ bin_width: v !== null && v > 0 ? v : null })} />
          </label>
        )}
        {mode === "count" && (
          <label>
            Число бинов (до 1000)
            <NumberInput label="Число бинов" integer value={s.bins} placeholder="30" onChange={(v) => set({ bins: v !== null && v >= 1 ? v : null })} />
          </label>
        )}
        <div className={css.pair}>
          <label>
            От
            <NumberInput label="Начало диапазона" value={s.start} placeholder="авто" onChange={(start) => set({ start })} />
          </label>
          <label>
            До
            <NumberInput label="Конец диапазона" value={s.end} placeholder="авто" onChange={(end) => set({ end })} />
          </label>
        </div>
        {s.log && <p className={css.muted}>Ширина в log10: 0,1 — десять бинов на порядок.</p>}
      </fieldset>
      <fieldset>
        <legend>Вид</legend>
        <label>
          Ось Y
          <select value={s.view.y} aria-label="Ось Y" onChange={(e) => setView({ y: e.target.value as HistogramY })}>
            {(Object.keys(Y_TITLES) as HistogramY[]).map((k) => (
              <option key={k} value={k}>
                {Y_TITLES[k]}
              </option>
            ))}
          </select>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.cumulative} onChange={(e) => setView({ cumulative: e.target.checked })} /> Накопленная кривая
        </label>
        <div className={css.checks}>
          {MARKS.map(([k, t]) => (
            <label key={k} className={css.check}>
              <input type="checkbox" checked={s.view.marks[k]} onChange={(e) => setView({ marks: { ...s.view.marks, [k]: e.target.checked } })} /> {t}
            </label>
          ))}
        </div>
        <label className={css.check}>
          <input type="color" value={s.view.color} aria-label="Цвет столбцов" onChange={(e) => setView({ color: e.target.value })} /> Цвет столбцов
        </label>
      </fieldset>
    </>
  );

  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result} csvLabel="CSV бинов" csv={() => (result ? histogramCsv(result) : null)} onReset={() => setView({ ...DEFAULT_HISTOGRAM_VIEW, marks: { ...DEFAULT_HISTOGRAM_VIEW.marks } })} />;
  const empty = fields === null ? "Расчёт…" : !numeric.length ? "В таблице нет числовых полей." : !request ? "Выберите поле веса." : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error}
      empty={empty}
      note={saved.note}
      chart={result && <HistogramChart ref={svgRef} result={result} y={s.view.y} cumulative={s.view.cumulative} marks={histogramMarks(result, s)} color={s.view.color} xLabel={xLabel} />}
      footer={
        result && (
          <>
            <p className={css.muted}>{countsNotes(result.counts, s, result).join(" · ")}</p>
            <StatsTable result={result} />
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
