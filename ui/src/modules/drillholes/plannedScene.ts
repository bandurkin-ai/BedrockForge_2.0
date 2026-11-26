/**
 * The planned holes in the main 3D (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29, № 103): one layer `planned` of the scene (`viz/planned/layer.ts`: the paths,
 * the chosen hole thicker, the target marks, the collars) made from the list of the project and the view (the holes switched off, the hole chosen). A
 * scene layer is clipped by the section window like the rest, so the holes are seen in the sections (7.14) too — the layer needs no section code. The
 * layer follows the stores; a lost context drops it and `onRestored` builds it again. A lazy chunk with three.js (`plannedBind.ts` attaches it to every
 * scene). The paths come from the core every 1 m, float64; the layer takes them from an integer origin (`viz/origin.ts`, ТЗ 5.4).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import type { Scene } from "../../viz/Scene";
import { buildPlanned, type PlannedDraw } from "../../viz/planned/build";
import { PlannedLayer } from "../../viz/planned/layer";
import { sceneUi } from "../scene/sceneHost";
import type { PlannedRow } from "./plannedApi";
import { isShown, plannedStore, plannedViewStore } from "./plannedState";

export const PLANNED_KEY = "planned";

/** The holes to draw: those with a path that the view has not switched off. */
export function drawsOf(rows: readonly PlannedRow[], hidden: readonly number[]): PlannedDraw[] {
  const off = new Set(hidden);
  const out: PlannedDraw[] = [];
  for (const r of rows) if (r.trajectory && !off.has(r.id)) out.push({ id: r.id, x: r.trajectory.x, y: r.trajectory.y, z: r.trajectory.z, target: r.meta.target });
  return out;
}

export interface PlannedStats {
  holes: number;
  segments: number;
  chosenSegments: number;
  targets: number;
  collars: number;
  origin: { x: number; y: number; z: number };
  bbox: number[] | null;
  /** ms of the last redraw: the build of the buffers and the layer in the scene */
  redrawMs: number | null;
}

export class PlannedScene {
  private layer: PlannedLayer | null = null;
  private of: { rows: readonly PlannedRow[]; hidden: readonly number[]; chosen: number | null } | null = null;
  private readonly stops: (() => void)[] = [];
  private disposed = false;
  redrawMs: number | null = null;

  constructor(private readonly scene: Scene) {
    const sync = () => this.sync();
    this.stops.push(plannedStore.subscribe(sync), plannedViewStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  sync(): void {
    if (this.disposed) return;
    const rows = plannedStore.get().rows;
    const v = plannedViewStore.get();
    const same = this.of && this.of.rows === rows && this.of.hidden === v.hidden && this.of.chosen === v.chosen;
    if (same) return;
    this.of = { rows, hidden: v.hidden, chosen: v.chosen };
    const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (this.layer) this.scene.removeLayer(PLANNED_KEY, false);
    this.layer = null;
    try {
      const build = buildPlanned(drawsOf(rows, v.hidden), v.chosen !== null && isShown(v, v.chosen) ? v.chosen : null);
      if (build) {
        this.layer = new PlannedLayer(build, { clip: this.scene.clipPlanes, resolution: this.scene.resolution, pixelRatio: typeof window === "undefined" ? 1 : window.devicePixelRatio || 1 });
        this.scene.addLayer(PLANNED_KEY, this.layer);
      }
    } catch (err) {
      sceneUi.set({ error: `Проектные скважины не показаны в 3D: ${errorText(err)}` });
    }
    this.scene.invalidate();
    this.redrawMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
  }

  /** After a lost context the scene dropped every layer: the layer is built again from the stores. */
  private restore(): void {
    this.layer = null;
    this.of = null;
    this.sync();
  }

  stats(): PlannedStats {
    const s = this.layer?.stats();
    return { holes: s?.holes ?? 0, segments: s?.segments ?? 0, chosenSegments: s?.chosenSegments ?? 0, targets: s?.targets ?? 0, collars: s?.collars ?? 0, origin: s?.origin ?? { x: 0, y: 0, z: 0 }, bbox: s?.bbox ?? null, redrawMs: this.redrawMs };
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    if (this.layer) this.scene.removeLayer(PLANNED_KEY, false);
    this.layer = null;
    this.scene.invalidate();
  }
}

/** Attaches the layer of the planned holes to a scene; gives what detaches it. With BF_E2E=1 the layer as drawn is `window.__bfPlanned.stats()`. */
export function attachPlannedScene(scene: Scene): () => void {
  const s = new PlannedScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfPlanned = { stats: () => s.stats() };
  return () => {
    s.dispose();
    if (hook) delete window.__bfPlanned;
  };
}

declare global {
  interface Window {
    __bfPlanned?: { stats: () => PlannedStats };
  }
}
