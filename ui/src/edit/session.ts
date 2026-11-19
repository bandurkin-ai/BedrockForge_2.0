/**
 * One editing session of a line layer (ADR 0015 §3–§5; ТЗ 7.22; BF `edit/session.ts`): the working copy with
 * undo, the active tool, the selection and snapping. No React and no three.js here: the 3D view feeds pointer
 * events and a ray under the cursor, the interface subscribes to changes. The commands are in `tools.ts`.
 *
 * Where a point falls: in plan — on the Z of the panel (`zLevel`); with the section clipped — on the vertical
 * plane in the middle of the section window; with a snap — on the target, or on its projection into the section
 * plane (`snap.project`) for hole and wireframe targets. Coordinates are project float64 throughout.
 */
import type { SectionLine } from "../viz/section/math";
import { horizontalPlane, projectToPlane, rayPlane, sectionPlane, type Plane } from "./coords";
import { moveBody, rebaseOnSaved, saveBody, summarize, type LoadedLayer, type SaveSummary } from "./io";
import { EMPTY_SELECTION, layerHistory, lineById, vertexAt, type EditLayer, type History, type Selection, type Vec3, type VertexRef } from "./model";
import * as ops from "./ops";
import { linesInBox, nearestSegment, nearestVertex, verticesInBox } from "./pick";
import { gridSnap, layerSnapSource, PROJECTED_KINDS, SNAP_TITLES, type SnapHit, type ViewTransform } from "./snap";
import { SnapIndex } from "./snapIndex";
import * as cmd from "./tools";
import { closestOnSegmentToRay, DEFAULT_SNAP, keyCommand, PROBE_DELAY_MS, toolAttrs, WF_KINDS, wireframeHit } from "./tools";
import type { CommandHost, EditKey, EditPointer, Hover, SessionView, SnapSettings, Tool, WireframeProbe } from "./tools";

type Drag =
  | { kind: "vertex"; ref: VertexRef; origin: Vec3; moved: boolean }
  | { kind: "lines"; ids: number[]; last: Vec3; moved: boolean }
  | { kind: "box"; x0: number; y0: number; x1: number; y1: number; add: boolean };

const sameRef = (a: VertexRef, b: VertexRef) => a.line === b.line && a.index === b.index;
const samePoint = (a: Vec3, b: Vec3) => Object.is(a[0], b[0]) && Object.is(a[1], b[1]) && Object.is(a[2], b[2]);
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

export class EditSession implements CommandHost {
  history: History<EditLayer>;
  /** Selected lines and vertices; not part of the undo history. */
  selection: Selection = EMPTY_SELECTION;
  readonly snapIndex = new SnapIndex();
  tool: Tool = "select";
  snap: SnapSettings = { ...DEFAULT_SNAP };
  /** Level of new points in plan (when the view is not clipped to a section): the Z of the panel. */
  zLevel = 0;
  section: SectionLine | null = null;
  clip = false;
  view: SessionView | null = null;
  probe: WireframeProbe | null = null;
  hover: Hover | null = null;
  /** The line being drawn: its id and the end that grows. */
  drawing: { line: number; atStart: boolean } | null = null;
  /** «Собрать из контуров»: closed lines in the order clicked; `closed` — the open ends are capped. */
  assemble: { order: number[]; closed: boolean } | null = null;
  drag: Drag | null = null;
  message: string | null = null;
  private listeners = new Set<() => void>();
  private revision = 0;
  private probed: { x: number; y: number; hit: SnapHit | null } | null = null;
  private probeTimer: ReturnType<typeof setTimeout> | null = null;
  private probeAbort: AbortController | null = null;
  private sourceLayer: EditLayer | null = null;

  constructor(
    public loaded: LoadedLayer,
    public layerName = "",
    public color = "",
  ) {
    this.history = layerHistory(loaded.layer);
    this.refreshOwnSource();
  }

