/**
 * Tools, keys, snapping settings and the editing commands of the string editor (ADR 0015 §3, §5; ТЗ 7.22;
 * BF `edit/session.ts`). The commands are plain functions over the public interface of an `EditSession`, one
 * per command of the registry (ribbon, Ctrl+K, keys, assistant), with the reason when one is not available.
 */
import type { Plane, Ray } from "./coords";
import { ROLE_FIELD, TIE } from "./io";
import { EMPTY_SELECTION, lineById, vertexAt, type AttrValue, type EditLayer, type Selection, type Vec3, type VertexRef } from "./model";
import * as ops from "./ops";
import { projectPoint, SNAP_TITLES, type SnapHit, type SnapKind, type ViewTransform } from "./snap";

export type Tool = "select" | "line" | "polygon" | "point" | "tie" | "insert";

export interface ToolInfo {
  id: Tool;
  title: string;
  action: string;
  keys: string;
}

/** The tools of `lines.tool` in the order of the panel. */
export const TOOLS: readonly ToolInfo[] = [
  { id: "select", title: "Выбор и перемещение", action: "Щелчок — выбрать, тянуть — переместить вершину или линию; Ctrl — рамка, Shift — добавить к выбору", keys: "V" },
  { id: "line", title: "Линия", action: "Щелчок — точка; щелчок по концу линии — продолжить её", keys: "L" },
  { id: "polygon", title: "Полигон", action: "Щелчок — вершина; закончить — полигон замыкается", keys: "P" },
  { id: "point", title: "Точка", action: "Щелчок — отдельная точка", keys: "T" },
  { id: "tie", title: "Тай-линия", action: "Две точки между контурами: связь для «Собрать из контуров»", keys: "Y" },
  { id: "insert", title: "Вставить вершину", action: "Щелчок по отрезку — новая вершина на нём", keys: "I" },
];

export const TOOL_TITLES = Object.fromEntries(TOOLS.map((t) => [t.id, t.title])) as Record<Tool, string>;

/** Attributes of a new line of the tool: a tie line is marked `ROLE = TIE` (ADR 0015 §1). */
export function toolAttrs(tool: Tool): Record<string, AttrValue> {
  return tool === "tie" ? { [ROLE_FIELD]: TIE } : {};
}

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

export const WF_KINDS: readonly SnapKind[] = ["wf-vertex", "wf-edge"];

/** The pause before asking the core for the wireframe triangle under the cursor (ADR 0015 §4). */
export const PROBE_DELAY_MS = 30;

export interface EditPointer {
  /** `leave` — the cursor left the 3D window */
  type: "down" | "move" | "up" | "click" | "dblclick" | "leave";
  /** px inside the canvas */
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  button: number;
  shift: boolean;
  ctrl: boolean;
}

/** What the 3D view gives the session: the cursor ray and the view transform. Nothing else. */
export interface SessionView {
  ray(clientX: number, clientY: number): Ray | null;
  transform(): ViewTransform | null;
}

/** The triangle of a wireframe under a ray (exact corners), from `POST /wireframes/pick`; aborted when stale. */
export type WireframeProbe = (ray: Ray, signal: AbortSignal) => Promise<{ corners: Vec3[]; name: string } | null>;

export interface Hover {
  point: Vec3 | null;
  snap: SnapHit | null;
  x: number;
  y: number;
}

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
  if (t === 0) return [a[0], a[1], a[2]];
  if (t === 1) return [b[0], b[1], b[2]];
  return [a[0] + u[0] * t, a[1] + u[1] * t, a[2] + u[2] * t];
}

