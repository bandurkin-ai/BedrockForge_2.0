/**
 * One editing session of a line layer: the working copy with undo, the active tool, the selection
 * and snapping. No React and no three.js here: the 3D view feeds pointer events and a ray under
 * the cursor, the interface subscribes to changes.
 *
 * All coordinates are project coordinates in float64; the view gives only the cursor position.
 */
import type { SectionLine } from "../viz/geometry";
import { horizontalPlane, projectToPlane, rayPlane, sectionPlane, type Plane, type Ray } from "./coords";
import { toStored, type StoredLines } from "./io";
import { EMPTY_SELECTION, History, lineById, vertexAt, type AttrValue, type EditLayer, type Selection, type Vec3, type VertexRef } from "./model";
import * as ops from "./ops";
import { linesInBox, nearestSegment, nearestVertex, verticesInBox } from "./pick";
import { gridSnap, projectPoint, SNAP_TITLES, SnapIndex, type SnapHit, type SnapKind, type ViewTransform } from "./snap";

export type Tool = "select" | "line" | "polygon" | "point" | "tie" | "insert";

export const TOOL_TITLES: Record<Tool, string> = {
  select: "Выбор и перемещение",
  line: "Линия",
  polygon: "Полигон",
  point: "Точка",
  tie: "Тай-линия",
  insert: "Вставить вершину",
};

export interface SnapSettings {
  enabled: boolean;
  kinds: SnapKind[];
  /** px on screen */
  radius: number;
  /** On a section, hole and wireframe targets are projected into the section plane. */
  project: boolean;
  /** Grid step for the "grid" target, map units. */
  grid: number;
}

export const DEFAULT_SNAP: SnapSettings = {
  enabled: true,
  kinds: ["vertex", "midpoint", "nearest", "wf-section", "collar", "interval"],
  radius: 12,
  project: true,
  grid: 10,
};

/** Targets that lie off the section plane as a rule: these are the ones projected into it. */
const PROJECTED: ReadonlySet<SnapKind> = new Set<SnapKind>(["collar", "trace", "interval", "wf-vertex", "wf-edge"]);
const WF_KINDS: SnapKind[] = ["wf-vertex", "wf-edge"];

export interface EditPointer {
  type: "down" | "move" | "up" | "click" | "dblclick";
  /** px inside the canvas */
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  button: number;
  shift: boolean;
  ctrl: boolean;
}

/** What the 3D view gives the session. */
export interface SessionView {
  ray(clientX: number, clientY: number): Ray | null;
  transform(): ViewTransform | null;
}

/** The triangle of a wireframe under a ray (exact corners), from the server. */
export type WireframeProbe = (ray: Ray) => Promise<{ corners: Vec3[]; name: string } | null>;

export interface Hover {
  point: Vec3 | null;
  snap: SnapHit | null;
  x: number;
  y: number;
}

type Drag =
  | { kind: "vertex"; ref: VertexRef; moved: boolean }
  | { kind: "lines"; ids: number[]; last: Vec3; moved: boolean }
  | { kind: "box"; x0: number; y0: number; x1: number; y1: number; add: boolean };

const sameRef = (a: VertexRef, b: VertexRef) => a.line === b.line && a.index === b.index;
const samePoint = (a: Vec3, b: Vec3) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

/** The point of segment a–b closest to a ray (for snapping to an edge under the cursor). */
export function closestOnSegmentToRay(a: Vec3, b: Vec3, ray: Ray): Vec3 {
  const u = sub(b, a);
  const w = sub(a, ray.origin);
  const d = ray.dir;
  const uu = u[0] * u[0] + u[1] * u[1] + u[2] * u[2];
  const ud = u[0] * d[0] + u[1] * d[1] + u[2] * d[2];
  const dd = d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
  const uw = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
  const dw = d[0] * w[0] + d[1] * w[1] + d[2] * w[2];
  const den = uu * dd - ud * ud;
  const t = den > 1e-18 * uu * dd ? Math.min(1, Math.max(0, (ud * dw - dd * uw) / den)) : 0;
  return [a[0] + u[0] * t, a[1] + u[1] * t, a[2] + u[2] * t];
}

