import { useEffect, useState } from "react";
import { createProject, listProjects, type Project } from "./api";
import { AuditPage } from "./AuditPage";
import { DataPage } from "./DataPage";
import { ImportPage } from "./ImportPage";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { useOverview } from "./useOverview";
import { DEFAULT_VIZ, type VizSettings } from "./viz/ComponentPanel";
import type { SectionLine } from "./viz/geometry";
import { VizTab } from "./viz/VizTab";
import { formatRoute, parseRoute, useRoute, type Tab, type TableKind } from "./route";

const STORAGE_KEY = "geo.projectId";

const TABS: { tab: Tab; label: string }[] = [
  { tab: "table", label: "Таблица" },
  { tab: "plan", label: "План" },
  { tab: "section", label: "Разрез" },
  { tab: "log", label: "Колонка" },
  { tab: "view3d", label: "3D" },
  { tab: "audit", label: "Аудит" },
  { tab: "import", label: "Импорт" },
];

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

function Workspace({
  projects,
  project,
  onSelectProject,
  onNewProject,
}: {
  projects: Project[];
  project: Project;
  onSelectProject: (id: number) => void;
  onNewProject: () => void;
}) {
  const projectId = project.id;
  const [route, navigate] = useRoute();
  const [refreshKey, setRefreshKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [viz, setViz] = useState<VizSettings>(DEFAULT_VIZ);
  const [section, setSection] = useState<SectionLine | null>(null);
  const { summary, holes, audit, error } = useOverview(projectId, refreshKey);
  const reload = () => setRefreshKey((k) => k + 1);

  // The selected hole lives in the URL, so it survives reload and back/forward.
  const selectedHole = route.hole;
  const setSelectedHole = (hole: string | null) => navigate({ hole }, { replace: true });

  // Drop the selection if that hole no longer exists (e.g. after re-importing collars).
  useEffect(() => {
    if (selectedHole && holes.length > 0 && !holes.some((h) => h.hole_id === selectedHole)) {
      navigate({ hole: null }, { replace: true });
    }
  }, [holes, selectedHole, navigate]);

  const openTable = (kind: TableKind) => {
    navigate({ tab: "table", kind });
    setMenuOpen(false);
  };

  return (
    <div className="app">
      <header className="topbar">
        <button
          className="icon menu-button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-label="Меню проекта"
          aria-expanded={menuOpen}
        >
          ☰
        </button>
        <strong className="brand">Геология</strong>
        <label>
          Проект:{" "}
          <select value={projectId} onChange={(e) => onSelectProject(Number(e.target.value))}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <button onClick={onNewProject}>+ Новый проект</button>
      </header>

      <Sidebar
        open={menuOpen}
        projectId={projectId}
        summary={summary}
        holes={holes}
        route={route}
        selectedHole={selectedHole}
        refreshKey={refreshKey}
        onOpenTable={openTable}
        onSelectHole={setSelectedHole}
      />
      {menuOpen && <div className="backdrop" onClick={() => setMenuOpen(false)} />}

      <div className="workspace">
        <nav className="tabstrip" role="tablist" aria-label="Разделы">
          {TABS.map(({ tab, label }) => (
            <button
              key={tab}
              role="tab"
              aria-selected={route.tab === tab}
              className={route.tab === tab ? "tab active" : "tab"}
              onClick={() => navigate({ tab })}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="content">
          {error && <div className="alert error">{error}</div>}
          {route.tab === "import" && <ImportPage key={projectId} projectId={projectId} onImported={reload} />}
          {route.tab === "table" && (
            <DataPage
              projectId={projectId}
              refreshKey={refreshKey}
              kind={route.kind}
              holeId={selectedHole}
              summary={summary}
              onKindChange={(kind) => navigate({ kind })}
              onClearHole={() => setSelectedHole(null)}
            />
          )}
          {route.tab === "audit" && (
            <AuditPage
              key={projectId}
              projectId={projectId}
              holeId={selectedHole}
              onAudited={reload}
              onClearHole={() => setSelectedHole(null)}
            />
          )}
          {(route.tab === "plan" || route.tab === "section" || route.tab === "log" || route.tab === "view3d") && (
            <VizTab
              kind={route.tab}
              projectId={projectId}
              refreshKey={refreshKey}
              summary={summary}
              holes={holes}
              selectedHole={selectedHole}
              settings={viz}
              section={section}
              onSettings={setViz}
              onSelectHole={setSelectedHole}
              onSection={setSection}
              onOpenSection={() => navigate({ tab: "section" })}
            />
          )}
        </div>
      </div>

      <StatusBar projectName={project.name} holeCount={holes.length} selectedHole={selectedHole} audit={audit} />
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

  async function onNewProject() {
    const name = window.prompt("Название нового проекта")?.trim();
    if (!name) return;
    try {
      const project = await createProject(name);
      setProjects((prev) => [...(prev ?? []), project]);
      selectProject(project.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <main className="page"><div className="alert error">{error}</div></main>;
  const project = projects?.find((p) => p.id === projectId);
  if (!projects || !project) return <main className="page"><p className="muted">Загрузка…</p></main>;

  return <Workspace key={project.id} projects={projects} project={project} onSelectProject={selectProject} onNewProject={onNewProject} />;
}
