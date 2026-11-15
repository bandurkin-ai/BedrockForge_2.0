import { useEffect, useMemo, useRef, useState } from "react";
import { getHistogram, getNumericFields, type CutMode, type Describe, type HistogramParams, type HistogramResult, type NumericField, type WeightMode } from "../api";
import { formatNumber, parseNumber } from "../stats/chartMath";
import { download, savePng, saveSvg, toCsv } from "./exportChart";
import { HistogramChart, Y_TITLES, barValues, type Mark, type YMode } from "./HistogramChart";

/** Everything the user can set; also what Claude passes to open a histogram. */
export interface HistogramSettings {
  field: string | null;
  weight: WeightMode;
  weightField: string | null;
  log: boolean;
  binMode: "auto" | "width" | "count";
  binWidth: string;
  bins: string;
  start: string;
  end: string;
  y: YMode;
  negatives: HistogramParams["negatives"];
  excludeZero: boolean;
  bottomCut: string;
  bottomMode: CutMode;
  topCut: string;
  topMode: CutMode;
  cumulative: boolean;
  marks: { mean: boolean; median: boolean; p90: boolean; p975: boolean; topCut: boolean };
  color: string;
}

export const DEFAULT_HISTOGRAM: HistogramSettings = {
  field: null,
  // Assay intervals differ in length: length weighting is the usual choice for grades.
  weight: "length",
  weightField: null,
  log: false,
  binMode: "auto",
  binWidth: "",
  bins: "30",
  start: "",
  end: "",
  y: "percent",
  negatives: "keep",
  excludeZero: false,
  bottomCut: "",
  bottomMode: "exclude",
  topCut: "",
  topMode: "cap",
  cumulative: true,
  marks: { mean: true, median: true, p90: false, p975: false, topCut: true },
  color: "#4f9dde",
};

/** The request for the server from the form; null when it cannot be sent yet. */
export function toParams(s: HistogramSettings): HistogramParams | null {
  if (!s.field) return null;
  if ((s.weight === "field" || s.weight === "length_field") && !s.weightField) return null;
  return {
    field: s.field,
    weight: s.weight,
    weight_field: s.weight === "field" || s.weight === "length_field" ? s.weightField : null,
    log: s.log,
    bin_width: s.binMode === "width" ? parseNumber(s.binWidth) : null,
    bins: s.binMode === "count" ? parseNumber(s.bins) : null,
    start: parseNumber(s.start),
    end: parseNumber(s.end),
    negatives: s.negatives,
    exclude_zero: s.excludeZero,
    bottom_cut: parseNumber(s.bottomCut),
    bottom_mode: s.bottomMode,
    top_cut: parseNumber(s.topCut),
    top_mode: s.topMode,
  };
}

/** A cut value with its mode: replace values beyond it, or leave those samples out. */
function CutInput({ label, value, mode, below, onChange }: {
  label: string;
  value: string;
  mode: CutMode;
  below?: boolean;
  onChange: (value: string, mode: CutMode) => void;
}) {
  const beyond = below ? "ниже" : "выше";
  return (
    <div className="hist-cut">
      <label>{label}
        <input value={value} onChange={(e) => onChange(e.target.value, mode)} placeholder="нет" inputMode="decimal" aria-label={label}
          title={mode === "cap" ? `Значения ${beyond} заменяются этим` : `Пробы ${beyond} не учитываются`} />
      </label>
      <select value={mode} onChange={(e) => onChange(value, e.target.value as CutMode)} aria-label={`${label}: режим`}>
        <option value="exclude">исключить</option>
        <option value="cap">{below ? "поднять до значения" : "урезать до значения"}</option>
      </select>
    </div>
  );
}

const WEIGHTS: [WeightMode, string][] = [
  ["none", "Без веса"],
  ["length", "Длина интервала"],
  ["field", "Поле…"],
  ["length_field", "Длина × поле (тоннаж)"],
];

