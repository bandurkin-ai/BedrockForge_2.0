/**
 * Commands of line layers (ТЗ 7.18; ADR 0011 §5): `lines.export` — the window «Экспорт линий» (format, layers,
 * file), or the job `export.lines` at once when `path` is given. `layer_id` — one layer, `file_id` — every
 * layer of a file; without them the selected layer is checked.
 */
import type { App } from "../../shell/app";
import { needProject, noted, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { startLinesExport } from "./actions";
import { defaultLayers, exportBody, exportReason, formatInfo } from "./export";
import { findLayer } from "./props";

export function bindLines(app: App): void {
  app.registry.bind(
    "lines.export",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const files = sceneObjects.get().files;
      const format = formatInfo(typeof a.format === "string" ? a.format : "str").id;
      const layerId = typeof a.layer_id === "number" ? a.layer_id : null;
      const fileId = typeof a.file_id === "number" ? a.file_id : null;
      if (layerId !== null && !findLayer(files, layerId)) throw new Error(`Слой линий №${layerId} не найден: обновите дерево проекта`);
      if (fileId !== null && !files.some((f) => f.id === fileId)) throw new Error(`Файл линий №${fileId} не найден: обновите дерево проекта`);
      const selected = app.store.get().selected;
      const ids = layerId !== null ? [layerId] : defaultLayers(files, selected?.kind === "lines" ? selected.id : null, fileId);
      if (typeof a.path === "string") {
        const reason = exportReason(files, ids, format, a.path);
        if (reason) throw new Error(reason);
        const job = await startLinesExport(exportBody(files, ids, format, a.path));
        showJobs(app);
        await refreshJobs();
        return `Экспорт линий (${formatInfo(format).title}) запущен: задание №${job.id}`;
      }
      if (!files.some((f) => f.layers.length)) throw new Error("В проекте нет слоёв линий: загрузите линии или точки");
      openWindow("lines-export", "Экспорт линий", "form", { format, layerIds: ids }, ...viewport());
      return "Открыто окно «Экспорт линий»";
    }),
    needProject,
  );
}
