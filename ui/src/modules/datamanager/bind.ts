/**
 * «Менеджер данных» (Д-20; ADR 0017 §6): the command `data.manager` and what the window does with its rows — each
 * action is a registry command of the module that owns the object (`object.select`, `layer.show`, `blocks.show`,
 * `object.rename`, `object.delete`, `dataset.move`, `import.files`) or the window «Экспорт проекта › Объекты в
 * файлы» with the chosen ids («Экспорт…», «Исходный файл → Сохранить копию как…» — the format «Исходный файл»).
 * An action that does not fit the chosen rows is unavailable with a Russian reason. Deleting several rows asks
 * once, in our own window (`askConfirm`, danger). No React.
 */
import { bridge } from "../../bridge";
import type { App } from "../../shell/app";
import { askConfirm, showMessage } from "../../shell/dialogs";
import { needProject, viewport } from "../../shell/objectRun";
import { showProperties } from "../../shell/treeRun";
import { counted } from "../../shell/treeActions";
import { openWindow } from "../../shell/windows";
import { importPaths } from "../importing/bind";
import { projectStore } from "../projects/state";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { kindTitle, type CatalogRow } from "./catalog";

export const DATA_MANAGER = "data-manager";

export function dataManagerTitle(project: string | null | undefined): string {
  return `Менеджер данных · ${project ?? "проект"}`;
}

export function openDataManager(app: App): string {
  const name = projectStore.get().current?.name ?? app.store.get().projectName;
  return openWindow(DATA_MANAGER, dataManagerTitle(name), "normal", undefined, ...viewport());
}

export function bindDataManager(app: App): void {
  app.registry.bind(
    "data.manager",
    () => {
      openDataManager(app);
      return "Открыт «Менеджер данных»";
    },
    needProject,
  );
}

// ——— the actions of the rows ———

export type ActionId = "properties" | "show" | "export" | "source" | "rename" | "delete";

export interface RowAction {
  id: ActionId;
  label: string;
  /** null — available; a Russian text — why not */
  reason: string | null;
}

/** The kind of the commands `object.*` for a row of the catalog; null — the commands do not take it. */
export function objectKind(kind: string): "wireframe" | "raster" | "line_file" | "blocks" | null {
  if (kind === "wireframe" || kind === "dtm") return "wireframe";
  if (kind === "raster") return "raster";
  if (kind === "line_file" || kind === "cloud") return "line_file";
  if (kind === "block_model") return "blocks";
  return null;
}

const single = (rows: readonly CatalogRow[]): string | null => (rows.length === 1 ? null : rows.length ? "Выберите одну строку" : "Выберите строку");
const building = (rows: readonly CatalogRow[]): string | null => (rows.some((r) => r.state === "building") ? "Объект ещё строится: дождитесь конца задания" : null);

/** The actions of the menu and of the toolbar for the chosen rows, with the reasons of the unavailable ones. */
export function rowActions(rows: readonly CatalogRow[], commandReason: (command: string) => string | null = () => null): RowAction[] {
  const none = rows.length ? null : "Выберите строки";
  const busy = building(rows);
  const deletable = rows.filter((r) => objectKind(r.kind) !== null);
  const sourced = rows.filter((r) => r.format);
  const one = rows[0];
  return [
    { id: "properties", label: "Свойства", reason: single(rows) ?? busy },
    { id: "show", label: "Показать в 3D", reason: none ?? busy ?? (rows.every((r) => r.kind === "block_model") ? commandReason("blocks.show") : null) },
    { id: "export", label: "Экспорт…", reason: none ?? busy },
    { id: "source", label: "Исходный файл → Сохранить копию как…", reason: none ?? busy ?? (sourced.length ? null : rows.length === 1 ? "Объект создан в программе: исходного файла нет" : "У выбранных объектов нет исходных файлов") },
    {
      id: "rename",
      label: "Переименовать…",
      reason:
        single(rows) ??
        busy ??
        (one.kind === "line_file" || one.kind === "cloud" ? "Файл линий не переименовывается: переименуйте его слой в дереве" : objectKind(one.kind) ? null : `${kindTitle(one.kind)}: переименование — в дереве проекта`),
    },
    { id: "delete", label: "Удалить…", reason: none ?? busy ?? (deletable.length ? null : `${kindTitle(rows[0].kind)}: удаление — в дереве проекта`) },
  ];
}

