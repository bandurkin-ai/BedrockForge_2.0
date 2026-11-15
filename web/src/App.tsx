import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  auditExportUrl, cancelJob, createChart, createDemoProject, deleteChart, getChart, patchChart, createProject, createSet, deleteDataset, deleteLines, deleteProject, deleteWireframe,
  deleteIntervalTable, renameIntervalTable, getIntervalTables, listProjects, moveDataset, patchLines, patchRaster, patchWireframe, renameDataset, runAudit, setBlockUnit,
  type Dataset, type JobInfo, type Project,
} from "./api";
import { GradeTonnageWindow } from "./charts/GradeTonnageWindow";
import { StripLogWindow } from "./charts/StripLogWindow";
import { tableDisplay, withDefaults, type StripLogSettings } from "./charts/stripLog";
import type { BlockStyle } from "./viz/blockStyle";
import { AuditPage, DEFAULT_CONFIG } from "./AuditPage";
import { DataPage } from "./DataPage";
import { StatsPage } from "./StatsPage";
import type { StatsView } from "./stats/analyses";
import { formatRoute, parseRoute, useRoute, type TableKind } from "./route";
import { AssistantPanel, type Approval } from "./shell/AssistantPanel";
import { CommandLog } from "./shell/CommandLog";
import { CommandPalette } from "./shell/CommandPalette";
import { commandTools, eventShortcut, findByToolName, validateArgs, type CommandSource, type LogEntry } from "./shell/commands";
import { DataTree } from "./shell/DataTree";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { Icon } from "./shell/icons";
import { ImportDialog } from "./shell/ImportDialog";
import { ImportWizard, WIZARD_ACCEPT } from "./shell/ImportWizard";
import { isBlockModelFile } from "./shell/sniffBlocks";
import { ChartWindow } from "./charts/ChartWindow";
import { HistogramWindow, type HistogramSettings } from "./charts/HistogramWindow";
import { QQWindow, defaultQQ, type QQSettings } from "./charts/QQWindow";
import { DendrogramWindow, type DendrogramSettings } from "./charts/DendrogramWindow";
import { ContextMenu } from "./shell/ContextMenu";
import type { ChartKind } from "./shell/registry";
import { JobsPanel } from "./shell/JobsPanel";
import { useJobs } from "./shell/useJobs";
import { PropertiesPanel } from "./shell/PropertiesPanel";
import { buildCommands, type Live } from "./shell/registry";
import { Ribbon } from "./shell/Ribbon";
import { buildRibbon, flattenRibbon, type RibbonItem } from "./shell/ribbonConfig";
import { Splitter } from "./shell/Splitter";
import { useAssistant, type ToolOutcome } from "./shell/useAssistant";
import { useLiveState } from "./shell/useLiveState";
import { useRemoteCommands } from "./shell/useRemoteCommands";
import {
  BOTTOM_TITLES, isShown, layerKey, loadWorkspace, openBottom, resize, saveWorkspace, setLogShown, setPanel, setShown,
  type BottomTab, type SelectedObject, type WorkspaceState,
} from "./shell/workspace";
import { StatusBar } from "./StatusBar";
import { useOverview } from "./useOverview";
import { DEFAULT_VIZ, type VizSettings } from "./viz/ComponentPanel";
import type { SectionLine } from "./viz/geometry";
import { THEME_EVENT } from "./viz/scene3d-theme";

// three.js is large: the 3D view is a separate chunk, the shell appears first.
import type { DownholeLog, SceneApi } from "./viz/SceneView";

const SceneView = lazy(() => import("./viz/SceneView").then((m) => ({ default: m.SceneView })));

const STORAGE_KEY = "geo.projectId";
const LOG_LIMIT = 200;
const ACCEPT = {
  wireframes: ".tridb,.dxf,.obj,.stl",
  lines: ".str,.dxf",
  rasters: ".jpg,.jpeg,.png,.tif,.tiff,.bmp,.jgw,.jpgw,.pgw,.pngw,.tfw,.tifw,.bpw,.wld,.tab,.grf",
  blocks: ".dat,.csv,.txt",
};
type UploadKind = keyof typeof ACCEPT;

interface ChartState {
  id: number;
  /** the saved chart in the project tree */
  chartId: number;
  kind: ChartKind;
  setId: number;
  /** settings the window opens with (histogram / dendrogram: partial; Q-Q: complete) */
  settings: Record<string, unknown>;
  /** stacking order: the window clicked last is on top */
  z: number;
  /** downhole log: the table and hole it was opened from (its first columns and hole) */
  hint?: { table?: string | null; hole?: string | null };
}

/** Drag type of a saved downhole display dragged from the tree onto the 3D view. */
export const LOG_DRAG = "application/x-bedrockforge-log";

const CHART_TITLES: Record<ChartKind, string> = { histogram: "Гистограмма", qq: "Q-Q график", dendrogram: "Дендрограмма", striplog: "Данные по стволу" };

const raise = (list: ChartState[], id: number): ChartState[] => {
  const top = Math.max(...list.map((c) => c.z));
  return list.map((c) => (c.id === id && c.z !== top ? { ...c, z: top + 1 } : c));
};

function loadSavedProjectId(): number | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value ? Number(value) : null;
  } catch {
    return null;
  }
}

function saveProjectId(id: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    /* storage may be unavailable; the choice just will not be remembered */
  }
}

const isEditable = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));

