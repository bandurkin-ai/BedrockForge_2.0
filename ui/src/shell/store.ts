/**
 * Application state outside React (pure, testable): workspace layout, project view settings,
 * core status, selection. React reads it with useSyncExternalStore (useStore.ts).
 */
import type { CommandContext } from "./commands";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE, type ViewState, type WorkspaceState } from "./workspace";
import type { ShortcutOverrides } from "./shortcuts";

export type CoreStatus = "starting" | "online" | "offline";

export interface AppState {
  workspace: WorkspaceState;
  view: ViewState;
  shortcuts: ShortcutOverrides;
  core: CoreStatus;
  coreVersion: string | null;
  projectName: string | null;
  setName: string | null;
  setHoles: number;
  holeId: string | null;
  hasSection: boolean;
  editing: boolean;
  /** Modal UI (palette, import wizard) is open: ribbon hotkeys are off (ТЗ 7.0.6). */
  modal: boolean;
}

export const INITIAL_STATE: AppState = {
  workspace: DEFAULT_WORKSPACE,
  view: DEFAULT_VIEW,
  shortcuts: {},
  core: "starting",
  coreVersion: null,
  projectName: null,
  setName: null,
  setHoles: 0,
  holeId: null,
  hasSection: false,
  editing: false,
  modal: false,
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
    hasSection: s.hasSection,
    editing: s.editing,
    coreOnline: s.core === "online",
  };
}
