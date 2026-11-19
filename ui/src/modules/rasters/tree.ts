/**
 * The «Растры» section of the data tree as data (ТЗ 7.19, ADR 0011 §5): the check box of visibility
 * (`layer.show {layer: "raster", id}`, key `rs:<raster>`), what a row tells and offers. Pure.
 */
import { SEPARATOR, type TreeAction } from "../../shell/treeActions";
import type { RasterInfo } from "../scene/objects";

export const rasterKey = (id: number): string => `rs:${id}`;

const number = (v: number): string => v.toLocaleString("ru-RU", { maximumFractionDigits: 2 });

/** Where the raster stands: «вертикальный» · «в плане, высота 250,5 м» · «в плане, под самым низким объектом». */
export function rasterPlace(r: Pick<RasterInfo, "vertical" | "elevation">): string {
  if (r.vertical) return "вертикальный";
  return r.elevation === null ? "в плане, под самым низким объектом" : `в плане, высота ${number(r.elevation)} м`;
}

export function rasterTitle(r: RasterInfo): string {
  const size = `${r.width.toLocaleString("ru-RU")} × ${r.height.toLocaleString("ru-RU")} px`;
  return `${r.name}: ${size}, ${rasterPlace(r)}. Щелчок — выбор, галочка — показать в 3D`;
}

export function rasterMenu(r: Pick<RasterInfo, "id">, visible: boolean): TreeAction[] {
  return [
    { label: "Свойства", local: "properties" },
    { label: visible ? "Скрыть в 3D" : "Показать в 3D", command: "layer.show", args: { layer: "raster", id: r.id, visible: !visible } },
    { label: "На текущий разрез…", command: "raster.place_on_section", args: { raster_id: r.id } },
    { label: "Экспорт…", command: "rasters.export", args: { raster_id: r.id } },
    SEPARATOR,
    { label: "Переименовать…", command: "object.rename", args: { kind: "raster", id: r.id } },
    { label: "Удалить…", command: "object.delete", args: { kind: "raster", id: r.id } },
  ];
}

/** «Растр наложен на ЦТМ: …» of a refused delete (422 `raster_in_use`): the names of the DTMs, or null. */
export function inUseBy(body: Record<string, unknown> | undefined): string[] | null {
  const list = body?.wireframes;
  if (!Array.isArray(list) || !list.length) return null;
  return list.map((w) => String((w as { name?: unknown }).name ?? "")).filter(Boolean);
}
