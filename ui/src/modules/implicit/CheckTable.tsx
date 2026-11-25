/**
 * The tab «Отклонения» of the window «Проверка по скважинам» (ТЗ 7.35 Д-32 п. 1): the summary, the groups of the model, the table of the
 * points of the data (the sort, the pages, the highlight of the residuals beyond the threshold, a click — to the point in 3D) and the
 * file. The reads are in `CheckState.ts`. A gap is «—», never 0.
 */
import { num } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { CHECK_SIZES, CHECK_SORTS, CHECK_SORT_TITLES, type CheckSort } from "./checkApi";
import { cellText, COLUMNS, depthText, numText, pagesOf, placeText } from "./checkModel";
import { applyHighlight, checkStore, pickRow, saveCsv, setHighlightText, setQuery, toggleShown } from "./CheckState";
import mod from "./Check.module.css";
import { useSel } from "./hooks";

const GROUP_TYPES: Record<string, string> = { domain: "домен", shell: "оболочка", vein: "жила", expand: "расширение", fault: "разлом" };

export function CheckTable() {
  const s = useSel(checkStore, (x) => x);
  const p = s.page;
  const busy = s.phase === "loading";
  if (!p) return null;
  const pages = pagesOf(p.total, p.size);
  const groupLabel = (g: (typeof p.groups)[number]) => `${g.name}${g.threshold !== null ? ` ≥ ${numText(g.threshold)}` : ""}${g.wing !== null ? `, крыло ${g.wing}` : ""}`;
  return (
    <>
      <div className={mod.bar} style={{ padding: 0, border: 0 }}>
        <label>
          <span>Группа</span>
          <select className={mod.select} value={s.query.group ?? ""} disabled={busy} aria-label="Группа" data-testid="chk-group" onChange={(e) => void setQuery({ group: e.target.value === "" ? null : Number(e.target.value) })}>
            <option value="">Все группы</option>
            {[...new Map(p.groups.filter((g) => g.group !== null).map((g) => [g.group, g])).values()].map((g) => (
              <option key={g.group as number} value={g.group as number}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Сортировка</span>
          <select className={mod.select} value={s.query.sort} disabled={busy} aria-label="Сортировка" data-testid="chk-sort" onChange={(e) => void setQuery({ sort: e.target.value as CheckSort })}>
            {CHECK_SORTS.map((k) => (
              <option key={k} value={k}>
                {CHECK_SORT_TITLES[k]}
              </option>
            ))}
          </select>
        </label>
        <label title="Точки, у которых модуль невязки больше этого числа метров, подсвечены в таблице и в 3D. Пусто — 0,1·h модели.">
          <span>Порог подсветки, м</span>
          <input
            className={mod.select}
            value={s.highlightText}
            placeholder={p.highlight !== null ? `по умолчанию ${numText(p.highlight)}` : "по умолчанию 0,1·h"}
            aria-label="Порог подсветки, м"
            data-testid="chk-highlight"
            onChange={(e) => setHighlightText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void applyHighlight()}
            onBlur={() => void applyHighlight()}
          />
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.btn} title="Сохранить все строки выборки (группа и сортировка таблицы) в файл CSV" disabled={s.saving} data-testid="chk-csv" onClick={() => void saveCsv()}>
            Сохранить CSV
          </button>
          <button type="button" className={css.btn} title={s.shown ? "Убрать точки этой страницы из 3D-окна" : "Показать точки этой страницы в 3D-окне: цвет — невязка"} data-testid="chk-3d" onClick={() => toggleShown()}>
            {s.shown ? "Скрыть в 3D" : "Показать в 3D"}
          </button>
        </div>
      </div>
      <dl className={mod.facts} data-testid="chk-facts">
        <div className={mod.fact} title="Сколько точек данных в выборке">
          <dt>Точек</dt>
          <dd data-testid="chk-n">{num(p.n, 0)}</dd>
        </div>
        <div className={mod.fact} title="Наибольший модуль невязки f модели − f данных в выборке">
          <dt>Наибольшая невязка</dt>
          <dd data-testid="chk-max">{numText(p.max_abs_residual)}</dd>
        </div>
        <div className={mod.fact} title="Точки, у которых модуль невязки больше порога подсветки">
          <dt>Выше порога</dt>
          <dd data-testid="chk-over">{num(p.over, 0)}</dd>
        </div>
        <div className={mod.fact} title="Порог подсветки, м">
          <dt>Порог, м</dt>
          <dd>{numText(p.highlight)}</dd>
        </div>
      </dl>
      {p.warnings.map((w) => (
        <p key={w.code + w.message} className={mod.banner} role="status" data-testid="chk-warning">
          {w.message} {w.hint}
        </p>
      ))}
      <details className={mod.readme}>
        <summary>Группы модели: {p.groups.length}</summary>
        <ul data-testid="chk-groups">
          {p.groups.map((g, k) => (
            <li key={k}>
              <b>{groupLabel(g)}</b> ({GROUP_TYPES[g.type] ?? g.type}): точек {num(g.points, 0)}, наибольшая невязка {numText(g.max_abs_residual)}
              {g.wireframes.length ? `, каркасов ${g.wireframes.length}` : ", без каркаса"}
            </li>
          ))}
        </ul>
      </details>
      {s.note && (
        <p className={css.note} role="status" data-testid="chk-note">
          {s.note}
        </p>
      )}
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="chk-table" data-rows={p.rows.length}>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.num ? mod.num : undefined} title={c.hint} scope="col">
                  {c.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {p.rows.map((r) => (
              <tr key={r.i} data-over={r.over} data-picked={s.picked === r.i} title={`${placeText(r)}${depthText(r) ? `; ${depthText(r)}` : ""}. Нажмите, чтобы перейти к точке в 3D.`} data-testid={`chk-row-${r.i}`} onClick={() => pickRow(r)}>
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.num ? mod.num : undefined}>
                    {cellText(r, c.key)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!p.rows.length && <p className={mod.empty}>В этой выборке нет точек данных.</p>}
      </div>
      <div className={mod.pager}>
        <button type="button" className={css.btn} disabled={busy || p.page <= 1} data-testid="chk-prev" onClick={() => void setQuery({ page: p.page - 1 })}>
          ‹ Назад
        </button>
        <span data-testid="chk-page">
          Страница {num(p.page, 0)} из {num(pages, 0)}
        </span>
        <button type="button" className={css.btn} disabled={busy || p.page >= pages} data-testid="chk-next" onClick={() => void setQuery({ page: p.page + 1 })}>
          Вперёд ›
        </button>
        <span className={mod.grow} />
        <label>
          Строк на странице{" "}
          <select value={p.size} disabled={busy} aria-label="Строк на странице" data-testid="chk-size" onChange={(e) => void setQuery({ size: Number(e.target.value) })}>
            {[...new Set([...CHECK_SIZES, p.size])]
              .sort((a, b) => a - b)
              .map((n) => (
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