/** The layers of a file of lines (a cloud is a file with one layer) as the scene knows them. */
async function layersOf(fileId: number): Promise<number[]> {
  if (!sceneObjects.get().loaded) await loadSceneObjects();
  return sceneObjects.get().files.find((f) => f.id === fileId)?.layers.map((l) => l.id) ?? [];
}

/** The window «Экспорт проекта › Объекты в файлы» with the chosen objects (their types checked). */
export function exportRows(rows: readonly CatalogRow[], source = false): string {
  const chosen = source ? rows.filter((r) => r.format) : [...rows];
  const types = [...new Set(chosen.map((r) => r.kind))];
  const props: Record<string, unknown> = { mode: "objects", ids: chosen.map((r) => r.id), types };
  if (source) props.formats = Object.fromEntries(types.map((k) => [k, "source"]));
  return openWindow("project-export", "Экспорт проекта", "form", props, ...viewport());
}

/** Runs a bound command as already confirmed: the person said «Удалить» once for all the rows. */
async function runConfirmed(app: App, id: string, args: Record<string, unknown>): Promise<string> {
  const c = app.registry.get(id);
  if (!c?.run) throw new Error(`Команда «${id}» недоступна`);
  const out = await c.run(args, "claude"); // "claude": the handler does not ask again (it was asked here)
  return out || "Выполнено";
}