function Workspace({
  projects,
  project,
  onSelectProject,
  onNewProject,
  onDemoProject,
  onDeleteProject,
}: {
  projects: Project[];
  project: Project;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  onDemoProject: () => Promise<string>;
  onDeleteProject: (id: number) => Promise<string>;
}) {
  const projectId = project.id;
  const [route, navigate] = useRoute();
  const [ws, setWs, wsRef] = useLiveState<WorkspaceState>(loadWorkspace);
  const [refreshKey, setRefreshKey] = useState(0);
  const [viz, setViz, vizRef] = useLiveState<VizSettings>(DEFAULT_VIZ);
  const [section, setSection, sectionRef] = useLiveState<SectionLine | null>(null);
  const [selected, setSelected, selectedRef] = useLiveState<SelectedObject | null>(null);
  const [palette, setPalette] = useState<string | null>(null);
  const [statsView, setStatsView] = useState<StatsView>({ analysis: "declus", component: null });
  const [importTarget, setImportTarget] = useState<number | null | undefined>(undefined);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const fileInputs = { wireframes: useRef<HTMLInputElement>(null), lines: useRef<HTMLInputElement>(null), rasters: useRef<HTMLInputElement>(null), blocks: useRef<HTMLInputElement>(null) };
  const openFiles = (kind: UploadKind) => fileInputs[kind].current?.click();
  const anyFileInput = useRef<HTMLInputElement>(null);
  const [wizard, setWizard] = useState<{ files: File[]; kindHint: UploadKind | null; blockModels: Set<File> } | null>(null);
  // Chart windows over the workspace, and the right-click menu of the tree.
  const [charts, setCharts] = useState<ChartState[]>([]);
  // Grade–tonnage windows of block models (shown with the chart windows, not saved in the tree).
  const [reports, setReports] = useState<{ id: number; modelId: number; z: number }[]>([]);
  const openBlockReport = (modelId: number) =>
    setReports((prev) => {
      const top = Math.max(0, ...prev.map((r) => r.z), ...charts.map((c) => c.z)) + 1;
      const open = prev.find((r) => r.modelId === modelId);
      return open ? prev.map((r) => (r.id === open.id ? { ...r, z: top } : r)) : [...prev, { id: Date.now(), modelId, z: top }];
    });
  // How each block model is shown, and the block picked in the view.
  const [blockStyles, setBlockStyles] = useState<Record<number, BlockStyle>>({});
  const [pickedBlock, setPickedBlock] = useState<{ modelId: number; index: number } | null>(null);
  const [menu, setMenu] = useState<{ setId: number; x: number; y: number } | null>(null);
  // Right click on an interval table of a set (not the assays: those get the charts menu).
  const [tableMenu, setTableMenu] = useState<{ setId: number; id: number; name: string; x: number; y: number } | null>(null);
  const [logsMenu, setLogsMenu] = useState<{ setId: number; x: number; y: number } | null>(null);
  const [chartMenu, setChartMenu] = useState<{ id: number; kind: ChartKind; name: string; x: number; y: number } | null>(null);
  const showChart = (chartId: number, kind: ChartKind, setId: number, settings: Record<string, unknown>, hint?: ChartState["hint"]) =>
    setCharts((prev) => {
      const open = prev.find((c) => c.chartId === chartId);
      if (open) return raise(prev, open.id);
      return [...prev, { id: Date.now() + Math.random(), chartId, kind, setId, settings, hint, z: prev.length ? Math.max(...prev.map((c) => c.z)) + 1 : 0 }];
    });
  const draftSeq = useRef(0);
  /** A new chart: saved in the tree under its set right away, then shown (a downhole display: a draft until saved). */
  const openChart = async (kind: ChartKind, setId: number, initial?: Partial<HistogramSettings>, qq?: QQSettings, dendrogram?: Partial<DendrogramSettings>,
    striplog?: { settings?: Record<string, unknown>; table?: string | null; hole?: string | null }): Promise<string> => {
    const settings = (kind === "qq" ? qq ?? defaultQQ(setId) : kind === "dendrogram" ? dendrogram ?? {} : kind === "striplog" ? striplog?.settings ?? {} : initial ?? {}) as Record<string, unknown>;
    if (kind === "striplog") {
      // A downhole display is a draft until «Сохранить» in its window: nothing is added to the tree by opening it.
      const draftId = -(Date.now() + ++draftSeq.current);
      showChart(draftId, kind, setId, settings, { table: striplog?.table, hole: striplog?.hole ?? (activeSetRef.current === setId ? holeRef.current : null) });
      return "Данные по стволу открыты как черновик: «Сохранить» в окне добавит их в дерево («Данные по скважинам»)";
    }
    const saved = await createChart(setId, kind, CHART_TITLES[kind], settings);
    showChart(saved.id, kind, setId, settings);
    reload();
    return `${CHART_TITLES[kind]} сохранён(а) в дереве: ${sets.find((d) => d.id === setId)?.name ?? ""} → Графики`;
  };
  /** A saved chart from the tree, with its settings. */
  const openSavedChart = async (chartId: number): Promise<string> => {
    const open = charts.find((c) => c.chartId === chartId);
    if (open) {
      setCharts((prev) => raise(prev, open.id));
      return `График «${chartNames.current.get(chartId) ?? ""}» уже открыт`;
    }
    const c = await getChart(chartId);
    showChart(c.id, c.kind, c.set_id, c.settings);
    return `Открыт график «${c.name}»`;
  };
  // Settings of open charts are saved a moment after the last change.
  const saveTimers = useRef(new Map<number, number>());
  const chartNames = useRef(new Map<number, string>());
  // Data along the holes in the 3D view: the settings of every saved display that is on (live while its window is open).
  const [logSettings, setLogSettings] = useState<Record<number, StripLogSettings>>({});
  const logRequested = useRef(new Set<number>());
  const toggleLog = (chartId: number, on: boolean) => setWs((prev) => setLogShown(prev, chartId, on));
  useEffect(() => {
    for (const id of ws.logs) {
      if (logRequested.current.has(id)) continue;
      logRequested.current.add(id);
      getChart(id)
        .then(async (c) => {
          let settings = withDefaults(c.settings as Partial<StripLogSettings>);
          if (settings.tracks.length === 0) {
            // Saved without columns (never set up): the default display of the first table with data.
            const table = (await getIntervalTables(c.set_id)).find((t) => t.columns.length > 0);
            if (table) settings = { ...settings, ...tableDisplay(table), holes: settings.holes, allHoles: settings.holes.length === 0 };
          } else if (settings.holes.length === 0 && !settings.allHoles) {
            settings = { ...settings, allHoles: true };
          }
          setLogSettings((prev) => (id in prev ? prev : { ...prev, [id]: settings }));
        })
        .catch(() => logRequested.current.delete(id)); // deleted: it just does not show
    }
  }, [ws.logs]);
  /** A table on the holes of its whole set, as a draft window with a live preview in the view (saved by its button). */
  const previewOnHoles = async (setId: number, tableKey: string): Promise<string> => {
    const tables = await getIntervalTables(setId);
    const table = tables.find((t) => t.key === tableKey);
    if (!table || table.columns.length === 0) throw new Error("В таблице нет полей для показа на скважинах");
    return openChart("striplog", setId, undefined, undefined, undefined, { settings: tableDisplay(table) as unknown as Record<string, unknown>, table: tableKey });
  };
  /** «Сохранить» of a draft window: the display becomes a row of the tree and stays on in the view. */
  const saveDraft = async (draftId: number, setId: number, settings: StripLogSettings, autoName: string): Promise<string> => {
    const saved = await createChart(setId, "striplog", autoName || CHART_TITLES.striplog, settings);
    logRequested.current.add(saved.id);
    chartNames.current.set(saved.id, saved.name);
    setLogSettings((prev) => {
      const { [draftId]: _draft, ...rest } = prev;
      return { ...rest, [saved.id]: settings };
    });
    setCharts((prev) => prev.map((c) => (c.chartId === draftId ? { ...c, chartId: saved.id } : c)));
    setWs((prev) => setLogShown(prev, saved.id, true));
    reload();
    return `Сохранено в дереве: ${sets.find((d) => d.id === setId)?.name ?? ""} → Данные по скважинам → «${saved.name}»`;
  };
  /** A table on the holes of its whole set, saved in the tree and on at once (the assistant's command). */
  const showOnHoles = async (setId: number, tableKey: string, fields?: string[]): Promise<string> => {
    const tables = await getIntervalTables(setId);
    const table = tables.find((t) => t.key === tableKey);
    if (!table || table.columns.length === 0) throw new Error("В таблице нет полей для показа на скважинах");
    const settings = tableDisplay(table, fields);
    const saved = await createChart(setId, "striplog", `${table.name} на скважинах`, settings);
    logRequested.current.add(saved.id);
    setLogSettings((prev) => ({ ...prev, [saved.id]: settings }));
    setWs((prev) => setLogShown(prev, saved.id, true));
    reload();
    return `«${table.name}» показана на скважинах; настройки — в дереве: ${sets.find((d) => d.id === setId)?.name ?? ""} → Данные по скважинам`;
  };
  const saveChartSettings = (chartId: number, settings: unknown, autoName: string) => {
    if (charts.find((c) => c.chartId === chartId)?.kind === "striplog") setLogSettings((prev) => ({ ...prev, [chartId]: withDefaults(settings as Partial<StripLogSettings>) }));
    if (chartId < 0) return; // a draft: kept in the window (and previewed in the view) until saved
    const timers = saveTimers.current;
    window.clearTimeout(timers.get(chartId));
    timers.set(chartId, window.setTimeout(() => {
      timers.delete(chartId);
      patchChart(chartId, { settings, auto_name: autoName })
        .then((c) => {
          if (chartNames.current.get(chartId) !== c.name) {
            chartNames.current.set(chartId, c.name);
            reload();
          }
        })
        .catch(() => undefined); // deleted meanwhile
    }, 800));
  };
  const sceneApi = useRef<SceneApi | null>(null);
  const reload = useCallback(() => setRefreshKey((k) => k + 1), []);
  const jobs = useJobs(projectId, (job: JobInfo) => {
    if (job.status === "done") reload();
    setNotice({
      text: job.status === "done" ? `${job.title}: готово` : job.status === "failed" ? `${job.title}: ${job.error ?? "ошибка"}` : `${job.title}: отменено`,
      error: job.status === "failed",
    });
  });

  // The active set and the selected hole live in the URL, so they survive a reload.
  const [activeSet, setActiveSet, activeSetRef] = useLiveState<number | null>(route.set);
  const [selectedHole, setSelectedHole, holeRef] = useLiveState<string | null>(route.hole);

  const { datasets, datasetsKey, summary, holes, audit, error } = useOverview(projectId, activeSet, refreshKey);
  // A set created just now (import, "new set") is not in the tree until it reloads: do not reset to the first set meanwhile.
  const pendingSet = useRef<{ set: number; key: number } | null>(null);
  const refreshKeyRef = useRef(refreshKey);
  refreshKeyRef.current = refreshKey;
  const reloadAndSelect = (setId: number) => {
    pendingSet.current = { set: setId, key: refreshKeyRef.current + 1 };
    reload();
    selectHole(setId, null);
  };
  const sets = useMemo(() => (datasets ?? []).filter((d) => d.kind === "drillholes"), [datasets]);
  // Saved downhole displays that are on, with their settings: what the 3D view draws on the holes.
  const logLayers = useMemo<DownholeLog[]>(() => {
    const out: DownholeLog[] = [];
    for (const d of sets) {
      for (const c of d.charts ?? []) {
        if (c.kind !== "striplog" || !ws.logs.includes(c.id) || !logSettings[c.id]) continue;
        out.push({ id: c.id, setId: d.id, name: c.name, settings: logSettings[c.id] });
      }
    }
    // Drafts (open windows not saved yet) are previewed too.
    for (const c of charts) {
      if (c.kind === "striplog" && c.chartId < 0 && logSettings[c.chartId]) out.push({ id: c.chartId, setId: c.setId, name: "Черновик", settings: logSettings[c.chartId] });
    }
    return out;
  }, [sets, ws.logs, logSettings, charts]);

  const selectHole = useCallback(
    (setId: number, holeId: string | null) => {
      setActiveSet(setId);
      setSelectedHole(holeId);
      if (holeId) setSelected(null);
      navigate({ set: setId, hole: holeId });
    },
    [navigate, setActiveSet, setSelectedHole, setSelected],
  );
  const selectSet = useCallback(
    (setId: number) => {
      if (setId !== activeSetRef.current) selectHole(setId, null);
    },
    [activeSetRef, selectHole],
  );

  // Keep the active set valid: the first set by default, never a deleted one.
  useEffect(() => {
    if (!datasets) return;
    if (activeSet !== null && sets.some((d) => d.id === activeSet)) return;
    const pending = pendingSet.current;
    if (pending && pending.set === activeSet && datasetsKey < pending.key) return;
    const next = sets[0]?.id ?? null;
    setActiveSet(next);
    setSelectedHole(null);
    navigate({ set: next, hole: null });
  }, [datasets, datasetsKey, sets, activeSet, navigate, setActiveSet, setSelectedHole]);

  // Drop the hole selection if that hole no longer exists (e.g. after re-importing collars).
  useEffect(() => {
    if (selectedHole && activeSet !== null && holes.length > 0 && !holes.some((h) => h.hole_id === selectedHole)) selectHole(activeSet, null);
  }, [holes, selectedHole, activeSet, selectHole]);

  useEffect(() => saveWorkspace(ws), [ws]);
  useEffect(() => {
    document.documentElement.dataset.theme = ws.theme;
    window.dispatchEvent(new Event(THEME_EVENT));
  }, [ws.theme]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), notice.error ? 9000 : 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const assistant = useAssistant(projectId, {
    tools: () => commandTools(commands),
    context: () => ({
      project: { id: project.id, name: project.name },
      view: { clip: ws.clip && section !== null, perspective: ws.perspective, vertical_exaggeration: ws.vexag },
      drillhole_sets: sets.map((d) => ({
        id: d.id, name: d.name, shown: isShown(ws, layerKey.set(d.id)), active: d.id === activeSet,
        counts: d.counts, components: d.components, audit: d.audit,
      })),
      wireframes: (datasets ?? []).flatMap((d) => (d.wireframes ?? []).map((w) => ({ id: w.id, name: w.name, file: d.name, shown: isShown(ws, layerKey.wireframe(w.id)) }))),
      lines: (datasets ?? []).flatMap((d) => (d.lines ?? []).map((l) => ({ id: l.id, name: l.name, file: d.name, lines: l.line_count, shown: isShown(ws, layerKey.lines(l.id)) }))),
      rasters: (datasets ?? []).flatMap((d) => (d.rasters ?? []).map((r) => ({ id: r.id, name: r.name, elevation: r.elevation, shown: isShown(ws, layerKey.raster(r.id)) }))),
      selected_hole: selectedHole,
      selected_object: selected,
      color_scale: viz,
      section,
    }),
    execute: (name, input) => executeTool(name, input),
  });

  async function createNewSet(name: string): Promise<string> {
    const created = await createSet(projectId, name.trim() || "Скважины");
    reloadAndSelect(created.id);
    return `Создан набор «${created.name}»`;
  }

  // Commands read the latest state through this ref, so the registry itself never changes.
  const live = useRef<Live>(null!);
  live.current = {
    projectId, projects, datasets: datasets ?? [], holes, summary,
    get ws() { return wsRef.current; },
    get viz() { return vizRef.current; },
    get section() { return sectionRef.current; },
    get activeSet() { return activeSetRef.current; },
    get selectedHole() { return holeRef.current; },
    get selected() { return selectedRef.current; },
    setWs, setViz, setSection, selectSet, selectHole,
    selectObject: (object) => {
      setSelected(object);
      if (object !== null) setWs((prev) => setPanel(prev, "properties", true));
    },
    openTable: (kind: TableKind) => {
      navigate({ kind });
      setWs((prev) => openBottom(prev, "table"));
    },
    openChart: (kind, setId, initial, qq, dendrogram, striplog) => openChart(kind, setId, initial, qq, dendrogram, striplog),
    openSavedChart,
    showOnHoles,
    openBlockReport,
    openStats: (analysis, component) => {
      setStatsView((prev) => ({ analysis, component: component ?? prev.component }));
      // charts need room: grow a short dock, never shrink one the user enlarged
      setWs((prev) => resize(openBottom(prev, "stats"), "bottom", Math.max(prev.sizes.bottom, 400)));
    },
    reload, onSelectProject, onNewProject, onDemoProject, onDeleteProject,
    moveDataset: async (id, folder) => {
      await moveDataset(id, folder);
      reload();
    },
    jobs: jobs.jobs,
    cancelJob: async (id) => {
      await cancelJob(id);
    },
    onCreateSet: createNewSet,
    openPalette: (query) => setPalette(query),
    openImport: (setId) => setImportTarget(setId),
    upload: openFiles,
    openImportFiles: () => anyFileInput.current?.click(),
    patchRaster: async (id, patch) => {
      await patchRaster(id, patch);
      reload();
    },
    sectionFrame: () => sceneApi.current?.sectionFrame() ?? null,
    sectionThroughHole: (setId, holeId) => sceneApi.current?.sectionThroughHole(setId, holeId) ?? null,
    runAudit: (setId) => runAudit(setId, DEFAULT_CONFIG),
    exportAudit: (setId) => window.open(auditExportUrl(setId), "_blank"),
    fit: () => sceneApi.current?.fit(),
    view: (preset) => sceneApi.current?.view(preset),
    drawSection: () => sceneApi.current?.drawSection(),
    openSection: (line) => sceneApi.current?.openSection(line),
    ask: (prompt) => void assistant.send(prompt),
    resetChat: assistant.reset,
  };
  const commands = useMemo(() => buildCommands(live), []);

  const runCommand = useCallback(
    async (id: string, args: Record<string, unknown>, source: CommandSource): Promise<ToolOutcome> => {
      const command = commands.find((c) => c.id === id);
      if (!command) return { title: id, content: `Неизвестная команда: ${id}`, ok: false };
      let content: string;
      let ok = true;
      try {
        content = (await command.run(args)) ?? "Готово";
      } catch (e) {
        content = (e as Error).message;
        ok = false;
      }
      if (!command.silent) {
        setLog((prev) => [{ id: Date.now() + Math.random(), time: new Date(), source, title: command.title, args, result: content, ok }, ...prev].slice(0, LOG_LIMIT));
      }
      if (!ok && source === "user") setNotice({ text: content, error: true });
      return { title: command.title, content, ok };
    },
    [commands],
  );

  /** A command requested by Claude or an MCP client: validated, and confirmed by the user when it changes data. */
  const executeExternal = useCallback(
    async (commandId: string | null, toolName: string | null, input: unknown, source: CommandSource): Promise<ToolOutcome> => {
      const command = commandId ? commands.find((c) => c.id === commandId && !c.uiOnly) : findByToolName(commands, toolName ?? "");
      if (!command) return { title: commandId ?? toolName ?? "?", content: "Такой команды интерфейса нет", ok: false };
      const problem = validateArgs(command, input);
      if (problem) return { title: command.title, content: problem, ok: false };
      const args = input as Record<string, unknown>;
      if (command.confirm) {
        setWs((prev) => setPanel(prev, "assistant", true));
        const allowed = await new Promise<boolean>((resolve) => setApproval({ title: command.title, detail: command.description, resolve }));
        setApproval(null);
        if (!allowed) return { title: command.title, content: "Пользователь отклонил действие", ok: false };
      }
      return runCommand(command.id, args, source);
    },
    [commands, runCommand, setWs],
  );
  const executeTool = (name: string, input: Record<string, unknown>) => executeExternal(null, name, input, "claude");

  const remote = useRemoteCommands(projectId, commands, (id, args) => executeExternal(id, null, args, "remote"));

  const tabs = useMemo(
    () => buildRibbon(ws, { hasHole: selectedHole !== null, hasSet: activeSet !== null, hasSection: section !== null }),
    [ws, selectedHole, activeSet, section],
  );
  const items = useMemo(() => flattenRibbon(tabs), [tabs]);
  const runItem = useCallback(
    (item: RibbonItem) => {
      if (!item.disabled) void runCommand(item.command, item.args ?? {}, "user");
    },
    [runCommand],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (palette !== null || importTarget !== undefined) return;
      const shortcut = eventShortcut(e);
      const item = items.find((i) => i.shortcut === shortcut);
      if (!item) return;
      // Single keys and Esc belong to the field being edited; Ctrl combinations work everywhere.
      if (isEditable(e.target) && !shortcut.startsWith("ctrl+")) return;
      if (item.disabled) return;
      e.preventDefault();
      runItem(item);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, palette, importTarget, runItem]);

  // ---- project tree actions

  async function guarded(action: () => Promise<string | void>) {
    try {
      const text = await action();
      if (text) setNotice({ text, error: false });
    } catch (e) {
      setNotice({ text: (e as Error).message, error: true });
    }
  }

  const onRename = (d: Dataset) =>
    guarded(async () => {
      const raster = d.rasters?.[0];
      const current = raster?.name ?? d.name;
      const name = window.prompt("Новое название", current)?.trim();
      if (!name || name === current) return;
      if (raster) await patchRaster(raster.id, { name });
      else await renameDataset(d.id, name);
      reload();
      return `Переименовано: «${name}»`;
    });

  const onMove = (d: Dataset) =>
    guarded(async () => {
      const folder = window.prompt("Папка (через «/», пусто — в корень группы)", d.folder ?? "");
      if (folder === null || folder.trim() === (d.folder ?? "")) return;
      const moved = await moveDataset(d.id, folder);
      reload();
      return moved.folder ? `«${d.name}» → «${moved.folder}»` : `«${d.name}» → корень группы`;
    });

  const onDelete = (d: Dataset) =>
    guarded(async () => {
      const what =
        d.kind === "drillholes" ? `набор скважин «${d.name}» со всеми устьями, замерами, пробами и аудитом`
        : d.kind === "wireframes" ? `файл «${d.name}» со всеми каркасами (${d.wireframes?.length ?? 0})`
        : d.kind === "lines" ? `файл «${d.name}» со всеми слоями линий (${d.lines?.length ?? 0})`
        : `растр «${d.rasters?.[0]?.name ?? d.name}»`;
      if (!window.confirm(`Удалить ${what}? Это действие нельзя отменить.`)) return;
      await deleteDataset(d.id);
      const items = [...(d.wireframes ?? []), ...(d.lines ?? []), ...(d.rasters ?? [])].map((x) => x.id);
      if (selected && items.includes(selected.id)) setSelected(null);
      reload();
      return `Удалено: «${d.name}»`;
    });

  const onDeleteItem = (kind: "wireframe" | "lines", id: number, name: string) =>
    guarded(async () => {
      const what = kind === "wireframe" ? `каркас «${name}»` : `слой линий «${name}»`;
      if (!window.confirm(`Удалить ${what}? Это действие нельзя отменить.`)) return;
      await (kind === "wireframe" ? deleteWireframe(id) : deleteLines(id));
      if (selected?.kind === kind && selected.id === id) setSelected(null);
      reload();
      return `Удалено: ${what}`;
    });

  const onColor = (kind: "wireframe" | "lines", id: number, color: string) =>
    guarded(async () => {
      await (kind === "wireframe" ? patchWireframe(id, { color }) : patchLines(id, { color }));
      reload();
    });

  function logUpload(file: string, result: string) {
    setLog((prev) => [{ id: Date.now() + Math.random(), time: new Date(), source: "user" as const, title: "Загрузка файла", args: { file }, result, ok: true }, ...prev].slice(0, LOG_LIMIT));
  }

  /** Every file import goes through the wizard: transfer, what is inside, parameters, background jobs. */
  function onFiles(kind: UploadKind | null, list: FileList | File[] | null) {
    const files = Array.from(list ?? []);
    const input = kind ? fileInputs[kind].current : anyFileInput.current;
    if (input) input.value = "";
    if (!files.length) return;
    // Tables that are block models go to the object import, the rest to the drillhole wizard.
    void Promise.all(files.map(async (f) => ((kind === "blocks" && /\.(dat|csv|txt)$/i.test(f.name)) || (await isBlockModelFile(f)) ? f : null)))
      .then((found) => setWizard({ files, kindHint: kind, blockModels: new Set(found.filter((f): f is File => f !== null)) }));
  }

  function onJobsStarted(started: JobInfo[]) {
    setWs((prev) => openBottom(prev, "jobs"));
    for (const job of started) logUpload(job.title.replace(/^Импорт: /, ""), `Задание ${job.id}: ${job.title}`);
    setNotice({ text: started.length === 1 ? `${started[0].title}: идёт в фоне` : `Заданий импорта: ${started.length}, идут в фоне`, error: false });
  }

  // Files dropped anywhere on the window open the wizard.
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  const dropHandlers = {
    onDragEnter: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: React.DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      onFiles(null, e.dataTransfer.files);
    },
  };

  const clearHole = () => activeSet !== null && selectHole(activeSet, null);
  const bottomTabs: BottomTab[] = ["table", "audit", "stats", "jobs", "log"];
  const activeSetName = sets.find((d) => d.id === activeSet)?.name ?? null;

  return (
    <div className={dragging ? "app drop-target" : "app"} {...dropHandlers}>
      <header className="titlebar">
        <span className="brand"><span className="brand-mark" aria-hidden="true" />BedrockForge</span>
        <label className="project-pick">
          <span className="muted">Проект</span>
          <select value={projectId} onChange={(e) => onSelectProject(Number(e.target.value))} aria-label="Проект">
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <button className="command-search" onClick={() => setPalette("")} title="Поиск команд и скважин (Ctrl+K)">
          <Icon name="search" size={14} />
          <span>Поиск команд и скважин…</span>
          <kbd>Ctrl+K</kbd>
        </button>
        <span className="spacer" />
        <button className="icon" onClick={() => runItem({ command: "theme.set", label: "", icon: "theme" })} title="Сменить тему" aria-label="Сменить тему">
          <Icon name="theme" size={16} />
        </button>
        <button
          className={ws.right && ws.rightTab === "assistant" ? "claude-button active" : "claude-button"}
          onClick={() => runItem({ command: "panel.set", args: { panel: "assistant" }, label: "", icon: "spark" })}
          title="Ассистент Claude (Ctrl+J)"
        >
          <Icon name="spark" size={16} /> Claude
        </button>
      </header>

      <Ribbon
        tabs={tabs}
        activeTab={ws.ribbonTab}
        collapsed={ws.ribbonCollapsed}
        onTab={(id) => setWs((prev) => ({ ...prev, ribbonTab: id, ribbonCollapsed: false }))}
        onToggleCollapsed={() => setWs((prev) => ({ ...prev, ribbonCollapsed: !prev.ribbonCollapsed }))}
        onRun={runItem}
      />

      <div className="main">
        {ws.left && (
          <>
            <aside className="dock dock-left" style={{ width: ws.sizes.left }} aria-label="Дерево проекта">
              <ErrorBoundary label="Дерево проекта" compact>
                <DataTree
                  datasets={datasets} ws={ws} refreshKey={refreshKey} activeSet={activeSet}
                  selectedHole={selectedHole} selected={selected}
                  onShow={(key, shown) => setWs((prev) => setShown(prev, key, shown))}
                  onSelectSet={selectSet}
                  onSelectHole={selectHole}
                  onSelectObject={(object) => live.current.selectObject(object)}
                  onOpenTable={(setId, kind) => void runCommand("table.open", { kind, set_id: setId }, "user")}
                  onOpenAudit={(setId) => {
                    selectSet(setId);
                    setWs((prev) => openBottom(prev, "audit"));
                  }}
                  onImport={(setId) => setImportTarget(setId)}
                  onUpload={openFiles}
                  onRename={onRename}
                  onMove={onMove}
                  onDelete={onDelete}
                  onColor={onColor}
                  onDeleteItem={onDeleteItem}
                  onDemo={() => void runCommand("project.demo", {}, "user")}
                  onSetMenu={(setId, x, y) => setMenu({ setId, x, y })}
                  onTableMenu={(setId, table, x, y) => (table.id === null ? setMenu({ setId, x, y }) : setTableMenu({ setId, id: table.id, name: table.name, x, y }))}
                  onNewLog={(setId, table) => void guarded(() => (table ? previewOnHoles(setId, table) : openChart("striplog", setId, undefined, undefined, undefined, { table })))}
                  onLogsMenu={(setId, x, y) => setLogsMenu({ setId, x, y })}
                  logsOn={ws.logs}
                  onToggleLog={toggleLog}
                  onOpenChart={(id) => void guarded(() => openSavedChart(id))}
                  onChartMenu={(chart, x, y) => setChartMenu({ ...chart, x, y })}
                />
              </ErrorBoundary>
            </aside>
            <Splitter axis="x" sign={1} size={ws.sizes.left} label="Ширина дерева проекта" onResize={(s) => setWs((prev) => resize(prev, "left", s))} />
          </>
        )}

        <div className="center">
          {error && <div className="alert error">{error}</div>}
          <section
            className="viewport" aria-label="3D-вид"
            onDragOver={(e) => { if (e.dataTransfer.types.includes(LOG_DRAG)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }}
            onDrop={(e) => {
              const id = Number(e.dataTransfer.getData(LOG_DRAG));
              if (!e.dataTransfer.types.includes(LOG_DRAG) || !Number.isInteger(id)) return;
              e.preventDefault();
              toggleLog(id, true);
            }}
          >
            <ErrorBoundary label="3D-вид">
              <Suspense fallback={<div className="canvas-note static">Загрузка 3D…</div>}>
              <SceneView
                datasets={datasets} ws={ws} refreshKey={refreshKey} viz={viz}
                activeSet={activeSet} selectedHole={selectedHole} section={section}
                onWorkspace={(patch) => setWs((prev) => ({ ...prev, ...patch }))}
                onPick={(setId, holeId) => selectHole(setId, holeId)}
                onSection={setSection}
                onEmptyClick={() => {
                  if (holeRef.current && activeSetRef.current !== null) selectHole(activeSetRef.current, null);
                }}
                registerApi={(api) => (sceneApi.current = api)}
                logs={logLayers}
                blockStyles={blockStyles}
                onPickBlock={(modelId, index) => {
                  setPickedBlock({ modelId, index });
                  live.current.selectObject({ kind: "blocks", id: modelId });
                }}
              />
              </Suspense>
            </ErrorBoundary>
          </section>
          {ws.bottom && (
            <>
              <Splitter axis="y" sign={-1} size={ws.sizes.bottom} label="Высота нижней панели" onResize={(s) => setWs((prev) => resize(prev, "bottom", s))} />
              <section className="dock dock-bottom" style={{ height: ws.sizes.bottom }} aria-label="Нижняя панель">
                <div className="dock-tabs" role="tablist">
                  {bottomTabs.map((tab) => (
                    <button key={tab} role="tab" aria-selected={ws.bottomTab === tab} className={ws.bottomTab === tab ? "active" : ""} onClick={() => setWs((p) => ({ ...p, bottomTab: tab }))}>
                      {BOTTOM_TITLES[tab]}
                      {tab === "log" && log.length > 0 && ` · ${log.length}`}
                      {tab === "jobs" && jobs.active > 0 && <span className="badge"> {jobs.active}</span>}
                      {tab !== "log" && tab !== "jobs" && activeSetName && <span className="muted"> · {activeSetName}</span>}
                    </button>
                  ))}
                  <span className="spacer" />
                  <button className="icon" onClick={() => setWs((p) => setPanel(p, "bottom", false))} title="Закрыть панель" aria-label="Закрыть панель"><Icon name="close" size={14} /></button>
                </div>
                <div className="dock-body">
                  <ErrorBoundary key={ws.bottomTab} label={BOTTOM_TITLES[ws.bottomTab]} compact>
                    {ws.bottomTab === "log" ? (
                      <CommandLog entries={log} />
                    ) : ws.bottomTab === "jobs" ? (
                      <JobsPanel jobs={jobs.jobs} uploads={jobs.uploads} onClearUploads={jobs.clearUploads} />
                    ) : activeSet === null ? (
                      <p className="muted small">Нет наборов скважин. Импортируйте данные: лента «Главная → Скважины».</p>
                    ) : ws.bottomTab === "table" ? (
                      <DataPage
                        setId={activeSet} refreshKey={refreshKey} kind={route.kind} holeId={selectedHole} summary={summary}
                        onKindChange={(kind) => navigate({ kind })} onClearHole={clearHole}
                      />
                    ) : ws.bottomTab === "stats" ? (
                      <StatsPage setId={activeSet} refreshKey={refreshKey} summary={summary} holes={holes} view={statsView} onViewChange={setStatsView} />
                    ) : (
                      <AuditPage key={activeSet} setId={activeSet} refreshKey={refreshKey} holeId={selectedHole} onAudited={reload} onClearHole={clearHole} />
                    )}
                  </ErrorBoundary>
                </div>
              </section>
            </>
          )}
        </div>

        {ws.right && (
          <>
            <Splitter axis="x" sign={-1} size={ws.sizes.right} label="Ширина правой панели" onResize={(s) => setWs((prev) => resize(prev, "right", s))} />
            <aside className="dock dock-right" style={{ width: ws.sizes.right }} aria-label="Свойства и ассистент">
              <div className="dock-tabs" role="tablist">
                <button role="tab" aria-selected={ws.rightTab === "properties"} className={ws.rightTab === "properties" ? "active" : ""} onClick={() => setWs((p) => ({ ...p, rightTab: "properties" }))}>Свойства</button>
                <button role="tab" aria-selected={ws.rightTab === "assistant"} className={ws.rightTab === "assistant" ? "active" : ""} onClick={() => setWs((p) => ({ ...p, rightTab: "assistant" }))}>Claude</button>
                <span className="spacer" />
                <button className="icon" onClick={() => setWs((p) => ({ ...p, right: false }))} title="Закрыть панель" aria-label="Закрыть панель"><Icon name="close" size={14} /></button>
              </div>
              {/* The chat stays mounted while hidden so a running request keeps its place. */}
              <div className="dock-body" hidden={ws.rightTab !== "properties"}>
                <ErrorBoundary label="Свойства" compact>
                  <PropertiesPanel
                    datasets={datasets ?? []} activeSet={activeSet} summary={summary} audit={audit}
                    selectedHole={selectedHole} selected={selected} refreshKey={refreshKey}
                    viz={viz} onViz={setViz}
                    onOpenTable={(kind) => void runCommand("table.open", { kind }, "user")} onClearHole={clearHole}
                    onRaster={(id, patch) => void guarded(async () => {
                      await patchRaster(id, patch);
                      reload();
                      return patch.vertical ? "Растр поставлен в вертикальную плоскость" : undefined;
                    })}
                    sectionFrame={() => (section ? sceneApi.current?.sectionFrame() ?? null : null)}
                    blockStyles={blockStyles}
                    onBlockStyle={(id, style) => setBlockStyles((prev) => ({ ...prev, [id]: style }))}
                    pickedBlock={pickedBlock}
                    onBlockUnit={(id, key, unit) => void guarded(async () => {
                      await setBlockUnit(id, key, unit);
                      reload();
                      return undefined;
                    })}
                    onBlockReport={openBlockReport}
                  />
                </ErrorBoundary>
              </div>
              <div className="dock-body flush" hidden={ws.rightTab !== "assistant"}>
                <AssistantPanel
                  items={assistant.items} busy={assistant.busy} status={assistant.status} approval={approval}
                  hasHole={selectedHole !== null}
                  context={[
                    project.name,
                    section && ws.clip ? `Разрез ${Math.round(section.az)}°` : "3D",
                    activeSetName ? `Набор: ${activeSetName}` : "Нет наборов",
                    selectedHole ? `Скважина ${selectedHole}` : "Скважина не выбрана",
                  ]}
                  onSend={(text) => void assistant.send(text)} onStop={assistant.stop} onReset={assistant.reset}
                />
              </div>
            </aside>
          </>
        )}
      </div>

      <StatusBar
        projectName={project.name} setName={activeSetName} holeCount={holes.length} selectedHole={selectedHole} audit={audit}
        mode={section && ws.clip ? `разрез, азимут ${Math.round(section.az)}°, окно от ${section.towards} до ${section.away}` : "3D"} assistant={assistant.status} remote={remote}
      />

      {(Object.keys(ACCEPT) as UploadKind[]).map((kind) => (
        <input key={kind} ref={fileInputs[kind]} type="file" accept={ACCEPT[kind]} multiple hidden data-upload={kind} onChange={(e) => onFiles(kind, e.target.files)} />
      ))}
      <input ref={anyFileInput} type="file" accept={WIZARD_ACCEPT} multiple hidden data-upload="any" onChange={(e) => onFiles(null, e.target.files)} />

      {wizard && (
        <ImportWizard
          projectId={projectId} files={wizard.files} blockModels={wizard.blockModels} kindHint={wizard.kindHint} datasets={datasets ?? []}
          sectionFrame={() => (section ? sceneApi.current?.sectionFrame() ?? null : null)}
          trackUpload={jobs.trackUpload} updateUpload={jobs.updateUpload} onStarted={onJobsStarted}
          onTables={(tables) => {
            setImportTarget(null);
            setNotice({ text: `Выберите в мастере скважин: ${tables.map((f) => f.name).join(", ")}`, error: false });
          }}
          onClose={() => setWizard(null)}
        />
      )}

      {notice && (
        <div className={notice.error ? "toast error" : "toast"} role="status" onClick={() => setNotice(null)}>
          {notice.text}
        </div>
      )}

      {importTarget !== undefined && (
        <ImportDialog
          projectId={projectId}
          sets={sets}
          initialSet={importTarget}
          onImported={reloadAndSelect}
          onClose={() => setImportTarget(undefined)}
        />
      )}

      {charts.map((c, i) => {
        const setName = sets.find((d) => d.id === c.setId)?.name ?? `набор ${c.setId}`;
        const saved = sets.find((d) => d.id === c.setId)?.charts?.find((x) => x.id === c.chartId);
        const onSettings = (settings: unknown, autoName: string) => saveChartSettings(c.chartId, settings, autoName);
        const draft = c.chartId < 0;
        return (
          <ChartWindow
            key={c.id} index={i} z={30 + c.z} title={`${saved?.name ?? CHART_TITLES[c.kind]}${draft ? " (не сохранено)" : ""} · ${setName}`} icon={c.kind}
            onFocus={() => setCharts((prev) => raise(prev, c.id))}
            onClose={() => {
              setCharts((prev) => prev.filter((x) => x.id !== c.id));
              if (draft) setLogSettings((prev) => { const { [c.chartId]: _gone, ...rest } = prev; return rest; });
            }}
          >
            {c.kind === "striplog" ? (
              <StripLogWindow setId={c.setId} setName={setName} initial={c.settings as Partial<StripLogSettings>} tableHint={c.hint?.table} holeHint={c.hint?.hole} onSettings={onSettings}
                saved={!draft} onSave={(settings, autoName) => void guarded(() => saveDraft(c.chartId, c.setId, settings, autoName))} />
            ) : c.kind === "qq" ? (
              <QQWindow sets={sets.map((d) => ({ id: d.id, name: d.name }))} initial={{ ...defaultQQ(c.setId), ...(c.settings as Partial<QQSettings>) }} onSettings={onSettings} />
            ) : c.kind === "dendrogram" ? (
              <DendrogramWindow setId={c.setId} setName={setName} initial={c.settings as Partial<DendrogramSettings>} onSettings={onSettings} />
            ) : (
              <HistogramWindow setId={c.setId} setName={setName} initial={c.settings as Partial<HistogramSettings>} onSettings={onSettings} />
            )}
          </ChartWindow>
        );
      })}

      {reports.map((r, i) => {
        const model = (datasets ?? []).find((d) => d.id === r.modelId && d.kind === "blocks");
        if (!model) return null;
        return (
          <ChartWindow
            key={r.id} index={charts.length + i} z={30 + r.z} title={`Содержание–тоннаж · ${model.name}`} icon="blocks"
            onFocus={() => openBlockReport(r.modelId)} onClose={() => setReports((prev) => prev.filter((x) => x.id !== r.id))}
          >
            <GradeTonnageWindow dataset={model} />
          </ChartWindow>
        );
      })}

      {chartMenu && (
        <ContextMenu
          x={chartMenu.x} y={chartMenu.y} title={chartMenu.name} onClose={() => setChartMenu(null)}
          items={[
            ...(chartMenu.kind === "striplog" ? [{
              label: ws.logs.includes(chartMenu.id) ? "Скрыть со скважин (3D)" : "Показать на скважинах (3D)", icon: "striplog" as const,
              run: () => toggleLog(chartMenu.id, !ws.logs.includes(chartMenu.id)),
            }] : []),
            { label: chartMenu.kind === "striplog" ? "Настройки и колонка…" : "Открыть", icon: chartMenu.kind, run: () => void guarded(() => openSavedChart(chartMenu.id)) },
            { label: "Переименовать…", icon: "edit", run: () => void guarded(async () => {
              const name = window.prompt("Название графика", chartMenu.name)?.trim();
              if (!name || name === chartMenu.name) return;
              await patchChart(chartMenu.id, { name });
              chartNames.current.set(chartMenu.id, name);
              reload();
              return `График переименован: «${name}»`;
            }) },
            "separator",
            { label: "Удалить", icon: "trash", run: () => void guarded(async () => {
              if (!window.confirm(`Удалить график «${chartMenu.name}»? Данные проб не меняются.`)) return;
              await deleteChart(chartMenu.id);
              setWs((prev) => setLogShown(prev, chartMenu.id, false));
              setCharts((prev) => prev.filter((x) => x.chartId !== chartMenu.id));
              reload();
              return `График «${chartMenu.name}» удалён`;
            }) },
          ]}
        />
      )}

      {menu && (() => {
        const target = sets.find((d) => d.id === menu.setId);
        const assays = target?.counts?.assay ?? 0;
        const noData = assays === 0 ? "нет проб" : undefined;
        return (
          <ContextMenu
            x={menu.x} y={menu.y} title={`Графики · ${target?.name ?? ""}`} onClose={() => setMenu(null)}
            items={[
              { label: "Гистограмма", icon: "histogram", disabledNote: noData, run: () => void runCommand("chart.histogram", { set_id: menu.setId }, "user") },
              { label: "Q-Q график", icon: "qq", disabledNote: noData, run: () => void runCommand("chart.qq", { set_id: menu.setId }, "user") },
              { label: "Дендрограмма", icon: "dendrogram", disabledNote: noData ?? ((target?.components?.length ?? 0) < 2 ? "нужно 2+ поля" : undefined), run: () => void runCommand("chart.dendrogram", { set_id: menu.setId }, "user") },
              { label: "Показать на скважинах (3D)", icon: "striplog", disabledNote: noData, run: () => void guarded(() => previewOnHoles(menu.setId, "assay")) },
              { label: "Данные по стволу (окно)…", icon: "striplog", disabledNote: noData, run: () => void guarded(() => openChart("striplog", menu.setId, undefined, undefined, undefined, { table: "assay" })) },
              "separator",
              { label: "Таблица проб", icon: "table", run: () => { selectSet(menu.setId); navigate({ kind: "assay" }); setWs((p) => openBottom(p, "table")); } },
            ]}
          />
        );
      })()}

      {logsMenu && (() => {
        const target = sets.find((d) => d.id === logsMenu.setId);
        const mine = (target?.charts ?? []).filter((c) => c.kind === "striplog").map((c) => c.id);
        const anyOn = mine.some((id) => ws.logs.includes(id));
        return (
          <ContextMenu
            x={logsMenu.x} y={logsMenu.y} title={`Данные по скважинам · ${target?.name ?? ""}`} onClose={() => setLogsMenu(null)}
            items={[
              { label: "Новое отображение…", icon: "striplog", run: () => void guarded(() => openChart("striplog", logsMenu.setId, undefined, undefined, undefined, { table: null })) },
              { label: "Скрыть все со скважин", icon: "clear", disabledNote: anyOn ? undefined : "ничего не показано", run: () => setWs((prev) => ({ ...prev, logs: prev.logs.filter((id) => !mine.includes(id)) })) },
            ]}
          />
        );
      })()}

      {tableMenu && (
        <ContextMenu
          x={tableMenu.x} y={tableMenu.y} title={tableMenu.name} onClose={() => setTableMenu(null)}
          items={[
            { label: "Показать на скважинах (3D)", icon: "striplog", run: () => void guarded(() => previewOnHoles(tableMenu.setId, String(tableMenu.id))) },
            { label: "Данные по стволу (окно)…", icon: "striplog", run: () => void guarded(() => openChart("striplog", tableMenu.setId, undefined, undefined, undefined, { table: String(tableMenu.id) })) },
            "separator",
            { label: "Переименовать…", icon: "edit", run: () => void guarded(async () => {
              const name = window.prompt("Название таблицы", tableMenu.name)?.trim();
              if (!name || name === tableMenu.name) return undefined;
              await renameIntervalTable(tableMenu.id, name);
              reload();
              return `Таблица переименована: «${name}»`;
            }) },
            { label: "Удалить таблицу", icon: "trash", run: () => void guarded(async () => {
              if (!window.confirm(`Удалить таблицу «${tableMenu.name}» со всеми интервалами? Пробы и другие таблицы не меняются.`)) return undefined;
              await deleteIntervalTable(tableMenu.id);
              reload();
              return `Таблица «${tableMenu.name}» удалена`;
            }) },
          ]}
        />
      )}

      {palette !== null && (
        <CommandPalette items={items} holes={holes} initialQuery={palette} onRun={runItem} onClose={() => setPalette(null)} />
      )}
    </div>
  );
}

