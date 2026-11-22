/**
 * The ellipsoids of the windows «Вариография» in the main 3D (ТЗ 8.6, 7.35 Д-14; ADR 0022 §9, решения 17–19): for
 * every open window whose ellipsoid is shown (`Work.ellipsoid`, `variography.ellipsoid` / `variography.show`) — a
 * scene layer `vario:<window>` (`viz/ellipsoid/layer.ts`) built from the working model of the window
 * (`variographyStore`, «общие оси» applied); the structure chosen has the handles (`handles.ts`), those of the
 * window on top. One store: a drag writes the model there, the table and the curves of the window and this layer
 * follow in the same frame — the layer takes new matrices only (`setPoses`), it is built again only when the number
 * of structures or the centre changed. The frame of an edit is measured: `vario:edit` (`state.markEdit`) →
 * `vario:drawn` after the frame drawn, `performance.measure("vario:frame")` (≤ 50 ms, ТД14-5). After a lost context
 * the layers come again. A lazy chunk with three.js (`bind.ts` attaches it to every scene).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { windowStore } from "../../shell/windows";
import type { Scene } from "../../viz/Scene";
import type { HandleKind, Model } from "../../viz/ellipsoid/drag";
import { buildEllipsoid, type StructureView } from "../../viz/ellipsoid/build";
import { EllipsoidLayer } from "../../viz/ellipsoid/layer";
import { sceneUi } from "../scene/sceneHost";
import { EllipsoidHandlesControl, type EllipsoidHost, type EllipsoidTarget } from "./handles";
import { effectiveStructures, type VModel } from "./model";
import { chosenStructure, ellipsoidCentre, leastRange, markEdit, setDragging, setModel, takeEdit, variographyStore, withStructure, workOf, } from "./state";

export const VARIO_WINDOW_KIND = "variography";
export const ellipsoidLayerKey = (win: string): string => `vario:${win}`;

interface Shown {
  layer: EllipsoidLayer;
  /** what a new build depends on: the number of structures and the centre */
  shape: string;
  /** the poses drawn (JSON of the views) */
  poses: string;
  /** turned while a handle is dragged: the extent of the scene follows at the release */
  stale: boolean;
}

/** The structures of a model as the layer draws them (positive ranges only: a structure with a range 0 is not drawn). */
export function viewsOf(m: VModel): StructureView[] {
  return effectiveStructures(m).map((s) => ({ angles: { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 }, ranges: { a1: s.a1, a2: s.a2, a3: s.a3 } }));
}

const drawable = (v: readonly StructureView[]): boolean => v.length > 0 && v.every((s) => [s.ranges.a1, s.ranges.a2, s.ranges.a3].every((x) => Number.isFinite(x) && x > 0));

/** The times of the frames of the edits, ms (the last 200), for the E2E hook and the checkpoint. */
const frames: number[] = [];
export const frameTimes = (): number[] => [...frames];

export class VarioScene implements EllipsoidHost {
  private readonly shown = new Map<string, Shown>();
  private readonly stops: (() => void)[] = [];
  private disposed = false;
  /** an edit was applied to a layer: its frame is measured */
  private pending = false;
  readonly handles: EllipsoidHandlesControl;

  constructor(private readonly scene: Scene) {
    this.handles = new EllipsoidHandlesControl(scene, this);
    const sync = () => this.sync();
    this.stops.push(variographyStore.subscribe(sync), windowStore.subscribe(sync), scene.onRestored(() => this.restore()));
    if (typeof scene.onFrame === "function") this.stops.push(scene.onFrame(() => this.framed()));
    this.sync();
  }

  /** The open windows with their ellipsoid shown, the one on top first. */
  private windows(): string[] {
    return windowStore
      .get()
      .windows.filter((w) => w.kind === VARIO_WINDOW_KIND && workOf(w.id)?.ellipsoid?.visible === true)
      .sort((a, b) => b.z - a.z)
      .map((w) => w.id);
  }

  focused(): boolean {
    const wins = windowStore.get().windows;
    if (!wins.length) return false;
    const top = wins.reduce((a, b) => (b.z > a.z ? b : a));
    return top.kind === VARIO_WINDOW_KIND && this.shown.has(top.id);
  }

  target(): EllipsoidTarget | null {
    for (const id of this.windows()) {
      const st = this.shown.get(id);
      const w = workOf(id);
      if (!st || !w) continue;
      const index = chosenStructure(w);
      const s = effectiveStructures(w.model)[index];
      if (!s) continue;
      return { id, layer: st.layer, index, model: { angles: { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 }, ranges: { a1: s.a1, a2: s.a2, a3: s.a3 } }, min: leastRange(w) };
    }
    return null;
  }