export class EditSession {
  readonly history: History<EditLayer>;
  /** Selected lines and vertices; not part of the undo history. */
  selection: Selection = EMPTY_SELECTION;
  readonly snapIndex = new SnapIndex();
  tool: Tool = "select";
  snap: SnapSettings = { ...DEFAULT_SNAP };
  /** Level of new points in plan (when the view is not clipped to a section). */
  zLevel = 0;
  section: SectionLine | null = null;
  clip = false;
  view: SessionView | null = null;
  probe: WireframeProbe | null = null;
  hover: Hover | null = null;
  /** The line being drawn: its id and the end that grows. */
  drawing: { line: number; atStart: boolean } | null = null;
  drag: Drag | null = null;
  message: string | null = null;
  saving = false;
  private listeners = new Set<() => void>();
  private revision = 0;
  private probed: { x: number; y: number; hit: SnapHit | null } | null = null;
  private probeTimer: ReturnType<typeof setTimeout> | null = null;
  private probeRun = 0;
  private sourceLayer: EditLayer | null = null;

  constructor(readonly layerId: number, public layerName: string, public color: string, public baseVersion: number, layer: EditLayer) {
    this.history = new History<EditLayer>(layer);
    this.refreshOwnSource();
  }

  // ---- subscription (React: useSyncExternalStore)
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getRevision = (): number => this.revision;
  private emit(): void {
    this.revision++;
    for (const fn of this.listeners) fn();
  }
  /** Something outside changed what the session shows (settings set directly). */
  touch(): void {
    this.emit();
  }

  get layer(): EditLayer {
    return this.history.present;
  }
  get dirty(): boolean {
    return this.history.dirty;
  }

  /** The plane new points fall on: the middle of the section window when clipped, else the plan level. */
  drawingPlane(): Plane {
    return this.clip && this.section ? sectionPlane(this.section) : horizontalPlane(this.zLevel);
  }

  // ---- state changes
  private apply(layer: EditLayer, label: string, selection: Selection = this.selection): void {
    if (layer !== this.layer) this.history.push(layer, label);
    this.selection = selection;
    this.afterChange();
  }
  private afterChange(): void {
    if (this.drawing && !lineById(this.layer, this.drawing.line)) this.drawing = null;
    // Lines and vertices that are gone (undo, delete) leave the selection.
    const sel = this.selection;
    const lines = sel.lines.filter((id) => lineById(this.layer, id));
    const vertices = sel.vertices.filter((v) => vertexAt(this.layer, v));
    if (lines.length !== sel.lines.length || vertices.length !== sel.vertices.length) this.selection = { lines, vertices };
    this.refreshOwnSource();
    this.emit();
  }
  private select(selection: Selection): void {
    this.selection = selection;
    this.emit();
  }
  get shownSelection(): Selection {
    return this.selection;
  }

  private refreshOwnSource(exclude?: VertexRef): void {
    const layer = this.layer;
    if (layer === this.sourceLayer && !exclude) return;
    this.sourceLayer = exclude ? null : layer;
    let np = 0;
    let ns = 0;
    for (const l of layer.lines) {
      np += l.points.length;
      ns += Math.max(0, l.points.length - 1) + (l.closed && l.points.length > 2 ? 1 : 0);
    }
    const pts = new Float64Array(np * 3);
    const segs = new Float64Array(ns * 6);
    let i = 0;
    let j = 0;
    for (const l of layer.lines) {
      const n = l.points.length;
      for (let k = 0; k < n; k++) {
        if (exclude && exclude.line === l.id && exclude.index === k) continue;
        pts.set(l.points[k], i);
        i += 3;
      }
      const count = l.closed && n > 2 ? n : n - 1;
      for (let k = 0; k < count; k++) {
        const b = (k + 1) % n;
        if (exclude && exclude.line === l.id && (exclude.index === k || exclude.index === b)) continue;
        segs.set(l.points[k], j);
        segs.set(l.points[b], j + 3);
        j += 6;
      }
    }
    this.snapIndex.setSource({
      key: "edit",
      points: { kind: "vertex", xyz: pts.subarray(0, i) },
      segments: { kind: "nearest", xyz: segs.subarray(0, j), midpoints: true },
    });
  }

  viewChanged(): void {
    const t = this.view?.transform();
    if (t) this.snapIndex.setView(t);
    this.probed = null;
  }