export function App() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        let list = await listProjects();
        if (list.length === 0) {
          try {
            list = [await createProject("Основной проект")];
          } catch {
            // Another load (React StrictMode runs effects twice) created it first.
            list = await listProjects();
          }
        }
        if (list.length === 0) throw new Error("Не удалось создать проект");
        setProjects(list);
        const saved = loadSavedProjectId();
        setProjectId(list.some((p) => p.id === saved) ? saved : list[0].id);
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, []);

  function selectProject(id: number) {
    // Sets and holes belong to one project: drop them from the URL before the workspace is rebuilt.
    window.history.replaceState(null, "", formatRoute({ ...parseRoute(window.location.hash), set: null, hole: null }));
    setProjectId(id);
    saveProjectId(id);
  }

  async function onDemoProject(): Promise<string> {
    const project = await createDemoProject();
    setProjects((prev) => (prev?.some((p) => p.id === project.id) ? prev : [...(prev ?? []), project]));
    selectProject(project.id);
    return `Открыт проект «${project.name}»`;
  }

  /** Creates and opens a project; without a name the user is asked for one. */
  async function onNewProject(name?: string): Promise<string> {
    const title = (name ?? window.prompt("Название нового проекта") ?? "").trim();
    if (!title) return "Отменено";
    const project = await createProject(title);
    setProjects((prev) => [...(prev ?? []), project]);
    selectProject(project.id);
    return `Создан проект «${project.name}»`;
  }

  /** Deletes a project with everything in it, after the user confirms; opens another one. */
  async function onDeleteProject(id: number): Promise<string> {
    const target = projects?.find((p) => p.id === id);
    if (!target) throw new Error(`Проекта с id ${id} нет`);
    if (!window.confirm(`Удалить проект «${target.name}» со всеми данными? Это нельзя отменить.`)) return "Отменено";
    await deleteProject(id);
    let rest = (projects ?? []).filter((p) => p.id !== id);
    if (rest.length === 0) rest = [await createProject("Основной проект")];
    setProjects(rest);
    if (id === projectId) selectProject(rest[0].id);
    return `Проект «${target.name}» удалён`;
  }

  if (error) return <main className="page"><div className="alert error">{error}</div></main>;
  const project = projects?.find((p) => p.id === projectId);
  if (!projects || !project) return <main className="page"><p className="muted">Загрузка…</p></main>;

  return <Workspace key={project.id} projects={projects} project={project} onSelectProject={selectProject} onNewProject={onNewProject} onDemoProject={onDemoProject} onDeleteProject={onDeleteProject} />;
}
