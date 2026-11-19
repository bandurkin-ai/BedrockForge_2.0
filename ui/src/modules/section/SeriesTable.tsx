/**
 * The table of the sections of a series in «Менеджер серий» (ТЗ 8.1; ADR 0016 §4): number, name, s, the window
 * of each section — own windows are edited in place (Enter or leaving the field writes them, «×» returns the
 * window of the series). 100 rows a page; a click on a row makes the series active at that section.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { viewStore } from "../scene/bind";
import { faultText, seriesApi, seriesFault, type SeriesRow, type SeriesSectionRow } from "./seriesApi";
import { activateSeries } from "./seriesActions";
import { fmt, overridesWith, readWindow } from "./seriesForm";
import { ensureTable, loadSeries, putSeries, seriesStore } from "./seriesState";
import { sectionOf } from "./state";

export const TABLE_PAGE = 100;

function WindowCell({ value, testid, onCommit }: { value: number; testid: string; onCommit: (text: string) => void }) {
  const [text, setText] = useState(fmt(value));
  useEffect(() => setText(fmt(value)), [value]);
  const commit = () => text !== fmt(value) && onCommit(text);
  return <input className={css.input} style={{ width: 64 }} inputMode="decimal" data-testid={testid} value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />;
}

export function SeriesTable({ row }: { row: SeriesRow }) {
  const app = useApp();
  const table = useStore(seriesStore, (s) => (s.tables[row.id]?.version === row.version ? s.tables[row.id] : null));
  const active = useStore(viewStore, (k) => sectionOf(k).series ?? null);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    ensureTable(row.id).catch((e: unknown) => setError(faultText(seriesFault(e))));
  }, [row.id, row.version]);
  if (error && !table) return <p className={css.error}>{error}</p>;
  if (!table) return <p className={css.note}>Таблица разрезов читается…</p>;
  const own = row.params.overrides ?? {};
  const pages = Math.max(1, Math.ceil(table.sections.length / TABLE_PAGE));
  const rows = table.sections.slice(page * TABLE_PAGE, (page + 1) * TABLE_PAGE);
  const save = async (s: SeriesSectionRow, win: { towards: number; away: number } | null) => {
    try {
      const next = await seriesApi.patch(row.id, row.version, { params: { overrides: overridesWith(row.params, s.k, win) } });
      putSeries(next);
      setError(null);
    } catch (e) {
      const f = seriesFault(e);
      if (f.status === 409) await loadSeries();
      setError(f.status === 409 ? "Серию только что изменили в другом окне: таблица обновлена — повторите правку окна." : faultText(f));
    }
  };
  const edit = (s: SeriesSectionRow, side: "towards" | "away") => (text: string) => {
    const w = readWindow(side === "towards" ? text : fmt(s.towards), side === "away" ? text : fmt(s.away));
    if ("error" in w) return setError(w.error);
    void save(s, w);
  };
  return (
    <div className={css.group} data-testid="series-table">
      <div className={css.tableWrap}>
        <table className={css.table}>
          <thead>
            <tr>
              <th>№</th>
              <th>Имя</th>
              <th>s, м</th>
              <th title="Окно к наблюдателю">к себе, м</th>
              <th title="Окно за плоскостью">от себя, м</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const mine = own[String(s.k)];
              const current = active?.id === row.id && active.k === s.k;
              return (
                <tr key={s.k} className={current ? css.picked : undefined} data-testid={`series-section-${s.k}`} onDoubleClick={() => app.store.set({ statusNote: activateSeries(row.id, s.k) })} title="Двойной щелчок — перейти к разрезу">
                  <td className={css.rowNo}>{s.k + 1}</td>
                  <td>{s.name}</td>
                  <td>{fmt(s.s)}</td>
                  <td>
                    <WindowCell value={s.towards} testid={`series-towards-${s.k}`} onCommit={edit(s, "towards")} />
                  </td>
                  <td>
                    <WindowCell value={s.away} testid={`series-away-${s.k}`} onCommit={edit(s, "away")} />
                  </td>
                  <td>
                    {mine && (
                      <button type="button" className={css.iconBtn} title="Вернуть окно серии" aria-label="Вернуть окно серии" data-testid={`series-own-reset-${s.k}`} onClick={() => void save(s, null)}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className={css.pager}>
          <button type="button" className={css.btn} disabled={page === 0} onClick={() => setPage(page - 1)}>
            Назад
          </button>
          <span>
            Разрезы {page * TABLE_PAGE + 1}–{Math.min((page + 1) * TABLE_PAGE, table.sections.length)} из {table.sections.length}
          </span>
          <button type="button" className={css.btn} disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
            Далее
          </button>
        </div>
      )}
      {error && (
        <p className={css.error} role="alert" data-testid="series-table-error">
          {error}
        </p>
      )}
    </div>
  );
}
