/**
 * The section of the project (ТЗ 7.14, 7.0.10): the view key `section` = `{line, clip}` is the source (the
 * project keeps it); here — reading it back normalized as the core does, and the state of drawing a line,
 * which lives only in the window.
 */
import { Store } from "../../shell/store";
import { DRAW_OFF, type DrawState } from "../../viz/section/draw";
import { currentScene } from "../scene/sceneHost";
import { DEFAULT_WINDOW, type SectionLine } from "../../viz/section/math";

export const sectionStore = new Store<{ draw: DrawState }>({ draw: DRAW_OFF });

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** A line from stored state; null when it is missing or broken. */
export function normalizeLine(raw: unknown): SectionLine | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!finite(r.x0) || !finite(r.y0) || !finite(r.az)) return null;
  const length = finite(r.length) && r.length > 0 ? r.length : null;
  const win = (v: unknown) => (finite(v) && v >= 0 ? v : DEFAULT_WINDOW);
  return { x0: r.x0, y0: r.y0, az: ((r.az % 360) + 360) % 360, length, towards: win(r.towards), away: win(r.away) };
}

/** `section` of the view keys: the line and whether clipping is on (no line — no clipping). */
export function sectionOf(keys: Record<string, unknown>): { line: SectionLine | null; clip: boolean } {
  const s = (keys.section ?? {}) as Record<string, unknown>;
  const line = normalizeLine(s.line);
  return { line, clip: line !== null && s.clip === true };
}

/** «Разрез: азимут 45°, окно 25 / 25 м» — a line for the command log and Claude. */
export function describeLine(line: SectionLine, clip: boolean): string {
  const r = (v: number) => Math.round(v * 100) / 100;
  const len = line.length === null ? "без ограничения длины" : `длина ${r(line.length)} м`;
  return `Разрез: начало ${r(line.x0)}; ${r(line.y0)}, азимут ${r(line.az)}°, ${len}, окно от ${r(line.towards)} до ${r(line.away)} м, отсечение ${clip ? "вкл" : "выкл"}`;
}

/** Stops drawing a line; false when none was being drawn (Esc then clears the selection). */
export function cancelSectionDraw(): boolean {
  if (sectionStore.get().draw.stage === "off") return false;
  sectionStore.set({ draw: DRAW_OFF });
  currentScene()?.setDrawing(DRAW_OFF);
  return true;
}
