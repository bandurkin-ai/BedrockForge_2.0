/**
 * The points of the check in the 3D window (ТЗ 7.35 Д-32 п. 1, «подсветка в 3D»): one layer `implicit-check` of the scene — the layer of the
 * errors of the cross-validation (`viz/xval/errorPoints.ts`, a lazy chunk with three.js) over the points of the page of the table, coloured by
 * the residual. The cloud is kept, so a scene made again gets the layer back while it is shown; `reveal` brings the camera to a box (the
 * point of a row). No React.
 */
import { refusal } from "../../shell/objectRun";
import type { Scene } from "../../viz/Scene";
import { currentScene, onScene } from "../scene/sceneHost";
import type { ErrorCloud } from "../estimation/xvalModel";

export const CHECK_KEY = "implicit-check";

let cloud: ErrorCloud | null = null;
let off: (() => void) | null = null;
let turn = 0;
let fresh = false;

function apply(scene: Scene | null): void {
  const mine = ++turn;
  if (!scene) return;
  if (!cloud) {
    scene.removeLayer(CHECK_KEY);
    return;
  }
  const shown = cloud;
  void import("../../viz/xval/errorPoints").then((mod) => {
    if (mine !== turn || cloud !== shown) return;
    const layer = new mod.ErrorPoints(shown, scene.clipPlanes, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
    scene.addLayer(CHECK_KEY, layer);
    if (fresh && layer.bbox) scene.reveal([layer.bbox]);
    fresh = false;
    scene.invalidate();
  });
}

const NOT_READY = () => refusal("scene_not_ready", "3D-окно ещё не готово: откройте проект и дождитесь первого кадра.", "Когда в 3D-окне появится сетка, повторите.");

/** Puts the points into the scene (instead of the ones shown); the 3D window must be up. */
export function showCheckPoints(next: ErrorCloud): void {
  const scene = currentScene();
  if (!scene) throw NOT_READY();
  fresh = cloud === null;
  cloud = next;
  if (!off) off = onScene(apply);
  else apply(scene);
}

/** The camera goes to a box (absolute [xmin, ymin, zmin, xmax, ymax, zmax]). */
export function revealBox(box: number[]): void {
  const scene = currentScene();
  if (!scene) throw NOT_READY();
  scene.reveal([box]);
  scene.invalidate();
}

/** Takes the layer away. */
export function hideCheckPoints(): void {
  turn++;
  cloud = null;
  fresh = false;
  off?.();
  off = null;
  currentScene()?.removeLayer(CHECK_KEY);
}

export const checkPointsShown = (): { n: number; limit: number } | null => (cloud ? { n: cloud.n, limit: cloud.limit } : null);
