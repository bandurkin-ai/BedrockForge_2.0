/** The 3D view commands that do not need the scene itself: scene.style, layer.show and the view settings
 * of the project (ТЗ 7.0.10, ADR 0007 §2). The scene module adds the rest when the 3D chunk loads. */
import type { App } from "../../shell/app";
import { api } from "../../shell/api";
import { Store } from "../../shell/store";
import { DEFAULT_VIEW, VEXAG_STEPS, type ViewState } from "../../shell/workspace";
import { beforeProjectChange, projectStore } from "../projects/state";
import { openWindow } from "../../shell/windows";
import { onJobFinished } from "../jobs/state";
import { tablesStore } from "../tables/state";
import { hiddenKey, patchFields, toggleHidden, ViewSync, type ViewIO } from "./viewSync";
import { clearedKeys, effectiveHidden, normalizeHideAll, showNew, showUnderHideAll, topKey } from "./hideAll";

const SUB_LAYERS = ["collars", "paths", "intervals"] as const;
import { bindViewCommands } from "./viewCommands";

/** Every view key of the open project as the core keeps it (styles of collars and traces, displays…). */
export const viewStore = new Store<Record<string, unknown>>({});

const io: ViewIO = {
  get: () => api.get("/view"),
  put: (key, value, expectedVersion) =>
    api.put(`/view/${key}`, { value, expected_version: expectedVersion, project: projectStore.get().current?.uid ?? null }),
};

let sync: ViewSync | null = null;

/** Change a view key of the project (applied now, written shortly). */
export function updateView(key: string, op: (v: unknown) => unknown): unknown {
  if (!sync) throw new Error("Настройки вида недоступны: откройте проект");
  return sync.update(key, op);
}

export function viewSyncOf(): ViewSync | null {
  return sync;
}

const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** Object keys of the project the scene can show (sets now; wireframes, lines… with their stages). */
export function objectKeys(): string[] {
  return tablesStore.get().sets.map((s) => `set:${s.id}`);
}

/** The part of the view kept in the app store (ribbon, status bar, Claude). `hidden` there is the effective
 * list: with «Очистить окно» on, every object not shown again (ADR 0008). */
export function viewFromKeys(keys: Record<string, unknown>, base: ViewState = DEFAULT_VIEW, objects: readonly string[] = objectKeys()): ViewState {
  const scene = (keys.scene ?? {}) as Record<string, unknown>;
  const section = (keys.section ?? {}) as Record<string, unknown>;
  const d = DEFAULT_VIEW;
  return {
    ...base,
    perspective: typeof scene.perspective === "boolean" ? scene.perspective : d.perspective,
    grid: typeof scene.grid === "boolean" ? scene.grid : d.grid,
    vexag: VEXAG_STEPS.includes(scene.vexag as number) ? (scene.vexag as number) : d.vexag,
    wireframeOpacity: num(scene.wireframe_opacity, d.wireframeOpacity),
    clip: section.clip === true,
    hidden: effectiveHidden(
      Array.isArray(keys.hidden) ? (keys.hidden as unknown[]).filter((k): k is string => typeof k === "string") : [],
      normalizeHideAll(keys.hide_all),
      objects,
    ),
  };
}

