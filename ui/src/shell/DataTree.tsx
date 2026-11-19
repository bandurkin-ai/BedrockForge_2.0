import { useState, type DragEvent } from "react";
import { useApp } from "./AppContext";
import { openMenu, type MenuItem } from "./ContextMenu";
import { Icon } from "./Icon";
import { onTreeKeyDown, onTreeMouseDown, TreeRow } from "./TreeRow";
import { DtmTree } from "../modules/dtm/DtmTree";
import { LinesTree } from "../modules/lines/LinesTree";
import { RastersTree } from "../modules/rasters/RastersTree";
import { SetsTree } from "../modules/tables/SetsTree";
import { ViewsTree } from "../modules/views/ViewsTree";
import { SeriesTree } from "../modules/section/SeriesTree";
import { WireframesTree } from "../modules/wireframes/WireframesTree";
import { sectionActions, sectionPlus } from "./treeSections";
import { useStore } from "./useStore";
import { moveSection, TREE_SECTIONS, type TreeSection } from "./workspace";
import css from "./DataTree.module.css";
import shell from "./Shell.module.css";

/** Context menu of a section header (ТЗ 7.0.13, 7.1 «+»): `treeSections.ts` as menu items. */
function sectionMenu(s: TreeSection, reason: (id: string) => string | null, run: (id: string, args?: Record<string, unknown>) => void): MenuItem[] {
  const actions = sectionActions(s);
  if (!actions.length) return [{ label: "Нет действий", reason: "У раздела нет команд" }];
  return actions.map((a) => ({ label: a.label, reason: reason(a.command), run: () => run(a.command, a.args) }));
}

const CONTENT: Partial<Record<TreeSection, () => JSX.Element>> = {
  views: ViewsTree,
  series: SeriesTree,
  holes: SetsTree,
  wireframes: WireframesTree,
  dtm: DtmTree,
  lines: LinesTree,
  rasters: RastersTree,
};

/** Data tree (ТЗ 7.1): sections collapse by their arrow only (Д-03) and are reordered by dragging the header. */
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
      <div
        className={css.tree}
        role="tree"
        aria-label="Разделы и объекты проекта"
        tabIndex={0}
        onKeyDown={onTreeKeyDown}
        onMouseDown={onTreeMouseDown}
        onFocus={(e) => {
          // the container is a Tab stop only while no row has the focus: Shift+Tab from a row leaves the tree
          if (e.target === e.currentTarget) e.currentTarget.querySelector<HTMLElement>("[data-tree-row]")?.focus();
          else e.currentTarget.tabIndex = -1;
        }}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) e.currentTarget.tabIndex = 0;
        }}
      >
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
          const plus = sectionPlus(s);
          const Content = CONTENT[s];
          return (
            <div key={s} className={`${css.section} ${over === s ? css.dragOver : ""}`}>
              <TreeRow
                label={TREE_SECTIONS[s]}
                level={1}
                expanded={!isCollapsed}
                onToggle={() => toggle(s)}
                className={css.head}
                testId={`tree-section-${s}`}
                onContextMenu={(e) => {
                  e.preventDefault();
                  openMenu(e.clientX, e.clientY, sectionMenu(s, (id) => app.registry.reason(id), (id, args) => void app.run(id, args)));
                }}
                drag={{
                  draggable: true,
                  onDragStart: (e) => {
                    setDragging(s);
                    e.dataTransfer.setData("application/x-bf-section", s);
                    e.dataTransfer.effectAllowed = "move";
                  },
                  onDragOver: (e) => {
                    if (!dragging) return;
                    e.preventDefault();
                    setOver(s);
                  },
                  onDragLeave: () => setOver(null),
                  onDrop: drop(s),
                  onDragEnd: () => {
                    setDragging(null);
                    setOver(null);
                  },
                }}
                after={
                  plus && (
                    <button
                      type="button"
                      tabIndex={-1}
                      data-tree-after=""
                      className={css.plus}
                      title={`${plus.title}${plus.command && app.registry.reason(plus.command) ? `\nНедоступно: ${app.registry.reason(plus.command)}` : ""}`}
                      aria-label={plus.title}
                      aria-haspopup={plus.command ? undefined : "menu"}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (plus.command) return void app.run(plus.command, plus.args);
                        // several ways to add: the menu of the section opens under the button
                        const box = e.currentTarget.getBoundingClientRect();
                        openMenu(box.left, box.bottom + 2, sectionMenu(s, (id) => app.registry.reason(id), (id, args) => void app.run(id, args)));
                      }}
                    >
                      <Icon name="plus" size={13} />
                    </button>
                  )
                }
              />
              {!isCollapsed && (Content ? <Content /> : <div className={css.none}>Нет объектов</div>)}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
