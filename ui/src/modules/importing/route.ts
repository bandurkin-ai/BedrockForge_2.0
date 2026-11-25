/**
 * Where the files chosen in «Импорт файлов» (or dropped from Explorer) go (ТЗ 7.8; ADR 0011 §5; ADR 0013 §6):
 * tables of drillholes — to their wizard, lines, points, rasters and wireframes — to the composition of
 * objects, an OMF file (ADR 0017 §2) — to the composition too (the core tells what of it loads; an OMF 2 is refused
 * there with its hint), a `.bfggis` — to the import of a project. What comes with later stages is told. Pure.
 */
import { detectImportKind, IMPORT_TITLES, type ImportKind } from "../../shell/tree";

/** Formats of the other object types arrive with their stages (ТЗ 7.8): none is waiting now. */
const LATER: Partial<Record<ImportKind, string>> = {};
/** Drawings hold lines and wireframes; Datamine `.dm` — wireframes, strings or a block model: the core tells by the
 * content; `.fbm` is a block model of Datamine (ADR 0023 §7). */
const COMPOSED = new Set(["dxf", "dwg", "dm", "fbm"]);

export interface Route {
  /** tables of the drillhole wizard */
  holes: string[];
  /** lines, points, rasters, wireframes and their companions: the composition of «Импорт файлов» */
  objects: string[];
  project: string[];
  /** «<имя> — <почему не загружается>», when the selection has no object to show them with */
  skipped: string[];
}

export const baseName = (p: string): string => p.split(/[\\/]/).pop() ?? p;
const ext = (p: string): string => (baseName(p).toLowerCase().split(".").pop() ?? "");

/**
 * "auto" — by the extension (a DXF or a DWG holds lines, points, text and wireframes: the composition shows both);
 * "objects" — «Линии», «Растры», «Каркасы»: every file is an object, so a CSV is a table of points.
 */
export function routeImport(paths: readonly string[], prefer: "auto" | "objects" = "auto"): Route {
  const out: Route = { holes: [], objects: [], project: [], skipped: [] };
  const rest: { path: string; kind: ImportKind | null }[] = [];
  for (const p of paths) {
    const kind = detectImportKind([p]);
    if (kind === "project") out.project.push(p);
    else if (prefer === "objects" || kind === "lines" || kind === "rasters" || kind === "wireframes" || kind === "omf" || COMPOSED.has(ext(p))) out.objects.push(p);
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
