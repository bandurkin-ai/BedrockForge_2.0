import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

const noSubscribe = () => () => undefined;
import {
  auditExportUrl, cancelJob, createChart, createDemoProject, deleteChart, getChart, patchChart, createProject, createSet, deleteDataset, deleteLines, deleteProject, deleteWireframe,
  deleteIntervalTable, renameIntervalTable, getIntervalTables, listProjects, moveDataset, patchLines, patchRaster, patchWireframe, editBlock, renameDataset, runAudit, setBlockUnit,
  getStyles, getStyleUsage, setAssistantPreferences, patchProject, startExport, startProjectExport, type Dataset, type JobInfo, type Project, type ProjectImportResult, type SavedChartKind, type StyleRecord, type PickResult,
} from "./api";
import { GradeTonnageWindow } from "./charts/GradeTonnageWindow";
import { StripLogWindow } from "./charts/StripLogWindow";
import { displayName, FORM_TITLES, tableDisplay, withDefaults, type DisplayForm, type StripLogSettings } from "./charts/stripLog";
import { DownholeForm } from "./viz/downhole/DownholeForm";
import { resolveTracks, unlinkStyle, type StyleRec } from "./viz/downhole/styles";
import type { ApplyTarget } from "./viz/downhole/ApplyDialog";
import { hasSamples } from "./shell/SampleTableSelect";
import { CollarStyleForm } from "./viz/CollarStyleForm";
import { DEFAULT_COLLAR_STYLE } from "./viz/collarStyle";
import { TraceStyleForm } from "./viz/TraceStyleForm";
import { DEFAULT_TRACE_STYLE } from "./viz/traceStyle";
import { defaultBlockStyle } from "./viz/blockStyle";
import { blockMeta } from "./api";
import { BlockSettingsForm } from "./viz/BlockSettingsForm";
import { AuditPage, DEFAULT_CONFIG } from "./AuditPage";
import { DataTables } from "./DataTables";
import { StatsPage } from "./StatsPage";
import type { StatsView } from "./stats/analyses";
import { formatRoute, parseRoute, useRoute, type TableKind } from "./route";
import { AssistantPanel, type Approval } from "./shell/AssistantPanel";
import { ClaudeAccount, ClaudeAccountButton } from "./shell/ClaudeAccount";
import { askConfirm, askText, DialogHost, showError } from "./shell/dialogs";
import { downloadWithLog } from "./shell/exportDownload";
import { ProjectsPanel } from "./shell/ProjectsPanel";
import { SettingsPanel } from "./shell/SettingsPanel";
import { applyShortcuts, loadShortcuts, saveShortcuts, type ShortcutOverrides } from "./shell/shortcuts";
import { CommandLog } from "./shell/CommandLog";
import { CommandPalette } from "./shell/CommandPalette";
import { commandTools, eventShortcut, findByToolName, validateArgs, type CommandSource, type LogEntry } from "./shell/commands";
import { DataTree } from "./shell/DataTree";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { Icon } from "./shell/icons";
import { ImportDialog } from "./shell/ImportDialog";
import { ImportWizard, WIZARD_ACCEPT } from "./shell/ImportWizard";
import { OmfExportDialog } from "./shell/OmfExportDialog";
import { ProjectExportDialog, ProjectImportDialog } from "./shell/ProjectArchiveDialogs";
import { applyTreeOrder, collectBrowserState, mergeBrowserState } from "./shell/projectBrowserState";
import { isBlockModelFile } from "./shell/sniffBlocks";
import { ChartWindow } from "./charts/ChartWindow";
import { HistogramWindow, type HistogramSettings } from "./charts/HistogramWindow";
import { QQWindow, defaultQQ, type QQSettings } from "./charts/QQWindow";
import { DendrogramWindow, type DendrogramSettings } from "./charts/DendrogramWindow";
import { ContextMenu } from "./shell/ContextMenu";
import type { ChartKind } from "./shell/registry";
import { JobsPanel } from "./shell/JobsPanel";
import { LogsDialog } from "./shell/LogButton";
import { useJobs } from "./shell/useJobs";
import { createModeling, type TriangleEdit } from "./shell/modeling";
import { FormHost } from "./shell/FormDialog";
import type { EditSession } from "./edit/session";
import type { MenuEntry } from "./shell/ContextMenu";
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
  omf: ".omf",
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

const CHART_TITLES: Record<ChartKind, string> = { histogram: "Гистограмма", qq: "Q-Q график", dendrogram: "Дендрограмма", striplog: "Данные по скважинам" };

