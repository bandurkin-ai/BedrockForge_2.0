/**
 * The window «Экспорт каркасов» as data (ТЗ 7.21, 9.4, 9.5, 9.12; ADR 0013 §2, §5 `export.wireframes`): the
 * formats — Micromine TRIDB, DXF, DWG (through ODA File Converter), OBJ, STL, Surpac DTM + STR — their options
 * (the version of a DWG, a binary or a text STL), the wireframes to write, the name of the file and why the
 * export cannot start. Pure.
 */
import { counted } from "../../shell/treeActions";
import type { SaveKind } from "../../bridge";
import type { WireframeInfo } from "./state";

export type WireframeFormat = "tridb" | "dxf" | "dwg" | "obj" | "stl" | "dtm";

export interface WireframeFormatInfo {
  id: WireframeFormat;
  title: string;
  /** added to a name that has another one (`wireframes/export.py` `target_path`) */
  ext: string;
  /** the filter of the save window (`bf:pick-save`) */
  filter: SaveKind;
  note: string;
}

export const WIREFRAME_FORMATS: readonly WireframeFormatInfo[] = [
  { id: "tridb", title: "Micromine TRIDB", ext: ".tridb", filter: "tridb", note: "Каркасы с названиями, цветом и атрибутами; координаты двойной точности, порядок треугольников — как в исходном файле." },
  { id: "dxf", title: "DXF", ext: ".dxf", filter: "dxf", note: "AutoCAD DXF R2004, сетки POLYFACE: слой — каркас; координаты двойной точности." },
  { id: "dwg", title: "DWG", ext: ".dwg", filter: "dwg", note: "Тот же DXF, сохранённый в DWG программой ODA File Converter (Настройка › Внешние программы)." },
  { id: "obj", title: "Wavefront OBJ", ext: ".obj", filter: "obj", note: "Текстовый OBJ: объект «o» на каркас; координаты двойной точности." },
  { id: "stl", title: "STL", ext: ".stl", filter: "stl", note: "STL хранит только треугольники: названия, цвет и атрибуты не записываются." },
  { id: "dtm", title: "Surpac DTM", ext: ".dtm", filter: "dtm", note: "Пара файлов: .dtm (треугольники) и .str (точки) с одним именем." },
];

export const WIREFRAME_FORMAT_IDS = WIREFRAME_FORMATS.map((f) => f.id);

/** Versions of a DWG to choose from (`formats/dwg.py` `VERSIONS`); R2013 unless chosen. */
export const DWG_VERSIONS = ["R2000", "R2004", "R2007", "R2010", "R2013", "R2018"] as const;
export type DwgVersion = (typeof DWG_VERSIONS)[number];
export const DWG_DEFAULT: DwgVersion = "R2013";

/** `export.wireframes` takes no more wireframes in one file (`wireframes/export.py` `MAX_WIREFRAMES`). */
export const MAX_EXPORT_WIREFRAMES = 5000;

/** Coordinates above this lose centimetres in float32 (the core warns the same, `formats/stl.py`). */
export const FLOAT32_LIMIT = 1e5;

export const wireframeFormat = (id: string): WireframeFormatInfo => WIREFRAME_FORMATS.find((f) => f.id === id) ?? WIREFRAME_FORMATS[0];

export interface ExportOptions {
  version: DwgVersion;
  /** STL: true — binary (float32), false — text (float64) */
  binary: boolean;
}

export const OPTION_DEFAULTS: ExportOptions = { version: DWG_DEFAULT, binary: true };

/** The options a command names; what it does not name stays default. */
export function exportOptions(a: { version?: unknown; binary?: unknown }): ExportOptions {
  const version = typeof a.version === "string" ? a.version.toUpperCase() : "";
  return {
    version: (DWG_VERSIONS as readonly string[]).includes(version) ? (version as DwgVersion) : DWG_DEFAULT,
    binary: typeof a.binary === "boolean" ? a.binary : true,
  };
}

type Row = Pick<WireframeInfo, "id" | "name" | "dtm" | "folder" | "point_count" | "triangle_count" | "extent">;

/** The wireframes of a folder of the tree and of the folders in it. */
export function inFolder<T extends Pick<WireframeInfo, "folder">>(list: readonly T[], folder: string): T[] {
  const path = folder.replace(/^\/+|\/+$/g, "");
  return list.filter((w) => {
    const f = w.folder ?? "";
    return f === path || f.startsWith(`${path}/`);
  });
}

/** What a command asks to export; `section` — every wireframe of «Каркасы» or «ЦТМ». */
export interface ExportPick {
  wireframeId?: number | null;
  folder?: string | null;
  section?: "wireframes" | "dtm" | null;
  selected?: number | null;
}

/** «Каркасы» (`wireframes`) or «ЦТМ» (`dtm`) of the tree; no section — both. */
export function inSection<T extends Pick<WireframeInfo, "dtm">>(list: readonly T[], section: ExportPick["section"]): T[] {
  return section ? list.filter((w) => (section === "dtm") === (w.dtm !== null)) : [...list];
}

/** Wireframes checked when the window opens: the one named, a folder (of a section), a section, the selected
 * one, or the only wireframe of the project; else none. */
export function defaultWireframes(list: readonly Row[], pick: ExportPick): number[] {
  if (pick.wireframeId != null) return list.some((w) => w.id === pick.wireframeId) ? [pick.wireframeId] : [];
  if (pick.folder != null) return inFolder(inSection(list, pick.section), pick.folder).map((w) => w.id);
  if (pick.section) return inSection(list, pick.section).map((w) => w.id);
  if (pick.selected != null && list.some((w) => w.id === pick.selected)) return [pick.selected];
  return list.length === 1 ? [list[0].id] : [];
}

