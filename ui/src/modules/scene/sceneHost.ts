/**
 * The live 3D scene of the window, for the command handlers and other layers (displays along holes): the
 * facade `viz/Scene` is a lazy chunk, so it is reached through here only by type. `onScene` calls back
 * with the scene now and whenever it is created or closed (project switched, context lost → restored).
 */
import { Store } from "../../shell/store";
import type { Scene } from "../../viz/Scene";
import type { WebGl2Probe } from "../../viz/core/renderer";

let current: Scene | null = null;
const listeners = new Set<(s: Scene | null) => void>();

export function currentScene(): Scene | null {
  return current;
}

export function setCurrentScene(s: Scene | null): void {
  current = s;
  for (const fn of listeners) fn(s);
}

/** Called now with the current scene and on every change; gives the unsubscribe. */
export function onScene(fn: (s: Scene | null) => void): () => void {
  listeners.add(fn);
  fn(current);
  return () => listeners.delete(fn);
}

/** The scene or a Russian error for a command run before the 3D window is up. */
export function requireScene(): Scene {
  if (!current) throw new Error("3D-окно ещё не готово: откройте проект и дождитесь первого кадра.");
  return current;
}

export interface SceneUi {
  /** the context is lost: «Графика перезапускается…» */
  lost: boolean;
  /** three losses within a minute: budgets halved (ADR 0004 §4) */
  degraded: boolean;
  /** manifests and parts being loaded */
  loading: number;
  error: string | null;
  /** WebGL2 is missing: the diagnostic window */
  noWebgl: WebGl2Probe | null;
  /** software WebGL (SwiftShader, WARP, llvmpipe): slow */
  software: boolean;
  /** sets with holes in the scene */
  sets: number;
  /** grows when the holes in the scene change (the mini-plan rebuilds) */
  content: number;
}

export const sceneUi = new Store<SceneUi>({ lost: false, degraded: false, loading: 0, error: null, noWebgl: null, software: false, sets: 0, content: 0 });
