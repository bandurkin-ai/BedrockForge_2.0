import { useEffect, useMemo, useRef, useState } from "react";
import type { Hole } from "../api";
import { formatShortcut, matchesQuery } from "./commands";
import { Icon, type IconName } from "./icons";
import type { RibbonItem } from "./ribbonConfig";

interface Entry {
  key: string;
  label: string;
  group: string;
  icon: IconName;
  shortcut?: string;
  disabled?: boolean;
  item: RibbonItem;
}

const MAX_RESULTS = 60;
const HOLE_PREFIX = "скв ";

/** Ctrl+K: every ribbon command plus «скв <номер>» to jump to a hole. */
export function CommandPalette({
  items,
  holes,
  initialQuery,
  onRun,
  onClose,
}: {
  items: (RibbonItem & { group: string })[];
  holes: Hole[];
  initialQuery: string;
  onRun: (item: RibbonItem) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [index, setIndex] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  const entries = useMemo<Entry[]>(() => {
    const q = query.trim();
    const holeQuery = query.toLowerCase().startsWith(HOLE_PREFIX) ? query.slice(HOLE_PREFIX.length).trim() : null;
    const commands: Entry[] =
      holeQuery !== null
        ? []
        : items
            .filter((i) => matchesQuery(`${i.label} ${i.group} ${i.hint ?? ""}`, q))
            .map((i) => ({ key: `${i.command}:${JSON.stringify(i.args ?? {})}`, label: i.label, group: i.group, icon: i.icon, shortcut: i.shortcut, disabled: i.disabled, item: i }));
    const holeText = holeQuery ?? q;
    const found: Entry[] =
      holeQuery === null && q.length < 2
        ? []
        : holes
            .filter((h) => h.hole_id.toLowerCase().includes(holeText.toLowerCase()))
            .slice(0, MAX_RESULTS)
            .map((h) => ({
              key: `hole:${h.hole_id}`,
              label: h.hole_id,
              group: "Скважина",
              icon: "hole",
              item: { command: "hole.select", args: { hole_id: h.hole_id }, label: h.hole_id, icon: "hole" },
            }));
    return [...commands, ...found].slice(0, MAX_RESULTS);
  }, [items, holes, query]);

  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    listRef.current?.children[index]?.scrollIntoView({ block: "nearest" });
  }, [index]);

  const run = (entry: Entry | undefined) => {
    if (!entry || entry.disabled) return;
    onClose();
    onRun(entry.item);
  };

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-label="Поиск команд" onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Icon name="search" size={16} />
          <input
            autoFocus
            value={query}
            placeholder="Команда или «скв <номер>»…"
            aria-label="Поиск команд"
            onChange={(e) => setQuery(e.target.value)}
            onFocus={(e) => e.target.setSelectionRange(e.target.value.length, e.target.value.length)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, entries.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); run(entries[index]); }
              else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
            }}
          />
        </div>
        <ul className="palette-list" ref={listRef} role="listbox">
          {entries.map((entry, i) => (
            <li key={entry.key} role="option" aria-selected={i === index}>
              <button
                className={i === index ? "active" : ""}
                disabled={entry.disabled}
                onMouseEnter={() => setIndex(i)}
                onClick={() => run(entry)}
              >
                <Icon name={entry.icon} size={16} />
                <span className="palette-label">{entry.label}</span>
                <span className="palette-group">{entry.group}</span>
                {entry.shortcut && <kbd>{formatShortcut(entry.shortcut)}</kbd>}
              </button>
            </li>
          ))}
          {entries.length === 0 && <li className="palette-empty">Ничего не найдено</li>}
        </ul>
      </div>
    </div>
  );
}
