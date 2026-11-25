/**
 * The ellipsoids of the trends in the main 3D (ТЗ 7.35 Д-15; ADR 0022 §9–10): a stored trend shown (the view key
 * `ellipsoids`, `trend.show`) — a layer `trend:<id>` (`viz/ellipsoid/layer.ts`, one structure in the colour of the
 * trend); an open window «Тренд» — a layer `trend:w:<window>` of its edit as typed, with the handles (the same
 * `EllipsoidHandlesControl` as a variogram model, the key `trend:handles`): the arcs change the angles, the axis ends
 * the ratios and the size shown (`state.withModel`); the stored layer of that trend steps aside while its window is
 * open. A trend without a centre stands at the centre of the view when it is first shown (kept for the session).
 * A drag writes the edit of the window (its form follows in the same frame); «Сохранить» stores it. A structural trend
 * has no ellipsoid (it is skipped here): its discs are `discScene.ts`, attached with this scene. A lazy chunk
 * with three.js (`bind.ts` attaches it to every scene).
 */
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";
import { windowStore } from "../../shell/windows";
import type { Vec3 } from "../../viz/core/vec3";
import type { HandleKind, Model } from "../../viz/ellipsoid/drag";
import { buildEllipsoid, type StructureView } from "../../viz/ellipsoid/build";
import { EllipsoidLayer } from "../../viz/ellipsoid/layer";
import type { Scene } from "../../viz/Scene";
import { viewStore } from "../scene/bind";
import { sceneUi } from "../scene/sceneHost";
import { EllipsoidHandlesControl, type EllipsoidHost, type EllipsoidTarget } from "../variography/handles";
import { attachDiscScene } from "./discScene";
import { checkValues, ELLIPSOIDS_KEY, modelOf, setDragging, setEdit, shownObjects, TREND_WINDOW, trendsStore, withModel } from "./state";

export const trendLayerKey = (id: number): string => `trend:${id}`;
export const editLayerKey = (win: string): string => `trend:w:${win}`;

/** What one layer shows: its key, the view of the structure, the centre and (a window) the owner of the handles. */
interface Item {
  key: string;
  view: StructureView;
  centre: Vec3;
  win: string | null;
}

interface Shown {
  layer: EllipsoidLayer;
  shape: string;
  poses: string;
  stale: boolean;
}

const centreKey = (trendId: number | null, win: string): string => (trendId !== null ? `t:${trendId}` : `w:${win}`);

export class TrendScene implements EllipsoidHost {
  private readonly shown = new Map<string, Shown>();
  private readonly stops: (() => void)[] = [];
  /** the centres of the ellipsoids without one: the centre of the view when first shown (absolute, this session) */
  private readonly kept = new Map<string, Vec3>();
  private disposed = false;
  readonly handles: EllipsoidHandlesControl;

  constructor(private readonly scene: Scene) {
    this.handles = new EllipsoidHandlesControl(scene, this, "trend:handles");
    const sync = () => this.sync();
    this.stops.push(trendsStore.subscribe(sync), windowStore.subscribe(sync), viewStore.subscribe(sync), scene.onRestored(() => this.restore()));
    this.sync();
  }

  /** A centre of the edit or the trend; none given — the one kept, else the centre of the view now (kept). */
  private centreOf(given: Vec3 | null, key: string): Vec3 | null {
    if (given) return given;
    const kept = this.kept.get(key);
    if (kept) return kept;
    const target = this.scene.worldCamera?.()?.target;
    if (!target || !target.every(Number.isFinite)) return null;
    const c: Vec3 = [target[0], target[1], target[2]];
    this.kept.set(key, c);
    return c;
  }

  /** The open windows «Тренд» with their ellipsoid on, the one on top first. */
  private windows(): string[] {
    const edits = trendsStore.get().edits;
    return windowStore
      .get()
      .windows.filter((w) => w.kind === TREND_WINDOW && edits[w.id]?.show === true && edits[w.id]?.kind !== "structural")
      .sort((a, b) => b.z - a.z)
      .map((w) => w.id);
  }

  /** What is to be drawn now: the windows (their edits as typed), then the stored trends shown and not open. */
  items(): Item[] {
    const st = trendsStore.get();
    const out: Item[] = [];
    const open = new Set<number>();
    for (const win of this.windows()) {
      const e = st.edits[win];
      if (e.trendId !== null) open.add(e.trendId);
      if (e.kind === "structural") continue; // no ellipsoid: its discs (`discScene.ts`)
      const c = checkValues(e.values);
      if (!c.ok) continue;
      const centre = this.centreOf(c.shape.centre, centreKey(e.trendId, win));
      if (!centre) continue;
      const m = modelOf(c.shape);
      out.push({ key: editLayerKey(win), view: { ...m, color: c.shape.color }, centre, win });
    }
    // windows of stored trends hide their layer even when their own ellipsoid is off
    for (const [win, e] of Object.entries(st.edits)) if (e.trendId !== null && windowStore.get().windows.some((w) => w.id === win)) open.add(e.trendId);
    for (const id of shownObjects(viewStore.get()[ELLIPSOIDS_KEY])) {
      const t = st.trends.find((x) => x.id === id);
      if (!t || t.kind === "structural" || open.has(id)) continue;
      const centre = this.centreOf(t.centre, centreKey(t.id, ""));
      if (!centre) continue;
      out.push({ key: trendLayerKey(id), view: { ...modelOf(t), color: t.color }, centre, win: null });
    }
    return out;
  }

