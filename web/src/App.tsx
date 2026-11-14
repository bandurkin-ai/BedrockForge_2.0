import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  auditExportUrl, createDemoProject, createProject, createSet, deleteDataset, deleteLines, deleteWireframe, listProjects, patchLines,
  patchRaster, patchWireframe, renameDataset, runAudit, uploadLines, uploadRasters, uploadWireframes, type Dataset, type Project,
} from "./api";
import { AuditPage, DEFAULT_CONFIG } from "./AuditPage";
import { DataPage } from "./DataPage";
import { formatRoute, parseRoute, useRoute, type TableKind } from "./route";
import { AssistantPanel, type Approval } from "./shell/AssistantPanel";
import { CommandLog } from "./shell/CommandLog";
import { CommandPalette } from "./shell/CommandPalette";
import { commandTools, eventShortcut, findByToolName, validateArgs, type CommandSource, type LogEntry } from "./shell/commands";
import { DataTree } from "./shell/DataTree";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { Icon } from "./shell/icons";
import { ImportDialog } from "./shell/ImportDialog";
import { PropertiesPanel } from "./shell/PropertiesPanel";
import { buildCommands, type Live } from "./shell/registry";
import { Ribbon } from "./shell/Ribbon";
import { buildRibbon, flattenRibbon, type RibbonItem } from "./shell/ribbonConfig";
import { Splitter } from "./shell/Splitter";
import { useAssistant, type ToolOutcome } from "./shell/useAssistant";
import { useLiveState } from "./shell/useLiveState";
import { useRemoteCommands } from "./shell/useRemoteCommands";
import {
  BOTTOM_TITLES, isShown, layerKey, loadWorkspace, openBottom, resize, saveWorkspace, setPanel, setShown,
  type BottomTab, type SelectedObject, type WorkspaceState,
} from "./shell/workspace";
import { StatusBar } from "./StatusBar";
import { useOverview } from "./useOverview";
import { DEFAULT_VIZ, type VizSettings } from "./viz/ComponentPanel";
import type { SectionLine } from "./viz/geometry";
import { THEME_EVENT } from "./viz/scene3d-theme";

// three.js is large: the 3D view is a separate chunk, the shell appears first.
import type { SceneApi } from "./viz/SceneView";

const SceneView = lazy(() => import("./viz/SceneView").then((m) => ({ default: m.SceneView })));

