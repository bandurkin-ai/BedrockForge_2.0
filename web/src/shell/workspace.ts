export type Theme = "dark" | "light";
export type PanelId = "project" | "properties" | "assistant" | "bottom";
export type RightTab = "properties" | "assistant";
export type BottomTab = "table" | "audit" | "stats" | "jobs" | "log";

/** An object picked in the tree: its properties are shown on the right. */
export type ObjectKind = "wireframe" | "lines" | "raster" | "blocks";
export interface SelectedObject {
  kind: ObjectKind;
  id: number;
}

/** Camera presets of the single 3D window. They only turn the view; nothing is a separate mode. */
export type ViewPreset = "top" | "front" | "side" | "iso" | "section";
export const VIEW_TITLES: Record<ViewPreset, string> = {
  top: "В план",
  front: "Сбоку (Z/X)",
  side: "Сбоку (Z/Y)",
  iso: "Изометрия",
  section: "В плоскость разреза",
};
export const VIEW_PRESETS = Object.keys(VIEW_TITLES) as ViewPreset[];
export const BOTTOM_TITLES: Record<BottomTab, string> = { table: "Таблица", audit: "Аудит", stats: "Статистика", jobs: "Задания", log: "Журнал команд" };

export interface WorkspaceState {
  /** Clip the scene to the window of the section line. */
  clip: boolean;
  /** Perspective projection; off = orthographic, as in plans and sections. */
  perspective: boolean;
  left: boolean;
  right: boolean;
  bottom: boolean;
  rightTab: RightTab;
  bottomTab: BottomTab;
  ribbonTab: string;
  ribbonCollapsed: boolean;
  theme: Theme;
  sizes: { left: number; right: number; bottom: number };
  /** Vertical exaggeration of the 3D and section views. */
  vexag: number;
  grid: boolean;
  /** Opacity of wireframes, 0.1–1. */
  wireframeOpacity: number;
  /** Hidden layers: "set:3", "set:3:intervals", "wf:12", "ln:4", "rs:2"... Everything else is shown. */
  hidden: string[];
}

export const DEFAULT_WORKSPACE: WorkspaceState = {
  clip: false,
  perspective: false,
  left: true,
  right: true,
  bottom: false,
  rightTab: "properties",
  bottomTab: "table",
  ribbonTab: "home",
  ribbonCollapsed: false,
  theme: "dark",
  sizes: { left: 280, right: 340, bottom: 280 },
  vexag: 1,
  grid: true,
  wireframeOpacity: 0.7,
  hidden: [],
};

export const SIZE_LIMITS = {
  left: [200, 520],
  right: [260, 640],
  bottom: [120, 640],
} as const;

export const VEXAG_STEPS = [1, 2, 3, 5];

/** Layer keys used in `hidden`. */
export const layerKey = {
  set: (id: number) => `set:${id}`,
  setPart: (id: number, part: "paths" | "intervals" | "collars") => `set:${id}:${part}`,
  wireframe: (id: number) => `wf:${id}`,
  lines: (id: number) => `ln:${id}`,
  raster: (id: number) => `rs:${id}`,
  blocks: (id: number) => `bm:${id}`,
};

export function isShown(state: WorkspaceState, key: string): boolean {
  return !state.hidden.includes(key);
}

export function setShown(state: WorkspaceState, key: string, shown: boolean): WorkspaceState {
  const hidden = state.hidden.filter((k) => k !== key);
  if (!shown) hidden.push(key);
  return { ...state, hidden };
}

/** Open or close a panel; `open` omitted toggles it. The assistant and properties share the right dock. */
export function setPanel(state: WorkspaceState, panel: PanelId, open?: boolean): WorkspaceState {
  switch (panel) {
    case "project":
      return { ...state, left: open ?? !state.left };
    case "bottom":
      return { ...state, bottom: open ?? !state.bottom };
    case "properties":
    case "assistant": {
      const shown = state.right && state.rightTab === panel;
      const next = open ?? !shown;
      return next ? { ...state, right: true, rightTab: panel } : shown ? { ...state, right: false } : state;
    }
  }
}

/** Show a tab of the bottom dock (opens the dock). */
export function openBottom(state: WorkspaceState, tab: BottomTab): WorkspaceState {
  return { ...state, bottom: true, bottomTab: tab };
}

export function resize(state: WorkspaceState, dock: keyof WorkspaceState["sizes"], size: number): WorkspaceState {
  const [min, max] = SIZE_LIMITS[dock];
  return { ...state, sizes: { ...state.sizes, [dock]: Math.round(Math.min(max, Math.max(min, size))) } };
}

/** Stored state may come from an older version: keep only values that are still valid. */
export function normalize(raw: unknown): WorkspaceState {
  if (!raw || typeof raw !== "object") return DEFAULT_WORKSPACE;
  const r = raw as Partial<WorkspaceState>;
  const d = DEFAULT_WORKSPACE;
  const bool = (value: unknown, fallback: boolean) => (typeof value === "boolean" ? value : fallback);
  let state: WorkspaceState = {
    clip: bool(r.clip, d.clip),
    perspective: bool(r.perspective, d.perspective),
    left: bool(r.left, d.left),
    right: bool(r.right, d.right),
    bottom: bool(r.bottom, d.bottom),
    rightTab: r.rightTab === "assistant" ? "assistant" : "properties",
    bottomTab: r.bottomTab && r.bottomTab in BOTTOM_TITLES ? r.bottomTab : d.bottomTab,
    ribbonTab: typeof r.ribbonTab === "string" ? r.ribbonTab : d.ribbonTab,
    ribbonCollapsed: bool(r.ribbonCollapsed, d.ribbonCollapsed),
    theme: r.theme === "light" ? "light" : "dark",
    sizes: d.sizes,
    vexag: VEXAG_STEPS.includes(r.vexag as number) ? (r.vexag as number) : d.vexag,
    grid: bool(r.grid, d.grid),
    wireframeOpacity:
      typeof r.wireframeOpacity === "number" && r.wireframeOpacity >= 0.1 && r.wireframeOpacity <= 1 ? r.wireframeOpacity : d.wireframeOpacity,
    hidden: Array.isArray(r.hidden) ? r.hidden.filter((k): k is string => typeof k === "string").slice(0, 2000) : [],
  };
  for (const dock of ["left", "right", "bottom"] as const) {
    const size = r.sizes?.[dock];
    if (typeof size === "number" && Number.isFinite(size)) state = resize(state, dock, size);
  }
  return state;
}

const STORAGE_KEY = "bedrockforge.workspace.v3";

export function loadWorkspace(): WorkspaceState {
  try {
    return normalize(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null"));
  } catch {
    return DEFAULT_WORKSPACE;
  }
}

export function saveWorkspace(state: WorkspaceState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage may be unavailable; the layout just will not be remembered */
  }
}
