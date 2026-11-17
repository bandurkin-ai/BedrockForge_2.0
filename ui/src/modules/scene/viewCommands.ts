/**
 * Commands that need the live scene (ТЗ 7.13): the standard views (T, X, Y, iso, the section plane),
 * «Вписать» (F) and the selected hole (`hole.select`, Esc clears it). A hole selected in 3D is selected in
 * the «Таблица» panel and shown in «Свойства» (HoleCard reads tablesStore.hole) — and the other way round.
 */
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { VizError } from "../../viz/error";
import { holeIndex } from "../../viz/holes/manifest";
import type { ViewPreset } from "../../viz/core/views";
import { cancelSectionDraw } from "../section/state";
import { filterHole, selectSet, tablesStore } from "../tables/state";
import { fpsStore, runSmoke } from "./fps";
import { currentScene, requireScene } from "./sceneHost";

const VIEW_TITLES: Record<ViewPreset, string> = {
  top: "в план (сверху)",
  front: "сбоку Z/X (на север)",
  side: "сбоку Z/Y (на запад)",
  iso: "изометрия",
  section: "в плоскость разреза",
};

const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте или создайте проект");

const vizText = (e: unknown) => (e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e));

export function bindViewCommands(app: App): void {
  const r = app.registry;
  r.bind(
    "scene.view",
    (a) => {
      const preset = String(a.view) as ViewPreset;
      if (!(preset in VIEW_TITLES)) throw new Error("Вид: top, front, side, iso или section");
      try {
        requireScene().view(preset);
      } catch (e) {
        throw new Error(vizText(e));
      }
      return `Вид ${VIEW_TITLES[preset]}`;
    },
    needProject,
  );
  r.bind(
    "scene.fit",
    () => {
      requireScene().fit();
      return app.store.get().view.clip ? "Вписаны объекты в окне разреза" : "Вписаны все видимые объекты";
    },
    needProject,
  );

  r.bind(
    "diag.fps",
    async (a) => {
      if (a.smoke === true) {
        const scene = requireScene();
        if (fpsStore.get().running) throw new Error("Дымовой сценарий уже идёт");
        fpsStore.set({ open: true });
        return `Дымовой сценарий: ${await runSmoke(scene)}`;
      }
      const open = typeof a.open === "boolean" ? a.open : !fpsStore.get().open;
      fpsStore.set({ open });
      return open ? "Счётчик кадров показан" : "Счётчик кадров скрыт";
    },
    needProject,
  );

  r.bind("hole.select", async (a) => {
    const name = a.hole_id === null || a.hole_id === undefined || a.hole_id === "" ? null : String(a.hole_id);
    if (name === null) {
      if (cancelSectionDraw()) return "Рисование разреза отменено";
      app.store.set({ holeId: null });
      currentScene()?.select(null);
      if (tablesStore.get().hole) void filterHole(null);
      return "Выбор скважины снят";
    }
    const scene = currentScene();
    const t = tablesStore.get();
    const prefer = typeof a.set_id === "number" ? [a.set_id] : [t.setId, ...(scene?.debug().sets ?? [])];
    let found: { setId: number; id: number } | null = null;
    for (const setId of prefer) {
      if (setId === null) continue;
      const layer = scene?.holes(setId);
      const i = layer ? holeIndex(layer.manifest, name) : -1;
      if (layer && i >= 0) {
        found = { setId, id: layer.manifest.id[i] };
        break;
      }
    }
    if (!found) {
      if (scene) throw new Error(`Скважина «${name}» не найдена в 3D-окне`);
      app.store.set({ holeId: name });
      return `Выбрана скважина ${name}`;
    }
    app.store.set({ holeId: name });
    scene?.select({ setId: found.setId, name });
    if (t.setId !== found.setId) await selectSet(found.setId);
    void filterHole({ id: found.id, name });
    return `Выбрана скважина ${name}`;
  });

  // The other way: a hole picked in the table is selected in 3D and in the status bar.
  let last: string | null = null;
  tablesStore.subscribe(() => {
    const s = tablesStore.get();
    const name = s.hole?.name ?? null;
    if (name === last) return;
    last = name;
    app.store.set({ holeId: name });
    currentScene()?.select(name !== null && s.setId !== null ? { setId: s.setId, name } : null);
  });
}
