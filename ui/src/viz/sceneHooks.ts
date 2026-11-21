/**
 * Small pure pieces of `Scene.ts`, kept apart so that the scene stays within 600 lines (ТЗ 17): the camera of
 * the frame benchmark, where a project point is on the screen (the E2E hook), the labels of a frame placed by
 * the view-projection matrix, the observers of the host element and the pointer shared by the tool of the string
 * editor and the handles of a swath (`ToolChain`). No state of the scene lives here.
 */
import type { ToolHandler, ToolPointer } from "./core/toolInput";
import type { Vec3 } from "./core/vec3";
import type { CameraState } from "./core/views";
import { placeLabels, type LabelItem } from "./labels/sprites";
import { projectToScreen, type Mat4 } from "./pick/ray";

/** The camera turned by `rad` about the vertical through its centre (smoke scenario, frame benchmark). */
export function orbited(c: CameraState, rad: number): CameraState {
  const dx = c.position[0] - c.target[0];
  const dy = c.position[1] - c.target[1];
  const [s, k] = [Math.sin(rad), Math.cos(rad)];
  return { ...c, position: [c.target[0] + dx * k - dy * s, c.target[1] + dx * s + dy * k, c.position[2]] };
}

/**
 * A project point (absolute coordinates) on the screen, client px, by the matrix of the last frame; null —
 * nothing is drawn yet (no matrix, no scene origin) or the point is behind the camera.
 */
export function screenOf(vp: Mat4 | null, origin: { x: number; y: number; z: number } | null, vexag: number, size: { w: number; h: number }, canvas: HTMLCanvasElement, abs: Vec3): { x: number; y: number } | null {
  const s = vp && origin ? projectToScreen(vp, abs[0] - origin.x, abs[1] - origin.y, (abs[2] - origin.z) * vexag, size.w, size.h) : null;
  if (!s) return null;
  const r = canvas.getBoundingClientRect();
  return { x: s[0] + r.left, y: s[1] + r.top };
}

/** The labels of a frame: those in the section window that fit, with their size in pixels (`placeLabels`);
 * `inWindow` — scene coordinates (the rendered Z divided by `vexag`). */
export function placeFrameLabels(items: readonly LabelItem[], vp: Mat4, w: number, h: number, ppuAt: (at: Vec3) => number, inWindow: (x: number, y: number, z: number) => boolean, vexag = 1): number {
  return placeLabels(items, {
    width: w,
    height: h,
    project: (p) => {
      const s = projectToScreen(vp, p.x, p.y, p.z, w, h);
      return s ? { x: s[0], y: s[1], ppu: ppuAt([p.x, p.y, p.z]) } : null;
    },
    inWindow: (p) => inWindow(p.x, p.y, p.z / (vexag || 1)),
  });
}

/** Calls `onResize` when the host changes its size and `onTheme` when the theme of the document changes;
 * returns what stops both. */
export function watchHost(host: HTMLElement, onResize: () => void, onTheme: () => void): () => void {
  const ro = new ResizeObserver(onResize);
  ro.observe(host);
  const mo = new MutationObserver(onTheme);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => {
    ro.disconnect();
    mo.disconnect();
  };
}

/**
 * The pointer of the 3D view shared by two takers (ADR 0015 §5, ADR 0019 §6): the tool of the string editor, or
 * — while there is none — the handles of a swath (Д-01); what it does not take is the scene's (turning the view,
 * `T`, a click on a collar — `ToolInput`). While a tool is set the handles get nothing: not lit, the cursor is the
 * tool's (`Scene.setTool`). A press taken belongs to its taker until the release and the click after it. With
 * neither set it takes nothing: the old input.
 */
export class ToolChain implements ToolHandler {
  /** the tool of the string editor (`Scene.setTool`) */
  tool: ToolHandler | null = null;
  /** the handles of a swath (`Scene.setHandles`) */
  handles: ToolHandler | null = null;
  private taker: (ToolHandler & { cancel?(): void }) | null = null;

  /**
   * The tool changed (`ToolInput.setTool` drops the press): a drag in progress is called off, the handles let go of
   * the hover and its cursor (before `Scene.setTool` sets the tool's).
   */
  reset(): void {
    const t = this.taker;
    this.taker = null;
    t?.cancel?.();
    this.handles?.pointer({ type: "leave", x: 0, y: 0, clientX: 0, clientY: 0, button: -1, shift: false, ctrl: false });
  }

  pointer(e: ToolPointer): boolean {
    const one = this.tool ?? this.handles;
    const both = one ? [one] : [];
    switch (e.type) {
      case "down":
        this.taker = both.find((t) => t.pointer(e)) ?? null;
        return this.taker !== null;
      case "up":
        return this.taker ? this.taker.pointer(e) || true : false;
      case "click": {
        const t = this.taker;
        this.taker = null;
        if (t) return t.pointer(e) || true;
        return both.some((x) => x.pointer(e));
      }
      case "dblclick":
        return both.some((x) => x.pointer(e));
      case "move":
        if (this.taker && e.button === 0) return this.taker.pointer(e) || true;
        return both.reduce((taken, x) => x.pointer(e) || taken, false);
      default:
        for (const x of both) x.pointer(e);
        return false;
    }
  }
}
