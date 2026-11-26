/**
 * The application object: registry + state + handlers of the commands implemented on Э1.
 * No React here; components read app.store and run app.registry.execute().
 */
import { bindAudit } from "../modules/audit/bind";
import { bindDownhole } from "../modules/downhole/bind";
import { bindComposites } from "../modules/composites/bind";
import { bindCharts } from "../modules/charts/bind";
import { bindSampleSelection } from "../modules/charts/selection";
import { bindSwath } from "../modules/swath/bind";
import { bindStats } from "../modules/stats/bind";
import { bindQaqc } from "../modules/qaqc/bind";
import { bindQaqcSetup } from "../modules/qaqc/bindSetup";
import { bindIntercepts } from "../modules/intercepts/bind";
import { bindVariography } from "../modules/variography/bind";
import { bindEstimation } from "../modules/estimation/bind";
import { bindImplicit } from "../modules/implicit/bind";
import { bindPlanned } from "../modules/drillholes/plannedBind";
import { bindLineage } from "../modules/lineage/bind";
import { bindSimulation } from "../modules/simulation/bind";
import { bindDtm } from "../modules/dtm/bind";
import { bindModel } from "../modules/model/bind";
import { bindDigitize } from "../modules/section/digitize";
import { bindShadow } from "../modules/section/shadowSource";
import { bindStitch } from "../modules/section/seriesStitch";
import { bindImporting } from "../modules/importing/bind";
import { bindJobs } from "../modules/jobs/bind";
import { bindLines } from "../modules/lines/bind";
import { bindProjects } from "../modules/projects/bind";
import { bindDataManager } from "../modules/datamanager/bind";
import { bindBlocks } from "../modules/blocks/bind";
import { bindFilters } from "../modules/blocks/filters/bind";
import { bindReport } from "../modules/blocks/report/bind";
import { bindPit } from "../modules/pit/bind";
import { bindResources } from "../modules/resources/bind";
import { bindEstimateCompare } from "../modules/blocks/compare/bind";
import { bindBuild } from "../modules/blocks/build/bind";
import { bindExport } from "../modules/blocks/export/bind";
import { bindModelWindow } from "../modules/blocks/window/bind";
import { bindFields } from "../modules/blocks/fields/bind";
import { bindOmf } from "../modules/omf/bind";
import { bindRasters } from "../modules/rasters/bind";
import { bindStripLog } from "../charts/striplog/bind";
import { bindScene } from "../modules/scene/bind";
import { bindSection } from "../modules/section/bind";
import { bindTables } from "../modules/tables/bind";
import { bindViews } from "../modules/views/bind";
import { bindObjects } from "../modules/wireframes/bind";
import { createRegistry } from "./catalog";
import { logText, opLogStore, openLogWindow } from "./opLog";
import type { Command, CommandSource } from "./commands";
import { askConfirm } from "./dialogs";
import { formatArgs, type CommandRegistry } from "./registry";
import { loadSettings, saveSettings } from "./settings";
import { commandContext, INITIAL_STATE, Store, type AppState } from "./store";
import { toggleAbout } from "./aboutInfo";
import { focusWindow, openWindow, updateWindow, windowStore } from "./windows";
import { BOTTOM_TITLES, setPanel, type BottomTab, type PanelId } from "./workspace";
import { bindAssistant } from "../modules/assistant/bind";

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
    odaConverter: loaded?.settings.odaConverter ?? null,
  });
  const palette = new Store<{ query: string | null }>({ query: null });

  if (loaded?.writable) {
    let last = "";
    store.subscribe(() => {
      const { workspace, shortcuts, lastProject, projectsDir, heavyWorkers, odaConverter } = store.get();
      const key = JSON.stringify([workspace, shortcuts, lastProject, projectsDir, heavyWorkers, odaConverter]);
      if (key !== last) {
        last = key;
        saveSettings({ version: 1, workspace, shortcuts, lastProject, projectsDir, heavyWorkers, odaConverter });
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
    // one window: asked for a tab while it is open, it comes to the front on that tab (`asked` tells the calls apart)
    const open = windowStore.get().windows.find((w) => w.kind === "settings");
    if (!open) return void openWindow("settings", "Настройка", "form", { tab: a.tab ?? "shortcuts" }, ...viewport());
    if (typeof a.tab === "string") updateWindow(open.id, { props: { tab: a.tab, asked: Number(open.props?.asked ?? 0) + 1 } });
    focusWindow(open.id);
  });
  registry.bind("app.about", () => {
    toggleAbout();
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
  bindDataManager(app);
  bindJobs(app);
  bindImporting(app);
  bindTables(app);
  bindAudit(app);
  bindScene(app);
  bindObjects(app);
  bindBlocks(app);
  bindFilters(app);
  bindReport(app);
  bindResources(app);
  bindPit(app);
  bindEstimateCompare(app);
  bindBuild(app);
  bindExport(app);
  bindModelWindow(app);
  bindFields(app);
  bindOmf(app);
  bindLines(app);
  bindRasters(app);
  bindDtm(app);
  bindModel(app);
  bindDigitize(app);
  bindStitch(app);
  bindShadow();
  bindViews(app);
  bindSection(app);
  bindStripLog(app);
  bindDownhole(app);
  bindComposites(app);
  bindCharts(app);
  bindSampleSelection(app);
  bindSwath(app);
  bindStats(app);
  bindQaqc(app);
  bindQaqcSetup(app);
  bindIntercepts(app);
  bindVariography(app);
  bindEstimation(app);
  bindImplicit(app);
  bindPlanned(app);
  bindSimulation(app);
  bindLineage(app);
  bindAssistant(app, {
    openWindow: (id, props) => {
      // The history of dialogs (23.16) is a part of the chat panel; only the connection has a window of its own.
      if (id === "assistant.connect") openWindow("claude-connection", "Claude: подключение", "normal", props, ...viewport());
    },
  });
  return app;
}