const STAT_ROWS: [string, (s: Describe) => number | null | undefined][] = [
  ["Число проб", (s) => s.count],
  ["Сумма весов", (s) => s.weight_sum],
  ["Минимум", (s) => s.min],
  ["Максимум", (s) => s.max],
  ["Среднее", (s) => s.mean],
  ["Медиана", (s) => s.median],
  ["Ст. отклонение", (s) => s.std],
  ["Дисперсия", (s) => s.variance],
  ["Коэф. вариации", (s) => s.cv],
  ["Асимметрия", (s) => s.skewness],
  ...["5", "10", "25", "75", "90", "95", "97.5", "99"].map((p): [string, (s: Describe) => number] => [`P${p}`, (s) => s.percentiles[p]]),
  ["Геом. среднее", (s) => s.log?.geometric_mean],
  ["Среднее ln", (s) => s.log?.mean_ln],
  ["Дисперсия ln", (s) => s.log?.variance_ln],
];

/** One table: the (weighted) statistics and, when weighted, the plain ones beside them. */
function StatsTable({ main, plain, weighted }: { main: Describe; plain: Describe | null; weighted: boolean }) {
  const half = Math.ceil(STAT_ROWS.length / 2);
  const block = (rows: typeof STAT_ROWS) => (
    <table className="hist-stats">
      <thead>
        <tr><th /><th>{weighted ? "С весом" : "Значение"}</th>{plain && <th>Без веса</th>}</tr>
      </thead>
      <tbody>
        {rows.map(([k, f]) => (
          <tr key={k}><th>{k}</th><td>{formatNumber(f(main) ?? null, 5)}</td>{plain && <td>{formatNumber(f(plain) ?? null, 5)}</td>}</tr>
        ))}
      </tbody>
    </table>
  );
  return <div className="hist-tables">{block(STAT_ROWS.slice(0, half))}{block(STAT_ROWS.slice(half))}</div>;
}

/** Histogram of a numeric field of a drillhole set's assay table, Leapfrog / Micromine style. */
export const fieldLabel = (id: string | null) => (id === "length" ? "длина" : (id ?? "").split(":").pop() ?? "");

export const histogramName = (s: HistogramSettings) =>
  `Гистограмма ${fieldLabel(s.field)}${s.log ? " (log)" : ""}${s.weight === "none" ? "" : s.weight === "length" ? "" : `, вес ${fieldLabel(s.weightField)}`}`.trim();

