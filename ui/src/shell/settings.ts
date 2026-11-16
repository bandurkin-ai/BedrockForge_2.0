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
}

export function normalizeSettings(raw: unknown): UserSettings {
  const r = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return { version: SETTINGS_VERSION, workspace: normalizeWorkspace(r.workspace), shortcuts: normalizeShortcuts(r.shortcuts) };
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

/** Debounced write (resizing a dock must not write the file on every pixel). */
export function saveSettings(s: UserSettings, delayMs = 400): void {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    const b = bridge();
    try {
      if (b) b.saveSettings(s);
      else localStorage.setItem(LOCAL_KEY, JSON.stringify(s));
    } catch {
      /* the layout just will not be remembered */
    }
  }, delayMs);
}
