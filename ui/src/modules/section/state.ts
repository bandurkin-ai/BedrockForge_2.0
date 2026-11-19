/**
 * The section of the project (ТЗ 7.14, 8.1, 7.0.10): the view key `section` = `{line, clip[, series]}` is the
 * source (the project keeps it); here — reading it back normalized as the core does (`viewstate/keys.py`), and
 * the state of drawing a line, which lives only in the window. `series` — the active series of sections
 * `{id, k, seg}` (ADR 0016 §4); a key of Э7 without it reads as it was.
 */
import { Store } from "../../shell/store";
import { DRAW_OFF, type DrawState } from "../../viz/section/draw";
import { currentScene } from "../scene/sceneHost";
import { DEFAULT_WINDOW, type SectionLine } from "../../viz/section/math";

export const sectionStore = new Store<{ draw: DrawState }>({ draw: DRAW_OFF });

/**
 * «Часть каркаса в окне» (ADR 0016 §5): under the section of a series the wireframes are shown by their exact
 * lines; checked — also the surface itself, clipped by the window. A setting of the window, not of the project.
 */
export const seriesView = new Store<{ surfaces: boolean }>({ surfaces: true });

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

/** The active series of sections: `{id, k, seg}` — the object, the section from 0, the polyline segment or null. */
export interface ActiveSeries {
  id: number;
  k: number;
  seg: number | null;
}

const SERIES_K_MAX = 5000;
const SERIES_SEG_MAX = 1000;
const int = (v: unknown, max: number): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0 && v < max;

/** `section.series` as the core reads it (not strict): no id — no series; a broken k is 0, a broken seg — null. */
export function normalizeSeries(raw: unknown): ActiveSeries | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!int(r.id, 1e12) || r.id === 0) return null;
  return { id: r.id, k: int(r.k, SERIES_K_MAX) ? r.k : 0, seg: int(r.seg, SERIES_SEG_MAX) ? r.seg : null };
}

export interface SectionKey {
  /** the free line when it is the section (null while a series is active) */
  line: SectionLine | null;
  clip: boolean;
  /** the active series; absent — none (a key of Э7 reads exactly as before) */
  series?: ActiveSeries;
  /** while a series is active: the last free line, kept for `S`, profiles and «Через скважину» */
  free?: SectionLine | null;
}

/** `section` of the view keys: the section shown (the free line or the active series) and whether clipping is on
 * (nothing — no clipping). While a series is active `line` is null, so what reads `{line, clip}` never clips by
 * the free line hidden under it. */
export function sectionOf(keys: Record<string, unknown>): SectionKey {
  const s = (keys.section ?? {}) as Record<string, unknown>;
  const line = normalizeLine(s.line);
  const series = normalizeSeries(s.series);
  if (series) return { line: null, clip: s.clip === true, series, free: line };
  return { line, clip: line !== null && s.clip === true };
}

/** The last free line of the key, whether or not a series is active. */
export function freeLine(keys: Record<string, unknown>): SectionLine | null {
  const k = sectionOf(keys);
  return k.series ? (k.free ?? null) : k.line;
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
