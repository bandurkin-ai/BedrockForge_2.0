/**
 * The parts of the window «Перекрёстная проверка» (ТЗ 8.8.5): the summary, the table by pages with the sort, the scatter plot
 * «истинное — оценка» (line 1:1 and the regression) and the histogram of the errors — the charts of Э13 (`charts/scatter`,
 * `charts/histogram`) over the data of the core. A gap is «—», never 0.
 */
import { useMemo } from "react";
import { BRAND } from "../../charts/column/classes";
import { HistogramChart } from "../../charts/histogram/HistogramChart";
import { ScatterChart } from "../../charts/scatter/ScatterChart";
import css from "../../shell/PropsParts.module.css";
import { useSel } from "./hooks";
import { COLUMNS, cellText, histogramMarks, histogramResult, numberText, pageRows, pagesOf, scatterResult, sortRows, statusLine, summaryFacts, TABLE_SIZES } from "./xvalModel";
import mod from "./XvalWindow.module.css";
import { isPartial, setSize, setSort, setTablePage, xvalStore, type XvalMeta } from "./xvalState";

export function Summary({ meta }: { meta: XvalMeta }) {
  const facts = summaryFacts(meta.summary);
  return (
    <>
      <dl className={mod.facts} data-testid="xval-summary">
        {facts.map((f) => (
          <div key={f.key} className={mod.fact} title={f.hint} data-testid={`xval-fact-${f.key}`}>
            <dt>{f.title}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className={mod.status} data-testid="xval-statuses">
        По статусам: {statusLine(meta.summary.by_status)}
      </p>
      <details className={mod.readme}>
        <summary>Как читать сводку</summary>
        <ul>
          {facts.map((f) => (
            <li key={f.key}>
              <b>{f.title}.</b> {f.hint}
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

export function Table() {
  const s = useSel(xvalStore, (x) => x);
  const sorted = useMemo(() => sortRows(s.rows, s.sort), [s.rows, s.sort]);
  const pages = pagesOf(sorted.length, s.size);
  const page = Math.min(s.tablePage, pages);
  const shown = pageRows(sorted, page, s.size);
  if (!s.rows.length) return <p className={mod.empty}>В таблице нет строк.</p>;
  return (
    <>
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="xval-table" data-rows={s.rows.length}>
          <thead>
            <tr>
              {COLUMNS.map((c) => {
                const on = s.sort?.key === c.key;
                return (
                  <th key={c.key} className={c.kind === "text" ? undefined : mod.num} aria-sort={on ? (s.sort?.dir === "asc" ? "ascending" : "descending") : "none"} data-testid={`xval-th-${c.key}`}>
                    <button type="button" title={`${c.hint}. Нажмите, чтобы упорядочить`} onClick={() => setSort(c.key)}>
                      {c.title}
                      {on ? (s.sort?.dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.i} data-skipped={r.status !== 0} data-testid="xval-row">
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.kind === "text" ? undefined : mod.num} title={c.key === "status" ? `код ${r.status}` : undefined}>
                    {cellText(r, c.key)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={mod.pager}>
        <button type="button" className={css.btn} disabled={page <= 1} aria-label="Предыдущая страница" data-testid="xval-prev" onClick={() => setTablePage(page - 1)}>
          ‹
        </button>
        <span data-testid="xval-page">
          Страница {page} из {pages}
        </span>
        <button type="button" className={css.btn} disabled={page >= pages} aria-label="Следующая страница" data-testid="xval-next" onClick={() => setTablePage(page + 1)}>
          ›
        </button>
        <span className={mod.grow} />
        <label>
          Строк на странице{" "}
          <select value={s.size} aria-label="Строк на странице" data-testid="xval-size" onChange={(e) => setSize(Number(e.target.value))}>
            {TABLE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}

export function Scatter({ meta }: { meta: XvalMeta }) {
  const rows = useSel(xvalStore, (x) => x.rows);
  const partial = useSel(xvalStore, (x) => isPartial(x));
  const result = useMemo(() => scatterResult(rows, meta.summary), [rows, meta.summary]);
  if (!result) return <p className={mod.empty}>Нет пар «оценка — истинное» для графика.</p>;
  const fit = result.fits.ols;
  return (
    <div className={mod.plot} data-testid="xval-scatter" data-points={result.n}>
      <ScatterChart result={result} kx="linear" ky="linear" regression={fit ? "ols" : "none"} diagonal color={BRAND.cobalt} xLabel="Оценка" yLabel="Истинное" />
      <p className={mod.caption}>
        Прямая 1:1 — идеал, где оценка равна истинному. Вторая линия — регрессия истинного на оценку (наклон {numberText(meta.summary.slope)}).
        {partial ? ` На графике первые ${result.n.toLocaleString("ru-RU")} проб из ${meta.total.toLocaleString("ru-RU")}.` : ""}
      </p>
    </div>
  );
}

export function Errors({ meta }: { meta: XvalMeta }) {
  const result = useMemo(() => histogramResult(meta.histogram, meta.summary), [meta]);
  if (!result) return <p className={mod.empty}>Гистограмма ошибок пуста: ядро не вернуло интервалов.</p>;
  return (
    <div className={mod.plot} data-testid="xval-histogram" data-bins={result.bins.counts.length}>
      <HistogramChart result={result} y="count" cumulative={false} marks={histogramMarks(meta.summary)} color={BRAND.cobalt} xLabel="Ошибка (оценка − истинное)" />
      <p className={mod.caption}>Ошибка — оценка минус истинное. Пики справа от нуля — завышение, слева — занижение; хорошая оценка даёт узкий пик у нуля.</p>
    </div>
  );
}
