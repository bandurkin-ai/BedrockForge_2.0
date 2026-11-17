/**
 * One row of every tree (ТЗ 7.35 Д-03, ADR 0008 §2): only the arrow ▸/▾ expands and collapses (it alone has
 * aria-expanded); a click on the name selects, a double click runs the default action. Rows are flat with
 * aria-level; `onTreeKeyDown` on the container gives the WAI-ARIA keyboard (shell/tree.ts `treeKey`).
 */
import type { DragEvent, KeyboardEvent, MouseEvent, ReactNode } from "react";
import { Icon } from "./Icon";
import { treeKey, type KeyRow } from "./tree";
import css from "./TreeRow.module.css";

export interface TreeRowProps {
  label: string;
  /** accessible name of the row (the label by default) */
  ariaLabel?: string;
  level: number;
  /** undefined: the row has nothing to expand */
  expanded?: boolean;
  onToggle?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  onActivate?: () => void;
  onContextMenu?: (e: MouseEvent) => void;
  check?: { checked: boolean; label: string; onChange: (checked: boolean) => void };
  title?: string;
  glyph?: ReactNode;
  after?: ReactNode;
  className?: string;
  testId?: string;
  drag?: {
    draggable: boolean;
    onDragStart: (e: DragEvent) => void;
    onDragOver: (e: DragEvent) => void;
    onDragLeave: () => void;
    onDrop: (e: DragEvent) => void;
    onDragEnd: () => void;
  };
}

export function TreeRow(p: TreeRowProps) {
  const name = p.ariaLabel ?? p.label;
  return (
    <div
      role="treeitem"
      aria-level={p.level}
      aria-selected={p.selected === true}
      aria-label={name}
      tabIndex={-1}
      data-tree-row=""
      data-expanded={p.expanded === undefined ? undefined : String(p.expanded)}
      data-testid={p.testId}
      className={`${css.row} ${p.selected ? css.selected : ""} ${p.className ?? ""}`}
      style={{ paddingLeft: 4 + (p.level - 1) * 14 }}
      title={p.title}
      onContextMenu={p.onContextMenu}
      {...p.drag}
    >
      {p.expanded === undefined ? (
        <span className={css.spacer} aria-hidden />
      ) : (
        <button
          type="button"
          tabIndex={-1}
          className={css.chev}
          aria-expanded={p.expanded}
          aria-label={`${p.expanded ? "Свернуть" : "Развернуть"} «${p.label}»`}
          data-tree-toggle=""
          onClick={(e) => {
            e.stopPropagation();
            p.onToggle?.();
          }}
        >
          <span className={p.expanded ? "" : css.collapsed}>
            <Icon name="chevronDown" size={14} />
          </span>
        </button>
      )}
      {p.check && (
        <input
          type="checkbox"
          tabIndex={-1}
          className={css.check}
          checked={p.check.checked}
          aria-label={p.check.label}
          title={p.check.label}
          data-tree-check=""
          onChange={(e) => p.check!.onChange(e.target.checked)}
        />
      )}
      <span
        className={css.name}
        data-tree-name=""
        onClick={() => p.onSelect?.()}
        onDoubleClick={() => p.onActivate?.()}
      >
        {p.glyph}
        {p.label}
      </span>
      {p.after}
    </div>
  );
}

/** Keyboard of a tree container (role="tree"): the rows are its visible [data-tree-row] elements. */
export function onTreeKeyDown(e: KeyboardEvent<HTMLElement>): void {
  const target = e.target as HTMLElement;
  if (target.closest("input[type=text], textarea")) return;
  const rows = [...e.currentTarget.querySelectorAll<HTMLElement>("[data-tree-row]")];
  const i = rows.indexOf(target.closest<HTMLElement>("[data-tree-row]")!);
  if (i < 0) return;
  const keyRows: KeyRow[] = rows.map((r) => ({
    level: Number(r.getAttribute("aria-level") ?? 1),
    expanded: r.dataset.expanded === undefined ? undefined : r.dataset.expanded === "true",
  }));
  const action = treeKey(e.key, keyRows, i);
  if (!action) return;
  e.preventDefault();
  const row = rows[action.index];
  if (action.kind === "focus") row.focus();
  else if (action.kind === "expand" || action.kind === "collapse") row.querySelector<HTMLElement>("[data-tree-toggle]")?.click();
  else if (action.kind === "select") row.querySelector<HTMLElement>("[data-tree-name]")?.click();
  else row.querySelector<HTMLElement>("[data-tree-check]")?.click();
}

/** Roving focus: a click on a row makes it the keyboard's row. */
export function onTreeMouseDown(e: MouseEvent<HTMLElement>): void {
  const row = (e.target as HTMLElement).closest<HTMLElement>("[data-tree-row]");
  if (row && !(e.target as HTMLElement).closest("button[data-tree-toggle], input, [data-tree-after]")) row.focus();
}
