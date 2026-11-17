import { useApp } from "../../shell/AppContext";
import { api, CoreError } from "../../shell/api";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, askText, showMessage } from "../../shell/dialogs";
import { useState } from "react";
import { toggleExpanded } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import { useStore } from "../../shell/useStore";
import { DisplayRows } from "../downhole/DisplaysTree";
import { libraryStore } from "../downhole/library";
import { loadSets, selectSet, tablesStore, type SetInfo } from "./state";
import css from "./Tables.module.css";

/** The selection of a click in flight: the double click that follows waits for its tables. */
let selecting: Promise<void> = Promise.resolve();

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** Sets under «Скважины» in the data tree (ТЗ 7.1, 7.10, 7.35 Д-03): a click — the active set, a double click — its
 * table, the arrow — its displays; menu — tables, displays, rename, delete. */
export function SetsTree() {
  const app = useApp();
  const s = useStore(tablesStore, (x) => x);
  const charts = useStore(libraryStore, (x) => x.charts);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  if (!s.sets.length) return <div className={css.treeNone}>Нет объектов</div>;
  const open = async (set: SetInfo, kind?: string) => {
    await selecting;
    if (set.id !== tablesStore.get().setId) await selectSet(set.id);
    await app.run("table.open", kind ? { kind } : { kind: tablesStore.get().table ?? "collar" });
  };
  const rename = async (set: SetInfo) => {
    const name = await askText({ title: "Переименовать набор", label: "Название", initial: set.name, okLabel: "Переименовать" });
    if (!name || name === set.name) return;
    try {
      await api.patch(`/drillholes/sets/${set.id}`, { name });
      await loadSets();
    } catch (e) {
      await showMessage({ title: "Набор не переименован", message: errText(e) });
    }
  };
  const remove = async (set: SetInfo) => {
    const ok = await askConfirm({ title: "Удалить набор", message: `Набор «${set.name}» удаляется со скважинами, таблицами и историей импорта. Отменить удаление нельзя.`, okLabel: "Удалить", danger: true });
    if (!ok) return;
    try {
      await api.del(`/drillholes/sets/${set.id}`);
      await loadSets();
    } catch (e) {
      await showMessage({ title: "Набор не удалён", message: errText(e) });
    }
  };
  const select = (set: SetInfo) => {
    if (set.id !== tablesStore.get().setId) selecting = selectSet(set.id).catch(() => undefined);
  };
  return (
    <div role="group" aria-label="Наборы скважин">
      {s.sets.map((set) => {
        const hasDisplays = charts.some((c) => c.set_id === set.id && c.kind === "striplog");
        const open_ = !collapsed.includes(String(set.id));
        return (
          <div key={set.id}>
            <TreeRow
              label={set.name}
              ariaLabel={`${set.name}, ${set.holes.toLocaleString("ru-RU")} скв.`}
              level={2}
              expanded={hasDisplays ? open_ : undefined}
              onToggle={() => setCollapsed((c) => toggleExpanded(c, String(set.id)))}
              selected={set.id === s.setId}
              onSelect={() => select(set)}
              onActivate={() => void open(set)}
              title={`${set.name}: ${set.holes.toLocaleString("ru-RU")} скв. Щелчок — активный набор, двойной щелчок — таблица`}
              testId={`tree-set-${set.id}`}
              after={<span className={css.count}>{set.holes.toLocaleString("ru-RU")}</span>}
              onContextMenu={(e) => {
                e.preventDefault();
                openMenu(e.clientX, e.clientY, [
                  { label: "Устья", run: () => void open(set, "collar") },
                  { label: "Инклинометрия", run: () => void open(set, "survey") },
                  { label: "Пробы", run: () => void open(set, "assay") },
                  { separator: true, label: "" },
                  { label: "Содержания…", run: () => void app.run("downhole.open", { form: "grade", set_id: set.id }) },
                  { label: "Литология…", run: () => void app.run("downhole.open", { form: "lith", set_id: set.id }) },
                  { label: "События…", run: () => void app.run("downhole.open", { form: "event", set_id: set.id }) },
                  { separator: true, label: "" },
                  { label: "Переименовать…", run: () => void rename(set) },
                  { label: "Удалить…", run: () => void remove(set) },
                ]);
              }}
            />
            {hasDisplays && open_ && <DisplayRows setId={set.id} />}
          </div>
        );
      })}
    </div>
  );
}