  // ---- where the cursor points
  private wireframeHit(corners: Vec3[], name: string, x: number, y: number, ray: Ray): SnapHit | null {
    const t = this.view?.transform();
    if (!t) return null;
    const kinds = new Set(this.snap.kinds);
    const screen = corners.map((c) => projectPoint(t.matrix, t.width, t.height, c[0], c[1], c[2]));
    let best: SnapHit | null = null;
    if (kinds.has("wf-vertex")) {
      corners.forEach((c, i) => {
        const s = screen[i];
        if (!s) return;
        const d = Math.hypot(s[0] - x, s[1] - y);
        if (d <= this.snap.radius && (!best || d < best.distancePx)) best = { kind: "wf-vertex", point: c, label: `${SNAP_TITLES["wf-vertex"]} · ${name}`, distancePx: d };
      });
    }
    if (!best && kinds.has("wf-edge")) {
      for (let i = 0; i < 3; i++) {
        const a = screen[i];
        const b = screen[(i + 1) % 3];
        if (!a || !b) continue;
        const dx = b[0] - a[0];
        const dy = b[1] - a[1];
        const len = dx * dx + dy * dy;
        const k = len ? Math.min(1, Math.max(0, ((x - a[0]) * dx + (y - a[1]) * dy) / len)) : 0;
        const d = Math.hypot(a[0] + dx * k - x, a[1] + dy * k - y);
        if (d <= this.snap.radius && (!best || d < (best as SnapHit).distancePx)) {
          best = { kind: "wf-edge", point: closestOnSegmentToRay(corners[i], corners[(i + 1) % 3], ray), label: `${SNAP_TITLES["wf-edge"]} · ${name}`, distancePx: d };
        }
      }
    }
    return best;
  }

  private wantsProbe(): boolean {
    return this.snap.enabled && !!this.probe && this.snap.kinds.some((k) => WF_KINDS.includes(k));
  }

  private async runProbe(e: { x: number; y: number; clientX: number; clientY: number }): Promise<SnapHit | null> {
    const ray = this.view?.ray(e.clientX, e.clientY);
    if (!ray || !this.probe) return null;
    const run = ++this.probeRun;
    let hit: SnapHit | null = null;
    try {
      const found = await this.probe(ray);
      if (found) hit = this.wireframeHit(found.corners, found.name, e.x, e.y, ray);
    } catch {
      hit = null;
    }
    if (run === this.probeRun) this.probed = { x: e.x, y: e.y, hit };
    return hit;
  }

  /** The point a click at this cursor position gives, and what it snapped to. */
  resolve(e: { x: number; y: number; clientX: number; clientY: number }, extra: SnapHit | null = null): Hover {
    const ray = this.view?.ray(e.clientX, e.clientY) ?? null;
    const plane = this.drawingPlane();
    let snap: SnapHit | null = null;
    if (this.snap.enabled) {
      snap = this.snapIndex.query(e.x, e.y, this.snap.radius, new Set(this.snap.kinds.filter((k) => k !== "grid")), ray ?? undefined);
      const wf = extra ?? (this.probed && Math.hypot(this.probed.x - e.x, this.probed.y - e.y) <= 3 ? this.probed.hit : null);
      if (wf && (!snap || (wf.kind === "wf-vertex" && snap.kind !== "vertex" && snap.kind !== "collar" && snap.kind !== "interval") || wf.distancePx < snap.distancePx - 4)) snap = wf;
      // The last point of the line being drawn is not a target for its own next point.
      if (snap && this.drawing) {
        const line = lineById(this.layer, this.drawing.line);
        const last = line && line.points[this.drawing.atStart ? 0 : line.points.length - 1];
        if (last && samePoint(last, snap.point)) snap = null;
      }
    }
    let point: Vec3 | null = null;
    if (snap) {
      point = this.clip && this.section && this.snap.project && PROJECTED.has(snap.kind) ? projectToPlane(snap.point, plane) : snap.point;
    } else if (ray) {
      point = rayPlane(ray, plane);
      if (point && this.snap.enabled && this.snap.kinds.includes("grid") && this.snap.grid > 0) {
        point = gridSnap(point, this.snap.grid, plane);
        snap = { kind: "grid", point, label: SNAP_TITLES.grid, distancePx: 0 };
      }
    }
    return { point, snap, x: e.x, y: e.y };
  }

