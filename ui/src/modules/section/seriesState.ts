/**
 * Series of sections in the window (ТЗ 8.1; ADR 0016 §3–4): the list of the project, the tables of sections by
 * version, the active series and its section k in the view key `section.series {id, k, seg}` (decision 13: the
 * current section is not part of the series — `]` does not change its version, saved views remember it).
 *
 * The key is changed through `updateView`: the scene follows at once, the project gets it from `ViewSync` 400 ms
 * after the last change — never more often than once in 300 ms while `]` is held, and the last value always
 * (flushed also on closing the project and the window). The list follows `objects.changed {kind: "section_series"}`.
 *
 *     seriesStore        {list, limit, loaded, error, tables}
 *     currentSection()   the active series: row, geometry, k, seg, plane, name (from the table)
 *     sectionPlaneOf()   what the scene shows: the plane of the series or of the free line
 *     setActiveSeries()  writes `section.series` (null — the free line again)
 *     seriesRange()      s0, s1 over the visible data along v̂, multiples of Δ, K ≤ 5 000
 */
import type { App } from "../../shell/app";
import { Store } from "../../shell/store";
import type { Vec3 } from "../../viz/core/vec3";
import type { SectionLine } from "../../viz/section/math";
import { axisDot, planeFromLine, type SectionPlane } from "../../viz/section/plane";
import { MAX_K, seriesCount, seriesGeometry, type PolylineGeometry, type SeriesGeometry } from "../../viz/section/series";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { sceneObjects } from "../scene/objects";
import { currentScene } from "../scene/sceneHost";
import { patchFields } from "../scene/viewSync";
import { seriesApi, seriesFault, type SeriesFault, type SeriesRow, type SeriesTable } from "./seriesApi";
import { freeLine, sectionOf, seriesView, type ActiveSeries } from "./state";

export { seriesView } from "./state";

export type { ActiveSeries } from "./state";
export type Geometry = SeriesGeometry | PolylineGeometry;

export interface SeriesState {
  list: SeriesRow[];
  limit: number;
  /** the list of the open project has been read (or failed: `error`) */
  loaded: boolean;
  error: SeriesFault | null;
  /** the tables read, by series id; one is current while its `version` is the version of the row */
  tables: Record<number, SeriesTable>;
}

const EMPTY: SeriesState = { list: [], limit: 1000, loaded: false, error: null, tables: {} };
export const seriesStore = new Store<SeriesState>(EMPTY);

export const seriesById = (id: number, s: SeriesState = seriesStore.get()): SeriesRow | undefined => s.list.find((r) => r.id === id);

// ---------------------------------------------------------------- geometry

const geoms = new Map<string, Geometry | null>();

/** The geometry of a series from its parameters, cached by id and version; null — parameters it cannot take. */
export function geometryOf(row: SeriesRow): Geometry | null {
  const key = `${row.id}:${row.version}`;
  if (!geoms.has(key)) {
    let g: Geometry | null = null;
    try {
      g = seriesGeometry(row.params);
    } catch {
      g = null;
    }
    if (geoms.size > 64) geoms.clear();
    geoms.set(key, g);
  }
  return geoms.get(key) ?? null;
}

/** Steps of the series: K sections, or the segments of a polyline section. */
export const stepsOf = (g: Geometry): number => (g.kind === "polyline" ? g.segments : g.count);

export interface CurrentSection {
  row: SeriesRow;
  geom: Geometry;
  /** the section from 0 (a polyline: 0) */
  k: number;
  /** the segment of a polyline section, null for the others */
  seg: number | null;
  /** the position along the series: k, or the segment of a polyline */
  index: number;
  /** K, or the number of segments */
  steps: number;
  plane: SectionPlane;
  /** the name of the section (from the table of its version), null until it is read */
  name: string | null;
}

const clampInt = (v: number, n: number) => Math.min(Math.max(Math.trunc(v), 0), Math.max(n - 1, 0));

/** The active series as the window shows it; null — none, not read yet, or one it cannot show. */
export function currentSection(keys: Record<string, unknown> = viewStore.get(), state: SeriesState = seriesStore.get()): CurrentSection | null {
  const sel = sectionOf(keys).series ?? null;
  const row = sel ? seriesById(sel.id, state) : undefined;
  const geom = row ? geometryOf(row) : null;
  if (!sel || !row || !geom) return null;
  const steps = stepsOf(geom);
  const poly = geom.kind === "polyline";
  const index = clampInt(poly ? (sel.seg ?? 0) : sel.k, steps);
  const k = poly ? 0 : index;
  const table = state.tables[row.id];
  const name = table && table.version === row.version ? (table.sections[k]?.name ?? null) : null;
  return { row, geom, k, seg: poly ? index : null, index, steps, plane: geom.plane(index), name };
}

