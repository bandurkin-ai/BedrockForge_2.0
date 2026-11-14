import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { auditExportUrl, createProject, listProjects, runAudit, type Project } from "./api";
import { AuditPage, DEFAULT_CONFIG } from "./AuditPage";
import { DataPage } from "./DataPage";
import { ImportPage } from "./ImportPage";
import { formatRoute, parseRoute, useRoute, type TableKind } from "./route";
import { AssistantPanel, type Approval } from "./shell/AssistantPanel";
import { CommandLog } from "./shell/CommandLog";
import { CommandPalette } from "./shell/CommandPalette";
import { commandTools, eventShortcut, findByToolName, validateArgs, type CommandSource, type LogEntry } from "./shell/commands";
import { Icon } from "./shell/icons";
import { ProjectTree } from "./shell/ProjectTree";
import { PropertiesPanel } from "./shell/PropertiesPanel";
import { buildRibbon, flattenRibbon, type RibbonItem } from "./shell/ribbon";
import { Ribbon } from "./shell/Ribbon";
import { buildCommands, type Live } from "./shell/registry";
import { Splitter } from "./shell/Splitter";
import { useAssistant, type ToolOutcome } from "./shell/useAssistant";
import { useLiveState } from "./shell/useLiveState";
import { useRemoteCommands } from "./shell/useRemoteCommands";
import { ViewportGrid } from "./shell/ViewportGrid";
import {
  loadWorkspace, PANE_COUNT, resize, saveWorkspace, setLayout, setPane, setPanel, VIEW_TITLES,
  type ViewKind, type WorkspaceState,
} from "./shell/workspace";
import { StatusBar } from "./StatusBar";
import { useOverview } from "./useOverview";
import { DEFAULT_VIZ, type VizSettings } from "./viz/ComponentPanel";
import type { SectionLine } from "./viz/geometry";
import { THEME_EVENT } from "./viz/scene3d-theme";
import { VizTab } from "./viz/VizTab";

