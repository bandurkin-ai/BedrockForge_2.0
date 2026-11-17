import { useEffect, useState } from "react";
import { useStore } from "../../shell/useStore";
import { cellText, loadEdits, tablesStore, undoEdit, type EditRecord } from "./state";
import css from "./Tables.module.css";

const ACTIONS: Record<string, string> = { update: "Правка", insert: "Новая строка", delete: "Удаление", column: "Колонка", append: "Объединение" };

function describe(e: EditRecord, names: Record<string, string>): string {
  if (e.action === "append") return `добавлено строк: ${String((e.changes as { rows?: number }).rows ?? "")}`;
  return Object.entries(e.changes)
    .filter(([k]) => k !== "kind" && k !== "expression")
    .map(([k, v]) => {
      const [before, after] = Array.isArray(v) ? v : [undefined, v];
      const show = (x: unknown) => (x === null || x === undefined || x === "" ? "пусто" : typeof x === "number" ? cellText(x, "number") : String(x));
      return e.action === "update" || e.action === "column" ? `${names[k] ?? k}: ${show(before)} → ${show(after)}` : `${names[k] ?? k} = ${show(e.action === "delete" ? before : after)}`;
    })
    .join("; ");
}

/** Журнал правок (ТЗ 7.10): who, what, before → after; «Отменить» on each (a group is undone at once). */
export function EditLog({ onClose }: { onClose: () => void }) {
  const s = useStore(tablesStore, (x) => x);
  const [edits, setEdits] = useState<EditRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void loadEdits().then(setEdits, (e: unknown) => setError(String(e)));
  }, [s.version, s.setId]);
  const names = Object.fromEntries(s.columns.map((c) => [c.key, c.name]));
  const tabNames = Object.fromEntries(s.tabs.map((t) => [t.table, t.name]));
  const shown = edits.filter((e, i) => i === 0 || e.group !== edits[i - 1].group || e.action === "column");
  return (
    <aside className={css.log} aria-label="Журнал правок">
      <header>
        <b>Журнал правок</b>
        <button type="button" className={css.link} onClick={onClose}>
          Закрыть
        </button>
      </header>
      {error && <div className={css.error}>{error}</div>}
      {!edits.length && <div className={css.note}>Правок нет.</div>}
      <ul>
        {shown.map((e) => {
          const group = edits.filter((x) => x.group === e.group).length;
          return (
            <li key={e.id} className={e.undone ? css.undone : ""}>
              <div className={css.note}>
                {new Date(e.at).toLocaleString("ru-RU")} · {e.author ?? "—"} · {tabNames[e.table] ?? e.table}
              </div>
              <div>
                {ACTIONS[e.action] ?? e.action}
                {e.hole ? ` · ${e.hole}` : ""}
                {group > 1 ? ` · строк: ${group}` : ""}: {describe(e, names)}
              </div>
              {!e.undone ? (
                <button type="button" className={css.link} onClick={() => void undoEdit(e.id).then((m) => setError(m))}>
                  Отменить
                </button>
              ) : (
                <span className={css.note}>отменено</span>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
