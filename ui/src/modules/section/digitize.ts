/**
 * «Контур на разрезе» (ТЗ 8.1; ADR 0016 §7): digitizing on a section of the active series in the string editor.
 *
 *     editSection()        what the editing session takes from the view keys: the free line of 7.14 (as in Э9) or
 *                          the plane t = 0 of section k, clipping, the test «the line is on section k» (all its
 *                          vertices in the window — `SeriesGeometry.lineSection`, the rule of the core) and the
 *                          attributes of new lines `SECTION_SET` (the number of the series in the project),
 *                          `SECTION` (k + 1), `SECTION_NAME` — they reach the save as `new_attrs` (ADR 0015)
 *     applyEditSection()   sets them on the session; the editor calls it on every change of the section, so the
 *                          drawing plane follows `]` / `[` (one session: nothing unsaved is lost); a line being
 *                          drawn is finished first (as Enter: a polygon closes) — it stays on its own section
 *                          with its attributes, the next click starts a new line on the new one
 *     series.digitize      clipping on, the layer of contours of the series (the one of the series; else the layer
 *                          on edit; else a new «Контуры <серия>» in the file «Серии разрезов», kept as the layer
 *                          of the series), opened on edit with the tool «Полигон»
 */
import type { LineTest } from "../../edit/lock";
import type { AttrValue } from "../../edit/model";
import type { EditSession } from "../../edit/session";
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { needWritable, noted, refusal } from "../../shell/objectRun";
import type { SectionLine } from "../../viz/section/math";
import type { SectionPlane } from "../../viz/section/plane";
import { renameLayer } from "../lines/actions";
import { newLayer } from "../lines/editApi";
import { editStore } from "../lines/editState";
import { viewStore } from "../scene/bind";
import { lineLayers, loadSceneObjects, sceneObjects } from "../scene/objects";
import { seriesApi } from "./seriesApi";
import { currentSection, ensureTable, putSeries, sectionPlaneOf, seriesStore, setActiveSeries, type CurrentSection, type SeriesState } from "./seriesState";
import { sectionOf } from "./state";

/** The file of the layers of contours made by «Контур на разрезе». */
export const CONTOUR_FILE = "Серии разрезов";
export const NO_SERIES = "Нет активной серии разрезов: сделайте серию активной («Разрезы › Менеджер серий»)";

/** What the editing session takes from the section shown. */
export interface EditSection {
  /** the free line of 7.14 when it is the section (points in the middle of its window, as in Э9) */
  line: SectionLine | null;
  /** the section of the active series: points on its plane t = 0 */
  plane: SectionPlane | null;
  clip: boolean;
  /** on a section of a series with clipping: is a line on it («текущий разрез») */
  test: LineTest | null;
  /** on a section of a series with clipping: the attributes of new lines */
  attrs: Record<string, AttrValue> | undefined;
  /** changes when anything above does */
  key: string;
  /** where points go: the plane (or the free line) and clipping — not the name of the section */
  place: string;
  /** the active series is not read yet: the session keeps what it has */
  pending: boolean;
}

/** `SECTION_SET`, `SECTION`, `SECTION_NAME` of a line drawn on the section (ADR 0016 решение 2: reference values). */
export function sectionAttrs(cur: CurrentSection): Record<string, AttrValue> {
  const attrs: Record<string, AttrValue> = { SECTION_SET: cur.row.number, SECTION: cur.k + 1 };
  if (cur.name) attrs.SECTION_NAME = cur.name;
  return attrs;
}

const tests = new Map<string, LineTest>();

/** «The line is on section k»: all its vertices in the window of k (kept per series version and k: the lock keeps
 * its verdicts while the test is the same). */
export function sectionTest(cur: CurrentSection): LineTest {
  const key = `${cur.row.id}:${cur.row.version}:${cur.k}:${cur.seg}`;
  let t = tests.get(key);
  if (!t) {
    if (tests.size > 32) tests.clear();
    const { geom, k } = cur;
    t = (points) => geom.lineSection(points) === k;
    tests.set(key, t);
  }
  return t;
}

/** The section of the editing session for the view keys and the series read. */
export function editSection(keys: Record<string, unknown> = viewStore.get(), state: SeriesState = seriesStore.get()): EditSection {
  const shown = sectionPlaneOf(keys, state);
  const cur = shown.series && shown.plane ? currentSection(keys, state) : null;
  if (cur && shown.plane) {
    const on = shown.clip;
    const key = `series:${cur.row.id}:${cur.row.version}:${cur.k}:${cur.seg}:${on}:${cur.name ?? ""}`;
    const p = shown.plane;
    const place = `series:${cur.row.id}:${cur.k}:${cur.seg}:${on}:${p.origin.join(",")}:${p.v.join(",")}:${p.towards}:${p.away}`;
    return { line: null, plane: p, clip: on, test: on ? sectionTest(cur) : null, attrs: on ? sectionAttrs(cur) : undefined, key, place, pending: false };
  }
  const key = `line:${JSON.stringify(shown.line)}:${shown.clip}:${shown.pending}`;
  const place = `line:${JSON.stringify(shown.line)}:${shown.clip}`;
  return { line: shown.line, plane: null, clip: shown.clip, test: null, attrs: undefined, key, place, pending: shown.pending };
}

