/**
 * The window «Экспорт растра» as data (ТЗ 7.19, 9.7; ADR 0011 §3 `export.raster`): the source file with its
 * georeference next to it (a world file and `.prj`), or one GeoTIFF. Pure.
 *
 * The save window asks about the chosen name only; the export also writes companion files, and the image may
 * get the extension of its kind after the chosen name. The core does not replace such files unasked: it
 * answers 409 `files_exist` with their names, the window asks (`overwriteQuestion`) and starts again.
 */
import type { ConfirmOptions } from "../../shell/dialogs";
import type { RasterRow } from "./props";

export type RasterFormat = "source" | "geotiff";
export const RASTER_FORMAT_IDS: readonly RasterFormat[] = ["source", "geotiff"];

export const RASTER_FORMATS: readonly { id: RasterFormat; title: string; note: string }[] = [
  { id: "source", title: "Исходный файл с привязкой", note: "Изображение как загружено и рядом — world-файл и .prj; у вертикального растра — его исходные файлы привязки." },
  { id: "geotiff", title: "GeoTIFF", note: "Один файл .tif: привязка, система координат и высота — в тегах." },
];

/** The kind of the source image (`meta.format`) → its extension and the filter of the save window. */
const KINDS: Record<string, { ext: string; filter: "jpg" | "png" | "tif" | "bmp" }> = {
  JPEG: { ext: ".jpg", filter: "jpg" },
  PNG: { ext: ".png", filter: "png" },
  TIFF: { ext: ".tif", filter: "tif" },
  BMP: { ext: ".bmp", filter: "bmp" },
};

type Raster = Pick<RasterRow, "name" | "georef" | "elevation" | "vertical" | "format">;

/** The filter of the save window: by the image kind for the source file (unknown — any file), `.tif` for GeoTIFF. */
export function exportFilter(r: Pick<Raster, "format">, format: RasterFormat): "jpg" | "png" | "tif" | "bmp" | "any" {
  if (format === "geotiff") return "tif";
  return KINDS[String(r.format ?? "").toUpperCase()]?.filter ?? "any";
}

/** The name offered in the save window; an unknown image kind gets its extension from the core. */
export function exportName(r: Pick<Raster, "name" | "format">, format: RasterFormat): string {
  const base = r.name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "Растр";
  const ext = format === "geotiff" ? ".tif" : (KINDS[String(r.format ?? "").toUpperCase()]?.ext ?? "");
  return base.toLowerCase().endsWith(ext) ? base : `${base}${ext}`;
}

const hasGeoref = (r: Pick<Raster, "georef">): boolean => Array.isArray((r.georef as { affine?: unknown } | null)?.affine);

/** What the user should know before the export starts. */
export function exportNotes(r: Raster, format: RasterFormat): string[] {
  const notes: string[] = [];
  if (!hasGeoref(r)) notes.push(format === "geotiff" ? "У растра нет привязки: GeoTIFF записать нельзя — экспортируйте исходный файл." : "У растра нет привязки: будет записан только исходный файл.");
  else if (format === "source" && r.vertical) notes.push("Привязка вертикального растра записывается его исходными файлами привязки: сдвиг, сделанный в проекте, в них не попадает.");
  else if (format === "source" && r.elevation !== null) notes.push("Высота растра в world-файл не записывается: чтобы сохранить её, выберите GeoTIFF.");
  return notes;
}

/** Why the export cannot start; null — it can. */
export function exportReason(r: Raster | undefined, format: RasterFormat, path: string | null): string | null {
  if (!r) return "Выберите растр.";
  if (format === "geotiff" && !hasGeoref(r)) return "У растра нет привязки: GeoTIFF записать нельзя. Выберите «Исходный файл с привязкой».";
  return path ? null : "Выберите файл: «Сохранить как…».";
}

/** The files a 409 `files_exist` of the core names: the export would replace them; null — another error. */
export function existingFiles(e: unknown): string[] | null {
  const err = e as { code?: unknown; body?: { files?: unknown } } | null;
  if (!err || err.code !== "files_exist") return null;
  const files = Array.isArray(err.body?.files) ? err.body.files.filter((f): f is string => typeof f === "string" && f !== "") : [];
  return files.length ? files : null;
}

const SHOWN_FILES = 12;

/** The question of our own window before files next to the chosen one are replaced. */
export function overwriteQuestion(files: readonly string[]): ConfirmOptions {
  const shown = files.slice(0, SHOWN_FILES).map((f) => `• ${f}`);
  if (files.length > SHOWN_FILES) shown.push(`… и ещё ${files.length - SHOWN_FILES}`);
  const head = files.length === 1 ? "В папке уже есть файл с таким именем. Экспорт заменит его:" : "В папке уже есть файлы с такими именами. Экспорт заменит их:";
  return { title: "Заменить файлы?", message: `${head}\n${shown.join("\n")}`, okLabel: "Заменить", danger: true };
}

/**
 * Starts the export; when the core names files it would replace, asks and starts again with the permission.
 * null — the user said no: nothing is started and nothing is written.
 */
export async function startWithOverwrite<T>(start: (overwrite: boolean) => Promise<T>, ask: (files: string[]) => Promise<boolean>): Promise<T | null> {
  try {
    return await start(false);
  } catch (e) {
    const files = existingFiles(e);
    if (!files) throw e;
    return (await ask(files)) ? start(true) : null;
  }
}