export interface SectionShown {
  plane: SectionPlane | null;
  clip: boolean;
  /** the free line of 7.14 when it is what is shown */
  line: SectionLine | null;
  series: ActiveSeries | null;
  /** the active series is not read yet: the scene keeps what it shows */
  pending: boolean;
}

/** What the scene shows for the view keys: the section of the active series, otherwise the free line. */
export function sectionPlaneOf(keys: Record<string, unknown> = viewStore.get(), state: SeriesState = seriesStore.get()): SectionShown {
  const sec = sectionOf(keys);
  const line = freeLine(keys);
  if (sec.series) {
    const cur = currentSection(keys, state);
    if (cur) return { plane: cur.plane, clip: sec.clip, line: null, series: sec.series, pending: false };
    if (!state.loaded) return { plane: null, clip: sec.clip, line, series: sec.series, pending: true };
  }
  return { plane: line ? planeFromLine(line) : null, clip: sec.clip && line !== null, line, series: null, pending: false };
}

type SectionTarget = { setSection(line: SectionLine | null, clip: boolean): void; setSectionPlane(plane: SectionPlane | null, clip: boolean, line?: SectionLine | null): void };

/** Shows the section of the view keys in the scene (`SceneView` on every change of the key `section`). */
export function applySectionKey(scene: SectionTarget | null, keys: Record<string, unknown> = viewStore.get()): void {
  if (!scene) return;
  const s = sectionPlaneOf(keys);
  if (s.pending) return;
  if (s.series && s.plane) scene.setSectionPlane(s.plane, s.clip, null);
  else scene.setSection(s.line, s.clip);
}

/** «Серия «Профили» · разрез 3 из 12 · 5050N» (a polyline: «сегмент 2 из 3»); null — no active series. */
export function seriesStatus(cur: CurrentSection | null = currentSection()): string | null {
  if (!cur) return null;
  const what = cur.seg !== null ? "сегмент" : "разрез";
  const name = cur.name && cur.seg === null ? ` · ${cur.name}` : "";
  return `Серия «${cur.row.name}» · ${what} ${cur.index + 1} из ${cur.steps}${name}`;
}

/** Why ×2…×5 are off (ADR 0016 decision 7): the plane and the orthographic view of an inclined series would bend. */
export const VEXAG_INCLINED = "Пока активна наклонная серия, преувеличение — ×1: иначе плоскость разреза искажается";

/** The surfaces of wireframes are hidden now: a series is shown with clipping and «часть каркаса в окне» is off. */
export function surfacesHidden(keys: Record<string, unknown> = viewStore.get()): boolean {
  return !seriesView.get().surfaces && sectionOf(keys).clip && currentSection(keys) !== null;
}

// ---------------------------------------------------------------- the key

/** Makes a series active at section k (segment `seg`), or the free line again (null); `clip` — set it too. */
export function setActiveSeries(sel: ActiveSeries | null, clip?: boolean): void {
  const patch: Record<string, unknown> = { series: sel ? { id: sel.id, k: sel.k, seg: sel.seg } : null };
  if (clip !== undefined) patch.clip = clip;
  updateView("section", patchFields(patch));
  applySectionKey(currentScene());
}

// ---------------------------------------------------------------- the list and the tables

let generation = 0;
const inflight = new Map<string, Promise<SeriesTable>>();

export function clearSeries(): void {
  generation++;
  inflight.clear();
  seriesStore.set(EMPTY);
}

/** Reads the list of the open project; the tables of changed or deleted series are dropped. */
export async function loadSeries(): Promise<SeriesState> {
  const gen = ++generation;
  try {
    const r = await seriesApi.list();
    if (gen !== generation) return seriesStore.get();
    const old = seriesStore.get().tables;
    const tables: Record<number, SeriesTable> = {};
    for (const row of r.series) if (old[row.id]?.version === row.version) tables[row.id] = old[row.id];
    seriesStore.set({ list: r.series, limit: r.limit, loaded: true, error: null, tables });
  } catch (e) {
    if (gen === generation) seriesStore.set({ loaded: true, error: seriesFault(e) });
  }
  return seriesStore.get();
}

/** The table of the sections of a series in its current version (read once per version). */
export async function ensureTable(id: number): Promise<SeriesTable> {
  const row = seriesById(id);
  if (!row) throw new Error("Серия разрезов не найдена: её удалили. Обновите список серий.");
  const have = seriesStore.get().tables[id];
  if (have && have.version === row.version) return have;
  const key = `${id}:${row.version}`;
  let p = inflight.get(key);
  if (!p) {
    const gen = generation;
    p = seriesApi.table(id).then((t) => {
      if (gen === generation && (seriesById(id)?.version ?? -1) <= t.version) seriesStore.set((s) => ({ tables: { ...s.tables, [id]: t } }));
      return t;
    });
    p.finally(() => inflight.delete(key)).catch(() => undefined);
    inflight.set(key, p);
  }
  return p;
}

