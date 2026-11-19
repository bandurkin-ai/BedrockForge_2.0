/**
 * Commands of DTMs (ТЗ 7.20, 7.35 Д-07, Д-22; ADR 0011 §5, ADR 0014 §6): `dtm.settings` — «Настройки ЦТМ…»: the
 * DTM is selected and its settings stand at the top of «Свойства»; `model.dtm` — the window «Построение ЦТМ» (job
 * `model.dtm`); `ground.classify` — the window «Фильтр земли», or with its parameters the job at once;
 * `terrain.from_points` — the window «Построение ЦТМ» with the cloud, or with its parameters the job at once.
 */
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import { needProject, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { selectWithProperties } from "../../shell/treeRun";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { loadSceneObjects, sceneObjects, type LineLayerInfo } from "../scene/objects";
import { dtms, wireframeById } from "../wireframes/state";
import { fromPointsBody } from "./cloud";
import { bodyOfArgs, cloudLayers, pickCloud, profileTitle } from "./ground";
import { loadGroundProfiles } from "./groundProfiles";

/** The cloud of a command: `cloud_id`, else the selected cloud, else the only one; a refusal when there is none. */
async function commandCloud(app: App, a: Record<string, unknown>, needed: boolean): Promise<LineLayerInfo | null> {
  if (!sceneObjects.get().loaded) await loadSceneObjects();
  const files = sceneObjects.get().files;
  const clouds = cloudLayers(files);
  if (!clouds.length) throw refusal("no_cloud", "В проекте нет облаков точек.", "Загрузите LAS или таблицу XYZ: «Главная › Загрузка › Облако точек».");
  const asked = typeof a.cloud_id === "number" ? a.cloud_id : null;
  if (asked !== null && !clouds.some((l) => l.id === asked)) {
    const layer = files.some((f) => f.layers.some((l) => l.id === asked));
    throw layer ? refusal("not_a_cloud", `Слой №${asked} — не облако точек.`, "Выберите слой-облако (значок облака в разделе «Линии»).") : refusal("line_layer_not_found", `Облако №${asked} не найдено.`, "Обновите дерево проекта.");
  }
  const selected = app.store.get().selected;
  const id = pickCloud(files, asked, selected?.kind === "lines" ? selected.id : null);
  if (id === null && needed) throw refusal("no_cloud", "Не выбрано облако точек.", "Выберите облако в дереве (раздел «Линии») или укажите cloud_id.");
  return clouds.find((l) => l.id === id) ?? null;
}

/** Any parameter but `cloud_id` runs the job at once; without them the window opens. */
const direct = (a: Record<string, unknown>): boolean => Object.keys(a).some((k) => k !== "cloud_id");

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
    "ground.classify",
    noted(app, async (a) => {
      const now = direct(a);
      const cloud = await commandCloud(app, a, now);
      if (!now) {
        openWindow("dtm-ground", "Фильтр земли", "form", cloud ? { cloudId: cloud.id } : undefined, ...viewport());
        return cloud ? `Открыто окно «Фильтр земли» для облака «${cloud.name}»` : "Открыто окно «Фильтр земли»: выберите облако";
      }
      const body = bodyOfArgs(a);
      await api.post(`/lines/layers/${cloud!.id}/classify`, body);
      showJobs(app);
      await refreshJobs();
      return `Запущен «Фильтр земли» облака «${cloud!.name}» (профиль «${profileTitle(await loadGroundProfiles(), body.profile)}»): ход — на вкладке «Задания»`;
    }),
    needProject,
  );

  r.bind(
    "terrain.from_points",
    noted(app, async (a) => {
      const now = direct(a);
      const cloud = await commandCloud(app, a, now);
      if (!now) {
        openWindow("dtm-build", "Построение ЦТМ", "form", cloud ? { cloudId: cloud.id } : undefined, ...viewport());
        return cloud ? `Открыто окно «Построение ЦТМ» с облаком «${cloud.name}»: классы, шаг прореживания и «Построить»` : "Открыто окно «Построение ЦТМ»: отметьте облако";
      }
      const { body, error } = fromPointsBody(cloud!.id, a);
      if (!body) throw refusal("bad_args", error ?? "Параметры заданы неверно.", "Исправьте параметры команды.");
      await api.post("/wireframes/from-points", body);
      showJobs(app);
      await refreshJobs();
      return `Запущено «Построение ЦТМ» из облака «${cloud!.name}» (классы ${body.classes.join(", ")}): ЦТМ появится в разделе «ЦТМ»`;
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