/** An open form of a display of data along the holes: a new one (chartId null) or a saved one being edited. */
interface DisplayFormState {
  id: number;
  setId: number;
  form: DisplayForm;
  chartId: number | null;
  initial: StripLogSettings | null;
  z: number;
  /** the layer to select on opening (a click in the legend) */
  focus?: string | null;
}

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
  onPatchProject,
  onProjectImported,
}: {
  projects: Project[];
  project: Project;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  onDemoProject: () => Promise<string>;
  onDeleteProject: (id: number) => Promise<string>;
  onPatchProject: (id: number, patch: { name?: string; description?: string }) => Promise<string>;
  /** a project came from a .bfggis file: reread the list (an imported copy may have replaced a project) */
  onProjectImported: (result: ProjectImportResult) => Promise<void>;
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
  const fileInputs = { wireframes: useRef<HTMLInputElement>(null), lines: useRef<HTMLInputElement>(null), rasters: useRef<HTMLInputElement>(null), blocks: useRef<HTMLInputElement>(null), omf: useRef<HTMLInputElement>(null) };
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
  const [pickedBlock, setPickedBlock] = useState<{ modelId: number; index: number } | null>(null);
  const [menu, setMenu] = useState<{ setId: number; x: number; y: number } | null>(null);
  // Right click on an interval table of a set (not the assays: those get the charts menu).
  const [tableMenu, setTableMenu] = useState<{ setId: number; id: number; name: string; x: number; y: number } | null>(null);
  const [chartMenu, setChartMenu] = useState<{ id: number; kind: SavedChartKind; name: string; x: number; y: number } | null>(null);
  // Right click on «Стволы»: what to show along the holes.
  const [pathsMenu, setPathsMenu] = useState<{ setId: number; x: number; y: number } | null>(null);
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
      // The window of data along the holes is a viewer: it is not saved and adds nothing to the tree.
      const draftId = -(Date.now() + ++draftSeq.current);
      showChart(draftId, kind, setId, settings, { table: striplog?.table, hole: striplog?.hole ?? (activeSetRef.current === setId ? holeRef.current : null) });
      return "Открыто окно «Данные по скважинам»";
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
    if (c.kind === "downhole") {
      await openDisplayForm(c.set_id, "grade", c.id);
      return `Открыты настройки «${c.name}»`;
    }
    showChart(c.id, c.kind, c.set_id, c.settings);
    return `Открыт график «${c.name}»`;
  };
  // Settings of open charts are saved a moment after the last change.
  const saveTimers = useRef(new Map<number, number>());
  const chartNames = useRef(new Map<number, string>());
  // Data along the holes in the 3D view: the saved settings of every display that is on.
  const [logSettings, setLogSettings] = useState<Record<number, StripLogSettings>>({});
  const logRequested = useRef(new Set<number>());
  const toggleLog = (chartId: number, on: boolean) => setWs((prev) => setLogShown(prev, chartId, on));
  useEffect(() => {
    for (const id of ws.logs) {
      if (logRequested.current.has(id)) continue;
      logRequested.current.add(id);
      getChart(id)
        .then((c) => setLogSettings((prev) => (id in prev ? prev : { ...prev, [id]: withDefaults(c.settings as Partial<StripLogSettings>) })))
        .catch(() => logRequested.current.delete(id)); // deleted: it just does not show
    }
  }, [ws.logs]);
  // Forms of displays (grades, lithology, events): what a form shows is previewed in the view; only its button saves.
  const [forms, setForms] = useState<DisplayFormState[]>([]);
  const [previews, setPreviews] = useState<Record<number, StripLogSettings>>({});
  const topZ = () => Math.max(0, ...charts.map((c) => c.z), ...forms.map((f) => f.z)) + 1;
  /** Opens the form of a new display, or of a saved one (chartId). */
  const openDisplayForm = async (setId: number, form: DisplayForm, chartId: number | null = null, focus: string | null = null): Promise<void> => {
    const open = chartId !== null ? forms.find((f) => f.chartId === chartId) : undefined;
    if (open) {
      setForms((prev) => prev.map((f) => (f.id === open.id ? { ...f, z: topZ(), focus: focus ?? f.focus } : f)));
      return;
    }
    let initial: StripLogSettings | null = null;
    if (chartId !== null) {
      const c = await getChart(chartId);
      initial = withDefaults(c.settings as Partial<StripLogSettings>);
      form = initial.form ?? (initial.tracks[0]?.type === "event" ? "event" : initial.tracks[0]?.type === "color" || initial.tracks[0]?.type === "color_text" ? "lith" : "grade");
      setId = c.set_id;
    }
    setForms((prev) => [...prev, { id: Date.now(), setId, form, chartId, initial, z: topZ(), focus }]);
  };
  const closeForm = (id: number) => {
    setForms((prev) => prev.filter((f) => f.id !== id));
    setPreviews((prev) => {
      const { [id]: _gone, ...rest } = prev;
      return rest;
    });
  };
  /** «Сохранить» of a form: a new row under «Стволы» (on at once), or the changes of a saved display. */
  const saveDisplay = async (f: DisplayFormState, settings: StripLogSettings, name: string): Promise<string> => {
    const setName = sets.find((d) => d.id === f.setId)?.name ?? "";
    if (f.chartId !== null) {
      const c = await patchChart(f.chartId, { settings, auto_name: name });
      const id = f.chartId;
      setLogSettings((prev) => ({ ...prev, [id]: settings }));
      reload();
      return `Сохранено: ${setName} → Стволы → «${c.name}»`;
    }
    const saved = await createChart(f.setId, "downhole", name, settings);
    logRequested.current.add(saved.id);
    setLogSettings((prev) => ({ ...prev, [saved.id]: settings }));
    setForms((prev) => prev.map((x) => (x.id === f.id ? { ...x, chartId: saved.id, initial: settings } : x)));
    setWs((prev) => setLogShown(prev, saved.id, true));
    reload();
    return `Сохранено в дереве: ${setName} → Стволы → «${saved.name}»`;
  };
  // The library of styles of the project: linked layers of every display take their settings from it.
  const [styles, setStyles] = useState<StyleRecord[]>([]);
  useEffect(() => {
    let cancelled = false;
    getStyles(projectId).then((list) => !cancelled && setStyles(list)).catch(() => !cancelled && setStyles([]));
    return () => {
      cancelled = true;
    };
  }, [projectId]);
  /** Before a style is deleted: saved displays linked to it keep a copy of its settings. */
  const unlinkCharts = async (style: StyleRec): Promise<void> => {
    const usage = await getStyleUsage(style.id);
    for (const u of usage) {
      const c = await getChart(u.chart_id);
      const settings = unlinkStyle(withDefaults(c.settings as Partial<StripLogSettings>), style.id, styles as StyleRec[]);
      await patchChart(u.chart_id, { settings });
      setLogSettings((prev) => (u.chart_id in prev ? { ...prev, [u.chart_id]: settings } : prev));
    }
  };
  /** «Применить к…» of a style: new displays, saved and on at once. */
  const createDisplays = async (targets: ApplyTarget[]): Promise<string> => {
    const names: string[] = [];
    for (const t of targets) {
      const table = (await getIntervalTables(t.setId)).find((x) => x.key === t.settings.tracks[0]?.table);
      const saved = await createChart(t.setId, "downhole", displayName(t.settings, table?.name), t.settings);
      logRequested.current.add(saved.id);
      setLogSettings((prev) => ({ ...prev, [saved.id]: t.settings }));
      setWs((prev) => setLogShown(prev, saved.id, true));
      names.push(saved.name);
    }
    reload();
    return `Создано отображений: ${names.length} (${names.slice(0, 3).join(", ")}${names.length > 3 ? "…" : ""})`;
  };
  /** Fields of a table on the holes of its set, saved under «Стволы» and on at once (the assistant's command). */
  const showOnHoles = async (setId: number, tableKey: string, fields?: string[]): Promise<string> => {
    const tables = await getIntervalTables(setId);
    const table = tables.find((t) => t.key === tableKey);
    if (!table || table.columns.length === 0) throw new Error("В таблице нет полей для показа на скважинах");
    const base = tableDisplay(table, fields);
    const names: string[] = [];
    for (const t of base.tracks) {
      const track = table.kind === "event" ? { ...t, type: "event" as const, marker: "diamond" as const, size: 12 } : t;
      const form: DisplayForm = table.kind === "event" ? "event" : track.type === "bars" || track.type === "line" ? "grade" : "lith";
      const settings: StripLogSettings = { ...base, form, tracks: [track] };
      const saved = await createChart(setId, "downhole", displayName(settings, table.name), settings);
      logRequested.current.add(saved.id);
      setLogSettings((prev) => ({ ...prev, [saved.id]: settings }));
      setWs((prev) => setLogShown(prev, saved.id, true));
      names.push(saved.name);
    }
    reload();
    return `На скважинах показано: ${names.join(", ")}; настройки — в дереве: ${sets.find((d) => d.id === setId)?.name ?? ""} → Стволы`;
  };
  // Marker and label of the collars of a set (the gear of «Устья»), and windows with rows of interval tables.
  const [collarForm, setCollarForm] = useState<number | null>(null);
  const [traceForm, setTraceForm] = useState<number | null>(null);
  // The settings window of a block model (the gear in the tree): restriction, colouring, view.
  const [blockForm, setBlockForm] = useState<number | null>(null);
  const [traceNotes, setTraceNotes] = useState<Record<number, string[]>>({});
  /** An interval or event table of a set: in the bottom panel, like the collars, surveys and assays. */
  const openIntervalTable = (t: { id: number; set_id?: number }, setId?: number) => {
    const sid = setId ?? t.set_id;
    if (sid !== undefined && sid !== activeSetRef.current) selectSet(sid);
    navigate({ kind: String(t.id) });
    // a table needs room: grow a short dock, never shrink one the user enlarged
    setWs((prev) => resize(openBottom(prev, "table"), "bottom", Math.max(prev.sizes.bottom, 380)));
  };
  const saveChartSettings = (chartId: number, settings: unknown, autoName: string) => {
    if (chartId < 0) return; // the window of data along the holes is a viewer: nothing is saved
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
  // The string editor's session and the manual triangle edit, while they are on.
  const [edit, setEdit, editRef] = useLiveState<EditSession | null>(null);
  const [triangles, setTriangles, trianglesRef] = useLiveState<TriangleEdit | null>(null);
  const [objectMenu, setObjectMenu] = useState<{ x: number; y: number; title: string; items: MenuEntry[] } | null>(null);
  const [triangleInfo, setTriangleInfo] = useState<PickResult | null>(null);
  const modelingRef = useRef<ReturnType<typeof createModeling>>(null!);
  const jobs = useJobs(projectId, (job: JobInfo) => {
    if (job.status === "done") {
      reload();
      modelingRef.current?.jobDone(job);
    }
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
  // What the 3D view draws on the holes: saved displays that are on (a display being edited shows its form's
  // state, on or not), then new displays of open forms. The order is the order of the tree: columns stand in it.
  const logLayers = useMemo<DownholeLog[]>(() => {
    const out: DownholeLog[] = [];
    for (const d of sets) {
      for (const c of d.charts ?? []) {
        if (c.kind !== "downhole") continue;
        const editing = forms.find((f) => f.chartId === c.id);
        const settings = (editing && previews[editing.id]) || (ws.logs.includes(c.id) ? logSettings[c.id] : undefined);
        if (settings) out.push({ id: c.id, setId: d.id, name: c.name, settings: editing ? settings : resolveTracks(settings, styles as StyleRec[]) });
      }
      for (const f of forms) {
        if (f.setId === d.id && f.chartId === null && previews[f.id]) out.push({ id: -f.id, setId: d.id, name: `${FORM_TITLES[f.form]} (не сохранено)`, settings: previews[f.id] });
      }
    }
    return out;
  }, [sets, ws.logs, logSettings, forms, previews, styles]);

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

  const [accountOpen, setAccountOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(false);
  /** The OMF export window: the datasets checked when it opens ([]: every exportable one). */
  const [omfExport, setOmfExport] = useState<number[] | null>(null);
  /** The window of an export of a whole project (.bfggis) and the one of an import. */
  const [projectExport, setProjectExport] = useState<{ id: number; name: string } | null>(null);
  const [projectImport, setProjectImport] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [shortcuts, setShortcutsState] = useState<ShortcutOverrides>(loadShortcuts);
  const setShortcuts = (next: ShortcutOverrides) => {
    setShortcutsState(next);
    saveShortcuts(next);
  };
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
      setWs((prev) => resize(openBottom(prev, "table"), "bottom", Math.max(prev.sizes.bottom, 380)));
    },
    openChart: (kind, setId, initial, qq, dendrogram, striplog) => openChart(kind, setId, initial, qq, dendrogram, striplog),
    openSavedChart,
    showOnHoles,
    previewOnHoles: (setId) => openChart("striplog", setId, undefined, undefined, undefined, { table: null }),
    openBlockReport,
    openStats: (analysis, component) => {
      setStatsView((prev) => ({ analysis, component: component ?? prev.component }));
      // charts need room: grow a short dock, never shrink one the user enlarged
      setWs((prev) => resize(openBottom(prev, "stats"), "bottom", Math.max(prev.sizes.bottom, 400)));
    },
    reload, onSelectProject, onNewProject, onDemoProject, onDeleteProject, onPatchProject,
    openProjects: () => setProjectsOpen(true),
    openProjectExport: (id) => {
      const target = projects.find((p) => p.id === (id ?? projectId));
      if (target) setProjectExport({ id: target.id, name: target.name });
    },
    openProjectImport: () => setProjectImport(true),
    exportProject: async (id, filename, includeView = true) => {
      const target = projects.find((p) => p.id === id);
      const job = await startProjectExport(id, {
        filename: filename || `${target?.name ?? "Проект"}_${new Date().toISOString().slice(0, 10)}`,
        browser: includeView && id === projectId ? { ...collectBrowserState(wsRef.current, datasets ?? []) } : null,
      });
      onExportStarted(job);
      return job;
    },
    openSettings: () => setSettingsOpen(true),
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
    exportAudit: (setId) => void downloadWithLog(auditExportUrl(setId), "audit.xlsx", "Отчёт аудита в Excel", { "Набор": setId }).catch(() => undefined),
    openOmfExport: (ids) => setOmfExport(ids ?? []),
    openLogs: () => setLogsOpen(true),
    exportOmf: async (ids, filename) => {
      const job = await startExport(projectId, { format: "omf", dataset_ids: ids, filename });
      onExportStarted(job);
      return job;
    },
    fit: () => sceneApi.current?.fit(),
    view: (preset) => sceneApi.current?.view(preset),
    drawSection: () => sceneApi.current?.drawSection(),
    openSection: (line) => sceneApi.current?.openSection(line),
    ask: (prompt) => void assistant.send(prompt),
    resetChat: assistant.reset,
    get modeling() { return modelingRef.current; },
    edit: () => editRef.current,
  };
  modelingRef.current = useMemo(() => createModeling({
    projectId,
    datasets: () => live.current.datasets,
    reload,
    notify: (text, error = false) => setNotice({ text, error }),
    edit: () => editRef.current,
    setEdit,
    section: () => sectionRef.current,
    selected: () => selectedRef.current,
    select: (object) => live.current.selectObject(object),
    show: (kind, id) => setWs((prev) => setShown(prev, kind === "wireframe" ? layerKey.wireframe(id) : layerKey.lines(id), true)),
    onJobStarted: (job) => onJobsStarted([job]),
    triangles: () => trianglesRef.current,
    setTriangles,
    mark: (key, corners, color) => sceneApi.current?.mark(key, corners, color),
  }), [projectId]); // eslint-disable-line react-hooks/exhaustive-deps
  const modeling = modelingRef.current;
  // Leaving the page with unsaved lines asks first.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (editRef.current?.dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [editRef]);
  // The tree and the status bar show whether the edited layer has unsaved changes.
  const editDirty = useSyncExternalStore(edit?.subscribe ?? noSubscribe, () => edit?.dirty ?? false);
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

  const builtTabs = useMemo(
    () => buildRibbon(ws, { hasHole: selectedHole !== null, hasSet: activeSet !== null, hasSection: section !== null, editing: edit !== null }),
    [ws, selectedHole, activeSet, section, edit],
  );
  // The user's hotkeys (Главная › Настройка) replace the built-in ones everywhere: ribbon hints, palette, keys.
  const tabs = useMemo(() => applyShortcuts(builtTabs, shortcuts), [builtTabs, shortcuts]);
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
      const name = await askText({ title: "Переименовать", message: `Новое название для «${current}»`, value: current });
      if (!name || name === current) return;
      if (raster) await patchRaster(raster.id, { name });
      else await renameDataset(d.id, name);
      reload();
      return `Переименовано: «${name}»`;
    });

  const onMove = (d: Dataset) =>
    guarded(async () => {
      const folder = await askText({ title: "Переместить в папку", message: "Папка через «/»; пусто — в корень группы", value: d.folder ?? "", allowEmpty: true });
      if (folder === null || folder === (d.folder ?? "")) return;
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
      if (!(await askConfirm({ title: "Удаление", message: `Удалить ${what}? Это действие нельзя отменить.`, danger: true }))) return;
      await deleteDataset(d.id);
      const items = [...(d.wireframes ?? []), ...(d.lines ?? []), ...(d.rasters ?? [])].map((x) => x.id);
      if (selected && items.includes(selected.id)) setSelected(null);
      reload();
      return `Удалено: «${d.name}»`;
    });

  const onDeleteItem = (kind: "wireframe" | "lines", id: number, name: string) =>
    guarded(async () => {
      const what = kind === "wireframe" ? `каркас «${name}»` : `слой линий «${name}»`;
      if (!(await askConfirm({ title: "Удаление", message: `Удалить ${what}? Это действие нельзя отменить.`, danger: true }))) return;
      await (kind === "wireframe" ? deleteWireframe(id) : deleteLines(id));
      if (selected?.kind === kind && selected.id === id) setSelected(null);
      reload();
      return `Удалено: ${what}`;
    });

  const onDtm = (id: number, on: boolean) => void runCommand("wireframe.dtm", { wireframe_id: id, on }, "user");

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

  function onExportStarted(job: JobInfo) {
    setWs((prev) => openBottom(prev, "jobs"));
    setNotice({ text: `${job.title}: идёт в фоне, файл появится во вкладке «Задания»`, error: false });
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
    <div
      className={dragging ? "app drop-target" : "app"} {...dropHandlers}
      // Only the application's own menus: the browser's menu stays just in text fields (copy, paste).
      onContextMenu={(e) => {
        const t = e.target as HTMLElement;
        const text = t instanceof HTMLTextAreaElement || (t instanceof HTMLInputElement && /^(text|search|number|email|url|password|)$/.test(t.type)) || t.isContentEditable;
        if (!text) e.preventDefault();
      }}
    >
      <header className="titlebar">
        <span className="brand"><span className="brand-mark" aria-hidden="true" />BedrockForge<span className="brand-by">by Mikhail Bandurkin</span></span>
        <label className="project-pick">
          <span className="muted">Проект</span>
          <select value={projectId} onChange={(e) => onSelectProject(Number(e.target.value))} aria-label="Проект">
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="icon" onClick={() => setProjectsOpen(true)} title="Управление проектами: переименовать, описание, состав, все проекты" aria-label="Управление проектами">
            <Icon name="gear" size={15} />
          </button>
        </label>
        <button className="command-search" onClick={() => setPalette("")} title="Поиск команд и скважин (Ctrl+K)">
          <Icon name="search" size={14} />
          <span>Поиск команд и скважин…</span>
          <kbd>Ctrl+K</kbd>
        </button>
        <span className="spacer" />
        <button className="icon" onClick={() => runItem({ command: "theme.set", label: "", icon: "theme" })} title={ws.theme === "dark" ? "Тёмная тема — включить светлую" : "Светлая тема — включить тёмную"} aria-label="Сменить тему">
          <Icon name={ws.theme === "dark" ? "moon" : "sun"} size={16} />
        </button>
        <button
          className={ws.right && ws.rightTab === "assistant" ? "claude-button active" : "claude-button"}
          onClick={() => runItem({ command: "panel.set", args: { panel: "assistant" }, label: "", icon: "spark" })}
          title="Ассистент Claude (Ctrl+J)"
        >
          <Icon name="spark" size={16} /> Claude
        </button>
        <ClaudeAccountButton status={assistant.status} onClick={() => setAccountOpen(true)} />
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
                  onDtm={onDtm}
                  onDtmSettings={(id) => void runCommand("object.select", { kind: "wireframe", id }, "user")}
                  editing={edit ? { layerId: edit.layerId, dirty: editDirty } : null}
                  onLinesMenu={(x, y) => setObjectMenu({ x, y, title: "Линии", items: [
                    { label: "Новый слой…", icon: "plus", run: () => void runCommand("lines.new_layer", {}, "user") },
                    { label: "Загрузить файл: STR, DXF", icon: "import", run: () => openFiles("lines") },
                  ] })}
                  onItemMenu={(kind, id, x, y) => {
                    live.current.selectObject({ kind, id });
                    const cmd = (label: string, command: string, args: Record<string, unknown> = {}): MenuEntry => ({ label, run: () => void runCommand(command, args, "user") });
                    const name = (kind === "lines" ? (datasets ?? []).flatMap((d) => d.lines ?? []) : (datasets ?? []).flatMap((d) => d.wireframes ?? [])).find((o) => o.id === id)?.name ?? "";
                    const isDtm = kind === "wireframe" && Boolean((datasets ?? []).flatMap((d) => d.wireframes ?? []).find((o) => o.id === id)?.dtm);
                    setObjectMenu({ x, y, title: name, items: kind === "lines" ? [
                      edit?.layerId === id ? cmd("Завершить правку", "lines.finish") : cmd("Редактировать", "lines.edit", { layer_id: id }),
                      cmd("Версии…", "lines.versions", { layer_id: id }),
                      "separator",
                      cmd("Сшить контуры в тело…", "model.stitch"), cmd("Собрать из контуров", "model.assemble"), cmd("Выдавить полигон…", "model.extrude"), cmd("ЦМР из точек и линий…", "model.dtm"),
                      "separator",
                      cmd("Экспорт в Micromine STR", "lines.export", { layer_id: id, format: "str" }), cmd("Экспорт в DXF", "lines.export", { layer_id: id, format: "dxf" }),
                    ] : [
                      ...(isDtm ? [cmd("Настройки ЦТМ…", "object.select", { kind: "wireframe", id }), cmd("Вернуть в каркасы", "wireframe.dtm", { wireframe_id: id, on: false })]
                        : [cmd("Перенести в ЦТМ", "wireframe.dtm", { wireframe_id: id, on: true })]),
                      "separator",
                      cmd("Исправить…", "model.repair"), cmd("Показать самопересечения", "model.self_intersections"), cmd("Править треугольники", "model.triangles"),
                      cmd("Упростить…", "model.simplify"), cmd("Сгладить…", "model.smooth"), cmd("Разделить на части", "model.split"), cmd("Объединить в один объект…", "model.merge"),
                      "separator",
                      cmd("Булева операция…", "model.boolean"), cmd("Обрезать…", "model.clip"), cmd("Линии пересечения…", "model.intersection"),
                      cmd("Изолинии…", "model.contours"), cmd("Объём между поверхностями…", "model.volume"), cmd("Перестроить", "model.rebuild", { wireframe_id: id }),
                      "separator",
                      cmd("Экспорт в DXF", "wireframe.export", { wireframe_id: id, format: "dxf" }), cmd("Экспорт в OBJ", "wireframe.export", { wireframe_id: id, format: "obj" }),
                    ] });
                  }}
                  onDemo={() => void runCommand("project.demo", {}, "user")}
                  onSetMenu={(setId, x, y) => setMenu({ setId, x, y })}
                  onTableMenu={(setId, table, x, y) => (table.id === null ? setMenu({ setId, x, y }) : setTableMenu({ setId, id: table.id, name: table.name, x, y }))}
                  onCollarStyle={(setId) => setCollarForm(setId)}
                  onTraceStyle={(setId) => setTraceForm(setId)}
                  onBlockSettings={(id) => setBlockForm(id)}
                  onPathsMenu={(setId, x, y) => setPathsMenu({ setId, x, y })}
                  onOpenDisplay={(id) => void guarded(() => openDisplayForm(0, "grade", id))}
                  onOpenIntervalTable={(t, setId) => openIntervalTable(t, setId)}
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
                onLegendLayer={(chartId, trackId) => chartId > 0 && void guarded(() => openDisplayForm(0, "grade", chartId, trackId))}
                collarStyles={ws.collarStyles}
                traceStyles={ws.traceStyles}
                onTraceNotes={(setId, notes) => setTraceNotes((prev) => (JSON.stringify(prev[setId] ?? []) === JSON.stringify(notes) ? prev : { ...prev, [setId]: notes }))}
                blockStyles={ws.blockStyles}
                onPickBlock={(modelId, index) => {
                  setPickedBlock({ modelId, index });
                  live.current.selectObject({ kind: "blocks", id: modelId });
                }}
                onPickWireframe={(id) => live.current.selectObject({ kind: "wireframe", id })}
                edit={edit}
                onEditSave={() => void modeling.save()}
                onEditExit={() => void modeling.leave()}
                onAssemble={(lines, closed) => void guarded(() => modeling.finishAssemble(lines, closed))}
                onTriangle={(r) => {
                  if (!modeling.triangleClick(r)) setTriangleInfo(r);
                }}
                banner={triangles && (
                  <div className="edit-toolbar" aria-label="Правка треугольников">
                    <div className="edit-row">
                      <strong className="edit-layer">Правка треугольников: {triangles.name}</strong>
                      <div className="segmented">
                        <button className={triangles.mode === "remove" ? "active" : ""} onClick={() => modeling.setTriangleMode("remove")} title="Щелчок по треугольнику отмечает его к удалению (повторный — снимает)">Удалить</button>
                        <button className={triangles.mode === "add" ? "active" : ""} onClick={() => modeling.setTriangleMode("add")} title="Три щелчка у вершин каркаса — новый треугольник">Добавить</button>
                      </div>
                      <span className="muted small">к удалению {triangles.remove.size} · новых {triangles.add.length}{triangles.pending.length ? ` · вершин указано ${triangles.pending.length} из 3` : ""}</span>
                      <span className="toolbar-sep" />
                      <button className="primary" disabled={!triangles.remove.size && !triangles.add.length} onClick={() => void guarded(() => modeling.applyTriangles(true))}>Применить</button>
                      <button onClick={() => void modeling.applyTriangles(false)}>Отмена</button>
                    </div>
                  </div>
                )}
                selectedWireframe={selected?.kind === "wireframe" ? selected.id : null}
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
                      <DataTables
                        setId={activeSet} refreshKey={refreshKey} tableKey={route.kind} holeId={selectedHole}
                        onTableChange={(kind) => navigate({ kind })} onClearHole={clearHole}
                        onSelectHole={(hole) => selectHole(activeSet, hole)} onChanged={reload}
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
                    blockStyles={ws.blockStyles}
                    onBlockStyle={(id, style) => setWs((prev) => ({ ...prev, blockStyles: { ...prev.blockStyles, [id]: style } }))}
                    onBlockSettings={(id) => setBlockForm(id)}
                    pickedBlock={pickedBlock}
                    onBlockUnit={(id, key, unit) => void guarded(async () => {
                      await setBlockUnit(id, key, unit);
                      reload();
                      return undefined;
                    })}
                    onBlockReport={openBlockReport}
                    onWireframe={async (id, patch) => {
                      await patchWireframe(id, patch);
                      reload();
                    }}
                    edit={edit} triangle={triangleInfo}
                    onCommand={(id, args) => void runCommand(id, args ?? {}, "user")}
                    onBlockValues={async (id, index, values) => {
                      const stored = await editBlock(id, index, values);
                      reload();
                      return stored;
                    }}
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
                  title={assistant.title} usage={assistant.usage} chats={assistant.chats} chatId={assistant.chatId}
                  onOpenChat={(id) => void assistant.openChat(id).catch(showError)}
                  onRenameChat={(id, name) => void assistant.renameChat(id, name).catch(showError)}
                  onDeleteChat={(id) => void assistant.deleteChat(id).catch(showError)}
                  onAccount={() => setAccountOpen(true)}
                  onModel={(model) => void setAssistantPreferences({ model }).then(assistant.setStatus).catch(showError)}
                />
              </div>
            </aside>
          </>
        )}
      </div>

      <DialogHost />
      <FormHost />
      {objectMenu && <ContextMenu x={objectMenu.x} y={objectMenu.y} title={objectMenu.title} items={objectMenu.items} onClose={() => setObjectMenu(null)} />}
      <StatusBar
        projectName={project.name} setName={activeSetName} holeCount={holes.length} selectedHole={selectedHole} audit={audit}
        mode={(edit ? `правка «${edit.layerName}»${editDirty ? " — не сохранено" : ""} · ` : "") + (section && ws.clip ? `разрез, азимут ${Math.round(section.az)}°, окно от ${section.towards} до ${section.away}` : "3D")} assistant={assistant.status} remote={remote}
        onAssistant={() => setAccountOpen(true)}
      />

      {projectsOpen && (
        <ChartWindow index={1} z={58} size="medium" icon="folder" title="Проекты" onFocus={() => undefined} onClose={() => setProjectsOpen(false)}>
          <ProjectsPanel
            projects={projects} currentId={projectId} refreshKey={refreshKey}
            onOpen={(id) => { setProjectsOpen(false); onSelectProject(id); }}
            onRename={(id, name) => onPatchProject(id, { name })}
            onDescribe={(id, description) => onPatchProject(id, { description })}
            onDelete={(id) => onDeleteProject(id)}
            onNew={() => onNewProject()}
            onDemo={() => onDemoProject()}
            onExport={(p) => setProjectExport({ id: p.id, name: p.name })}
            onImport={() => setProjectImport(true)}
          />
        </ChartWindow>
      )}

      {settingsOpen && (
        <ChartWindow index={1} z={58} size="medium" icon="gear" title="Настройка" onFocus={() => undefined} onClose={() => setSettingsOpen(false)}>
          <SettingsPanel tabs={tabs} defaults={builtTabs} overrides={shortcuts} onChange={setShortcuts} />
        </ChartWindow>
      )}

      {accountOpen && (
        <ChartWindow index={1} z={60} size="form" icon="spark" title="Claude: подключение" onFocus={() => undefined} onClose={() => setAccountOpen(false)}>
          <ClaudeAccount status={assistant.status} onStatus={(st) => { assistant.setStatus(st); assistant.refreshStatus(); }} />
        </ChartWindow>
      )}

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

      {omfExport && (
        <OmfExportDialog
          projectName={projects.find((p) => p.id === projectId)?.name ?? "Проект"} datasets={datasets ?? []} initial={omfExport}
          onStart={(ids, filename) => live.current.exportOmf(ids, filename)}
          onClose={() => setOmfExport(null)}
        />
      )}

      {projectExport && (
        <ProjectExportDialog
          project={projectExport}
          browserState={projectExport.id === projectId ? { ...collectBrowserState(ws, datasets ?? []) } : null}
          onClose={() => setProjectExport(null)}
        />
      )}

      {projectImport && (
        <ProjectImportDialog
          onImported={(result) => void onProjectImported(result)}
          onOpenProject={(result, applyView) => {
            if (applyView && result.browser) {
              // Saved now: this workspace is rebuilt for the other project and reads it from storage.
              saveWorkspace(mergeBrowserState(wsRef.current, result.browser, result.maps));
              applyTreeOrder(result.browser);
            }
            setProjectImport(false);
            setProjectsOpen(false);
            onSelectProject(result.project_id);
          }}
          onClose={() => setProjectImport(false)}
        />
      )}

      {logsOpen && <LogsDialog jobs={jobs.jobs} onClose={() => setLogsOpen(false)} />}

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
        return (
          <ChartWindow
            key={c.id} index={i} z={30 + c.z} title={`${saved?.name ?? CHART_TITLES[c.kind]} · ${setName}`} icon={c.kind}
            onFocus={() => setCharts((prev) => raise(prev, c.id))}
            onClose={() => setCharts((prev) => prev.filter((x) => x.id !== c.id))}
          >
            {c.kind === "striplog" ? (
              <StripLogWindow projectId={projectId} setId={c.setId} setName={setName} initial={c.settings as Partial<StripLogSettings>} tableHint={c.hint?.table} holeHint={c.hint?.hole} onSettings={onSettings} />
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

      {forms.map((f, i) => {
        const setName = sets.find((d) => d.id === f.setId)?.name ?? `набор ${f.setId}`;
        const saved = f.chartId !== null ? sets.find((d) => d.id === f.setId)?.charts?.find((x) => x.id === f.chartId) : undefined;
        return (
          <ChartWindow
            key={f.id} index={i + 2} z={30 + f.z} size="form" icon="striplog"
            title={`${saved?.name ?? `${FORM_TITLES[f.form]} (новое)`} · ${setName}`}
            onFocus={() => setForms((prev) => prev.map((x) => (x.id === f.id ? { ...x, z: topZ() } : x)))} onClose={() => closeForm(f.id)}
          >
            <DownholeForm
              projectId={projectId} setId={f.setId} form={f.form} initial={f.initial} saved={f.chartId !== null} refreshKey={refreshKey} focus={f.focus}
              styles={styles} sets={sets.map((d) => ({ id: d.id, name: d.name }))} onStyles={setStyles} onUnlinkCharts={unlinkCharts}
              onCreate={createDisplays} onOpenDisplay={(id) => void guarded(() => openDisplayForm(0, "grade", id))}
              onPreview={(settings) => setPreviews((prev) => {
                if (!settings) {
                  const { [f.id]: _gone, ...rest } = prev;
                  return rest;
                }
                return { ...prev, [f.id]: settings };
              })}
              onSave={(settings, name) => saveDisplay(f, settings, name)}
            />
          </ChartWindow>
        );
      })}

      {traceForm !== null && sets.some((d) => d.id === traceForm) && (
        <ChartWindow
          index={1} z={30 + topZ()} size="compact" icon="gear" title={`Стволы · ${sets.find((d) => d.id === traceForm)?.name ?? ""}`}
          onFocus={() => undefined} onClose={() => setTraceForm(null)}
        >
          <TraceStyleForm
            style={ws.traceStyles[traceForm] ?? DEFAULT_TRACE_STYLE}
            notes={traceNotes[traceForm]}
            onChange={(next) => setWs((prev) => ({ ...prev, traceStyles: { ...prev.traceStyles, [traceForm]: next } }))}
          />
        </ChartWindow>
      )}

      {collarForm !== null && sets.some((d) => d.id === collarForm) && (
        <ChartWindow
          index={1} z={30 + topZ()} size="compact" icon="gear" title={`Устья · ${sets.find((d) => d.id === collarForm)?.name ?? ""}`}
          onFocus={() => undefined} onClose={() => setCollarForm(null)}
        >
          <CollarStyleForm
            style={ws.collarStyles[collarForm] ?? DEFAULT_COLLAR_STYLE}
            onChange={(next) => setWs((prev) => ({ ...prev, collarStyles: { ...prev.collarStyles, [collarForm]: next } }))}
          />
        </ChartWindow>
      )}

      {(() => {
        const model = blockForm === null ? undefined : (datasets ?? []).find((d) => d.id === blockForm && d.kind === "blocks");
        if (!model) return null;
        return (
          <ChartWindow
            index={2} z={30 + topZ()} size="form" icon="gear" title={`Блочная модель · ${model.name}`}
            onFocus={() => undefined} onClose={() => setBlockForm(null)}
          >
            <BlockSettingsForm
              dataset={model} style={ws.blockStyles[model.id] ?? defaultBlockStyle(blockMeta(model))}
              onChange={(next) => setWs((prev) => ({ ...prev, blockStyles: { ...prev.blockStyles, [model.id]: next } }))}
              projectId={projectId} styles={styles} onStyles={setStyles}
              displays={sets.flatMap((d) => (d.charts ?? []).filter((c) => c.kind === "downhole").map((c) => ({ id: c.id, name: c.name, setName: d.name })))}
              say={(text, error = false) => setNotice({ text, error })}
            />
          </ChartWindow>
        );
      })()}

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
            ...(chartMenu.kind === "downhole" ? [{
              label: ws.logs.includes(chartMenu.id) ? "Скрыть со скважин (3D)" : "Показать на скважинах (3D)", icon: "striplog" as const,
              run: () => toggleLog(chartMenu.id, !ws.logs.includes(chartMenu.id)),
            }] : []),
            { label: chartMenu.kind === "downhole" ? "Настройки…" : "Открыть", icon: chartMenu.kind === "downhole" ? "gear" as const : chartMenu.kind, run: () => void guarded(() => openSavedChart(chartMenu.id)) },
            { label: "Переименовать…", icon: "edit", run: () => void guarded(async () => {
              const name = await askText({ title: "Переименовать график", value: chartMenu.name });
              if (!name || name === chartMenu.name) return;
              await patchChart(chartMenu.id, { name });
              chartNames.current.set(chartMenu.id, name);
              reload();
              return `График переименован: «${name}»`;
            }) },
            "separator",
            { label: "Удалить", icon: "trash", run: () => void guarded(async () => {
              if (!(await askConfirm({ title: "Удаление графика", message: `Удалить «${chartMenu.name}»? Данные проб не меняются.`, danger: true }))) return;
              await deleteChart(chartMenu.id);
              setWs((prev) => setLogShown(prev, chartMenu.id, false));
              setCharts((prev) => prev.filter((x) => x.chartId !== chartMenu.id));
              for (const f of forms) if (f.chartId === chartMenu.id) closeForm(f.id);
              reload();
              return `График «${chartMenu.name}» удалён`;
            }) },
          ]}
        />
      )}

      {menu && (() => {
        const target = sets.find((d) => d.id === menu.setId);
        const assays = target?.counts?.assay ?? 0;
        const noData = hasSamples(target) ? undefined : "нет опробования";
        return (
          <ContextMenu
            x={menu.x} y={menu.y} title={`Графики · ${target?.name ?? ""}`} onClose={() => setMenu(null)}
            items={[
              { label: "Гистограмма", icon: "histogram", disabledNote: noData, run: () => void runCommand("chart.histogram", { set_id: menu.setId }, "user") },
              { label: "Q-Q график", icon: "qq", disabledNote: noData, run: () => void runCommand("chart.qq", { set_id: menu.setId }, "user") },
              { label: "Дендрограмма", icon: "dendrogram", disabledNote: noData ?? (assays > 0 && (target?.components?.length ?? 0) < 2 ? "нужно 2+ поля" : undefined), run: () => void runCommand("chart.dendrogram", { set_id: menu.setId }, "user") },
              "separator",
              { label: "Таблица проб", icon: "table", disabledNote: assays > 0 ? undefined : "нет встроенных проб", run: () => { selectSet(menu.setId); navigate({ kind: "assay" }); setWs((p) => openBottom(p, "table")); } },
            ]}
          />
        );
      })()}

      {pathsMenu && (
        <ContextMenu
          x={pathsMenu.x} y={pathsMenu.y} title={`По стволам · ${sets.find((d) => d.id === pathsMenu.setId)?.name ?? ""}`} onClose={() => setPathsMenu(null)}
          items={[
            { label: "Содержания…", icon: "histogram", run: () => void guarded(() => openDisplayForm(pathsMenu.setId, "grade")) },
            { label: "Литология…", icon: "striplog", run: () => void guarded(() => openDisplayForm(pathsMenu.setId, "lith")) },
            { label: "События…", icon: "hole", run: () => void guarded(() => openDisplayForm(pathsMenu.setId, "event")) },
          ]}
        />
      )}

      {tableMenu && (
        <ContextMenu
          x={tableMenu.x} y={tableMenu.y} title={tableMenu.name} onClose={() => setTableMenu(null)}
          items={[
            { label: "Открыть таблицу", icon: "table", run: () => {
              const t = sets.find((d) => d.id === tableMenu.setId)?.interval_tables?.find((x) => x.id === tableMenu.id);
              if (t) openIntervalTable(t, tableMenu.setId);
            } },
            "separator",
            { label: "Переименовать…", icon: "edit", run: () => void guarded(async () => {
              const name = await askText({ title: "Переименовать таблицу", value: tableMenu.name });
              if (!name || name === tableMenu.name) return undefined;
              await renameIntervalTable(tableMenu.id, name);
              reload();
              return `Таблица переименована: «${name}»`;
            }) },
            { label: "Удалить таблицу", icon: "trash", run: () => void guarded(async () => {
              if (!(await askConfirm({ title: "Удаление таблицы", message: `Удалить таблицу «${tableMenu.name}» со всеми интервалами? Пробы и другие таблицы не меняются.`, danger: true }))) return undefined;
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
    const title = (name ?? (await askText({ title: "Новый проект", message: "Название нового проекта", placeholder: "например, Участок Северный" })) ?? "").trim();
    if (!title) return "Отменено";
    const project = await createProject(title);
    setProjects((prev) => [...(prev ?? []), project]);
    selectProject(project.id);
    return `Создан проект «${project.name}»`;
  }

  /** Renames a project or changes its description. */
  async function onPatchProject(id: number, patch: { name?: string; description?: string }): Promise<string> {
    const updated = await patchProject(id, patch);
    setProjects((prev) => (prev ?? []).map((p) => (p.id === id ? updated : p)));
    return patch.name !== undefined ? `Проект переименован: «${updated.name}»` : "Описание проекта сохранено";
  }

  /** A project imported from a .bfggis file: the list again; the open project may have been replaced. */
  async function onProjectImported(result: ProjectImportResult): Promise<void> {
    const list = await listProjects();
    setProjects(list);
    if (result.replaced_project_id !== null && result.replaced_project_id === projectId) selectProject(result.project_id);
  }

  /** Deletes a project with everything in it, after the user confirms; opens another one. */
  async function onDeleteProject(id: number): Promise<string> {
    const target = projects?.find((p) => p.id === id);
    if (!target) throw new Error(`Проекта с id ${id} нет`);
    if (!(await askConfirm({ title: "Удаление проекта", message: `Удалить проект «${target.name}» со всеми данными? Это нельзя отменить.`, danger: true }))) return "Отменено";
    await deleteProject(id);
    let rest = (projects ?? []).filter((p) => p.id !== id);
    if (rest.length === 0) rest = [await createProject("Основной проект")];
    setProjects(rest);
    if (id === projectId) selectProject(rest[0].id);
    return `Проект «${target.name}» удалён`;
  }

  if (error) return <main className="page"><div className="alert error">{error}</div></main>;
  const project = projects?.find((p) => p.id === projectId);
  if (!projects || !project) return <Splash note="Загрузка проектов…" />;

  return <Workspace key={project.id} projects={projects} project={project} onSelectProject={selectProject} onNewProject={onNewProject} onDemoProject={onDemoProject} onDeleteProject={onDeleteProject} onPatchProject={onPatchProject} onProjectImported={onProjectImported} />;
}

/** Startup splash, the same markup as the one in index.html (shown before React starts). */
function Splash({ note }: { note: string }) {
  return (
    <div className="splash" role="status" aria-live="polite">
      <div className="splash-card">
        <div className="splash-logo" aria-hidden="true" />
        <div className="splash-title">BedrockForge</div>
        <div className="splash-by">by Mikhail Bandurkin</div>
        <div className="splash-bar" aria-hidden="true" />
        <div className="splash-note">{note}</div>
      </div>
    </div>
  );
}