  // ---- pointer events from the view; true = the event is taken (the view does not rotate or pick)
  pointer(e: EditPointer): boolean {
    const t = this.view?.transform() ?? null;
    if (e.type === "move") return this.onMove(e, t);
    if (e.type === "down") return this.onDown(e, t);
    if (e.type === "up") return this.onUp(e, t);
    if (e.type === "dblclick") return this.onDoubleClick(e, t);
    if (e.button !== 0) return false;
    void this.onClick(e, t);
    return true;
  }

  private onMove(e: EditPointer, t: ViewTransform | null): boolean {
    const d = this.drag;
    if (d?.kind === "box") {
      d.x1 = e.x;
      d.y1 = e.y;
      this.emit();
      return true;
    }
    this.hover = this.resolve(e);
    if (d?.kind === "vertex" && this.hover.point) {
      d.moved = true;
      this.history.replace(ops.moveVertex(this.layer, d.ref, this.hover.point));
    } else if (d?.kind === "lines") {
      // Lines move in the drawing plane (no snapping: the grabbed point is arbitrary).
      const ray = this.view?.ray(e.clientX, e.clientY);
      const p = ray ? rayPlane(ray, this.drawingPlane()) : null;
      if (p) {
        d.moved = true;
        this.history.replace(ops.translateLines(this.layer, d.ids, sub(p, d.last)));
        d.last = p;
      }
    }
    if (this.wantsProbe() && !d) {
      if (this.probeTimer) clearTimeout(this.probeTimer);
      const at = { x: e.x, y: e.y, clientX: e.clientX, clientY: e.clientY };
      this.probeTimer = setTimeout(() => {
        void this.runProbe(at).then(() => {
          if (this.hover && Math.hypot(this.hover.x - at.x, this.hover.y - at.y) <= 3) {
            this.hover = this.resolve(at);
            this.emit();
          }
        });
      }, 60);
    }
    void t;
    this.emit();
    return !!d;
  }

  private onDown(e: EditPointer, t: ViewTransform | null): boolean {
    if (e.button !== 0 || this.tool !== "select" || !t) return false;
    if (e.ctrl) {
      this.drag = { kind: "box", x0: e.x, y0: e.y, x1: e.x, y1: e.y, add: e.shift };
      return true;
    }
    const v = nearestVertex(this.layer, t, e.x, e.y, this.snap.radius);
    if (v) {
      const sel = this.shownSelection;
      if (!sel.vertices.some((r) => sameRef(r, v.ref))) this.select({ lines: e.shift ? sel.lines : [], vertices: e.shift ? [...sel.vertices, v.ref] : [v.ref] });
      this.refreshOwnSource(v.ref);
      this.drag = { kind: "vertex", ref: v.ref, moved: false };
      return true;
    }
    const s = nearestSegment(this.layer, t, e.x, e.y, 6);
    if (s && this.shownSelection.lines.includes(s.line)) {
      const ray = this.view?.ray(e.clientX, e.clientY);
      const p = ray ? rayPlane(ray, this.drawingPlane()) : null;
      if (p) {
        this.drag = { kind: "lines", ids: this.shownSelection.lines, last: p, moved: false };
        return true;
      }
    }
    return false;
  }

  private onUp(_e: EditPointer, t: ViewTransform | null): boolean {
    const d = this.drag;
    if (!d) return false;
    this.drag = null;
    if (d.kind === "box") {
      if (t && Math.hypot(d.x1 - d.x0, d.y1 - d.y0) > 3) {
        const lines = linesInBox(this.layer, t, d.x0, d.y0, d.x1, d.y1);
        const vertices = verticesInBox(this.layer, t, d.x0, d.y0, d.x1, d.y1);
        const old = this.shownSelection;
        this.select(d.add
          ? { lines: [...new Set([...old.lines, ...lines])], vertices: [...old.vertices, ...vertices.filter((v) => !old.vertices.some((r) => sameRef(r, v)))] }
          : { lines, vertices });
      } else this.emit();
      return true;
    }
    if (d.moved) {
      this.history.commit(d.kind === "vertex" ? "Перемещение вершины" : "Перемещение линий");
    } else if (this.history.pending) this.history.cancel();
    this.sourceLayer = null;
    this.afterChange();
    // A press without movement is a click: let the click handler select.
    return d.moved;
  }

