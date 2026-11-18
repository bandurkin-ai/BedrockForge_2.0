/**
 * «Свойства» of a raster as data (ТЗ 7.19; ADR 0011 §5): the georeference and the deviations at the control
 * points, «Высота», «Сдвиг по Z» (not available without a height — with the reason), the edges of a raster in
 * a vertical plane («Левый/Правый край X/Y», «Верх/Низ Z»), «На текущий разрез». Numbers are typed with a
 * comma or a point; an empty height is «not set», never 0. Pure.
 */
import { fieldNumber, num, type Fact } from "../../shell/props";
import { counted } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import type { RasterInfo } from "../scene/objects";
import { rasterPlace } from "./tree";

export const OPACITY_MIN = 0.05;
export const OPACITY_MAX = 1;

/** Where the georeference came from (`formats/georef.py` `SOURCE_LABELS`). */
export const SOURCE_TITLES: Record<string, string> = {
  geotiff: "GeoTIFF",
  world: "World-файл",
  tab: "MapInfo TAB",
  grf: "Micromine GRF",
  section: "по линии разреза",
  manual: "задана вручную",
};

/** The edges of a raster standing in a vertical plane. */
export interface Edges {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  z_top: number;
  z_bottom: number;
}
export type EdgeKey = keyof Edges;

export const EDGE_LABELS: readonly (readonly [EdgeKey, string])[] = [
  ["x1", "Левый край X"],
  ["y1", "Левый край Y"],
  ["x2", "Правый край X"],
  ["y2", "Правый край Y"],
  ["z_top", "Верх Z"],
  ["z_bottom", "Низ Z"],
];

/** A raster as `/rasters` gives it: with the kind and the name of its source file, which the list of the scene
 * does not declare. */
export type RasterRow = RasterInfo & { format?: string | null; filename?: string | null };

type Georef = NonNullable<RasterInfo["georef"]> & { rotation?: number; crs_name?: string | null; checks?: unknown[] };

/** The edges of a vertical raster from its corners (top-left, top-right, bottom-right, bottom-left); null —
 * the raster has no corners with heights. */
export function rasterEdges(r: Pick<RasterInfo, "georef">): Edges | null {
  const c = r.georef?.corners3d;
  if (!c || c.length < 4) return null;
  const [tl, tr, , bl] = c;
  const values = [tl[0], tl[1], tr[0], tr[1], tl[2], bl[2]];
  if (!values.every((v): v is number => typeof v === "number" && Number.isFinite(v))) return null;
  return { x1: values[0], y1: values[1], x2: values[2], y2: values[3], z_top: values[4], z_bottom: values[5] };
}

/** The edges as the texts of their fields. */
export function edgeTexts(e: Edges | null): Record<EdgeKey, string> {
  return Object.fromEntries(EDGE_LABELS.map(([k]) => [k, e ? fieldNumber(e[k]) : ""])) as Record<EdgeKey, string>;
}

/** What «Свойства» tell about a raster. */
export function rasterFacts(r: RasterRow): Fact[] {
  const g = r.georef as Georef | null;
  const facts: Fact[] = [{ label: "Размер", value: `${num(r.width, 0)} × ${num(r.height, 0)} px` }];
  facts.push({ label: "Привязка", value: g?.source ? (SOURCE_TITLES[g.source] ?? g.source) : "нет" });
  if (typeof g?.pixel === "number" && Number.isFinite(g.pixel) && g.pixel > 0) facts.push({ label: "Пиксель", value: `${num(g.pixel, 4)} м` });
  if (!r.vertical && typeof g?.rotation === "number" && Math.abs(g.rotation) >= 0.005) facts.push({ label: "Поворот", value: `${num(g.rotation, 2)}°` });
  if (g?.crs_name) facts.push({ label: "Система координат", value: g.crs_name });
  facts.push({ label: "Положение", value: rasterPlace(r) });
  if (r.filename) facts.push({ label: "Файл", value: r.filename });
  return facts;
}