/** Why the export cannot start; null — it can. */
export function exportReason(list: readonly Row[], ids: readonly number[], path: string | null): string | null {
  if (!list.length) return "В проекте нет каркасов: загрузите каркасы или постройте ЦТМ.";
  const chosen = new Set(ids);
  const taken = list.filter((w) => chosen.has(w.id));
  if (!taken.length) return "Отметьте каркасы, которые нужно записать.";
  if (taken.length > MAX_EXPORT_WIREFRAMES) return `Отмечено больше ${MAX_EXPORT_WIREFRAMES} каркасов: экспортируйте их несколькими файлами.`;
  if (!taken.some((w) => w.triangle_count > 0)) return "В отмеченных каркасах нет треугольников: записывать нечего.";
  return path ? null : "Выберите файл: «Сохранить как…».";
}

const safe = (name: string): string => name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim();

/** The name offered in the save window: the only wireframe, their common folder, or «Каркасы». */
export function exportName(list: readonly Row[], ids: readonly number[], format: string): string {
  const chosen = new Set(ids);
  const taken = list.filter((w) => chosen.has(w.id));
  const folders = new Set(taken.map((w) => w.folder ?? ""));
  const folder = folders.size === 1 ? [...folders][0] : "";
  const base = taken.length === 1 ? taken[0].name : (folder.split("/").pop() ?? "").replace(/\.[^.\\/]+$/, "");
  return `${safe(base) || "Каркасы"}${wireframeFormat(format).ext}`;
}

/** «2 каркаса: 1 200 точек, 2 396 треугольников» of what is checked. */
export function exportTotals(list: readonly Row[], ids: readonly number[]): string {
  const chosen = new Set(ids);
  const taken = list.filter((w) => chosen.has(w.id));
  if (!taken.length) return "Каркасы не отмечены";
  const points = taken.reduce((n, w) => n + w.point_count, 0);
  const triangles = taken.reduce((n, w) => n + w.triangle_count, 0);
  return `${counted(taken.length, "каркас", "каркаса", "каркасов")}: ${counted(points, "точка", "точки", "точек")}, ${counted(triangles, "треугольник", "треугольника", "треугольников")}`;
}

/** The largest coordinate of the checked wireframes by their extents; null — no extent is known. */
export function largestCoordinate(list: readonly Row[], ids: readonly number[]): number | null {
  const chosen = new Set(ids);
  let top: number | null = null;
  for (const w of list) {
    if (!chosen.has(w.id) || !w.extent) continue;
    for (const v of w.extent) if (typeof v === "number" && Number.isFinite(v)) top = Math.max(top ?? 0, Math.abs(v));
  }
  return top;
}

/** The warning of a binary STL (ТЗ 9.4: «float32: на UTM точность около 0,5 м»); null — a text STL. */
export function stlWarning(list: readonly Row[], ids: readonly number[], binary: boolean): string | null {
  if (!binary) return null;
  const top = largestCoordinate(list, ids);
  const big = top !== null && top > FLOAT32_LIMIT;
  return big
    ? "Двоичный STL хранит координаты в float32: у отмеченных каркасов координаты больше 100 000, на таких (UTM) точность около 0,5 м. Для точных координат выберите текстовый STL."
    : "Двоичный STL хранит координаты в float32 (около 7 значащих цифр): на координатах UTM точность около 0,5 м. Текстовый STL пишет координаты без потерь.";
}

export interface WireframesExportBody {
  path: string;
  format: WireframeFormat;
  wireframe_ids: number[];
  options: { version?: DwgVersion; binary?: boolean };
}

/** The body of `POST /wireframes/export`: the version goes only with a DWG, `binary` only with an STL. The
 * path of ODA File Converter is not sent: the core keeps the one of «Настройка» (`importing/tools.py`). */
export function exportBody(list: readonly Row[], ids: readonly number[], format: string, path: string, options: ExportOptions = OPTION_DEFAULTS): WireframesExportBody {
  const chosen = new Set(ids);
  const f = wireframeFormat(format).id;
  const opts: WireframesExportBody["options"] = {};
  if (f === "dwg") opts.version = options.version;
  if (f === "stl") opts.binary = options.binary;
  return { path, format: f, wireframe_ids: list.filter((w) => chosen.has(w.id)).map((w) => w.id), options: opts };
}

/** The error of a DWG without ODA File Converter: the window offers «Настройка › Внешние программы». */
export const odaMissing = (e: unknown): boolean => (e as { code?: unknown } | null)?.code === "oda_not_found";

/** Groups of the window: «Каркасы» and «ЦТМ», then the folders of the tree in each. */
export interface ExportGroup {
  key: string;
  title: string;
  items: Row[];
}

export function exportGroups(list: readonly Row[]): ExportGroup[] {
  const out: ExportGroup[] = [];
  for (const [section, title] of [["wf", "Каркасы"], ["dtm", "ЦТМ"]] as const) {
    const rows = list.filter((w) => (section === "dtm") === (w.dtm !== null));
    const folders = [...new Set(rows.map((w) => w.folder ?? ""))].sort((a, b) => a.localeCompare(b, "ru"));
    for (const folder of folders) out.push({ key: `${section}:${folder}`, title: folder ? `${title} › ${folder.replace(/\//g, " › ")}` : title, items: rows.filter((w) => (w.folder ?? "") === folder) });
  }
  return out;
}
