/**
 * The handles of Д-01 in the main 3D (ТЗ 7.35 Д-01; ADR 0019 §6): at the swath shown in 3D and chosen (its window
 * in focus or its tree row) — the ring of the azimuth, the half circle of the dip, the circle of the roll
 * (`viz/swath/drag.ts`, drawn by `viz/swath/handle.ts`). The pointer comes after the tool of the string editor
 * and before the scene (`Scene.setHandles`): within 8 px of a handle a press is a drag, else the scene turns the
 * view, picks a collar, and so on. A drag: the ray ∩ the plane of the handle → the angle, steps of 1° (`Shift` —
 * 5°); every move turns the cuboid and its swaths about the centre (`SwathLayer.setPose`) and shows the angles in
 * the window (`dragStore`) — no request to the core. The release: the turned pose is held until the next answer
 * and `stats.swath {swath_id, azimuth, dip, roll}` runs once (the window takes the settings, saves them and
 * computes once; X, Y, Z become «по углам»). Part of the lazy chunk of `scene.ts`.
 */
import type { Ray } from "../../viz/section/math";
import type { Scene } from "../../viz/Scene";
import type { ToolHandler, ToolPointer } from "../../viz/core/toolInput";
import { addHandles, removeHandles } from "../../viz/core/handleChain";
import { dragAngles, handlesOf, hitHandle, rotationBetween, sameAngles, type Angles, type Handle, type HandleKind } from "../../viz/swath/drag";
import { frameOf } from "../../viz/swath/frame";
import { SwathHandles } from "../../viz/swath/handle";
import type { SwathLayer } from "../../viz/swath/layer";
import { setDrag } from "./state";

export const HANDLES_KEY = "swath:handles";

export interface HandleTarget {
  id: number;
  layer: SwathLayer;
  /** the angles of its box now (or held after a release) */
  angles: Angles;
}

/** What the handles need of the swaths in the scene (`SwathScene`). */
export interface HandlesHost {
  /** the swath with handles; null — none chosen, not shown or not drawn yet */
  target(): HandleTarget | null;
  /** keeps the turned pose and the angles of a swath until its next answer; null — let go (drawn as built) */
  hold(id: number, pose: { rotation: number[]; angles: Angles } | null): void;
  /** `stats.swath {swath_id, azimuth, dip, roll}`; false — the command failed */
  run(args: { swath_id: number } & Angles): Promise<boolean>;
}

type HandleScene = Pick<Scene, "cursorRay" | "projectView" | "addLayer" | "removeLayer" | "invalidate" | "resolution" | "setHandles"> & { canvas: { style: { cursor: string } } };

interface Drag {
  id: number;
  layer: SwathLayer;
  handle: Handle;
  start: Angles;
  now: Angles;
  from: Ray;
}

const pick = (a: Angles): Angles => ({ azimuth: a.azimuth, dip: a.dip, roll: a.roll });

export class SwathHandlesControl implements ToolHandler {
  private view: SwathHandles | null = null;
  private viewOrigin = "";
  private hover: HandleKind | null = null;
  private drag: Drag | null = null;
  /** grows with every drag: the answer of a release lets go only if no drag came after it */
  private generation = 0;
  /** the cursor of the canvas before a handle was under it */
  private cursorWas: string | null = null;
  private disposed = false;

  constructor(
    private readonly scene: HandleScene,
    private readonly host: HandlesHost,
  ) {
    addHandles(scene, this);
  }

  /** For the tests and the E2E hook: the handles drawn (kind, angle, lit) and the drag now. */
  stats(): { shown: { kind: HandleKind; at: number; lit: boolean }[]; dragging: HandleKind | null } {
    return { shown: this.view?.shown ?? [], dragging: this.drag?.handle.kind ?? null };
  }

  /** The handles again (the swath chosen, its build or its angles changed); a drag keeps its own. */
  refresh(): void {
    if (this.disposed || this.drag) return;
    const t = this.host.target();
    if (!t) return this.hide();
    this.draw(t.layer, t.angles, null);
  }