/** «MapInfo TAB: наибольшее отклонение 0,35 м (4 точки)» per georeference file with control points. */
export function checkLines(r: Pick<RasterInfo, "georef">): string[] {
  const checks = (r.georef as Georef | null)?.checks ?? [];
  return checks.flatMap((raw) => {
    const c = raw as { source?: unknown; max?: unknown; points?: unknown };
    if (typeof c.max !== "number" || !Number.isFinite(c.max)) return [];
    const points = Array.isArray(c.points) ? c.points.length : 0;
    const source = SOURCE_TITLES[String(c.source)] ?? String(c.source ?? "опорные точки");
    return [`${source}: наибольшее отклонение ${num(c.max, 3)} м${points ? ` (${counted(points, "точка", "точки", "точек")})` : ""}`];
  });
}

/** Why «Сдвинуть» is not available: a plan raster without a height has nothing to move. */
export function shiftReason(r: Pick<RasterInfo, "has_elevation">): string | null {
  return r.has_elevation ? null : "У растра не задана высота: сдвигать нечего. Введите «Высоту» и нажмите «Применить».";
}

export interface Parsed {
  /** null — the field is empty */
  value: number | null;
  error: string | null;
}

/** A number of a field of «Свойства»: «12,5», «12.5», «−5»; empty — null unless `required`. */
export function parseField(text: string, label: string, required = false): Parsed {
  const value = parseNumber(text);
  if (value === undefined) return { value: null, error: `«${label}»: «${text.trim()}» — не число. Введите число, например 12,5.` };
  if (value === null && required) return { value: null, error: `«${label}»: введите число.` };
  return { value, error: null };
}

/** The six edges of a vertical raster from their fields; the first problem is told. */
export function parseEdges(texts: Record<EdgeKey, string>): { edges: Edges | null; error: string | null } {
  const out: Partial<Edges> = {};
  for (const [key, label] of EDGE_LABELS) {
    const p = parseField(texts[key], label, true);
    if (p.error) return { edges: null, error: p.error };
    out[key] = p.value!;
  }
  const e = out as Edges;
  if (Math.hypot(e.x2 - e.x1, e.y2 - e.y1) < 1e-6) return { edges: null, error: "Левый и правый края растра совпадают в плане: задайте разные края." };
  if (Math.abs(e.z_top - e.z_bottom) < 1e-6) return { edges: null, error: "Верх и низ растра на одной высоте: задайте разные высоты." };
  return { edges: e, error: null };
}

/** Opacity of a raster: 0,05…1; null — out of the range or not a number. */
export function opacityOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= OPACITY_MIN && value <= OPACITY_MAX ? value : null;
}

/** The question of the own window before «На текущий разрез». */
export function placeMessage(r: Pick<RasterInfo, "name" | "vertical">, zTop: number | null): string {
  const height = zTop === null ? "верх и низ — по высотам данных проекта" : `верх — на отметке ${num(zTop, 3)} м, низ — по пропорциям изображения`;
  const was = r.vertical ? "Прежнее положение растра будет заменено." : "Растр перестанет лежать в плане; прежняя привязка будет заменена.";
  return `Растр «${r.name}» встанет в плоскость линии разреза: края — по линии, ${height}. ${was}`;
}

/** «в плане, высота 250,5 м, непрозрачность 0,8» */
export const rasterState = (r: Pick<RasterInfo, "vertical" | "elevation" | "opacity">): string => `${rasterPlace(r)}, непрозрачность ${num(r.opacity, 2)}`;

/** «Растр «Ортофото»: в плане, высота 250,5 м, непрозрачность 0,8» */
export const rasterSummary = (r: Pick<RasterInfo, "name" | "vertical" | "elevation" | "opacity">): string => `Растр «${r.name}»: ${rasterState(r)}`;
