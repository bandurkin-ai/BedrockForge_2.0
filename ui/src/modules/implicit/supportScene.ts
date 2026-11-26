/**
 * The map of the support in the 3D window (ТЗ 7.35 Д-32 п. 2): one layer `implicit-support` of the scene (`viz/support/layer.ts`, a lazy
 * chunk with three.js). The surface and its distances come from the core in one answer of the route `support` (`format: "f4mesh"`,
 * `checkApi.ts`): the whole wireframe — the vertices and triangles in the order of the distances, so a surface of several parts or of over
 * 64 000 triangles is painted as well as a small one, and no vertices are matched. The layer is kept, so a scene made again (the graphics restarted, another project)
 * gets it back while it is shown. `repaint` colours the vertices again without a read (D_оп moved). No React.
 */
import { refusal } from "../../shell/objectRun";
import type { Scene } from "../../viz/Scene";
import type { SupportMeshData, SupportView } from "../../viz/support/layer";
import { currentScene, onScene } from "../scene/sceneHost";
import { colorsOf } from "./supportModel";

export const SUPPORT_KEY = "implicit-support";

/** The surface as the window draws it (the whole wireframe), and the distances of its vertices (the same order). */
export interface SupportCloud {
  wireframeId: number;
  mesh: SupportMeshData;
  /** m, the order of the vertices; NaN — no data points */
  values: Float32Array;
  threshold: number;
}

let cloud: SupportCloud | null = null;
let view: SupportView | null = null;
let rgb: Float32Array | null = null;
let off: (() => void) | null = null;
let turn = 0;
let fresh = false;

function apply(scene: Scene | null): void {
  const mine = ++turn;
  view = null;
  if (!scene) return;
  if (!cloud) {
    scene.removeLayer(SUPPORT_KEY);
    return;
  }
  const shown = cloud;
  void import("../../viz/support/layer").then((mod) => {
    if (mine !== turn || cloud !== shown) return;
    rgb = colorsOf(shown.values, shown.threshold, rgb && rgb.length === shown.values.length * 3 ? rgb : undefined);
    const layer = new mod.SupportView(shown.mesh, rgb, scene.clipPlanes);
    scene.addLayer(SUPPORT_KEY, layer);
    view = layer;
    if (fresh && layer.bbox) scene.reveal([layer.bbox]);
    fresh = false;
    scene.invalidate();
  });
}

/** Puts the surface into the scene (instead of the one shown); the 3D window must be up. */
export function showSupport(next: SupportCloud): void {
  const scene = currentScene();
  if (!scene) throw refusal("scene_not_ready", "3D-окно ещё не готово: откройте проект и дождитесь первого кадра.", "Когда в 3D-окне появится сетка, повторите.");
  fresh = cloud === null || cloud.wireframeId !== next.wireframeId;
  cloud = next;
  rgb = null;
  if (!off) off = onScene(apply);
  else apply(scene);
}

/** The threshold moved: the same vertices, new colours (one pass over the distances, the array of the layer reused). Milliseconds of the pass. */
export function repaint(threshold: number): number | null {
  if (!cloud) return null;
  cloud = { ...cloud, threshold };
  if (!view || !rgb) return null; // the layer is not made yet: it is painted with the new threshold when it is
  const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
  colorsOf(cloud.values, threshold, rgb);
  view.setColors(rgb);
  currentScene()?.invalidate();
  return (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
}

/** Takes the layer away. */
export function hideSupport(): void {
  turn++;
  cloud = null;
  view = null;
  rgb = null;
  fresh = false;
  off?.();
  off = null;
  currentScene()?.removeLayer(SUPPORT_KEY);
}

export const supportShown = (): { wireframeId: number; threshold: number } | null => (cloud ? { wireframeId: cloud.wireframeId, threshold: cloud.threshold } : null);

/** For the E2E hook: the surface painted now (the layer is in the scene when `vertices` is a number). */
export const supportStats = (): { wireframeId: number; threshold: number; vertices: number | null } | null => (cloud ? { wireframeId: cloud.wireframeId, threshold: cloud.threshold, vertices: view ? view.vertices : null } : null);
