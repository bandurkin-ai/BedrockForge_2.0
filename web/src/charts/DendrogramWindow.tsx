import { useEffect, useMemo, useRef, useState } from "react";
import { getDendrogram, getNumericFields, type DendrogramParams, type DendrogramResult, type NumericField } from "../api";
import { formatNumber, parseNumber } from "../stats/chartMath";
import { clustersAt, DendrogramChart } from "./DendrogramChart";
import { download, savePng, saveSvg, toCsv } from "./exportChart";

export interface DendrogramSettings {
  /** null: every component of the set */
  fields: string[] | null;
  method: DendrogramParams["method"];
  log: boolean;
  linkage: DendrogramParams["linkage"];
  distance: DendrogramParams["distance"];
  missing: DendrogramParams["missing"];
  weight: DendrogramParams["weight"];
  negatives: DendrogramParams["negatives"];
  excludeZero: boolean;
  holes: string;
  cut: string;
  matrix: boolean;
  values: boolean;
}

export const DEFAULT_DENDROGRAM: DendrogramSettings = {
  fields: null,
  method: "pearson",
  // Grades are lognormal: correlations of ln values are the usual choice.
  log: true,
  linkage: "average",
  distance: "1-r",
  missing: "pairwise",
  weight: "length",
  negatives: "half_abs",
  excludeZero: false,
  holes: "",
  cut: "",
  matrix: true,
  values: true,
};

export function toDendrogramParams(s: DendrogramSettings): DendrogramParams {
  const holes = s.holes.split(/[\s,;]+/).map((h) => h.trim()).filter(Boolean);
  return {
    fields: s.fields, method: s.method, log: s.log, linkage: s.linkage, distance: s.distance, missing: s.missing,
    weight: s.weight, negatives: s.negatives, exclude_zero: s.excludeZero, holes: holes.length ? holes : null,
  };
}

/** "поле", "поля", "полей" for a count (Russian plural). */
export function fieldsWord(n: number): string {
  const d = n % 10;
  const dd = n % 100;
  if (d === 1 && dd !== 11) return "поле";
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return "поля";
  return "полей";
}

export const dendrogramName = (s: DendrogramSettings, count: number | null) =>
  `Дендрограмма${count ? ` (${count} ${fieldsWord(count)})` : ""}${s.method === "spearman" ? ", Спирмен" : ""}`;

