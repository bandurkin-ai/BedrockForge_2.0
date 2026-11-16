import { useState, type DragEvent } from "react";
import { useApp } from "./AppContext";
import { Icon } from "./Icon";
import { useStore } from "./useStore";
import { moveSection, TREE_SECTIONS, type TreeSection } from "./workspace";
import css from "./DataTree.module.css";
import shell from "./Shell.module.css";

const PLUS: Partial<Record<TreeSection, { command: string; title: string }>> = {
  holes: { command: "import.open", title: "Новый набор и импорт скважин" },
  wireframes: { command: "wireframes.upload", title: "Загрузить каркасы" },
  lines: { command: "lines.upload", title: "Новый слой или загрузить файл: STR, DXF" },
  rasters: { command: "rasters.upload", title: "Загрузить растр" },
  blocks: { command: "omf.import", title: "Загрузить блочную модель" },
  audit: { command: "stats.swath", title: "Новый swath plot" },
};

/** Data tree (ТЗ 7.1): sections collapse and are reordered by dragging the header; objects arrive on later stages. */
export function DataTree() {
  const app = useApp();
  const order = useStore(app.store, (s) => s.workspace.treeOrder);
  const collapsed = useStore(app.store, (s) => s.workspace.treeCollapsed);
  const width = useStore(app.store, (s) => s.workspace.sizes.left);
  const project = useStore(app.store, (s) => s.projectName);
  const [dragging, setDragging] = useState<TreeSection | null>(null);
  const [over, setOver] = useState<TreeSection | null>(null);

  const toggle = (s: TreeSection) =>
    app.store.set((st) => {
      const c = st.workspace.treeCollapsed;
      return { workspace: { ...st.workspace, treeCollapsed: c.includes(s) ? c.filter((x) => x !== s) : [...c, s] } };
    });
  const drop = (target: TreeSection) => (e: DragEvent) => {
    e.preventDefault();
    if (dragging && dragging !== target) app.store.set((st) => ({ workspace: { ...st.workspace, treeOrder: moveSection(st.workspace.treeOrder, dragging, target) } }));
    setDragging(null);
    setOver(null);
  };

  return (
    <aside className={shell.left} style={{ width }} aria-label="Дерево данных">
      <div className={css.title}>Дерево данных</div>
      <div className={css.tree} role="tree">
        {!project && (
          <div className={css.demo}>
            Проект пуст.
            <br />
            <button type="button" onClick={() => void app.run("project.demo")} title={app.registry.reason("project.demo") ?? "48 скважин, пробы, аудит, каркасы рудного тела"}>
              Загрузить демо-проект
            </button>
          </div>
        )}
        {order.map((s) => {
          const isCollapsed = collapsed.includes(s);
          const plus = PLUS[s];
          return (
            <div key={s} className={css.section} role="treeitem" aria-expanded={!isCollapsed} aria-label={TREE_SECTIONS[s]}>
              <div
                className={`${css.head} ${over === s ? css.dragOver : ""}`}
                draggable
                onDragStart={(e) => {
                  setDragging(s);
                  e.dataTransfer.setData("application/x-bf-section", s);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragOver={(e) => {
                  if (!dragging) return;
                  e.preventDefault();
                  setOver(s);
                }}
                onDragLeave={() => setOver(null)}
                onDrop={drop(s)}
                onDragEnd={() => {
                  setDragging(null);
                  setOver(null);
                }}
                onClick={() => toggle(s)}
              >
                <span className={`${css.chev} ${isCollapsed ? css.collapsed : ""}`}>
                  <Icon name="chevronDown" size={14} />
                </span>
                <span className={css.name}>{TREE_SECTIONS[s]}</span>
                {plus && (
                  <button
                    type="button"
                    className={css.plus}
                    title={`${plus.title}${app.registry.reason(plus.command) ? `\nНедоступно: ${app.registry.reason(plus.command)}` : ""}`}
                    aria-label={plus.title}
                    onClick={(e) => {
                      e.stopPropagation();
                      void app.run(plus.command);
                    }}
                  >
                    <Icon name="plus" size={13} />
                  </button>
                )}
              </div>
              {!isCollapsed && <div className={css.none}>Нет объектов</div>}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
