/**
 * The section of the 3D window apart from `Scene.ts` (ТЗ 7.14, 8.1; ADR 0016 §4): the current plane (a line of
 * 7.14 or a section of a series, a polyline segment), clipping by its window (`SharedClip`), the trace while
 * clipping is off and the rubber band of a line being drawn (`SectionOverlay`), the frame of the «section» view
 * and the exaggeration the plane allows (×1 for an inclined one, ADR 0016 решение 7), the lock of the view on the
 * plane («Зафиксировать вид», `core/lockView.ts`). The scene owns the camera and the origin and calls `refresh`
 * when the origin, the content or the exaggeration change, `follow` when the section or the exaggeration do.
 */
import type { Box3, WindowTest } from "../core/bounds";
import { freeCamera, followSection, lockedView, lockOn, type ViewLock } from "../core/lockView";
import type { Vec3 } from "../core/vec3";
import { pixelsPerUnit, type CameraState, type SectionFrame, type ViewOptions } from "../core/views";
import { viewPlaneAt } from "../core/toolInput";
import type { Origin } from "../origin";
import { SharedClip } from "./clip";
import { DRAW_OFF, type DrawState } from "./draw";
import type { SectionLine } from "./math";
import { SectionOverlay } from "./overlay";
import { allowedVexag, planeFromLine, type SectionPlane } from "./plane";

export class SectionRig {
  readonly clip = new SharedClip();
  readonly overlay = new SectionOverlay();
  /** the free line of 7.14 when it is the section (the view key `section.line`), null for a section of a series */
  line: SectionLine | null = null;
  plane: SectionPlane | null = null;
  clipOn = false;
  drawing: DrawState = DRAW_OFF;
  /** «Зафиксировать вид»: the plane and the direction the view is locked on */
  lock: ViewLock | null = null;

  /** A new section: a plane (with the line it came from, if any) and clipping. */
  set(plane: SectionPlane | null, clip: boolean, line: SectionLine | null = null): void {
    this.plane = plane;
    this.line = plane ? line : null;
    this.clipOn = clip && plane !== null;
  }

  /** The section of a line of 7.14 (null — none). */
  setLine(line: SectionLine | null, clip: boolean): void {
    this.set(line ? planeFromLine(line) : null, clip, line);
  }

  /** The planes and the trace for the scene origin, the content (scene coordinates) and the exaggeration. */
  refresh(origin: Origin | null, content: Box3 | null, vexag: number): void {
    this.clip.update(this.plane, this.clipOn, origin, vexag);
    this.overlay.setLine(this.plane, this.clipOn, origin, content);
  }

  /** Is a scene point (Z not stretched) inside the window; everything is without clipping. */
  readonly inWindow = (x: number, y: number, z: number): boolean => this.clip.inWindow(x, y, z);

  /** «Вписать» with clipping: only the boxes reaching into the window. */
  boxTest(): WindowTest | null {
    return this.clip.boxTest();
  }

  /** The frame of the «section» view, null — no section yet. */
  frame(origin: Origin | null): SectionFrame | null {
    return this.plane && origin ? { plane: this.plane, line: this.line, origin } : null;
  }

  /** The exaggeration the view may show with the current plane. */
  vexag(requested: number): number {
    return allowedVexag(this.plane, requested);
  }

  /**
   * The rubber band of a line being drawn from its first point to the cursor (`px` < 0 — no cursor) on the plane
   * through the view centre; false — nothing to draw (no first point, no origin).
   */
  draft(cam: CameraState, opts: ViewOptions, size: { w: number; h: number }, origin: Origin | null, px: number, py: number): boolean {
    const first = this.drawing.stage === "second" ? this.drawing.first : null;
    if (!first || !origin) return false;
    const start: Vec3 = [first.x - origin.x, first.y - origin.y, cam.target[2] / (opts.vexag || 1)];
    const p = px >= 0 ? viewPlaneAt(cam, opts, size, px, py) : null;
    const end: Vec3 | null = p ? [p[0], p[1], start[2]] : null;
    const mark = 6 / Math.max(pixelsPerUnit(cam, cam.target, size.h, opts), 1e-9);
    this.overlay.setDraft(start, end, mark);
    return true;
  }

  /** Locks the view on the current plane: the camera to apply, null — no plane or origin (nothing changes). */
  lockOn(cam: CameraState, origin: Origin | null, vexag: number): CameraState | null {
    if (!this.plane || !origin) return null;
    const r = lockOn(cam, this.plane, origin, vexag);
    this.lock = r.lock;
    return r.camera;
  }

  /** Frees the view: the camera with Z up, null — it was not locked. */
  unlock(cam: CameraState): CameraState | null {
    if (!this.lock) return null;
    this.lock = null;
    return freeCamera(cam);
  }

  /** The locked view after a new section or exaggeration (a step: moved along v̂); null — not locked or no plane. */
  follow(cam: CameraState, origin: Origin | null, vexag: number): CameraState | null {
    if (!this.lock || !this.plane || !origin) return null;
    const r = followSection(cam, this.lock, this.plane, origin, vexag);
    this.lock = r.lock;
    return r.camera;
  }

  /** «Вписать» or the «section» view while locked (null — not locked). */
  lockedView(box: Box3, opts: ViewOptions, origin: Origin | null): CameraState | null {
    return this.lock ? lockedView(this.lock, box, opts, this.frame(origin)) : null;
  }

  dispose(): void {
    this.overlay.dispose();
  }
}
