/**
 * Window layout and user preferences kept in the user settings file (ТЗ 7.0.10).
 * Project view settings (clip, perspective, vexag, hidden layers…) live in the project (Э2) — ViewState.
 * Pure module; storage goes through settings.ts.
 */

export type Theme = "dark" | "light";
export type PanelId = "project" | "properties" | "assistant" | "bottom";
export type RightTab = "properties" | "assistant";
export type BottomTab = "table" | "audit" | "stats" | "swath" | "jobs" | "log";
export type TreeSection = "views" | "series" | "holes" | "wireframes" | "dtm" | "lines" | "rasters" | "blocks" | "audit";

export const BOTTOM_TITLES: Record<BottomTab, string> = {
  table: "Таблица",
  audit: "Аудит и статистика",
  stats: "Статистика",
  swath: "Swath plot",
  jobs: "Задания",
  log: "Журнал команд",
};

export const TREE_SECTIONS: Record<TreeSection, string> = {
  views: "Виды",
  series: "Серии разрезов",
  holes: "Скважины",
  wireframes: "Каркасы",
  dtm: "ЦТМ",
  lines: "Линии",
  rasters: "Растры",
  blocks: "Блочные модели",
  audit: "Аудит и статистика",
};
export const DEFAULT_TREE_ORDER = Object.keys(TREE_SECTIONS) as TreeSection[];

export interface WorkspaceState {
  left: boolean;
  right: boolean;
  bottom: boolean;
  rightTab: RightTab;
  bottomTab: BottomTab;
  ribbonTab: string;
  ribbonCollapsed: boolean;
  theme: Theme;
  sizes: { left: number; right: number; bottom: number };
  treeOrder: TreeSection[];
  treeCollapsed: TreeSection[];
  sectionMiniPlan: boolean;
}

export const DEFAULT_WORKSPACE: WorkspaceState = {
  left: true,
  right: true,
  bottom: false,
  rightTab: "properties",
  bottomTab: "table",
  ribbonTab: "home",
  ribbonCollapsed: false,
  theme: "dark",
  sizes: { left: 280, right: 340, bottom: 280 },
  treeOrder: DEFAULT_TREE_ORDER,
  treeCollapsed: [],
  sectionMiniPlan: true,
};

export const SIZE_LIMITS = {
  left: [200, 520],
  right: [260, 640],
  bottom: [120, 640],
} as const;

/** Settings of the 3D view stored per project (ТЗ 7.0.10, second row). */
export interface ViewState {
  clip: boolean;
  perspective: boolean;
  vexag: number;
  grid: boolean;
  wireframeOpacity: number;
  hidden: string[];
}

export const DEFAULT_VIEW: ViewState = { clip: false, perspective: false, vexag: 1, grid: true, wireframeOpacity: 0.7, hidden: [] };
export const VEXAG_STEPS = [1, 2, 3, 5];

/** Open or close a panel; `open` omitted toggles it. Properties and Claude share the right dock. */
export function setPanel(state: WorkspaceState, panel: PanelId, open?: boolean, tab?: BottomTab): WorkspaceState {
  switch (panel) {
    case "project":
      return { ...state, left: open ?? !state.left };
    case "bottom": {
      if (tab) {
        const shown = state.bottom && state.bottomTab === tab;
        const next = open ?? !shown;
        return next ? { ...state, bottom: true, bottomTab: tab } : { ...state, bottom: false };
      }
      return { ...state, bottom: open ?? !state.bottom };
    }
    case "properties":
    case "assistant": {
      const shown = state.right && state.rightTab === panel;
      const next = open ?? !shown;
      return next ? { ...state, right: true, rightTab: panel } : shown ? { ...state, right: false } : state;
    }
  }
}

export function resize(state: WorkspaceState, dock: keyof WorkspaceState["sizes"], size: number): WorkspaceState {
  const [min, max] = SIZE_LIMITS[dock];
  return { ...state, sizes: { ...state.sizes, [dock]: Math.round(Math.min(max, Math.max(min, size))) } };
}

