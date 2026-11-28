/**
 * An editing session in the 3D window (ADR 0015 §5): the session takes the pointer first (`Scene.setTool`), sees
 * the view through the cursor ray and the matrix project → clip of the last frame, its working copy is the layer
 * `edit` (`viz/edit/overlay.ts`, a lazy chunk with three.js) redrawn once a frame after a change; a moved camera
 * rebuilds the screen index of the snap after a pause. In an empty project the editing layer sets the origin of
 * the scene at the level of the panel and the view turns to the plan. After a lost context the layer comes back.
 */
import { lineById, vertexCount } from "../../edit/model";
import { nearestSegment } from "../../edit/pick";
import type { EditSession } from "../../edit/session";
import type { Scene } from "../../viz/Scene";
import type { EditOverlay, OverlayColors, OverlayState } from "../../viz/edit/overlay";
import type { Origin } from "../../viz/origin";

export const EDIT_KEY = "edit";
/** Vertex marks of the lines near the cursor are looked for in layers up to this size (every move). */
const NEAR_LIMIT = 200_000;
const VIEW_PAUSE_MS = 120;

/** The colours of the editing layer from the theme; the lines — in the colour of the layer. */
export function overlayColors(layerColor: string): OverlayColors {
  const css = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  const accent = v("--accent", "#1f6bff");
  return { line: layerColor || accent, selected: v("--highlight", "#f46a1a"), vertex: v("--text", "#e6e9ee"), cursor: accent, snap: v("--highlight", "#f46a1a") };
}

/** What the editing layer shows now. */
export function overlayState(s: EditSession): OverlayState {
  const h = s.hover;
  const t = h && vertexCount(s.layer) <= NEAR_LIMIT ? s.view?.transform() : null;
  const near = t && h ? nearestSegment(s.layer, t, h.x, h.y, 30) : null;
  // the mark of the cursor: where a point would go (drawing tools, a dragged vertex) or a snap target
  const marking = s.tool !== "select" || s.drag?.kind === "vertex" || !!h?.snap;
  const drawing = s.drawing && lineById(s.layer, s.drawing.line) ? s.drawing : null;
  return { layer: s.layer, selection: s.shownSelection, drawing, polygon: s.tool === "polygon", cursor: marking ? h?.point ?? null : null, snapped: !!h?.snap, near: near ? [near.line] : [], missingZ: s.zLevel };
}

function startOrigin(s: EditSession, scene: Scene): Origin {
  const o = scene.origin.value;
  if (o) return { ...o };
  for (const l of s.layer.lines) for (const p of l.points) if (Number.isFinite(p[0]) && Number.isFinite(p[1])) return { x: Math.round(p[0]), y: Math.round(p[1]), z: Math.round(Number.isFinite(p[2]) ? p[2] : s.zLevel) };
  return { x: 0, y: 0, z: Math.round(s.zLevel) };
}

/** Ties a session to a scene; gives the detach. */
export function attachSession(s: EditSession, scene: Scene, onView?: () => void): () => void {
  let overlay: EditOverlay | null = null;
  let frame = 0;
  let viewTimer: ReturnType<typeof setTimeout> | null = null;
  let lastCam = "";
  let alive = true;
  const off: (() => void)[] = [];

  s.view = {
    ray: (cx, cy) => {
      const r = scene.canvas.getBoundingClientRect();
      return scene.cursorRay(cx - r.left, cy - r.top);
    },
    transform: () => scene.projectView(),
  };
  // a general tool of the window (a section line) or another tool takes the pointer: what is drawn is finished
  scene.setTool({ pointer: (e) => s.pointer(e), deactivate: () => alive && s.release() });

  const draw = () => {
    frame = 0;
    if (!overlay || !alive) return;
    const changed = overlay.update(overlayState(s));
    // the extent counts for «Вписать» and the grid; not on every step of a drag
    if (changed && !s.drag) scene.layerChanged();
    scene.invalidate();
  };
  const schedule = () => {
    if (frame || !alive) return;
    frame = typeof requestAnimationFrame === "undefined" ? (setTimeout(draw, 16) as unknown as number) : requestAnimationFrame(draw);
  };

  const addOverlay = async () => {
    const mod = await import("../../viz/edit/overlay");
    if (!alive) return;
    const empty = !scene.origin.value;
    overlay = new mod.EditOverlay(scene.clipPlanes, startOrigin(s, scene), overlayColors(s.color), scene.renderer.pixelRatio);
    overlay.update(overlayState(s));
    scene.addLayer(EDIT_KEY, overlay);
    if (empty) scene.view("top");
    s.viewChanged();
    schedule();
  };
  void addOverlay();

  off.push(s.subscribe(schedule));
  // a moved camera: the screen index of the snap follows after a pause
  off.push(
    scene.onFrame((ctx) => {
      const c = ctx.camera;
      const key = `${c.position.join()}|${c.target.join()}|${c.half}|${ctx.width}x${ctx.height}|${ctx.vexag}`;
      if (key === lastCam) return;
      lastCam = key;
      if (viewTimer) clearTimeout(viewTimer);
      viewTimer = setTimeout(() => {
        viewTimer = null;
        s.viewChanged();
        onView?.();
      }, VIEW_PAUSE_MS);
    }),
  );
  off.push(
    scene.onRestored(() => {
      overlay = null;
      void addOverlay();
    }),
  );

  return () => {
    alive = false;
    off.forEach((fn) => fn());
    if (viewTimer) clearTimeout(viewTimer);
    if (frame && typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(frame);
    scene.setTool(null);
    scene.removeLayer(EDIT_KEY);
    s.view = null;
    s.probe = null;
    overlay = null;
  };
}

/** [xmin, ymin, xmax, ymax] of what the view shows, project coordinates (the snap targets of sets in plan). */
export function viewBox(scene: Scene): number[] | null {
  const o = scene.origin.value;
  if (!o) return null;
  const cam = scene.camera();
  const opts = scene.viewOptions();
  const half = cam.half * 1.5;
  const wide = half * Math.max(opts.aspect, 1);
  return [cam.target[0] + o.x - wide, cam.target[1] + o.y - wide, cam.target[0] + o.x + wide, cam.target[1] + o.y + wide];
}
