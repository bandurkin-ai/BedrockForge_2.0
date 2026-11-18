/**
 * User settings file (ТЗ 7.0.10): theme, layout, hotkeys, tree order… Read once at start and
 * normalised; a damaged or unavailable file means defaults without remembering.
 */
import { bridge } from "../bridge";
import { normalizeShortcuts, type ShortcutOverrides } from "./shortcuts";
import { normalizeWorkspace, type WorkspaceState } from "./workspace";

export const SETTINGS_VERSION = 1;

export interface UserSettings {
  version: number;
  workspace: WorkspaceState;
  shortcuts: ShortcutOverrides;
  /** uid of the project open last: it opens at start (ТЗ 7.3). */
  lastProject: string | null;
  /** Folder of projects chosen in «Настройка» (null — Документы\\BedrockForge\\Проекты); the shell reads it. */
  projectsDir: string | null;
  /** Workers of the general job queue (ТЗ 7.6: 1 by default, plus the quick one). */
  heavyWorkers: number;
  /** ODA File Converter of «Настройка › Внешние программы»: the program or its folder (null — the core looks for it). */
  odaConverter: string | null;
}

export const ODA_PATH_MAX = 2000;

/** A path as it was typed or pasted: no spaces and quotes around it; empty, too long or with control characters — none. */
export function normalizeOdaPath(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const text = raw.trim().replace(/^"+|"+$/g, "").trim();
  return text && text.length <= ODA_PATH_MAX && !/[\u0000-\u001f\u007f]/.test(text) ? text : null;
}

export function normalizeSettings(raw: unknown): UserSettings {
  const r = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const last = typeof r.lastProject === "string" && /^[0-9a-f]{32}$/.test(r.lastProject) ? r.lastProject : null;
  const dir = typeof r.projectsDir === "string" && r.projectsDir.length < 400 ? r.projectsDir : null;
  const heavy = Number.isInteger(r.heavyWorkers) && (r.heavyWorkers as number) >= 1 && (r.heavyWorkers as number) <= 16 ? (r.heavyWorkers as number) : 1;
  return {
    version: SETTINGS_VERSION,
    workspace: normalizeWorkspace(r.workspace),
    shortcuts: normalizeShortcuts(r.shortcuts),
    lastProject: last,
    projectsDir: dir,
    heavyWorkers: heavy,
    odaConverter: normalizeOdaPath(r.odaConverter),
  };
}

const LOCAL_KEY = "bedrockforge2.settings";

export function loadSettings(): { settings: UserSettings; writable: boolean } {
  const b = bridge();
  try {
    const raw = b ? b.loadSettings() : JSON.parse(localStorage.getItem(LOCAL_KEY) ?? "null");
    if (raw === "damaged") return { settings: normalizeSettings(null), writable: false };
    return { settings: normalizeSettings(raw), writable: true };
  } catch {
    return { settings: normalizeSettings(null), writable: false };
  }
}

let timer: ReturnType<typeof setTimeout> | null = null;
let pending: (() => void) | null = null;

/** Writes a debounced change at once (window closing or reloading). */
export function flushSettings(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  const p = pending;
  pending = null;
  p?.();
}

if (typeof window !== "undefined") window.addEventListener("beforeunload", flushSettings);

/** Debounced write (resizing a dock must not write the file on every pixel). */
export function saveSettings(s: UserSettings, delayMs = 400): void {
  if (timer) clearTimeout(timer);
  pending = () => {
    const b = bridge();
    try {
      if (b) b.saveSettings(s);
      else localStorage.setItem(LOCAL_KEY, JSON.stringify(s));
    } catch {
      /* the layout just will not be remembered */
    }
  };
  timer = setTimeout(flushSettings, delayMs);
}
