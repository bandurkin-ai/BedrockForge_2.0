/**
 * Input of the 3D view with an editing tool (ADR 0015 §5): the automaton between the pointer events of the
 * canvas, the tool of a session and the scene itself. Without a tool it is the old behaviour — a click (press
 * and release of the left button within 4 px and 600 ms) picks, moves feed the rubber band of a section, a
 * drag turns the view. With a tool the session gets every event first: a press it takes (a vertex, a box)
 * holds the camera until the release, and the release that did not move is also a click; a right click
 * without a drag (a drag pans) and a double click go to the tool. Plus the float64 bridge from the screen to
 * project coordinates: the ray under the cursor and the matrix project → clip (Z exaggeration undone). Pure.
 */
import type { Origin } from "../origin";
import type { Mat4 } from "../pick/ray";
import { toNdc } from "../pick/ray";
import { viewPlanePoint, type Ray } from "../section/math";
import { screenRay, viewDirection, type CameraState, type ViewOptions } from "./views";
import type { Vec3 } from "./vec3";
import { attachPointer, CLICK_MOVE_PX, CLICK_MS, type RawPointer } from "./pointer";

/** An event for a tool: `EditPointer` of `edit/tools.ts` has this shape. */
export interface ToolPointer {
  /** `leave` — the cursor left the canvas (the tool drops its hover) */
  type: "down" | "move" | "up" | "click" | "dblclick" | "leave";
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  button: number;
  shift: boolean;
  ctrl: boolean;
}

/** An editing tool: `true` — the event is taken (the view neither turns nor picks). */
export interface ToolHandler {
  pointer(e: ToolPointer): boolean;
  /**
   * Another tool or a general tool of the view (a section line, `Scene.setDrawing`) takes the pointer: the tool ends
   * what it holds (a line being drawn is finished, a drag is dropped). Called once per hand-over.
   */
  deactivate?(): void;
}

/** What the scene does with the events no tool took. */
export interface SceneInput {
  click(x: number, y: number): void;
  move(x: number, y: number): void;
  leave(): void;
  /** the camera controls on or off (off while a tool drags) */
  camera(enabled: boolean): void;
}

interface Down {
  x: number;
  y: number;
  t: number;
  button: number;
  /** the tool took the press: the drag is its own */
  taken: boolean;
}

const tp = (type: ToolPointer["type"], e: RawPointer, button = e.button): ToolPointer => ({ type, x: e.x, y: e.y, clientX: e.clientX, clientY: e.clientY, button, shift: e.shift, ctrl: e.ctrl });

export class ToolInput {
  private tool: ToolHandler | null = null;
  private down: Down | null = null;

  constructor(private readonly scene: SceneInput) {}

  get active(): boolean {
    return this.tool !== null;
  }

  /** Listens to the canvas (capture phase: before the camera controls). */
  attach(el: HTMLElement): () => void {
    return attachPointer(el, (e) => this.feed(e));
  }

  /** A tool in or out; a drag of the old one ends (the camera comes back). */
  setTool(tool: ToolHandler | null): void {
    if (this.down?.taken) this.scene.camera(true);
    this.down = null;
    this.tool = tool;
  }

  /** One event of the canvas; `true` — taken, the camera controls must not see it. */
  feed(e: RawPointer): boolean {
    switch (e.type) {
      case "down":
        return this.onDown(e);
      case "move":
        return this.onMove(e);
      case "up":
        return this.onUp(e);
      case "dblclick":
        return this.tool ? this.tool.pointer(tp("dblclick", e, 0)) : false;
      default:
        this.scene.leave();
        this.tool?.pointer(tp("leave", e, -1));
        return false;
    }
  }

  private still(d: Down, e: RawPointer): boolean {
    return Math.hypot(e.clientX - d.x, e.clientY - d.y) < CLICK_MOVE_PX;
  }

  private onDown(e: RawPointer): boolean {
    // a second press while one is held (another button): the first one is over
    if (this.down?.taken) this.scene.camera(true);
    const d: Down = { x: e.clientX, y: e.clientY, t: e.t, button: e.button, taken: false };
    this.down = d;
    if (!this.tool || e.button !== 0) return false;
    d.taken = this.tool.pointer(tp("down", e));
    if (d.taken) this.scene.camera(false);
    return d.taken;
  }

