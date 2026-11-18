/**
 * «Свойства» of a layer of lines or points as data (ТЗ 7.18; ADR 0011 §5): counts, length, closed lines,
 * the attributes of the line picked in the 3D window and the table of attributes by pages
 * (`GET /lines/layers/{id}/attrs?offset&limit`). An empty attribute stays empty — never 0. Pure.
 */
import { extentFacts, num, type Fact } from "../../shell/props";
import { count } from "../../shell/treeActions";
import type { LineFileInfo, LineLayerInfo, PickedObject } from "../scene/objects";

/** Rows of the attribute table on one page. */
export const ATTR_PAGE = 50;

export interface AttrField {
  name: string;
  type?: string;
  unit?: string | null;
  scope?: string;
}

/** A page of attributes as the core gives it: a row per line (per point of a layer of points). */
export interface AttrPage {
  layer_id: number;
  version: number;
  fields: AttrField[];
  total: number;
  offset: number;
  limit: number;
  rows: unknown[][];
}

export function findLayer(files: readonly LineFileInfo[], id: number): { layer: LineLayerInfo; file: LineFileInfo } | null {
  for (const file of files) {
    const layer = file.layers.find((l) => l.id === id);
    if (layer) return { layer, file };
  }
  return null;
}

/** «все линии замкнуты» · «замкнутых 3 из 12» · «замкнутых нет» */
export function closedText(l: Pick<LineLayerInfo, "closed" | "closed_count" | "line_count">): string {
  if (l.closed === "all") return "все линии замкнуты";
  return l.closed === "some" ? `замкнутых ${count(l.closed_count)} из ${count(l.line_count)}` : "замкнутых нет";
}

/** What «Свойства» tell about a layer. */
export function layerFacts(l: LineLayerInfo, file?: Pick<LineFileInfo, "name" | "label" | "crs_name">): Fact[] {
  const facts: Fact[] = [];
  if (file) facts.push({ label: "Файл", value: file.label ? `${file.name} (${file.label})` : file.name });
  if (l.points_only) facts.push({ label: "Точек", value: count(l.point_count) });
  else {
    facts.push({ label: "Линий", value: count(l.line_count) }, { label: "Вершин", value: count(l.point_count) });
    facts.push({ label: "Длина", value: Number.isFinite(l.length) ? `${num(l.length, 1)} м` : "—" });
    facts.push({ label: "Замкнутость", value: closedText(l) });
  }
  facts.push({ label: "Высоты", value: l.flat ? "нет: слой плоский" : "есть" });
  facts.push(...extentFacts(l.extent));
  if (file?.crs_name) facts.push({ label: "Система координат", value: file.crs_name });
  return facts;
}

/** Fields of the attribute table: one value per line (per point). */
export const featureFields = (l: Pick<LineLayerInfo, "attr_schema">): AttrField[] => l.attr_schema.filter((f) => (f.scope ?? "feature") === "feature");

export const attrsPath = (layerId: number, offset: number, limit = ATTR_PAGE): string => `/lines/layers/${layerId}/attrs?offset=${offset}&limit=${limit}`;

export interface Pager {
  /** «Строки 51–100 из 1 234» */
  text: string;
  /** offsets of the neighbour pages; null — there is none */
  prev: number | null;
  next: number | null;
}

export function pager(total: number, offset: number, limit = ATTR_PAGE): Pager {
  if (total <= 0) return { text: "Строк нет", prev: null, next: null };
  const from = Math.min(Math.max(0, offset), total - 1);
  const to = Math.min(total, from + limit);
  return {
    text: `Строки ${count(from + 1)}–${count(to)} из ${count(total)}`,
    prev: from > 0 ? Math.max(0, from - limit) : null,
    next: to < total ? to : null,
  };
}

/** The first row of the page that holds the row `index`. */
export const pageStart = (index: number, limit = ATTR_PAGE): number => Math.floor(Math.max(0, index) / limit) * limit;

/** A cell of the attribute table: an empty one is «—» (never 0), a number keeps its digits, yes/no in Russian. */
export function attrText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return Number.isFinite(v) ? v.toLocaleString("ru-RU", { maximumFractionDigits: 6, useGrouping: false }) : "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  return String(v);
}

/** «AU, г/т» */
export const fieldTitle = (f: AttrField): string => (f.unit ? `${f.name}, ${f.unit}` : f.name);

/** The line (point) of this layer picked in the 3D window: the row of its attributes; null — none. */
export function pickedLine(picked: PickedObject | null, layerId: number): number | null {
  return picked && picked.kind === "lines" && picked.id === layerId && Number.isInteger(picked.line) && picked.line >= 0 ? picked.line : null;
}

/** «Линия № 3» · «Точка № 3» (numbers from 1, as in the table). */
export const pickedTitle = (l: Pick<LineLayerInfo, "points_only">, line: number): string => `${l.points_only ? "Точка" : "Линия"} № ${count(line + 1)}`;

/** Name → value of one row of a page. */
export function rowFacts(page: Pick<AttrPage, "fields" | "rows">, row = 0): Fact[] {
  const values = page.rows[row];
  return values ? page.fields.map((f, i) => ({ label: fieldTitle(f), value: attrText(values[i]) })) : [];
}