  private async onClick(e: EditPointer, t: ViewTransform | null): Promise<void> {
    if (!t) return;
    if (this.tool === "select") return this.clickSelect(e, t);
    if (this.tool === "insert") {
      const s = nearestSegment(this.layer, t, e.x, e.y, this.snap.radius);
      if (!s) return this.say("Щёлкните по сегменту линии");
      const line = lineById(this.layer, s.line)!;
      const a = line.points[s.segment];
      const b = line.points[(s.segment + 1) % line.points.length];
      const ray = this.view?.ray(e.clientX, e.clientY);
      const p = ray ? closestOnSegmentToRay(a, b, ray) : ([a[0] + (b[0] - a[0]) * s.t, a[1] + (b[1] - a[1]) * s.t, a[2] + (b[2] - a[2]) * s.t] as Vec3);
      return this.apply(ops.insertVertex(this.layer, s.line, s.segment, p), "Вставка вершины", { lines: [], vertices: [{ line: s.line, index: s.segment + 1 }] });
    }
    let extra: SnapHit | null = null;
    if (this.wantsProbe() && !(this.probed && Math.hypot(this.probed.x - e.x, this.probed.y - e.y) <= 3)) extra = await this.runProbe(e);
    const hit = this.resolve(e, extra);
    if (!hit.point) return this.say("Здесь точку поставить нельзя: плоскость построения видна с ребра. Поверните вид или введите координаты");
    this.addPoint(hit.point, t, e);
  }

  /** Adds a point with the active drawing tool (a click or typed coordinates). */
  addPoint(p: Vec3, t: ViewTransform | null = null, e: { x: number; y: number } | null = null): void {
    if (this.tool === "point") {
      const made = ops.addLine(this.layer, [p], false);
      return this.apply(made.layer, "Точка", { lines: [made.id], vertices: [] });
    }
    if (this.tool !== "line" && this.tool !== "polygon" && this.tool !== "tie") return;
    const drawing = this.drawing && lineById(this.layer, this.drawing.line) ? this.drawing : null;
    if (!drawing) {
      // A click on the end of an open line continues that line.
      const v = t && e && this.tool === "line" ? nearestVertex(this.layer, t, e.x, e.y, this.snap.radius) : null;
      const line = v ? lineById(this.layer, v.ref.line) : null;
      if (v && line && !line.closed && line.points.length > 1 && (v.ref.index === 0 || v.ref.index === line.points.length - 1)) {
        this.drawing = { line: line.id, atStart: v.ref.index === 0 };
        this.select({ lines: [line.id], vertices: [] });
        return this.say("Продолжение линии: следующие точки добавляются к её концу");
      }
      const made = ops.addLine(this.layer, [p], false, this.tool === "tie" ? { ROLE: "TIE" } : {});
      this.drawing = { line: made.id, atStart: false };
      return this.apply(made.layer, this.tool === "tie" ? "Тай-линия" : "Новая линия", { lines: [made.id], vertices: [] });
    }
    const line = lineById(this.layer, drawing.line)!;
    // A click on the first point closes the line.
    const first = line.points[drawing.atStart ? line.points.length - 1 : 0];
    if (line.points.length >= 3 && samePoint(first, p)) {
      this.apply(ops.setClosed(this.layer, line.id, true), "Замыкание линии");
      return this.finishDrawing();
    }
    this.apply(ops.appendPoint(this.layer, line.id, p, drawing.atStart), "Точка линии");
    if (this.tool === "tie") this.finishDrawing();
  }

  private clickSelect(e: EditPointer, t: ViewTransform): void {
    const sel = this.shownSelection;
    const v = nearestVertex(this.layer, t, e.x, e.y, this.snap.radius);
    if (v) {
      const has = sel.vertices.some((r) => sameRef(r, v.ref));
      if (e.shift) return this.select({ lines: sel.lines, vertices: has ? sel.vertices.filter((r) => !sameRef(r, v.ref)) : [...sel.vertices, v.ref] });
      return this.select({ lines: [v.ref.line], vertices: [v.ref] });
    }
    const s = nearestSegment(this.layer, t, e.x, e.y, 8);
    if (s) {
      const has = sel.lines.includes(s.line);
      if (e.shift) return this.select({ lines: has ? sel.lines.filter((id) => id !== s.line) : [...sel.lines, s.line], vertices: sel.vertices });
      return this.select({ lines: [s.line], vertices: [] });
    }
    if (!e.shift) this.select(EMPTY_SELECTION);
  }