/** The place each session draws on now. */
const places = new WeakMap<EditSession, string>();

/** The session draws on the section: its plane, clipping, the lock of «текущий разрез» and the new attributes. */
export function applyEditSection(s: EditSession, sec: EditSection = editSection()): void {
  if (sec.pending) return;
  const was = places.get(s);
  places.set(s, sec.place);
  if (s.drawing && was !== undefined && was !== sec.place) {
    // the section changed under a line being drawn (`]`, the tree, Claude): it ends where it was drawn
    const before = s.message;
    s.finishDrawing();
    if (s.message === before) s.say("Линия завершена на прежнем разрезе: следующий щелчок начинает новую на этом");
  }
  s.section = sec.line;
  s.plane = sec.plane;
  s.clip = sec.clip;
  s.lock.setTest(sec.test);
  s.newAttrs = sec.attrs;
  s.viewChanged();
  s.touch();
}

// ---------------------------------------------------------------- series.digitize

const exists = (id: unknown): id is number => typeof id === "number" && lineLayers().some((l) => l.id === id && !l.cloud);

/** A new layer «Контуры <серия>» in the file «Серии разрезов» (made with its first layer when there is none). */
async function newContourLayer(cur: CurrentSection): Promise<number> {
  const name = `Контуры ${cur.row.name}`;
  const file = sceneObjects.get().files.find((f) => f.name === CONTOUR_FILE && !f.folder);
  let id: number;
  if (file) id = (await newLayer(name, file.id)).id;
  else {
    const made = await newLayer(CONTOUR_FILE);
    await renameLayer(made, name);
    id = made.id;
  }
  await loadSceneObjects();
  return id;
}

/** The layer of contours: `layer_id`, the layer of the series, the layer on edit, or a new one (kept by the series). */
export async function contourLayer(cur: CurrentSection, given?: unknown): Promise<number> {
  if (!sceneObjects.get().loaded) await loadSceneObjects();
  if (given !== undefined && given !== null) {
    if (!exists(given)) throw refusal("line_layer_not_found", `Слой линий №${String(given)} не найден.`, "Укажите слой линий проекта (не облако точек) или вызовите команду без слоя.");
    return given;
  }
  const own = cur.row.params.contour_layer;
  if (exists(own)) return own;
  const open = editStore.get().session?.layerId;
  const id = exists(open) ? open : await newContourLayer(cur);
  try {
    putSeries(await seriesApi.patch(cur.row.id, cur.row.version, { params: { contour_layer: id } }));
  } catch {
    // the layer still works for this digitizing; the series keeps its old layer (another change of it came first)
  }
  return id;
}

/** `series.digitize`: digitizing a contour on the current section of the active series. */
export async function startDigitize(app: App, a: Record<string, unknown> = {}): Promise<string> {
  const cur = currentSection();
  if (!cur) throw refusal("no_series", "Нет активной серии разрезов.", "Сделайте серию активной («Разрезы › Менеджер серий» или «Новая серия») и повторите.");
  if (!sectionOf(viewStore.get()).clip) setActiveSeries({ id: cur.row.id, k: cur.k, seg: cur.seg }, true);
  void ensureTable(cur.row.id).catch(() => undefined);
  const layerId = await contourLayer(cur, a.layer_id);
  if (editStore.get().session?.layerId !== layerId) await app.run("lines.edit", { layer_id: layerId });
  const s = editStore.get().session;
  if (!s || s.layerId !== layerId) return "Слой контуров не открыт на правку: правка другого слоя продолжается";
  applyEditSection(s);
  s.setTool("polygon");
  const where = cur.seg !== null ? `сегменте ${cur.index + 1}` : `разрезе ${cur.name ? `«${cur.name}»` : cur.k + 1}`;
  return `Контур на ${where} серии «${cur.row.name}»: слой «${s.layerName}». Щёлкайте вершины контура, Enter — замкнуть; ] / [ — соседний разрез, Ctrl+S — сохранить`;
}

export function bindDigitize(app: App): void {
  const writable = needWritable(app);
  app.registry.bind(
    "series.digitize",
    noted(app, (a) => startDigitize(app, a)),
    (ctx: CommandContext) => writable(ctx) ?? (currentSection() ? null : NO_SERIES),
  );
}
