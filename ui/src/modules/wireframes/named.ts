/**
 * How the objects of the data tree are called in messages (ADR 0011 §5): a layer of lines, a raster, a
 * wireframe — or, when it holds the settings of a relief, a DTM; a block model. No React.
 */
import type { App } from "../../shell/app";
import { refusal } from "../../shell/objectRun";
import { KIND_TITLES, type ObjectKind } from "../../shell/selection";
import { blockById } from "../blocks/state";
import { lineLayers, sceneObjects } from "../scene/objects";
import { wireframeById, type WireframeInfo } from "./state";

/** The name of an object of the open project; null — there is no such object. */
export function objectName(kind: ObjectKind, id: number): string | null {
  if (kind === "lines") return lineLayers().find((l) => l.id === id)?.name ?? null;
  if (kind === "raster") return sceneObjects.get().rasters.find((r) => r.id === id)?.name ?? null;
  if (kind === "wireframe") return wireframeById(id)?.name ?? null;
  if (kind === "blocks") return blockById(id)?.name ?? null;
  return null;
}

/** «слой линий», «растр», «каркас» — or «ЦТМ» (feminine: «переименована», «удалена»). */
export function objectWhat(kind: ObjectKind, id: number): { what: string; fem: boolean } {
  if (kind === "wireframe" && wireframeById(id)?.dtm) return { what: "ЦТМ", fem: true };
  return { what: KIND_TITLES[kind].name, fem: kind === "blocks" };
}

/** «Выбран слой линий «Дороги»»; a DTM is called a DTM. */
export function selectedText(kind: ObjectKind, id: number, name: string): string {
  const { what, fem } = objectWhat(kind, id);
  return `Выбран${fem ? "а" : ""} ${what} «${name}»`;
}

/** The wireframe of a command: `wireframe_id`, or the selected one. */
export function wireframeOf(app: App, a: Record<string, unknown>): WireframeInfo {
  const selected = app.store.get().selected;
  const id = typeof a.wireframe_id === "number" ? a.wireframe_id : selected?.kind === "wireframe" ? selected.id : null;
  if (id === null) throw refusal("no_wireframe", "Каркас не выбран.", "Выберите каркас в дереве проекта или укажите wireframe_id.");
  const w = wireframeById(id);
  if (!w) throw refusal("wireframe_not_found", `Каркас №${id} не найден.`, "Обновите дерево проекта.");
  return w;
}
