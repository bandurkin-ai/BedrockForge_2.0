/**
 * The handles of an anisotropy ellipsoid in the main 3D (ТЗ 7.35 Д-14; ADR 0022 §9, решения 18–19; образец —
 * `modules/swath/handles.ts`, Д-01): the ring of ang1, the arcs of ang2 and ang3, the knobs of a1, a2, a3 of the
 * structure chosen (`viz/ellipsoid/drag.ts`, drawn by `viz/ellipsoid/handle.ts`). In the chain of the handles of the
 * scene (`viz/core/handleChain.ts`): first while its window is in focus; within 8 px of a handle a press is a drag,
 * else the scene has the pointer. A drag: the ray of the cursor → `dragModel` (only its own parameter, steps of 1°,
 * `Shift` — 5°; a range — 3 significant digits, not less than L / 2, `Ctrl` — all three in one ratio) → `host.write`
 * (the one store of the model of the window: the table, the curves and the ellipsoid follow in the same frame; the
 * mark `vario:edit`). No request to the core here: the window computes after the release (`host.release`).
 * Generic over its host: the model of a window (`scene.ts`) or a trend (16.20). Part of the lazy chunk of `scene.ts`.
 */
import type { Scene } from "../../viz/Scene";
import { addHandles, removeHandles, type ChainHandler } from "../../viz/core/handleChain";
import type { ToolPointer } from "../../viz/core/toolInput";
import type { Vec3 } from "../../viz/core/vec3";
import { dragModel, handlesOf, hitHandle, isArc, pointAt, sameModel, type Handle, type HandleKind, type Model } from "../../viz/ellipsoid/drag";
import { EllipsoidHandles } from "../../viz/ellipsoid/handle";
import type { EllipsoidLayer } from "../../viz/ellipsoid/layer";
import { projectToScreen } from "../../viz/pick/ray";
import type { Ray } from "../../viz/section/math";

export interface EllipsoidTarget {
  /** the owner: a window id (a model) or `trend:<id>` */
  id: string;
  layer: EllipsoidLayer;
  /** the structure with the handles */
  index: number;
  /** its angles and ranges now (as the store has them) */
  model: Model;
  /** the least range of a drag, m (L / 2); 0 — the default of `drag.ts` */
  min: number;
}

/** What the handles need of the ellipsoids in the scene. */
export interface EllipsoidHost {
  /** the ellipsoid with handles; null — none shown */
  target(): EllipsoidTarget | null;
  /** a new model of the structure (each move of a drag, a cancel) */
  write(id: string, index: number, model: Model): void;
  /** a drag began (`kind`) or ended (null); `changed` — the release left another model */
  dragging?(id: string, kind: HandleKind | null, changed: boolean): void;
  /** its window is in focus: the chain asks it first */
  focused?(): boolean;
}

type HandleScene = Pick<Scene, "cursorRay" | "projectView" | "addLayer" | "removeLayer" | "invalidate" | "resolution" | "setHandles"> & {
  canvas: { style: { cursor: string }; getBoundingClientRect?: () => { left: number; top: number } };
};

interface Drag {
  id: string;
  index: number;
  handle: Handle;
  start: Model;
  now: Model;
  from: Ray;
  min: number;
}

const copy = (m: Model): Model => ({ angles: { ...m.angles }, ranges: { ...m.ranges } });

export class EllipsoidHandlesControl implements ChainHandler {
  private view: EllipsoidHandles | null = null;
  private viewOrigin = "";
  private hover: HandleKind | null = null;
  private drag: Drag | null = null;
  private cursorWas: string | null = null;
  private disposed = false;

  constructor(
    private readonly scene: HandleScene,
    private readonly host: EllipsoidHost,
    readonly key = "vario:handles",
  ) {
    addHandles(scene, this);
  }

  focused(): boolean {
    return this.drag !== null || this.host.focused?.() === true;
  }

  /** For the tests and the E2E hook: the handles drawn and the drag now. */
  stats(): { shown: EllipsoidHandles["shown"]; dragging: HandleKind | null; hover: HandleKind | null } {
    return { shown: this.view?.shown ?? [], dragging: this.drag?.handle.kind ?? null, hover: this.hover };
  }

  private handlesFor(t: Pick<EllipsoidTarget, "layer" | "model">, m: Model = t.model): Handle[] {
    return handlesOf(m.angles, m.ranges, t.layer.build.centre);
  }

  /** The handles again (the model, the structure or the window changed); a drag draws its own. */
  refresh(): void {
    if (this.disposed) return;
    const t = this.host.target();
    if (!t) return this.hide();
    this.draw(t, t.model, this.drag?.handle.kind ?? null);
  }

