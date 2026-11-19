/**
 * The window «Экспорт линий» as data (ТЗ 7.18, 9.3, 9.5, 9.6; ADR 0011 §3 `export.lines`): the formats —
 * Micromine STR, Surpac STR, DXF, CSV, Micromine DAT — the layers to write, the name of the file and why the
 * export cannot start. The STR formats take lines only: layers of points are left out, with a note.
 * A point cloud (ADR 0014 §6, `lines/cloud_export.py`) goes alone, to LAS — by its source file or a new one —
 * or to CSV `X;Y;Z;класс[;h]`; the core refuses anything else (`cloud_alone`, `cloud_format`, `las_cloud_only`,
 * `no_las_source`), and the window tells it before the request. Pure.
 */
import { counted } from "../../shell/treeActions";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import type { LinesExportBody } from "./actions";

export type LinesFormat = "str" | "surpac" | "dxf" | "csv" | "dat" | "las";

export interface FormatInfo {
  id: LinesFormat;
  title: string;
  /** added to a name that has none */
  ext: string;
  /** the filter of the save window (`bf:pick-save`) */
  filter: "str" | "dxf" | "csv" | "dat" | "las";
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

/** LAS takes a point cloud only. */
export const LAS_FORMAT: FormatInfo = { id: "las", title: "LAS", ext: ".las", filter: "las", points: true, note: "Облако точек в LAS: по исходному файлу (меняются только классы) или новым файлом." };
/** Every format of `POST /lines/export`. */
export const EXPORT_FORMATS: readonly FormatInfo[] = [...LINES_FORMATS, LAS_FORMAT];
/** The formats of a cloud and the CSV of a cloud as the window names it. */
export const CLOUD_FORMATS: readonly LinesFormat[] = ["las", "csv"];
export const CLOUD_CSV: Pick<FormatInfo, "title" | "note"> = { title: "CSV (X;Y;Z;класс;h)", note: "Строка на точку облака: X, Y, Z, класс; h — высота над землёй, если был «Фильтр земли»." };

export const LINES_FORMAT_IDS = EXPORT_FORMATS.map((f) => f.id);
/** `export.lines` takes no more layers in one file (`lines/export.py`). */
export const MAX_EXPORT_LAYERS = 2000;

export const formatInfo = (id: string): FormatInfo => EXPORT_FORMATS.find((f) => f.id === id) ?? LINES_FORMATS[0];

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
  const cloud = cloudReason(files, ids, format);
  if (cloud) return cloud;
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
  if (layers.length === 1 && layers[0].cloud) return `Облако точек: ${counted(layers[0].point_count, "точка", "точки", "точек")}`;
  const lines = layers.reduce((n, l) => n + (l.points_only ? 0 : l.line_count), 0);
  const points = layers.reduce((n, l) => n + l.point_count, 0);
  return `${counted(layers.length, "слой", "слоя", "слоёв")}: ${counted(lines, "линия", "линии", "линий")}, ${counted(points, "вершина или точка", "вершины и точки", "вершин и точек")}`;
}

export interface CsvOptions {
  delimiter: ";" | "," | "\t";
  decimal: "," | ".";
}

export const CSV_DEFAULTS: CsvOptions = { delimiter: ";", decimal: "," };

/** The body of `POST /lines/export`; the CSV options go only with CSV, the way of LAS only with LAS. */
export function exportBody(files: readonly LineFileInfo[], ids: readonly number[], format: string, path: string, csv: CsvOptions = CSV_DEFAULTS, las: LasMode | null = null): LinesExportBody {
  const body: LinesExportBody = { path, format: formatInfo(format).id, layer_ids: exportLayers(files, ids, format).take };
  if (body.format === "csv") Object.assign(body, { delimiter: csv.delimiter, decimal: csv.decimal });
  if (body.format === "las") body.las = lasMode(chosenClouds(files, ids)[0] ?? null, las);
  return body;
}

/** A comma cannot separate both the columns and the decimals. */
export const csvError = (csv: CsvOptions): string | null => (csv.delimiter === "," && csv.decimal === "," ? "Разделитель столбцов и десятичный знак — оба запятая: выберите разные." : null);

// ---- a point cloud (ADR 0014 §6) -------------------------------------------------------------------------

export type LasMode = "source" | "new";

/** The chosen layers that are point clouds. */
export function chosenClouds(files: readonly LineFileInfo[], ids: readonly number[]): LineLayerInfo[] {
  const chosen = new Set(ids);
  return allLayers(files).filter((l) => chosen.has(l.id) && l.cloud);
}

/** The cloud was loaded from a LAS: it can be written «по исходному файлу». */
export const fromLas = (l: Pick<LineLayerInfo, "cloud"> | null): boolean => l?.cloud?.source?.kind === "las";

/** The way LAS is written: the one asked for when it can be, else by the source for a cloud of a LAS, else new. */
export function lasMode(cloud: Pick<LineLayerInfo, "cloud"> | null, asked: LasMode | null): LasMode {
  const can = fromLas(cloud);
  return asked === "new" || !can ? "new" : "source";
}

/** «По исходному файлу» is off for a cloud not of a LAS: why. */
export const NO_LAS_SOURCE = "облако загружено не из LAS — исходного файла LAS нет";

/** The format the window works in: a cloud takes LAS or CSV (LAS by default), other layers — anything but LAS. */
export function fitFormat(files: readonly LineFileInfo[], ids: readonly number[], format: string): LinesFormat {
  const id = formatInfo(format).id;
  if (chosenClouds(files, ids).length) return CLOUD_FORMATS.includes(id) ? id : "las";
  return id === "las" ? "str" : id;
}

/** The formats in the list of the window: of a cloud — LAS and CSV of a cloud; else the five of lines and LAS,
 * off with its reason. */
export function formatChoices(files: readonly LineFileInfo[], ids: readonly number[]): { id: LinesFormat; title: string; ext: string; why: string | null }[] {
  if (chosenClouds(files, ids).length) return [{ ...LAS_FORMAT, why: null }, { ...formatInfo("csv"), title: CLOUD_CSV.title, why: null }];
  return EXPORT_FORMATS.map((f) => ({ id: f.id, title: f.title, ext: f.ext, why: f.id === "las" ? "только облако точек" : null }));
}

/** The note under the format: that of a cloud for its CSV. */
export const formatNote = (files: readonly LineFileInfo[], ids: readonly number[], format: string): string => (format === "csv" && chosenClouds(files, ids).length ? CLOUD_CSV.note : formatInfo(format).note);

/** Why a cloud cannot be exported so (what the core would refuse); null — it can, or there is no cloud. */
export function cloudReason(files: readonly LineFileInfo[], ids: readonly number[], format: string): string | null {
  const clouds = chosenClouds(files, ids);
  const id = formatInfo(format).id;
  if (!clouds.length) return id === "las" ? "В LAS экспортируются только облака точек: отметьте облако или выберите другой формат." : null;
  if (ids.length > 1) return "Облако точек экспортируется отдельным файлом, без других слоёв: оставьте отмеченным одно облако.";
  return CLOUD_FORMATS.includes(id) ? null : `Облако точек не записывается в ${formatInfo(format).title}: выберите LAS или CSV.`;
}
