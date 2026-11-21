/**
 * The common frame of a chart window (ADR 0019 §11): settings on the left (they fold), the chart, under it the
 * statistics and counters, the export panel. The floating window itself (title «‹имя графика› · ‹набор›»,
 * drag, size, maximize) is the shell's (`shell/WindowLayer.tsx`). While a calculation is on its way the
 * previous chart stays, dimmed, with «Расчёт…»; a failure of the core is shown over the dimmed chart.
 */
import { useState, type ReactNode } from "react";
import css from "./core/Chart.module.css";

export function ChartWindow({ settings, toolbar, chart, footer, busy, error, empty, readOnly, note }: {
  settings: ReactNode;
  toolbar?: ReactNode;
  /** the chart; null — nothing computed yet */
  chart: ReactNode | null;
  footer?: ReactNode;
  busy?: boolean;
  error?: string | null;
  /** text instead of the chart while there is none */
  empty?: string;
  /** a project open only for reading: the chart is computed and drawn, not saved */
  readOnly?: boolean;
  /** a line about saving (read-only project, the chart deleted, a failed save) */
  note?: string | null;
}) {
  const [folded, setFolded] = useState(false);
  return (
    <div className={css.window} data-testid="chart-window">
      {folded ? (
        <div className={css.collapsed}>
          <button type="button" className={css.fold} title="Показать настройки" aria-label="Показать настройки" onClick={() => setFolded(false)}>
            »
          </button>
        </div>
      ) : (
        <form className={css.settings} onSubmit={(e) => e.preventDefault()} aria-label="Настройки графика">
          <button type="button" className={css.fold} title="Свернуть настройки" aria-label="Свернуть настройки" onClick={() => setFolded(true)}>
            «
          </button>
          {settings}
        </form>
      )}
      <div className={css.main}>
        {toolbar}
        {readOnly && !note && <p className={css.muted}>Проект открыт только для чтения: график не сохраняется.</p>}
        {note && (
          <p className={css.muted} data-testid="chart-note">
            {note}
          </p>
        )}
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
        {chart ? (
          <div className={`${css.plot} ${busy || error ? css.busy : ""}`} aria-busy={busy || undefined}>
            {chart}
            {busy && <span className={css.status}>Расчёт…</span>}
          </div>
        ) : (
          !error && <p className={css.muted}>{busy ? "Расчёт…" : (empty ?? "Нет данных для графика.")}</p>
        )}
        {chart && footer}
      </div>
    </div>
  );
}
