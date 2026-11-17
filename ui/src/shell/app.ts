/**
 * The application object: registry + state + handlers of the commands implemented on Э1.
 * No React here; components read app.store and run app.registry.execute().
 */
import { bindJobs } from "../modules/jobs/bind";
import { bindProjects } from "../modules/projects/bind";
import { createRegistry } from "./catalog";
import { logText, opLogStore, openLogWindow } from "./opLog";
import type { Command, CommandSource } from "./commands";
import { askConfirm } from "./dialogs";
import { formatArgs, type CommandRegistry } from "./registry";
import { loadSettings, saveSettings } from "./settings";
import { commandContext, INITIAL_STATE, Store, type AppState } from "./store";
import { openWindow } from "./windows";
import { BOTTOM_TITLES, setLayerShown, setPanel, VEXAG_STEPS, type BottomTab, type PanelId } from "./workspace";

export interface App {
  registry: CommandRegistry;
  store: Store<AppState>;
  /** Palette state: null — closed, otherwise the initial query. */
  palette: Store<{ query: string | null }>;
  run: (id: string, args?: Record<string, unknown>, source?: CommandSource) => Promise<void>;
}

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

export function createApp(opts: { persist?: boolean } = {}): App {
  const registry = createRegistry();
  const loaded = opts.persist === false ? null : loadSettings();
  const store = new Store<AppState>({
    ...INITIAL_STATE,
    workspace: loaded?.settings.workspace ?? INITIAL_STATE.workspace,
    shortcuts: loaded?.settings.shortcuts ?? {},
    lastProject: loaded?.settings.lastProject ?? null,
    projectsDir: loaded?.settings.projectsDir ?? null,
    heavyWorkers: loaded?.settings.heavyWorkers ?? 1,
  });
  const palette = new Store<{ query: string | null }>({ query: null });

  if (loaded?.writable) {
    let last = "";
    store.subscribe(() => {
      const { workspace, shortcuts, lastProject, projectsDir, heavyWorkers } = store.get();
      const key = JSON.stringify([workspace, shortcuts, lastProject, projectsDir, heavyWorkers]);
      if (key !== last) {
        last = key;
        saveSettings({ version: 1, workspace, shortcuts, lastProject, projectsDir, heavyWorkers });
      }
    });
  }

  registry.hooks = {
    context: () => commandContext(store.get()),
    confirm: (c: Command, args) =>
      askConfirm({
        title: "Claude просит выполнить действие",
        message: `«${c.title}»${Object.keys(args).length ? ` (${formatArgs(args)})` : ""}. Разрешить?`,
        okLabel: "Разрешить",
        danger: true,
      }),
  };

  const ws = (fn: (w: AppState["workspace"]) => AppState["workspace"]) => store.set((s) => ({ workspace: fn(s.workspace) }));

  registry.bind("panel.set", (a) => {
    const panel = a.panel as PanelId;
    ws((w) => setPanel(w, panel, a.open as boolean | undefined, a.tab as BottomTab | undefined));
    const w = store.get().workspace;
    const open = panel === "project" ? w.left : panel === "bottom" ? w.bottom : w.right && w.rightTab === panel;
    const tab = panel === "bottom" && open ? ` · ${BOTTOM_TITLES[w.bottomTab]}` : "";
    return `${open ? "Открыта" : "Закрыта"} панель ${panel}${tab}`;
  });
  registry.bind("theme.set", (a) => {
    ws((w) => ({ ...w, theme: (a.theme as "dark" | "light" | undefined) ?? (w.theme === "dark" ? "light" : "dark") }));
    return store.get().workspace.theme === "dark" ? "Тёмная тема" : "Светлая тема";
  });
  registry.bind("ribbon.toggle", () => {
    ws((w) => ({ ...w, ribbonCollapsed: !w.ribbonCollapsed }));
    return store.get().workspace.ribbonCollapsed ? "Лента свёрнута" : "Лента развёрнута";
  });
  registry.bind("palette.open", (a) => {
    palette.set({ query: typeof a.query === "string" ? a.query : "" });
  });
  registry.bind("settings.open", (a) => {
    openWindow("settings", "Настройка", "form", { tab: a.tab ?? "shortcuts" }, ...viewport());
  });
  registry.bind("license.open", () => {
    openWindow("license", "Лицензия", "medium", undefined, ...viewport());
  });
  registry.bind("workspace.state", () => {
    const s = store.get();
    const w = s.workspace;
    const panels = [w.left && "дерево", w.right && (w.rightTab === "assistant" ? "Claude" : "свойства"), w.bottom && `нижняя (${BOTTOM_TITLES[w.bottomTab]})`].filter(Boolean);
    return [
      `Проект: ${s.projectName ?? "—"}`,
      `Набор: ${s.setName ?? "—"}`,
      `Скважина: ${s.holeId ?? "—"}`,
      `Панели: ${panels.join(", ") || "нет"}`,
      `Тема: ${w.theme === "dark" ? "тёмная" : "светлая"}`,
      `Вид: ${s.view.perspective ? "перспектива" : "ортогональная"}, преувеличение ${s.view.vexag}, сетка ${s.view.grid ? "вкл" : "выкл"}`,
    ].join("; ");
  });
  registry.bind("scene.style", (a) => {
    const v = { ...store.get().view };
    if (typeof a.vexag === "number") {
      if (!VEXAG_STEPS.includes(a.vexag)) throw new Error("Преувеличение: 1, 2, 3 или 5");
      v.vexag = a.vexag;
    }
    if (typeof a.grid === "boolean") v.grid = a.grid;
    if (typeof a.perspective === "boolean") v.perspective = a.perspective;
    if (typeof a.wireframe_opacity === "number") {
      if (a.wireframe_opacity < 0.1 || a.wireframe_opacity > 1) throw new Error("Непрозрачность каркасов: от 0,1 до 1");
      v.wireframeOpacity = a.wireframe_opacity;
    }
    store.set({ view: v });
    return `Сетка ${v.grid ? "вкл" : "выкл"}, ${v.perspective ? "перспектива" : "ортогональная проекция"}, преувеличение ${v.vexag}`;
  });
  registry.bind("layer.show", (a) => {
    const key = a.id === undefined ? String(a.layer) : `${a.layer}:${a.id}`;
    store.set((s) => ({ view: setLayerShown(s.view, key, a.visible as boolean) }));
    return `${a.visible ? "Показан" : "Скрыт"} слой ${key}`;
  });

  registry.bind("logs.open", () => {
    openLogWindow();
  });
  registry.bind("logs.last", (a) => {
    const index = typeof a.index === "number" ? a.index : 0;
    const log = opLogStore.get().logs[index];
    if (!log) return "Журналов операций в этом сеансе нет";
    return logText(log).slice(-20_000);
  });

  const run = async (id: string, args: Record<string, unknown> = {}, source: CommandSource = "user") => {
    await registry.execute(id, args, source);
  };
  const app: App = { registry, store, palette, run };
  bindProjects(app);
  bindJobs(app);
  return app;
}
