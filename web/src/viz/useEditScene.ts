import { useEffect, useRef, type MutableRefObject } from "react";
import { getHoleSnap, getLineArrays, pickTriangle, type HoleSnapData, type LineArrays } from "../api";
import { lineById, type Vec3 } from "../edit/model";
import type { EditSession } from "../edit/session";
import type { SectionLine } from "./geometry";
import type { EditOverlay, GeoScene } from "./scene3d";
import { cached } from "./useVizData";

export interface EditSceneContext {
  section: SectionLine | null;
  clip: boolean;
  /** Visible line layers other than the edited one: [id, version]. */
  lineLayers: [number, number][];
  /** Visible drillhole sets. */
  sets: number[];
  refreshKey: number;
  /** Visible wireframes (for snapping to their vertices and edges). */
  wireframes: number[];
  /** Exact sections of wireframes on the current section plane. */
  cuts: Map<number, { origin: number[]; polylines: { closed: boolean; points: number[][] }[] }>;
  cutsKey: number;
  onSave: () => void;
}

const loadLineArrays = (id: number, version: number): Promise<LineArrays> => cached(`lnx:${id}:${version}`, () => getLineArrays(id));
const loadHoleSnap = (setId: number, refreshKey: number): Promise<HoleSnapData> => cached(`hsnap:${setId}:${refreshKey}`, () => getHoleSnap(setId));

/** Segments (6 numbers each) of stored lines: consecutive vertices of every line. */
function segmentsOf(vertices: Float64Array, offsets: ArrayLike<number>): Float64Array {
  let n = 0;
  for (let l = 0; l + 1 < offsets.length; l++) n += Math.max(0, offsets[l + 1] - offsets[l] - 1);
  const out = new Float64Array(n * 6);
  let j = 0;
  for (let l = 0; l + 1 < offsets.length; l++) {
    for (let i = offsets[l]; i + 1 < offsets[l + 1]; i++) {
      out.set(vertices.subarray(i * 3, i * 3 + 6), j);
      j += 6;
    }
  }
  return out;
}

function buildOverlay(edit: EditSession): EditOverlay {
  const layer = edit.layer;
  const sel = edit.shownSelection;
  const picked = new Set(sel.lines);
  let nv = 0;
  let ns = 0;
  let nsel = 0;
  for (const l of layer.lines) {
    const n = l.points.length;
    const segs = l.closed && n > 2 ? n : Math.max(0, n - 1);
    nv += n;
    ns += segs;
    if (picked.has(l.id)) nsel += segs;
  }
  const vertices = new Float64Array(nv * 3);
  const segments = new Float64Array(ns * 6);
  const selected = new Float64Array(nsel * 6);
  let iv = 0;
  let is = 0;
  let ix = 0;
  for (const l of layer.lines) {
    const n = l.points.length;
    for (const p of l.points) {
      vertices.set(p, iv);
      iv += 3;
    }
    const segs = l.closed && n > 2 ? n : n - 1;
    for (let k = 0; k < segs; k++) {
      const a = l.points[k];
      const b = l.points[(k + 1) % n];
      segments.set(a, is);
      segments.set(b, is + 3);
      is += 6;
      if (picked.has(l.id)) {
        selected.set(a, ix);
        selected.set(b, ix + 3);
        ix += 6;
      }
    }
  }
  const selectedVertices = new Float64Array(sel.vertices.length * 3);
  sel.vertices.forEach((v, i) => {
    const p = lineById(layer, v.line)?.points[v.index];
    if (p) selectedVertices.set(p, i * 3);
  });
  // The rubber band: from the growing end of the line being drawn to the cursor (and back to its start for a polygon).
  const draft: number[] = [];
  const target = edit.hover?.point ?? null;
  const drawing = edit.drawing ? lineById(layer, edit.drawing.line) : null;
  if (drawing && target && drawing.points.length) {
    const end = drawing.points[edit.drawing!.atStart ? 0 : drawing.points.length - 1];
    draft.push(...end, ...target);
    if (edit.tool === "polygon" && drawing.points.length > 1) draft.push(...target, ...drawing.points[0]);
  }
  const marking = edit.tool !== "select" || edit.drag?.kind === "vertex";
  return { color: edit.color, segments, selected, vertices, selectedVertices, draft: Float64Array.from(draft), marker: marking && target ? [...target] : null };
}

