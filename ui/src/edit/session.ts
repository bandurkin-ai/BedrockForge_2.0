/**
 * One editing session of a line layer (ADR 0015 §3–§5; ТЗ 7.22; BF `edit/session.ts`): the working copy with
 * undo, the active tool, the selection and snapping. No React and no three.js here: the 3D view feeds pointer
 * events and a ray under the cursor, the interface subscribes to changes. The commands are in `tools.ts`.
 *
 * Where a point falls — `drawPlane.ts`; which lines may be picked on a section of a series — `lock.ts`; the own
 * snap targets, rebuilt only for the lines an edit changed — `ownSnap.ts`. Coordinates are project float64.
 */
import type { SectionLine } from "../viz/section/math";
import type { SectionPlane } from "../viz/section/plane";
import { rayPlane, type Plane } from "./coords";
import { assembledContours, pickContour, startAssembly, stopAssembly, type Assembly } from "./assembleMode";
import { drawingPlane, onSection, placePoint } from "./drawPlane";
import { addPoint, dropPoint, finishDrawing } from "./drawing";
import { carryOver, moveBody, rebaseOnSaved, saveBody, summarize, type LoadedLayer, type SaveSummary } from "./io";
import { EMPTY_SELECTION, layerHistory, lineById, vertexAt, type AttrValue, type EditLayer, type History, type Selection, type Vec3, type VertexRef } from "./model";
import * as ops from "./ops";
import { boxSelection, clickSelection, nearestSegment, nearestVertex } from "./pick";
import { SectionLock } from "./lock";
import { OwnSnapSource } from "./ownSnap";
import { CoreProbe } from "./probe";
import type { SnapHit, ViewTransform } from "./snap";
import { SnapIndex } from "./snapIndex";
import * as cmd from "./tools";
import { closestOnSegmentToRay, DEFAULT_SNAP, sessionKey } from "./tools";
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
  /** the section of a series the points fall on (its plane t = 0), null — none */
  plane: SectionPlane | null = null;
  clip = false;
  /** «Текущий разрез / все»: on a section of a series only its lines are picked */
  readonly lock = new SectionLock();
  /** attributes of every new line (a contour on a section of a series: `SECTION_SET`, `SECTION`, `SECTION_NAME`) */
  newAttrs: Record<string, AttrValue> | undefined = undefined;
  view: SessionView | null = null;
  probe: WireframeProbe | null = null;
  hover: Hover | null = null;
  /** The line being drawn: its id and the end that grows. */
  drawing: { line: number; atStart: boolean } | null = null;
  /** «Собрать из контуров»: closed lines in the order clicked; `closed` — the open ends are capped. */
  assemble: Assembly | null = null;
  drag: Drag | null = null;
  message: string | null = null;
  /** A save (or a move) went through but its answer is not read (`GET …/geometry` failed): the next save reads
   * the layer first and rebases `sent` (`editor.ts`), never sends over the old base. */
  unsynced: { layerId: number; sent: EditLayer; moved: readonly number[]; name?: string } | null = null;
  private listeners = new Set<() => void>();
  private revision = 0;
  private readonly prober = new CoreProbe(this, () => this.transform());
  private readonly own = new OwnSnapSource();

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
    return drawingPlane(this);
  }

  /** The lines that may be picked: all, or those on the current section (`lock.ts`). */
  pickLayer(): EditLayer {
    return this.lock.pickable(this.layer);
  }

  /** The view transform; in plan a target without a height is drawn on the plan level. */
  private transform(): ViewTransform | null {
    const t = this.view?.transform() ?? null;
    if (!t || t.missingZ !== undefined) return t;
    return { ...t, missingZ: onSection(this) ? undefined : this.zLevel };
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

  /** The own snap targets follow the working copy: only the pieces with changed lines are rebuilt. */
  private refreshOwnSource(exclude?: VertexRef): void {
    const c = this.own.update(this.layer, exclude);
    for (const key of c.removed) this.snapIndex.removeSource(key);
    for (const s of c.set) this.snapIndex.setSource(s);
  }

  viewChanged(): void {
    const t = this.transform();
    if (t) this.snapIndex.setView(t);
    this.prober.forget();
  }

  // ---- where the cursor points
  /** The point a click at this cursor position gives, and what it snapped to. */
  resolve(e: { x: number; y: number; clientX: number; clientY: number }, extra: SnapHit | null = null): Hover {
    const ray = this.view?.ray(e.clientX, e.clientY) ?? null;
    let snap: SnapHit | null = null;
    if (this.snap.enabled) {
      snap = this.snapIndex.query(e.x, e.y, this.snap.radius, new Set(this.snap.kinds.filter((k) => k !== "grid")), ray ?? undefined);
      const wf = extra ?? this.prober.hitAt(e);
      if (wf && (!snap || (wf.kind === "wf-vertex" && snap.kind !== "vertex" && snap.kind !== "collar" && snap.kind !== "interval") || wf.distancePx < snap.distancePx - 4)) snap = wf;
      // The last point of the line being drawn is not a target for its own next point.
      if (snap && this.drawing) {
        const line = lineById(this.layer, this.drawing.line);
        const last = line && line.points[this.drawing.atStart ? 0 : line.points.length - 1];
        if (last && samePoint(last, snap.point)) snap = null;
      }
    }
    return { ...placePoint(this, this.snap, snap, ray), x: e.x, y: e.y };
  }

  // ---- pointer events from the view; true = the event is taken (the view does not rotate or pick)
  pointer(e: EditPointer): boolean {
    const t = this.transform();
    if (e.type === "leave") {
      this.leave();
      return false;
    }
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
    if (this.prober.wanted() && !d) {
      const at = { x: e.x, y: e.y, clientX: e.clientX, clientY: e.clientY };
      this.prober.schedule(at, () => {
        if (this.hover && Math.hypot(this.hover.x - at.x, this.hover.y - at.y) <= 3) {
          this.hover = this.resolve(at);
          this.emit();
        }
      });
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
    const v = nearestVertex(this.pickLayer(), t, e.x, e.y, this.snap.radius);
    if (v) {
      const sel = this.selection;
      if (!sel.vertices.some((r) => sameRef(r, v.ref))) this.setSelection({ lines: e.shift ? sel.lines : [], vertices: e.shift ? [...sel.vertices, v.ref] : [v.ref] });
      this.refreshOwnSource(v.ref);
      this.drag = { kind: "vertex", ref: v.ref, origin: vertexAt(this.layer, v.ref) as Vec3, moved: false };
      return true;
    }
    const s = nearestSegment(this.pickLayer(), t, e.x, e.y, 6);
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
      if (t && Math.hypot(d.x1 - d.x0, d.y1 - d.y0) > 3) this.setSelection(boxSelection(this.pickLayer(), t, this.selection, d.x0, d.y0, d.x1, d.y1, d.add));
      else this.emit();
      return true;
    }
    if (d.moved) this.history.commit(d.kind === "vertex" ? "Перемещение вершины" : "Перемещение линий");
    else if (this.history.pending) this.history.cancel();
    this.afterChange();
    // A press without movement is a click: let the click handler select.
    return d.moved;
  }

  private async onClick(e: EditPointer, t: ViewTransform | null): Promise<void> {
    if (!t) return;
    if (this.tool === "select") return this.clickSelect(e, t);
    if (this.tool === "insert") return this.insertAt(e, t, this.snap.radius, false) ? undefined : this.say("Щёлкните по сегменту линии");
    let extra: SnapHit | null = null;
    if (this.prober.wanted() && !this.prober.answered(e)) {
      // the core is asked: what the click was meant for may change meanwhile (a key, an undo, another tool)
      const was = { tool: this.tool, drawing: this.drawing, layer: this.layer };
      extra = await this.prober.run(e);
      if (was.tool !== this.tool || was.drawing !== this.drawing || was.layer !== this.layer) return this.say("Точка не поставлена: пока искалась привязка, правка изменилась. Щёлкните ещё раз");
    }
    const hit = this.resolve(e, extra);
    if (!hit.point) return this.say("Здесь точку поставить нельзя: плоскость построения видна с ребра. Поверните вид или введите координаты");
    this.addPoint(hit.point, t, e);
  }

  /** A new vertex on the segment under the cursor; false when there is none within `radius`. */
  private insertAt(e: EditPointer, t: ViewTransform, radius: number, selectLine: boolean): boolean {
    const s = nearestSegment(this.pickLayer(), t, e.x, e.y, radius);
    if (!s) return false;
    const line = lineById(this.layer, s.line) as NonNullable<ReturnType<typeof lineById>>;
    const a = line.points[s.segment];
    const b = line.points[(s.segment + 1) % line.points.length];
    const ray = this.view?.ray(e.clientX, e.clientY);
    const p: Vec3 = ray ? closestOnSegmentToRay(a, b, ray) : [a[0] + (b[0] - a[0]) * s.t, a[1] + (b[1] - a[1]) * s.t, a[2] + (b[2] - a[2]) * s.t];
    this.edit(ops.insertVertex(this.layer, s.line, s.segment, p), "Вставка вершины", { lines: selectLine ? [s.line] : [], vertices: [{ line: s.line, index: s.segment + 1 }] });
    return true;
  }

  /** Adds a point with the active drawing tool (a click or typed coordinates; `drawing.ts`). */
  addPoint(p: Vec3, t: ViewTransform | null = null, e: { x: number; y: number } | null = null): void {
    addPoint(this, p, t, e);
  }

  private clickSelect(e: EditPointer, t: ViewTransform): void {
    if (this.assemble) return this.clickAssemble(e, t);
    const next = clickSelection(this.pickLayer(), t, this.selection, e.x, e.y, this.snap.radius, e.shift);
    if (next !== this.selection) this.setSelection(next);
  }

  // ---- «Собрать из контуров»
  startAssemble(): void {
    if (this.drawing) this.finishDrawing();
    this.tool = "select";
    startAssembly(this);
  }
  stopAssemble(): void {
    stopAssembly(this);
  }
  /** Contours of the assembly that still exist, in order (a line may have been deleted meanwhile). */
  assembled(): number[] {
    return assembledContours(this);
  }
  /** Adds the picked contour (or takes it out, with a second click). */
  pickContour(id: number): void {
    pickContour(this, id);
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
    const v = nearestVertex(this.pickLayer(), t, e.x, e.y, this.snap.radius);
    const s = v ? null : nearestSegment(this.pickLayer(), t, e.x, e.y, 8);
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
    if (this.tool === "select" && t && !nearestVertex(this.pickLayer(), t, e.x, e.y, this.snap.radius)) return this.insertAt(e, t, 8, true);
    return false;
  }

  /** The cursor left the 3D window (or the editor closes): no hover, no pending probe of the core. */
  leave(): void {
    this.prober.stop();
    if (!this.hover) return;
    this.hover = null;
    this.emit();
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
    finishDrawing(this);
  }
  /** Backspace: takes the last point off the line being drawn (the line goes with its only point). */
  dropPoint(): boolean {
    return dropPoint(this);
  }

  /** Esc: ends a drag or the drawing, else drops the selection, else returns to the select tool. */
  escape(): void {
    if (this.drag) {
      this.drag = null;
      if (this.history.pending) this.history.cancel();
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

  /** A key of the editor (`tools.sessionKey`): the id of its command, null — not the editor's key. */
  key(e: EditKey): string | null {
    return sessionKey(this, e);
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
   * After a save (or a move) of `sent` — the working copy the body was made of — the layer as the core gives it
   * back (`GET …/geometry`): the lines of `sent` keep their ids and points and take the stable numbers given to
   * them; the steps before the save stay undoable. Edits made while the save was on their way (the save does not
   * block them) are carried over the new base and stay unsaved. When the answer does not line up with `sent`,
   * the working copy is the answer — or, with edits made meanwhile, those edits over it, every line unsaved.
   */
  saved(reloaded: LoadedLayer, moved: readonly number[] = [], sent: EditLayer = this.layer): void {
    const later = this.layer !== sent;
    const next = rebaseOnSaved(sent, reloaded, moved);
    this.loaded = next ? next.loaded : reloaded;
    if (next) this.history.rebaseSent(sent, next.layer, carryOver(sent, next.layer, moved));
    else if (later) this.history.savedAs(reloaded.layer);
    else {
      this.history = layerHistory(reloaded.layer);
      this.drawing = null;
    }
    this.message = later ? `Сохранено: версия ${reloaded.version}. Правки, сделанные во время сохранения, ещё не записаны — сохраните ещё раз` : `Сохранено: версия ${reloaded.version}`;
    this.afterChange();
  }
}