/** Moves a tree section before another one (drag of the section header). */
export function moveSection(order: TreeSection[], moved: TreeSection, before: TreeSection | null): TreeSection[] {
  const rest = order.filter((s) => s !== moved);
  const at = before === null ? rest.length : rest.indexOf(before);
  rest.splice(at < 0 ? rest.length : at, 0, moved);
  return rest;
}

const bool = (value: unknown, fallback: boolean) => (typeof value === "boolean" ? value : fallback);

/** A stored order of the sections: «Виды», when missing (an order saved before Э6.1), goes first (ADR 0008);
 * other new sections go to the end. */
export function normalizeOrder(raw: unknown): TreeSection[] {
  const valid = Array.isArray(raw) ? raw.filter((s): s is TreeSection => typeof s === "string" && s in TREE_SECTIONS) : [];
  const unique = [...new Set(valid)];
  const head: TreeSection[] = unique.includes("views") ? [] : ["views"];
  return [...head, ...unique, ...DEFAULT_TREE_ORDER.filter((s) => !unique.includes(s) && !head.includes(s))];
}

/** Stored state may come from an older version or be damaged: keep only valid values. */
export function normalizeWorkspace(raw: unknown): WorkspaceState {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return DEFAULT_WORKSPACE;
  const r = raw as Partial<Record<keyof WorkspaceState, unknown>>;
  const d = DEFAULT_WORKSPACE;
  let state: WorkspaceState = {
    left: bool(r.left, d.left),
    right: bool(r.right, d.right),
    bottom: bool(r.bottom, d.bottom),
    rightTab: r.rightTab === "assistant" ? "assistant" : "properties",
    bottomTab: typeof r.bottomTab === "string" && r.bottomTab in BOTTOM_TITLES ? (r.bottomTab as BottomTab) : d.bottomTab,
    ribbonTab: typeof r.ribbonTab === "string" && /^[a-z]{1,16}$/.test(r.ribbonTab) ? r.ribbonTab : d.ribbonTab,
    ribbonCollapsed: bool(r.ribbonCollapsed, d.ribbonCollapsed),
    theme: r.theme === "light" ? "light" : "dark",
    sizes: d.sizes,
    treeOrder: normalizeOrder(r.treeOrder),
    treeCollapsed: Array.isArray(r.treeCollapsed) ? r.treeCollapsed.filter((s): s is TreeSection => typeof s === "string" && s in TREE_SECTIONS) : [],
    sectionMiniPlan: bool(r.sectionMiniPlan, d.sectionMiniPlan),
  };
  const sizes = (r.sizes && typeof r.sizes === "object" ? r.sizes : {}) as Record<string, unknown>;
  for (const dock of ["left", "right", "bottom"] as const) {
    const size = sizes[dock];
    if (typeof size === "number" && Number.isFinite(size)) state = resize(state, dock, size);
  }
  return state;
}

export function normalizeView(raw: unknown): ViewState {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return DEFAULT_VIEW;
  const r = raw as Partial<Record<keyof ViewState, unknown>>;
  const d = DEFAULT_VIEW;
  const op = r.wireframeOpacity;
  return {
    clip: bool(r.clip, d.clip),
    perspective: bool(r.perspective, d.perspective),
    vexag: VEXAG_STEPS.includes(r.vexag as number) ? (r.vexag as number) : d.vexag,
    grid: bool(r.grid, d.grid),
    wireframeOpacity: typeof op === "number" && op >= 0.1 && op <= 1 ? op : d.wireframeOpacity,
    hidden: Array.isArray(r.hidden) ? r.hidden.filter((k): k is string => typeof k === "string").slice(0, 2000) : [],
  };
}

export function setLayerShown(view: ViewState, key: string, shown: boolean): ViewState {
  const hidden = view.hidden.filter((k) => k !== key);
  if (!shown) hidden.push(key);
  return { ...view, hidden: hidden.slice(-2000) }; // ТЗ 7.0.10: at most 2000 keys
}
