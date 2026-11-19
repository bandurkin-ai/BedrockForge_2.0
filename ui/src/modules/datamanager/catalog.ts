/**
 * The model of «Менеджер данных» (Д-20; ADR 0010 §2, ADR 0017 §6): the rows of the catalog of the core (one per
 * data object: drillhole sets, wireframes, DTM, line files, clouds, rasters, block models), the folders on the
 * left — «Все данные», the types, the folders of the user (`objects.folder`, a path by «/») with their counts —
 * and what the table shows: the rows of the chosen folder, found by the search, sorted by a column. Where rows
 * may be dropped (a folder of the user, «Все данные» — the root) and whether a move changes anything.
 * Pure: no React, no calls (api.ts reads the core). 1 000 rows — the whole model well under 20 ms.
 */

export interface CatalogRow {
  id: number;
  /** drillhole_set | wireframe | dtm | line_file | cloud | raster | block_model (a DTM and a cloud are their own) */
  kind: string;
  name: string;
  /** the folder of the user, «/» between levels; null — the root */
  folder: string | null;
  /** the format of the source file («STR», «TRIDB»…); null — made in the program */
  format: string | null;
  /** bytes of the blobs of the object and its layers (a shared pack — its share); null — unknown */
  size: number | null;
  /** rows of a drillhole set in the database; null for other types */
  rows: number | null;
  /** ISO time of the last change */
  modified: string | null;
  crs: string | null;
  /** ready | building */
  state: string;
}

export interface FolderCount {
  folder: string;
  count: number;
}

export const KIND_TITLES: Record<string, string> = {
  drillhole_set: "Наборы скважин",
  wireframe: "Каркасы",
  dtm: "ЦТМ",
  line_file: "Линии",
  cloud: "Облака точек",
  raster: "Растры",
  block_model: "Блочные модели",
};
const KIND_ORDER = Object.keys(KIND_TITLES);
export const kindTitle = (kind: string): string => KIND_TITLES[kind] ?? kind;

/** A node of the folders on the left: `all`, `kind:<kind>`, `folder:<path>`. */
export interface FolderNode {
  key: string;
  title: string;
  /** objects in it and in its folders */
  count: number;
  depth: number;
  children: FolderNode[];
}

export const ALL = "all";
export const kindKey = (kind: string) => `kind:${kind}`;
export const folderKey = (path: string) => `folder:${path}`;

/** «a//b/» → «a/b»; empty → null (the root). */
export function normFolder(folder: string | null | undefined): string | null {
  if (!folder) return null;
  const p = folder.split("/").map((s) => s.trim()).filter(Boolean).join("/");
  return p || null;
}

const collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

/**
 * The folders on the left: «Все данные», then the types that have rows, then the folders of the user as a tree
 * (a folder named only by a deeper path is made too; its count is that of everything under it).
 */
export function folderTree(rows: readonly CatalogRow[], folders: readonly FolderCount[] = []): FolderNode[] {
  const byKind = new Map<string, number>();
  for (const r of rows) byKind.set(r.kind, (byKind.get(r.kind) ?? 0) + 1);
  const kinds = [...byKind.keys()].sort((a, b) => (KIND_ORDER.indexOf(a) + 1 || 99) - (KIND_ORDER.indexOf(b) + 1 || 99) || collator.compare(a, b));
  const out: FolderNode[] = [{ key: ALL, title: "Все данные", count: rows.length, depth: 0, children: [] }];
  for (const k of kinds) out.push({ key: kindKey(k), title: kindTitle(k), count: byKind.get(k)!, depth: 0, children: [] });

  // own counts: from the rows; folders the core names (an empty one too) are kept
  const own = new Map<string, number>();
  for (const f of folders) {
    const p = normFolder(f.folder);
    if (p && !own.has(p)) own.set(p, 0);
  }
  for (const r of rows) {
    const p = normFolder(r.folder);
    if (p) own.set(p, (own.get(p) ?? 0) + 1);
  }
  const nodes = new Map<string, FolderNode>();
  const roots: FolderNode[] = [];
  const node = (path: string): FolderNode => {
    let n = nodes.get(path);
    if (n) return n;
    const parts = path.split("/");
    n = { key: folderKey(path), title: parts[parts.length - 1], count: 0, depth: parts.length - 1, children: [] };
    nodes.set(path, n);
    if (parts.length > 1) node(parts.slice(0, -1).join("/")).children.push(n);
    else roots.push(n);
    return n;
  };
  for (const [path, n] of own) {
    node(path);
    const parts = path.split("/");
    for (let i = 1; i <= parts.length; i++) nodes.get(parts.slice(0, i).join("/"))!.count += n;
  }
  const sort = (list: FolderNode[]) => {
    list.sort((a, b) => collator.compare(a.title, b.title));
    for (const c of list) sort(c.children);
  };
  sort(roots);
  return [...out, ...roots];
}

/** The nodes in the order they are drawn, children under open folders (`open` — keys of open folders). */
export function visibleNodes(tree: readonly FolderNode[], open: ReadonlySet<string>): FolderNode[] {
  const out: FolderNode[] = [];
  const walk = (list: readonly FolderNode[]) => {
    for (const n of list) {
      out.push(n);
      if (n.children.length && open.has(n.key)) walk(n.children);
    }
  };
  walk(tree);
  return out;
}