  write(id: string, index: number, m: Model): void {
    const w = workOf(id);
    if (!w) return;
    markEdit();
    setModel(id, withStructure(w.model, index, { ...m.angles, ...m.ranges }));
  }

  dragging(id: string, kind: HandleKind | null): void {
    setDragging(kind ? { win: id, kind } : null);
  }

  /** The layer of a window (the samples of the disk are given by the window: `layer.setSamples`). */
  layerOf(win: string): EllipsoidLayer | null {
    return this.shown.get(win)?.layer ?? null;
  }

  /** For the E2E hook and the tests: the ellipsoids drawn. */
  stats(): { win: string; selected: number; structures: number; samples: number; centre: number[] }[] {
    return [...this.shown.entries()].map(([win, s]) => {
      const sh = s.layer.shown();
      return { win, selected: sh.selected, structures: sh.bases.length, samples: sh.samples, centre: [...s.layer.build.centre] };
    });
  }

  sync(): void {
    if (this.disposed) return;
    const wanted = new Set(this.windows());
    const dragging = !!variographyStore.get().dragging;
    let changed = false;
    for (const id of [...this.shown.keys()]) if (!wanted.has(id)) changed = this.drop(id) || changed;
    for (const id of wanted) {
      const w = workOf(id)!;
      const views = viewsOf(w.model);
      const centre = ellipsoidCentre(w);
      if (!centre || !drawable(views)) {
        changed = this.drop(id) || changed;
        continue;
      }
      const shape = JSON.stringify([views.length, centre]);
      const poses = JSON.stringify(views);
      const selected = chosenStructure(w);
      let st = this.shown.get(id);
      if (!st || st.shape !== shape) {
        if (st) this.scene.removeLayer(ellipsoidLayerKey(id), false);
        const layer = this.make(views, centre, selected);
        if (!layer) {
          this.shown.delete(id);
          continue;
        }
        st = { layer, shape, poses, stale: false };
        this.shown.set(id, st);
        this.scene.addLayer(ellipsoidLayerKey(id), layer);
        changed = true;
      } else if (st.poses !== poses) {
        st.layer.setPoses(views);
        st.poses = poses;
        st.stale = true;
        changed = true;
      }
      // the extent of the layer changed in place: «Вписать» and near / far follow (once, not every move of a drag)
      if (st.stale && !dragging) {
        st.stale = false;
        this.scene.layerChanged?.();
      }
      if (st.layer.selected !== selected) {
        st.layer.select(selected);
        changed = true;
      }
    }
    if (changed) {
      this.pending = true;
      this.scene.invalidate();
    }
    this.handles.refresh();
  }

  private make(views: StructureView[], centre: [number, number, number], selected: number): EllipsoidLayer | null {
    try {
      return new EllipsoidLayer(buildEllipsoid({ centre, structures: views, selected }), { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
    } catch (e) {
      sceneUi.set({ error: `Эллипсоид не показан в 3D: ${errorText(e)}` });
      return null;
    }
  }

  /** Before the frame that draws an edit: `vario:drawn` right after it (the task of the frame is over). */
  private framed(): void {
    if (!this.pending) return;
    this.pending = false;
    const at = takeEdit();
    if (at === null) return;
    setTimeout(() => {
      const ms = performance.now() - at;
      frames.push(ms);
      if (frames.length > 200) frames.shift();
      try {
        performance.mark("vario:drawn");
        performance.measure("vario:frame", "vario:edit", "vario:drawn");
      } catch {
        /* no User Timing: the time is kept in `frameTimes` */
      }
    }, 0);
  }

  private drop(id: string): boolean {
    const st = this.shown.get(id);
    if (!st) return false;
    this.shown.delete(id);
    // the scene disposes the layer it removes
    this.scene.removeLayer(ellipsoidLayerKey(id), false);
    return true;
  }

  /** After a lost context the scene dropped every layer: they are built again from the store. */
  private restore(): void {
    this.handles.lost();
    this.shown.clear();
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.handles.dispose();
    this.stops.forEach((fn) => fn());
    for (const id of [...this.shown.keys()]) this.drop(id);
    this.scene.invalidate();
  }
}

/** Attaches the ellipsoids to a scene; gives what detaches them. */
export function attachVarioScene(scene: Scene): () => void {
  const s = new VarioScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  // BF_E2E=1: the ellipsoids drawn, the handles, their points on the screen, the frames of the edits (app/e2e/variography.spec.ts)
  if (hook) window.__bfVario = { ellipsoids: () => s.stats(), handles: () => s.handles.stats(), point: (kind: HandleKind, deg?: number) => s.handles.point(kind, deg), frames: () => frameTimes() };
  return () => {
    s.dispose();
    if (hook) delete window.__bfVario;
  };
}

declare global {
  interface Window {
    __bfVario?: unknown;
  }
}
