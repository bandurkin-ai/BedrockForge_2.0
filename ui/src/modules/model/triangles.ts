/**
 * «Править треугольники» (ТЗ 7.23; ADR 0015 §6–7 «правка треугольников»; BF `shell/modeling.ts`): a manual fix of
 * a wireframe in the 3D window. Every click goes as a ray to the exact pick of the core (`POST /wireframes/pick`
 * on this wireframe only, the section window with it; a newer click overtakes an older one): in «Отметить» the
 * triangle under the cursor is marked to be removed (a second click takes the mark off); in «Добавить» the
 * vertex of the hit triangle nearest to the click on screen is taken — its exact float64 coordinates, so the core
 * reuses that vertex bit for bit — and every three vertices make a new triangle. «Применить» starts the operation
 * `edit_triangles {wireframe_id, remove, add}`: the result is a new wireframe, the source does not change. No
 * React; the marks in the scene — `viz/edit/trianglesMark.ts` (a lazy chunk with three.js).
 */
import type { App } from "../../shell/app";
import { api, CoreError } from "../../shell/api";
import { refusal, showJobs } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import type { ToolPointer } from "../../viz/core/toolInput";
import type { Vec3 } from "../../viz/core/vec3";
import type { MarkData } from "../../viz/edit/trianglesMark";
import { ExactPicker, pickRequest, type ExactPick, type PickRequest } from "../../viz/mesh/exact";
import type { Scene } from "../../viz/Scene";
import { editStore } from "../lines/editState";
import { projectStore } from "../projects/state";
import { viewStore } from "../scene/bind";
import { sceneObjects } from "../scene/objects";
import { currentScene, onScene } from "../scene/sceneHost";
import { sectionOf } from "../section/state";
import { afterStart, queueText, refusalOf, startOperation, type OperationBody, type Started } from "./run";

export type TriMode = "remove" | "add";
export type Corners = [Vec3, Vec3, Vec3];

/** One fix in progress: the marks, the new triangles and the vertices clicked for the next one. */
export interface TriEdit {
  wireframeId: number;
  name: string;
  mode: TriMode;
  /** the number of the triangle as stored → its exact corners (for the marks) */
  remove: ReadonlyMap<number, Corners>;
  add: readonly Corners[];
  pending: readonly Vec3[];
}

export const newEdit = (wireframeId: number, name: string): TriEdit => ({ wireframeId, name, mode: "remove", remove: new Map(), add: [], pending: [] });

const same = (a: Vec3, b: Vec3) => Object.is(a[0], b[0]) && Object.is(a[1], b[1]) && Object.is(a[2], b[2]);

/** The result of a click: the next state and what to tell. */
export interface Step {
  edit: TriEdit;
  message: string;
}

/** «Отметить»: the triangle of the exact pick is marked (or unmarked); another wireframe is not this fix's. */
export function markPick(t: TriEdit, pick: ExactPick | null): Step {
  if (!pick || pick.wireframeId !== t.wireframeId) return { edit: t, message: `Щёлкните по треугольнику каркаса «${t.name}»` };
  const remove = new Map(t.remove);
  const had = remove.delete(pick.triangle);
  if (!had) remove.set(pick.triangle, pick.vertices);
  const n = remove.size;
  return { edit: { ...t, remove }, message: had ? `Отметка снята: треугольник №${pick.triangle + 1}; к удалению — ${n}` : `К удалению: треугольник №${pick.triangle + 1}; всего ${n}` };
}

/**
 * The vertex of the hit triangle the click snaps to: the nearest on screen (`screen` — project → px), else the
 * nearest in 3D to the point of the hit. The corner is returned as the core gave it (exact).
 */
