/**
 * The window «Переход к блокам» (ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решения 22–24, вопрос № 111): the cell of the report asked for, the blocks, the volume and the tonnes of the cell (they are the
 * numbers of the cell of the table), the warning when the report is out of date, the notes of the core, «Сбросить» (the temporary filter of the 3D), the table of the blocks of the cell by pages
 * of 1000, «Сохранить страницу CSV…» (the save window of the application) and the inspector of Д-30 for a block. Floating, not modal. `renderDrillWindow` is for the window layer of the shell.
 */
import type { ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import type { FloatWin } from "../../shell/windows";
import { cellText, cellTitle, clearFilter, DRILL_TITLE, DRILL_WINDOW, drillStore, inspectRow, loadPage, pagesOf, reapplyFilter, saveCsv } from "./drill";
import { BlocksTable } from "./DrillParts";
import { fmtCount, warningText } from "./format";
import { useSel } from "./ReportParts";
import mod from "./DrillWindow.module.css";

export function DrillWindow() {
  const app = useApp();
  const st = useSel(drillStore, (s) => s);
  const { target, answer, rows } = st;
  const why = app.registry.reason("estimate.inspect");
  const total = rows.data?.total ?? 0;
  const pages = pagesOf(total);
  const busy = rows.load === "loading";
  return (
    <div className={mod.window} data-testid="drill-window">
      <h4 className={mod.head} data-testid="drill-title">
        {target ? `${target.reportName}: ${cellTitle(target.label, target.body)}` : DRILL_TITLE}
      </h4>
      {st.load === "loading" && <p className={css.note}>Ядро отбирает блоки ячейки…</p>}
      {st.error && (
        <p className={css.error} role="alert" data-testid="drill-error">
          {st.error.message} {st.error.hint}
        </p>
      )}
      {answer && (
        <>
          <dl className={mod.facts} data-testid="drill-facts">
            <div className={mod.fact}>
              <dt>Блоков</dt>
              <dd data-testid="drill-blocks">{fmtCount(answer.cell.blocks)}</dd>
            </div>
            <div className={mod.fact}>
              <dt>Объём, м³</dt>
              <dd data-testid="drill-volume">{cellText(answer.cell.volume)}</dd>
            </div>
            <div className={mod.fact}>
              <dt>Тоннаж, т</dt>
              <dd data-testid="drill-tonnes">{cellText(answer.cell.tonnes)}</dd>
            </div>
          </dl>
          {answer.stale && (
            <p className={mod.warn} role="alert" data-testid="drill-stale">
              Отчёт устарел{answer.stale_reason ? ` (${answer.stale_reason})` : ""}: набор блоков может отличаться от показанного в таблице. Пересчитайте отчёт.
            </p>
          )}
          {(answer.warnings.length > 0 || st.notes.length > 0) && (
            <ul className={mod.notes} data-testid="drill-warnings">
              {answer.warnings.map((w, i) => (
                <li key={`w${i}`} className={mod.warn}>
                  {warningText(w)}
                </li>
              ))}
              {st.notes.map((n, i) => (
                <li key={`n${i}`} className={mod.warn}>
                  {n}
                </li>
              ))}
            </ul>
          )}
          <div className={mod.bar}>
            {st.applied ? (
              <button type="button" className={css.btn} title="Вернуть в 3D прежний ограничитель модели" data-testid="drill-reset" onClick={clearFilter}>
                Сбросить
              </button>
            ) : (
              <button type="button" className={css.btn} title="Показать в 3D только блоки ячейки (временный фильтр, в библиотеку фильтров не попадает)" data-testid="drill-apply" onClick={reapplyFilter}>
                Показать в 3D
              </button>
            )}
            <span className={mod.grow} />
            <span className={mod.pager}>
              <button type="button" className={css.btn} disabled={busy || st.page <= 1} aria-label="Предыдущая страница" data-testid="drill-prev" onClick={() => void loadPage(st.page - 1)}>
                ◀
              </button>
              <span data-testid="drill-page">
                Страница {st.page} из {pages}
                {rows.data ? `, блоков ${fmtCount(total)}` : ""}
              </span>
              <button type="button" className={css.btn} disabled={busy || st.page >= pages} aria-label="Следующая страница" data-testid="drill-next" onClick={() => void loadPage(st.page + 1)}>
                ▶
              </button>
            </span>
            <button type="button" className={css.btn} disabled={busy || !rows.data || !rows.data.rows.length} title="Блоки этой страницы в файл CSV (разделитель «;», десятичная запятая)" data-testid="drill-csv" onClick={() => void saveCsv()}>
              Сохранить страницу CSV…
            </button>
          </div>
          {st.csvNote && (
            <p className={st.csvNote.error ? css.error : css.note} role={st.csvNote.error ? "alert" : "status"} data-testid="drill-csv-note">
              {st.csvNote.text}
            </p>
          )}
          {st.inspectNote && (
            <p className={css.error} role="alert" data-testid="drill-inspect-note">
              {st.inspectNote}
            </p>
          )}
          {rows.error && (
            <p className={css.error} role="alert" data-testid="drill-rows-error">
              {rows.error.message} {rows.error.hint}
            </p>
          )}
          {busy && !rows.data && <p className={css.note}>Читаются блоки ячейки…</p>}
          {rows.data && <BlocksTable rows={rows.data.rows} inspectWhy={why} onInspect={(r) => void inspectRow(app, r)} />}
        </>
      )}
    </div>
  );
}

/** The window for the window layer of the shell (`shell/Shell.tsx`); null — not this window. */
export function renderDrillWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== DRILL_WINDOW) return null;
  return <DrillWindow key={w.id} />;
}
