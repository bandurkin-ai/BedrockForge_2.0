/**
 * The errors of the cross-validation in the 3D window (ТЗ 8.8.5): one layer `xval-errors` of the scene (`viz/xval/errorPoints.ts`, a
 * lazy chunk with three.js). The cloud is kept, so a scene made again (the graphics restarted, another project) gets the layer back
 * while it is shown; `hideErrors` takes it away. No React.
 */
import { refusal } from "../../shell/objectRun";
import type { Scene } from "../../viz/Scene";
import { currentScene, onScene } from "../scene/sceneHost";
import type { ErrorCloud } from "./xvalModel";

export const XVAL_KEY = "xval-errors";

let cloud: ErrorCloud | null = null;
let off: (() => void) | null = null;
let turn = 0;
let fresh = false;

function apply(scene: Scene | null): void {
  const mine = ++turn;
  if (!scene) return;
  if (!cloud) {
    scene.removeLayer(XVAL_KEY);
    return;
  }
  const shown = cloud;
  void import("../../viz/xval/errorPoints").then((mod) => {
    if (mine !== turn || cloud !== shown) return;
    const layer = new mod.ErrorPoints(shown, scene.clipPlanes, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
    scene.addLayer(XVAL_KEY, layer);
    if (fresh && layer.bbox) scene.reveal([layer.bbox]);
    fresh = false;
    scene.invalidate();
  });
}

/** Puts the cloud into the scene (instead of the one shown); the 3D window must be up. */
export function showErrors(next: ErrorCloud): void {
  const scene = currentScene();
  if (!scene) throw refusal("scene_not_ready", "3D-окно ещё не готово: откройте проект и дождитесь первого кадра.", "Когда в 3D-окне появится сетка, повторите.");
  fresh = cloud === null;
  cloud = next;
  if (!off) off = onScene(apply);
  else apply(scene);
}

/** Takes the layer away. */
export function hideErrors(): void {
  turn++;
  cloud = null;
  fresh = false;
  off?.();
  off = null;
  currentScene()?.removeLayer(XVAL_KEY);
}

export const errorsShown = (): boolean => cloud !== null;
