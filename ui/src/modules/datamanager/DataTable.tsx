/**
 * The table of «Менеджер данных» (Д-20): the columns of the catalog (name, type, folder, source format, size,
 * rows of a set, changed, CRS, state), a header click sorts, the virtual scroll draws only the rows in view and a
 * few around (1 000 rows — about 40 in the DOM, ≤ 60). Rows are chosen as in Explorer (selection.ts: the window
 * holds the state) and dragged onto a folder (`ROWS_MIME`). An empty value is «—», never 0.
 */
import { useEffect, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent } from "react";
import { count } from "../../shell/treeActions";
import { sizeText } from "../projects/exportObjectsModel";
import { kindTitle, ROW_H, scrollToRow, visibleRange, type CatalogRow, type SortColumn, type ViewQuery } from "./catalog";
import css from "./DataManager.module.css";
import type { Mods } from "./selection";

export const COLUMNS: { id: SortColumn | "state"; title: string; num?: boolean }[] = [
  { id: "name", title: "Имя" },
  { id: "kind", title: "Тип" },
  { id: "folder", title: "Папка" },
  { id: "format", title: "Формат" },
  { id: "size", title: "Размер", num: true },
  { id: "rows", title: "Строки", num: true },
  { id: "modified", title: "Изменён" },
  { id: "crs", title: "СК" },
  { id: "state", title: "Состояние" },
];

const DATE = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** «19.11.2026, 14:05»; an unreadable time is shown as it came. */
export function modifiedText(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? iso : DATE.format(new Date(t));
}

/** The text of a cell; null and an unknown size are «—» (a missing value is not 0). */
export function cellText(r: CatalogRow, column: (typeof COLUMNS)[number]["id"]): string {
  switch (column) {
    case "name":
      return r.name;
    case "kind":
      return kindTitle(r.kind);
    case "folder":
      return r.folder ?? "—";
    case "format":
      return r.format ?? "—";
    case "size":
      return r.size === null ? "—" : sizeText(r.size);
    case "rows":
      return r.rows === null ? "—" : count(r.rows);
    case "modified":
      return modifiedText(r.modified);
    case "crs":
      return r.crs ?? "—";
    case "state":
      return r.state === "building" ? "строится" : r.state === "ready" ? "готов" : r.state;
  }
}

export interface DataTableProps {
  rows: readonly CatalogRow[];
  chosen: ReadonlySet<number>;
  focus: number | null;
  query: ViewQuery;
  onSort: (column: SortColumn) => void;
  onRowClick: (id: number, mods: Mods) => void;
  onRowMenu: (id: number, e: MouseEvent) => void;
  onRowActivate: (id: number) => void;
  onRowDrag: (id: number, e: DragEvent) => void;
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>, page: number) => void;
  /** the height of the viewport before it is measured (a test renders without a layout) */
  initialHeight?: number;
}

export function DataTable(p: DataTableProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(p.initialHeight ?? 480);
  const [start, end] = visibleRange(scrollTop, height, p.rows.length);
  const page = Math.max(1, Math.floor(height / ROW_H) - 1);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.clientHeight || height);
    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // the keyboard focus stays in view
  const focusAt = p.focus === null ? -1 : p.rows.findIndex((r) => r.id === p.focus);
  useEffect(() => {
    const el = ref.current;
    if (!el || focusAt < 0) return;
    const to = scrollToRow(focusAt, el.scrollTop, el.clientHeight || height);
    if (to !== null) el.scrollTop = to;
  }, [focusAt, height]);

  const sortMark = (id: string) => (p.query.sort === id ? (p.query.desc ? " ▼" : " ▲") : "");
  return (
    <div className={css.table} data-testid="dm-table">
      <div className={css.head} role="row">
        {COLUMNS.map((c) => (
          <span key={c.id} role="columnheader" aria-sort={p.query.sort === c.id ? (p.query.desc ? "descending" : "ascending") : "none"} className={c.num ? css.num : undefined}>
            {c.id === "state" ? (
              c.title
            ) : (
              <button type="button" title={`Сортировать по столбцу «${c.title}»`} onClick={() => p.onSort(c.id as SortColumn)}>
                {c.title}
                {sortMark(c.id)}
              </button>
            )}
          </span>
        ))}
      </div>
      <div
        ref={ref}
        className={css.scroll}
        role="grid"
        aria-label="Данные проекта"
        aria-rowcount={p.rows.length}
        aria-multiselectable
        tabIndex={0}
        data-testid="dm-rows"
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        onKeyDown={(e) => p.onKeyDown(e, page)}
      >
        {p.rows.length === 0 ? (
          <div className={css.empty}>Здесь нет данных.</div>
        ) : (
          <div style={{ height: p.rows.length * ROW_H, position: "relative" }}>
            {p.rows.slice(start, end).map((r, j) => {
              const i = start + j;
              const chosen = p.chosen.has(r.id);
              return (
                <div
                  key={r.id}
                  role="row"
                  aria-rowindex={i + 1}
                  aria-selected={chosen}
                  data-testid={`dm-row-${r.id}`}
                  data-dm-row=""
                  className={`${css.row} ${chosen ? css.chosen : ""} ${p.focus === r.id ? css.focus : ""}`}
                  style={{ position: "absolute", top: i * ROW_H, left: 0, right: 0 }}
                  title={r.state === "building" ? `${r.name}: строится` : r.name}
                  draggable
                  onDragStart={(e) => p.onRowDrag(r.id, e)}
                  onMouseDown={(e) => {
                    if (e.button !== 0) return;
                    if (e.shiftKey) e.preventDefault(); // no text selection on Shift
                    const mods = { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey };
                    // a chosen row pressed alone keeps the choice until the click: several rows can be dragged
                    if (!mods.shift && !mods.ctrl && chosen) return;
                    p.onRowClick(r.id, mods);
                  }}
                  onClick={(e) => {
                    if (!e.shiftKey && !e.ctrlKey && !e.metaKey && chosen && p.chosen.size > 1) p.onRowClick(r.id, {});
                  }}
                  onDoubleClick={() => p.onRowActivate(r.id)}
                  onContextMenu={(e) => p.onRowMenu(r.id, e)}
                >
                  {COLUMNS.map((c) => (
                    <span key={c.id} role="gridcell" className={`${c.num ? css.num : ""} ${cellText(r, c.id) === "—" ? css.dim : ""} ${c.id === "state" && r.state === "building" ? css.building : ""}`}>
                      {cellText(r, c.id)}
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