export function snapCorner(pick: ExactPick, at: { x: number; y: number } | null = null, screen: ((p: Vec3) => [number, number] | null) | null = null): Vec3 {
  let best = 0;
  let dist = Infinity;
  pick.vertices.forEach((c, i) => {
    const s = at && screen ? screen(c) : null;
    const d = s && at ? Math.hypot(s[0] - at.x, s[1] - at.y) : Math.hypot(c[0] - pick.point[0], c[1] - pick.point[1], c[2] - pick.point[2]);
    if (d < dist) {
      dist = d;
      best = i;
    }
  });
  return pick.vertices[best];
}

/** «Добавить»: the snapped vertex joins the pending ones; the third makes a triangle. */
export function addCorner(t: TriEdit, corner: Vec3): Step {
  if (t.pending.some((p) => same(p, corner))) return { edit: t, message: "Эта вершина уже выбрана: укажите другую" };
  const pending = [...t.pending, corner];
  if (pending.length < 3) return { edit: { ...t, pending }, message: `Вершина ${pending.length} из 3 выбрана` };
  const add = [...t.add, pending as Corners];
  return { edit: { ...t, add, pending: [] }, message: `Добавлен треугольник; новых — ${add.length}` };
}

/** «Убрать последнее»: a pending vertex, else the last new triangle, else the last mark. */
export function dropLast(t: TriEdit): TriEdit {
  if (t.pending.length) return { ...t, pending: t.pending.slice(0, -1) };
  if (t.mode === "add" && t.add.length) return { ...t, add: t.add.slice(0, -1) };
  if (t.remove.size) {
    const remove = new Map(t.remove);
    remove.delete([...remove.keys()].at(-1) as number);
    return { ...t, remove };
  }
  if (t.add.length) return { ...t, add: t.add.slice(0, -1) };
  return t;
}

export const isEmpty = (t: TriEdit): boolean => !t.remove.size && !t.add.length;

/** The parameters of the operation `edit_triangles`. */
export function editParams(t: TriEdit): { wireframe_id: number; remove: number[]; add: Corners[] } {
  return { wireframe_id: t.wireframeId, remove: [...t.remove.keys()].sort((a, b) => a - b), add: t.add.map((c) => [[...c[0]], [...c[1]], [...c[2]]] as Corners) };
}

/** What the scene marks: corners in project coordinates (float64, nine numbers a triangle). */
export function markData(t: TriEdit | null): MarkData {
  const flat = (list: readonly Corners[]) => Float64Array.from(list.flatMap((c) => [...c[0], ...c[1], ...c[2]]));
  if (!t) return { remove: new Float64Array(0), add: new Float64Array(0), pending: new Float64Array(0) };
  return { remove: flat([...t.remove.values()]), add: flat(t.add), pending: Float64Array.from(t.pending.flatMap((p) => [...p])) };
}

// ---- in the application

export interface TrianglesState {
  edit: TriEdit | null;
  message: string | null;
  /** a pick is waited for */
  busy: boolean;
  creating: boolean;
}

const IDLE: TrianglesState = { edit: null, message: null, busy: false, creating: false };
export const trianglesStore = new Store<TrianglesState>({ ...IDLE });

const textOf = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

export const MARK_KEY = "triangles";

let markSeq = 0;
/** The marks in the 3D window; null — away. */
export async function showMarks(scene: Scene | null, data: MarkData | null): Promise<void> {
  const mine = ++markSeq;
  if (!scene) return;
  if (!data || !(data.remove.length || data.add.length || data.pending.length)) {
    scene.removeLayer(MARK_KEY);
    scene.invalidate();
    return;
  }
  const mod = await import("../../viz/edit/trianglesMark");
  if (mine !== markSeq || currentScene() !== scene) return;
  const css = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  scene.addLayer(MARK_KEY, new mod.TrianglesMark(data, scene.clipPlanes, { remove: v("--danger", "#e5484d"), add: v("--success", "#2fbf71"), pending: v("--highlight", "#f46a1a") }));
  scene.invalidate();
}

export interface TrianglesDeps {
  pick?: (body: PickRequest) => Promise<unknown>;
  scene?: () => Scene | null;
  show?: (scene: Scene | null, data: MarkData | null) => unknown;
}

