/**
 * Shared parts of the object sections of the data tree (ТЗ 7.1; ADR 0011 §5): objects in their folders
 * («А/Б» → nested rows that expand by the arrow), the colour of an object, the buttons at the right of a row
 * and its context menu — all drawn from the actions of `treeActions.ts`.
 */
import { useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { useApp } from "./AppContext";
import { openMenu } from "./ContextMenu";
import { Icon, type IconName } from "./Icon";
import { buildFolders, toggleExpanded, type FolderNode } from "./tree";
import type { TreeAction } from "./treeActions";
import { TreeRow } from "./TreeRow";
import { menuItems, runAction, type LocalAction } from "./treeRun";
import { useRegistryVersion } from "./useStore";
import css from "./DataTree.module.css";

export interface TreeItem {
  id: number;
  name: string;
  folder: string | null;
}

/** Objects of a section in their folders; `row` draws an object at its level. */
export function FolderRows<T extends TreeItem>({ items, level = 2, row }: { items: readonly T[]; level?: number; row: (item: T, level: number) => ReactNode }) {
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const root = useMemo(() => buildFolders(items.map((o) => ({ key: String(o.id), name: o.name, folder: o.folder ?? "" }))), [items]);
  const byKey = new Map(items.map((o) => [String(o.id), o]));
  const rows = (node: FolderNode, at: number): ReactNode[] => [
    ...node.folders.flatMap((f) => {
      const open = !collapsed.includes(f.path);
      return [
        <TreeRow
          key={`f:${f.path}`}
          label={f.name}
          level={at}
          expanded={open}
          onToggle={() => setCollapsed((c) => toggleExpanded(c, f.path))}
          glyph={
            <span className={css.glyph} aria-hidden>
              <Icon name="folder" size={13} />
            </span>
          }
        />,
        ...(open ? rows(f, at + 1) : []),
      ];
    }),
    ...node.objects.map((o) => row(byKey.get(o.key)!, at)),
  ];
  return <>{rows(root, level)}</>;
}

/** The colour of an object next to its name; an object without a colour of its own has an empty square. */
export function Swatch({ color }: { color: string | null }) {
  return <span className={css.swatch} style={color ? { background: color } : undefined} aria-hidden />;
}

export function Glyph({ icon }: { icon: IconName }) {
  return (
    <span className={css.glyph} aria-hidden>
      <Icon name={icon} size={13} />
    </span>
  );
}

/** Buttons at the right of a row: one per action (none — nothing is drawn). */
export function RowButtons({ actions, local }: { actions: readonly TreeAction[]; local: (kind: LocalAction) => void }) {
  const app = useApp();
  useRegistryVersion(app.registry); // availability follows the registry
  if (!actions.length) return null;
  return (
    <>
      {actions.map((a) => {
        const reason = a.command ? app.registry.reason(a.command) : null;
        return (
          <button
            key={a.label}
            type="button"
            tabIndex={-1}
            data-tree-after=""
            className={css.rowBtn}
            disabled={reason !== null}
            title={reason ? `${a.label}\nНедоступно: ${reason}` : a.label}
            aria-label={a.label}
            onClick={(e) => {
              e.stopPropagation();
              void runAction(app, a, local);
            }}
          >
            <Icon name={a.icon ?? "edit"} size={13} />
          </button>
        );
      })}
    </>
  );
}

/** The context menu of a row from its actions. */
export function useRowMenu() {
  const app = useApp();
  return (actions: readonly TreeAction[], local: (kind: LocalAction) => void) => (e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, menuItems(app, actions, local));
  };
}
