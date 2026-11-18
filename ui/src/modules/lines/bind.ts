/**
 * Commands of line layers (ТЗ 7.18; ADR 0011 §5): `lines.export` opens the window «Экспорт линий» with the
 * format and the layers checked: `layer_id` — one layer, `file_id` — every layer of a file; without them the
 * selected layer. The file is chosen by a person in the window (a command never writes to a path of its own).
 */
import type { App } from "../../shell/app";
import { needProject, noted, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { defaultLayers, formatInfo } from "./export";
import { findLayer } from "./props";

export function bindLines(app: App): void {
  app.registry.bind(
    "lines.export",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const files = sceneObjects.get().files;
      const format = formatInfo(typeof a.format === "string" ? a.format : "str");
      const layerId = typeof a.layer_id === "number" ? a.layer_id : null;
      const fileId = typeof a.file_id === "number" ? a.file_id : null;
      if (layerId !== null && !findLayer(files, layerId)) throw new Error(`Слой линий №${layerId} не найден: обновите дерево проекта`);
      if (fileId !== null && !files.some((f) => f.id === fileId)) throw new Error(`Файл линий №${fileId} не найден: обновите дерево проекта`);
      if (!files.some((f) => f.layers.length)) throw new Error("В проекте нет слоёв линий: загрузите линии или точки");
      const selected = app.store.get().selected;
      const ids = layerId !== null ? [layerId] : defaultLayers(files, selected?.kind === "lines" ? selected.id : null, fileId);
      openWindow("lines-export", "Экспорт линий", "form", { format: format.id, layerIds: ids }, ...viewport());
      return `Открыто окно «Экспорт линий» (${format.title}): выберите файл и нажмите «Экспортировать»`;
    }),
    needProject,
  );
}