  get layerId(): number {
    return this.loaded.layerId;
  }
  /** The version the edits are based on: a save over a newer one is refused (409). */
  get baseVersion(): number {
    return this.loaded.version;
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
  get canUndo(): boolean {
    return this.history.canUndo;
  }
  get canRedo(): boolean {
    return this.history.canRedo;
  }
  get shownSelection(): Selection {
    return this.selection;
  }

  /** The plane new points fall on: the middle of the section window when clipped, else the plan level. */
  drawingPlane(): Plane {
    return this.clip && this.section ? sectionPlane(this.section) : horizontalPlane(this.zLevel);
  }

  /** The view transform; in plan a target without a height is drawn on the plan level. */
  private transform(): ViewTransform | null {
    const t = this.view?.transform() ?? null;
    if (!t || t.missingZ !== undefined) return t;
    return { ...t, missingZ: this.clip && this.section ? undefined : this.zLevel };
  }

  // ---- state changes (CommandHost)
  edit(layer: EditLayer, label: string, selection: Selection = this.selection): void {
    if (layer !== this.layer) this.history.push(layer, label);
    this.selection = selection;
    this.afterChange();
  }
  setSelection(selection: Selection): void {
    this.selection = selection;
    this.emit();
  }
  say(text: string | null): void {
    this.message = text;
    this.emit();
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

  private refreshOwnSource(exclude?: VertexRef): void {
    const layer = this.layer;
    if (layer === this.sourceLayer && !exclude) return;
    this.sourceLayer = exclude ? null : layer;
    this.snapIndex.setSource(layerSnapSource(layer, "edit", exclude));
  }

  viewChanged(): void {
    const t = this.transform();
    if (t) this.snapIndex.setView(t);
    this.probed = null;
  }

  // ---- where the cursor points
  private wantsProbe(): boolean {
    return this.snap.enabled && !!this.probe && this.snap.kinds.some((k) => WF_KINDS.includes(k));
  }

  /** Asks the core for the triangle under the cursor; an earlier request still on its way is aborted. */
  private async runProbe(e: { x: number; y: number; clientX: number; clientY: number }): Promise<SnapHit | null> {
    const ray = this.view?.ray(e.clientX, e.clientY);
    const t = this.transform();
    if (!ray || !t || !this.probe) return null;
    this.probeAbort?.abort();
    const abort = new AbortController();
    this.probeAbort = abort;
    let hit: SnapHit | null = null;
    try {
      const found = await this.probe(ray, abort.signal);
      if (found) hit = wireframeHit(found.corners, found.name, e.x, e.y, ray, t, new Set(this.snap.kinds), this.snap.radius);
    } catch {
      hit = null;
    }
    if (abort.signal.aborted) return null;
    this.probeAbort = null;
    this.probed = { x: e.x, y: e.y, hit };
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
      point = this.clip && this.section && this.snap.project && PROJECTED_KINDS.has(snap.kind) ? projectToPlane(snap.point, plane) : snap.point;
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
    const t = this.transform();
    if (e.type === "move") return this.onMove(e);
    if (e.type === "down") return this.onDown(e, t);
    if (e.type === "up") return this.onUp(t);
    if (e.type === "dblclick") return this.onDoubleClick(e, t);
    // A right click (without dragging: a drag pans the view) ends the line being drawn, as in AutoCAD.
    if (e.button === 2) {
      if (!this.drawing) return false;
      this.finishDrawing();
      return true;
    }
    if (e.button !== 0) return false;
    void this.onClick(e, t);
    return true;
  }

  private onMove(e: EditPointer): boolean {
    const d = this.drag;
    if (d?.kind === "box") {
      d.x1 = e.x;
      d.y1 = e.y;
      this.emit();
      return true;
    }
    this.hover = this.resolve(e);
    if (d?.kind === "vertex" && !this.hover.snap) {
      // Without a snap a dragged vertex slides in its own plane: in plan it keeps its Z, on a
      // section its distance from the section plane (only a snap or typed coordinates change those).
      const ray = this.view?.ray(e.clientX, e.clientY);
      const origin: Vec3 = Number.isFinite(d.origin[2]) ? d.origin : [d.origin[0], d.origin[1], this.zLevel];
      const p = ray ? rayPlane(ray, { point: origin, normal: this.drawingPlane().normal }) : null;
      // a vertex without a height moved in plan stays without one
      if (p && !Number.isFinite(d.origin[2]) && this.drawingPlane().normal[2] === 1) p[2] = d.origin[2];
      this.hover = { ...this.hover, point: p };
    }
    if (d?.kind === "vertex" && this.hover.point) {
      d.moved = true;
      this.history.replace(ops.moveVertex(this.history.committed, d.ref, this.hover.point));
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
      }, PROBE_DELAY_MS);
    }
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
      const sel = this.selection;
      if (!sel.vertices.some((r) => sameRef(r, v.ref))) this.setSelection({ lines: e.shift ? sel.lines : [], vertices: e.shift ? [...sel.vertices, v.ref] : [v.ref] });
      this.refreshOwnSource(v.ref);
      this.drag = { kind: "vertex", ref: v.ref, origin: vertexAt(this.layer, v.ref) as Vec3, moved: false };
      return true;
    }
    const s = nearestSegment(this.layer, t, e.x, e.y, 6);
    if (s && this.selection.lines.includes(s.line)) {
      const ray = this.view?.ray(e.clientX, e.clientY);
      const p = ray ? rayPlane(ray, this.drawingPlane()) : null;
      if (p) {
        this.drag = { kind: "lines", ids: this.selection.lines, last: p, moved: false };
        return true;
      }
    }
    return false;
  }

  private onUp(t: ViewTransform | null): boolean {
    const d = this.drag;
    if (!d) return false;
    this.drag = null;
    if (d.kind === "box") {
      if (t && Math.hypot(d.x1 - d.x0, d.y1 - d.y0) > 3) {
        const lines = linesInBox(this.layer, t, d.x0, d.y0, d.x1, d.y1);
        const vertices = verticesInBox(this.layer, t, d.x0, d.y0, d.x1, d.y1);
        const old = this.selection;
        this.setSelection(d.add
          ? { lines: [...new Set([...old.lines, ...lines])], vertices: [...old.vertices, ...vertices.filter((v) => !old.vertices.some((r) => sameRef(r, v)))] }
          : { lines, vertices });
      } else this.emit();
      return true;
    }
    if (d.moved) this.history.commit(d.kind === "vertex" ? "Перемещение вершины" : "Перемещение линий");
    else if (this.history.pending) this.history.cancel();
    this.sourceLayer = null;
    this.afterChange();
    // A press without movement is a click: let the click handler select.
    return d.moved;
  }

  private async onClick(e: EditPointer, t: ViewTransform | null): Promise<void> {
    if (!t) return;
    if (this.tool === "select") return this.clickSelect(e, t);
    if (this.tool === "insert") return this.insertAt(e, t, this.snap.radius, false) ? undefined : this.say("Щёлкните по сегменту линии");
    let extra: SnapHit | null = null;
    if (this.wantsProbe() && !(this.probed && Math.hypot(this.probed.x - e.x, this.probed.y - e.y) <= 3)) extra = await this.runProbe(e);
    const hit = this.resolve(e, extra);
    if (!hit.point) return this.say("Здесь точку поставить нельзя: плоскость построения видна с ребра. Поверните вид или введите координаты");
    this.addPoint(hit.point, t, e);
  }

  /** A new vertex on the segment under the cursor; false when there is none within `radius`. */
  private insertAt(e: EditPointer, t: ViewTransform, radius: number, selectLine: boolean): boolean {
    const s = nearestSegment(this.layer, t, e.x, e.y, radius);
    if (!s) return false;
    const line = lineById(this.layer, s.line) as NonNullable<ReturnType<typeof lineById>>;
    const a = line.points[s.segment];
    const b = line.points[(s.segment + 1) % line.points.length];
    const ray = this.view?.ray(e.clientX, e.clientY);
    const p: Vec3 = ray ? closestOnSegmentToRay(a, b, ray) : [a[0] + (b[0] - a[0]) * s.t, a[1] + (b[1] - a[1]) * s.t, a[2] + (b[2] - a[2]) * s.t];
    this.edit(ops.insertVertex(this.layer, s.line, s.segment, p), "Вставка вершины", { lines: selectLine ? [s.line] : [], vertices: [{ line: s.line, index: s.segment + 1 }] });
    return true;
  }

  /** Adds a point with the active drawing tool (a click or typed coordinates). */
  addPoint(p: Vec3, t: ViewTransform | null = null, e: { x: number; y: number } | null = null): void {
    if (this.tool === "point") {
      const made = ops.addLine(this.layer, [p], false);
      return this.edit(made.layer, "Точка", { lines: [made.id], vertices: [] });
    }
    if (this.tool !== "line" && this.tool !== "polygon" && this.tool !== "tie") return;
    const drawing = this.drawing && lineById(this.layer, this.drawing.line) ? this.drawing : null;
    if (!drawing) {
      // A click on the end of an open line continues that line.
      const v = t && e && this.tool === "line" ? nearestVertex(this.layer, t, e.x, e.y, this.snap.radius) : null;
      const line = v ? lineById(this.layer, v.ref.line) : null;
      if (v && line && !line.closed && line.points.length > 1 && (v.ref.index === 0 || v.ref.index === line.points.length - 1)) {
        this.drawing = { line: line.id, atStart: v.ref.index === 0 };
        this.setSelection({ lines: [line.id], vertices: [] });
        return this.say("Продолжение линии: следующие точки добавляются к её концу");
      }
      const made = ops.addLine(this.layer, [p], false, toolAttrs(this.tool));
      this.drawing = { line: made.id, atStart: false };
      return this.edit(made.layer, this.tool === "tie" ? "Тай-линия" : "Новая линия", { lines: [made.id], vertices: [] });
    }
    const line = lineById(this.layer, drawing.line) as NonNullable<ReturnType<typeof lineById>>;
    // A click on the first point closes the line.
    const first = line.points[drawing.atStart ? line.points.length - 1 : 0];
    if (line.points.length >= 3 && samePoint(first, p)) {
      this.edit(ops.setClosed(this.layer, line.id, true), "Замыкание линии");
      return this.finishDrawing();
    }
    this.edit(ops.appendPoint(this.layer, line.id, p, drawing.atStart), "Точка линии");
    if (this.tool === "tie") this.finishDrawing();
  }

  private clickSelect(e: EditPointer, t: ViewTransform): void {
    if (this.assemble) return this.clickAssemble(e, t);
    const sel = this.selection;
    const v = nearestVertex(this.layer, t, e.x, e.y, this.snap.radius);
    if (v) {
      const has = sel.vertices.some((r) => sameRef(r, v.ref));
      if (e.shift) return this.setSelection({ lines: sel.lines, vertices: has ? sel.vertices.filter((r) => !sameRef(r, v.ref)) : [...sel.vertices, v.ref] });
      return this.setSelection({ lines: [v.ref.line], vertices: [v.ref] });
    }
    const s = nearestSegment(this.layer, t, e.x, e.y, 8);
    if (s) {
      const has = sel.lines.includes(s.line);
      if (e.shift) return this.setSelection({ lines: has ? sel.lines.filter((id) => id !== s.line) : [...sel.lines, s.line], vertices: sel.vertices });
      return this.setSelection({ lines: [s.line], vertices: [] });
    }
    if (!e.shift) this.setSelection(EMPTY_SELECTION);
  }

  // ---- «Собрать из контуров»
  startAssemble(): void {
    if (this.drawing) this.finishDrawing();
    this.tool = "select";
    this.assemble = { order: [], closed: false };
    this.selection = EMPTY_SELECTION;
    this.say("Сборка каркаса: щёлкайте замкнутые контуры по порядку — каркас растёт от контура к контуру");
  }

  stopAssemble(): void {
    this.assemble = null;
    this.selection = EMPTY_SELECTION;
    this.say(null);
  }

  /** Contours of the assembly that still exist, in order (a line may have been deleted meanwhile). */
  assembled(): number[] {
    return (this.assemble?.order ?? []).filter((id) => lineById(this.layer, id)?.closed);
  }

  /** Adds the picked contour (or takes it out, with a second click). */
  pickContour(id: number): void {
    const a = this.assemble;
    const line = a ? lineById(this.layer, id) : undefined;
    if (!a || !line) return;
    if (!line.closed || line.points.length < 3) return this.say("Контур должен быть замкнутым: выберите его в режиме правки и нажмите «Замкнуть»");
    a.order = a.order.includes(id) ? a.order.filter((x) => x !== id) : [...a.order, id];
    this.selection = { lines: [...a.order], vertices: [] };
    const n = a.order.length;
    this.say(n === 0 ? "Щёлкните первый контур" : n === 1 ? "Щёлкните следующий контур" : `Контуров в каркасе: ${n}. Следующий контур — щелчок; «Замкнуть торцы» закроет каркас`);
  }

  dropLastContour(): void {
    const a = this.assemble;
    if (a?.order.length) this.pickContour(a.order[a.order.length - 1]);
  }

  setAssembleClosed(closed: boolean): void {
    if (!this.assemble) return;
    this.assemble = { ...this.assemble, closed };
    this.emit();
  }

  private clickAssemble(e: EditPointer, t: ViewTransform): void {
    const v = nearestVertex(this.layer, t, e.x, e.y, this.snap.radius);
    const s = v ? null : nearestSegment(this.layer, t, e.x, e.y, 8);
    const id = v?.ref.line ?? s?.line;
    if (id === undefined) return this.say("Щёлкните по линии контура");
    this.pickContour(id);
  }

  private onDoubleClick(e: EditPointer, t: ViewTransform | null): boolean {
    if (this.drawing) {
      // The two clicks of the double click have put the same point twice: the second goes.
      const line = lineById(this.layer, this.drawing.line);
      const n = line?.points.length ?? 0;
      if (line && n >= 2) {
        const a = line.points[this.drawing.atStart ? 0 : n - 1];
        const b = line.points[this.drawing.atStart ? 1 : n - 2];
        if (samePoint(a, b)) this.dropPoint();
      }
      this.finishDrawing();
      return true;
    }
    if (this.tool === "select" && t && !nearestVertex(this.layer, t, e.x, e.y, this.snap.radius)) return this.insertAt(e, t, 8, true);
    return false;
  }

  // ---- commands of the session itself
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
      if (this.tool === "polygon" && !line.closed && line.points.length >= 3) this.edit(ops.setClosed(this.layer, line.id, true), "Замыкание полигона");
      else if (line.points.length < 2 && this.tool !== "point") this.edit(ops.deleteLines(this.layer, [line.id]), "Отмена линии", EMPTY_SELECTION);
      else if (this.tool === "polygon" && line.points.length < 3) this.say("Полигону нужно не меньше трёх вершин: линия оставлена незамкнутой");
    }
    this.emit();
  }

  /** Backspace: takes the last point off the line being drawn (the line goes with its only point). */
  dropPoint(): boolean {
    const d = this.drawing;
    const line = d ? lineById(this.layer, d.line) : null;
    if (!d || !line || !line.points.length) return false;
    const index = d.atStart ? 0 : line.points.length - 1;
    this.edit(ops.deleteVertices(this.layer, [{ line: line.id, index }]), "Удаление точки", { lines: [line.id], vertices: [] });
    if (!lineById(this.layer, line.id)) this.say("Линия убрана: точек не осталось");
    return true;
  }

  /** Esc: ends a drag or the drawing, else drops the selection, else returns to the select tool. */
  escape(): void {
    if (this.drag) {
      this.drag = null;
      if (this.history.pending) this.history.cancel();
      this.sourceLayer = null;
      return this.afterChange();
    }
    if (this.drawing) return this.finishDrawing();
    if (this.assemble) return this.stopAssemble();
    const sel = this.selection;
    if (sel.lines.length || sel.vertices.length) return this.setSelection(EMPTY_SELECTION);
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

  /**
   * A key of the editor: the command it ran, `lines.save` for the caller to save, or null when the key is not
   * the editor's (Enter and Backspace only while a line is being drawn).
   */
  key(e: EditKey): string | null {
    const id = keyCommand(e);
    switch (id) {
      case "lines.finish":
        if (!this.drawing) return null;
        this.finishDrawing();
        return id;
      case "lines.drop_point":
        return this.dropPoint() ? id : null;
      case "lines.escape":
        this.escape();
        return id;
      case "lines.delete":
        cmd.deleteSelection(this);
        return id;
      case "lines.undo":
        this.undo();
        return id;
      case "lines.redo":
        this.redo();
        return id;
      case "lines.select_all":
        cmd.selectAll(this);
        return id;
      case "lines.save":
        return id;
      default:
        return null;
    }
  }

  /** The last point of the line being drawn, or the selected vertex: what relative coordinates count from. */
  anchor(): Vec3 | null {
    if (this.drawing) {
      const line = lineById(this.layer, this.drawing.line);
      if (line?.points.length) return line.points[this.drawing.atStart ? 0 : line.points.length - 1];
    }
    const v = this.selection.vertices[0];
    return v ? vertexAt(this.layer, v) ?? null : null;
  }

  // ---- saving
  /** «было → стало» of the save (lines, touched, new, removed, vertices). */
  summary(): SaveSummary {
    return summarize(this.layer, this.loaded);
  }
  /** `PUT /lines/layers/{id}/geometry` body. */
  saveBody(note = ""): ArrayBuffer {
    return saveBody(this.layer, this.loaded, note);
  }
  /** `POST /lines/layers/{id}/move-lines` body for the selected lines (`lines.move_selected`). */
  moveBody(targetId: number, targetBase: number, note = ""): ArrayBuffer {
    return moveBody(this.layer, this.loaded, cmd.targetLines(this), targetId, targetBase, note);
  }

  /**
   * After a save (or a move) the layer as the core gives it back (`GET …/geometry`): the lines keep their ids
   * and points and take the stable numbers given to them; the steps before the save stay undoable. When the
   * answer does not line up with the working copy (lines dropped by a move), the working copy is the answer.
   */
  saved(reloaded: LoadedLayer, moved: readonly number[] = []): void {
    const next = rebaseOnSaved(this.layer, reloaded, moved);
    this.loaded = next ? next.loaded : reloaded;
    if (next) this.history.rebase(next.layer);
    else {
      this.history = layerHistory(reloaded.layer);
      this.drawing = null;
    }
    this.message = `Сохранено: версия ${reloaded.version}`;
    this.afterChange();
  }
}
