/** Commands of import (ТЗ 7.7, 7.8): the drillhole wizard and the frame «Импорт файлов». */
import { bridge } from "../../bridge";
import type { App } from "../../shell/app";
import { showMessage } from "../../shell/dialogs";
import { detectImportKind, IMPORT_TITLES, type ImportKind } from "../../shell/tree";
import { openWindow } from "../../shell/windows";

/** Formats of the other object types arrive with their stages (ТЗ 7.8). */
const LATER: Partial<Record<ImportKind, string>> = { lines: "Э7", rasters: "Э7", wireframes: "Э8", omf: "Э11" };

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

export function openDrillholeWizard(paths: string[] = []): void {
  openWindow("import-drillholes", "Импорт скважин", "normal", paths.length ? { paths } : undefined, ...viewport());
}

/** Files from the dialog or dropped from Explorer: the kind decides the wizard. */
export async function importPaths(app: App, paths: string[]): Promise<string> {
  const kind = detectImportKind(paths);
  if (!kind) {
    await showMessage({ title: "Импорт файлов", message: "Тип файлов не распознан. Поддерживаются DAT, CSV, Excel, TRIDB, DXF, OBJ, STL, STR, растры с привязкой, OMF, .bfggis." });
    return "Тип файлов не распознан";
  }
  if (kind === "holes") {
    openDrillholeWizard(paths);
    return "Открыт мастер импорта скважин";
  }
  if (kind === "project") {
    await app.run("project.import", { path: paths[0] });
    return "Открыт импорт проекта";
  }
  const message = `Импорт: ${IMPORT_TITLES[kind]} — появится на этапе ${LATER[kind] ?? "разработки"}.`;
  await showMessage({ title: "Импорт файлов", message });
  return message;
}

export function bindImporting(app: App): void {
  const needProject = (ctx: { hasProject: boolean }) => (ctx.hasProject ? null : "Откройте или создайте проект");
  app.registry.bind(
    "import.open",
    () => {
      openDrillholeWizard();
      return "Открыт мастер импорта скважин";
    },
    needProject,
  );
  app.registry.bind(
    "import.files",
    async () => {
      const paths = (await bridge()?.pickFiles("tables", true)) ?? [];
      if (!paths.length) return "Отменено";
      return importPaths(app, paths);
    },
    needProject,
  );
}
