import { useEffect, useMemo, useState } from "react";
import { eventShortcut, formatShortcut, matchesQuery } from "./commands";
import { askConfirm } from "./dialogs";
import { Icon } from "./icons";
import { flattenRibbon, type RibbonTab } from "./ribbonConfig";
import { assign, BROWSER_SHORTCUTS, functionKey, isComplete, type ShortcutOverrides } from "./shortcuts";

/** Settings of the application. For now: hotkeys of every function of the ribbon. */
export function SettingsPanel({ tabs, defaults, overrides, onChange }: {
  /** the ribbon with the user's hotkeys */
  tabs: RibbonTab[];
  /** the ribbon as built in (its hotkeys are the defaults) */
  defaults: RibbonTab[];
  overrides: ShortcutOverrides;
  onChange: (next: ShortcutOverrides) => void;
}) {
  const [query, setQuery] = useState("");
  const [capture, setCapture] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);

  const items = useMemo(() => {
    const seen = new Set<string>();
    return flattenRibbon(tabs).filter((i) => {
      const k = functionKey(i);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [tabs]);
  const byDefault = useMemo(() => Object.fromEntries(flattenRibbon(defaults).map((i) => [functionKey(i), i.shortcut ?? ""])), [defaults]);
  const current = useMemo(() => Object.fromEntries(items.map((i) => [functionKey(i), i.shortcut ?? ""])), [items]);
  const title = (key: string) => items.find((i) => functionKey(i) === key)?.label ?? key;

  const set = (key: string, shortcut: string) => {
    onChange(assign(overrides, byDefault, current, key, shortcut));
    setNote({ text: shortcut ? `«${title(key)}»: ${formatShortcut(shortcut)}` : `«${title(key)}»: без сочетания` });
  };

  // Waiting for a shortcut: the keys go here, not to the application's hotkeys.
  useEffect(() => {
    if (!capture) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopImmediatePropagation();
      const shortcut = eventShortcut(e);
      if (!isComplete(shortcut)) return;
      if (shortcut === "escape") return setCapture(null);
      const key = capture;
      setCapture(null);
      if (shortcut === "backspace" || shortcut === "delete") return set(key, "");
      if (BROWSER_SHORTCUTS.has(shortcut)) {
        setNote({ text: `${formatShortcut(shortcut)} занято браузером: страница его не получит. Выберите другое сочетание.`, error: true });
        return;
      }
      const holder = Object.entries(current).find(([k, sc]) => k !== key && sc === shortcut)?.[0];
      if (!holder) return set(key, shortcut);
      void askConfirm({
        title: "Сочетание уже занято",
        message: `${formatShortcut(shortcut)} сейчас вызывает «${title(holder)}». Отдать его функции «${title(key)}»? У «${title(holder)}» сочетания не останется.`,
        ok: "Переназначить",
      }).then((ok) => ok && set(key, shortcut));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [capture, current, overrides]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = items.filter((i) => !query.trim() || matchesQuery(`${i.label} ${i.group} ${i.hint ?? ""} ${i.shortcut ?? ""}`, query));
  const groups = [...new Set(shown.map((i) => i.group))];
  const changed = Object.keys(overrides).length;

  return (
    <div className="settings-panel">
      <section className="pp-card">
        <div className="pp-head">
          <Icon name="command" size={16} />
          <strong className="grow">Быстрые клавиши</strong>
          <button disabled={!changed} title="Вернуть все сочетания по умолчанию"
            onClick={() => void askConfirm({ title: "Сбросить быстрые клавиши", message: `Вернуть сочетания по умолчанию? Изменённых: ${changed}.`, ok: "Сбросить" })
              .then((ok) => { if (ok) { onChange({}); setNote({ text: "Все сочетания — по умолчанию" }); } })}>
            Сбросить всё
          </button>
        </div>
        <span className="muted small">
          «Назначить» и нажмите сочетание: одна клавиша, или с Ctrl, Alt, Shift. Delete — убрать сочетание, Esc — отмена.
          Одиночные клавиши не срабатывают при вводе текста в поле, сочетания с Ctrl — срабатывают везде. Настройка хранится в этом браузере.
        </span>
        <input value={query} placeholder="поиск функции или сочетания" aria-label="Поиск функции" onChange={(e) => setQuery(e.target.value)} />
        <div className="settings-scroll">
          <table className="pp-table settings-keys">
            <thead><tr><th>Функция</th><th>Сочетание</th><th /></tr></thead>
            {groups.map((g) => (
              <tbody key={g}>
                <tr className="settings-group"><td colSpan={3}>{g}</td></tr>
                {shown.filter((i) => i.group === g).map((i) => {
                  const key = functionKey(i);
                  const own = key in overrides;
                  const waiting = capture === key;
                  return (
                    <tr key={key} className={waiting ? "current" : ""}>
                      <td title={i.hint}><span className="settings-fn"><Icon name={i.icon} size={14} /> {i.label}</span></td>
                      <td>
                        {waiting ? <span className="settings-wait">нажмите сочетание…</span>
                          : i.shortcut ? <kbd>{formatShortcut(i.shortcut)}</kbd> : <span className="muted small">—</span>}
                        {own && !waiting && <span className="muted small" title={byDefault[key] ? `по умолчанию ${formatShortcut(byDefault[key])}` : "по умолчанию без сочетания"}> · изменено</span>}
                      </td>
                      <td className="pp-actions">
                        <button onClick={() => setCapture(waiting ? null : key)}>{waiting ? "Отмена" : "Назначить"}</button>
                        <button className="icon" disabled={!i.shortcut} onClick={() => set(key, "")} title="Убрать сочетание" aria-label={`Убрать сочетание: ${i.label}`}><Icon name="close" size={13} /></button>
                        <button className="icon" disabled={!own} onClick={() => set(key, byDefault[key] ?? "")}
                          title={byDefault[key] ? `Вернуть ${formatShortcut(byDefault[key])}` : "Вернуть по умолчанию (без сочетания)"} aria-label={`По умолчанию: ${i.label}`}>
                          <Icon name="refresh" size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            ))}
            {!shown.length && <tbody><tr><td colSpan={3} className="muted small">Ничего не найдено.</td></tr></tbody>}
          </table>
        </div>
      </section>
      {note && <div className={`alert ${note.error ? "error" : "ok"} small`}>{note.text}</div>}
    </div>
  );
}
