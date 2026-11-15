import { useEffect, useMemo, useRef, useState } from "react";
import { getNumericFields, getQQ, type NumericField, type QQResult, type SampleParams, type WeightMode } from "../api";
import { formatNumber, parseNumber } from "../stats/chartMath";
import { download, savePng, saveSvg, toCsv } from "./exportChart";
import { QQChart } from "./QQChart";

/** One side of a Q-Q plot: which values of which set, weighted how, filtered how. */
export interface SampleSettings {
  setId: number;
  field: string | null;
  weight: WeightMode;
  weightField: string | null;
  negatives: SampleParams["negatives"];
  excludeZero: boolean;
  topCut: string;
  /** hole ids separated by commas or spaces; empty: all holes */
  holes: string;
}

export interface QQSettings {
  mode: "theory" | "sample";
  distribution: "normal" | "lognormal";
  x: SampleSettings;
  y: SampleSettings;
  quantiles: string;
  log: boolean;
  equal: boolean;
  diagonal: boolean;
  labels: boolean;
  color: string;
}

export const sampleDefaults = (setId: number): SampleSettings => ({
  setId, field: null, weight: "length", weightField: null, negatives: "keep", excludeZero: false, topCut: "", holes: "",
});

export const defaultQQ = (setId: number): QQSettings => ({
  // Grades are usually checked against a lognormal distribution first.
  mode: "theory",
  distribution: "lognormal",
  x: sampleDefaults(setId),
  y: sampleDefaults(setId),
  quantiles: "99",
  log: true,
  equal: true,
  diagonal: true,
  labels: true,
  color: "#4f9dde",
});

export function toSample(s: SampleSettings): SampleParams | null {
  if (!s.field) return null;
  const needsField = s.weight === "field" || s.weight === "length_field";
  if (needsField && !s.weightField) return null;
  const holes = s.holes.split(/[\s,;]+/).map((h) => h.trim()).filter(Boolean);
  return {
    set_id: s.setId, field: s.field, weight: s.weight, weight_field: needsField ? s.weightField : null,
    negatives: s.negatives, exclude_zero: s.excludeZero, top_cut: parseNumber(s.topCut), holes: holes.length ? holes : null,
  };
}

/** The request body, or null when a side is not complete yet. */
export function toQQRequest(s: QQSettings) {
  const x = toSample(s.x);
  if (!x) return null;
  const quantiles = Math.round(parseNumber(s.quantiles) ?? 99);
  if (s.mode === "theory") return { x, y: { kind: s.distribution }, quantiles: 99 } as const;
  const y = toSample(s.y);
  if (!y) return null;
  return { x, y: { kind: "sample" as const, ...y }, quantiles: Math.min(999, Math.max(3, quantiles)) };
}

const fieldCache = new Map<number, Promise<NumericField[]>>();
const fieldsOf = (setId: number) => {
  let p = fieldCache.get(setId);
  if (!p) {
    p = getNumericFields(setId).catch((e) => {
      fieldCache.delete(setId);
      throw e;
    });
    fieldCache.set(setId, p);
  }
  return p;
};

function useFields(setId: number) {
  const [fields, setFields] = useState<NumericField[] | null>(null);
  useEffect(() => {
    let alive = true;
    setFields(null);
    fieldsOf(setId).then((f) => alive && setFields(f)).catch(() => alive && setFields([]));
    return () => {
      alive = false;
    };
  }, [setId]);
  return fields;
}

const WEIGHTS: [WeightMode, string][] = [["none", "Без веса"], ["length", "Длина интервала"], ["field", "Поле…"], ["length_field", "Длина × поле"]];