/** One fix in the 3D window: its tool takes the clicks (a drag still turns the view). */
export class TrianglesController {
  private readonly picker: ExactPicker;
  private readonly offs: (() => void)[] = [];
  private scene: Scene | null = null;
  private stopped = false;
  private readonly show: (scene: Scene | null, data: MarkData | null) => unknown;

  constructor(edit: TriEdit, deps: TrianglesDeps = {}) {
    this.picker = new ExactPicker(deps.pick ?? ((body) => api.post("/wireframes/pick", body)));
    this.show = deps.show ?? ((sc, d) => void showMarks(sc, d));
    trianglesStore.set({ ...IDLE, edit, message: `Правка треугольников «${edit.name}»: щёлкайте по треугольникам, чтобы отметить их к удалению` });
    const attach = (sc: Scene | null) => {
      this.scene?.setTool(null);
      this.scene = sc;
      sc?.setTool({ pointer: (e) => this.pointer(e) });
      this.redraw();
    };
    if (deps.scene) attach(deps.scene());
    else this.offs.push(onScene(attach));
    // the string editor takes the 3D window: this fix ends
    this.offs.push(editStore.subscribe(() => void (editStore.get().session && this.stop("Правка треугольников закрыта: открыт редактор стрингов"))));
    // another project (or none): the wireframe is not in it — the fix goes, nothing applied
    const uid = projectStore.get().current?.uid ?? null;
    this.offs.push(projectStore.subscribe(() => void ((projectStore.get().current?.uid ?? null) !== uid && this.stop("Правка треугольников закрыта: открыт другой проект, изменения не применены"))));
  }

  get edit(): TriEdit | null {
    return trianglesStore.get().edit;
  }

  private set(edit: TriEdit, message: string | null = trianglesStore.get().message): void {
    trianglesStore.set({ edit, message });
    this.redraw();
  }

  private redraw(): void {
    this.show(this.scene, markData(this.edit));
  }

  setMode(mode: TriMode): void {
    const t = this.edit;
    if (!t || t.mode === mode) return;
    this.set({ ...t, mode, pending: [] }, mode === "add" ? "Добавление: щёлкните три вершины каркаса — щелчок берёт ближайшую вершину треугольника под курсором" : "Отметка: щёлкайте по треугольникам, чтобы отметить их к удалению");
  }

  dropLast(): void {
    const t = this.edit;
    if (t) this.set(dropLast(t), null);
  }

  /** A pointer event of the 3D window: a left click is this tool's; a right click drops the last step. */
  pointer(e: ToolPointer): boolean {
    if (e.type !== "click") return false;
    if (e.button === 2) {
      this.dropLast();
      return true;
    }
    if (e.button !== 0) return false;
    void this.click(e.x, e.y);
    return true;
  }

  /** A click at (x, y) px of the canvas: the exact pick, then a mark or a vertex. */
  async click(x: number, y: number): Promise<void> {
    const t = this.edit;
    const sc = this.scene;
    const ray = sc?.cursorRay(x, y);
    if (!t || !sc || !ray) return;
    const { line, clip } = sectionOf(viewStore.get());
    trianglesStore.set({ busy: true });
    let pick: ExactPick | null | undefined;
    try {
      pick = await this.picker.pick(pickRequest(ray, [t.wireframeId], line, clip));
    } catch (e) {
      if (!this.stopped) trianglesStore.set({ busy: false, message: textOf(e) });
      return;
    }
    if (pick === undefined || this.stopped) return; // overtaken by a newer click
    trianglesStore.set({ busy: false });
    const now = this.edit;
    if (!now || now.wireframeId !== t.wireframeId) return;
    if (now.mode === "remove") {
      const step = markPick(now, pick);
      return this.set(step.edit, step.message);
    }
    if (!pick || pick.wireframeId !== now.wireframeId) return this.set(now, "Щёлкните по треугольнику рядом с нужной вершиной: щелчок берёт ближайшую вершину треугольника под курсором");
    const view = sc.projectView();
    const screen = view ? (p: Vec3) => projectToScreen(view, p) : null;
    const step = addCorner(now, snapCorner(pick, { x, y }, screen));
    this.set(step.edit, step.message);
  }

