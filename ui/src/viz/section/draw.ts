/**
 * Drawing a section line by two clicks (ТЗ 7.14 `section.draw`, BF SceneView): in the current view without
 * turning or fitting it, or on the mini-plan. The first click marks the start, an orange rubber band follows
 * the cursor, the second gives the line (unlimited, the window kept from the line before). Pure state.
 */
import { lineFromPoints, type SectionLine } from "./math";

export type DrawStage = "off" | "first" | "second";

export interface DrawState {
  stage: DrawStage;
  /** the first point (project coordinates), once given */
  first: { x: number; y: number } | null;
}

export const DRAW_OFF: DrawState = { stage: "off", first: null };

export function startDraw(): DrawState {
  return { stage: "first", first: null };
}

/**
 * A click at `p` (project coordinates). The second click gives the line; a click on the first point again
 * keeps waiting for the end.
 */
export function addPoint(state: DrawState, p: { x: number; y: number }, base: SectionLine | null): { state: DrawState; line: SectionLine | null } {
  if (state.stage === "off") return { state, line: null };
  if (state.stage === "first" || !state.first) return { state: { stage: "second", first: { x: p.x, y: p.y } }, line: null };
  const line = lineFromPoints(state.first, p, base);
  return line ? { state: DRAW_OFF, line } : { state, line: null };
}

/** The prompt over the view while drawing. */
export function drawHint(state: DrawState): string | null {
  if (state.stage === "off") return null;
  return `${state.stage === "first" ? "Укажите начало профиля" : "Укажите конец профиля"} — в виде или на плане · Esc — отмена`;
}
