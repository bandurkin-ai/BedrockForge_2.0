/**
 * `omf.export` (ТЗ 7.24; ADR 0017 §4, decision 9). From the interface — the window «Экспорт OMF» with the named
 * objects checked (`dataset_ids` of a row of the tree), the file chosen by a person («Сохранить как…»). From the
 * assistant with `dataset_ids` — our own confirmation, then at once the job `export.omf` into
 * `Документы\BedrockForge\Экспорт\‹filename›.omf` (a path is not taken); a file that is there is replaced only
 * after «Заменить?» in our own window. Without `dataset_ids` — the window.
 */
import type { App } from "../../shell/app";
import { askConfirm } from "../../shell/dialogs";
import { needProject, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "../projects/state";
import { overwriteQuestion, startWithOverwrite } from "../rasters/export";
import { loadExportable, projectsRoot, startOmfExport } from "./api";
import { chosenText, cleanFileName, exportBody, exportFolder, exportName, joinPath, parseIds, sortIds } from "./omfExport";

export function openOmfExport(ids: number[] | null = null): void {
  openWindow("omf-export", "Экспорт OMF", "form", ids?.length ? { ids } : undefined, ...viewport());
}

export function bindOmf(app: App): void {
  app.registry.bind(
    "omf.export",
    noted(app, async (a, source) => {
      const given = typeof a.dataset_ids === "string" ? a.dataset_ids : null;
      const ids = given === null ? null : parseIds(given);
      if (given !== null && ids === null) throw refusal("bad_ids", `dataset_ids «${given}»: нужны номера объектов через запятую, например «7, 12».`, "Номера объектов — в дереве проекта и в менеджере данных.");
      if (source !== "claude" || !ids) {
        openOmfExport(ids);
        return "Открыто окно «Экспорт OMF»: отметьте объекты, выберите файл и нажмите «Экспортировать»";
      }
      const lists = await loadExportable();
      const sorted = sortIds(lists, ids);
      if (sorted.unknown.length)
        throw refusal("object_not_found", `Объекты №${sorted.unknown.join(", ")} не найдены среди каркасов, файлов линий и блочных моделей.`, "Обновите дерево проекта; в OMF пишутся каркасы, ЦТМ, файлы линий и блочные модели.");
      if (sorted.unavailable.length) throw refusal("omf_not_exportable", `Не записываются в OMF: ${sorted.unavailable.join("; ")}`, "Уберите их из dataset_ids.");
      const what = chosenText(sorted.chosen);
      const name = cleanFileName(a.filename, exportName(lists, sorted.chosen, projectStore.get().current?.name ?? null));
      const path = joinPath(exportFolder(await projectsRoot()), name);
      if (!(await askConfirm({ title: "Экспорт OMF", message: `Ассистент записывает в OMF ${what}.\nФайл: ${path}`, okLabel: "Экспортировать" }))) return "Отменено";
      const started = await startWithOverwrite((overwrite) => startOmfExport(exportBody(sorted.chosen, path), overwrite), (files) => askConfirm(overwriteQuestion(files)));
      if (!started) return "Отменено: файл не заменён";
      showJobs(app);
      await refreshJobs().catch(() => undefined);
      return `Экспорт OMF начат (задание №${started.id}): ${what} → ${path}`;
    }),
    needProject,
  );
}
