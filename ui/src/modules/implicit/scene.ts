/**
 * The implicit models in the main 3D (ТЗ 8.9.4; ADR 0026): the data points of the groups the tree's check boxes switched on (`viewState.ts`) — a layer
 * `implicit:points:<model>:<group>` of the scene (`viz/xval/errorPoints.ts` as it is: discs coloured by the kind of the point, float32 from an integer
 * origin, `viz/origin.ts`, ТЗ 5.4) — and the ellipsoid of the anisotropy of a model (`implicit:ellipsoid:<model>`: `viz/ellipsoid` as it is, one structure,
 * the colour of the models, Cobalt). The wireframes of the outputs are ordinary wireframes: the layer of wireframes shows them, nothing here. The layers
 * follow the stores; a lost context drops them and `onRestored` builds them again. A lazy chunk with three.js (`bind.ts` attaches it to every scene).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import type { Scene } from "../../viz/Scene";
import { buildEllipsoid } from "../../viz/ellipsoid/build";
import { EllipsoidLayer } from "../../viz/ellipsoid/layer";
import { ErrorPoints } from "../../viz/xval/errorPoints";
import { sceneUi } from "../scene/sceneHost";
import { trendsStore } from "../trends/state";
import { implicitStore } from "./state";
import { anisotropyOf, drawable, ellipsoidFrame, trendAngles } from "./propsModel";
import { colorsOf, implicitViewStore, type PointsEntry } from "./viewState";

export const pointsLayerKey = (model: number, group: number): string => `implicit:points:${model}:${group}`;
export const ellipsoidLayerKey = (model: number): string => `implicit:ellipsoid:${model}`;

interface Held<L> {
  layer: L;
  /** what it was built from: the entry (points) or the signature of the pose (ellipsoid) */
  of: unknown;
}

/** What the E2E hook and the tests read: the layers drawn. */
export interface ImplicitStats {
  points: { key: string; count: number; origin: { x: number; y: number; z: number }; bbox: number[] | null }[];
  ellipsoids: { key: string; centre: number[]; ranges: number[]; angles: number[] }[];
}

export class ImplicitScene {
  private readonly points = new Map<string, Held<ErrorPoints>>();
  private readonly ellipsoids = new Map<string, Held<EllipsoidLayer>>();
  private readonly stops: (() => void)[] = [];
  private disposed = false;

  constructor(private readonly scene: Scene) {
    const sync = () => this.sync();
    this.stops.push(implicitViewStore.subscribe(sync), implicitStore.subscribe(sync), trendsStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  private readyPoints(): Map<string, PointsEntry> {
    const v = implicitViewStore.get();
    const out = new Map<string, PointsEntry>();
    for (const key of v.points) {
      const e = v.entries[key];
      if (e && e.status === "ready" && e.drawn > 0) out.set(key, e);
    }
    return out;
  }

  /** The ellipsoids to draw now, by layer key: the pose as a signature and what the layer needs. */
  private wantedEllipsoids(): Map<string, { sig: string; build: Parameters<typeof buildEllipsoid>[0] }> {
    const out = new Map<string, { sig: string; build: Parameters<typeof buildEllipsoid>[0] }>();
    const { details } = implicitStore.get();
    for (const m of implicitViewStore.get().ellipsoids) {
      const info = details[m];
      if (!info) continue;
      const a = anisotropyOf(info.meta, trendAngles);
      const f = ellipsoidFrame(info.meta);
      if (!drawable(a) || !f) continue;
      const ranges = { a1: f.size, a2: f.size * a.ratios[1], a3: f.size * a.ratios[2] };
      const sig = JSON.stringify([a.ang1, a.ang2, a.ang3, ranges.a1, ranges.a2, ranges.a3, f.centre]);
      out.set(ellipsoidLayerKey(m), { sig, build: { centre: f.centre, structures: [{ angles: { ang1: a.ang1, ang2: a.ang2, ang3: a.ang3 }, ranges }], selected: 0 } });
    }
    return out;
  }

  sync(): void {
    if (this.disposed) return;
    let changed = false;
    const ready = this.readyPoints();
    for (const [key, held] of this.points) {
      const e = ready.get(key.slice("implicit:points:".length));
      if (e !== held.of) {
        this.scene.removeLayer(key, false);
        this.points.delete(key);
        changed = true;
      }
    }
    for (const [short, e] of ready) {
      const key = `implicit:points:${short}`;
      if (this.points.has(key)) continue;
      try {
        const layer = new ErrorPoints({ xyz: e.xyz, rgb: colorsOf(e.kinds) }, this.scene.clipPlanes, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
        this.scene.addLayer(key, layer);
        this.points.set(key, { layer, of: e });
        changed = true;
      } catch (err) {
        sceneUi.set({ error: `Точки данных не показаны в 3D: ${errorText(err)}` });
      }
    }
    const wanted = this.wantedEllipsoids();
    for (const [key, held] of this.ellipsoids) {
      if (wanted.get(key)?.sig !== held.of) {
        this.scene.removeLayer(key, false);
        this.ellipsoids.delete(key);
        changed = true;
      }
    }
    for (const [key, w] of wanted) {
      if (this.ellipsoids.has(key)) continue;
      try {
        const layer = new EllipsoidLayer(buildEllipsoid(w.build), { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
        this.scene.addLayer(key, layer);
        this.ellipsoids.set(key, { layer, of: w.sig });
        changed = true;
      } catch (err) {
        sceneUi.set({ error: `Эллипсоид анизотропии не показан в 3D: ${errorText(err)}` });
      }
    }
    if (changed) this.scene.invalidate();
  }

  /** After a lost context the scene dropped every layer: they are built again from the stores. */
  private restore(): void {
    this.points.clear();
    this.ellipsoids.clear();
    this.sync();
  }

  stats(): ImplicitStats {
    return {
      points: [...this.points.entries()].map(([key, h]) => ({ key, count: h.layer.count, origin: { ...h.layer.origin }, bbox: h.layer.bbox ? [...h.layer.bbox] : null })),
      ellipsoids: [...this.ellipsoids.entries()].map(([key, h]) => {
        const p = h.layer.pose(0);
        return { key, centre: [...h.layer.build.centre], ranges: [p.ranges.a1, p.ranges.a2, p.ranges.a3], angles: [p.frame.ang1, p.frame.ang2, p.frame.ang3] };
      }),
    };
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    for (const key of [...this.points.keys(), ...this.ellipsoids.keys()]) this.scene.removeLayer(key, false);
    this.points.clear();
    this.ellipsoids.clear();
    this.scene.invalidate();
  }
}

/** Attaches the layers of the implicit models to a scene; gives what detaches them. */
export function attachImplicitScene(scene: Scene): () => void {
  const s = new ImplicitScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  // BF_E2E=1: the layers as drawn (app/e2e/implicit.spec.ts)
  if (hook) window.__bfImplicit = { stats: () => s.stats() };
  return () => {
    s.dispose();
    if (hook) delete window.__bfImplicit;
  };
}

declare global {
  interface Window {
    __bfImplicit?: { stats: () => ImplicitStats };
  }
}
