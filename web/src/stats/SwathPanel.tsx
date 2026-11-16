import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { blockMeta, getNumericFields, type Dataset, type NumericField, type SwathResult } from "../api";
import { download, savePng, saveSvg, toCsv } from "../charts/exportChart";
import { unitTitle } from "../shell/BlockModelPanel";
import { Icon } from "../shell/icons";
import { loadSampleTables } from "../shell/SampleTableSelect";
import { formatNumber } from "./chartMath";
import {
  DIRECTION_TITLES, axisTitle, axisValue, barScale, barValues, blockSeries, matchComponent, sampleSeries, shownRange, swathProblem, valueAxis,
  type SwathBlockSeries, type SwathDirection, type SwathSampleSeries, type SwathSeries, type SwathSettings,
} from "./swath";

/** The calculation of a plot: the last answer stays on screen while a newer one is counted. */
export interface SwathState {
  busy: boolean;
  error: string | null;
  result: SwathResult | null;
}

export interface SwathPanelProps {
  datasets: Dataset[];
  /** the plot open in the panel (an object of the tree, section «Аудит»); null: none */
  swath: Dataset | null;
  settings: SwathSettings | null;
  state: SwathState | undefined;
  refreshKey: number;
  /** the swaths (and the plot over them) are drawn in the 3D view */
  shown3d: boolean;
  onChange: (next: SwathSettings) => void;
  /** the swath under the cursor of the plot: lit in the 3D view */
  onHover: (index: number | null) => void;
  onShow3d: (on: boolean) => void;
  onNew: () => void;
  onOpen: (id: number) => void;
}

const fieldCache = new Map<string, Promise<{ fields: NumericField[]; tables: { key: string; name: string }[] }>>();

/** Numeric fields of a sample table of a set, and the set's sample tables. */
function useSampleFields(setId: number | null, table: string | null, refreshKey: number) {
  const [data, setData] = useState<{ fields: NumericField[]; tables: { key: string; name: string }[] } | null>(null);
  useEffect(() => {
    if (setId === null) {
      setData(null);
      return;
    }
    const key = `${setId}:${table ?? ""}:${refreshKey}`;
    let p = fieldCache.get(key);
    if (!p) {
      p = Promise.all([getNumericFields(setId, table), loadSampleTables(setId, refreshKey)]).then(([fields, t]) => ({ fields, tables: t.tables }));
      p.catch(() => fieldCache.delete(key));
      fieldCache.set(key, p);
    }
    let cancelled = false;
    p.then((d) => !cancelled && setData(d)).catch(() => !cancelled && setData({ fields: [], tables: [] }));
    return () => {
      cancelled = true;
    };
  }, [setId, table, refreshKey]);
  return data;
}

