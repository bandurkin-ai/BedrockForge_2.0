/**
 * «Перейти к разрезу» (`Ctrl+G`, ТЗ 8.1.3): the name or the number (from 1) of a section of the active series,
 * suggestions as one types (↑ ↓ choose, Enter goes, Esc — the cross of the window). The move is `series.goto`.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { viewStore } from "../scene/bind";
import { gotoHints, resolveGoto } from "./goto";
import { currentSection, ensureTable, seriesStore } from "./seriesState";

export function GotoWindow({ winId }: { winId: string }) {
  const app = useApp();
  const keys = useStore(viewStore, (k) => k);
  const state = useStore(seriesStore, (s) => s);
  const cur = currentSection(keys, state);
  const table = cur && state.tables[cur.row.id]?.version === cur.row.version ? state.tables[cur.row.id] : null;
  const [text, setText] = useState("");
  const [pick, setPick] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (cur) void ensureTable(cur.row.id).catch(() => undefined);
  }, [cur?.row.id, cur?.row.version]);
  if (!cur) return <p className={css.note}>Нет активной серии разрезов.</p>;
  const hints = table ? gotoHints(table.sections, text) : [];
  const go = async (k: number | null) => {
    const r = await app.registry.execute("series.goto", k === null ? { name: text } : { number: k + 1 });
    if (!r.ok) return setError(r.result);
    closeWindow(winId);
  };
  return (
    <form
      className={css.panel}
      data-testid="series-goto"
      onSubmit={(e) => {
        e.preventDefault();
        const exact = table ? resolveGoto(table.sections, text) : null;
        void go(exact ?? hints[pick]?.k ?? null);
      }}
    >
      <label className={css.field}>
        <span>Имя или номер разреза (1–{cur.steps})</span>
        <input
          className={css.input}
          autoFocus
          data-testid="series-goto-input"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPick(0);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setPick((p) => Math.min(p + 1, hints.length - 1));
            else if (e.key === "ArrowUp") setPick((p) => Math.max(p - 1, 0));
            else return;
            e.preventDefault();
          }}
        />
      </label>
      <div className={css.layers} role="listbox" aria-label="Подсказки">
        {hints.map((h, i) => (
          <div key={h.k} role="option" aria-selected={i === pick} data-testid={`series-goto-hint-${h.k}`} style={{ cursor: "pointer", fontWeight: i === pick ? 600 : undefined }} onClick={() => void go(h.k)}>
            {h.label}
          </div>
        ))}
        {!table && <span className={css.note}>Имена разрезов читаются…</span>}
      </div>
      {error && (
        <p className={css.error} role="alert" data-testid="series-goto-error">
          {error}
        </p>
      )}
      <div className={css.row}>
        <button type="submit" className={css.primary} data-testid="series-goto-go">
          Перейти
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
      </div>
    </form>
  );
}
