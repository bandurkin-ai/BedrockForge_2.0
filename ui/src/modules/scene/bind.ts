/** The 3D view commands that do not need the scene itself: scene.style, layer.show and the view settings
 * of the project (ТЗ 7.0.10, ADR 0007 §2). The scene module adds the rest when the 3D chunk loads. */
import type { App } from "../../shell/app";
import { api } from "../../shell/api";
import { Store } from "../../shell/store";
import { DEFAULT_VIEW, VEXAG_STEPS, type ViewState } from "../../shell/workspace";
import { projectStore } from "../projects/state";
import { hiddenKey, patchFields, toggleHidden, ViewSync, type ViewIO } from "./viewSync";

/** Every view key of the open project as the core keeps it (styles of collars and traces, displays…). */
export const viewStore = new Store<Record<string, unknown>>({});

const io: ViewIO = {
  get: () => api.get("/view"),
  put: (key, value, expectedVersion) => api.put(`/view/${key}`, { value, expected_version: expectedVersion }),
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

/** The part of the view kept in the app store (ribbon, status bar, Claude). */
export function viewFromKeys(keys: Record<string, unknown>, base: ViewState = DEFAULT_VIEW): ViewState {
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
    hidden: Array.isArray(keys.hidden) ? (keys.hidden as unknown[]).filter((k): k is string => typeof k === "string") : [],
  };
}

export function bindScene(app: App): void {
  const { registry, store } = app;
  sync = new ViewSync(
    io,
    (key, value) => {
      viewStore.set({ [key]: value });
      if (key === "scene" || key === "section" || key === "hidden") store.set((s) => ({ view: viewFromKeys(viewStore.get(), s.view) }));
    },
    (text) => store.set({ statusNote: text }),
  );
  let uid: string | null = null;
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
    if (projectStore.get().current) updateView("hidden", op);
    else store.set((s) => ({ view: { ...s.view, hidden: op(s.view.hidden) as string[] } }));
    return `${a.visible ? "Показан" : "Скрыт"} слой ${key}`;
  });
}
