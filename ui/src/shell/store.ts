/**
 * Application state outside React (pure, testable): workspace layout, project view settings,
 * core status, selection. React reads it with useSyncExternalStore (useStore.ts).
 */
import type { CommandContext } from "./commands";
import type { Selected } from "./selection";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE, type ViewState, type WorkspaceState } from "./workspace";
import type { ShortcutOverrides } from "./shortcuts";

export type CoreStatus = "starting" | "online" | "offline";

export interface AppState {
  workspace: WorkspaceState;
  view: ViewState;
  shortcuts: ShortcutOverrides;
  core: CoreStatus;
  coreVersion: string | null;
  /** Reason shown while the core is offline, or the last notice («перезапущено»). */
  coreMessage: string | null;
  projectName: string | null;
  /** The open project is read-only (open in another window, ADR 0002 §5). */
  projectReadOnly: boolean;
  lastProject: string | null;
  projectsDir: string | null;
  heavyWorkers: number;
  setName: string | null;
  setHoles: number;
  holeId: string | null;
  /** The object chosen by `object.select` (a layer of lines, a raster, a wireframe or a DTM), or null. */
  selected: Selected | null;
  hasSection: boolean;
  editing: boolean;
  /** A passing notice of the view (settings not saved, «упрощено»…). */
  statusNote: string | null;
  /** Number of open modal UIs (shortcut capture, import wizard): ribbon hotkeys are off (ТЗ 7.0.6). */
  modal: number;
}

export const INITIAL_STATE: AppState = {
  workspace: DEFAULT_WORKSPACE,
  view: DEFAULT_VIEW,
  shortcuts: {},
  core: "starting",
  coreVersion: null,
  coreMessage: null,
  projectName: null,
  projectReadOnly: false,
  lastProject: null,
  projectsDir: null,
  heavyWorkers: 1,
  setName: null,
  setHoles: 0,
  holeId: null,
  selected: null,
  hasSection: false,
  editing: false,
  statusNote: null,
  modal: 0,
};

export class Store<T> {
  private listeners = new Set<() => void>();
  constructor(private state: T) {}

  get = (): T => this.state;

  set(patch: Partial<T> | ((s: T) => Partial<T>)): void {
    const p = typeof patch === "function" ? patch(this.state) : patch;
    this.state = { ...this.state, ...p };
    for (const fn of this.listeners) fn();
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
}

export function commandContext(s: AppState): CommandContext {
  return {
    hasProject: s.projectName !== null,
    hasSet: s.setName !== null,
    hasHole: s.holeId !== null,
    hasObject: s.selected !== null,
    hasSection: s.hasSection,
    editing: s.editing,
    coreOnline: s.core === "online",
  };
}
