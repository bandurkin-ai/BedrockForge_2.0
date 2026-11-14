import type { Tab } from "../route";

/** What a viewport pane can show. Same set as the URL tabs, so the active pane is addressable. */
export type ViewKind = Tab;
export type Layout = "single" | "columns" | "rows" | "quad";
export type Theme = "dark" | "light";
export type PanelId = "project" | "properties" | "assistant" | "bottom";
export type RightTab = "properties" | "assistant";
export type BottomTab = "table" | "log";

export const VIEW_TITLES: Record<ViewKind, string> = {
  view3d: "3D",
  plan: "План",
  section: "Разрез",
  log: "Колонка",
  table: "Таблица",
  audit: "Аудит",
  import: "Импорт",
};
export const VIEW_KINDS = Object.keys(VIEW_TITLES) as ViewKind[];

export const LAYOUT_TITLES: Record<Layout, string> = {
  single: "Одно окно",
  columns: "Два окна рядом",
  rows: "Два окна друг над другом",
  quad: "Четыре окна",
};
export const PANE_COUNT: Record<Layout, number> = { single: 1, columns: 2, rows: 2, quad: 4 };

export interface WorkspaceState {
  layout: Layout;
  /** Always four entries; the layout decides how many are visible. */
  panes: ViewKind[];
  active: number;
  left: boolean;
  right: boolean;
  bottom: boolean;
  rightTab: RightTab;
  bottomTab: BottomTab;
  ribbonTab: string;
  ribbonCollapsed: boolean;
  theme: Theme;
  sizes: { left: number; right: number; bottom: number };
}

export const DEFAULT_WORKSPACE: WorkspaceState = {
  layout: "single",
  panes: ["view3d", "plan", "section", "log"],
  active: 0,
  left: true,
  right: true,
  bottom: false,
  rightTab: "properties",
  bottomTab: "table",
  ribbonTab: "home",
  ribbonCollapsed: false,
  theme: "dark",
  sizes: { left: 260, right: 340, bottom: 260 },
};

export const SIZE_LIMITS = {
  left: [180, 480],
  right: [260, 640],
  bottom: [120, 600],
} as const;

export function setLayout(state: WorkspaceState, layout: Layout): WorkspaceState {
  return { ...state, layout, active: Math.min(state.active, PANE_COUNT[layout] - 1) };
}

export function setPane(state: WorkspaceState, index: number, view: ViewKind): WorkspaceState {
  if (index < 0 || index > 3 || state.panes[index] === view) return state;
  const panes = state.panes.slice();
  panes[index] = view;
  return { ...state, panes };
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

export function resize(state: WorkspaceState, dock: keyof WorkspaceState["sizes"], size: number): WorkspaceState {
  const [min, max] = SIZE_LIMITS[dock];
  return { ...state, sizes: { ...state.sizes, [dock]: Math.round(Math.min(max, Math.max(min, size))) } };
}

/** Stored state may come from an older version: keep only values that are still valid. */
export function normalize(raw: unknown): WorkspaceState {
  if (!raw || typeof raw !== "object") return DEFAULT_WORKSPACE;
  const r = raw as Partial<WorkspaceState>;
  const d = DEFAULT_WORKSPACE;
  const layout = r.layout && r.layout in PANE_COUNT ? r.layout : d.layout;
  const panes = d.panes.map((fallback, i) => {
    const view = Array.isArray(r.panes) ? r.panes[i] : undefined;
    return view && VIEW_KINDS.includes(view) ? view : fallback;
  });
  const bool = (value: unknown, fallback: boolean) => (typeof value === "boolean" ? value : fallback);
  let state: WorkspaceState = {
    layout,
    panes,
    active: typeof r.active === "number" && r.active >= 0 && r.active < PANE_COUNT[layout] ? r.active : 0,
    left: bool(r.left, d.left),
    right: bool(r.right, d.right),
    bottom: bool(r.bottom, d.bottom),
    rightTab: r.rightTab === "assistant" ? "assistant" : "properties",
    bottomTab: r.bottomTab === "log" ? "log" : "table",
    ribbonTab: typeof r.ribbonTab === "string" ? r.ribbonTab : d.ribbonTab,
    ribbonCollapsed: bool(r.ribbonCollapsed, d.ribbonCollapsed),
    theme: r.theme === "light" ? "light" : "dark",
    sizes: d.sizes,
  };
  for (const dock of ["left", "right", "bottom"] as const) {
    const size = r.sizes?.[dock];
    if (typeof size === "number" && Number.isFinite(size)) state = resize(state, dock, size);
  }
  return state;
}

const STORAGE_KEY = "bedrockforge.workspace.v1";

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
