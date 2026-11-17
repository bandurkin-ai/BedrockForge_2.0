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

const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;

/** Files from the dialog or dropped from Explorer, file by file: drillhole tables go to their wizard, a
 * `.bfggis` to the project import; the composition says what is not loaded yet and when (ТЗ 7.8). */
export async function importPaths(app: App, paths: string[]): Promise<string> {
  const groups = new Map<ImportKind | null, string[]>();
  for (const p of paths) {
    const kind = detectImportKind([p]);
    groups.set(kind, [...(groups.get(kind) ?? []), p]);
  }
  const skipped: string[] = [];
  for (const [kind, files] of groups) {
    const names = files.map(baseName).join(", ");
    if (kind === null) skipped.push(`${names} — тип файла не распознан`);
    else if (kind !== "holes" && kind !== "project") skipped.push(`${names} — ${IMPORT_TITLES[kind]}: появится на этапе ${LATER[kind] ?? "разработки"}`);
  }
  const holes = groups.get("holes") ?? [];
  const project = groups.get("project") ?? [];
  if (skipped.length) {
    const loaded = holes.length + project.length;
    await showMessage({
      title: "Импорт файлов",
      message: loaded ? `Загрузится файлов: ${loaded}. Не загрузятся:` : "Эти файлы пока не загружаются:",
      log: skipped.join("\n"),
    });
  }
  if (holes.length) openDrillholeWizard(holes);
  if (project.length) await app.run("project.import", { path: project[0] });
  if (holes.length) return "Открыт мастер импорта скважин";
  if (project.length) return "Открыт импорт проекта";
  return skipped.length ? "Файлы не загружены" : "Отменено";
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
      const paths = (await bridge()?.pickFiles("objects", true)) ?? [];
      if (!paths.length) return "Отменено";
      return importPaths(app, paths);
    },
    needProject,
  );
}