const STORAGE_KEY = "geo.projectId";
const LOG_LIMIT = 200;
const ACCEPT = {
  wireframes: ".tridb,.dxf",
  lines: ".str,.dxf",
  rasters: ".jpg,.jpeg,.png,.tif,.tiff,.bmp,.jgw,.jpgw,.pgw,.pngw,.tfw,.tifw,.bpw,.wld,.tab,.grf",
};
type UploadKind = keyof typeof ACCEPT;

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
}: {
  projects: Project[];
  project: Project;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  onDemoProject: () => Promise<string>;
}) {
  const projectId = project.id;
  const [route, navigate] = useRoute();
  const [ws, setWs, wsRef] = useLiveState<WorkspaceState>(loadWorkspace);
  const [refreshKey, setRefreshKey] = useState(0);
  const [viz, setViz, vizRef] = useLiveState<VizSettings>(DEFAULT_VIZ);
  const [section, setSection, sectionRef] = useLiveState<SectionLine | null>(null);
  const [selected, setSelected, selectedRef] = useLiveState<SelectedObject | null>(null);
  const [palette, setPalette] = useState<string | null>(null);
  const [importTarget, setImportTarget] = useState<number | null | undefined>(undefined);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const fileInputs = { wireframes: useRef<HTMLInputElement>(null), lines: useRef<HTMLInputElement>(null), rasters: useRef<HTMLInputElement>(null) };
  const openFiles = (kind: UploadKind) => fileInputs[kind].current?.click();
  const sceneApi = useRef<SceneApi | null>(null);
  const reload = useCallback(() => setRefreshKey((k) => k + 1), []);

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
    reload, onSelectProject, onNewProject, onDemoProject,
    onCreateSet: createNewSet,
    openPalette: (query) => setPalette(query),
    openImport: (setId) => setImportTarget(setId),
    upload: openFiles,
    patchRaster: async (id, patch) => {
      await patchRaster(id, patch);
      reload();
    },
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

  async function onFiles(kind: UploadKind, list: FileList | null) {
    const files = Array.from(list ?? []);
    const input = fileInputs[kind].current;
    if (input) input.value = "";
    if (!files.length) return;
    if (kind === "rasters") {
      // Images and their georeference files go together, paired by name on the server.
      await guarded(async () => {
        setNotice({ text: `Загрузка: ${files.map((f) => f.name).join(", ")}…`, error: false });
        const created = await uploadRasters(projectId, files);
        const result = `Растров: ${created.length} (${created.map((d) => `${d.rasters?.[0]?.name}: ${d.rasters?.[0]?.georef.source}`).join("; ")})`;
        logUpload(files.map((f) => f.name).join(", "), result);
        reload();
        return result;
      });
      return;
    }
    for (const file of files) {
      await guarded(async () => {
        setNotice({ text: `Загрузка ${file.name}…`, error: false });
        let result: string;
        if (kind === "wireframes") {
          const created = await uploadWireframes(projectId, file);
          const triangles = (created.wireframes ?? []).reduce((n, w) => n + w.triangle_count, 0);
          result = `${file.name}: каркасов ${created.wireframes?.length ?? 0}, треугольников ${triangles.toLocaleString("ru-RU")}`;
        } else {
          const created = await uploadLines(projectId, file);
          const lines = (created.lines ?? []).reduce((n, l) => n + l.line_count, 0);
          result = `${file.name}: слоёв ${created.lines?.length ?? 0}, линий ${lines.toLocaleString("ru-RU")} (${created.meta.format})`;
        }
        logUpload(file.name, result);
        reload();
        return result;
      });
    }
  }

  const clearHole = () => activeSet !== null && selectHole(activeSet, null);
  const bottomTabs: BottomTab[] = ["table", "audit", "log"];
  const activeSetName = sets.find((d) => d.id === activeSet)?.name ?? null;

  return (
    <div className="app">
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
                  onDelete={onDelete}
                  onColor={onColor}
                  onDeleteItem={onDeleteItem}
                  onDemo={() => void runCommand("project.demo", {}, "user")}
                />
              </ErrorBoundary>
            </aside>
            <Splitter axis="x" sign={1} size={ws.sizes.left} label="Ширина дерева проекта" onResize={(s) => setWs((prev) => resize(prev, "left", s))} />
          </>
        )}

        <div className="center">
          {error && <div className="alert error">{error}</div>}
          <section className="viewport" aria-label="3D-вид">
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
                      {tab !== "log" && activeSetName && <span className="muted"> · {activeSetName}</span>}
                    </button>
                  ))}
                  <span className="spacer" />
                  <button className="icon" onClick={() => setWs((p) => setPanel(p, "bottom", false))} title="Закрыть панель" aria-label="Закрыть панель"><Icon name="close" size={14} /></button>
                </div>
                <div className="dock-body">
                  <ErrorBoundary key={ws.bottomTab} label={BOTTOM_TITLES[ws.bottomTab]} compact>
                    {ws.bottomTab === "log" ? (
                      <CommandLog entries={log} />
                    ) : activeSet === null ? (
                      <p className="muted small">Нет наборов скважин. Импортируйте данные: лента «Главная → Скважины».</p>
                    ) : ws.bottomTab === "table" ? (
                      <DataPage
                        setId={activeSet} refreshKey={refreshKey} kind={route.kind} holeId={selectedHole} summary={summary}
                        onKindChange={(kind) => navigate({ kind })} onClearHole={clearHole}
                      />
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
                    })}
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
        <input key={kind} ref={fileInputs[kind]} type="file" accept={ACCEPT[kind]} multiple hidden data-upload={kind} onChange={(e) => void onFiles(kind, e.target.files)} />
      ))}

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

  if (error) return <main className="page"><div className="alert error">{error}</div></main>;
  const project = projects?.find((p) => p.id === projectId);
  if (!projects || !project) return <main className="page"><p className="muted">Загрузка…</p></main>;

  return <Workspace key={project.id} projects={projects} project={project} onSelectProject={selectProject} onNewProject={onNewProject} onDemoProject={onDemoProject} />;
}