  private handlesFor(layer: SwathLayer, a: Angles): Handle[] {
    return handlesOf(a, layer.build.centre, layer.build.half);
  }

  private draw(layer: SwathLayer, a: Angles, active: HandleKind | null): void {
    const key = JSON.stringify(layer.build.origin);
    if (!this.view || this.viewOrigin !== key) {
      if (this.view) this.scene.removeLayer(HANDLES_KEY, false);
      this.view = new SwathHandles(layer.build.centre, this.scene.resolution);
      this.viewOrigin = key;
      this.scene.addLayer(HANDLES_KEY, this.view);
    }
    this.view.update(this.handlesFor(layer, a), this.hover, active);
    this.scene.invalidate();
  }

  private hide(): void {
    this.setHover(null);
    if (!this.view) return;
    this.scene.removeLayer(HANDLES_KEY, false);
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

  /** The handle under a canvas point of the target, or null. */
  private hit(t: HandleTarget, x: number, y: number): HandleKind | null {
    const view = this.scene.projectView();
    return view ? hitHandle(this.handlesFor(t.layer, t.angles), view, x, y) : null;
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
      if (t && was) this.draw(t.layer, t.angles, null);
      return false;
    }
    if (!t) {
      this.setHover(null);
      return false;
    }
    const kind = e.type === "move" || e.type === "down" ? this.hit(t, e.x, e.y) : null;
    if (e.type === "move") {
      if (kind !== this.hover) {
        this.setHover(kind);
        this.draw(t.layer, t.angles, null);
      }
      return false;
    }
    if (e.type !== "down" || e.button !== 0 || !kind) return false;
    const from = this.scene.cursorRay(e.x, e.y);
    if (!from) return false;
    const handle = this.handlesFor(t.layer, t.angles).find((h) => h.kind === kind)!;
    const start = pick(t.angles);
    this.drag = { id: t.id, layer: t.layer, handle, start, now: start, from };
    this.generation++;
    this.scene.canvas.style.cursor = "grabbing";
    this.draw(t.layer, start, kind);
    setDrag({ id: t.id, ...start });
    return true;
  }

  private dragTo(e: ToolPointer): void {
    const d = this.drag!;
    const ray = this.scene.cursorRay(e.x, e.y);
    const next = ray && dragAngles(d.start, d.handle, d.from, ray, e.shift);
    if (!next || sameAngles(next, d.now)) return;
    d.now = next;
    d.layer.setPose(rotationBetween(d.layer.build, frameOf(next.azimuth, next.dip, next.roll)));
    this.draw(d.layer, next, d.handle.kind);
    setDrag({ id: d.id, ...next });
  }

  private release(): void {
    const d = this.drag!;
    this.drag = null;
    this.scene.canvas.style.cursor = this.hover ? "grab" : (this.cursorWas ?? "");
    if (sameAngles(d.now, d.start)) {
      d.layer.setPose(null);
      setDrag(null);
      this.refresh();
      return;
    }
    const rotation = rotationBetween(d.layer.build, frameOf(d.now.azimuth, d.now.dip, d.now.roll));
    this.host.hold(d.id, { rotation, angles: d.now });
    this.refresh();
    const mine = this.generation;
    void this.host
      .run({ swath_id: d.id, ...d.now })
      .catch(() => false)
      .then((ok) => {
        // another drag began before the answer: the angles and the pose are its own
        if (mine !== this.generation) return;
        setDrag(null);
        if (ok || this.disposed) return;
        // the command failed: the box comes back to the angles it has
        this.host.hold(d.id, null);
        this.refresh();
      });
  }

  /** After a lost context the scene dropped every layer, the handles too: drawn anew by the next `refresh`. */
  lost(): void {
    this.view = null;
    this.viewOrigin = "";
    this.cancel();
  }

  /** The editor's tool came in the middle of a drag: the box comes back as it was. */
  cancel(): void {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    d.layer.setPose(null);
    setDrag(null);
    this.refresh();
  }

  dispose(): void {
    this.cancel();
    this.hide();
    this.disposed = true;
    removeHandles(this.scene, this);
  }
}
