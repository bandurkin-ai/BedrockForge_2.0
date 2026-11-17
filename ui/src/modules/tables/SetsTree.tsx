import { useApp } from "../../shell/AppContext";
import { api, CoreError } from "../../shell/api";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, askText, showMessage } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { loadSets, selectSet, tablesStore, type SetInfo } from "./state";
import css from "./Tables.module.css";

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** Sets under «Скважины» in the data tree (ТЗ 7.1, 7.10): click — active set and its table; menu — tables, rename, delete. */
export function SetsTree() {
  const app = useApp();
  const s = useStore(tablesStore, (x) => x);
  if (!s.sets.length) return <div className={css.treeNone}>Нет объектов</div>;
  const open = async (set: SetInfo, kind?: string) => {
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
  return (
    <ul className={css.treeList} role="group">
      {s.sets.map((set) => (
        <li key={set.id}>
          <button
            type="button"
            role="treeitem"
            aria-selected={set.id === s.setId}
            className={set.id === s.setId ? css.treeItemActive : css.treeItem}
            title={`${set.name}: ${set.holes.toLocaleString("ru-RU")} скв.`}
            onClick={() => void open(set)}
            onContextMenu={(e) => {
              e.preventDefault();
              openMenu(e.clientX, e.clientY, [
                { label: "Устья", run: () => void open(set, "collar") },
                { label: "Инклинометрия", run: () => void open(set, "survey") },
                { label: "Пробы", run: () => void open(set, "assay") },
                { label: "Переименовать…", run: () => void rename(set) },
                { label: "Удалить…", run: () => void remove(set) },
              ]);
            }}
          >
            {set.name} <span className={css.count}>{set.holes.toLocaleString("ru-RU")}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
