/**
 * The drawing tools of the session (ADR 0015 §3; moved out of `session.ts`): a point, a line, a polygon, a tie line
 * — a click adds a point to the line being drawn, a click on the end of an open line continues it, a click on the
 * first point closes it; Enter / a right click ends it, Backspace takes the last point off. No three.js.
 */
import { EMPTY_SELECTION, lineById, type AttrValue, type EditLayer, type Selection, type Vec3 } from "./model";
import * as ops from "./ops";
import { nearestVertex } from "./pick";
import type { ViewTransform } from "./snap";
import { toolAttrs, type SnapSettings, type Tool } from "./tools";

export interface DrawHost {
  readonly layer: EditLayer;
  readonly tool: Tool;
  readonly snap: SnapSettings;
  drawing: { line: number; atStart: boolean } | null;
  /** attributes every new line gets besides those of the tool (a contour on a section of a series) */
  readonly newAttrs?: Record<string, AttrValue>;
  pickLayer(): EditLayer;
  edit(layer: EditLayer, label: string, selection?: Selection): void;
  setSelection(selection: Selection): void;
  say(text: string | null): void;
  touch(): void;
}

const samePoint = (a: Vec3, b: Vec3) => Object.is(a[0], b[0]) && Object.is(a[1], b[1]) && Object.is(a[2], b[2]);

/** Adds a point with the active drawing tool; `t` and `e` — the view and the pixel of a click (none: typed). */
export function addPoint(s: DrawHost, p: Vec3, t: ViewTransform | null = null, e: { x: number; y: number } | null = null): void {
  const attrs = (): Record<string, AttrValue> => ({ ...toolAttrs(s.tool), ...s.newAttrs });
  if (s.tool === "point") {
    const made = ops.addLine(s.layer, [p], false, s.newAttrs ? attrs() : undefined);
    return s.edit(made.layer, "Точка", { lines: [made.id], vertices: [] });
  }
  if (s.tool !== "line" && s.tool !== "polygon" && s.tool !== "tie") return;
  const drawing = s.drawing && lineById(s.layer, s.drawing.line) ? s.drawing : null;
  if (!drawing) {
    // A click on the end of an open line continues that line.
    const v = t && e && s.tool === "line" ? nearestVertex(s.pickLayer(), t, e.x, e.y, s.snap.radius) : null;
    const line = v ? lineById(s.layer, v.ref.line) : null;
    if (v && line && !line.closed && line.points.length > 1 && (v.ref.index === 0 || v.ref.index === line.points.length - 1)) {
      s.drawing = { line: line.id, atStart: v.ref.index === 0 };
      s.setSelection({ lines: [line.id], vertices: [] });
      return s.say("Продолжение линии: следующие точки добавляются к её концу");
    }
    const made = ops.addLine(s.layer, [p], false, attrs());
    s.drawing = { line: made.id, atStart: false };
    return s.edit(made.layer, s.tool === "tie" ? "Тай-линия" : "Новая линия", { lines: [made.id], vertices: [] });
  }
  const line = lineById(s.layer, drawing.line) as NonNullable<ReturnType<typeof lineById>>;
  // A click on the first point closes the line.
  const first = line.points[drawing.atStart ? line.points.length - 1 : 0];
  if (line.points.length >= 3 && samePoint(first, p)) {
    s.edit(ops.setClosed(s.layer, line.id, true), "Замыкание линии");
    return finishDrawing(s);
  }
  s.edit(ops.appendPoint(s.layer, line.id, p, drawing.atStart), "Точка линии");
  if (s.tool === "tie") finishDrawing(s);
}

/** Ends the line being drawn: a polygon closes, a line left with one point goes away. */
export function finishDrawing(s: DrawHost): void {
  const d = s.drawing;
  s.drawing = null;
  const line = d ? lineById(s.layer, d.line) : null;
  if (line) {
    if (s.tool === "polygon" && !line.closed && line.points.length >= 3) s.edit(ops.setClosed(s.layer, line.id, true), "Замыкание полигона");
    else if (line.points.length < 2 && s.tool !== "point") s.edit(ops.deleteLines(s.layer, [line.id]), "Отмена линии", EMPTY_SELECTION);
    else if (s.tool === "polygon" && line.points.length < 3) s.say("Полигону нужно не меньше трёх вершин: линия оставлена незамкнутой");
  }
  s.touch();
}

/** Backspace: takes the last point off the line being drawn (the line goes with its only point). */
export function dropPoint(s: DrawHost): boolean {
  const d = s.drawing;
  const line = d ? lineById(s.layer, d.line) : null;
  if (!d || !line || !line.points.length) return false;
  const index = d.atStart ? 0 : line.points.length - 1;
  s.edit(ops.deleteVertices(s.layer, [{ line: line.id, index }]), "Удаление точки", { lines: [line.id], vertices: [] });
  if (!lineById(s.layer, line.id)) s.say("Линия убрана: точек не осталось");
  return true;
}