  private onDoubleClick(e: EditPointer, t: ViewTransform | null): boolean {
    if (this.drawing) {
      this.finishDrawing();
      return true;
    }
    if (this.tool === "select" && t) {
      const s = nearestSegment(this.layer, t, e.x, e.y, 8);
      if (s && !nearestVertex(this.layer, t, e.x, e.y, this.snap.radius)) {
        const line = lineById(this.layer, s.line)!;
        const ray = this.view?.ray(e.clientX, e.clientY);
        const a = line.points[s.segment];
        const b = line.points[(s.segment + 1) % line.points.length];
        if (ray) this.apply(ops.insertVertex(this.layer, s.line, s.segment, closestOnSegmentToRay(a, b, ray)), "Вставка вершины", { lines: [s.line], vertices: [{ line: s.line, index: s.segment + 1 }] });
        return true;
      }
    }
    return false;
  }

  // ---- commands
  say(text: string | null): void {
    this.message = text;
    this.emit();
  }

  setTool(tool: Tool): void {
    if (this.drawing) this.finishDrawing();
    this.tool = tool;
    this.message = null;
    this.emit();
  }

  /** Ends the line being drawn: a polygon closes, a line left with one point goes away. */
  finishDrawing(): void {
    const d = this.drawing;
    this.drawing = null;
    const line = d ? lineById(this.layer, d.line) : null;
    if (line) {
      if (this.tool === "polygon" && !line.closed && line.points.length >= 3) this.apply(ops.setClosed(this.layer, line.id, true), "Замыкание полигона");
      else if (line.points.length < 2) this.apply(ops.deleteLines(this.layer, [line.id]), "Отмена линии", EMPTY_SELECTION);
      else if (this.tool === "polygon" && line.points.length < 3) this.say("Полигону нужно не меньше трёх вершин: линия оставлена незамкнутой");
    }
    this.emit();
  }

  /** Esc: ends the drawing, else drops the selection, else returns to the select tool. */
  escape(): void {
    if (this.drag) {
      this.drag = null;
      if (this.history.pending) this.history.cancel();
      this.sourceLayer = null;
      return this.afterChange();
    }
    if (this.drawing) return this.finishDrawing();
    const sel = this.shownSelection;
    if (sel.lines.length || sel.vertices.length) return this.select(EMPTY_SELECTION);
    if (this.tool !== "select") this.setTool("select");
  }

  undo(): void {
    const label = this.history.undo();
    this.message = label ? `Отменено: ${label}` : "Отменять нечего";
    this.afterChange();
  }
  redo(): void {
    const label = this.history.redo();
    this.message = label ? `Возвращено: ${label}` : "Возвращать нечего";
    this.afterChange();
  }

  /** Lines the commands work on: the selected ones, or those of the selected vertices. */
  targetLines(): number[] {
    const sel = this.shownSelection;
    return sel.lines.length ? sel.lines : [...new Set(sel.vertices.map((v) => v.line))];
  }
  private needLines(): number[] | null {
    const ids = this.targetLines().filter((id) => lineById(this.layer, id));
    if (!ids.length) this.say("Сначала выберите линию: инструмент «Выбор», щелчок по линии");
    return ids.length ? ids : null;
  }
  private each(label: string, fn: (layer: EditLayer, id: number) => EditLayer): void {
    const ids = this.needLines();
    if (!ids) return;
    this.apply(ids.reduce(fn, this.layer), label, { lines: ids, vertices: [] });
  }

