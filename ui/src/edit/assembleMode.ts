/**
 * «Собрать из контуров» inside the editing session (ADR 0015 §6; moved out of `session.ts`): closed lines clicked
 * in order, a second click takes one out; `closed` — the open ends are capped. The session keeps the state
 * (`EditSession.assemble`) and delegates here. No three.js.
 */
import { EMPTY_SELECTION, lineById, type EditLayer, type Selection } from "./model";

export interface Assembly {
  order: number[];
  closed: boolean;
}

export interface AssemblyHost {
  readonly layer: EditLayer;
  assemble: Assembly | null;
  selection: Selection;
  say(text: string | null): void;
}

export function startAssembly(s: AssemblyHost): void {
  s.assemble = { order: [], closed: false };
  s.selection = EMPTY_SELECTION;
  s.say("Сборка каркаса: щёлкайте замкнутые контуры по порядку — каркас растёт от контура к контуру");
}

export function stopAssembly(s: AssemblyHost): void {
  s.assemble = null;
  s.selection = EMPTY_SELECTION;
  s.say(null);
}

/** Contours of the assembly that still exist, in order (a line may have been deleted meanwhile). */
export function assembledContours(s: AssemblyHost): number[] {
  return (s.assemble?.order ?? []).filter((id) => lineById(s.layer, id)?.closed);
}

/** Adds the picked contour (or takes it out, with a second click). */
export function pickContour(s: AssemblyHost, id: number): void {
  const a = s.assemble;
  const line = a ? lineById(s.layer, id) : undefined;
  if (!a || !line) return;
  if (!line.closed || line.points.length < 3) return s.say("Контур должен быть замкнутым: выберите его в режиме правки и нажмите «Замкнуть»");
  a.order = a.order.includes(id) ? a.order.filter((x) => x !== id) : [...a.order, id];
  s.selection = { lines: [...a.order], vertices: [] };
  const n = a.order.length;
  s.say(n === 0 ? "Щёлкните первый контур" : n === 1 ? "Щёлкните следующий контур" : `Контуров в каркасе: ${n}. Следующий контур — щелчок; «Замкнуть торцы» закроет каркас`);
}
