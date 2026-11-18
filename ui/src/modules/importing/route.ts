/**
 * Where the files chosen in «Импорт файлов» (or dropped from Explorer) go (ТЗ 7.8; ADR 0011 §5): tables of
 * drillholes — to their wizard, lines, points and rasters — to the composition of objects, a `.bfggis` — to
 * the import of a project. What comes with later stages is told. Pure.
 */
import { detectImportKind, IMPORT_TITLES, type ImportKind } from "../../shell/tree";

/** Formats of the other object types arrive with their stages (ТЗ 7.8). */
const LATER: Partial<Record<ImportKind, string>> = { wireframes: "Э8", omf: "Э11" };

export interface Route {
  /** tables of the drillhole wizard */
  holes: string[];
  /** lines, points, rasters and their companions: the composition of «Импорт файлов» */
  objects: string[];
  project: string[];
  /** «<имя> — <почему не загружается>», when the selection has no object to show them with */
  skipped: string[];
}

export const baseName = (p: string): string => p.split(/[\\/]/).pop() ?? p;
const ext = (p: string): string => (baseName(p).toLowerCase().split(".").pop() ?? "");

/**
 * "auto" — by the extension (a DXF holds lines, points and text: its meshes are told in the composition);
 * "objects" — «Линии» and «Растры»: every file is an object, so a CSV is a table of points.
 */
export function routeImport(paths: readonly string[], prefer: "auto" | "objects" = "auto"): Route {
  const out: Route = { holes: [], objects: [], project: [], skipped: [] };
  const rest: { path: string; kind: ImportKind | null }[] = [];
  for (const p of paths) {
    const kind = detectImportKind([p]);
    if (kind === "project") out.project.push(p);
    else if (prefer === "objects" || kind === "lines" || kind === "rasters" || ext(p) === "dxf") out.objects.push(p);
    else if (kind === "holes") out.holes.push(p);
    else rest.push({ path: p, kind });
  }
  if (out.objects.length) {
    // next to objects the core tells for each file why it is not loaded: keep the order of the selection
    const other = new Set(rest.map((r) => r.path));
    out.objects = paths.filter((p) => out.objects.includes(p) || other.has(p));
  } else {
    out.skipped = rest.map((r) => `${baseName(r.path)} — ${r.kind === null ? "тип файла не распознан" : `${IMPORT_TITLES[r.kind]}: появится на этапе ${LATER[r.kind] ?? "разработки"}`}`);
  }
  return out;
}
