/**
 * The folders of «Менеджер данных» (Д-20): «Все данные», the types, the folders of the user — rows of the common
 * `TreeRow` (ADR 0008 §2: only the arrow expands; WAI-ARIA keys by `onTreeKeyDown`). A folder and «Все данные»
 * take the rows of the table dropped on them (`dataset.move`) and files from Explorer (the import wizard).
 */
import { useState, type DragEvent, type MouseEvent } from "react";
import { onTreeKeyDown, onTreeMouseDown, TreeRow } from "../../shell/TreeRow";
import { dropFolder, visibleNodes, type FolderNode } from "./catalog";
import css from "./DataManager.module.css";

/** The type of the rows of the table on the clipboard of a drag (their ids by commas). */
export const ROWS_MIME = "application/x-bf-catalog";

export interface FolderTreeProps {
  nodes: readonly FolderNode[];
  open: ReadonlySet<string>;
  selected: string;
  onSelect: (key: string) => void;
  onToggle: (key: string) => void;
  /** why the dragged rows are not dropped on the node, or null (the rows being dragged are known to the window) */
  dropReason: (key: string) => string | null;
  onDropRows: (key: string) => void;
  onDropFiles: (key: string, files: File[]) => void;
  onContextMenu: (key: string, e: MouseEvent) => void;
}

const hasFiles = (e: DragEvent) => e.dataTransfer.types.includes("Files");
const hasRows = (e: DragEvent) => e.dataTransfer.types.includes(ROWS_MIME);

export function FolderTree(p: FolderTreeProps) {
  const [over, setOver] = useState<string | null>(null);
  const rows = visibleNodes(p.nodes, p.open);
  const drag = (n: FolderNode) => ({
    draggable: false,
    onDragStart: () => undefined,
    onDragOver: (e: DragEvent) => {
      const place = dropFolder(n.key) !== null;
      if (!place || !(hasFiles(e) || (hasRows(e) && p.dropReason(n.key) === null))) return;
      e.preventDefault();
      e.stopPropagation(); // not the drop of the whole app
      e.dataTransfer.dropEffect = hasFiles(e) ? "copy" : "move";
      setOver(n.key);
    },
    onDragLeave: () => setOver((k) => (k === n.key ? null : k)),
    onDrop: (e: DragEvent) => {
      setOver(null);
      if (dropFolder(n.key) === null) return;
      e.preventDefault();
      e.stopPropagation();
      if (hasFiles(e) && e.dataTransfer.files.length) p.onDropFiles(n.key, [...e.dataTransfer.files]);
      else if (hasRows(e)) p.onDropRows(n.key);
    },
    onDragEnd: () => setOver(null),
  });
  return (
    <div className={css.folders} role="tree" aria-label="Папки данных" tabIndex={0} data-testid="dm-folders" onKeyDown={onTreeKeyDown} onMouseDown={onTreeMouseDown}>
      {rows.map((n) => {
        const place = dropFolder(n.key);
        return (
          <TreeRow
            key={n.key}
            label={n.title}
            ariaLabel={`${n.title}: ${n.count}`}
            level={n.depth + 1}
            expanded={n.children.length ? p.open.has(n.key) : undefined}
            onToggle={() => p.onToggle(n.key)}
            selected={p.selected === n.key}
            onSelect={() => p.onSelect(n.key)}
            onActivate={() => p.onSelect(n.key)}
            onContextMenu={(e) => p.onContextMenu(n.key, e)}
            title={place === null ? `${n.title}: ${n.count}` : `${n.title}: ${n.count}. Перетащите сюда строки таблицы или файлы из Проводника`}
            after={<span className={css.count}>{n.count}</span>}
            className={over === n.key ? css.dropOn : undefined}
            testId={`dm-folder-${n.key}`}
            drag={drag(n)}
          />
        );
      })}
    </div>
  );
}
