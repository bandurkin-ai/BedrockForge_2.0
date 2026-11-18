/**
 * Commands of DTMs (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5): `dtm.settings` — «Настройки ЦТМ…»: the DTM is selected
 * and its settings stand at the top of «Свойства»; `model.dtm` — the window «Построение ЦТМ» (job `model.dtm`).
 */
import type { App } from "../../shell/app";
import { needProject, noted, viewport } from "../../shell/objectRun";
import { selectWithProperties } from "../../shell/treeRun";
import { openWindow } from "../../shell/windows";
import { loadSceneObjects, sceneObjects } from "../scene/objects";
import { dtms, wireframeById } from "../wireframes/state";

export function bindDtm(app: App): void {
  const r = app.registry;

  r.bind(
    "dtm.settings",
    noted(app, async (a) => {
      if (!sceneObjects.get().loaded) await loadSceneObjects();
      const list = dtms(sceneObjects.get().wireframes);
      const selected = app.store.get().selected;
      const chosen = selected?.kind === "wireframe" && wireframeById(selected.id)?.dtm ? selected.id : null;
      const id = typeof a.wireframe_id === "number" ? a.wireframe_id : (chosen ?? (list.length === 1 ? list[0].id : null));
      if (id === null) {
        throw new Error(list.length ? "Выберите ЦТМ в дереве (раздел «ЦТМ») или укажите wireframe_id" : "В проекте нет ЦТМ: постройте её («Построить ЦТМ») или перенесите каркас поверхности («В ЦТМ»)");
      }
      const w = wireframeById(id);
      if (!w) throw new Error(`ЦТМ №${id} не найдена: обновите дерево проекта`);
      if (!w.dtm) throw new Error(`«${w.name}» — каркас, а не ЦТМ: сначала перенесите его в раздел «ЦТМ» («В ЦТМ»)`);
      await selectWithProperties(app, "wireframe", id);
      return `Настройки ЦТМ «${w.name}» — вверху панели «Свойства»`;
    }),
    needProject,
  );

  r.bind(
    "model.dtm",
    () => {
      openWindow("dtm-build", "Построение ЦТМ", "form", undefined, ...viewport());
      return "Открыто окно «Построение ЦТМ»";
    },
    needProject,
  );
}
