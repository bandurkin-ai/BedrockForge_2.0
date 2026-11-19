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
import type { ShadowMode } from "./seriesApi";

export const sectionStore = new Store<{ draw: DrawState }>({ draw: DRAW_OFF });

/**
 * «Часть каркаса в окне» (ADR 0016 §5): under the section of a series the wireframes are shown by their exact
 * lines; checked — also the surface itself, clipped by the window. A setting of the window, not of the project.
 */
export const seriesView = new Store<{ surfaces: boolean }>({ surfaces: true });

/**
 * What the buttons of the tab «Разрезы» show pressed besides `seriesView`: the view is locked (`Scene` →
 * `onLock`), the shadow of the active series (null — no active series). The ribbon is built anew on each change.
 */
export const seriesRibbon = new Store<{ locked: boolean; shadow: ShadowMode | null }>({ locked: false, shadow: null });

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

/** The answer of `normalizeSeries` per stored key: the same key — the same object (a stable snapshot of `useStore`). */
const normalized = new WeakMap<object, ActiveSeries | null>();

/**
 * `section.series` as the core reads it (not strict): no id — no series; a broken k is 0, a broken seg — null. The
 * same stored key gives the same object: `useStore(viewStore, (k) => sectionOf(k).series)` is a snapshot of
 * `useSyncExternalStore`, and a new object on every read is an endless render (React #185) once a series is active.
 */
export function normalizeSeries(raw: unknown): ActiveSeries | null {
  if (!raw || typeof raw !== "object") return null;
  const known = normalized.get(raw);
  if (known !== undefined) return known;
  const r = raw as Record<string, unknown>;
  const out = !int(r.id, 1e12) || r.id === 0 ? null : { id: r.id, k: int(r.k, SERIES_K_MAX) ? r.k : 0, seg: int(r.seg, SERIES_SEG_MAX) ? r.seg : null };
  normalized.set(raw, out);
  return out;
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

let lastActive: ActiveSeries | null = null;

/**
 * The active series of the view keys as a selector of `useStore` (`useSyncExternalStore`): the same object while
 * id, k and seg are the same — `sectionOf(k).series` is a new object on every call, and a snapshot that is never
 * equal to the last one re-renders without end (React 18).
 */
export function activeSeriesOf(keys: Record<string, unknown>): ActiveSeries | null {
  const s = sectionOf(keys).series ?? null;
  if (!s) return null;
  const last = lastActive;
  if (last && last.id === s.id && last.k === s.k && last.seg === s.seg) return last;
  lastActive = s;
  return s;
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