export function bindScene(app: App): void {
  const { registry, store } = app;
  sync = new ViewSync(
    io,
    (key, value) => {
      viewStore.set({ [key]: value });
      if (key === "scene" || key === "section" || key === "hidden" || key === "hide_all") store.set((s) => ({ view: viewFromKeys(viewStore.get(), s.view) }));
    },
    (text) => store.set({ statusNote: text }),
  );
  beforeProjectChange.add(() => sync?.flushAll() ?? Promise.resolve());
  // closing the window: send what is pending (the requests go out before the page is gone)
  if (typeof window !== "undefined") window.addEventListener("pagehide", () => void sync?.flushAll());
  let uid: string | null = null;
  // the effective hidden list follows the objects of the project
  let setsRef = tablesStore.get().sets;
  tablesStore.subscribe(() => {
    if (tablesStore.get().sets === setsRef) return;
    setsRef = tablesStore.get().sets;
    store.set((s) => ({ view: viewFromKeys(viewStore.get(), s.view) }));
  });
  // a set just imported is shown also after «Очистить окно» (ТЗ 17.4.7)
  onJobFinished((job) => {
    const setId = job.result?.set_id;
    if (job.kind !== "import.drillholes" || job.status !== "done" || typeof setId !== "number") return;
    if (projectStore.get().current && normalizeHideAll(viewStore.get().hide_all).on) updateView("hide_all", showNew([`set:${setId}`]));
  });
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    viewStore.set(Object.fromEntries(Object.keys(viewStore.get()).map((k) => [k, undefined])));
    store.set({ view: DEFAULT_VIEW });
    if (!cur || !sync) {
      sync?.reset();
      return;
    }
    void sync
      .load()
      .then((keys) => {
        viewStore.set(keys);
        store.set((s) => ({ view: viewFromKeys(keys, s.view) }));
      })
      .catch(() => store.set({ statusNote: "Настройки вида проекта не прочитаны: показаны умолчания." }));
  });

  registry.bind("scene.style", (a) => {
    const patch: Record<string, unknown> = {};
    if (typeof a.vexag === "number") {
      if (!VEXAG_STEPS.includes(a.vexag)) throw new Error("Преувеличение: 1, 2, 3 или 5");
      patch.vexag = a.vexag;
    }
    if (typeof a.grid === "boolean") patch.grid = a.grid;
    if (typeof a.perspective === "boolean") patch.perspective = a.perspective;
    if (typeof a.wireframe_opacity === "number") {
      if (a.wireframe_opacity < 0.1 || a.wireframe_opacity > 1) throw new Error("Непрозрачность каркасов: от 0,1 до 1");
      patch.wireframe_opacity = a.wireframe_opacity;
    }
    const v = { ...store.get().view };
    if (patch.vexag !== undefined) v.vexag = patch.vexag as number;
    if (patch.grid !== undefined) v.grid = patch.grid as boolean;
    if (patch.perspective !== undefined) v.perspective = patch.perspective as boolean;
    if (patch.wireframe_opacity !== undefined) v.wireframeOpacity = patch.wireframe_opacity as number;
    store.set({ view: v });
    if (projectStore.get().current) {
      const full = { perspective: v.perspective, grid: v.grid, vexag: v.vexag, wireframe_opacity: v.wireframeOpacity };
      updateView("scene", patchFields({ ...full, ...patch }));
    }
    return `Сетка ${v.grid ? "вкл" : "выкл"}, ${v.perspective ? "перспектива" : "ортогональная проекция"}, преувеличение ${v.vexag}`;
  });

  registry.bind("layer.show", (a) => {
    const key = hiddenKey(String(a.layer), Number(a.id));
    const op = toggleHidden(key, a.visible as boolean);
    // after «Очистить окно» an object is shown by `hide_all.shown`; a sub-layer also by `hidden`
    const sub = key.split(":").length > 2;
    const hideAll = normalizeHideAll(viewStore.get().hide_all);
    if (projectStore.get().current && hideAll.on && (a.visible === true || !sub)) {
      const top = topKey(key);
      // a sub-layer shown after the clear: its object comes back with only this sub-layer
      const others = sub && !hideAll.shown.includes(top) ? SUB_LAYERS.map((p) => `${top}:${p}`).filter((k) => k !== key) : [];
      updateView("hide_all", showUnderHideAll(key, a.visible as boolean));
      if (a.visible === true) updateView("hidden", (v) => [...(op(v) as string[]).filter((k) => !others.includes(k)), ...others].slice(-2000));
    } else if (projectStore.get().current) updateView("hidden", op);
    else store.set((s) => ({ view: { ...s.view, hidden: op(s.view.hidden) as string[] } }));
    return `${a.visible ? "Показан" : "Скрыт"} слой ${key}`;
  });

  registry.bind(
    "scene.clear",
    async () => {
      const keys = clearedKeys([]);
      updateView("hide_all", () => keys.hide_all);
      updateView("hidden", () => keys.hidden);
      // an operation on the current list: displays added in another window are switched off, not lost
      updateView("displays", (v) => clearedKeys(v).displays);
      await registry.execute("hole.select", { hole_id: null });
      return "Окно очищено: все объекты скрыты; камера, разрез и сетка — прежние";
    },
    (ctx) => (ctx.hasProject ? null : "Откройте или создайте проект"),
  );

  bindViewCommands(app);

  for (const [command, kind, title] of [["holes.collar_style", "collars", "Устья"], ["holes.trace_style", "traces", "Стволы"]] as const) {
    registry.bind(command, async (a) => {
      const t = tablesStore.get();
      const setId = typeof a.set_id === "number" ? a.set_id : t.setId;
      const set = t.sets.find((x) => x.id === setId);
      if (!set) throw new Error("Выберите набор скважин");
      const { set_id: _ignored, ...patch } = a;
      const { saveSetStyle, setStyle } = await import("./SetStyleWindow");
      if (!Object.keys(patch).length) {
        openWindow(`${kind}-style`, `${title} · ${set.name}`, "form", { setId: set.id }, window.innerWidth, window.innerHeight);
        return `Открыта настройка «${title} · ${set.name}»`;
      }
      const next = { ...setStyle(kind, set.id), ...patch };
      const { normalizeCollarStyle } = await import("../../viz/holes/collarStyle");
      const { normalizeTraceStyle } = await import("../../viz/holes/traceStyle");
      const clean = kind === "collars" ? normalizeCollarStyle(next) : normalizeTraceStyle(next);
      saveSetStyle(kind, set.id, clean);
      return `${title} набора «${set.name}»: ${JSON.stringify(clean)}`;
    });
  }
}
