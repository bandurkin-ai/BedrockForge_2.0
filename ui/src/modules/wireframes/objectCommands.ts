/**
 * Rename, colour and delete of the objects of the data tree as registry commands (ADR 0011 §5; ТЗ 7.2: a
 * command that changes data is `confirm`): `object.rename`, `object.color`, `object.delete`. Without `kind`
 * and `id` they work on the selected object. A call from the interface asks in an own window (the new name,
 * «Удалить?»); a call from Claude was confirmed by the registry already.
 */
import type { App } from "../../shell/app";
import { askConfirm, askText } from "../../shell/dialogs";
import { needProject, noted } from "../../shell/objectRun";
import { normalizeColor } from "../../shell/props";
import type { ObjectKind } from "../../shell/selection";
import { counted } from "../../shell/treeActions";
import { selectWithProperties } from "../../shell/treeRun";
import { deleteLineFile, renameLayer, setLayerColor } from "../lines/actions";
import { findLayer } from "../lines/props";
import { deleteRaster, rasterById, renameRaster } from "../rasters/actions";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { objectName, objectWhat } from "./named";
import { deleteWireframe, renameWireframe, setWireframeColor } from "./state";

type Target = { kind: ObjectKind | "line_file"; id: number };

const capital = (text: string): string => text[0].toUpperCase() + text.slice(1);
const clean = (text: string): string => text.trim().replace(/\s+/g, " ");

/** The object of a command: `kind` and `id`, or the selected one. */
function target(app: App, a: Record<string, unknown>): Target {
  if (typeof a.kind === "string" && typeof a.id === "number") return { kind: a.kind as Target["kind"], id: a.id };
  if (a.kind !== undefined || a.id !== undefined) throw new Error("Укажите и kind, и id объекта — или ни того, ни другого: тогда берётся выбранный объект");
  const selected = app.store.get().selected;
  if (!selected) throw new Error("Объект не выбран: щёлкните слой линий, растр, каркас или ЦТМ в дереве или в 3D-окне");
  return selected;
}

function named(kind: ObjectKind, id: number): { name: string; what: string; fem: boolean } {
  const name = objectName(kind, id);
  const w = objectWhat(kind, id);
  if (name === null) throw new Error(`${capital(w.what)} №${id} не найден${w.fem ? "а" : ""}: обновите дерево проекта`);
  return { name, ...w };
}

export function bindObjectCommands(app: App): void {
  const r = app.registry;
  const ready = async () => {
    if (!sceneObjects.get().loaded) await loadSceneObjects();
  };

  r.bind(
    "object.rename",
    noted(app, async (a, source) => {
      await ready();
      const t = target(app, a);
      if (t.kind === "line_file" || t.kind === "blocks") throw new Error("Переименовать можно слой линий, растр, каркас или ЦТМ");
      const o = named(t.kind, t.id);
      let name = typeof a.name === "string" ? a.name : null;
      if (name === null) {
        if (source !== "user") throw new Error("Укажите новое название: name");
        name = await askText({ title: `Переименовать: ${o.what}`, label: "Название", initial: o.name, okLabel: "Переименовать" });
        if (name === null) return "Отменено";
      }
      name = clean(name);
      if (!name) throw new Error("Название не может быть пустым: введите название");
      if (name === o.name) return `Название «${o.name}» не изменено`;
      if (t.kind === "lines") await renameLayer(findLayer(sceneObjects.get().files, t.id)!.layer, name);
      else if (t.kind === "raster") await renameRaster(rasterById(t.id)!, name);
      else await renameWireframe(t.id, name);
      return `${capital(o.what)} «${o.name}» переименован${o.fem ? "а" : ""} в «${name}»`;
    }),
    needProject,
  );

  r.bind(
    "object.color",
    noted(app, async (a) => {
      await ready();
      const t = target(app, a);
      if (t.kind !== "lines" && t.kind !== "wireframe") throw new Error("Свой цвет есть у слоя линий, каркаса и ЦТМ: выберите один из них");
      const o = named(t.kind, t.id);
      if (!("color" in a)) {
        await selectWithProperties(app, t.kind, t.id);
        return `Цвет «${o.name}» выбирается в панели «Свойства»`;
      }
      const color = a.color === null ? null : normalizeColor(a.color);
      if (a.color !== null && color === null) throw new Error("Цвет задаётся как #rrggbb, например #f46a1a; null — без собственного цвета");
      if (t.kind === "lines") await setLayerColor(findLayer(sceneObjects.get().files, t.id)!.layer, color);
      else await setWireframeColor(t.id, color);
      return color ? `${capital(o.what)} «${o.name}»: цвет ${color}` : `${capital(o.what)} «${o.name}»: без собственного цвета`;
    }),
    needProject,
  );

  r.bind(
    "object.delete",
    noted(app, async (a, source) => {
      await ready();
      const t = target(app, a);
      const files = sceneObjects.get().files;
      if (t.kind === "lines") {
        const found = findLayer(files, t.id);
        if (!found) throw new Error(`Слой линий №${t.id} не найден: обновите дерево проекта`);
        const layers = counted(found.file.layers.length, "слой", "слоя", "слоёв");
        throw new Error(`Слой линий отдельно не удаляется. Удалите файл линий «${found.file.name}» (${layers}) — в его меню в дереве: «Удалить…».`);
      }
      if (t.kind === "blocks") throw new Error("Блочные модели появятся на этапе Э17");
      // own confirmation of a call from the interface; Claude's call was confirmed by the registry
      const ask = (title: string, message: string) => (source === "user" ? askConfirm({ title, message, okLabel: "Удалить", danger: true }) : Promise.resolve(true));
      if (t.kind === "line_file") {
        const file = files.find((f) => f.id === t.id);
        if (!file) throw new Error(`Файл линий №${t.id} не найден: обновите дерево проекта`);
        const layers = counted(file.layers.length, "слой", "слоя", "слоёв");
        if (!(await ask("Удалить файл линий", `Файл «${file.name}» удаляется из проекта со всеми слоями (${layers}). Отменить удаление нельзя.`))) return "Отменено";
        await deleteLineFile(file.id);
        return `Файл линий «${file.name}» удалён со всеми слоями (${layers})`;
      }
      const o = named(t.kind, t.id);
      if (t.kind === "raster") {
        if (!(await ask("Удалить растр", `Растр «${o.name}» удаляется из проекта вместе с файлом изображения. Отменить удаление нельзя.`))) return "Отменено";
        await deleteRaster(t.id);
      } else {
        if (!(await ask(`Удалить: ${o.what}`, `«${o.name}» удаляется из проекта. Отменить удаление нельзя.`))) return "Отменено";
        await deleteWireframe(t.id);
      }
      return `${capital(o.what)} «${o.name}» ${o.fem ? "удалена" : "удалён"}`;
    }),
    needProject,
  );
}
