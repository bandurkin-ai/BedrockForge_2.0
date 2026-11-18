/**
 * The window «Экспорт линий» as data (ТЗ 7.18, 9.3, 9.5, 9.6; ADR 0011 §3 `export.lines`): the formats —
 * Micromine STR, Surpac STR, DXF, CSV, Micromine DAT — the layers to write, the name of the file and why the
 * export cannot start. The STR formats take lines only: layers of points are left out, with a note. Pure.
 */
import { counted } from "../../shell/treeActions";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import type { LinesExportBody } from "./actions";

export type LinesFormat = "str" | "surpac" | "dxf" | "csv" | "dat";

export interface FormatInfo {
  id: LinesFormat;
  title: string;
  /** added to a name that has none */
  ext: string;
  /** the filter of the save window (`bf:pick-save`) */
  filter: "str" | "dxf" | "csv" | "dat";
  /** the format takes layers of points */
  points: boolean;
  note: string;
}

export const LINES_FORMATS: readonly FormatInfo[] = [
  { id: "str", title: "Micromine STR", ext: ".str", filter: "str", points: false, note: "Координаты двойной точности; файл, загруженный из STR и не изменённый, выходит байт в байт." },
  { id: "surpac", title: "Surpac STR", ext: ".str", filter: "str", points: false, note: "Текстовый стринг-файл Surpac: слой — номер стринга." },
  { id: "dxf", title: "DXF", ext: ".dxf", filter: "dxf", points: true, note: "Линии — POLYLINE, точки — POINT, подписи — TEXT; слой — слой DXF." },
  { id: "csv", title: "CSV (точки и вершины)", ext: ".csv", filter: "csv", points: true, note: "Строка на точку; у линий — строка на вершину с атрибутами своей линии." },
  { id: "dat", title: "Micromine DAT", ext: ".dat", filter: "dat", points: true, note: "Таблица Micromine: строка на точку; у линий — строка на вершину." },
];

export const LINES_FORMAT_IDS = LINES_FORMATS.map((f) => f.id);
/** `export.lines` takes no more layers in one file (`lines/export.py`). */
export const MAX_EXPORT_LAYERS = 2000;

export const formatInfo = (id: string): FormatInfo => LINES_FORMATS.find((f) => f.id === id) ?? LINES_FORMATS[0];

const allLayers = (files: readonly LineFileInfo[]): LineLayerInfo[] => files.flatMap((f) => f.layers);

/** Layers checked when the window opens: the selected one; else every layer of the only file; else none. */
export function defaultLayers(files: readonly LineFileInfo[], selectedLayer: number | null, fileId: number | null = null): number[] {
  if (fileId !== null) return files.find((f) => f.id === fileId)?.layers.map((l) => l.id) ?? [];
  if (selectedLayer !== null && allLayers(files).some((l) => l.id === selectedLayer)) return [selectedLayer];
  return files.length === 1 ? files[0].layers.map((l) => l.id) : [];
}

/** The layers the format writes and the names of the layers of points it leaves out. */
export function exportLayers(files: readonly LineFileInfo[], ids: readonly number[], format: string): { take: number[]; skipped: string[] } {
  const chosen = new Set(ids);
  const layers = allLayers(files).filter((l) => chosen.has(l.id));
  if (formatInfo(format).points) return { take: layers.map((l) => l.id), skipped: [] };
  return { take: layers.filter((l) => !l.points_only).map((l) => l.id), skipped: layers.filter((l) => l.points_only).map((l) => l.name) };
}

/** «Слои точек не войдут в файл: «Реперы», «Устья»…» — null when every chosen layer is written. */
export function skippedNote(skipped: readonly string[], format: string): string | null {
  if (!skipped.length) return null;
  const names = skipped.slice(0, 3).map((n) => `«${n}»`).join(", ") + (skipped.length > 3 ? ` и ещё ${skipped.length - 3}` : "");
  return `${formatInfo(format).title} пишет только линии. Слои точек не войдут в файл: ${names}. Для точек выберите DXF, CSV или Micromine DAT.`;
}

/** Why the export cannot start; null — it can. */
export function exportReason(files: readonly LineFileInfo[], ids: readonly number[], format: string, path: string | null): string | null {
  if (!allLayers(files).length) return "В проекте нет слоёв линий: загрузите линии или точки.";
  if (!ids.length) return "Отметьте слои, которые нужно записать.";
  const { take } = exportLayers(files, ids, format);
  if (!take.length) return `В отмеченных слоях нет линий, а ${formatInfo(format).title} пишет только линии: выберите DXF, CSV или Micromine DAT.`;
  if (take.length > MAX_EXPORT_LAYERS) return `Отмечено больше ${MAX_EXPORT_LAYERS} слоёв: экспортируйте их несколькими файлами.`;
  return path ? null : "Выберите файл: «Сохранить как…».";
}

/** The name offered in the save window: the file of the layers when they share one, the only layer, or «Линии». */
export function exportName(files: readonly LineFileInfo[], ids: readonly number[], format: string): string {
  const chosen = new Set(ids);
  const owners = files.filter((f) => f.layers.some((l) => chosen.has(l.id)));
  const layers = allLayers(files).filter((l) => chosen.has(l.id));
  const base = layers.length === 1 ? layers[0].name : owners.length === 1 ? owners[0].name.replace(/\.[^.\\/]+$/, "") : "Линии";
  // characters a file name cannot have on Windows
  return `${base.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "Линии"}${formatInfo(format).ext}`;
}

/** «3 слоя: 120 линий, 4 800 вершин» of what is checked. */
export function exportTotals(files: readonly LineFileInfo[], ids: readonly number[]): string {
  const chosen = new Set(ids);
  const layers = allLayers(files).filter((l) => chosen.has(l.id));
  if (!layers.length) return "Слои не отмечены";
  const lines = layers.reduce((n, l) => n + (l.points_only ? 0 : l.line_count), 0);
  const points = layers.reduce((n, l) => n + l.point_count, 0);
  return `${counted(layers.length, "слой", "слоя", "слоёв")}: ${counted(lines, "линия", "линии", "линий")}, ${counted(points, "вершина или точка", "вершины и точки", "вершин и точек")}`;
}

export interface CsvOptions {
  delimiter: ";" | "," | "\t";
  decimal: "," | ".";
}

export const CSV_DEFAULTS: CsvOptions = { delimiter: ";", decimal: "," };

/** The body of `POST /lines/export`; the CSV options go only with CSV. */
export function exportBody(files: readonly LineFileInfo[], ids: readonly number[], format: string, path: string, csv: CsvOptions = CSV_DEFAULTS): LinesExportBody {
  const body: LinesExportBody = { path, format: formatInfo(format).id, layer_ids: exportLayers(files, ids, format).take };
  if (body.format === "csv") Object.assign(body, { delimiter: csv.delimiter, decimal: csv.decimal });
  return body;
}

/** A comma cannot separate both the columns and the decimals. */
export const csvError = (csv: CsvOptions): string | null => (csv.delimiter === "," && csv.decimal === "," ? "Разделитель столбцов и десятичный знак — оба запятая: выберите разные." : null);