const STORAGE_KEY = "geo.projectId";
const LOG_LIMIT = 200;

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
}: {
  projects: Project[];
  project: Project;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
}) {
  const projectId = project.id;
  const [route, navigate] = useRoute();
  const [ws, setWs, wsRef] = useLiveState<WorkspaceState>(loadWorkspace);
  const [refreshKey, setRefreshKey] = useState(0);
  const [viz, setViz, vizRef] = useLiveState<VizSettings>(DEFAULT_VIZ);
  const [section, setSection, sectionRef] = useLiveState<SectionLine | null>(null);
  const [palette, setPalette] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const { summary, holes, audit, error } = useOverview(projectId, refreshKey);
  const reload = useCallback(() => setRefreshKey((k) => k + 1), []);

  // The selected hole lives in the URL, so it survives reload and back/forward.
  const selectedHole = route.hole;
  const setSelectedHole = useCallback((hole: string | null) => navigate({ hole }, { replace: true }), [navigate]);

  // Drop the selection if that hole no longer exists (e.g. after re-importing collars).
  useEffect(() => {
    if (selectedHole && holes.length > 0 && !holes.some((h) => h.hole_id === selectedHole)) {
      navigate({ hole: null }, { replace: true });
    }
  }, [holes, selectedHole, navigate]);

  useEffect(() => saveWorkspace(ws), [ws]);
  useEffect(() => {
    document.documentElement.dataset.theme = ws.theme;
    window.dispatchEvent(new Event(THEME_EVENT));
  }, [ws.theme]);

  // The URL addresses the active pane. Without a hash the remembered view is shown;
  // otherwise the pane follows the URL (links, back/forward).
  const [hashReady, setHashReady] = useState(() => window.location.hash.length > 1);
  useEffect(() => {
    if (!hashReady) {
      navigate({ tab: ws.panes[ws.active] }, { replace: true });
      setHashReady(true);
    } else {
      setWs((prev) => setPane(prev, prev.active, route.tab));
    }
  }, [route.tab, hashReady]); // eslint-disable-line react-hooks/exhaustive-deps

  const activatePane = useCallback(
    (index: number) => {
      setWs((prev) => (index < PANE_COUNT[prev.layout] ? { ...prev, active: index } : prev));
      navigate({ tab: wsRef.current.panes[index] }, { replace: true });
    },
    [navigate, setWs, wsRef],
  );

  const openView = useCallback(
    (view: ViewKind, pane?: number) => {
      if (pane === undefined || pane === wsRef.current.active) navigate({ tab: view });
      else setWs((prev) => setPane(prev, pane, view));
    },
    [navigate, setWs, wsRef],
  );

  const assistant = useAssistant(projectId, {
    tools: () => commandTools(commands),
    context: () => ({
      project: { id: project.id, name: project.name },
      counts: summary?.counts ?? null,
      components: summary?.components ?? [],
      selected_hole: selectedHole,
      layout: ws.layout,
      windows: ws.panes.slice(0, PANE_COUNT[ws.layout]).map((view, i) => ({ window: i + 1, view, active: i === ws.active })),
      color_scale: viz,
      section,
      audit: audit ? { ...audit.by_severity, stale: audit.stale } : null,
    }),
    execute: (name, input) => executeTool(name, input),
  });

  // Commands read the latest state through this ref, so the registry itself never changes.
  const live = useRef<Live>(null!);
  live.current = {
    projectId, projects, holes, summary, selectedHole,
    get ws() { return wsRef.current; },
    get viz() { return vizRef.current; },
    get section() { return sectionRef.current; },
    setWs, setViz, setSection, setSelectedHole, openView, navigate, reload, onSelectProject, onNewProject,
    openPalette: (query) => setPalette(query),
    runAudit: () => runAudit(projectId, DEFAULT_CONFIG),
    exportAudit: () => window.open(auditExportUrl(projectId), "_blank"),
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
        const allowed = await new Promise<boolean>((resolve) =>
          setApproval({ title: command.title, detail: command.description, resolve }),
        );
        setApproval(null);
        if (!allowed) return { title: command.title, content: "Пользователь отклонил действие", ok: false };
      }
      return runCommand(command.id, args, source);
    },
    [commands, runCommand],
  );
  const executeTool = (name: string, input: Record<string, unknown>) => executeExternal(null, name, input, "claude");

  const remote = useRemoteCommands(projectId, commands, (id, args) => executeExternal(id, null, args, "remote"));

  const tabs = useMemo(
    () => buildRibbon(ws, { hasHole: selectedHole !== null, hasData: holes.length > 0 }),
    [ws, selectedHole, holes.length],
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
      if (palette !== null) return;
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
  }, [items, palette, runItem]);

  const openTable = (kind: TableKind) => void runCommand("table.open", { kind }, "user");
  const clearHole = () => setSelectedHole(null);

  const renderView = (view: ViewKind) => {
    switch (view) {
      case "import":
        return <ImportPage key={projectId} projectId={projectId} onImported={reload} />;
      case "table":
        return (
          <DataPage
            projectId={projectId} refreshKey={refreshKey} kind={route.kind} holeId={selectedHole} summary={summary}
            onKindChange={(kind) => navigate({ kind })} onClearHole={clearHole}
          />
        );
      case "audit":
        return <AuditPage key={projectId} projectId={projectId} refreshKey={refreshKey} holeId={selectedHole} onAudited={reload} onClearHole={clearHole} />;
      default:
        return (
          <VizTab
            kind={view} projectId={projectId} refreshKey={refreshKey} summary={summary} holes={holes}
            selectedHole={selectedHole} settings={viz} section={section}
            onSelectHole={setSelectedHole} onSection={setSection}
            onOpenSection={() => {
              const shown = ws.panes.slice(0, PANE_COUNT[ws.layout]).includes("section");
              if (!shown) openView("section");
            }}
          />
        );
    }
  };

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
            <aside className="dock dock-left" style={{ width: ws.sizes.left }} aria-label="Проект">
              <ProjectTree
                summary={summary} holes={holes} selectedHole={selectedHole}
                onOpenTable={openTable} onSelectHole={setSelectedHole} onImport={() => openView("import")}
              />
            </aside>
            <Splitter axis="x" sign={1} size={ws.sizes.left} label="Ширина панели проекта" onResize={(s) => setWs((prev) => resize(prev, "left", s))} />
          </>
        )}

        <div className="center">
          {error && <div className="alert error">{error}</div>}
          <ViewportGrid
            layout={ws.layout}
            panes={ws.panes}
            active={ws.active}
            renderView={renderView}
            onActivate={activatePane}
            onChangeView={(index, view) => openView(view, index)}
            onMaximize={(index) => {
              const view = ws.panes[index];
              setWs((prev) => ({ ...setPane(setLayout(prev, "single"), 0, view), active: 0 }));
              navigate({ tab: view }, { replace: true });
            }}
          />
          {ws.bottom && (
            <>
              <Splitter axis="y" sign={-1} size={ws.sizes.bottom} label="Высота нижней панели" onResize={(s) => setWs((prev) => resize(prev, "bottom", s))} />
              <section className="dock dock-bottom" style={{ height: ws.sizes.bottom }} aria-label="Нижняя панель">
                <div className="dock-tabs" role="tablist">
                  <button role="tab" aria-selected={ws.bottomTab === "table"} className={ws.bottomTab === "table" ? "active" : ""} onClick={() => setWs((p) => ({ ...p, bottomTab: "table" }))}>Таблица</button>
                  <button role="tab" aria-selected={ws.bottomTab === "log"} className={ws.bottomTab === "log" ? "active" : ""} onClick={() => setWs((p) => ({ ...p, bottomTab: "log" }))}>
                    Журнал команд{log.length > 0 && ` · ${log.length}`}
                  </button>
                  <span className="spacer" />
                  <button className="icon" onClick={() => setWs((p) => setPanel(p, "bottom", false))} title="Закрыть панель" aria-label="Закрыть панель"><Icon name="close" size={14} /></button>
                </div>
                <div className="dock-body">
                  {ws.bottomTab === "table" ? (
                    <DataPage
                      projectId={projectId} refreshKey={refreshKey} kind={route.kind} holeId={selectedHole} summary={summary}
                      onKindChange={(kind) => navigate({ kind })} onClearHole={clearHole}
                    />
                  ) : (
                    <CommandLog entries={log} />
                  )}
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
                <PropertiesPanel
                  projectId={projectId} projectName={project.name} summary={summary} audit={audit}
                  selectedHole={selectedHole} refreshKey={refreshKey} viz={viz} onViz={setViz}
                  onOpenTable={openTable} onClearHole={clearHole}
                />
              </div>
              <div className="dock-body flush" hidden={ws.rightTab !== "assistant"}>
                <AssistantPanel
                  items={assistant.items} busy={assistant.busy} status={assistant.status} approval={approval}
                  hasHole={selectedHole !== null}
                  context={[
                    project.name,
                    selectedHole ? `Скважина ${selectedHole}` : "Скважина не выбрана",
                    ws.panes.slice(0, PANE_COUNT[ws.layout]).map((v) => VIEW_TITLES[v]).join(" + "),
                  ]}
                  onSend={(text) => void assistant.send(text)} onStop={assistant.stop} onReset={assistant.reset}
                />
              </div>
            </aside>
          </>
        )}
      </div>

      <StatusBar
        projectName={project.name} holeCount={holes.length} selectedHole={selectedHole} audit={audit}
        activeView={VIEW_TITLES[ws.panes[ws.active]]} assistant={assistant.status} remote={remote}
      />

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
    // A hole belongs to one project: drop it from the URL before the workspace is rebuilt.
    window.history.replaceState(null, "", formatRoute({ ...parseRoute(window.location.hash), hole: null }));
    setProjectId(id);
    saveProjectId(id);
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

  return <Workspace key={project.id} projects={projects} project={project} onSelectProject={selectProject} onNewProject={onNewProject} />;
}