/** Clustering of the set's numeric fields by correlation, with the correlation matrix. */
export function DendrogramWindow({ setId, setName, initial, onSettings }: {
  setId: number;
  setName: string;
  initial?: Partial<DendrogramSettings>;
  onSettings?: (settings: DendrogramSettings, autoName: string) => void;
}) {
  const [fields, setFields] = useState<NumericField[] | null>(null);
  const [s, setS] = useState<DendrogramSettings>({ ...DEFAULT_DENDROGRAM, ...initial });
  const [result, setResult] = useState<DendrogramResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<DendrogramSettings>) => setS((prev) => ({ ...prev, ...patch }));

  useEffect(() => {
    getNumericFields(setId).then(setFields).catch((e: Error) => setError(e.message));
  }, [setId]);

  const chosen = s.fields ?? (fields ?? []).filter((f) => f.kind === "component").map((f) => f.id);
  useEffect(() => onSettings?.(s, dendrogramName(s, fields ? chosen.length : null)), [s, fields]); // eslint-disable-line react-hooks/exhaustive-deps

  const params = useMemo(() => toDendrogramParams(s), [s]);
  const key = JSON.stringify(params);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      getDendrogram(setId, params, controller.signal)
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

  const toggle = (id: string, on: boolean) => {
    const next = on ? [...chosen, id] : chosen.filter((f) => f !== id);
    set({ fields: next });
  };
  const cut = parseNumber(s.cut);
  const groups = result && cut !== null ? clustersAt(result.linkage, result.names.length, result.order, cut) : null;
  const fileName = `Дендрограмма ${setName}`;
  const distanceLabel = `Расстояние ${s.distance === "1-r" ? "1 − r" : "1 − |r|"} (${s.method === "pearson" ? "Пирсон" : "Спирмен"}${s.log ? ", ln" : ""})`;

  function saveCsv() {
    if (!result) return;
    const rows = result.order.map((i) => [result.names[i], ...result.order.map((j) => result.correlation[i][j])]);
    download(`${fileName} корреляции.csv`, new Blob([toCsv(["", ...result.order.map((i) => result.names[i])], rows)], { type: "text/csv" }));
  }

  return (
    <div className="hist-window">
      <form className="hist-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Поля ({chosen.length})</legend>
          <div className="dendro-fields">
            {(fields ?? []).map((f) => (
              <label key={f.id} className="check">
                <input type="checkbox" checked={chosen.includes(f.id)} onChange={(e) => toggle(f.id, e.target.checked)} /> {f.name}
              </label>
            ))}
          </div>
          <div className="pair">
            <button type="button" onClick={() => set({ fields: (fields ?? []).filter((f) => f.kind === "component").map((f) => f.id) })}>Компоненты</button>
            <button type="button" onClick={() => set({ fields: (fields ?? []).map((f) => f.id) })}>Все</button>
          </div>
        </fieldset>
        <fieldset>
          <legend>Корреляция</legend>
          <label>Коэффициент
            <select value={s.method} onChange={(e) => set({ method: e.target.value as DendrogramSettings["method"] })} aria-label="Коэффициент корреляции">
              <option value="pearson">Пирсон</option>
              <option value="spearman">Спирмен (ранговый)</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={s.log} onChange={(e) => set({ log: e.target.checked })} /> По логарифмам (ln)</label>
          <label>Вес
            <select value={s.weight} onChange={(e) => set({ weight: e.target.value as DendrogramSettings["weight"] })} aria-label="Вес">
              <option value="length">Длина интервала</option>
              <option value="none">Без веса</option>
            </select>
          </label>
          <label>Пропуски
            <select value={s.missing} onChange={(e) => set({ missing: e.target.value as DendrogramSettings["missing"] })} aria-label="Пропуски">
              <option value="pairwise">Попарно</option>
              <option value="complete">Только полные строки</option>
            </select>
          </label>
          <label>Значения &lt; 0
            <select value={s.negatives} onChange={(e) => set({ negatives: e.target.value as DendrogramSettings["negatives"] })} aria-label="Отрицательные значения">
              <option value="half_abs">|x| / 2</option>
              <option value="exclude">исключить</option>
              <option value="keep">оставить</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={s.excludeZero} onChange={(e) => set({ excludeZero: e.target.checked })} /> Исключить нули</label>
          <label>Скважины<input value={s.holes} onChange={(e) => set({ holes: e.target.value })} placeholder="все" aria-label="Скважины" /></label>
        </fieldset>
        <fieldset>
          <legend>Кластеры</legend>
          <label>Расстояние
            <select value={s.distance} onChange={(e) => set({ distance: e.target.value as DendrogramSettings["distance"] })} aria-label="Расстояние">
              <option value="1-r">1 − r (обратная связь — далеко)</option>
              <option value="1-|r|">1 − |r| (только сила связи)</option>
            </select>
          </label>
          <label>Связь
            <select value={s.linkage} onChange={(e) => set({ linkage: e.target.value as DendrogramSettings["linkage"] })} aria-label="Способ связи">
              <option value="average">Средняя (UPGMA)</option>
              <option value="complete">Полная</option>
              <option value="single">Одиночная</option>
              <option value="weighted">Взвешенная (WPGMA)</option>
            </select>
          </label>
          <label>Порог отсечения<input value={s.cut} onChange={(e) => set({ cut: e.target.value })} placeholder="нет" inputMode="decimal" aria-label="Порог отсечения" title="Поля, связанные ниже порога, — одна группа (цвет)" /></label>
          <label className="check"><input type="checkbox" checked={s.matrix} onChange={(e) => set({ matrix: e.target.checked })} /> Матрица корреляций</label>
          <label className="check"><input type="checkbox" checked={s.values} onChange={(e) => set({ values: e.target.checked })} /> Значения в ячейках</label>
        </fieldset>
        <div className="hist-export">
          <button type="button" disabled={!result} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!result} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!result} onClick={saveCsv}>CSV r</button>
        </div>
      </form>
      <div className="hist-main">
        {error && <div className="alert error small">{error}</div>}
        {!result && !error && <p className="muted small">Расчёт…</p>}
        {result && (
          <>
            <div className={busy ? "hist-plot busy" : "hist-plot"}>
              <DendrogramChart ref={svgRef} result={result} cut={cut} matrix={s.matrix} values={s.values} distanceLabel={distanceLabel} />
            </div>
            <p className="muted small hist-notes">
              Полей {result.names.length}, строк {result.rows}
              {result.dropped_nonpositive_log > 0 && ` · значений ≤ 0 не учтено для ln: ${result.dropped_nonpositive_log}`}
              {` · наибольшее расстояние ${formatNumber(result.max_distance, 4)}`}
              {groups && ` · групп при пороге ${formatNumber(cut, 4)}: ${new Set(groups.leaf).size}`}
            </p>
            <p className="muted small">Наведите на ячейку матрицы — r и число общих проб. Методы: scipy.cluster.hierarchy; Спирмен — корреляция рангов.</p>
          </>
        )}
      </div>
    </div>
  );
}
