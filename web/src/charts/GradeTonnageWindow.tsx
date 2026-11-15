import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { blockMeta, blockReport, type BlockReport, type BlockReportParams, type Dataset } from "../api";
import { formatNumber } from "../stats/chartMath";
import { unitTitle } from "../shell/BlockModelPanel";
import { download, savePng, saveSvg, toCsv } from "./exportChart";

export interface ReportSettings {
  density: string | null;
  densityValue: string;
  grades: string[];
  cutoffField: string | null;
  cutoffs: string;
  filterField: string | null;
  filterLabels: string[];
  groupBy: string | null;
}

/**
 * Cut-offs from text: "0 0.3; 0,5 1 2" -> [0, 0.3, 0.5, 1, 2]. A lone "0,5" is a decimal comma;
 * "0.3,0.5" is a list.
 */
export function parseCutoffs(text: string): number[] {
  const tokens = text.split(/[\s;]+/).filter(Boolean)
    .flatMap((t) => (/^-?\d+,\d+$/.test(t) ? [t.replace(",", ".")] : t.split(",")));
  const values = tokens.map((t) => (t.trim() === "" ? NaN : Number(t.trim()))).filter((v) => Number.isFinite(v));
  return [...new Set(values)].sort((a, b) => a - b);
}

export function defaultReport(d: Dataset): ReportSettings {
  const meta = blockMeta(d);
  const r = meta.report ?? {};
  const grades = r.grades ?? [];
  const cutoffField = r.cutoff_field ?? grades.find((k) => meta.attributes.find((a) => a.key === k)?.unit === "g/t") ?? grades[0] ?? null;
  return {
    density: r.density ?? null,
    densityValue: r.density_value ? String(r.density_value) : r.density ? "" : "2.7",
    grades,
    cutoffField,
    cutoffs: (r.cutoffs?.length ? r.cutoffs : [0, 0.3, 0.5, 1, 2]).join(" "),
    filterField: null,
    filterLabels: [],
    groupBy: null,
  };
}

export function toReportParams(s: ReportSettings): BlockReportParams {
  const value = Number(s.densityValue.replace(",", "."));
  return {
    density: s.density,
    density_value: s.density ? null : Number.isFinite(value) && value > 0 ? value : null,
    grades: s.grades,
    cutoff_field: s.cutoffField,
    cutoffs: s.cutoffField ? parseCutoffs(s.cutoffs) : [],
    filters: s.filterField ? [{ key: s.filterField, labels: s.filterLabels }] : [],
    group_by: s.groupBy,
  };
}

const mt = (t: number) => t / 1e6;
/** Large numbers with digit groups, small ones with 4 significant digits. */
const big = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) : formatNumber(v, 4));

