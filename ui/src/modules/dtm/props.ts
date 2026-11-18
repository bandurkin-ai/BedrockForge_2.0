/**
 * The settings of a DTM as data (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5): the raster on the relief (a raster in plan
 * only — vertical ones are not offered), «Скрыть данные за ЦТМ», the colour of the raster (a multiplier: white
 * changes nothing), its opacity 0…1, the shadows of the relief. They stand at the top of «Свойства». Pure.
 */
import { normalizeColor, num } from "../../shell/props";
import type { DtmInfo, RasterInfo } from "../scene/objects";
import type { DtmChanges } from "../wireframes/state";

export interface DrapeOption {
  id: number;
  name: string;
}

/** Rasters that can lie on a relief: the ones in plan. A vertical raster is never offered. */
export function drapeOptions(rasters: readonly Pick<RasterInfo, "id" | "name" | "vertical">[]): DrapeOption[] {
  return rasters.filter((r) => !r.vertical).map((r) => ({ id: r.id, name: r.name }));
}

/** How many rasters are left out of the list because they stand in a vertical plane. */
export const verticalCount = (rasters: readonly Pick<RasterInfo, "vertical">[]): number => rasters.filter((r) => r.vertical).length;

/** Opacity of the raster on a DTM: 0…1; null — out of the range or not a number. */
export function dtmOpacity(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

/** The settings named in the arguments of `wireframe.dtm` as the body of the request; a wrong one is told. */
export function dtmChanges(a: Record<string, unknown>): DtmChanges {
  const out: DtmChanges = {};
  if (typeof a.on === "boolean") out.on = a.on;
  if (typeof a.raster_id === "number") {
    if (a.raster_id < 0) throw new Error("raster_id: номер растра или 0 — снять растр");
    out.raster_id = a.raster_id;
  }
  if (typeof a.clip === "boolean") out.clip = a.clip;
  if (typeof a.shade === "boolean") out.shade = a.shade;
  if (a.tint !== undefined) {
    const tint = normalizeColor(a.tint);
    if (!tint) throw new Error("Цвет растра задаётся как #rrggbb, например #ffffff — без изменений");
    out.tint = tint;
  }
  if (a.opacity !== undefined) {
    const opacity = dtmOpacity(a.opacity);
    if (opacity === null) throw new Error("Непрозрачность растра — число от 0 до 1");
    out.opacity = opacity;
  }
  return out;
}

/** Are there settings of the relief among the changes (not only `on`)? */
export const hasSettings = (c: DtmChanges): boolean => Object.keys(c).some((k) => k !== "on");

/** «растр «Ортофото», цвет #ffffff, непрозрачность 0,8, тени включены, данные за ЦТМ скрыты» */
export function dtmSummary(d: DtmInfo, rasterName: string | null): string {
  return [
    d.raster_id === null ? "без растра" : `растр «${rasterName ?? `№${d.raster_id}`}»`,
    `цвет растра ${d.tint}`,
    `непрозрачность ${num(d.opacity, 2)}`,
    d.shade ? "тени рельефа включены" : "тени рельефа выключены",
    d.clip ? "данные за ЦТМ скрыты" : "данные за ЦТМ видны",
  ].join(", ");
}

/** The question of the own window before «Вернуть в каркасы». */
export const backMessage = (name: string): string => `«${name}» вернётся в раздел «Каркасы». Настройки рельефа сбросятся, растр с него снимется.`;
