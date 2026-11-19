/**
 * The floating window «Менеджер данных · ‹проект›» (ТЗ 7.35 Д-20; ADR 0010 §2, ADR 0017 §6): the folders on the
 * left (FolderTree), the table on the right (DataTable, virtual scroll), the search, the sorting, choosing several
 * rows (Shift, Ctrl, Ctrl+A, the arrows). Reread on `objects.changed` while the window is open (api.ts: an
 * overtaken read waits for the newest). The actions — bind.ts: registry commands of the modules that own the
 * objects; delete asks in our own window (danger); rows dropped on a folder — `dataset.move`; files dropped
 * from Explorer — the import wizard. Keys: Delete, F2, Enter, Esc, Ctrl+A.
 */
import { useEffect, useMemo, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import { errorText } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { useStore } from "../../shell/useStore";
import type { FloatWin } from "../../shell/windows";
import { bindCatalog, catalogStore, loadCatalog } from "./api";
import { DATA_MANAGER, dropFiles, exportRows, importInto, moveRows, rowActions, runRowAction, type ActionId } from "./bind";
import { ALL, DEFAULT_QUERY, dropFolder, dropReason, folderTree, inNode, moveIds, sortBy, viewRows, type CatalogRow, type ViewQuery } from "./catalog";
import { DataTable } from "./DataTable";
import { FolderTree, ROWS_MIME } from "./FolderTree";
import { chosenSet, clickRow, contextRow, EMPTY_SELECTION, moveFocus, pruneSelection, selectAll, type MoveKey, type Selection } from "./selection";
import css from "./DataManager.module.css";

/** The window of the module for the chain of Shell: null — not ours. */
export function renderDataManagerWindow(w: FloatWin): ReactNode {
  return w.kind === DATA_MANAGER ? <DataManager /> : null;
}

const KEYS: Record<string, MoveKey> = { ArrowUp: "up", ArrowDown: "down", Home: "home", End: "end", PageUp: "pageUp", PageDown: "pageDown" };

/** The rows of the folder node itself and of its folders, for «Экспорт папки…». */
const rowsOfNode = (rows: readonly CatalogRow[], key: string) => rows.filter((r) => inNode(r, key));

export function DataManager({ initialHeight }: { initialHeight?: number } = {}) {
  const app = useApp();
  const state = useStore(catalogStore, (s) => s);
  const [query, setQuery] = useState<ViewQuery>(DEFAULT_QUERY);
  const [sel, setSel] = useState<Selection>(EMPTY_SELECTION);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [dragIds, setDragIds] = useState<number[]>([]);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => bindCatalog(), []);

  const tree = useMemo(() => folderTree(state.rows, state.folders), [state.rows, state.folders]);
  const view = useMemo(() => viewRows(state.rows, query), [state.rows, query]);
  const order = useMemo(() => view.map((r) => r.id), [view]);
  useEffect(() => setSel((s) => pruneSelection(s, order)), [order]);
  // a folder that went away (moved, emptied): back to «Все данные»
  useEffect(() => {
    if (query.node !== ALL && state.loaded && !tree.some(function has(n): boolean { return n.key === query.node || n.children.some(has); })) setQuery((q) => ({ ...q, node: ALL }));
  }, [tree, query.node, state.loaded]);

  const chosen = useMemo(() => chosenSet(sel), [sel]);
  const chosenRows = useMemo(() => view.filter((r) => chosen.has(r.id)), [view, chosen]);
  const actions = rowActions(chosenRows, (c) => app.registry.reason(c));
  const reason = (id: ActionId) => actions.find((a) => a.id === id)?.reason ?? null;

  const report = async (fn: () => Promise<string>) => {
    try {
      setNote({ text: await fn(), error: false });
    } catch (e) {
      setNote({ text: errorText(e), error: true });
    }
  };
  const run = (id: ActionId, rows: readonly CatalogRow[] = chosenRows) => void report(() => runRowAction(app, id, rows));
  const items = (rows: readonly CatalogRow[]): MenuItem[] => {
    const list = rowActions(rows, (c) => app.registry.reason(c));
    return list.flatMap((a, i) => [...(i === 3 || i === 4 ? [{ label: "", separator: true }] : []), { label: a.label, reason: a.reason, run: () => run(a.id, rows) }]);
  };

  const rowMenu = (id: number, e: MouseEvent) => {
    e.preventDefault();
    const next = contextRow(sel, order, id);
    setSel(next);
    const picked = new Set(next.ids);
    openMenu(e.clientX, e.clientY, items(view.filter((r) => picked.has(r.id))));
  };
  const folderMenu = (key: string, e: MouseEvent) => {
    e.preventDefault();
    const folder = dropFolder(key);
    const inside = rowsOfNode(state.rows, key);
    openMenu(e.clientX, e.clientY, [
      { label: "Импорт в эту папку…", reason: folder === null ? "Импорт — в папку или в «Все данные»" : app.registry.reason("import.files"), run: () => void report(async () => (await importInto(app, folder ?? ""), "Открыт мастер импорта")) },
      { label: "Экспорт папки…", reason: inside.length ? null : "В папке нет данных", run: () => void report(async () => (exportRows(inside), "Открыто окно «Экспорт проекта»")) },
    ]);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>, page: number) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (KEYS[e.key]) {
      e.preventDefault();
      setSel((s) => moveFocus(s, order, KEYS[e.key], { shift: e.shiftKey }, page));
    } else if (ctrl && (e.key === "a" || e.key === "A" || e.code === "KeyA")) {
      e.preventDefault();
      setSel((s) => selectAll(order, s));
    } else if (e.key === "Escape" && sel.ids.length) {
      e.preventDefault();
      e.stopPropagation(); // the window stays open
      setSel(EMPTY_SELECTION);
    } else if (e.key === "Delete" && !reason("delete")) {
      e.preventDefault();
      run("delete");
    } else if (e.key === "F2" && !reason("rename")) {
      e.preventDefault();
      run("rename");
    } else if (e.key === "Enter" && !reason("properties")) {
      e.preventDefault();
      run("properties");
    }
  };
  const rowDrag = (id: number, e: DragEvent) => {
    const ids = chosen.has(id) ? sel.ids : [id];
    if (!chosen.has(id)) setSel(clickRow(sel, order, id));
    e.dataTransfer.setData(ROWS_MIME, ids.join(","));
    e.dataTransfer.effectAllowed = "move";
    setDragIds(ids);
  };
  const dropRows = (key: string) => {
    const folder = dropFolder(key);
    const ids = moveIds(state.rows, dragIds, folder ?? "");
    setDragIds([]);
    if (folder === null || !ids.length) return;
    void report(() => moveRows(app, ids, folder));
  };
  const toggle = (key: string) => setOpen((o) => (o.has(key) ? new Set([...o].filter((k) => k !== key)) : new Set([...o, key])));
  const here = dropFolder(query.node);

  return (
    <div className={css.win} data-testid="data-manager">
      <div className={css.toolbar}>
        <input className={css.search} type="search" placeholder="Поиск: имя, папка, формат, тип, СК" aria-label="Поиск данных" data-testid="dm-search" value={query.q} onChange={(e) => setQuery((q) => ({ ...q, q: e.target.value }))} />
        <ToolButton label="Импорт…" reason={here === null ? "Выберите папку или «Все данные»" : app.registry.reason("import.files")} testId="dm-import" onClick={() => void report(async () => (await importInto(app, here ?? ""), "Открыт мастер импорта"))} />
        <ToolButton label="Экспорт…" reason={reason("export")} testId="dm-export" onClick={() => run("export")} />
        <ToolButton label="Переименовать…" reason={reason("rename")} testId="dm-rename" onClick={() => run("rename")} />
        <ToolButton label="Удалить…" reason={reason("delete")} testId="dm-delete" onClick={() => run("delete")} />
        <ToolButton label="Обновить" reason={null} testId="dm-refresh" onClick={() => void report(async () => (await loadCatalog(), "Перечитано"))} />
      </div>
      <div className={css.body}>
        <FolderTree
          nodes={tree}
          open={open}
          selected={query.node}
          onSelect={(key) => setQuery((q) => ({ ...q, node: key }))}
          onToggle={toggle}
          dropReason={(key) => dropReason(state.rows, dragIds, key)}
          onDropRows={dropRows}
          onDropFiles={(key, files) => void report(() => dropFiles(app, files, dropFolder(key) ?? ""))}
          onContextMenu={folderMenu}
        />
        {state.error && !state.rows.length ? (
          <div className={`${css.empty} ${css.error}`} role="alert">
            {state.error}
          </div>
        ) : (
          <DataTable
            rows={view}
            chosen={chosen}
            focus={sel.focus}
            query={query}
            onSort={(c) => setQuery((q) => sortBy(q, c))}
            onRowClick={(id, mods) => setSel((s) => clickRow(s, order, id, mods))}
            onRowMenu={rowMenu}
            onRowActivate={(id) => run("properties", view.filter((r) => r.id === id))}
            onRowDrag={rowDrag}
            onKeyDown={onKey}
            initialHeight={initialHeight}
          />
        )}
      </div>
      <div className={css.status} data-testid="dm-status">
        <span>{state.loaded ? `Объектов: ${count(view.length)}${view.length !== state.rows.length ? ` из ${count(state.rows.length)}` : ""}` : "Читаю каталог…"}</span>
        {sel.ids.length > 0 && <span>Выбрано: {count(sel.ids.length)}</span>}
        <span className={`${css.grow} ${note?.error ? css.error : ""}`} role={note?.error ? "alert" : undefined} title={note?.text}>
          {note?.text ?? (state.error ? state.error : "Перетащите строки на папку — перенос; файлы из Проводника — импорт")}
        </span>
      </div>
    </div>
  );
}

function ToolButton({ label, reason, testId, onClick }: { label: string; reason: string | null; testId: string; onClick: () => void }) {
  return (
    <button type="button" className={css.btn} disabled={reason !== null} title={reason ?? undefined} data-testid={testId} onClick={onClick}>
      {label}
    </button>
  );
}
