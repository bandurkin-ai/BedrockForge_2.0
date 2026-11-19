/**
 * «Менеджер данных» (Д-20; ADR 0017 §6): the command `data.manager` and what the window does with its rows — each
 * action is a registry command of the module that owns the object (`object.select`, `layer.show`, `blocks.show`,
 * `object.rename`, `object.delete`, `dataset.move`, `import.files`, `data.refresh`) or `project.export {ids, mode}` —
 * the window «Экспорт проекта › Объекты в файлы» with the chosen ids («Экспорт…», «Экспорт папки…»; «Исходный файл
 * → Сохранить копию как…» — `mode: "source"`, the format «Исходный файл»). An action that does not fit the chosen
 * rows is unavailable with a Russian reason. Deleting several rows asks once, in our own window with their names
 * (`askConfirm`, danger) — a call of Claude too. No React.
 */
import { bridge } from "../../bridge";
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm, showMessage } from "../../shell/dialogs";
import { needProject, needWritable, refusal, viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { showProperties } from "../../shell/treeRun";
import { counted } from "../../shell/treeActions";
import { openWindow } from "../../shell/windows";
import { loadBlocks } from "../blocks/state";
import { importPaths } from "../importing/bind";
import { moveIds } from "../projects/bind";
import { projectStore } from "../projects/state";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { catalogRowsOf, loadCatalog } from "./api";
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

  app.registry.bind("data.delete", (a, source) => deleteData(app, a, source), needWritable(app));
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

/**
 * «Экспорт…», «Экспорт папки…», «Исходный файл…»: the registry command `project.export {ids, mode}` — the window
 * «Экспорт проекта › Объекты в файлы» with the chosen objects (their types checked); `source` — only the rows that
 * have a source file, in the format «Исходный файл».
 */
export async function exportRows(app: App, rows: readonly CatalogRow[], source = false): Promise<string> {
  const chosen = source ? rows.filter((r) => r.format) : rows;
  const r = await app.registry.execute("project.export", { ids: chosen.map((x) => x.id).join(","), mode: source ? "source" : "objects" }, "user");
  if (!r.ok) throw new Error(r.result);
  return r.result;
}

/** What `DELETE` removes an object of the row — the requests of `object.delete`, without its reread (several
 * rows are deleted one after another, the lists are read once at the end). */
function deletePath(r: CatalogRow): string {
  const kind = objectKind(r.kind);
  if (kind === "wireframe") return `/wireframes/${r.id}`;
  if (kind === "raster") return `/rasters/${r.id}`;
  if (kind === "line_file") return `/lines/files/${r.id}`;
  if (kind === "blocks") return `/blocks/${r.id}`;
  throw new Error(`${kindTitle(r.kind)}: удаление — в дереве проекта`);
}

/** The question of «Удалить…» for several rows (our own window, danger); `claude` — Claude asks for it. */
export function deleteQuestion(ok: readonly CatalogRow[], left: number, claude = false) {
  const names = ok.slice(0, 10).map((r) => `• ${r.name} (${kindTitle(r.kind)})`);
  if (ok.length > 10) names.push(`… и ещё ${ok.length - 10}`);
  const skipped = left ? `\n${counted(left, "объект", "объекта", "объектов")} здесь не удаляются (наборы скважин — в дереве; строящиеся — после задания).` : "";
  const what = counted(ok.length, "объект", "объекта", "объектов");
  return {
    title: "Удалить объекты",
    message: `${claude ? `Claude просит удалить из проекта ${what}:` : `Удалить из проекта ${what}?`}\n${names.join("\n")}${skipped}\nОтменить удаление нельзя.`,
    okLabel: "Удалить",
    danger: true,
  };
}

/**
 * `data.delete {ids}`: several objects of the data in one registry command — one entry of the command log, one
 * question in our own window with the names of the objects, from the interface and from Claude alike (the command
 * is not `confirm`: the registry would ask with the bare ids — Э11 11.21). Rows the command does not delete
 * (drillhole sets, objects being built) are named in the result; an unknown id refuses all. The objects go one
 * after another; the catalog, the scene and the block models are read once at the end.
 */
export async function deleteData(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  try {
    const ids = moveIds(a);
    if (!ids.length) throw refusal("no_ids", "Не заданы объекты.", "Передайте ids — номера через запятую.");
    const { rows: chosen, missing } = await catalogRowsOf(ids);
    if (missing.length) throw refusal("object_not_found", `Объект №${missing[0]} не найден: ничего не удалено.`, "Обновите менеджер данных.");
    const ok = chosen.filter((r) => objectKind(r.kind) !== null && r.state !== "building");
    const left = chosen.filter((r) => !ok.includes(r));
    if (!ok.length) throw refusal("nothing_to_delete", "Эти объекты здесь не удаляются.", "Наборы скважин удаляются в дереве проекта; строящиеся — после конца задания.");
    if (!(await askConfirm(deleteQuestion(ok, left.length, source === "claude")))) return source === "claude" ? "Отменено пользователем" : "Отменено";
    const failed: string[] = [];
    for (const r of ok) {
      try {
        await api.del(deletePath(r));
      } catch (e) {
        failed.push(`${r.name}: ${errorText(e)}`);
      }
    }
    // once, after every object (after a failure too: what went is gone from the lists)
    const scene = ok.some((r) => objectKind(r.kind) !== "blocks");
    const blocks = ok.some((r) => objectKind(r.kind) === "blocks");
    await Promise.all([loadCatalog(), scene ? loadSceneObjects() : null, blocks ? loadBlocks() : null]);
    const kept = left.length ? `; не удаляются здесь: ${left.map((r) => `«${r.name}»`).join(", ")}` : "";
    if (failed.length === ok.length) throw new Error(`Ничего не удалено: ${failed.join("; ")}`);
    if (failed.length && source === "user") await showMessage({ title: "Удалить объекты", message: `Не удалено: ${failed.length} из ${ok.length}.`, log: failed.join("\n") });
    return `Удалено ${ok.length - failed.length} из ${ok.length}${failed.length ? ` (ошибки: ${failed.join("; ")})` : ""}${kept}`;
  } catch (e) {
    const text = errorText(e);
    if (source === "user") app.store.set({ statusNote: text });
    throw new Error(text);
  }
}

/** «Удалить…»: one row — `object.delete` (it asks itself); several — `data.delete` (one question). Both are
 * registry commands: each run is an entry of the command log. */
export async function deleteRows(app: App, rows: readonly CatalogRow[]): Promise<string> {
  const ok = rows.filter((r) => objectKind(r.kind) !== null && r.state !== "building");
  if (!ok.length) return "Нечего удалять";
  const r =
    ok.length === 1 && rows.length === 1
      ? await app.registry.execute("object.delete", { kind: objectKind(ok[0].kind), id: ok[0].id }, "user")
      : await app.registry.execute("data.delete", { ids: rows.map((x) => x.id).join(",") }, "user");
  if (!r.ok) throw new Error(r.result);
  return r.result;
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
      return exportRows(app, rows);
    case "source":
      return exportRows(app, rows, true);
    case "rename":
      return ok("object.rename", { kind: objectKind(one.kind), id: one.id });
    case "delete":
      return deleteRows(app, rows);
  }
}

