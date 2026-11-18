/**
 * Commands of rasters (ТЗ 7.19; ADR 0011 §5): `raster.set` — the height, the opacity and the edges of a raster
 * in a vertical plane; `raster.shift` — «Сдвинуть»; `raster.place_on_section` — «На текущий разрез» (own
 * confirmation window); `rasters.export` — the window «Экспорт растра» (the file is chosen by a person there).
 * Without `raster_id` — the selected raster, or the only raster of the project.
 */
import type { App } from "../../shell/app";
import { askConfirm, askText } from "../../shell/dialogs";
import { metres } from "../importing/objects";
import { needProject, needSection, noted, viewport } from "../../shell/objectRun";
import { selectWithProperties } from "../../shell/treeRun";
import { openWindow } from "../../shell/windows";
import { viewSyncOf } from "../scene/bind";
import { loadSceneObjects, sceneObjects, type RasterInfo } from "../scene/objects";
import { placeOnSection, rasterById, setRaster, shiftRaster } from "./actions";
import { RASTER_FORMATS } from "./export";
import { EDGE_LABELS, opacityOf, parseField, placeMessage, rasterState, rasterSummary, shiftReason, type Edges } from "./props";

/** The raster of a command: `raster_id`, the selected raster, or the only one of the project. */
function rasterOf(app: App, a: Record<string, unknown>): RasterInfo {
  const rasters = sceneObjects.get().rasters;
  const selected = app.store.get().selected;
  const id = typeof a.raster_id === "number" ? a.raster_id : selected?.kind === "raster" ? selected.id : rasters.length === 1 ? rasters[0].id : null;
  if (id === null) throw new Error(rasters.length ? "Выберите растр в дереве или укажите raster_id" : "В проекте нет растров: загрузите изображение с файлом привязки или GeoTIFF");
  const raster = rasterById(id);
  if (!raster) throw new Error(`Растр №${id} не найден: обновите дерево проекта`);
  return raster;
}

/** The six edges of `raster.set`; null — none is given; a part of them is refused. */
function edgesOf(a: Record<string, unknown>): Edges | null {
  const given = EDGE_LABELS.filter(([k]) => typeof a[k] === "number");
  if (!given.length) return null;
  if (given.length < EDGE_LABELS.length) throw new Error("Края вертикального растра задаются вместе: x1, y1, x2, y2, z_top, z_bottom");
  return Object.fromEntries(EDGE_LABELS.map(([k]) => [k, a[k] as number])) as unknown as Edges;
}

export function bindRasters(app: App): void {
  const r = app.registry;
  const ready = async () => {
    if (!sceneObjects.get().loaded) await loadSceneObjects();
  };

  r.bind(
    "raster.set",
    noted(app, async (a) => {
      await ready();
      const raster = rasterOf(app, a);
      const changes: Parameters<typeof setRaster>[1] = {};
      if ("elevation" in a) {
        if (raster.vertical) throw new Error("У вертикального растра нет одной высоты: измените «Верх Z» и «Низ Z» или сдвиньте растр по Z");
        changes.elevation = a.elevation as number | null;
      }
      if ("opacity" in a) {
        const opacity = opacityOf(a.opacity);
        if (opacity === null) throw new Error("Непрозрачность растра — число от 0,05 до 1");
        changes.opacity = opacity;
      }
      const edges = edgesOf(a);
      if (edges) changes.vertical = edges;
      if (!Object.keys(changes).length) {
        await selectWithProperties(app, "raster", raster.id);
        return `Свойства растра «${raster.name}» — в панели «Свойства»: высота, сдвиг по Z, непрозрачность`;
      }
      const saved = await setRaster(raster, changes);
      return rasterSummary(rasterById(raster.id) ?? saved);
    }),
    needProject,
  );

  r.bind(
    "raster.shift",
    noted(app, async (a, source) => {
      await ready();
      const raster = rasterOf(app, a);
      const reason = shiftReason(raster);
      if (reason) throw new Error(reason);
      let dz = typeof a.dz === "number" ? a.dz : null;
      if (dz === null) {
        if (source !== "user") throw new Error("Укажите сдвиг по Z в метрах: dz");
        const text = await askText({ title: `Сдвинуть растр «${raster.name}» по Z`, label: "Сдвиг, м (плюс — вверх, минус — вниз)", okLabel: "Сдвинуть" });
        if (text === null) return "Отменено";
        const parsed = parseField(text, "Сдвиг по Z", true);
        if (parsed.error) throw new Error(parsed.error);
        dz = parsed.value!;
      }
      if (!Number.isFinite(dz) || dz === 0) throw new Error("Сдвиг по Z — число, не равное нулю, например −5");
      const saved = await shiftRaster(rasterById(raster.id) ?? raster, dz);
      return `Растр «${raster.name}» сдвинут по Z на ${metres(dz, true)} м: ${rasterState(rasterById(raster.id) ?? saved)}`;
    }),
    needProject,
  );

  r.bind(
    "raster.place_on_section",
    noted(app, async (a, source) => {
      await ready();
      const raster = rasterOf(app, a);
      const zTop = typeof a.z_top === "number" ? a.z_top : null;
      // own confirmation of a call from the interface (ТЗ 7.19); Claude's call was confirmed by the registry
      if (source === "user" && !(await askConfirm({ title: "На текущий разрез", message: placeMessage(raster, zTop), okLabel: "Поставить" }))) return "Отменено";
      await viewSyncOf()?.flush("section"); // the core takes the line from the view settings of the project
      await placeOnSection(rasterById(raster.id) ?? raster, zTop);
      return `Растр «${raster.name}» поставлен в плоскость линии разреза${zTop === null ? "" : `, верх — ${metres(zTop)} м`}`;
    }),
    needSection,
  );

  r.bind(
    "rasters.export",
    noted(app, async (a) => {
      await ready();
      const rasters = sceneObjects.get().rasters;
      if (!rasters.length) throw new Error("В проекте нет растров: загрузите изображение с файлом привязки или GeoTIFF");
      const selected = app.store.get().selected;
      const id = typeof a.raster_id === "number" ? rasterOf(app, a).id : selected?.kind === "raster" ? selected.id : rasters[0].id;
      const format = RASTER_FORMATS.find((f) => f.id === a.format) ?? RASTER_FORMATS[0];
      openWindow("raster-export", "Экспорт растра", "medium", { rasterId: id, format: format.id }, ...viewport());
      return `Открыто окно «Экспорт растра» (${format.title}): выберите файл и нажмите «Экспортировать»`;
    }),
    needProject,
  );
}
