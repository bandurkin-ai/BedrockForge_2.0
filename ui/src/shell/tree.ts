/** Data tree logic (ТЗ 7.1): folders by «A/B» paths, import type by file extension. Pure. */

export interface TreeObject {
  key: string;
  name: string;
  folder: string;
}

export interface FolderNode {
  name: string;
  path: string;
  folders: FolderNode[];
  objects: TreeObject[];
}

/** «A / B/» -> «A/B»; empty or slashes only -> root. */
export function normalizeFolder(path: string): string {
  return path
    .split("/")
    .map((p) => p.trim())
    .filter(Boolean)
    .join("/");
}

/** Objects in folder «A/B» appear in nested folders A → B; folders first, both sorted by name (ru). */
export function buildFolders(objects: TreeObject[]): FolderNode {
  const root: FolderNode = { name: "", path: "", folders: [], objects: [] };
  for (const o of objects) {
    let node = root;
    const parts = normalizeFolder(o.folder).split("/").filter(Boolean);
    for (let i = 0; i < parts.length; i++) {
      let next = node.folders.find((f) => f.name === parts[i]);
      if (!next) {
        next = { name: parts[i], path: parts.slice(0, i + 1).join("/"), folders: [], objects: [] };
        node.folders.push(next);
      }
      node = next;
    }
    node.objects.push(o);
  }
  const coll = new Intl.Collator("ru", { numeric: true });
  const sort = (n: FolderNode) => {
    n.folders.sort((a, b) => coll.compare(a.name, b.name));
    n.objects.sort((a, b) => coll.compare(a.name, b.name));
    n.folders.forEach(sort);
  };
  sort(root);
  return root;
}

export type ImportKind = "holes" | "wireframes" | "lines" | "rasters" | "omf" | "project";

const BY_EXT: Record<string, ImportKind> = {
  tridb: "wireframes", obj: "wireframes", stl: "wireframes", dtm: "wireframes", dwg: "wireframes", dm: "wireframes",
  str: "lines", pit: "lines", shp: "lines", dbf: "lines", shx: "lines", cpg: "lines", prj: "lines", mif: "lines", mid: "lines", xyz: "lines", las: "lines", laz: "lines",
  dat: "holes", csv: "holes", txt: "holes", tsv: "holes", prn: "holes", xlsx: "holes", xlsm: "holes", xls: "holes",
  jpg: "rasters", jpeg: "rasters", png: "rasters", tif: "rasters", tiff: "rasters", bmp: "rasters",
  jgw: "rasters", jpgw: "rasters", pgw: "rasters", pngw: "rasters", tfw: "rasters", tifw: "rasters", bpw: "rasters", bmpw: "rasters", wld: "rasters", tab: "rasters", grf: "rasters",
  omf: "omf",
  bfggis: "project",
};

/**
 * Import type of dropped files (ТЗ 7.1, 7.8): the most specific kind wins (a .tridb with a .txt is
 * still wireframes). DXF and DWG may be lines or wireframes, Datamine .dm — wireframes or strings: here they
 * are wireframes (`importing/route.ts` sends them to the composition of «Импорт файлов», which shows both).
 */
export function detectImportKind(names: string[]): ImportKind | null {
  const kinds = names.map((n) => {
    const ext = n.toLowerCase().split(".").pop() ?? "";
    return ext === "dxf" ? "wireframes" : BY_EXT[ext];
  });
  const order: ImportKind[] = ["project", "omf", "wireframes", "lines", "rasters", "holes"];
  return order.find((k) => kinds.includes(k)) ?? null;
}

export const IMPORT_TITLES: Record<ImportKind, string> = {
  holes: "скважины",
  wireframes: "каркасы",
  lines: "линии",
  rasters: "растры",
  omf: "OMF",
  project: "проект .bfggis",
};

// ---- keyboard of the tree (WAI-ARIA tree pattern; ТЗ 7.35 Д-03, ADR 0008) ----------------------------

/** A visible row of the tree as the keyboard sees it. */
export interface KeyRow {
  level: number;
  /** undefined: nothing to expand */
  expanded?: boolean;
}

export type TreeAction =
  | { kind: "focus"; index: number }
  | { kind: "expand"; index: number }
  | { kind: "collapse"; index: number }
  | { kind: "select"; index: number }
  | { kind: "check"; index: number }
  /** Shift+↑/↓: move to the row and take the range from the anchor to it (`tree.select_range`) */
  | { kind: "extend"; index: number }
  /** Ctrl+Space: add the row to the chosen objects or take it away (`tree.select_toggle`) */
  | { kind: "toggle"; index: number };

export interface KeyMods {
  ctrl?: boolean;
  shift?: boolean;
}

/** What a key does on row `i`: ↑/↓ move, → expands or enters, ← collapses or goes to the parent,
 * Enter selects, Space switches the check box, Home/End — the first and the last row; Shift+↑/↓ extend the chosen
 * range and Ctrl+Space adds the row to the chosen objects or takes it away (Д-44 А5). */
export function treeKey(key: string, rows: readonly KeyRow[], i: number, mods: KeyMods = {}): TreeAction | null {
  const row = rows[i];
  if (!row) return null;
  if (mods.shift && !mods.ctrl && (key === "ArrowDown" || key === "ArrowUp")) {
    const index = key === "ArrowDown" ? i + 1 : i - 1;
    return index >= 0 && index < rows.length ? { kind: "extend", index } : null;
  }
  if (mods.ctrl && key === " ") return { kind: "toggle", index: i };
  switch (key) {
    case "ArrowDown":
      return i + 1 < rows.length ? { kind: "focus", index: i + 1 } : null;
    case "ArrowUp":
      return i > 0 ? { kind: "focus", index: i - 1 } : null;
    case "Home":
      return { kind: "focus", index: 0 };
    case "End":
      return { kind: "focus", index: rows.length - 1 };
    case "ArrowRight":
      if (row.expanded === false) return { kind: "expand", index: i };
      if (row.expanded === true && rows[i + 1]?.level > row.level) return { kind: "focus", index: i + 1 };
      return null;
    case "ArrowLeft": {
      if (row.expanded === true) return { kind: "collapse", index: i };
      for (let j = i - 1; j >= 0; j--) if (rows[j].level < row.level) return { kind: "focus", index: j };
      return null;
    }
    case "Enter":
      return { kind: "select", index: i };
    case " ":
      return { kind: "check", index: i };
    default:
      return null;
  }
}

/** Expanded state of a row that is changed only by its arrow: a click on the name never changes it. */
export function toggleExpanded(collapsed: readonly string[], key: string): string[] {
  return collapsed.includes(key) ? collapsed.filter((k) => k !== key) : [...collapsed, key];
}