  private draw(t: Pick<EllipsoidTarget, "layer" | "model">, m: Model, active: HandleKind | null): void {
    const centre = t.layer.build.centre;
    const key = JSON.stringify(t.layer.build.origin);
    if (!this.view || this.viewOrigin !== key) {
      if (this.view) this.scene.removeLayer(this.key, false);
      this.view = new EllipsoidHandles(centre, this.scene.resolution);
      this.viewOrigin = key;
      this.scene.addLayer(this.key, this.view);
    }
    this.view.update(this.handlesFor(t, m), this.hover, active);
    this.scene.invalidate();
  }

  private hide(): void {
    this.setHover(null);
    if (!this.view) return;
    this.scene.removeLayer(this.key, false);
    this.view = null;
    this.viewOrigin = "";
    this.scene.invalidate();
  }

  private setHover(kind: HandleKind | null): void {
    if (kind === this.hover) return;
    this.hover = kind;
    const style = this.scene.canvas.style;
    if (kind && this.cursorWas === null) {
      this.cursorWas = style.cursor;
      style.cursor = "grab";
    } else if (!kind && this.cursorWas !== null) {
      style.cursor = this.cursorWas;
      this.cursorWas = null;
    }
  }

  private hitOf(t: EllipsoidTarget, x: number, y: number): HandleKind | null {
    const view = this.scene.projectView();
    return view ? hitHandle(this.handlesFor(t), view, x, y) : null;
  }

  hits(x: number, y: number): boolean {
    const t = this.disposed ? null : this.host.target();
    return !!t && this.hitOf(t, x, y) !== null;
  }

  pointer(e: ToolPointer): boolean {
    if (this.disposed) return false;
    if (this.drag) {
      if (e.type === "move") this.dragTo(e);
      else if (e.type === "up") this.release();
      return e.type !== "leave";
    }
    const t = this.host.target();
    if (e.type === "leave") {
      const was = this.hover;
      this.setHover(null);
      if (t && was) this.draw(t, t.model, null);
      return false;
    }
    if (!t) {
      this.setHover(null);
      return false;
    }
    const kind = e.type === "move" || e.type === "down" ? this.hitOf(t, e.x, e.y) : null;
    if (e.type === "move") {
      if (kind !== this.hover) {
        this.setHover(kind);
        this.draw(t, t.model, null);
      }
      return false;
    }
    if (e.type !== "down" || e.button !== 0 || !kind) return false;
    const from = this.scene.cursorRay(e.x, e.y);
    if (!from) return false;
    const handle = this.handlesFor(t).find((h) => h.kind === kind)!;
    const start = copy(t.model);
    this.drag = { id: t.id, index: t.index, handle, start, now: start, from, min: t.min };
    this.scene.canvas.style.cursor = "grabbing";
    this.draw(t, start, kind);
    this.host.dragging?.(t.id, kind, false);
    return true;
  }

  private dragTo(e: ToolPointer): void {
    const d = this.drag!;
    const ray = this.scene.cursorRay(e.x, e.y);
    const next = ray && dragModel(d.start, d.handle, d.from, ray, { shift: e.shift, ctrl: e.ctrl, ...(d.min > 0 ? { min: d.min } : {}) });
    if (!next || sameModel(next, d.now)) return;
    d.now = next;
    this.host.write(d.id, d.index, next);
  }

  private release(): void {
    const d = this.drag!;
    this.drag = null;
    this.scene.canvas.style.cursor = this.hover ? "grab" : (this.cursorWas ?? "");
    this.host.dragging?.(d.id, null, !sameModel(d.now, d.start));
    this.refresh();
  }

  /** The editor's tool came in the middle of a drag: the model comes back as it was at the press. */
  cancel(): void {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    if (!sameModel(d.now, d.start)) this.host.write(d.id, d.index, d.start);
    this.host.dragging?.(d.id, null, false);
    this.refresh();
  }

  /** After a lost context the scene dropped every layer: drawn anew by the next `refresh`. */
  lost(): void {
    this.view = null;
    this.viewOrigin = "";
    this.cancel();
  }

  /**
   * For the E2E hook: a point of a handle in client px — an arc at `deg` (its angle now when omitted), a knob at
   * its end; null — no handles or off the screen.
   */
  point(kind: HandleKind, deg?: number): { x: number; y: number } | null {
    const t = this.host.target();
    const view = this.scene.projectView();
    if (!t || !view) return null;
    const h = this.handlesFor(t).find((x) => x.kind === kind)!;
    const p: Vec3 = isArc(h) ? pointAt(h, deg ?? h.at) : h.at;
    const s = projectToScreen(view.matrix, p[0], p[1], p[2], view.width, view.height);
    if (!s) return null;
    const r = this.scene.canvas.getBoundingClientRect?.() ?? { left: 0, top: 0 };
    return { x: r.left + s[0], y: r.top + s[1] };
  }

  dispose(): void {
    this.cancel();
    this.hide();
    this.disposed = true;
    removeHandles(this.scene, this);
  }
}