/** A row of the list replaced by the core's answer (after a change), without waiting for the event. */
export function putSeries(row: SeriesRow): void {
  seriesStore.set((s) => {
    const list = s.list.some((r) => r.id === row.id) ? s.list.map((r) => (r.id === row.id ? row : r)) : [...s.list, row];
    return { list };
  });
}

export function dropSeries(id: number): void {
  seriesStore.set((s) => {
    const tables = { ...s.tables };
    delete tables[id];
    return { list: s.list.filter((r) => r.id !== id), tables };
  });
}

/**
 * The list follows the project and `objects.changed {kind: "section_series"}`; an active series that is gone
 * leaves the free line; the table of the active series is read when it becomes active or changes; the scene
 * follows the list (a series read after the key).
 */
let unbind: (() => void)[] = [];

export function bindSeriesState(app: App): void {
  unbind.forEach((fn) => fn()); // one app at a time (the tests create several)
  unbind = [];
  let uid: string | null = null;
  unbind.push(projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    clearSeries();
    if (cur) void loadSeries();
  }));
  unbind.push(onCoreEvent((event, data) => {
    if (event !== "objects.changed" || !projectStore.get().current) return;
    try {
      if ((JSON.parse(data) as { kind?: string }).kind !== "section_series") return;
    } catch {
      return;
    }
    void loadSeries();
  }));
  let shown = "";
  const follow = () => {
    const s = seriesStore.get();
    const sel = sectionOf(viewStore.get()).series ?? null;
    if (sel && s.loaded && !s.error && !seriesById(sel.id, s) && projectStore.get().current) {
      app.store.set({ statusNote: "Активная серия разрезов удалена: показана свободная линия разреза" });
      setActiveSeries(null);
      return;
    }
    const cur = currentSection();
    if (cur && !(s.tables[cur.row.id]?.version === cur.row.version)) void ensureTable(cur.row.id).catch(() => undefined);
    const key = cur ? `${cur.row.id}:${cur.row.version}:${cur.index}` : sel ? "pending" : "";
    if (key !== shown) {
      shown = key;
      applySectionKey(currentScene());
    }
  };
  unbind.push(seriesStore.subscribe(follow), viewStore.subscribe(follow));
}

// ---------------------------------------------------------------- the range over the data

/** The visible data as boxes [xmin, ymin, zmin, xmax, ymax, zmax]: visible sets, line layers and wireframes. */
export function visibleBoxes(hidden: readonly string[] = []): number[][] {
  const out: number[][] = [];
  const off = new Set(hidden);
  const ok = (b: ArrayLike<number | null> | null | undefined): b is number[] =>
    !!b && b.length === 6 && Array.from(b).every((v) => typeof v === "number" && Number.isFinite(v));
  const scene = currentScene();
  if (scene) {
    for (const id of scene.debug().sets) {
      const layer = scene.holes(id);
      if (!layer?.visible) continue;
      for (const p of layer.manifest.parts) if (ok(p.bbox)) out.push(p.bbox);
    }
  }
  const o = sceneObjects.get();
  for (const f of o.files) for (const l of f.layers) if (!off.has(`ln:${l.id}`) && ok(l.extent)) out.push(l.extent);
  for (const w of o.wireframes) if (!off.has(`wf:${w.id}`) && ok(w.extent)) out.push(w.extent);
  return out;
}

/** The eight corners of each box. */
export function boxCorners(boxes: readonly number[][]): Vec3[] {
  const out: Vec3[] = [];
  for (const b of boxes) for (const x of [b[0], b[3]]) for (const y of [b[1], b[4]]) for (const z of [b[2], b[5]]) out.push([x, y, z]);
  return out;
}

const clean = (v: number) => Number(v.toPrecision(15)) + 0;

/**
 * s0 and s1 over the points along v̂ from P1 (8.1.2): multiples of Δ, the data inside [s0; s1]; when K would
 * pass 5 000 the step grows (to the centimetre) so that K ≤ 5 000. No points — one section at P1.
 */
export function seriesRange(points: readonly Vec3[], p1: Vec3, v: Vec3, step: number): { s0: number; s1: number; step: number; count: number } {
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of points) {
    const t = axisDot([p[0] - p1[0], p[1] - p1[1], p[2] - p1[2]], v);
    if (t < lo) lo = t;
    if (t > hi) hi = t;
  }
  if (!Number.isFinite(lo)) return { s0: 0, s1: 0, step, count: 1 };
  let d = step;
  for (let i = 0; i < 8; i++) {
    const s0 = clean(Math.floor(lo / d + 1e-9) * d);
    const s1 = clean(Math.ceil(hi / d - 1e-9) * d);
    const count = seriesCount(s0, s1, d);
    if (count <= MAX_K) return { s0, s1, step: d, count };
    d = Math.ceil(((hi - lo) / (MAX_K - 2)) * 100 * (1 + i * 0.01)) / 100;
  }
  return { s0: 0, s1: 0, step: d, count: 1 };
}