  stop(message: string | null = null): void {
    if (this.stopped) return;
    this.stopped = true;
    this.picker.cancel();
    this.offs.forEach((fn) => fn());
    this.scene?.setTool(null);
    this.show(this.scene, null);
    trianglesStore.set({ ...IDLE, message });
    if (controller === this) controller = null;
  }
}

function projectToScreen(view: { matrix: ArrayLike<number>; width: number; height: number }, p: Vec3): [number, number] | null {
  const m = view.matrix;
  const cw = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
  if (!(cw > 0)) return null;
  const x = ((m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / cw + 1) * 0.5 * view.width;
  const y = (1 - (m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / cw) * 0.5 * view.height;
  return [x, y];
}

let controller: TrianglesController | null = null;
export const trianglesController = (): TrianglesController | null => controller;

/** `model.triangles {wireframe_id}`: the fix of the named wireframe, else of the one chosen. */
export function startTriangles(app: App, wireframeId: number | null = null, deps: TrianglesDeps = {}): string {
  const sel = app.store.get().selected;
  const id = wireframeId ?? (sel?.kind === "wireframe" ? sel.id : null);
  if (id === null) throw refusal("no_wireframe", "Не выбран каркас для правки треугольников.", "Выберите каркас в дереве или в 3D-окне и повторите.");
  const w = sceneObjects.get().wireframes.find((x) => x.id === id);
  if (!w) throw refusal("wireframe_not_found", `Каркас №${id} не найден.`, "Обновите дерево проекта и выберите каркас.");
  const s = editStore.get().session;
  if (s) throw refusal("editing", `Открыт редактор стрингов (слой «${s.layerName}»): 3D-окно занято правкой линий.`, "Завершите правку слоя («Завершить») и повторите.");
  controller?.stop();
  controller = new TrianglesController(newEdit(w.id, w.name), deps);
  return `Правка треугольников «${w.name}»: щелчок по треугольнику отмечает его к удалению; «Добавить» — новый треугольник по трём вершинам; «Применить» — новый каркас`;
}

export function cancelTriangles(): void {
  controller?.stop();
}

export interface ApplyDeps {
  start?: (body: OperationBody) => Promise<Started>;
  after?: (s: Started) => Promise<void>;
}

/** «Применить»: the operation `edit_triangles`; the fix ends when the job is queued. */
export async function applyTriangles(app: App, deps: ApplyDeps = {}): Promise<string> {
  const t = controller?.edit;
  if (!controller || !t) throw refusal("not_editing", "Правка треугольников не начата.", "Выберите каркас и нажмите «Треугольники».");
  if (isEmpty(t)) throw refusal("empty_edit", "Правка пуста: треугольники не отмечены и не добавлены.", "Отметьте треугольники к удалению или добавьте новые.");
  if (trianglesStore.get().creating) return "Правка уже применяется";
  trianglesStore.set({ creating: true });
  try {
    let started: Started;
    try {
      started = await (deps.start ?? startOperation)({ op: "edit_triangles", params: editParams(t) as unknown as Record<string, unknown> });
    } catch (e) {
      const text = refusalOf(e).text;
      trianglesStore.set({ message: text });
      throw new Error(text);
    }
    controller?.stop();
    showJobs(app);
    await (deps.after ?? afterStart)(started);
    return `«${t.name}»: удаляется треугольников ${t.remove.size}, добавляется ${t.add.length} — новый каркас строится (задание №${started.id}, ${queueText(started.queue)})`;
  } finally {
    trianglesStore.set({ creating: false });
  }
}
