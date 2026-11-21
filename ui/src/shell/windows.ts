/**
 * Floating windows of the app (ТЗ 7.0.12): one layer, the clicked window goes on top, the title
 * bar never leaves the app window. Pure geometry + state; FloatingWindow.tsx draws them.
 * Place and size are remembered by window kind (ADR 0019 decision 9): the layer installs the memory
 * (the user's settings, `workspace.windows`), a new window of that kind opens where the last one was left.
 */
import { Store } from "./store";
import type { WindowRect } from "./workspace";

export type WindowSize = "compact" | "medium" | "form" | "normal";
export const WINDOW_SIZES: Record<WindowSize, { w: number; h: number }> = {
  compact: { w: 420, h: 300 },
  medium: { w: 560, h: 420 },
  form: { w: 640, h: 560 },
  normal: { w: 860, h: 600 },
};

export interface FloatWin {
  id: string;
  kind: string;
  title: string;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  maximized: boolean;
  props?: Record<string, unknown>;
}

export const windowStore = new Store<{ windows: FloatWin[] }>({ windows: [] });
let topZ = 1;

export const TITLE_H = 30;

/** Keeps the title bar reachable inside the viewport (ТЗ 7.0.12). */
export function clampToViewport(win: Pick<FloatWin, "x" | "y" | "w" | "h">, vw: number, vh: number) {
  const w = Math.min(win.w, vw);
  const h = Math.min(win.h, vh);
  const x = Math.min(Math.max(win.x, 60 - w), vw - 60);
  const y = Math.min(Math.max(win.y, 0), vh - TITLE_H);
  return { x, y, w, h };
}

/** Where windows of a kind were left: the user's settings behind it (set by the window layer). */
export interface WindowMemory {
  get(kind: string): WindowRect | undefined;
  put(kind: string, rect: WindowRect): void;
}

let memory: WindowMemory | null = null;

export function setWindowMemory(m: WindowMemory | null): void {
  memory = m;
}

/** Remembers the place and size of a window (after a move or a resize; a maximized window keeps the old ones). */
export function rememberWindowRect(win: Pick<FloatWin, "kind" | "x" | "y" | "w" | "h" | "maximized">): void {
  if (!win.maximized) memory?.put(win.kind, { x: win.x, y: win.y, w: win.w, h: win.h });
}

/** The top of a new window left centered: below the ribbon while the window still fits. */
const OPEN_TOP = 60;
/** A new window is not made lower than this to keep {@link OPEN_TOP} (a smaller screen — it goes up instead). */
const OPEN_MIN_H = 240;

/**
 * A new window whole inside the viewport: its size no more than the viewport, moved left and up as far as needed
 * (the bottom and the resize grip in sight). A window being dragged keeps {@link clampToViewport}.
 */
export function fitToViewport(win: Pick<FloatWin, "x" | "y" | "w" | "h">, vw: number, vh: number) {
  const w = Math.min(win.w, vw);
  const h = Math.min(win.h, vh);
  const x = Math.min(Math.max(win.x, 0), Math.max(0, vw - w));
  const y = Math.min(Math.max(win.y, 0), Math.max(0, vh - h));
  return { x, y, w, h };
}

/** The first place of a new window: the remembered one of its kind, shifted past open windows of that kind; else centered. */
export function initialRect(kind: string, size: WindowSize, open: readonly Pick<FloatWin, "kind">[], vw: number, vh: number, remembered?: WindowRect) {
  if (remembered) {
    const same = open.filter((o) => o.kind === kind).length;
    return fitToViewport({ x: remembered.x + same * 24, y: remembered.y + same * 24, w: remembered.w, h: remembered.h }, vw, vh);
  }
  const { w } = WINDOW_SIZES[size];
  const top = Math.max(OPEN_TOP, (vh - WINDOW_SIZES[size].h) / 2);
  const h = Math.min(WINDOW_SIZES[size].h, Math.max(OPEN_MIN_H, vh - top)); // below the ribbon: lower rather than off the screen
  const n = open.length;
  return fitToViewport({ x: (vw - w) / 2 + n * 24, y: top + n * 24, w, h }, vw, vh);
}

export function openWindow(kind: string, title: string, size: WindowSize = "medium", props?: Record<string, unknown>, vw = 1280, vh = 800): string {
  const existing = windowStore.get().windows.find((w) => w.kind === kind && JSON.stringify(w.props ?? {}) === JSON.stringify(props ?? {}));
  if (existing) {
    focusWindow(existing.id);
    return existing.id;
  }
  const open = windowStore.get().windows;
  const n = open.length;
  const id = `${kind}-${Date.now().toString(36)}-${n}`;
  const pos = initialRect(kind, size, open, vw, vh, memory?.get(kind));
  windowStore.set((s) => ({ windows: [...s.windows, { id, kind, title, ...pos, z: ++topZ, maximized: false, props }] }));
  return id;
}

export function focusWindow(id: string): void {
  windowStore.set((s) => ({ windows: s.windows.map((w) => (w.id === id ? { ...w, z: ++topZ } : w)) }));
}

export function updateWindow(id: string, patch: Partial<FloatWin>): void {
  windowStore.set((s) => ({ windows: s.windows.map((w) => (w.id === id ? { ...w, ...patch } : w)) }));
}

export function closeWindow(id: string): void {
  closeGuards.delete(id);
  windowStore.set((s) => ({ windows: s.windows.filter((w) => w.id !== id) }));
}

/** A window with unsaved changes asks before it closes (ТЗ 17.4.6): the guard resolves true to close. */
const closeGuards = new Map<string, () => Promise<boolean>>();

export function setCloseGuard(id: string, guard: (() => Promise<boolean>) | null): void {
  if (guard) closeGuards.set(id, guard);
  else closeGuards.delete(id);
}

/** The cross of the title bar: closes unless the window's guard says no. */
export async function requestClose(id: string): Promise<void> {
  const guard = closeGuards.get(id);
  if (guard && !(await guard())) return;
  closeWindow(id);
}
