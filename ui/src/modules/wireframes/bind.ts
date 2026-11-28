/**
 * Objects of the data tree (ADR 0011 §5): `object.select` — the selected layer of lines, raster, wireframe,
 * DTM or block model (`AppState.selected`; a click in the 3D window calls it too); `selection.clear` — «Снять выбор» (Esc);
 * `wireframe.dtm` — «Перенести в ЦТМ» / «Вернуть в каркасы» and the settings of a DTM; `wireframe.attr` — the
 * attributes of the user. Rename, colour and delete of any object — `objectCommands.ts`; the checks of a
 * wireframe — `checkCommands.ts`; the export of wireframes — `exportCommands.ts`.
 */
import type { App } from "../../shell/app";
import { bindTreeSelection } from "../../shell/multiSelect";
import { needProject, noted } from "../../shell/objectRun";
import { KIND_TITLES, keepSelected, sameObject, type ObjectKind, type Selected } from "../../shell/selection";
import { selectWithProperties } from "../../shell/treeRun";
import { dtmChanges, dtmSummary, hasSettings } from "../dtm/props";
import { blocksStore, loadBlocks } from "../blocks/state";
import { projectStore } from "../projects/state";
import { loadSceneObjects, pickedObject, sceneObjects } from "../scene/objects";
import { cancelSectionDraw } from "../section/state";
import { attrNameError, attrPatch, attrResult, findAttr, parseAttrValue } from "./attrs";
import { bindCheckCommands } from "./checkCommands";
import { bindExportCommands } from "./exportCommands";
import { objectName, selectedText, wireframeOf } from "./named";
import { bindObjectCommands } from "./objectCommands";
import { editAttrs, setDtm, setDtmSettings, wireframeById } from "./state";

export function bindObjects(app: App): void {
  const r = app.registry;
  const select = (selected: Selected | null) => {
    if (!sameObject(app.store.get().selected, selected)) app.store.set({ selected });
  };

  // --- the selection follows the project, the lists and the 3D window -----------------------------------
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    select(null);
  });
  // block models have their own list: `blocks/bind.ts` drops a selected one that is gone
  // implicit models have their own list too: `implicit/bind.ts` drops a selected one that is gone
  // resource reports have their own list too (`resources/ReportsTree.tsx`; Э20)
  const exists = (kind: ObjectKind, id: number) =>
    kind === "blocks" || kind === "implicit" || kind === "resource_report" || kind === "pit_scenario" || objectName(kind, id) !== null;
  sceneObjects.subscribe(() => {
    const s = app.store.get().selected;
    // lists that are not read yet (the project is opening) say nothing about their objects
    if (s && sceneObjects.get().loaded) select(keepSelected(s, exists));
  });
  let picked = pickedObject.get().picked;
  pickedObject.subscribe(() => {
    const before = picked;
    picked = pickedObject.get().picked;
    // a click on nothing in the 3D window: the object picked there is not selected any more
    if (before && !picked && sameObject(app.store.get().selected, { kind: before.kind, id: before.id })) select(null);
  });

  r.bind(
    "object.select",
    async (a) => {
      const kind = a.kind as ObjectKind;
      const id = Number(a.id);
      if (kind === "blocks" && !blocksStore.get().loaded) await loadBlocks();
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

  bindTreeSelection(app);

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
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const w = wireframeOf(app, a);
      const changes = dtmChanges(a);
      const on = changes.on !== false;
      if (!on || !hasSettings(changes)) {
        // only the move between the sections
        if (on === (w.dtm !== null)) return on ? `«${w.name}» уже в разделе «ЦТМ»` : `«${w.name}» уже в разделе «Каркасы»`;
        await setDtm(w.id, on);
        return on ? `«${w.name}» перенесён в раздел «ЦТМ»` : `«${w.name}» возвращён в раздел «Каркасы»: настройки рельефа сброшены`;
      }
      if (changes.raster_id) {
        const raster = sceneObjects.get().rasters.find((x) => x.id === changes.raster_id);
        if (!raster) throw new Error(`Растр №${changes.raster_id} не найден: обновите дерево проекта`);
        // ТЗ 7.20: a vertical raster is not put on a relief
        if (raster.vertical) throw new Error(`Растр «${raster.name}» стоит в вертикальной плоскости: на рельеф кладётся только растр в плане. Выберите карту или ортофото в плане.`);
      }
      const saved = await setDtmSettings(w.id, changes);
      const dtm = wireframeById(w.id)?.dtm ?? saved.dtm;
      if (!dtm) return `«${w.name}»: настройки рельефа сохранены`;
      const raster = dtm.raster_id === null ? null : (sceneObjects.get().rasters.find((x) => x.id === dtm.raster_id)?.name ?? null);
      return `${w.dtm ? "" : `«${w.name}» перенесён в раздел «ЦТМ». `}ЦТМ «${w.name}»: ${dtmSummary(dtm, raster)}`;
    }),
    needProject,
  );

  r.bind(
    "wireframe.attr",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const w = wireframeOf(app, a);
      const rename = typeof a.rename === "string" ? a.rename.trim().replace(/\s+/g, " ") : undefined;
      const remove = a.remove === true;
      if (!("value" in a) && rename === undefined && !remove) {
        await selectWithProperties(app, "wireframe", w.id);
        return `Атрибуты «${w.name}» — в панели «Свойства»: добавить, изменить, переименовать, удалить`;
      }
      const name = typeof a.name === "string" ? a.name.trim().replace(/\s+/g, " ") : "";
      if (!name) throw new Error("Укажите название атрибута: name");
      const attrs = w.attrs ?? [];
      const found = findAttr(attrs, name);
      if ((remove || rename !== undefined) && !found) throw new Error(`Атрибута «${name}» у «${w.name}» нет: обновите «Свойства»`);
      if (!remove && rename !== undefined) {
        const bad = attrNameError(rename, attrs, name);
        if (bad) throw new Error(bad);
      }
      const value = !remove && "value" in a ? (a.value === null ? null : parseAttrValue(String(a.value))) : undefined;
      const change = { name: found?.name ?? name, value, rename: remove ? undefined : rename, remove };
      await editAttrs(w.id, attrPatch(change));
      return `«${w.name}»: ${attrResult(change)}`;
    }),
    needProject,
  );

  bindObjectCommands(app);
  bindCheckCommands(app);
  bindExportCommands(app);
}
