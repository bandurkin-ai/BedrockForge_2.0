/**
 * Floating windows of the app (ТЗ 7.0.12): one layer, the clicked window goes on top, the title
 * bar never leaves the app window. Pure geometry + state; FloatingWindow.tsx draws them.
 */
import { Store } from "./store";

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

export function openWindow(kind: string, title: string, size: WindowSize = "medium", props?: Record<string, unknown>, vw = 1280, vh = 800): string {
  const existing = windowStore.get().windows.find((w) => w.kind === kind && JSON.stringify(w.props ?? {}) === JSON.stringify(props ?? {}));
  if (existing) {
    focusWindow(existing.id);
    return existing.id;
  }
  const { w, h } = WINDOW_SIZES[size];
  const n = windowStore.get().windows.length;
  const id = `${kind}-${Date.now().toString(36)}-${n}`;
  const pos = clampToViewport({ x: (vw - w) / 2 + n * 24, y: Math.max(60, (vh - h) / 2) + n * 24, w, h }, vw, vh);
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
  windowStore.set((s) => ({ windows: s.windows.filter((w) => w.id !== id) }));
}
