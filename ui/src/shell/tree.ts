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
  tridb: "wireframes", obj: "wireframes", stl: "wireframes", dtm: "wireframes",
  str: "lines",
  dat: "holes", csv: "holes", txt: "holes", xlsx: "holes", xlsm: "holes", xls: "holes",
  jpg: "rasters", jpeg: "rasters", png: "rasters", tif: "rasters", tiff: "rasters", bmp: "rasters",
  jgw: "rasters", jpgw: "rasters", pgw: "rasters", pngw: "rasters", tfw: "rasters", tifw: "rasters", bpw: "rasters", wld: "rasters", tab: "rasters", grf: "rasters",
  omf: "omf",
  bfggis: "project",
};

/**
 * Import type of dropped files (ТЗ 7.1, 7.8): the most specific kind wins (a .tridb with a .txt is
 * still wireframes). DXF may be lines or wireframes — the wizard decides from content; here: wireframes.
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
