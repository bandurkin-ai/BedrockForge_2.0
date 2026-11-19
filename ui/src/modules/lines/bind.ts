/**
 * Commands of line layers (ТЗ 7.18; ADR 0011 §5): `lines.export` opens the window «Экспорт линий» with the
 * format and the layers checked: `layer_id` — one layer, `file_id` — every layer of a file; without them the
 * selected layer; a point cloud — LAS (or CSV) whatever format was asked. The file is chosen by a person in the
 * window (a command never writes to a path of its own). `cloud.dz` — the window «Карта разностей» of a cloud, or
 * with `wireframe_id` the job `cloud.dz` at once (ADR 0014 §5); the window follows that job (`dzJobs`).
 */
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import { needProject, needWritable, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { cloudLayers, pickCloud } from "../dtm/ground";
import { refresh as refreshJobs } from "../jobs/state";
import { loadSceneObjects, sceneObjects, type LineLayerInfo } from "../scene/objects";
import { dzJobs, type JobRef } from "./dz";
import { defaultLayers, fitFormat, formatInfo } from "./export";
import { findLayer } from "./props";

/** The cloud of `cloud.dz`: `cloud_id`, else the selected cloud, else the only one; null — none chosen. */
async function dzCloud(app: App, a: Record<string, unknown>): Promise<LineLayerInfo | null> {
  if (!sceneObjects.get().loaded) await loadSceneObjects();
  const files = sceneObjects.get().files;
  const clouds = cloudLayers(files);
  if (!clouds.length) throw refusal("no_cloud", "В проекте нет облаков точек.", "Загрузите LAS или таблицу XYZ: «Главная › Загрузка › Облако точек».");
  const asked = typeof a.cloud_id === "number" ? a.cloud_id : null;
  if (asked !== null && !clouds.some((l) => l.id === asked)) {
    const layer = files.some((f) => f.layers.some((l) => l.id === asked));
    throw layer ? refusal("not_a_cloud", `Слой №${asked} — не облако точек.`, "Выберите слой-облако (значок облака в разделе «Линии»).") : refusal("line_layer_not_found", `Облако №${asked} не найдено.`, "Обновите дерево проекта.");
  }
  const selected = app.store.get().selected;
  const id = pickCloud(files, asked, selected?.kind === "lines" ? selected.id : null);
  return clouds.find((l) => l.id === id) ?? null;
}

/** Starts the job `cloud.dz` of a cloud against a wireframe or a DTM; the window of the cloud follows it. */
export async function startDz(cloudId: number, wireframeId: number): Promise<JobRef> {
  const job = await api.post<JobRef>(`/lines/layers/${cloudId}/dz`, { wireframe_id: wireframeId });
  dzJobs.set({ [String(cloudId)]: { source: job.source, id: job.id } });
  return job;
}

export function bindLines(app: App): void {
  app.registry.bind(
    "lines.export",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const files = sceneObjects.get().files;
      const asked = formatInfo(typeof a.format === "string" ? a.format : "str");
      const layerId = typeof a.layer_id === "number" ? a.layer_id : null;
      const fileId = typeof a.file_id === "number" ? a.file_id : null;
      if (layerId !== null && !findLayer(files, layerId)) throw new Error(`Слой линий №${layerId} не найден: обновите дерево проекта`);
      if (fileId !== null && !files.some((f) => f.id === fileId)) throw new Error(`Файл линий №${fileId} не найден: обновите дерево проекта`);
      if (!files.some((f) => f.layers.length)) throw new Error("В проекте нет слоёв линий: загрузите линии или точки");
      const selected = app.store.get().selected;
      const ids = layerId !== null ? [layerId] : defaultLayers(files, selected?.kind === "lines" ? selected.id : null, fileId);
      // a point cloud goes to LAS or CSV only (LAS by default); LAS takes nothing but a cloud
      const format = formatInfo(fitFormat(files, ids, asked.id));
      openWindow("lines-export", "Экспорт линий", "form", { format: format.id, layerIds: ids }, ...viewport());
      return `Открыто окно «Экспорт линий» (${format.title}): выберите файл и нажмите «Экспортировать»`;
    }),
    needProject,
  );

  app.registry.bind(
    "cloud.dz",
    noted(app, async (a) => {
      const cloud = await dzCloud(app, a);
      const wfId = typeof a.wireframe_id === "number" ? a.wireframe_id : null;
      if (wfId === null) {
        openWindow("cloud-dz", "Карта разностей", "form", cloud ? { cloudId: cloud.id } : undefined, ...viewport());
        return cloud ? `Открыто окно «Карта разностей» для облака «${cloud.name}»: выберите каркас или ЦТМ и нажмите «Рассчитать»` : "Открыто окно «Карта разностей»: выберите облако";
      }
      if (!cloud) throw refusal("no_cloud", "Не выбрано облако точек.", "Выберите облако в дереве (раздел «Линии») или укажите cloud_id.");
      const surface = sceneObjects.get().wireframes.find((w) => w.id === wfId);
      if (!surface) throw refusal("wireframe_not_found", `Каркас или ЦТМ №${wfId} не найдены.`, "Обновите дерево проекта и выберите поверхность из разделов «Каркасы» и «ЦТМ».");
      await startDz(cloud.id, wfId);
      showJobs(app);
      await refreshJobs();
      return `Запущена «Карта разностей» облака «${cloud.name}» относительно «${surface.name}»: ход — на вкладке «Задания», итог — в окне «Карта разностей» и в «Свойствах» облака`;
    }),
    needWritable(app),
  );
}