function BlockFields({ s, model, onChange }: { s: SwathBlockSeries; model: Dataset; onChange: (next: SwathSeries) => void }) {
  const meta = blockMeta(model);
  const numbers = meta.attributes.filter((a) => a.kind === "number");
  const categories = meta.attributes.filter((a) => a.kind === "category");
  const filter = categories.find((a) => a.key === s.filterField);
  return (
    <>
      <label>Поле
        <select value={s.field} onChange={(e) => onChange({ ...s, field: e.target.value })} aria-label="Поле модели">
          {!numbers.some((a) => a.key === s.field) && <option value={s.field}>— выберите —</option>}
          {numbers.map((a) => <option key={a.key} value={a.key}>{a.name}{a.unit ? `, ${unitTitle(a.unit)}` : ""}</option>)}
        </select>
      </label>
      <label>Среднее взвешено
        <select value={s.weight} onChange={(e) => onChange({ ...s, weight: e.target.value as SwathBlockSeries["weight"] })} aria-label="Вес блоков">
          <option value="volume">по объёму блока</option>
          <option value="tonnage">по тоннажу (объём × плотность)</option>
          <option value="none">без весов (по числу блоков)</option>
        </select>
      </label>
      {s.weight === "tonnage" && (
        <label>Плотность
          <select value={s.density ?? ""} onChange={(e) => onChange({ ...s, density: e.target.value || null })} aria-label="Поле плотности">
            <option value="">— выберите —</option>
            {numbers.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
          </select>
        </label>
      )}
      {categories.length > 0 && (
        <label>Только блоки
          <select
            value={s.filterField ?? ""} aria-label="Отбор блоков по полю"
            onChange={(e) => onChange({ ...s, filterField: e.target.value || null, filterLabels: categories.find((a) => a.key === e.target.value)?.labels ?? [] })}
          >
            <option value="">все</option>
            {categories.map((a) => <option key={a.key} value={a.key}>по {a.name}</option>)}
          </select>
        </label>
      )}
      {filter && (
        <div className="dendro-fields">
          {(filter.labels ?? []).map((label) => (
            <label key={label} className="check">
              <input
                type="checkbox" checked={s.filterLabels.includes(label)}
                onChange={(e) => onChange({ ...s, filterLabels: e.target.checked ? [...s.filterLabels, label] : s.filterLabels.filter((l) => l !== label) })}
              /> {label}
            </label>
          ))}
        </div>
      )}
    </>
  );
}

function SampleFields({ s, all, datasets, refreshKey, onChange }: {
  s: SwathSampleSeries; all: SwathSeries[]; datasets: Dataset[]; refreshKey: number; onChange: (next: SwathSeries) => void;
}) {
  const data = useSampleFields(s.setId, s.table, refreshKey);
  const fields = data?.fields ?? null;
  const values = (fields ?? []).filter((f) => f.kind !== "derived");
  // A field the table does not have (a new series, another table): its first component.
  useEffect(() => {
    if (!fields || !values.length || values.some((f) => f.id === s.field)) return;
    onChange({ ...s, field: (values.find((f) => f.kind === "component") ?? values[0]).id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields, s.field]);
  const models = all.filter((x): x is SwathBlockSeries => x.kind === "blocks");
  const modelName = (b: SwathBlockSeries) => {
    const d = datasets.find((x) => x.id === b.modelId);
    const field = d ? blockMeta(d).attributes.find((a) => a.key === b.field)?.name : null;
    return `${d?.name ?? "модель"}${field ? ` · ${field}` : ""}`;
  };
  const within = s.within === "blocks" ? (models.find((b) => b.id === s.withinSeries) ?? models[0])?.id ?? "" : "box";
  return (
    <>
      {data && data.tables.length > 1 && (
        <label>Таблица проб
          <select value={s.table ?? ""} onChange={(e) => onChange({ ...s, table: e.target.value || null })} aria-label="Таблица проб">
            <option value="">по умолчанию</option>
            {data.tables.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
          </select>
        </label>
      )}
      <label>Поле
        <select value={s.field} onChange={(e) => onChange({ ...s, field: e.target.value })} aria-label="Поле проб">
          {!values.some((f) => f.id === s.field) && <option value={s.field}>{fields ? "— выберите —" : "загрузка…"}</option>}
          {values.map((f) => <option key={f.id} value={f.id}>{f.name}{f.unit ? `, ${f.unit}` : ""} · {f.count.toLocaleString("ru-RU")}</option>)}
        </select>
      </label>
      <label>Среднее взвешено
        <select value={s.weight} onChange={(e) => onChange({ ...s, weight: e.target.value as SwathSampleSeries["weight"] })} aria-label="Вес проб">
          <option value="none">без весов (по числу проб)</option>
          <option value="length">по длине интервала</option>
          <option value="length_field">по длине × поле (например, плотность)</option>
          <option value="field">по полю (например, вес декластеризации)</option>
        </select>
      </label>
      {(s.weight === "field" || s.weight === "length_field") && (
        <label>Поле веса
          <select value={s.weightField ?? ""} onChange={(e) => onChange({ ...s, weightField: e.target.value || null })} aria-label="Поле веса">
            <option value="">— выберите —</option>
            {values.filter((f) => f.id !== s.field).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
      )}
      <label>Какие пробы
        <select
          value={within} aria-label="Отбор проб"
          onChange={(e) => onChange(e.target.value === "box" ? { ...s, within: "box", withinSeries: null } : { ...s, within: "blocks", withinSeries: e.target.value })}
        >
          <option value="box">в габаритах полос (кубоид)</option>
          {models.map((b) => <option key={b.id} value={b.id}>внутри блоков: {modelName(b)}</option>)}
        </select>
      </label>
      <div className="pair">
        <label>Значения &lt; 0
          <select value={s.negatives} onChange={(e) => onChange({ ...s, negatives: e.target.value as SwathSampleSeries["negatives"] })} aria-label="Отрицательные значения">
            <option value="keep">как есть</option>
            <option value="exclude">исключить</option>
            <option value="half_abs">½ модуля</option>
          </select>
        </label>
        <label>Урезка сверху
          <input value={s.topCut} onChange={(e) => onChange({ ...s, topCut: e.target.value })} inputMode="decimal" placeholder="нет" aria-label="Урезка ураганных значений" />
        </label>
      </div>
    </>
  );
}

function SeriesCard({ s, index, all, datasets, refreshKey, onChange, onMove, onRemove }: {
  s: SwathSeries; index: number; all: SwathSeries[]; datasets: Dataset[]; refreshKey: number;
  onChange: (next: SwathSeries) => void; onMove: (by: number) => void; onRemove: () => void;
}) {
  const models = datasets.filter((d) => d.kind === "blocks");
  const sets = datasets.filter((d) => d.kind === "drillholes");
  const source = s.kind === "blocks" ? `b:${s.modelId}` : `s:${s.setId}`;
  const dataset = datasets.find((d) => d.id === (s.kind === "blocks" ? s.modelId : s.setId) && d.kind === (s.kind === "blocks" ? "blocks" : "drillholes"));
  const setSource = (value: string) => {
    const [kind, id] = [value[0], Number(value.slice(2))];
    const d = datasets.find((x) => x.id === id);
    if (!d) return;
    const others = all.filter((x) => x.id !== s.id);
    const look = { id: s.id, color: s.color, line: s.line, bars: s.bars };
    if (kind === "b") {
      const next = blockSeries(d, others);
      if (next) onChange({ ...next, ...look });
    } else {
      const model = others.find((x): x is SwathBlockSeries => x.kind === "blocks");
      const modelData = model ? datasets.find((x) => x.id === model.modelId) : undefined;
      const blockName = model && modelData ? blockMeta(modelData).attributes.find((a) => a.key === model.field)?.name ?? null : null;
      const component = matchComponent(d, blockName);
      onChange({ ...sampleSeries(d, others, component ? `comp:${component}` : ""), ...look });
    }
  };
  return (
    <div className="swath-series">
      <div className="swath-series-head">
        <input type="color" value={s.color} onChange={(e) => onChange({ ...s, color: e.target.value })} aria-label={`Цвет ряда ${index + 1}`} title="Цвет ряда" />
        <strong className="grow">Ряд {index + 1} · {s.kind === "blocks" ? "блочная модель" : "пробы"}</strong>
        <button type="button" className={s.line ? "icon active" : "icon"} onClick={() => onChange({ ...s, line: !s.line })} aria-pressed={s.line} title="Линия средних" aria-label="Линия средних"><Icon name="chart" size={14} /></button>
        <button type="button" className={s.bars ? "icon active" : "icon"} onClick={() => onChange({ ...s, bars: !s.bars })} aria-pressed={s.bars} title={s.kind === "blocks" ? "Столбики: объём (вес) блоков в полосе" : "Столбики: число проб в полосе"} aria-label="Столбики"><Icon name="histogram" size={14} /></button>
        <button type="button" className="icon" onClick={() => onMove(-1)} disabled={index === 0} title="Выше" aria-label="Выше"><Icon name="chevronUp" size={14} /></button>
        <button type="button" className="icon" onClick={() => onMove(1)} disabled={index === all.length - 1} title="Ниже" aria-label="Ниже"><Icon name="chevronDown" size={14} /></button>
        <button type="button" className="icon" onClick={onRemove} title="Убрать ряд" aria-label="Убрать ряд"><Icon name="trash" size={14} /></button>
      </div>
      <label>Данные
        <select value={dataset ? source : ""} onChange={(e) => setSource(e.target.value)} aria-label={`Данные ряда ${index + 1}`}>
          {!dataset && <option value="">— источник удалён, выберите другой —</option>}
          {models.length > 0 && <optgroup label="Блочные модели">{models.map((d) => <option key={d.id} value={`b:${d.id}`}>{d.name}</option>)}</optgroup>}
          {sets.length > 0 && <optgroup label="Скважины (пробы)">{sets.map((d) => <option key={d.id} value={`s:${d.id}`}>{d.name}</option>)}</optgroup>}
        </select>
      </label>
      {dataset && s.kind === "blocks" && <BlockFields s={s} model={dataset} onChange={onChange} />}
      {dataset && s.kind === "samples" && <SampleFields s={s} all={all} datasets={datasets} refreshKey={refreshKey} onChange={onChange} />}
    </div>
  );
}

const W = 860;
const PLOT_H = 290;
const M = { left: 62, right: 86, bottom: 46 };
const BAR_SHARE = 0.4;

const big = (v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("ru-RU") : formatNumber(v, 4));

/** The plot for print: lines of means over the swaths, bars of what is in each swath, a legend with the overall means. */
export const SwathChart = forwardRef<SVGSVGElement, {
  result: SwathResult; settings: SwathSettings; hover: number | null; onHover: (index: number | null) => void;
}>(function SwathChart({ result, settings, hover, onHover }, ref) {
  const [from, to] = shownRange(settings, result.count);
  const look = result.series.map((_, i) => settings.series[i]);
  const lineShown = look.map((s) => !!s && s.line);
  const barShown = look.map((s) => !!s && s.bars);
  const axis = valueAxis(result, lineShown, [from, to]);
  const legend = result.series.map((s, i) => ({ s, i })).filter(({ i }) => lineShown[i] || barShown[i]);
  const top = 14 + legend.length * 16;
  const H = top + PLOT_H + M.bottom;
  const n = to - from + 1;
  const bw = (W - M.left - M.right) / n;
  const x0 = (k: number) => M.left + (k - from) * bw;
  const y = (value: number) => top + PLOT_H - ((value - axis.min) / (axis.max - axis.min || 1)) * PLOT_H;
  const barMax = { blocks: barScale(result, "blocks", barShown, [from, to]), samples: barScale(result, "samples", barShown, [from, to]) };
  const barSeries = result.series.filter((_, i) => barShown[i]);
  // The right axis measures the bars of block models; without them, the bars of samples.
  const rightKind = barSeries.some((s) => s.kind === "blocks") ? "blocks" : barSeries.length ? "samples" : null;
  const rightSeries = rightKind ? barSeries.find((s) => s.kind === rightKind)! : null;
  const every = Math.max(1, Math.ceil(n / 12));
  const color = (i: number) => look[i]?.color ?? "#888888";
  const unit = (s: SwathResult["series"][number]) => (s.unit ? `, ${unitTitle(s.unit as never) || s.unit}` : "");
  const units = [...new Set(result.series.filter((_, i) => lineShown[i]).map((s) => s.unit).filter(Boolean))];

  const pick = (e: React.MouseEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const k = from + Math.min(n - 1, Math.max(0, Math.floor(((e.clientX - box.left) / box.width) * n)));
    if (k !== hover) onHover(k);
  };
  const tip = hover !== null && hover >= from && hover <= to ? hover : null;
  const tipLines = tip === null ? [] : [
    `Полоса ${tip + 1}: ${formatNumber(axisValue(result, result.start + tip * result.width), 7)} – ${formatNumber(axisValue(result, result.start + (tip + 1) * result.width), 7)} м`,
    ...result.series.map((s) => `${s.name}: ${formatNumber(s.mean[tip], 4)} · ${s.kind === "samples" ? `проб ${s.count[tip]}` : `${s.weight_name} ${big(s.weights[tip] ?? 0)} ${s.weight_unit}`}`),
  ];
  const tipW = Math.min(420, 16 + Math.max(...tipLines.map((l) => l.length), 0) * 6.2);
  const tipX = tip === null ? 0 : Math.min(W - M.right - tipW, Math.max(M.left, x0(tip) + bw + 6));

  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="hist-chart swath-chart" role="img" aria-label="Swath plot: средние по полосам">
      <rect x={0} y={0} width={W} height={H} className="chart-bg" />
      {legend.map(({ s, i }, row) => (
        <g key={i} transform={`translate(${M.left} ${12 + row * 16})`}>
          {lineShown[i] ? <line x1={0} x2={22} y1={0} y2={0} stroke={color(i)} strokeWidth={2.4} /> : <rect x={4} y={-5} width={14} height={10} fill={color(i)} opacity={0.45} />}
          {lineShown[i] && <circle cx={11} cy={0} r={3} fill={color(i)} />}
          <text className="chart-axis" x={28} y={4}>
            {s.name}{unit(s)} — среднее {formatNumber(s.total.mean, 4)} ({s.weight_name}; {s.kind === "samples" ? "проб" : "блоков"} {s.total.count.toLocaleString("ru-RU")})
          </text>
        </g>
      ))}
      {axis.ticks.map((t) => (
        <g key={t}>
          <line className="chart-grid" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
          <text className="chart-tick" x={M.left - 6} y={y(t) + 4} textAnchor="end">{formatNumber(t, 4)}</text>
        </g>
      ))}
      {result.series.map((s, i) => {
        if (!barShown[i] || !(barMax[s.kind] > 0)) return null;
        const slot = barSeries.indexOf(s);
        const pad = bw * 0.1;
        const each = (bw - 2 * pad) / barSeries.length;
        const values = barValues(s);
        return (
          <g key={`b${i}`} fill={color(i)} opacity={0.32}>
            {Array.from({ length: n }, (_, j) => {
              const value = values[from + j] ?? 0;
              const h = (value / barMax[s.kind]) * PLOT_H * BAR_SHARE;
              return value > 0 ? <rect key={j} x={x0(from + j) + pad + slot * each} width={Math.max(each, 0.5)} y={top + PLOT_H - h} height={h} /> : null;
            })}
          </g>
        );
      })}
      {rightSeries && [0, 0.5, 1].map((f) => (
        <text key={f} className="chart-tick" x={W - M.right + 6} y={top + PLOT_H - f * PLOT_H * BAR_SHARE + 4}>{big(f * barMax[rightKind!])}</text>
      ))}
      {result.series.map((s, i) => {
        if (!lineShown[i]) return null;
        let d = "";
        let pen = false;
        for (let k = from; k <= to; k++) {
          const value = s.mean[k];
          if (value === null || !Number.isFinite(value)) {
            pen = false;
            continue;
          }
          d += `${pen ? "L" : "M"}${(x0(k) + bw / 2).toFixed(1)},${y(value).toFixed(1)}`;
          pen = true;
        }
        return (
          <g key={`l${i}`}>
            <path d={d} fill="none" stroke={color(i)} strokeWidth={2.2} strokeLinejoin="round" />
            {n <= 120 && Array.from({ length: n }, (_, j) => {
              const value = s.mean[from + j];
              return value === null || !Number.isFinite(value) ? null : <circle key={j} cx={x0(from + j) + bw / 2} cy={y(value)} r={n > 60 ? 2 : 3} fill={color(i)} />;
            })}
          </g>
        );
      })}
      <rect className="chart-frame" x={M.left} y={top} width={W - M.left - M.right} height={PLOT_H} fill="none" />
      {Array.from({ length: Math.ceil(n / every) }, (_, j) => from + j * every).map((k) => (
        <g key={k}>
          <line className="chart-grid" x1={x0(k) + bw / 2} x2={x0(k) + bw / 2} y1={top + PLOT_H} y2={top + PLOT_H + 4} />
          <text className="chart-tick" x={x0(k) + bw / 2} y={top + PLOT_H + 16} textAnchor="middle">{formatNumber(axisValue(result, result.centres[k]), 7)}</text>
        </g>
      ))}
      <text className="chart-axis" x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle">
        {axisTitle(result)} · полоса {formatNumber(result.width, 5)} м
      </text>
      <text className="chart-axis" transform={`translate(14 ${top + PLOT_H / 2}) rotate(-90)`} textAnchor="middle">
        Среднее{units.length === 1 ? `, ${unitTitle(units[0] as never) || units[0]}` : ""}
      </text>
      {rightSeries && (
        <text className="chart-axis" transform={`translate(${W - 8} ${top + PLOT_H - (PLOT_H * BAR_SHARE) / 2}) rotate(90)`} textAnchor="middle">
          {rightKind === "samples" ? "Число проб" : `${rightSeries.weight_name[0].toUpperCase()}${rightSeries.weight_name.slice(1)}, ${rightSeries.weight_unit}`}
        </text>
      )}
      {tip !== null && <rect x={x0(tip)} y={top} width={bw} height={PLOT_H} className="swath-hover" />}
      {tip !== null && (
        <g className="hist-tip" transform={`translate(${tipX} ${top + 6})`} pointerEvents="none">
          <rect width={tipW} height={8 + tipLines.length * 14} rx={4} />
          {tipLines.map((line, j) => <text key={j} x={8} y={17 + j * 14}>{line}</text>)}
        </g>
      )}
      <rect x={M.left} y={top} width={W - M.left - M.right} height={PLOT_H} fill="transparent" onMouseMove={pick} onMouseLeave={() => onHover(null)} />
    </svg>
  );
});

/** The index filter: the swaths the plot (and the 3D view) shows. */
function RangeFilter({ count, range, onChange }: { count: number; range: [number, number]; onChange: (next: [number, number] | null) => void }) {
  const all = range[0] === 0 && range[1] === count - 1;
  const set = (from: number, to: number) => {
    const a = Math.min(Math.max(0, from), count - 1);
    const b = Math.min(Math.max(a, to), count - 1);
    onChange(a === 0 && b === count - 1 ? null : [a, b]);
  };
  return (
    <div className="swath-range">
      <span className="muted small">Полосы</span>
      <input type="number" min={1} max={count} value={range[0] + 1} onChange={(e) => set(Number(e.target.value) - 1, range[1])} aria-label="Первая полоса" />
      <input type="range" min={0} max={count - 1} value={range[0]} onChange={(e) => set(Number(e.target.value), Math.max(range[1], Number(e.target.value)))} aria-label="Первая полоса (ползунок)" />
      <input type="range" min={0} max={count - 1} value={range[1]} onChange={(e) => set(Math.min(range[0], Number(e.target.value)), Number(e.target.value))} aria-label="Последняя полоса (ползунок)" />
      <input type="number" min={1} max={count} value={range[1] + 1} onChange={(e) => set(range[0], Number(e.target.value) - 1)} aria-label="Последняя полоса" />
      <span className="muted small">из {count}</span>
      <button type="button" disabled={all} onClick={() => onChange(null)}>Все</button>
    </div>
  );
}

function SwathTable({ result, range }: { result: SwathResult; range: [number, number] }) {
  const rows = [];
  const base = result.series[0];
  const diff = (value: number | null, ref: number | null) => (value === null || ref === null || ref === 0 ? "—" : `${value >= ref ? "+" : ""}${formatNumber(((value - ref) / ref) * 100, 3)}`);
  for (let k = range[0]; k <= range[1]; k++) rows.push(k);
  return (
    <table className="gt-table swath-table">
      <thead>
        <tr>
          <th rowSpan={2}>№</th><th rowSpan={2}>Центр, м</th>
          {result.series.map((s, i) => <th key={i} colSpan={i === 0 ? 2 : 3}>{s.name}</th>)}
        </tr>
        <tr>
          {result.series.map((s, i) => [
            <th key={`m${i}`}>среднее</th>,
            <th key={`n${i}`}>{s.kind === "samples" ? "проб" : s.weight_unit}</th>,
            i > 0 ? <th key={`d${i}`} title={`Отклонение от ряда 1 (${base.name}), %`}>Δ, %</th> : null,
          ])}
        </tr>
      </thead>
      <tbody>
        {rows.map((k) => (
          <tr key={k}>
            <td>{k + 1}</td>
            <td className="num">{formatNumber(axisValue(result, result.centres[k]), 8)}</td>
            {result.series.map((s, i) => [
              <td key={`m${i}`} className="num">{formatNumber(s.mean[k], 4)}</td>,
              <td key={`n${i}`} className="num">{s.kind === "samples" ? s.count[k] : big(s.weights[k] ?? 0)}</td>,
              i > 0 ? <td key={`d${i}`} className="num">{diff(s.mean[k], base.mean[k])}</td> : null,
            ])}
          </tr>
        ))}
        <tr className="swath-total">
          <td colSpan={2}>Все полосы</td>
          {result.series.map((s, i) => [
            <td key={`m${i}`} className="num">{formatNumber(s.total.mean, 4)}</td>,
            <td key={`n${i}`} className="num">{s.kind === "samples" ? s.total.count.toLocaleString("ru-RU") : big(s.total.weight)}</td>,
            i > 0 ? <td key={`d${i}`} className="num">{diff(s.total.mean, base.total.mean)}</td> : null,
          ])}
        </tr>
      </tbody>
    </table>
  );
}

/** What was left out of a series, in words. */
function seriesNote(s: SwathResult["series"][number]): string {
  const c = s.counts;
  const n = (v: number | undefined) => (v ?? 0).toLocaleString("ru-RU");
  if (s.kind === "blocks") {
    const parts = [`блоков в расчёте ${n(c.used)} из ${n(c.blocks)}`];
    if (c.selected < c.blocks) parts.push(`отобрано ${n(c.selected)}`);
    if (c.empty) parts.push(`без значения ${n(c.empty)}`);
    if (c.no_weight) parts.push(`без веса ${n(c.no_weight)}`);
    return `${s.name}: ${parts.join(", ")}.`;
  }
  const parts = [`проб в расчёте ${n(c.used)} из ${n(c.rows)}`];
  if (c.missing) parts.push(`без значения ${n(c.missing)}`);
  if (c.bad_weight) parts.push(`без веса ${n(c.bad_weight)}`);
  if (c.no_position) parts.push(`без положения на скважине ${n(c.no_position)}`);
  if (c.outside_box) parts.push(`вне кубоида ${n(c.outside_box)}`);
  if (c.outside_blocks) parts.push(`вне блоков модели ${n(c.outside_blocks)}`);
  if (c.capped) parts.push(`урезано ${n(c.capped)}`);
  return `${s.name}: ${parts.join(", ")}${s.within && s.within !== "box" ? ` (внутри блоков «${s.within}»)` : ""}.`;
}

/** The swath plot of the bottom dock: settings on the left, the plot, the index filter and the table on the right. */
export function SwathPanel(props: SwathPanelProps) {
  const { datasets, swath, settings, state, refreshKey } = props;
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [menu, setMenu] = useState(false);
  const saved = useMemo(() => datasets.filter((d) => d.kind === "swath"), [datasets]);
  const hoverTo = (index: number | null) => {
    setHover(index);
    props.onHover(index);
  };
  useEffect(() => () => props.onHover(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!swath || !settings) {
    return (
      <div className="swath-empty">
        <p className="muted small">
          Swath plot сравнивает средние содержания блочных моделей и проб по полосам вдоль X, Y, Z или азимута.
          Графики хранятся в дереве проекта, раздел «Аудит».
        </p>
        <button className="primary" onClick={props.onNew}>Новый Swath plot</button>
        {saved.length > 0 && (
          <ul className="swath-list">
            {saved.map((d) => <li key={d.id}><button className="link" onClick={() => props.onOpen(d.id)}><Icon name="swath" size={14} /> {d.name}</button></li>)}
          </ul>
        )}
      </div>
    );
  }

  const set = (patch: Partial<SwathSettings>) => props.onChange({ ...settings, ...patch });
  const setSeries = (series: SwathSeries[]) => {
    // A series that pointed at a removed model falls back to the first one.
    const ids = new Set(series.filter((x) => x.kind === "blocks").map((x) => x.id));
    set({ series: series.map((x) => (x.kind === "samples" && x.withinSeries && !ids.has(x.withinSeries) ? { ...x, withinSeries: null, within: ids.size ? x.within : "box" } : x)) });
  };
  const result = state?.result ?? null;
  const problem = swathProblem(settings);
  // The answer on screen belongs to the series as they were counted; a change of their number waits for the new one.
  const fits = !!result && result.series.length === settings.series.length;
  const range = result ? shownRange(settings, result.count) : ([0, 0] as [number, number]);
  const models = datasets.filter((d) => d.kind === "blocks");
  const sets = datasets.filter((d) => d.kind === "drillholes");
  const add = (kind: "blocks" | "samples", d: Dataset) => {
    setMenu(false);
    if (kind === "blocks") {
      const next = blockSeries(d, settings.series);
      if (next) setSeries([...settings.series, next]);
      return;
    }
    const model = settings.series.find((x): x is SwathBlockSeries => x.kind === "blocks");
    const modelData = model ? datasets.find((x) => x.id === model.modelId) : undefined;
    const blockName = model && modelData ? blockMeta(modelData).attributes.find((a) => a.key === model.field)?.name ?? null : null;
    const component = matchComponent(d, blockName);
    setSeries([...settings.series, sampleSeries(d, settings.series, component ? `comp:${component}` : "")]);
  };
  const fileName = swath.name;
  const saveCsv = () => {
    if (!result) return;
    const head = ["№", "От, м", "До, м", "Центр, м", ...result.series.flatMap((s) => [`${s.name}: среднее`, `${s.name}: число`, `${s.name}: ${s.weight_name}`])];
    const rows = [];
    for (let k = range[0]; k <= range[1]; k++) {
      rows.push([k + 1, axisValue(result, result.start + k * result.width), axisValue(result, result.start + (k + 1) * result.width), axisValue(result, result.centres[k]),
        ...result.series.flatMap((s) => [s.mean[k], s.count[k], s.weights[k]])]);
    }
    download(`${fileName}.csv`, new Blob([toCsv(head, rows)], { type: "text/csv" }));
  };

  return (
    <div className="swath-panel">
      <form className="hist-settings swath-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Полосы</legend>
          <div className="seg" role="group" aria-label="Направление полос">
            {(Object.keys(DIRECTION_TITLES) as SwathDirection[]).map((d) => (
              <button key={d} type="button" className={settings.direction === d ? "active" : ""} aria-pressed={settings.direction === d} onClick={() => set({ direction: d, range: null })}>
                {d === "azimuth" ? "Азимут" : d.toUpperCase()}
              </button>
            ))}
          </div>
          {settings.direction === "azimuth" && (
            <label>Азимут направления, °
              <input value={settings.azimuth} onChange={(e) => set({ azimuth: e.target.value, range: null })} inputMode="decimal" aria-label="Азимут" title="По часовой стрелке от севера; полосы — вертикальные слои поперёк направления" />
            </label>
          )}
          <div className="pair">
            <label>Ширина, м
              <input value={settings.width} onChange={(e) => set({ width: e.target.value, range: null })} inputMode="decimal" placeholder={result ? `авто: ${formatNumber(result.width, 5)}` : "авто"} aria-label="Ширина полосы" />
            </label>
            <label title="Через эту координату вдоль направления проходит граница между полосами">Привязка
              <input value={settings.anchor} onChange={(e) => set({ anchor: e.target.value, range: null })} inputMode="decimal" placeholder="от края" aria-label="Привязка полос" />
            </label>
          </div>
          {result && fits && (
            <p className="muted small">
              {result.count} полос по {formatNumber(result.width, 5)} м{result.block ? ` (блок ${formatNumber(result.block, 5)} м)` : ""}. Блок и проба относятся к полосе по своему центру.
            </p>
          )}
        </fieldset>
        <fieldset>
          <legend>Ряды</legend>
          {settings.series.map((s, i) => (
            <SeriesCard
              key={s.id} s={s} index={i} all={settings.series} datasets={datasets} refreshKey={refreshKey}
              onChange={(next) => setSeries(settings.series.map((x) => (x.id === s.id ? next : x)))}
              onMove={(by) => {
                const next = [...settings.series];
                const [item] = next.splice(i, 1);
                next.splice(i + by, 0, item);
                setSeries(next);
              }}
              onRemove={() => setSeries(settings.series.filter((x) => x.id !== s.id))}
            />
          ))}
          <div className="swath-add">
            <button type="button" onClick={() => setMenu((m) => !m)} disabled={settings.series.length >= 12 || (!models.length && !sets.length)} aria-expanded={menu}>
              <Icon name="plus" size={13} /> Добавить ряд
            </button>
            {menu && (
              <div className="swath-add-menu" role="menu">
                {models.map((d) => <button key={d.id} type="button" role="menuitem" onClick={() => add("blocks", d)}><Icon name="blocks" size={13} /> {d.name}</button>)}
                {sets.map((d) => <button key={d.id} type="button" role="menuitem" onClick={() => add("samples", d)}><Icon name="drillholes" size={13} /> {d.name} · пробы</button>)}
              </div>
            )}
          </div>
        </fieldset>
        <fieldset>
          <legend>3D-вид</legend>
          <label className="check"><input type="checkbox" checked={props.shown3d} onChange={(e) => props.onShow3d(e.target.checked)} /> Полосы в 3D</label>
          <label className="check"><input type="checkbox" checked={settings.chart3d} disabled={!props.shown3d} onChange={(e) => set({ chart3d: e.target.checked })} /> График над полосами</label>
          <label>Высота графика: {Math.round(settings.height3d * 100)} % размера кубоида
            <input type="range" min={0.1} max={1} step={0.05} value={settings.height3d} disabled={!props.shown3d || !settings.chart3d} onChange={(e) => set({ height3d: Number(e.target.value) })} aria-label="Высота графика в 3D" />
          </label>
        </fieldset>
        <div className="hist-export">
          <button type="button" disabled={!result} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!result} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!result} onClick={saveCsv}>CSV</button>
        </div>
      </form>
      <div className="hist-main swath-main">
        <div className="swath-title">
          <Icon name="swath" size={15} /> <strong>{swath.name}</strong>
          {state?.busy && <span className="muted small">расчёт…</span>}
          <span className="spacer" />
          {saved.length > 1 && (
            <select value={swath.id} onChange={(e) => props.onOpen(Number(e.target.value))} aria-label="Открыть другой Swath plot">
              {saved.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          )}
          <button type="button" onClick={props.onNew}><Icon name="plus" size={13} /> Новый</button>
        </div>
        {problem && <div className="alert error small">{problem}</div>}
        {!problem && state?.error && <div className="alert error small">{state.error}</div>}
        {!problem && !state?.error && !result && <p className="muted small">Расчёт…</p>}
        {result && fits && (
          <div className={state?.busy || problem ? "busy" : ""}>
            <SwathChart ref={svgRef} result={result} settings={settings} hover={hover} onHover={hoverTo} />
            {result.count > 1 && <RangeFilter count={result.count} range={range} onChange={(next) => set({ range: next })} />}
            {result.notes.map((note) => <p key={note} className="alert warning small">{note}</p>)}
            <SwathTable result={result} range={range} />
            <p className="muted small hist-notes">{result.series.map(seriesNote).join(" ")}</p>
          </div>
        )}
      </div>
    </div>
  );
}