/** Connects an editing session to the 3D view: pointer events, the overlay, snapping targets, keys. */
export function useEditScene(sceneRef: MutableRefObject<GeoScene | null>, edit: EditSession | null, ctx: EditSceneContext): void {
  const live = useRef(ctx);
  live.current = ctx;

  // ---- the session and the scene
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !edit) return;
    scene.onEditPointer = (e) => {
      // An empty project has no coordinate origin yet: the first press starts the scene around
      // (0, 0, Z of the drawing level) in plan, so clicks place points at once. Not on a mere hover,
      // so data still loading sets the origin itself.
      if (e.type !== "move" && !scene.focus()) {
        scene.startAt(0, 0, edit.zLevel);
        scene.view("top");
        edit.viewChanged();
      }
      return edit.pointer(e);
    };
    scene.onCamera = () => edit.viewChanged();
    edit.view = { ray: (x, y) => scene.rayAt(x, y), transform: () => scene.viewTransform() };
    edit.probe = async (ray) => {
      const ids = live.current.wireframes;
      if (!ids.length) return null;
      const found = await pickTriangle(ids, ray.origin, ray.dir);
      return found.hit ? { corners: found.hit.vertices as Vec3[], name: found.name ?? "" } : null;
    };
    const focus = scene.focus();
    if (focus && edit.zLevel === 0) edit.zLevel = Math.round(focus[2]);
    edit.viewChanged();
    let frame = 0;
    const draw = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        sceneRef.current?.setEditOverlay(buildOverlay(edit));
      });
    };
    const off = edit.subscribe(draw);
    draw();
    return () => {
      off();
      if (frame) cancelAnimationFrame(frame);
      scene.onEditPointer = null;
      scene.onCamera = () => {};
      scene.setEditOverlay(null);
      edit.view = null;
      edit.probe = null;
    };
  }, [edit, sceneRef]);

  // ---- the drawing plane follows the section
  useEffect(() => {
    if (!edit) return;
    edit.section = ctx.section;
    edit.clip = ctx.clip && ctx.section !== null;
    edit.viewChanged();
    edit.touch();
  }, [edit, ctx.section, ctx.clip]);

  // ---- snapping targets: other line layers, holes, sections of wireframes
  const linesKey = ctx.lineLayers.map((l) => l.join(":")).join(",");
  useEffect(() => {
    if (!edit) return;
    let alive = true;
    const index = edit.snapIndex;
    const wanted = new Set<string>(["edit"]);
    for (const [id, version] of live.current.lineLayers) {
      const key = `lines:${id}`;
      wanted.add(key);
      void loadLineArrays(id, version).then((a) => {
        if (!alive) return;
        index.setSource({ key, points: { kind: "vertex", xyz: a.vertices }, segments: { kind: "nearest", xyz: segmentsOf(a.vertices, a.offsets), midpoints: true } });
      }).catch(() => undefined);
    }
    for (const setId of live.current.sets) {
      const key = `set:${setId}`;
      wanted.add(key);
      wanted.add(`${key}:intervals`);
      wanted.add(`${key}:trace`);
      void loadHoleSnap(setId, live.current.refreshKey).then((h) => {
        if (!alive) return;
        index.setSource({ key, points: { kind: "collar", xyz: h.collars, labels: (i) => `устье ${h.holes[i]}` } });
        index.setSource({ key: `${key}:intervals`, points: { kind: "interval", xyz: h.bounds, labels: (i) => `${h.holes[h.boundHole[i]]} · ${Number(h.boundDepth[i].toFixed(2))} м` } });
        index.setSource({ key: `${key}:trace`, segments: { kind: "trace", xyz: segmentsOf(h.trace, h.traceOffsets) } });
      }).catch(() => undefined);
    }
    const cuts: number[] = [];
    for (const { origin, polylines } of live.current.cuts.values()) {
      for (const line of polylines) {
        const n = line.points.length;
        for (let i = 0; i + 1 < n || (line.closed && i < n); i++) {
          const a = line.points[i];
          const b = line.points[(i + 1) % n];
          cuts.push(a[0] + origin[0], a[1] + origin[1], a[2] + origin[2], b[0] + origin[0], b[1] + origin[1], b[2] + origin[2]);
        }
      }
    }
    wanted.add("cuts");
    index.setSource({ key: "cuts", segments: { kind: "wf-section", xyz: Float64Array.from(cuts) } });
    for (const key of known.current) if (!wanted.has(key)) index.removeSource(key);
    known.current = wanted;
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edit, linesKey, ctx.sets.join(","), ctx.refreshKey, ctx.cutsKey]);
  const known = useRef(new Set<string>());

  // ---- keys
  useEffect(() => {
    if (!edit) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (document.querySelector(".modal-backdrop")) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      let used = true;
      if (e.key === "Escape") edit.escape();
      else if (e.key === "Enter") edit.finishDrawing();
      else if (e.key === "Backspace" && edit.drawing) edit.undo();
      else if (e.key === "Delete" || (e.key === "Backspace" && !edit.drawing)) edit.deleteSelection();
      else if (ctrl && (key === "z" || key === "я") && !e.shiftKey) edit.undo();
      else if (ctrl && (key === "y" || key === "н" || ((key === "z" || key === "я") && e.shiftKey))) edit.redo();
      else if (ctrl && (key === "s" || key === "ы")) live.current.onSave();
      else if (ctrl && (key === "a" || key === "ф")) edit.selectAll();
      else used = false;
      if (used) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [edit]);
}