/** Grade–tonnage report of a block model: table by cut-off, grade–tonnage curve, CSV. */
export function GradeTonnageWindow({ dataset }: { dataset: Dataset }) {
  const meta = blockMeta(dataset);
  const [s, setS] = useState<ReportSettings>(() => defaultReport(dataset));
  const [result, setResult] = useState<BlockReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<ReportSettings>) => setS((p) => ({ ...p, ...patch }));
  const numbers = meta.attributes.filter((a) => a.kind === "number");
  const categories = meta.attributes.filter((a) => a.kind === "category");
  const params = useMemo(() => toReportParams(s), [s]);
  const key = JSON.stringify(params);

  useEffect(() => {
    if (!params.density && !params.density_value) {
      setError("Укажите поле плотности или постоянную плотность");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      blockReport(dataset.id, params, controller.signal)
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
  }, [dataset.id, key]);

  const filterAttr = categories.find((a) => a.key === s.filterField);
  const total = result?.rows.filter((r) => r.group === null) ?? [];
  const groups = result?.rows.filter((r) => r.group !== null) ?? [];
  const fileName = `Содержание-тоннаж ${dataset.name}`;

  function saveCsv() {
    if (!result) return;
    const head = ["Группа", "Борт", "Блоков", "Объём, м³", "Тоннаж, т"];
    for (const g of result.grades) head.push(`${g.name}, ${unitTitle(g.unit as never)}`, `${g.name}: металл`);
    const rows = result.rows.map((r) => [r.group ?? "все", r.cutoff, r.blocks, r.volume, r.tonnes,
      ...result.grades.flatMap((g) => [r.grades[g.key]?.grade ?? null, r.grades[g.key]?.metal?.value ?? null])]);
    download(`${fileName}.csv`, new Blob([toCsv(head, rows)], { type: "text/csv" }));
  }

  return (
    <div className="hist-window">
      <form className="hist-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Тоннаж</legend>
          <label>Плотность
            <select value={s.density ?? ""} onChange={(e) => set({ density: e.target.value || null })} aria-label="Поле плотности">
              <option value="">постоянная…</option>
              {numbers.map((a) => <option key={a.key} value={a.key}>{a.name} (ср. {formatNumber(a.stats.mean ?? NaN, 3)})</option>)}
            </select>
          </label>
          {!s.density && (
            <label>т/м³<input value={s.densityValue} onChange={(e) => set({ densityValue: e.target.value })} inputMode="decimal" aria-label="Постоянная плотность" /></label>
          )}
        </fieldset>
        <fieldset>
          <legend>Содержания</legend>
          <div className="dendro-fields">
            {numbers.map((a) => (
              <label key={a.key} className="check">
                <input type="checkbox" checked={s.grades.includes(a.key)}
                  onChange={(e) => set({ grades: e.target.checked ? [...s.grades, a.key] : s.grades.filter((k) => k !== a.key) })} />
                {a.name}{a.unit ? `, ${unitTitle(a.unit)}` : ""}
              </label>
            ))}
          </div>
          <p className="muted small">Металл считается по единицам поля (г/т → унции, % → тонны); единицы меняются в свойствах модели.</p>
        </fieldset>
        <fieldset>
          <legend>Борта</legend>
          <label>Поле борта
            <select value={s.cutoffField ?? ""} onChange={(e) => set({ cutoffField: e.target.value || null })} aria-label="Поле борта">
              <option value="">без бортов (итог)</option>
              {numbers.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
            </select>
          </label>
          {s.cutoffField && (
            <label>Значения (≥)<input value={s.cutoffs} onChange={(e) => set({ cutoffs: e.target.value })} aria-label="Бортовые содержания" title="Через пробел или точку с запятой" /></label>
          )}
        </fieldset>
        <fieldset>
          <legend>Отбор и группы</legend>
          <label>Только блоки
            <select value={s.filterField ?? ""} onChange={(e) => set({ filterField: e.target.value || null, filterLabels: categories.find((a) => a.key === e.target.value)?.labels ?? [] })} aria-label="Отбор по полю">
              <option value="">все</option>
              {categories.map((a) => <option key={a.key} value={a.key}>по {a.name}</option>)}
            </select>
          </label>
          {filterAttr && (
            <div className="dendro-fields">
              {(filterAttr.labels ?? []).map((label) => (
                <label key={label} className="check">
                  <input type="checkbox" checked={s.filterLabels.includes(label)}
                    onChange={(e) => set({ filterLabels: e.target.checked ? [...s.filterLabels, label] : s.filterLabels.filter((l) => l !== label) })} /> {label}
                </label>
              ))}
            </div>
          )}
          <label>Группировать
            <select value={s.groupBy ?? ""} onChange={(e) => set({ groupBy: e.target.value || null })} aria-label="Группировать по">
              <option value="">нет</option>
              {meta.attributes.map((a) => <option key={a.key} value={a.key}>по {a.name}</option>)}
            </select>
          </label>
        </fieldset>
        <div className="hist-export">
          <button type="button" disabled={!result} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!result} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!result} onClick={saveCsv}>CSV</button>
        </div>
      </form>
      <div className="hist-main">
        {error && <div className="alert error small">{error}</div>}
        {!result && !error && <p className="muted small">Расчёт…</p>}
        {result && (
          <div className={busy ? "busy" : ""}>
            {total.length > 1 && <GradeTonnageChart ref={svgRef} rows={total} grade={result.grades.find((g) => g.key === s.cutoffField) ?? result.grades[0] ?? null} />}
            <ReportTable result={result} rows={total} />
            {groups.length > 0 && (
              <>
                <h4 className="gt-group-title">По группам</h4>
                <ReportTable result={result} rows={groups} grouped />
              </>
            )}
            <p className="muted small hist-notes">
              Тоннаж = объём блока × плотность ({typeof result.density === "number" ? `${formatNumber(result.density, 3)} т/м³` : result.density}); содержания взвешены по тоннажу; борт — значение ≥ указанного.
              {result.excluded_no_density > 0 && ` Блоков без плотности (не учтены): ${result.excluded_no_density}.`}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function ReportTable({ result, rows, grouped }: { result: BlockReport; rows: BlockReport["rows"]; grouped?: boolean }) {
  return (
    <table className="gt-table">
      <thead>
        <tr>
          {grouped && <th>Группа</th>}
          <th>Борт</th><th>Млн т</th><th>Тыс. м³</th>
          {result.grades.map((g) => <th key={g.key}>{g.name}, {unitTitle(g.unit as never)}</th>)}
          {result.grades.map((g) => {
            const unit = rows.find((r) => r.grades[g.key]?.metal)?.grades[g.key]?.metal?.unit;
            return unit ? <th key={`m${g.key}`}>{g.name}, {unit}</th> : null;
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {grouped && <td>{r.group}</td>}
            <td className="num">{r.cutoff === null ? "—" : formatNumber(r.cutoff)}</td>
            <td className="num">{formatNumber(mt(r.tonnes), 4)}</td>
            <td className="num">{big(r.volume / 1000)}</td>
            {result.grades.map((g) => <td key={g.key} className="num">{r.grades[g.key]?.grade === null ? "—" : formatNumber(r.grades[g.key].grade!, 4)}</td>)}
            {result.grades.map((g) => (r.grades[g.key]?.metal ? <td key={`m${g.key}`} className="num">{big(r.grades[g.key].metal!.value)}</td> : null))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}


/** Tonnage (bars) and grade (line) against the cut-off. */
const GradeTonnageChart = forwardRef<SVGSVGElement, { rows: BlockReport["rows"]; grade: { key: string; name: string; unit: string | null } | null }>(
  function GradeTonnageChart({ rows, grade }, ref) {
    const W = 560;
    const H = 260;
    const M = { left: 60, right: 60, top: 16, bottom: 40 };
    const xs = rows.map((r) => r.cutoff ?? 0);
    const tmax = Math.max(...rows.map((r) => mt(r.tonnes)), 1e-9) * 1.1;
    const gvals = grade ? rows.map((r) => r.grades[grade.key]?.grade ?? NaN) : [];
    const gmax = Math.max(...gvals.filter(Number.isFinite), 1e-9) * 1.1;
    const n = rows.length;
    const band = (W - M.left - M.right) / n;
    const x = (i: number) => M.left + band * (i + 0.5);
    const yt = (v: number) => H - M.bottom - (v / tmax) * (H - M.top - M.bottom);
    const yg = (v: number) => H - M.bottom - (v / gmax) * (H - M.top - M.bottom);
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    return (
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="hist-chart gt-chart" role="img" aria-label="Кривая содержание–тоннаж">
        <rect x={0} y={0} width={W} height={H} className="chart-bg" />
        {ticks.map((t) => (
          <g key={t}>
            <line className="chart-grid" x1={M.left} x2={W - M.right} y1={yt(t * tmax)} y2={yt(t * tmax)} />
            <text className="chart-tick" x={M.left - 6} y={yt(t * tmax) + 4} textAnchor="end">{formatNumber(t * tmax, 3)}</text>
            {grade && <text className="chart-tick" x={W - M.right + 6} y={yg(t * gmax) + 4}>{formatNumber(t * gmax, 3)}</text>}
          </g>
        ))}
        {rows.map((r, i) => (
          <g key={i}>
            <rect className="gt-bar" x={x(i) - band * 0.35} width={band * 0.7} y={yt(mt(r.tonnes))} height={H - M.bottom - yt(mt(r.tonnes))}>
              <title>{`Борт ${formatNumber(xs[i])}: ${formatNumber(mt(r.tonnes), 4)} млн т`}</title>
            </rect>
            <text className="chart-tick" x={x(i)} y={H - M.bottom + 15} textAnchor="middle">{formatNumber(xs[i])}</text>
          </g>
        ))}
        {grade && <polyline className="gt-line" fill="none" points={gvals.map((v, i) => `${x(i)},${yg(Number.isFinite(v) ? v : 0)}`).join(" ")} />}
        {grade && gvals.map((v, i) => Number.isFinite(v) && <circle key={i} className="gt-point" cx={x(i)} cy={yg(v)} r={3}><title>{`${grade.name} ${formatNumber(v, 4)}`}</title></circle>)}
        <text className="chart-axis" x={(M.left + W - M.right) / 2} y={H - 6} textAnchor="middle">Бортовое содержание</text>
        <text className="chart-axis" transform={`translate(14 ${(M.top + H - M.bottom) / 2}) rotate(-90)`} textAnchor="middle">Млн т</text>
        {grade && <text className="chart-axis" transform={`translate(${W - 10} ${(M.top + H - M.bottom) / 2}) rotate(90)`} textAnchor="middle">{grade.name}{grade.unit ? `, ${unitTitle(grade.unit as never)}` : ""}</text>}
      </svg>
    );
  },
);
