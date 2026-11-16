import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "./AppContext";
import { commandContext } from "./store";
import { Icon } from "./Icon";
import { formatShortcut } from "./keys";
import { searchPalette, type PaletteRow } from "./paletteSearch";
import { buildRibbon, flattenRibbon, itemReason } from "./ribbonConfig";
import { effectiveShortcuts, functionKey } from "./shortcuts";
import { useStore } from "./useStore";
import css from "./Palette.module.css";

/** Command palette Ctrl+K (ТЗ 7.0.7): ↑/↓, Enter — run, Esc — close; at most 60 results. */
export function Palette() {
  const app = useApp();
  const initial = useStore(app.palette, (p) => p.query);
  if (initial === null) return null;
  return <PaletteBox initial={initial} />;
}

function PaletteBox({ initial }: { initial: string }) {
  const app = useApp();
  const s = useStore(app.store, (x) => x);
  const [query, setQuery] = useState(initial);
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(() => flattenRibbon(buildRibbon(s.workspace, s.view)), [s.workspace, s.view]);
  const keys = useMemo(() => effectiveShortcuts(items, s.shortcuts), [items, s.shortcuts]);
  const rows = useMemo(() => searchPalette(query, items, []), [query, items]);
  const ctx = commandContext(s);
  const close = () => app.palette.set({ query: null });

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => setSel(0), [query]);

  const reasonOf = (r: PaletteRow) => (r.kind === "command" ? itemReason(r.item, ctx, (id) => app.registry.reason(id, ctx)) : null);
  const runRow = (r: PaletteRow | undefined) => {
    if (!r || reasonOf(r)) return;
    close();
    if (r.kind === "command") void app.run(r.item.command, r.item.args ?? {});
    else void app.run("hole.select", { hole_id: r.holeId });
  };

  return (
    <div className={css.backdrop} onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className={css.box} role="dialog" aria-label="Палитра команд">
        <input
          ref={inputRef}
          className={css.input}
          value={query}
          placeholder="Команда или «скв » и номер скважины"
          aria-label="Поиск команды"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
            else if (e.key === "ArrowDown") setSel((i) => Math.min(rows.length - 1, i + 1));
            else if (e.key === "ArrowUp") setSel((i) => Math.max(0, i - 1));
            else if (e.key === "Enter") {
              // The text may be ahead of the last render (typed and Enter at once): search what is in the field.
              const text = e.currentTarget.value;
              runRow(text === query ? rows[Math.min(sel, rows.length - 1)] : searchPalette(text, items, [])[0]);
            }
            else return;
            e.preventDefault();
          }}
        />
        <div className={css.list} role="listbox">
          {rows.length === 0 && <div className={css.none}>{query.toLowerCase().startsWith("скв ") ? "Скважины не найдены: откройте проект с наборами скважин" : "Ничего не найдено"}</div>}
          {rows.map((r, i) => {
            const reason = reasonOf(r);
            const key = r.kind === "command" ? keys[functionKey(r.item)] : "";
            return (
              <div
                key={r.kind === "command" ? `${r.item.command}:${JSON.stringify(r.item.args ?? {})}` : `h:${r.holeId}`}
                role="option"
                aria-selected={i === sel}
                aria-disabled={reason ? true : undefined}
                title={reason ? `Недоступно: ${reason}` : undefined}
                className={[css.row, i === sel && css.selected, reason && css.disabled].filter(Boolean).join(" ")}
                onMouseMove={() => i !== sel && setSel(i)} // not mouseenter: a list appearing under a resting cursor must not move the choice
                onClick={() => runRow(r)}
              >
                {r.kind === "command" ? (
                  <>
                    <Icon name={r.item.icon} size={15} />
                    <span>{r.item.label}</span>
                    <small>{r.item.group}</small>
                    <span className={css.key}>{key ? formatShortcut(key) : ""}</span>
                  </>
                ) : (
                  <>
                    <Icon name="hole" size={15} />
                    <span>{r.holeId}</span>
                    <small>{r.setName}</small>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
