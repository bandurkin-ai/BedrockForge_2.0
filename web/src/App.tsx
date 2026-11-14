import { useEffect, useState } from "react";
import { createProject, listProjects, type Project } from "./api";
import { AuditPage } from "./AuditPage";
import { DataPage } from "./DataPage";
import { ImportPage } from "./ImportPage";

// Temporary shell: the full layout (project tree, Plan / Section / 3D tabs) comes in stage 1.
type Tab = "import" | "data" | "audit";

const STORAGE_KEY = "geo.projectId";

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

export function App() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("import");
  const [refreshKey, setRefreshKey] = useState(0);
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

  function selectProject(id: number) {
    setProjectId(id);
    saveProjectId(id);
  }

  if (error) return <main className="page"><div className="alert error">{error}</div></main>;
  if (!projects || projectId === null) return <main className="page"><p className="muted">Загрузка…</p></main>;

  return (
    <>
      <header className="topbar">
        <strong>Геология</strong>
        <label>
          Проект:{" "}
          <select value={projectId} onChange={(e) => selectProject(Number(e.target.value))}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <button onClick={onNewProject}>+ Новый проект</button>
        <nav className="tabs inline">
          <button className={tab === "import" ? "tab active" : "tab"} onClick={() => setTab("import")}>Импорт</button>
          <button className={tab === "data" ? "tab active" : "tab"} onClick={() => setTab("data")}>Данные</button>
          <button className={tab === "audit" ? "tab active" : "tab"} onClick={() => setTab("audit")}>Аудит</button>
        </nav>
      </header>
      {tab === "import" ? (
        // key resets the wizard when the project changes
        <ImportPage key={projectId} projectId={projectId} onImported={() => setRefreshKey((k) => k + 1)} />
      ) : tab === "data" ? (
        <DataPage projectId={projectId} refreshKey={refreshKey} />
      ) : (
        <AuditPage key={projectId} projectId={projectId} />
      )}
    </>
  );
}
