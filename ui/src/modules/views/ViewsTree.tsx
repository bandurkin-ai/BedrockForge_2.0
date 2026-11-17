/**
 * The «Виды» section of the data tree (ТЗ 7.35 Д-09): a click on a view applies it, the menu overwrites it with
 * the current view, renames it, moves it to a folder, deletes it. Folders («А/Б») expand by their arrow.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, askText } from "../../shell/dialogs";
import { buildFolders, toggleExpanded, type FolderNode } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import { useStore } from "../../shell/useStore";
import css from "../tables/Tables.module.css";
import { viewsStore, type ViewRow } from "./state";

export function ViewsTree() {
  const app = useApp();
  const list = useStore(viewsStore, (s) => s.views);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [last, setLast] = useState<number | null>(null);
  const root = useMemo(() => buildFolders(list.map((v) => ({ key: String(v.id), name: v.name, folder: v.folder ?? "" }))), [list]);
  if (!list.length) return <div className={css.treeNone}>Нет сохранённых видов: «+» сохраняет текущий</div>;
  const byKey = new Map(list.map((v) => [String(v.id), v]));

  const menu = (v: ViewRow) => (e: React.MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, [
      { label: "Применить", run: () => void app.run("views.apply", { view_id: v.id }) },
      {
        label: "Перезаписать текущим",
        run: () =>
          void askConfirm({ title: "Перезаписать вид", message: `Заменить «${v.name}» текущим видом 3D-окна?`, okLabel: "Перезаписать" }).then(
            (ok) => ok && void app.run("views.update", { view_id: v.id }),
          ),
      },
      {
        label: "Переименовать…",
        run: () =>
          void askText({ title: "Переименовать вид", label: "Название", initial: v.name, okLabel: "Переименовать" }).then(
            (name) => name && name !== v.name && void app.run("views.rename", { view_id: v.id, name }),
          ),
      },
      {
        label: "В папку…",
        run: () =>
          void askText({ title: "Вид в папку", label: "Папка (например «Разрезы/Север»; пусто — в корень)", initial: v.folder ?? "", okLabel: "Переложить" }).then(
            (folder) => folder !== null && void app.run("views.folder", { view_id: v.id, folder }),
          ),
      },
      { separator: true, label: "" },
      {
        label: "Удалить",
        run: () =>
          void askConfirm({ title: "Удалить вид", message: `Вид «${v.name}» удаляется из проекта.`, okLabel: "Удалить", danger: true }).then(
            (ok) => ok && void app.run("views.delete", { view_id: v.id }),
          ),
      },
    ]);
  };

  const rows = (node: FolderNode, level: number): React.ReactNode[] => [
    ...node.folders.flatMap((f) => {
      const open = !collapsed.includes(f.path);
      return [
        <TreeRow key={`f:${f.path}`} label={f.name} level={level} expanded={open} onToggle={() => setCollapsed((c) => toggleExpanded(c, f.path))} glyph={<span aria-hidden>📁 </span>} />,
        ...(open ? rows(f, level + 1) : []),
      ];
    }),
    ...node.objects.map((o) => {
      const v = byKey.get(o.key)!;
      return (
        <TreeRow
          key={o.key}
          label={v.name}
          level={level}
          selected={last === v.id}
          testId={`view-${v.id}`}
          title={`${v.name}: щелчок — применить вид`}
          onSelect={() => {
            setLast(v.id);
            void app.run("views.apply", { view_id: v.id });
          }}
          onContextMenu={menu(v)}
        />
      );
    }),
  ];

  return (
    <div role="group" aria-label="Сохранённые виды">
      {rows(root, 2)}
    </div>
  );
}