  focused(): boolean {
    const wins = windowStore.get().windows;
    if (!wins.length) return false;
    const top = wins.reduce((a, b) => (b.z > a.z ? b : a));
    return top.kind === TREND_WINDOW && this.shown.has(editLayerKey(top.id));
  }

  target(): EllipsoidTarget | null {
    for (const win of this.windows()) {
      const st = this.shown.get(editLayerKey(win));
      if (!st) continue;
      const pose = st.layer.pose(0);
      return { id: win, layer: st.layer, index: 0, model: { angles: { ang1: pose.frame.ang1, ang2: pose.frame.ang2, ang3: pose.frame.ang3 }, ranges: { ...pose.ranges } }, min: 0 };
    }
    return null;
  }

  write(win: string, _index: number, m: Model): void {
    if (!trendsStore.get().edits[win]) return;
    setEdit(win, (e) => ({ values: withModel(e.values, m), dirty: true, fromModel: null }));
  }

  dragging(win: string, kind: HandleKind | null): void {
    setDragging(kind ? win : null);
  }

  /** For the E2E hook and the tests: the ellipsoids drawn. */
  stats(): { key: string; ranges: number[]; angles: number[]; centre: number[]; color: string }[] {
    return [...this.shown.entries()].map(([key, s]) => {
      const p = s.layer.pose(0);
      return { key, ranges: [p.ranges.a1, p.ranges.a2, p.ranges.a3], angles: [p.frame.ang1, p.frame.ang2, p.frame.ang3], centre: [...s.layer.build.centre], color: p.color };
    });
  }

  sync(): void {
    if (this.disposed) return;
    const items = this.items();
    const wanted = new Set(items.map((i) => i.key));
    const dragging = trendsStore.get().dragging !== null;
    let changed = false;
    for (const key of [...this.shown.keys()]) if (!wanted.has(key)) changed = this.drop(key) || changed;
    for (const it of items) {
      const shape = JSON.stringify(it.centre);
      const poses = JSON.stringify(it.view);
      let st = this.shown.get(it.key);
      if (!st || st.shape !== shape) {
        if (st) this.scene.removeLayer(it.key, false);
        const layer = this.make(it);
        if (!layer) {
          this.shown.delete(it.key);
          continue;
        }
        st = { layer, shape, poses, stale: false };
        this.shown.set(it.key, st);
        this.scene.addLayer(it.key, layer);
        changed = true;
      } else if (st.poses !== poses) {
        st.layer.setPoses([it.view]);
        st.poses = poses;
        st.stale = true;
        changed = true;
      }
      if (st.stale && !dragging) {
        st.stale = false;
        this.scene.layerChanged?.();
      }
    }
    if (changed) this.scene.invalidate();
    this.handles.refresh();
  }

  private make(it: Item): EllipsoidLayer | null {
    try {
      return new EllipsoidLayer(buildEllipsoid({ centre: it.centre, structures: [it.view], selected: 0 }), { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
    } catch (e) {
      sceneUi.set({ error: `Эллипсоид тренда не показан в 3D: ${errorText(e)}` });
      return null;
    }
  }

  private drop(key: string): boolean {
    if (!this.shown.delete(key)) return false;
    this.scene.removeLayer(key, false); // the scene disposes the layer it removes
    return true;
  }

  private restore(): void {
    this.handles.lost();
    this.shown.clear();
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.handles.dispose();
    this.stops.forEach((fn) => fn());
    for (const key of [...this.shown.keys()]) this.drop(key);
    this.scene.invalidate();
  }
}

/** Attaches the trends to a scene — the ellipsoids of the global ones, the discs of the structural ones (`discScene.ts`); gives what detaches them. */
export function attachTrendScene(scene: Scene): () => void {
  const s = new TrendScene(scene);
  const discs = attachDiscScene(scene);
  const hook = bridge()?.flags?.().e2e === true && typeof window !== "undefined";
  if (hook) window.__bfTrends = { ellipsoids: () => s.stats(), handles: () => s.handles.stats(), point: (kind: HandleKind, deg?: number) => s.handles.point(kind, deg) };
  return () => {
    discs.dispose();
    s.dispose();
    if (hook) delete window.__bfTrends;
  };
}

declare global {
  interface Window {
    __bfTrends?: unknown;
  }
}