/** «Удалить…»: one row — the command asks itself; several — one own question (danger), then each of them. */
export async function deleteRows(app: App, rows: readonly CatalogRow[]): Promise<string> {
  const ok = rows.filter((r) => objectKind(r.kind) !== null && r.state !== "building");
  const left = rows.length - ok.length;
  if (!ok.length) return "Нечего удалять";
  if (ok.length === 1 && !left) {
    const r = await app.registry.execute("object.delete", { kind: objectKind(ok[0].kind), id: ok[0].id }, "user");
    if (!r.ok) throw new Error(r.result);
    return r.result;
  }
  const names = ok.slice(0, 10).map((r) => `• ${r.name} (${kindTitle(r.kind)})`);
  if (ok.length > 10) names.push(`… и ещё ${ok.length - 10}`);
  const skipped = left ? `\n${counted(left, "объект", "объекта", "объектов")} здесь не удаляются (наборы скважин — в дереве).` : "";
  const yes = await askConfirm({
    title: "Удалить объекты",
    message: `Удалить из проекта ${counted(ok.length, "объект", "объекта", "объектов")}?\n${names.join("\n")}${skipped}\nОтменить удаление нельзя.`,
    okLabel: "Удалить",
    danger: true,
  });
  if (!yes) return "Отменено";
  const failed: string[] = [];
  for (const r of ok) {
    try {
      await runConfirmed(app, "object.delete", { kind: objectKind(r.kind), id: r.id });
    } catch (e) {
      failed.push(`${r.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  if (failed.length) await showMessage({ title: "Удалить объекты", message: `Не удалено: ${failed.length} из ${ok.length}.`, log: failed.join("\n") });
  return `Удалено ${ok.length - failed.length} из ${ok.length}`;
}

/** Runs an action of the menu or of the toolbar for the chosen rows. */
export async function runRowAction(app: App, id: ActionId, rows: readonly CatalogRow[]): Promise<string> {
  const action = rowActions(rows, (c) => app.registry.reason(c)).find((a) => a.id === id);
  if (!action) throw new Error("Неизвестное действие");
  if (action.reason) throw new Error(action.reason);
  const one = rows[0];
  const ok = async (command: string, args: Record<string, unknown>) => {
    const r = await app.registry.execute(command, args, "user");
    if (!r.ok) throw new Error(r.result);
    return r.result;
  };
  switch (id) {
    case "properties": {
      if (one.kind === "drillhole_set") {
        const out = await ok("set.select", { set_id: one.id });
        showProperties(app);
        return out;
      }
      const kind = objectKind(one.kind)!;
      const target = kind === "line_file" ? (await layersOf(one.id))[0] : one.id;
      if (target === undefined) throw new Error("В файле линий нет слоёв");
      const out = await ok("object.select", { kind: kind === "line_file" ? "lines" : kind, id: target });
      showProperties(app);
      return out;
    }
    case "show": {
      const done: string[] = [];
      for (const r of rows) {
        if (r.kind === "drillhole_set") done.push(await ok("layer.show", { layer: "set", id: r.id, visible: true }));
        else if (r.kind === "block_model") {
          if (!app.registry.reason("blocks.show")) done.push(await ok("blocks.show", { id: r.id })); // Э17: shown later
        }
        else if (r.kind === "line_file" || r.kind === "cloud") for (const l of await layersOf(r.id)) done.push(await ok("layer.show", { layer: "lines", id: l, visible: true }));
        else done.push(await ok("layer.show", { layer: objectKind(r.kind)!, id: r.id, visible: true }));
      }
      return done.length === 1 ? done[0] : `Показано в 3D: ${done.length}`;
    }
    case "export":
      exportRows(rows);
      return "Открыто окно «Экспорт проекта» с выбранными объектами";
    case "source":
      exportRows(rows, true);
      return "Открыто окно «Экспорт проекта»: исходные файлы выбранных объектов";
    case "rename":
      return ok("object.rename", { kind: objectKind(one.kind), id: one.id });
    case "delete":
      return deleteRows(app, rows);
  }
}

// ——— import into a folder, files dropped from Explorer ———

/** Whether `import.files` takes the folder of destination (it does from 11.11 on). */
export const importTakesFolder = (app: App): boolean => Boolean(app.registry.get("import.files")?.params?.folder);

/** «Импорт…» of a folder: the wizard with the folder of destination (while `import.files` has no `folder`, the
 * objects go to the root and the status bar says so). */
export async function importInto(app: App, folder: string): Promise<void> {
  if (importTakesFolder(app)) return void (await app.run("import.files", folder ? { folder } : {}));
  await app.run("import.files");
  if (folder) app.store.set({ statusNote: `Папка назначения «${folder}» при импорте ещё не задаётся: перенесите объекты на папку после загрузки.` });
}

/** Files dropped from Explorer on a folder of the manager: the import wizard (the same route as a drop on the app). */
export async function dropFiles(app: App, files: readonly File[], folder: string): Promise<string> {
  const b = bridge();
  const paths = b ? files.map((f) => b.pathForFile(f)).filter(Boolean) : [];
  if (!paths.length) return "Файлы не получены";
  const reason = app.registry.reason("import.files");
  if (reason) {
    await showMessage({ title: "Импорт файлов", message: reason });
    return reason;
  }
  const out = await importPaths(app, paths);
  if (folder && !importTakesFolder(app)) app.store.set({ statusNote: `Папка назначения «${folder}» при импорте ещё не задаётся: перенесите объекты на папку после загрузки.` });
  return out;
}

/** Rows dropped on a folder: `dataset.move` (one transaction in the core, then `objects.changed`). */
export async function moveRows(app: App, ids: readonly number[], folder: string): Promise<string> {
  const r = await app.registry.execute("dataset.move", { ids: ids.join(","), folder }, "user");
  if (!r.ok) throw new Error(r.result);
  return r.result;
}