export function HistogramWindow({ setId, setName, initial, onSettings }: {
  setId: number;
  setName: string;
  initial?: Partial<HistogramSettings>;
  /** Every change of the settings (the chart is saved with them) and the name they suggest. */
  onSettings?: (settings: HistogramSettings, autoName: string) => void;
}) {
  const [fields, setFields] = useState<NumericField[] | null>(null);
  const [s, setS] = useState<HistogramSettings>({ ...DEFAULT_HISTOGRAM, ...initial, marks: { ...DEFAULT_HISTOGRAM.marks, ...initial?.marks } });
  const [result, setResult] = useState<HistogramResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<HistogramSettings>) => setS((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    getNumericFields(setId)
      .then((list) => {
        setFields(list);
        setS((prev) => ({ ...prev, field: prev.field && list.some((f) => f.id === prev.field) ? prev.field : (list.find((f) => f.kind === "component") ?? list[0])?.id ?? null }));
      })
      .catch((e: Error) => setError(e.message));
  }, [setId]);

  useEffect(() => {
    if (s.field) onSettings?.(s, histogramName(s));
  }, [s]); // eslint-disable-line react-hooks/exhaustive-deps

  const params = useMemo(() => toParams(s), [s]);
  const key = JSON.stringify(params);
  useEffect(() => {
    if (!params) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      getHistogram(setId, params, controller.signal)
        .then((r) => {
          setResult(r);
          setError(null);
        })
        .catch((e: Error) => e.name !== "AbortError" && setError(e.message))
        .finally(() => setBusy(false));
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setId, key]);

  const field = fields?.find((f) => f.id === s.field);
  const xLabel = field ? `${field.name}${field.unit ? `, ${field.unit}` : ""}` : "";
  const fileName = `Гистограмма ${setName} ${field?.name ?? ""}`.trim();
  const weightOptions = (fields ?? []).filter((f) => f.id !== "length");

  const marks: Mark[] = [];
  if (result) {
    const st = result.stats;
    if (s.marks.mean) marks.push({ value: st.mean, label: `Среднее ${formatNumber(st.mean)}` });
    if (s.marks.median) marks.push({ value: st.median, label: `Медиана ${formatNumber(st.median)}`, dashed: true });
    if (s.marks.p90) marks.push({ value: st.percentiles["90"], label: `P90 ${formatNumber(st.percentiles["90"])}`, dashed: true });
    if (s.marks.p975) marks.push({ value: st.percentiles["97.5"], label: `P97.5 ${formatNumber(st.percentiles["97.5"])}`, dashed: true });
    const low = parseNumber(s.bottomCut);
    const cut = parseNumber(s.topCut);
    if (s.marks.topCut && low !== null) marks.push({ value: low, label: `Отсечка ${formatNumber(low)}` });
    if (s.marks.topCut && cut !== null) marks.push({ value: cut, label: `${s.topMode === "cap" ? "Урезка" : "Отсечка"} ${formatNumber(cut)}` });
  }

  function saveCsv() {
    if (!result) return;
    const b = result.bins;
    const rows = b.counts.map((c, i) => [b.edges[i], b.edges[i + 1], c, b.weights[i], b.percent[i], b.cumulative_percent[i]]);
    download(`${fileName}.csv`, new Blob([toCsv(["От", "До", "Проб", "Сумма весов", "Доля, %", "Накоплено, %"], rows)], { type: "text/csv" }));
  }

  const c = result?.counts;
  return (
    <div className="hist-window">
      <form className="hist-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Данные</legend>
          <label>Поле
            <select value={s.field ?? ""} onChange={(e) => set({ field: e.target.value })} aria-label="Поле">
              {(fields ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}{f.unit ? `, ${f.unit}` : ""} ({f.count})</option>)}
            </select>
          </label>
          <label>Вес
            <select value={s.weight} onChange={(e) => set({ weight: e.target.value as WeightMode })} aria-label="Вес">
              {WEIGHTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          </label>
          {(s.weight === "field" || s.weight === "length_field") && (
            <label>Поле веса
              <select value={s.weightField ?? ""} onChange={(e) => set({ weightField: e.target.value || null })} aria-label="Поле веса">
                <option value="">— выберите —</option>
                {weightOptions.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            </label>
          )}
          <label>Значения &lt; 0
            <select value={s.negatives} onChange={(e) => set({ negatives: e.target.value as HistogramSettings["negatives"] })} aria-label="Отрицательные значения" title="Многие лаборатории и Micromine пишут «<0.01» как −0.01">
              <option value="keep">оставить как есть</option>
              <option value="half_abs">половина предела (|x| / 2)</option>
              <option value="exclude">исключить</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={s.excludeZero} onChange={(e) => set({ excludeZero: e.target.checked })} /> Исключить нули</label>
          <CutInput label="Отсечка снизу" value={s.bottomCut} mode={s.bottomMode} below
            onChange={(bottomCut, bottomMode) => set({ bottomCut, bottomMode })} />
          <CutInput label="Отсечка сверху" value={s.topCut} mode={s.topMode}
            onChange={(topCut, topMode) => set({ topCut, topMode })} />
        </fieldset>

        <fieldset>
          <legend>Бины</legend>
          <div className="seg" role="radiogroup" aria-label="Шкала">
            <button type="button" className={!s.log ? "active" : ""} onClick={() => set({ log: false, binWidth: "" })}>Линейная</button>
            <button type="button" className={s.log ? "active" : ""} onClick={() => set({ log: true, binWidth: "" })}>Логарифм.</button>
          </div>
          <div className="seg" role="radiogroup" aria-label="Бины">
            <button type="button" className={s.binMode === "auto" ? "active" : ""} onClick={() => set({ binMode: "auto" })}>Авто</button>
            <button type="button" className={s.binMode === "width" ? "active" : ""} onClick={() => set({ binMode: "width" })}>Ширина</button>
            <button type="button" className={s.binMode === "count" ? "active" : ""} onClick={() => set({ binMode: "count" })}>Число</button>
          </div>
          {s.binMode === "width" && (
            <label>{s.log ? "Ширина, log10" : "Ширина бина"}
              <input value={s.binWidth} onChange={(e) => set({ binWidth: e.target.value })} placeholder={result ? formatNumber(result.bins.width) : ""} inputMode="decimal" aria-label="Ширина бина" />
            </label>
          )}
          {s.binMode === "count" && (
            <label>Число бинов
              <input value={s.bins} onChange={(e) => set({ bins: e.target.value })} inputMode="numeric" aria-label="Число бинов" />
            </label>
          )}
          <div className="pair">
            <label>От<input value={s.start} onChange={(e) => set({ start: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Начало диапазона" /></label>
            <label>До<input value={s.end} onChange={(e) => set({ end: e.target.value })} placeholder="авто" inputMode="decimal" aria-label="Конец диапазона" /></label>
          </div>
          {s.log && <p className="muted small">Ширина в log10: 0.1 — десять бинов на порядок.</p>}
        </fieldset>

        <fieldset>
          <legend>Вид</legend>
          <label>Ось Y
            <select value={s.y} onChange={(e) => set({ y: e.target.value as YMode })} aria-label="Ось Y">
              {(Object.keys(Y_TITLES) as YMode[]).map((k) => <option key={k} value={k}>{Y_TITLES[k]}</option>)}
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={s.cumulative} onChange={(e) => set({ cumulative: e.target.checked })} /> Накопленная кривая</label>
          <div className="checks">
            {([["mean", "Среднее"], ["median", "Медиана"], ["p90", "P90"], ["p975", "P97.5"], ["topCut", "Отсечки"]] as [keyof HistogramSettings["marks"], string][]).map(([k, t]) => (
              <label key={k} className="check"><input type="checkbox" checked={s.marks[k]} onChange={(e) => set({ marks: { ...s.marks, [k]: e.target.checked } })} /> {t}</label>
            ))}
          </div>
          <label>Цвет<input type="color" value={s.color} onChange={(e) => set({ color: e.target.value })} aria-label="Цвет столбцов" /></label>
        </fieldset>

        <div className="hist-export">
          <button type="button" disabled={!result} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!result} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!result} onClick={saveCsv}>CSV бинов</button>
        </div>
      </form>

      <div className="hist-main">
        {error && <div className="alert error small">{error}</div>}
        {!result && !error && <p className="muted small">{fields === null || busy ? "Расчёт…" : "Нет числовых полей в таблице проб."}</p>}
        {result && (
          <>
            <div className={busy ? "hist-plot busy" : "hist-plot"}>
              <HistogramChart ref={svgRef} result={result} y={s.y} cumulative={s.cumulative} marks={marks} color={s.color} xLabel={xLabel} />
            </div>
            <p className="muted small hist-notes">
              Строк {c!.rows}, в расчёте {c!.used}
              {c!.missing > 0 && ` · без значения ${c!.missing}`}
              {c!.bad_weight > 0 && ` · без веса ${c!.bad_weight}`}
              {c!.negative > 0 && ` · отрицательных ${c!.negative}${s.negatives === "exclude" ? " (исключены)" : s.negatives === "half_abs" ? " (|x|/2)" : ""}`}
              {c!.zero_excluded > 0 && ` · нулей исключено ${c!.zero_excluded}`}
              {c!.bottom_excluded > 0 && ` · ниже отсечки исключено ${c!.bottom_excluded}`}
              {c!.bottom_capped > 0 && ` · поднято до отсечки ${c!.bottom_capped}`}
              {c!.capped > 0 && ` · урезано ${c!.capped}`}
              {c!.top_excluded > 0 && ` · выше отсечки исключено ${c!.top_excluded}`}
              {c!.nonpositive_log > 0 && ` · ≤ 0 не показаны на логарифмической шкале: ${c!.nonpositive_log}`}
              {result.bins.below.count + result.bins.above.count > 0 && ` · вне диапазона: ниже ${result.bins.below.count}, выше ${result.bins.above.count}`}
              {` · бинов ${result.bins.counts.length}, ширина ${formatNumber(result.bins.width)}${result.log ? " (log10)" : ""}`}
            </p>
            <StatsTable main={result.stats} plain={result.stats_unweighted} weighted={result.weighted} />
            <p className="muted small">{result.conventions} Ось Y сейчас: {Y_TITLES[s.y].toLowerCase()}, максимум {formatNumber(Math.max(...barValues(result, s.y)))}.</p>
          </>
        )}
      </div>
    </div>
  );
}