function SampleForm({ title, value, sets, onChange }: {
  title: string; value: SampleSettings; sets: { id: number; name: string }[]; onChange: (next: SampleSettings) => void;
}) {
  const fields = useFields(value.setId);
  const set = (patch: Partial<SampleSettings>) => onChange({ ...value, ...patch });
  // Pick a field once the set's fields are known (the first component).
  useEffect(() => {
    if (fields && (!value.field || !fields.some((f) => f.id === value.field))) {
      set({ field: (fields.find((f) => f.kind === "component") ?? fields[0])?.id ?? null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields]);
  const needsField = value.weight === "field" || value.weight === "length_field";
  return (
    <fieldset>
      <legend>{title}</legend>
      <label>Набор
        <select value={value.setId} onChange={(e) => set({ setId: Number(e.target.value), field: null, weightField: null })} aria-label={`${title}: набор`}>
          {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <label>Поле
        <select value={value.field ?? ""} onChange={(e) => set({ field: e.target.value })} aria-label={`${title}: поле`}>
          {(fields ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}{f.unit ? `, ${f.unit}` : ""} ({f.count})</option>)}
        </select>
      </label>
      <label>Вес
        <select value={value.weight} onChange={(e) => set({ weight: e.target.value as WeightMode })} aria-label={`${title}: вес`}>
          {WEIGHTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
      </label>
      {needsField && (
        <label>Поле веса
          <select value={value.weightField ?? ""} onChange={(e) => set({ weightField: e.target.value || null })} aria-label={`${title}: поле веса`}>
            <option value="">— выберите —</option>
            {(fields ?? []).filter((f) => f.id !== "length").map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
      )}
      <label>Значения &lt; 0
        <select value={value.negatives} onChange={(e) => set({ negatives: e.target.value as SampleSettings["negatives"] })} aria-label={`${title}: отрицательные`}>
          <option value="keep">оставить</option>
          <option value="half_abs">|x| / 2</option>
          <option value="exclude">исключить</option>
        </select>
      </label>
      <label className="check"><input type="checkbox" checked={value.excludeZero} onChange={(e) => set({ excludeZero: e.target.checked })} /> Исключить нули</label>
      <label>Урезка сверху<input value={value.topCut} onChange={(e) => set({ topCut: e.target.value })} placeholder="нет" inputMode="decimal" aria-label={`${title}: урезка`} /></label>
      <label>Скважины<input value={value.holes} onChange={(e) => set({ holes: e.target.value })} placeholder="все" aria-label={`${title}: скважины`} title="Номера через запятую или пробел; пусто — все" /></label>
    </fieldset>
  );
}

const STAT_ROWS: [string, (s: QQResult["x"]["stats"]) => number | null | undefined][] = [
  ["Число проб", (s) => s.count], ["Среднее", (s) => s.mean], ["Медиана", (s) => s.median], ["Ст. отклонение", (s) => s.std],
  ["Коэф. вариации", (s) => s.cv], ["P10", (s) => s.percentiles["10"]], ["P90", (s) => s.percentiles["90"]],
  ["Минимум", (s) => s.min], ["Максимум", (s) => s.max],
];

/** Q-Q plot of a set's assay field against a fitted distribution or against another sample. */
export function QQWindow({ sets, initial }: { sets: { id: number; name: string }[]; initial: QQSettings }) {
  const [s, setS] = useState<QQSettings>(initial);
  const [result, setResult] = useState<QQResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const xFields = useFields(s.x.setId);
  const yFields = useFields(s.y.setId);
  const set = (patch: Partial<QQSettings>) => setS((prev) => ({ ...prev, ...patch }));

  const body = useMemo(() => toQQRequest(s), [s]);
  const key = JSON.stringify(body);
  useEffect(() => {
    if (!body) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      getQQ(body, controller.signal)
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
  }, [key]);

  const setName = (id: number) => sets.find((d) => d.id === id)?.name ?? `набор ${id}`;
  const fieldName = (fields: NumericField[] | null, id: string | null) => {
    const f = fields?.find((x) => x.id === id);
    return f ? `${f.name}${f.unit ? `, ${f.unit}` : ""}` : "";
  };
  const xName = `${fieldName(xFields, s.x.field)} · ${setName(s.x.setId)}`;
  const yName = `${fieldName(yFields, s.y.field)} · ${setName(s.y.setId)}`;
  const theory = result?.mode === "theory";
  const distName = s.distribution === "lognormal" ? "логнормальное" : "нормальное";
  const axisX = theory ? `Квантили: ${distName} распределение` : xName;
  const axisY = theory ? xName : yName;
  const fileName = `Q-Q ${theory ? `${fieldName(xFields, s.x.field)} ${distName}` : `${fieldName(xFields, s.x.field)} ${fieldName(yFields, s.y.field)}`}`.trim();

  function saveCsv() {
    if (!result) return;
    const rows = result.points.map(([a, b, p]) => [p, a, b]);
    download(`${fileName}.csv`, new Blob([toCsv(["Вероятность, %", axisX, axisY], rows)], { type: "text/csv" }));
  }

  return (
    <div className="hist-window">
      <form className="hist-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Сравнение</legend>
          <div className="seg" role="radiogroup" aria-label="Режим">
            <button type="button" className={s.mode === "theory" ? "active" : ""} onClick={() => set({ mode: "theory" })} title="Выборка против подобранного распределения">Распределение</button>
            <button type="button" className={s.mode === "sample" ? "active" : ""} onClick={() => set({ mode: "sample" })} title="Квантили одной выборки против другой">Две выборки</button>
          </div>
          {s.mode === "theory" ? (
            <label>Распределение
              <select value={s.distribution} onChange={(e) => set({ distribution: e.target.value as QQSettings["distribution"], log: e.target.value === "lognormal" })} aria-label="Распределение">
                <option value="lognormal">Логнормальное</option>
                <option value="normal">Нормальное</option>
              </select>
            </label>
          ) : (
            <label>Квантилей
              <input value={s.quantiles} onChange={(e) => set({ quantiles: e.target.value })} inputMode="numeric" aria-label="Число квантилей" title="99 — каждый процент; 19 — каждые 5 %" />
            </label>
          )}
        </fieldset>
        <SampleForm title={s.mode === "theory" ? "Выборка" : "Выборка X"} value={s.x} sets={sets} onChange={(x) => set({ x })} />
        {s.mode === "sample" && <SampleForm title="Выборка Y" value={s.y} sets={sets} onChange={(y) => set({ y })} />}
        <fieldset>
          <legend>Вид</legend>
          <label className="check"><input type="checkbox" checked={s.log} onChange={(e) => set({ log: e.target.checked })} /> Логарифмические оси</label>
          <label className="check"><input type="checkbox" checked={s.equal} onChange={(e) => set({ equal: e.target.checked })} /> Одинаковый масштаб осей</label>
          <label className="check"><input type="checkbox" checked={s.diagonal} onChange={(e) => set({ diagonal: e.target.checked })} /> Линия 1:1</label>
          <label className="check"><input type="checkbox" checked={s.labels} onChange={(e) => set({ labels: e.target.checked })} /> Отметки P10, P50, P90</label>
          <label>Цвет<input type="color" value={s.color} onChange={(e) => set({ color: e.target.value })} aria-label="Цвет точек" /></label>
        </fieldset>
        <div className="hist-export">
          <button type="button" disabled={!result} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!result} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!result} onClick={saveCsv}>CSV точек</button>
        </div>
      </form>

      <div className="hist-main">
        {error && <div className="alert error small">{error}</div>}
        {!result && !error && <p className="muted small">{busy || !xFields ? "Расчёт…" : "Выберите поле."}</p>}
        {result && (
          <>
            <div className={busy ? "hist-plot qq-plot busy" : "hist-plot qq-plot"}>
              <QQChart ref={svgRef} result={result} log={s.log} equal={s.equal} diagonal={s.diagonal} labels={s.labels} color={s.color} xLabel={axisX} yLabel={axisY} />
            </div>
            <p className="muted small hist-notes">
              {theory && result.distribution && (
                <>
                  {result.distribution.name === "lognormal" ? "ln-среднее" : "Среднее"} {formatNumber(result.distribution.mu, 5)},{" "}
                  {result.distribution.name === "lognormal" ? "ln-σ" : "σ"} {formatNumber(result.distribution.sigma, 5)}
                  {" · "}PPCC {formatNumber(result.ppcc ?? null, 5)} (1 — точное совпадение)
                  {result.distribution.dropped_nonpositive > 0 && ` · ≤ 0 не учтены: ${result.distribution.dropped_nonpositive}`}
                  {` · точек ${result.points.length}`}
                </>
              )}
              {!theory && (
                <>
                  Квантилей {result.points.length} · расстояние Колмогорова–Смирнова D = {formatNumber(result.ks ?? null, 4)} (0 — одинаковые распределения)
                </>
              )}
            </p>
            <table className="hist-stats qq-stats">
              <thead>
                <tr><th /><th>{theory ? "Выборка" : "X"}</th>{result.y && <th>Y</th>}{result.y && <th>Y / X</th>}</tr>
              </thead>
              <tbody>
                {STAT_ROWS.map(([k, f]) => {
                  const a = f(result.x.stats);
                  const b = result.y ? f(result.y.stats) : null;
                  return (
                    <tr key={k}>
                      <th>{k}</th>
                      <td>{formatNumber(a ?? null, 5)}</td>
                      {result.y && <td>{formatNumber(b ?? null, 5)}</td>}
                      {result.y && <td>{a && b !== null && b !== undefined && k !== "Число проб" ? formatNumber(b / a, 4) : ""}</td>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="muted small">
              {result.x.weighted || result.y?.weighted ? "Квантили и статистика взвешены (процентили по GSLIB). " : ""}
              Проб в расчёте: X {result.x.counts.used} из {result.x.counts.rows}{result.y ? `, Y ${result.y.counts.used} из ${result.y.counts.rows}` : ""}.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
