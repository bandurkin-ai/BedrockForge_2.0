/** Commands of import (ТЗ 7.7, 7.8; ADR 0011 §5; ADR 0013 §6; ADR 0017 §2): the drillhole wizard (and its mode
 * «Канавы по полилиниям»), «Импорт файлов» with the composition of lines, points, rasters, wireframes and OMF
 * (`import.files {kind, folder}`; `omf.import` — the same with OMF files offered), «Линии»,
 * «Каркасы» and «Растры»; «Облако точек» (`points.import {mode: "las"|"xyz"}`, ADR 0014 §6) — the same window in
 * the mode of a cloud; the wireframes an import stored are checked (`afterImport.ts`). */
import { bridge } from "../../bridge";
import type { App } from "../../shell/app";
import { showMessage } from "../../shell/dialogs";
import { openWindow } from "../../shell/windows";
import { onJobFinished } from "../jobs/state";
import { loadSets } from "../tables/state";
import { watchImports } from "./afterImport";
import { CLOUD_TITLE, type CloudMode } from "./cloud";
import { cleanFolder } from "./objects";
import { routeImport } from "./route";
import { MODE_TITLES, type WizardMode } from "./wizard";

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

export function openDrillholeWizard(paths: string[] = [], mode: WizardMode = "holes"): void {
  const props = { ...(paths.length ? { paths } : {}), ...(mode === "holes" ? {} : { mode }) };
  openWindow("import-drillholes", MODE_TITLES[mode], "normal", Object.keys(props).length ? props : undefined, ...viewport());
}

export type ObjectsPick = "objects" | "lines" | "rasters" | "wireframes" | "omf";

/** The composition of lines, points, rasters, wireframes and OMF; `pick` — what «Добавить файлы…» of the window
 * offers; `cloud` — the window «Облако точек»: every table is a point cloud, «Добавить файлы…» offers LAS or XYZ;
 * `folder` — the folder of the tree the objects go to. */
export function openObjectsWizard(paths: string[], pick: ObjectsPick = "objects", cloud: CloudMode | null = null, folder: string | null = null): void {
  const at = folder ? { folder } : {};
  if (cloud) openWindow("import-objects", CLOUD_TITLE, "normal", { paths, cloud, ...at }, ...viewport());
  else openWindow("import-objects", "Импорт файлов", "normal", { paths, ...(pick === "objects" ? {} : { pick }), ...at }, ...viewport());
}

/** Files from the dialog or dropped from Explorer (also onto a folder of «Менеджер данных»: `folder`): drillhole
 * tables go to their wizard, lines, points, rasters, wireframes and OMF to the composition of «Импорт файлов», a
 * `.bfggis` to the project import; what is not loaded yet is told with its stage (ТЗ 7.8). */
export async function importPaths(app: App, paths: string[], prefer: "auto" | "objects" = "auto", folder: string | null = null): Promise<string> {
  const route = routeImport(paths, prefer);
  if (route.skipped.length) {
    const loaded = route.holes.length + route.project.length;
    await showMessage({
      title: "Импорт файлов",
      message: loaded ? `Загрузится файлов: ${loaded}. Не загрузятся:` : "Эти файлы пока не загружаются:",
      log: route.skipped.join("\n"),
    });
  }
  if (route.objects.length) openObjectsWizard(route.objects, prefer === "objects" ? "lines" : "objects", null, folder);
  if (route.holes.length) openDrillholeWizard(route.holes);
  if (route.project.length) await app.run("project.import", { path: route.project[0] });
  const opened = [route.objects.length && "состав импорта файлов", route.holes.length && "мастер импорта скважин", route.project.length && "импорт проекта"].filter(Boolean);
  if (opened.length) return `Открыт ${opened.join(", ")}`;
  return route.skipped.length ? "Файлы не загружены" : "Отменено";
}

export function bindImporting(app: App): void {
  const needProject = (ctx: { hasProject: boolean }) => (ctx.hasProject ? null : "Откройте или создайте проект");
  // trenches are holes of a set: the tree of sets follows their import (`import.drillholes` — in tables/bind.ts)
  onJobFinished((job) => {
    if (job.kind === "import.trenches" && job.status === "done") void loadSets(true);
  });
  watchImports();
  app.registry.bind(
    "import.open",
    (a) => {
      const mode: WizardMode = a.mode === "trenches" ? "trenches" : "holes";
      openDrillholeWizard([], mode);
      return mode === "trenches" ? "Открыт мастер импорта канав по полилиниям" : "Открыт мастер импорта скважин";
    },
    needProject,
  );
  const pickAndImport = async (filter: "objects" | "omf", raw: unknown) => {
    const folder = cleanFolder(raw);
    const paths = (await bridge()?.pickFiles(filter, true)) ?? [];
    if (!paths.length) return "Отменено";
    return importPaths(app, paths, "auto", folder);
  };
  app.registry.bind("import.files", (a) => pickAndImport(a.kind === "omf" ? "omf" : "objects", a.folder), needProject);
  app.registry.bind("omf.import", (a) => pickAndImport("omf", a.folder), needProject);
  app.registry.bind(
    "points.import",
    async (a) => {
      const mode: CloudMode = a.mode === "xyz" ? "xyz" : "las";
      const paths = (await bridge()?.pickFiles(mode, true)) ?? [];
      if (!paths.length) return "Отменено";
      const route = routeImport(paths, "objects");
      if (route.project.length) await showMessage({ title: CLOUD_TITLE, message: "Файл проекта .bfggis загружается командой «Импорт проекта»." });
      if (!route.objects.length) return "Файлы не загружены";
      openObjectsWizard(route.objects, "objects", mode);
      return mode === "las" ? "Открыт мастер «Облако точек» (LAS)" : "Открыт мастер «Облако точек» (XYZ)";
    },
    needProject,
  );
  for (const [command, pick] of [["lines.upload", "lines"], ["wireframes.upload", "wireframes"], ["rasters.upload", "rasters"]] as const) {
    app.registry.bind(
      command,
      async () => {
        const paths = (await bridge()?.pickFiles(pick, true)) ?? [];
        if (!paths.length) return "Отменено";
        const route = routeImport(paths, "objects");
        if (route.objects.length) openObjectsWizard(route.objects, pick);
        if (route.project.length) await showMessage({ title: "Импорт файлов", message: "Файл проекта .bfggis загружается командой «Импорт проекта»." });
        return route.objects.length ? "Открыт состав импорта файлов" : "Файлы не загружены";
      },
      needProject,
    );
  }
}
