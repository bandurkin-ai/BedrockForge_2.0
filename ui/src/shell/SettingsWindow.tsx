import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "./AppContext";
import { matchesQuery } from "./commands";
import { askConfirm, showMessage } from "./dialogs";
import { eventShortcut, formatShortcut, isComplete } from "./keys";
import { buildRibbon, flattenRibbon } from "./ribbonConfig";
import { assign, defaultShortcuts, effectiveShortcuts, functionKey, holderOf, isReserved } from "./shortcuts";
import { useStore } from "./useStore";
import css from "./SettingsWindow.module.css";

/** «Настройка» (ТЗ 7.0.6): own hotkeys; general settings arrive with projects (Э2). */
export function SettingsWindow({ tab: initialTab }: { tab?: string }) {
  const [tab, setTab] = useState(initialTab === "general" ? "general" : "shortcuts");
  return (
    <div className={css.wrap}>
      <div className={css.tabs} role="tablist">
        <button type="button" role="tab" aria-selected={tab === "shortcuts"} className={`${css.tab} ${tab === "shortcuts" ? css.tabActive : ""}`} onClick={() => setTab("shortcuts")}>
          Быстрые клавиши
        </button>
        <button type="button" role="tab" aria-selected={tab === "general"} className={`${css.tab} ${tab === "general" ? css.tabActive : ""}`} onClick={() => setTab("general")}>
          Общие
        </button>
      </div>
      {tab === "shortcuts" ? <Shortcuts /> : <div className={css.note}>Папка данных проектов и число исполнителей фоновых заданий настраиваются с этапа Э2 (проекты и задания).</div>}
    </div>
  );
}

function Shortcuts() {
  const app = useApp();
  const s = useStore(app.store, (x) => x);
  const [query, setQuery] = useState("");
  const [capturing, setCapturing] = useState<string | null>(null);
  const items = useMemo(() => {
    const seen = new Set<string>();
    return flattenRibbon(buildRibbon(s.workspace, s.view)).filter((i) => {
      const k = functionKey(i);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [s.workspace, s.view]);
  const defaults = useMemo(() => defaultShortcuts(items), [items]);
  const current = useMemo(() => effectiveShortcuts(items, s.shortcuts), [items, s.shortcuts]);
  const label = (key: string) => items.find((i) => functionKey(i) === key)?.label ?? key;
  const setOverrides = (next: typeof s.shortcuts) => app.store.set({ shortcuts: next });

  const give = async (key: string, shortcut: string) => {
    if (shortcut && isReserved(shortcut)) {
      await showMessage({ title: "Сочетание занято системой", message: `${formatShortcut(shortcut)} зарезервировано Windows или программой и не назначается.` });
      return;
    }
    const other = shortcut ? holderOf(current, shortcut, key) : null;
    if (other && !(await askConfirm({ title: "Сочетание уже занято", message: `${formatShortcut(shortcut)} назначено функции «${label(other)}». Переназначить? У той функции сочетание будет снято.`, okLabel: "Переназначить" }))) return;
    setOverrides(assign(app.store.get().shortcuts, defaults, current, key, shortcut));
  };

  const giveRef = useRef(give);
  giveRef.current = give;
  useEffect(() => {
    if (!capturing) return;
    app.store.set({ modal: true }); // ribbon hotkeys are off while a shortcut is being captured
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const sc = eventShortcut(e);
      if (sc === "escape") return setCapturing(null);
      if (!isComplete(sc)) return;
      setCapturing(null);
      void giveRef.current(capturing, sc);
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      app.store.set({ modal: false });
    };
  }, [capturing, app]);

  const changed = Object.keys(s.shortcuts).length;
  const rows = items.filter((i) => !query || matchesQuery(`${i.label} ${i.group} ${formatShortcut(current[functionKey(i)] ?? "")}`, query));

  return (
    <>
      <div className={css.tools}>
        <input className={css.search} placeholder="Поиск по функции или сочетанию" aria-label="Поиск по функции или сочетанию" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button
          type="button"
          className={css.btn}
          disabled={!changed}
          onClick={async () => {
            if (await askConfirm({ title: "Сбросить", message: `Вернуть сочетания по умолчанию? Изменённых: ${changed}`, okLabel: "Сбросить", danger: true })) setOverrides({});
          }}
        >
          Сбросить
        </button>
      </div>
      <div className={css.list}>
        <table className={css.table} aria-label="Быстрые клавиши">
          <thead>
            <tr>
              <th>Функция</th>
              <th>Сочетание</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => {
              const key = functionKey(i);
              const isChanged = key in s.shortcuts;
              return (
                <tr key={key}>
                  <td>
                    {i.label} <small style={{ color: "var(--text-faint)" }}>{i.group}</small>
                  </td>
                  <td>
                    {capturing === key ? <span className={css.capture}>нажмите сочетание…</span> : current[key] ? formatShortcut(current[key]) : "—"}
                    {isChanged && <span className={css.changed}>· изменено</span>}
                  </td>
                  <td className={css.actions}>
                    <button type="button" className={css.link} onClick={() => setCapturing(key)}>
                      Назначить
                    </button>
                    {current[key] && (
                      <button type="button" className={css.link} onClick={() => void give(key, "")}>
                        Убрать сочетание
                      </button>
                    )}
                    {isChanged && (
                      <button type="button" className={css.link} onClick={() => void give(key, defaults[key] ?? "")}>
                        Вернуть по умолчанию
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
