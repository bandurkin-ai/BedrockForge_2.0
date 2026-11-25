// A fake scene for the tests of the preview in 3D (Э18, 18.21): the layers kept by the key, what was added and removed, the camera brought to them.
import * as THREE from "three";
import type { Scene } from "../../src/viz/Scene";
import type { SceneLayer } from "../../src/viz/sceneTypes";

export interface Log {
  layers: Map<string, SceneLayer>;
  added: string[];
  removed: string[];
  invalid: number;
  changed: number;
  revealed: number[][];
  /** the scene lost its context and has it again */
  restore: () => void;
}

export function fakeScene(): { scene: Scene; log: Log } {
  const log: Log = { layers: new Map(), added: [], removed: [], invalid: 0, changed: 0, revealed: [], restore: () => undefined };
  const scene = {
    clipPlanes: [],
    resolution: new THREE.Vector2(800, 600),
    addLayer: (k: string, l: SceneLayer) => {
      log.added.push(k);
      log.layers.set(k, l);
    },
    removeLayer: (k: string) => {
      log.removed.push(k);
      log.layers.delete(k);
    },
    layerChanged: () => void log.changed++,
    invalidate: () => void log.invalid++,
    onRestored: (fn: () => void) => {
      log.restore = fn;
      return () => undefined;
    },
    // the other modules (swath, trends, variography) attach their handles to every scene that appears
    setHandles: () => undefined,
    reveal: (b: number[][]) => {
      log.revealed.push(b[0]);
      return true;
    },
  } as unknown as Scene;
  return { scene, log };
}