  private onMove(e: RawPointer): boolean {
    const d = this.down;
    if (d?.taken) {
      this.tool?.pointer(tp("move", e, 0));
      return true;
    }
    // the view is being dragged: no hover, no rubber band
    if (d && !this.still(d, e)) return false;
    this.scene.move(e.x, e.y);
    return this.tool ? this.tool.pointer(tp("move", e, -1)) : false;
  }

  private onUp(e: RawPointer): boolean {
    const d = this.down;
    if (!d || d.button !== e.button) return false;
    this.down = null;
    const click = this.still(d, e) && e.t - d.t < CLICK_MS;
    if (d.taken) {
      this.tool?.pointer(tp("up", e));
      this.scene.camera(true);
      // a press without a move was a click (selection) for the tool
      if (click) this.tool?.pointer(tp("click", e));
      return true;
    }
    if (!click) return false;
    if (e.button === 0) {
      if (!this.tool?.pointer(tp("click", e))) this.scene.click(e.x, e.y);
    } else if (e.button === 2) this.tool?.pointer(tp("click", e));
    // the camera controls had the press: they see the release too
    return false;
  }
}

/**
 * The ray under a canvas point (CSS px), project coordinates float64: the rendered ray from the camera with the
 * scene origin added back and the Z exaggeration undone (the direction is normalized). Null before the origin.
 */
export function projectRay(cam: CameraState, opts: ViewOptions, size: { w: number; h: number }, origin: Origin | null, vexag: number, px: number, py: number): Ray | null {
  if (!origin) return null;
  const r = screenRay(cam, opts, ...toNdc(px, py, size.w, size.h));
  const k = vexag || 1;
  const o: Vec3 = [r.origin[0] + origin.x, r.origin[1] + origin.y, r.origin[2] / k + origin.z];
  const d: Vec3 = [r.dir[0], r.dir[1], r.dir[2] / k];
  const n = Math.hypot(d[0], d[1], d[2]) || 1;
  return { origin: o, dir: [d[0] / n, d[1] / n, d[2] / n] };
}

/**
 * clip ← project coordinates (column-major, float64): `vp` (clip ← rendered) after the scene origin is taken off
 * and Z stretched by the exaggeration. The translation is folded in float64, so UTM coordinates lose nothing.
 */
export function projectMatrix(vp: ArrayLike<number>, origin: Origin, vexag: number): Mat4 {
  const m = new Float64Array(16);
  for (let r = 0; r < 4; r++) {
    m[r] = vp[r];
    m[4 + r] = vp[4 + r];
    m[8 + r] = vp[8 + r] * vexag;
    m[12 + r] = vp[12 + r] - origin.x * vp[r] - origin.y * vp[4 + r] - origin.z * vexag * vp[8 + r];
  }
  return m;
}

/** The view as an editing session sees it (`ViewTransform` of `edit/snap.ts`). */
export interface ProjectView {
  matrix: Mat4;
  width: number;
  height: number;
  /** inside the section window (project coordinates) */
  visible?: (x: number, y: number, z: number) => boolean;
}

/** The view of the last frame in project coordinates; `inWindow` — the section window in scene coordinates (Z not stretched). */
export function projectView(vp: Mat4 | null, origin: Origin | null, vexag: number, size: { w: number; h: number }, inWindow?: (x: number, y: number, z: number) => boolean): ProjectView | null {
  if (!vp || !origin) return null;
  const view: ProjectView = { matrix: projectMatrix(vp, origin, vexag), width: size.w, height: size.h };
  if (inWindow) view.visible = (x, y, z) => inWindow(x - origin.x, y - origin.y, z - origin.z);
  return view;
}

/** A point of the screen on the plane through the view centre (BF `pickPoint`), rendered space. */
export function viewPlaneAt(cam: CameraState, opts: ViewOptions, size: { w: number; h: number }, px: number, py: number): Vec3 | null {
  return viewPlanePoint(screenRay(cam, opts, ...toNdc(px, py, size.w, size.h)), cam.target, viewDirection(cam));
}

/** The plan point of a click while a section line is drawn (project coordinates), or null. */
export function groundPoint(cam: CameraState, opts: ViewOptions, size: { w: number; h: number }, origin: Origin | null, px: number, py: number): { x: number; y: number } | null {
  const p = origin ? viewPlaneAt(cam, opts, size, px, py) : null;
  return p && origin ? { x: p[0] + origin.x, y: p[1] + origin.y } : null;
}