/** A vertex or an edge of the exact triangle under the cursor, within the radius; vertices first. */
export function wireframeHit(corners: Vec3[], name: string, x: number, y: number, ray: Ray, t: ViewTransform, kinds: ReadonlySet<SnapKind>, radius: number): SnapHit | null {
  const screen = corners.map((c) => projectPoint(t.matrix, t.width, t.height, c[0], c[1], c[2]));
  let best: SnapHit | null = null;
  if (kinds.has("wf-vertex")) {
    corners.forEach((c, i) => {
      const s = screen[i];
      if (!s) return;
      const d = Math.hypot(s[0] - x, s[1] - y);
      if (d <= radius && (!best || d < best.distancePx)) best = { kind: "wf-vertex", point: c, label: `${SNAP_TITLES["wf-vertex"]} · ${name}`, distancePx: d };
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
      if (d <= radius && (!best || d < (best as SnapHit).distancePx)) {
        best = { kind: "wf-edge", point: closestOnSegmentToRay(corners[i], corners[(i + 1) % 3], ray), label: `${SNAP_TITLES["wf-edge"]} · ${name}`, distancePx: d };
      }
    }
  }
  return best;
}

// ---- keys

export interface EditKey {
  key: string;
  ctrl: boolean;
  shift: boolean;
}

/** Keys of the editor (ТЗ 7.22) → commands of the registry. */
export function keyCommand(e: EditKey): string | null {
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (e.ctrl) {
    if (k === "z" || k === "я") return e.shift ? "lines.redo" : "lines.undo";
    if (k === "y" || k === "н") return "lines.redo";
    if (k === "s" || k === "ы") return "lines.save";
    if (k === "a" || k === "ф") return "lines.select_all";
    return null;
  }
  if (k === "Enter") return "lines.finish";
  if (k === "Backspace") return "lines.drop_point";
  if (k === "Escape") return "lines.escape";
  if (k === "Delete") return "lines.delete";
  return null;
}

/** What `sessionKey` needs of a session: the keys it runs itself. */
export interface KeyHost {
  readonly drawing: { line: number; atStart: boolean } | null;
  finishDrawing(): void;
  dropPoint(): boolean;
  escape(): void;
}

/**
 * A key of the editor: Enter, Backspace and Esc the session runs itself (the command id is returned; Enter and
 * Backspace only while a line is being drawn, else null — not the editor's key); Delete, Ctrl+A, Ctrl+Z,
 * Ctrl+Y and Ctrl+S give the id of their command for the caller to run through the registry (`editKeys.ts`).
 */
export function sessionKey(s: KeyHost, e: EditKey): string | null {
  const id = keyCommand(e);
  if (id === "lines.finish") {
    if (!s.drawing) return null;
    s.finishDrawing();
  } else if (id === "lines.drop_point") return s.dropPoint() ? id : null;
  else if (id === "lines.escape") s.escape();
  return id;
}

// ---- commands

/** What the commands need of a session. */
export interface CommandHost {
  readonly layer: EditLayer;
  readonly selection: Selection;
  readonly drawing: { line: number; atStart: boolean } | null;
  readonly dirty: boolean;
  readonly canUndo: boolean;
  readonly canRedo: boolean;
  /** a new step (nothing when `layer` is the present one) and the selection after it */
  edit(layer: EditLayer, label: string, selection?: Selection): void;
  setSelection(selection: Selection): void;
  say(text: string | null): void;
  /** the plane points fall on: for an open line's offset */
  drawingPlane(): Plane;
}

/** Lines the commands work on: the selected ones, or those of the selected vertices. */
export function targetLines(s: CommandHost): number[] {
  const sel = s.selection;
  const ids = sel.lines.length ? sel.lines : [...new Set(sel.vertices.map((v) => v.line))];
  return ids.filter((id) => lineById(s.layer, id));
}

const NEED_LINE = "Сначала выберите линию: инструмент «Выбор», щелчок по линии";

/** Why a command cannot run now (the panel shows it), or null. */
export function blocked(s: CommandHost, id: string): string | null {
  const lines = targetLines(s);
  const sel = s.selection;
  switch (id) {
    case "lines.undo":
      return s.canUndo ? null : "Отменять нечего";
    case "lines.redo":
      return s.canRedo ? null : "Возвращать нечего";
    case "lines.save":
      return s.dirty ? null : "Изменений нет";
    case "lines.delete":
      return sel.lines.length || sel.vertices.length ? null : "Нечего удалять: выберите линии или вершины";
    case "lines.break":
      return sel.vertices.length === 1 ? null : "Чтобы разорвать линию, выберите одну её вершину";
    case "lines.join":
      return lines.length === 2 ? null : "Чтобы соединить, выберите ровно две незамкнутые линии (Shift — добавить к выбору)";
    case "lines.close":
      if (!lines.length) return NEED_LINE;
      return lines.some((l) => (lineById(s.layer, l)?.points.length ?? 0) >= 3) ? null : "Замкнуть можно линию не короче трёх вершин";
    case "lines.finish":
    case "lines.drop_point":
      return s.drawing ? null : "Линия не рисуется";
    case "lines.open":
    case "lines.reverse":
    case "lines.translate":
    case "lines.copy":
    case "lines.offset":
    case "lines.simplify":
    case "lines.densify":
    case "lines.smooth":
    case "lines.attrs":
    case "lines.move_selected":
      return lines.length ? null : NEED_LINE;
    default:
      return null;
  }
}

function each(s: CommandHost, label: string, fn: (layer: EditLayer, id: number) => EditLayer): void {
  const ids = targetLines(s);
  if (!ids.length) return s.say(NEED_LINE);
  s.edit(ids.reduce(fn, s.layer), label, { lines: ids, vertices: [] });
}

export function deleteSelection(s: CommandHost): void {
  const sel = s.selection;
  if (sel.vertices.length) return s.edit(ops.deleteVertices(s.layer, sel.vertices), "Удаление вершин", EMPTY_SELECTION);
  if (sel.lines.length) return s.edit(ops.deleteLines(s.layer, sel.lines), "Удаление линий", EMPTY_SELECTION);
  s.say("Нечего удалять: выберите линии или вершины");
}

export function setClosed(s: CommandHost, closed: boolean): void {
  const ids = targetLines(s);
  if (!ids.length) return s.say(NEED_LINE);
  if (closed && ids.every((id) => (lineById(s.layer, id)?.points.length ?? 0) < 3)) return s.say("Замкнуть можно линию не короче трёх вершин");
  s.edit(ids.reduce((l, id) => ops.setClosed(l, id, closed), s.layer), closed ? "Замыкание" : "Размыкание", { lines: ids, vertices: [] });
}

export const reverse = (s: CommandHost): void => each(s, "Разворот направления", ops.reverseLine);
export const simplify = (s: CommandHost, tolerance: number): void => each(s, "Прореживание", (l, id) => ops.simplifyLine(l, id, tolerance));
export const densify = (s: CommandHost, step: number): void => each(s, "Сгущение", (l, id) => ops.densifyLine(l, id, step));
export const smooth = (s: CommandHost, iterations = 1): void => each(s, "Сглаживание", (l, id) => ops.smoothLine(l, id, iterations));

export function breakAtVertex(s: CommandHost): void {
  const v = s.selection.vertices;
  if (v.length !== 1) return s.say("Чтобы разорвать линию, выберите одну её вершину");
  const made = ops.breakAt(s.layer, v[0]);
  s.edit(made.layer, "Разрыв линии", { lines: made.ids, vertices: [] });
}

export function join(s: CommandHost): void {
  const ids = targetLines(s);
  if (ids.length !== 2) return s.say("Чтобы соединить, выберите ровно две незамкнутые линии (Shift — добавить к выбору)");
  const made = ops.joinLines(s.layer, ids[0], ids[1]);
  if (!made) return s.say("Соединяются только незамкнутые линии");
  s.edit(made.layer, "Соединение линий", { lines: [made.id], vertices: [] });
}

/** Shift (or copy) by a vector; [0, 0, dz] — by Z. */
export function translate(s: CommandHost, delta: Vec3, copy = false): void {
  const ids = targetLines(s);
  if (!ids.length) return s.say(NEED_LINE);
  if (copy) {
    const made = ops.copyLines(s.layer, ids, delta);
    return s.edit(made.layer, "Копия линий", { lines: made.ids.length ? made.ids : ids, vertices: [] });
  }
  s.edit(ops.translateLines(s.layer, ids, delta), "Сдвиг линий", { lines: ids, vertices: [] });
}

export function offset(s: CommandHost, distance: number): void {
  const ids = targetLines(s);
  if (!ids.length) return s.say(NEED_LINE);
  let layer = s.layer;
  const made: number[] = [];
  for (const id of ids) {
    const line = lineById(layer, id) as NonNullable<ReturnType<typeof lineById>>;
    const r = ops.offsetLine(layer, id, distance, line.closed ? undefined : s.drawingPlane());
    if (r) {
      layer = r.layer;
      made.push(r.id);
    }
  }
  if (!made.length) return s.say("Копия со смещением не получилась: линия слишком мала для такого смещения");
  s.edit(layer, "Копия со смещением", { lines: made, vertices: [] });
}

export function setAttrs(s: CommandHost, patch: Record<string, AttrValue | undefined>): void {
  const ids = targetLines(s);
  if (!ids.length) return s.say(NEED_LINE);
  s.edit(ops.setAttrs(s.layer, ids, patch), "Атрибуты линий", s.selection);
}

export function moveVertexTo(s: CommandHost, ref: VertexRef, p: Vec3): void {
  if (vertexAt(s.layer, ref)) s.edit(ops.moveVertex(s.layer, ref, p), "Координаты вершины", s.selection);
}

export function selectAll(s: CommandHost): void {
  s.setSelection({ lines: s.layer.lines.map((l) => l.id), vertices: [] });
}

export function selectLines(s: CommandHost, ids: number[]): void {
  s.setSelection({ lines: ids.filter((id) => lineById(s.layer, id)), vertices: [] });
}

/** `lines.add_line {points, closed}`: a whole line at once (commands, the assistant). Returns its id, −1 for no points. */
export function addLine(s: CommandHost, points: Vec3[], closed: boolean, attrs: Record<string, AttrValue> = {}): number {
  const made = ops.addLine(s.layer, points, closed && points.length >= 3, attrs);
  if (made.id < 0) {
    s.say("Нужна хотя бы одна точка");
    return -1;
  }
  s.edit(made.layer, closed ? "Новый полигон" : "Новая линия", { lines: [made.id], vertices: [] });
  return made.id;
}
