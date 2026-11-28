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
  /** The path of ODA File Converter from «Настройка › Внешние программы» (null — not set). */
  odaConverter: string | null;
  setName: string | null;
  setHoles: number;
  holeId: string | null;
  /** The object chosen by `object.select` (a layer of lines, a raster, a wireframe or a DTM), or null. */
  selected: Selected | null;
  /** How many rows of the tree are chosen as a group (`groupStore`): the sections without `object.select` (Д-44 А5). */
  groupSize: number;
  /** How many changes of properties «Отменить» can take back (`propsUndo.ts`). */
  undoDepth: number;
  hasSection: boolean;
  /** The selected object is a list of blocks (a block model with no grid of parents, ADR 0030): `blocks/bind.ts`. */
  listModel: boolean;
  /** The numbers of the block models that are lists of blocks (for a command that names its own model). */
  listIds: number[];
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
  odaConverter: null,
  setName: null,
  setHoles: 0,
  holeId: null,
  selected: null,
  groupSize: 0,
  undoDepth: 0,
  hasSection: false,
  listModel: false,
  listIds: [],
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
    hasGroup: s.groupSize > 1,
    canUndo: s.undoDepth > 0,
    hasSection: s.hasSection,
    listModel: s.listModel,
    listIds: s.listIds,
    editing: s.editing,
    coreOnline: s.core === "online",
  };
}
