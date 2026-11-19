/**
 * `wireframe.export` (ТЗ 7.21, 9.4, 9.5; ADR 0013 §5): opens the window «Экспорт каркасов» with the format,
 * its options and the wireframes checked — `wireframe_id` (a row of the tree), `folder` (a folder of the tree:
 * the wireframes of one imported file), `section` (every wireframe of «Каркасы» or «ЦТМ»); without them the
 * selected wireframe. The file is chosen by a person in the window: a command never writes to a path of its own.
 * The job `export.wireframes` starts from the window (`startWireframesExport`).
 */
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { defaultWireframes, exportOptions, inFolder, inSection, wireframeFormat, type WireframesExportBody } from "./export";
import type { WireframeInfo } from "./state";

/** Starts the job `export.wireframes`; 409 `files_exist` names the files it would replace without `overwrite`. */
export function startWireframesExport(body: WireframesExportBody, overwrite = false): Promise<{ source: string; id: number }> {
  return api.post("/wireframes/export", { ...body, overwrite });
}

export function bindExportCommands(app: App): void {
  app.registry.bind(
    "wireframe.export",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const list = sceneObjects.get().wireframes as WireframeInfo[];
      if (!list.length) throw refusal("no_wireframes", "В проекте нет каркасов.", "Загрузите каркасы или постройте ЦТМ.");
      const format = wireframeFormat(typeof a.format === "string" ? a.format : "tridb");
      const wireframeId = typeof a.wireframe_id === "number" ? a.wireframe_id : null;
      const folder = typeof a.folder === "string" ? a.folder : null;
      const section = a.section === "wireframes" || a.section === "dtm" ? a.section : null;
      if (wireframeId !== null && !list.some((w) => w.id === wireframeId)) throw refusal("wireframe_not_found", `Каркас №${wireframeId} не найден.`, "Обновите дерево проекта.");
      if (folder !== null && !inFolder(inSection(list, section), folder).length) throw refusal("folder_empty", `В папке «${folder}» нет каркасов.`, "Обновите дерево проекта.");
      if (section !== null && !inSection(list, section).length) {
        throw section === "dtm"
          ? refusal("no_dtm", "В разделе «ЦТМ» нет поверхностей.", "Постройте ЦТМ или перенесите каркас поверхности («В ЦТМ»).")
          : refusal("no_wireframes", "В разделе «Каркасы» нет каркасов.", "Загрузите каркасы в проект.");
      }
      const selected = app.store.get().selected;
      const ids = defaultWireframes(list, { wireframeId, folder, section, selected: selected?.kind === "wireframe" ? selected.id : null });
      const options = exportOptions(a);
      openWindow("wireframes-export", "Экспорт каркасов", "form", { format: format.id, wireframeIds: ids, options }, ...viewport());
      const what = format.id === "dwg" ? `DWG ${options.version}` : format.id === "stl" ? `STL, ${options.binary ? "двоичный" : "текстовый"}` : format.title;
      return `Открыто окно «Экспорт каркасов» (${what}): выберите файл и нажмите «Экспортировать»`;
    }),
    needProject,
  );
}
