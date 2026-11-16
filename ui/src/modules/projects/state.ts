/** The open project and the project list as the interface sees them (ТЗ 7.3). No React here. */
import { api, CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import { Store } from "../../shell/store";
import { refresh as refreshJobs } from "../jobs/state";

export interface Description {
  deposit: string;
  license: string;
  stage: string;
}

export type Summary = Record<string, number>;

export interface ProjectInfo {
  uid: string;
  name: string;
  description: Description;
  created_at: string;
  updated_at: string;
  version: number;
  summary: Summary;
  path: string;
  read_only: boolean;
  lock_owner: Record<string, unknown>;
}

export interface ProjectEntry {
  uid: string;
  name: string;
  description: Partial<Description>;
  summary: Summary;
  created_at?: string;
  updated_at?: string;
  path: string;
  open: boolean;
  damaged?: boolean;
  outdated?: boolean;
}

/** Composition rows of «Сводка состава» (ТЗ 7.3) in the order of the window. */
export const SUMMARY_TITLES: [string, string][] = [
  ["sets", "Наборы скважин"],
  ["holes", "Скважины"],
  ["surveys", "Замеры"],
  ["assays", "Пробы"],
  ["interval_rows", "Интервалы таблиц"],
  ["wireframes", "Каркасы"],
  ["lines", "Линии"],
  ["rasters", "Растры"],
  ["block_models", "Блочные модели"],
  ["charts", "Графики"],
  ["chats", "Диалоги Claude"],
];

export const projectStore = new Store<{ current: ProjectInfo | null; version: number }>({ current: null, version: 0 });

/** Objects of a list row: everything in the composition except holes and their tables. */
export function objectCount(s: Summary): number {
  return ["wireframes", "lines", "rasters", "block_models", "charts"].reduce((n, k) => n + (s[k] ?? 0), 0);
}

export function setCurrent(app: App, info: ProjectInfo | null): void {
  const changed = projectStore.get().current?.uid !== info?.uid;
  projectStore.set((s) => ({ current: info, version: s.version + 1 }));
  if (changed) void refreshJobs(); // «Задания» show the jobs of the open project
  app.store.set((s) => ({
    projectName: info?.name ?? null,
    projectReadOnly: info?.read_only ?? false,
    lastProject: info ? info.uid : s.lastProject,
  }));
}

/** Something changed in the list (created, renamed, deleted, imported): windows reload. */
export function touch(): void {
  projectStore.set((s) => ({ version: s.version + 1 }));
}

export async function refreshCurrent(app: App): Promise<ProjectInfo | null> {
  const r = await api.get<{ project: ProjectInfo | null }>("/projects/current");
  setCurrent(app, r.project);
  return r.project;
}

/** At start: the last project opens (ТЗ 7.3); a deleted one is forgotten. */
export async function restoreLast(app: App): Promise<void> {
  const cur = await refreshCurrent(app);
  const last = app.store.get().lastProject;
  if (cur || !last) return;
  try {
    setCurrent(app, await api.post<ProjectInfo>(`/projects/${last}/open`));
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) app.store.set({ lastProject: null });
    else throw e;
  }
}

export async function listProjects(q = ""): Promise<{ root: string; projects: ProjectEntry[] }> {
  return api.get(`/projects${q ? `?q=${encodeURIComponent(q)}` : ""}`);
}

export async function openProject(app: App, uid: string): Promise<ProjectInfo> {
  const info = await api.post<ProjectInfo>(`/projects/${uid}/open`);
  setCurrent(app, info);
  return info;
}

export async function createProject(app: App, name: string): Promise<ProjectInfo> {
  const info = await api.post<ProjectInfo>("/projects", { name });
  setCurrent(app, info);
  touch();
  return info;
}

export async function updateProject(app: App, uid: string, patch: { name?: string; description?: Description; expected_version?: number }): Promise<ProjectInfo> {
  const info = await api.patch<ProjectInfo>(`/projects/${uid}`, patch);
  setCurrent(app, info);
  touch();
  return info;
}

export async function deleteProject(app: App, uid: string): Promise<void> {
  await api.del(`/projects/${uid}`);
  if (projectStore.get().current?.uid === uid) setCurrent(app, null);
  if (app.store.get().lastProject === uid) app.store.set({ lastProject: null });
  touch();
}

export async function demoProject(app: App): Promise<ProjectInfo & { created: boolean }> {
  const info = await api.post<ProjectInfo & { created: boolean }>("/projects/demo");
  setCurrent(app, info);
  touch();
  return info;
}

/** Default file name of an export: «<Название>_ГГГГ-ММ-ДД.bfggis» (ТЗ 7.4). */
export function exportFileName(name: string, now = new Date()): string {
  const safe = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim() || "Проект";
  const d = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return `${safe}_${d}.bfggis`;
}

export function formatDate(iso: string | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}