// ——— import into a folder, files dropped from Explorer ———

/** «Импорт…» of a folder: the import wizard with that folder of destination (`import.files {folder}`, 11.11). */
export async function importInto(app: App, folder: string): Promise<void> {
  await app.run("import.files", folder ? { folder } : {});
}

/** Files dropped from Explorer on a folder of the manager: the import wizard with that folder of destination (the
 * same route as a drop on the app, `importing/bind.ts::importPaths`). */
export async function dropFiles(app: App, files: readonly File[], folder: string): Promise<string> {
  const b = bridge();
  const paths = b ? files.map((f) => b.pathForFile(f)).filter(Boolean) : [];
  if (!paths.length) return "Файлы не получены";
  const reason = app.registry.reason("import.files");
  if (reason) {
    await showMessage({ title: "Импорт файлов", message: reason });
    return reason;
  }
  return importPaths(app, paths, "auto", folder || null);
}

/** «Обновить»: the registry command `data.refresh` (the project, the jobs and the catalog read again). */
export async function refreshRows(app: App): Promise<string> {
  const r = await app.registry.execute("data.refresh", {}, "user");
  if (!r.ok) throw new Error(r.result);
  return r.result;
}

/** Rows dropped on a folder: `dataset.move` (one transaction in the core, then `objects.changed`). */
export async function moveRows(app: App, ids: readonly number[], folder: string): Promise<string> {
  const r = await app.registry.execute("dataset.move", { ids: ids.join(","), folder }, "user");
  if (!r.ok) throw new Error(r.result);
  return r.result;
}