  deleteSelection(): void {
    const sel = this.shownSelection;
    const loose = sel.vertices.filter((v) => !sel.lines.includes(v.line) || sel.vertices.length > 0);
    if (loose.length && !(sel.lines.length && !sel.vertices.length)) return this.apply(ops.deleteVertices(this.layer, sel.vertices), "Удаление вершин", EMPTY_SELECTION);
    if (sel.lines.length) return this.apply(ops.deleteLines(this.layer, sel.lines), "Удаление линий", EMPTY_SELECTION);
    this.say("Нечего удалять: выберите линии или вершины");
  }
  setClosed(closed: boolean): void {
    const ids = this.needLines();
    if (!ids) return;
    const short = ids.filter((id) => (lineById(this.layer, id)?.points.length ?? 0) < 3);
    if (closed && short.length === ids.length) return this.say("Замкнуть можно линию не короче трёх вершин");
    this.apply(ids.reduce((l, id) => ops.setClosed(l, id, closed), this.layer), closed ? "Замыкание" : "Размыкание", { lines: ids, vertices: [] });
  }
  reverse(): void {
    this.each("Разворот направления", ops.reverseLine);
  }
  breakAtVertex(): void {
    const v = this.shownSelection.vertices;
    if (v.length !== 1) return this.say("Чтобы разорвать линию, выберите одну её вершину");
    const made = ops.breakAt(this.layer, v[0]);
    this.apply(made.layer, "Разрыв линии", { lines: made.ids, vertices: [] });
  }
  join(): void {
    const ids = this.targetLines();
    if (ids.length !== 2) return this.say("Чтобы соединить, выберите ровно две незамкнутые линии (Shift — добавить к выбору)");
    const made = ops.joinLines(this.layer, ids[0], ids[1]);
    if (!made) return this.say("Соединяются только незамкнутые линии");
    this.apply(made.layer, "Соединение линий", { lines: [made.id], vertices: [] });
  }
  translate(delta: Vec3, copy = false): void {
    const ids = this.needLines();
    if (!ids) return;
    if (copy) {
      const made = ops.copyLines(this.layer, ids, delta);
      return this.apply(made.layer, "Копия линий", { lines: made.ids, vertices: [] });
    }
    this.apply(ops.translateLines(this.layer, ids, delta), "Сдвиг линий", { lines: ids, vertices: [] });
  }
  offset(distance: number): void {
    const ids = this.needLines();
    if (!ids) return;
    let layer = this.layer;
    const made: number[] = [];
    for (const id of ids) {
      const line = lineById(layer, id)!;
      const r = ops.offsetLine(layer, id, distance, line.closed ? undefined : this.drawingPlane());
      if (r) {
        layer = r.layer;
        made.push(r.id);
      }
    }
    if (!made.length) return this.say("Копия со смещением не получилась: линия слишком мала для такого смещения");
    this.apply(layer, "Копия со смещением", { lines: made, vertices: [] });
  }
  simplify(tolerance: number): void {
    this.each("Прореживание", (l, id) => ops.simplifyLine(l, id, tolerance));
  }
  densify(step: number): void {
    this.each("Сгущение", (l, id) => ops.densifyLine(l, id, step));
  }
  smooth(iterations = 1): void {
    this.each("Сглаживание", (l, id) => ops.smoothLine(l, id, iterations));
  }
  setAttrs(patch: Record<string, AttrValue | undefined>): void {
    const ids = this.needLines();
    if (ids) this.apply(ops.setAttrs(this.layer, ids, patch), "Атрибуты линий", this.shownSelection);
  }
  moveVertexTo(ref: VertexRef, p: Vec3): void {
    if (vertexAt(this.layer, ref)) this.apply(ops.moveVertex(this.layer, ref, p), "Координаты вершины", this.shownSelection);
  }
  selectAll(): void {
    this.select({ lines: this.layer.lines.map((l) => l.id), vertices: [] });
  }
  selectLines(ids: number[]): void {
    this.select({ lines: ids.filter((id) => lineById(this.layer, id)), vertices: [] });
  }
  /** Adds a whole line at once (commands, Claude). */
  addLine(points: Vec3[], closed: boolean, attrs: Record<string, AttrValue> = {}): number {
    const made = ops.addLine(this.layer, points, closed && points.length >= 3, attrs);
    this.apply(made.layer, closed ? "Новый полигон" : "Новая линия", { lines: [made.id], vertices: [] });
    return made.id;
  }
  /** The last point of the line being drawn, or the selected vertex: what relative coordinates count from. */
  anchor(): Vec3 | null {
    if (this.drawing) {
      const line = lineById(this.layer, this.drawing.line);
      if (line?.points.length) return line.points[this.drawing.atStart ? 0 : line.points.length - 1];
    }
    const v = this.shownSelection.vertices[0];
    return v ? vertexAt(this.layer, v) ?? null : null;
  }

  /** The arrays to save, in the stored form. */
  stored(): StoredLines {
    return toStored(this.layer);
  }
  /** After a successful save. */
  saved(version: number): void {
    this.baseVersion = version;
    this.history.markSaved();
    this.message = `Сохранено: версия ${version}`;
    this.emit();
  }
}