/** Whether the row is in the node: everything, its type, the folder or a folder under it. */
export function inNode(row: CatalogRow, key: string): boolean {
  if (key === ALL) return true;
  if (key.startsWith("kind:")) return row.kind === key.slice(5);
  if (key.startsWith("folder:")) {
    const p = key.slice(7);
    const f = normFolder(row.folder);
    return f !== null && (f === p || f.startsWith(`${p}/`));
  }
  return false;
}

export type SortColumn = "name" | "kind" | "folder" | "format" | "size" | "rows" | "modified" | "crs";
export const SORT_COLUMNS: SortColumn[] = ["name", "kind", "folder", "format", "size", "rows", "modified", "crs"];

export interface ViewQuery {
  node: string;
  q: string;
  sort: SortColumn;
  desc: boolean;
}

export const DEFAULT_QUERY: ViewQuery = { node: ALL, q: "", sort: "name", desc: false };

const lower = (s: string) => s.toLocaleLowerCase("ru-RU");

/** Every word of the search is in the name, the folder, the format, the type or the CRS. */
export function matcher(q: string): ((row: CatalogRow) => boolean) | null {
  const words = lower(q).split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  return (r) => {
    const text = lower(`${r.name} ${r.folder ?? ""} ${r.format ?? ""} ${kindTitle(r.kind)} ${r.crs ?? ""}`);
    return words.every((w) => text.includes(w));
  };
}

function compareBy(sort: SortColumn): (a: CatalogRow, b: CatalogRow) => number {
  switch (sort) {
    case "size":
    case "rows":
      return (a, b) => (a[sort] ?? 0) - (b[sort] ?? 0);
    case "modified":
      return (a, b) => collator.compare(a.modified ?? "", b.modified ?? "");
    case "kind":
      return (a, b) => (KIND_ORDER.indexOf(a.kind) + 1 || 99) - (KIND_ORDER.indexOf(b.kind) + 1 || 99);
    default:
      return (a, b) => collator.compare(a[sort] ?? "", b[sort] ?? "");
  }
}

const isEmpty = (r: CatalogRow, sort: SortColumn): boolean => sort !== "name" && sort !== "kind" && (r[sort] === null || r[sort] === "");

/**
 * The rows the table shows: in the node, found by the search, sorted (empty values last in both directions —
 * a missing size is not 0; equal ones by the name, then by id, so the order is stable).
 */
export function viewRows(rows: readonly CatalogRow[], query: ViewQuery): CatalogRow[] {
  const match = matcher(query.q);
  const list = rows.filter((r) => inNode(r, query.node) && (!match || match(r)));
  const cmp = compareBy(query.sort);
  const byName = compareBy("name");
  const sign = query.desc ? -1 : 1;
  return list.sort((a, b) => {
    const ea = isEmpty(a, query.sort);
    const eb = isEmpty(b, query.sort);
    if (ea !== eb) return ea ? 1 : -1;
    return sign * cmp(a, b) || byName(a, b) || a.id - b.id;
  });
}

/** A click on a header: the same column turns the order, another one sorts by it ascending. */
export function sortBy(query: ViewQuery, column: SortColumn): ViewQuery {
  return query.sort === column ? { ...query, desc: !query.desc } : { ...query, sort: column, desc: false };
}

/**
 * The folder a drop on the node puts the rows (or imports the files) into: a folder of the user — it, «Все
 * данные» — the root (""); a type is not a place — null.
 */
export function dropFolder(key: string): string | null {
  if (key === ALL) return "";
  if (key.startsWith("folder:")) return key.slice(7);
  return null;
}

/** The ids of the rows a move to the folder would change (the ones already there are left alone). */
export function moveIds(rows: readonly CatalogRow[], ids: readonly number[], folder: string): number[] {
  const target = normFolder(folder);
  const want = new Set(ids);
  return rows.filter((r) => want.has(r.id) && normFolder(r.folder) !== target).map((r) => r.id);
}

/** Why the rows are not dropped on the node, or null. */
export function dropReason(rows: readonly CatalogRow[], ids: readonly number[], key: string): string | null {
  const folder = dropFolder(key);
  if (folder === null) return "Перенос — на папку или на «Все данные»";
  if (!ids.length) return "Нечего переносить";
  if (rows.some((r) => ids.includes(r.id) && r.state === "building")) return "Объект ещё строится";
  if (!moveIds(rows, ids, folder).length) return folder ? `Уже в папке «${folder}»` : "Уже в корне";
  return null;
}

// ——— the virtual scroll of the table ———

export const ROW_H = 24;
export const OVERSCAN = 8;

/**
 * The rows the table draws: those in the viewport and `overscan` above and below — [start, end). 1 000 rows in a
 * window of 600 px — about 40 in the DOM (Д-20: ≤ 60).
 */
export function visibleRange(scrollTop: number, viewHeight: number, total: number, rowH = ROW_H, overscan = OVERSCAN): [number, number] {
  if (total <= 0) return [0, 0];
  const top = Math.max(0, Math.floor(Math.max(0, scrollTop) / rowH) - overscan);
  const shown = Math.ceil(Math.max(rowH, viewHeight) / rowH) + 2 * overscan;
  const start = Math.min(top, Math.max(0, total - 1));
  return [start, Math.min(total, start + shown)];
}

/** The scroll that brings the row `i` into the viewport (the keyboard focus); null — it is there already. */
export function scrollToRow(i: number, scrollTop: number, viewHeight: number, rowH = ROW_H): number | null {
  const top = i * rowH;
  if (top < scrollTop) return top;
  if (top + rowH > scrollTop + viewHeight) return top + rowH - viewHeight;
  return null;
}
