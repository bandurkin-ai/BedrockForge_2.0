/**
 * The composition of an OMF v1 file in «Импорт файлов» (ТЗ 7.24; ADR 0017 §1–§2) as the core tells it from the
 * JSON alone (`formats/omf_compose.py`, `OmfComposition.as_dict`): every element with what it becomes — a surface
 * a wireframe (a DTM when BF2 wrote it so), a line set a layer of the line file, a regular volume a block model —
 * or why it will not load; what of it is not carried over (textures, data on vertices…). An OMF 2 (ZIP) file is
 * refused by the core (`omf2_not_supported`, with the hint), shown in the card of the file. Pure: no React.
 */
import { count, counted } from "../../shell/treeActions";

export interface OmfDatum {
  key: string;
  name: string;
  class: string;
  location: string;
  /** null — loads only when constant along each line (segments of a line set) */
  loads: boolean | null;
  reason: string | null;
  unit: string | null;
}

export interface OmfElement {
  key: string;
  index: number;
  class: string;
  kind: "surface" | "lines" | "volume" | "points" | "other";
  name: string;
  /** «Поверхность», «Линии», «Объём», «Точки» */
  title: string;
  /** what it becomes: «каркас», «слой линий», «блочная модель»; null — it does not load */
  as: string | null;
  geometry: string | null;
  color: string | null;
  description: string;
  points: number | null;
  triangles: number | null;
  counts: number[] | null;
  cells: number | null;
  data: OmfDatum[];
  textures: number;
  dtm: boolean;
  skip: string | null;
  error: { code: string; message: string; hint: string } | null;
}

export interface OmfInfo {
  format: string;
  file: string;
  version: string;
  project: string;
  description: string;
  units: string;
  crs: unknown;
  elements: OmfElement[];
}

/** A volume of the file that loads (`block_models` of the composition). */
export interface OmfBlockEntry {
  name: string;
  counts: number[];
  cells: number | null;
  fields: string[];
}

/** What is left out, as the composition tells it; an OMF entry names its element. */
export interface LeftOut {
  what: string;
  count: number | null;
  reason: string;
  key?: string;
  element?: string;
  kind?: string;
}

/** «каркас», «ЦТМ», «слой линий», «блочная модель» — or «не загрузится». */
export function elementBecomes(e: Pick<OmfElement, "as" | "dtm" | "kind">): string {
  if (!e.as) return "не загрузится";
  return e.kind === "surface" && e.dtm ? "ЦТМ" : e.as;
}

/** «196 треугольников», «10 × 15 × 20, 3 000 ячеек», «1 200 точек»; what the JSON does not tell — "". */
export function elementSize(e: Pick<OmfElement, "kind" | "points" | "triangles" | "counts" | "cells">): string {
  if (e.kind === "volume" && e.counts?.length) {
    const grid = e.counts.map(count).join(" × ");
    return e.cells === null ? grid : `${grid}, ${counted(e.cells, "ячейка", "ячейки", "ячеек")}`;
  }
  if (e.triangles !== null) return counted(e.triangles, "треугольник", "треугольника", "треугольников");
  if (e.points !== null) return counted(e.points, "точка", "точки", "точек");
  return "";
}

/** Why an element does not load, or what of it is not carried over: the error of the core with its hint first. */
export function elementNotes(e: OmfElement): string[] {
  if (e.error) return [`${e.error.message} ${e.error.hint}`.trim()];
  const out: string[] = [];
  if (e.skip) out.push(e.skip);
  if (e.textures) out.push(`текстуры (${count(e.textures)}) не переносятся`);
  if (e.skip) return out;
  for (const d of e.data) {
    if (d.loads === false) out.push(`«${d.name}»: ${d.reason ?? "не переносится"}`);
    else if (d.loads === null) out.push(`«${d.name}»: перенесётся, если постоянно вдоль каждой линии`);
  }
  return out;
}

/** The data an element carries over: «Au, Cu». */
export const elementData = (e: OmfElement): string =>
  e.data
    .filter((d) => d.loads === true)
    .map((d) => d.name)
    .join(", ");

/** «OMF 1.0 · проект «Карьер» · единицы: м» */
export function omfHeader(info: Pick<OmfInfo, "format" | "version" | "project" | "units">): string {
  const parts = [`${info.format || "OMF"}${info.version ? ` ${info.version}` : ""}`];
  if (info.project) parts.push(`проект «${info.project}»`);
  if (info.units) parts.push(`единицы: ${info.units}`);
  return parts.join(" · ");
}

/** «Поверхность «a»: текстуры — 2 (текстуры не переносятся)» — a line of «Не загрузится». */
export function leftOutText(x: LeftOut): string {
  const head = x.element ? `«${x.element}»: ${x.what}` : x.what;
  return `${head}${x.count === null ? "" : ` — ${count(x.count)}`} (${x.reason})`;
}

/** The volumes that load: «vol — 10 × 15 × 20, 3 000 ячеек; поля: Au, Cu». */
export function blockEntryText(b: OmfBlockEntry): string {
  const grid = b.counts.map(count).join(" × ");
  const cells = b.cells === null ? "" : `, ${counted(b.cells, "ячейка", "ячейки", "ячеек")}`;
  return `${b.name} — ${grid}${cells}; ${b.fields.length ? `поля: ${b.fields.join(", ")}` : "без полей"}`;
}
