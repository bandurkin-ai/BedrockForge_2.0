/**
 * Objects of the data tree (ADR 0011 §5): the list of wireframes follows the project; `object.select` — the
 * selected layer of lines, raster, wireframe or DTM (`AppState.selected`; a click in the 3D window calls it
 * too); `selection.clear` — «Снять выбор» (Esc); `wireframe.dtm` — «Перенести в ЦТМ» / «Вернуть в каркасы».
 */
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { KIND_TITLES, keepSelected, sameObject, type ObjectKind, type Selected } from "../../shell/selection";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { lineLayers, pickedObject, sceneObjects } from "../scene/objects";
import { cancelSectionDraw } from "../section/state";
import { clearWireframes, loadWireframes, setDtm, wireframeById, wireframesStore } from "./state";

const asText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте или создайте проект");

/** The name of an object of the open project; null — there is no such object. */
export function objectName(kind: ObjectKind, id: number): string | null {
  if (kind === "lines") return lineLayers().find((l) => l.id === id)?.name ?? null;
  if (kind === "raster") return sceneObjects.get().rasters.find((r) => r.id === id)?.name ?? null;
  if (kind === "wireframe") return wireframeById(id)?.name ?? null;
  return null;
}

/** «Выбран слой линий «Дороги»»; a DTM is called a DTM. */
export function selectedText(kind: ObjectKind, id: number, name: string): string {
  const t = KIND_TITLES[kind];
  const what = kind === "wireframe" && wireframeById(id)?.dtm ? "ЦТМ" : t.name;
  return `${t.chosen}${what === "ЦТМ" ? "а" : ""} ${what} «${name}»`;
}

export function bindObjects(app: App): void {
  const r = app.registry;
  const select = (selected: Selected | null) => {
    if (!sameObject(app.store.get().selected, selected)) app.store.set({ selected });
  };

  // --- the list of wireframes follows the project -------------------------------------------------------
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    select(null);
    clearWireframes();
    if (cur) void loadWireframes();
  });
  onCoreEvent((event) => {
    if (event === "objects.changed") void loadWireframes();
  });
  onJobFinished((job) => {
    if (job.status === "done" && (job.kind === "model.dtm" || job.kind.startsWith("import."))) void loadWireframes();
  });

  // --- the selection follows the lists and the 3D window ------------------------------------------------
  const exists = (kind: ObjectKind, id: number) => objectName(kind, id) !== null;
  const prune = () => {
    const o = sceneObjects.get();
    const s = app.store.get().selected;
    // a list that is not read yet (the project is opening) says nothing about its objects
    if (!s || (s.kind === "wireframe" ? !wireframesStore.get().loaded : !o.loaded)) return;
    select(keepSelected(s, exists));
  };
  sceneObjects.subscribe(prune);
  wireframesStore.subscribe(prune);
  let picked = pickedObject.get().picked;
  pickedObject.subscribe(() => {
    const before = picked;
    picked = pickedObject.get().picked;
    // a click on nothing in the 3D window: the object picked there is not selected any more
    if (before && !picked && sameObject(app.store.get().selected, { kind: before.kind, id: before.id })) select(null);
  });

  r.bind(
    "object.select",
    (a) => {
      const kind = a.kind as ObjectKind;
      const id = Number(a.id);
      if (kind === "blocks") throw new Error("Блочные модели появятся на этапе Э17");
      const name = objectName(kind, id);
      if (name === null) {
        const t = KIND_TITLES[kind];
        throw new Error(`${t.name[0].toUpperCase()}${t.name.slice(1)} №${id} ${t.missing}: обновите дерево проекта`);
      }
      select({ kind, id });
      return selectedText(kind, id, name);
    },
    needProject,
  );

  r.bind("selection.clear", async (_a, source) => {
    if (cancelSectionDraw()) return "Рисование разреза отменено";
    const had = app.store.get().selected !== null || pickedObject.get().picked !== null || app.store.get().holeId !== null;
    select(null);
    if (pickedObject.get().picked !== null) pickedObject.set({ picked: null });
    // the selected hole: its own command clears the scene, the card and the filter of the table
    const hole = r.get("hole.select");
    if (hole?.run && r.reason("hole.select") === null) await hole.run({ hole_id: null }, source);
    return had ? "Выбор снят" : "Ничего не выбрано";
  });

  r.bind(
    "wireframe.dtm",
    async (a) => {
      const selected = app.store.get().selected;
      const id = typeof a.wireframe_id === "number" ? a.wireframe_id : selected?.kind === "wireframe" ? selected.id : null;
      if (id === null) throw new Error("Выберите каркас в дереве или укажите wireframe_id");
      if (!wireframesStore.get().loaded) await loadWireframes();
      const w = wireframeById(id);
      if (!w) throw new Error(`Каркас №${id} не найден: обновите дерево проекта`);
      const on = a.on !== false;
      if (on === (w.dtm !== null)) return on ? `«${w.name}» уже в разделе «ЦТМ»` : `«${w.name}» уже в разделе «Каркасы»`;
      try {
        await setDtm(id, on);
      } catch (e) {
        throw new Error(asText(e));
      }
      return on ? `«${w.name}» перенесён в раздел «ЦТМ»` : `«${w.name}» возвращён в раздел «Каркасы»: настройки рельефа сброшены`;
    },
    needProject,
  );
}
