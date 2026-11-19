/**
 * «Собрать из контуров» in the application (ТЗ 7.23; ADR 0015 §6; as the linking of strings in Micromine): the
 * contours of the layer on edit are clicked one after another (`EditSession.assemble`; they may be unsaved) and
 * the wireframe grows in the 3D window — a pause of 250 ms after the last change, then
 * `POST /geometry/stitch/preview` with the coordinates of the working copy; an older answer is dropped
 * (`edit/assemble.ts::PreviewScheduler`). The panel shows the triangles and the volume (a volume only for a
 * closed body: «Замкнуть торцы» — flat caps). «Создать каркас» saves the layer (the new lines take their stable
 * numbers) and starts the operation `stitch` over the contours and the tie lines, in the order clicked. No React.
 */
import { assemblyInputs, PreviewScheduler, stitchParams, type AssemblyInputs, type PreviewBody, type Timers } from "../../edit/assemble";
import type { EditSession } from "../../edit/session";
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import { refusal, showJobs } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import type { PreviewMeshData } from "../../viz/edit/previewMesh";
import { bfa1Array, decodeBfa1 } from "../../viz/format/bfa1";
import { stitchPreview } from "../lines/editApi";
import { currentSession, openEditor, saveEditor } from "../lines/editor";
import { editStore } from "../lines/editState";
import { currentScene } from "../scene/sceneHost";
import { afterStart, queueText, refusalOf, startOperation, type OperationBody, type Started } from "./run";

export const PREVIEW_KEY = "assemble";

/** What the panel shows of a preview. */
export interface PreviewInfo {
  triangles: number;
  vertices: number;
  closed: boolean;
  /** m³ of a closed body; null — the ends are open */
  volume: number | null;
  warnings: string[];
}

export interface Preview extends PreviewInfo {
  mesh: PreviewMeshData;
}

export interface AssembleState {
  /** an assembly is under way (the panel is shown) */
  active: boolean;
  /** the preview is waited for */
  busy: boolean;
  preview: PreviewInfo | null;
  /** why there is no preview (the refusal of the core) */
  problem: string | null;
  /** «Создать каркас» is running */
  creating: boolean;
  contours: number;
  ties: number;
}

const IDLE: AssembleState = { active: false, busy: false, preview: null, problem: null, creating: false, contours: 0, ties: 0 };
export const assembleStore = new Store<AssembleState>({ ...IDLE });

const bad = (what: string) => new CoreError(0, "preview_answer", `Ответ предпросмотра повреждён: ${what}.`, "Измените выбор контуров — предпросмотр будет запрошен заново.");
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** BFA1 `mesh` of the preview: float32 offsets from the whole-metre origin, the corners, the header. */
export function readPreview(buf: ArrayBuffer): Preview {
  const doc = decodeBfa1<Record<string, unknown>>(buf);
  const h = doc.header;
  if (h.kind !== "mesh") throw bad("не сетка");
  const o = h.origin;
  if (!Array.isArray(o) || o.length !== 3 || !o.every((v) => finite(v) && Number.isInteger(v))) throw bad("нет целого начала координат");
  const triangles = Number(h.triangles);
  const vertices = Number(h.vertices);
  const xyz = bfa1Array(doc, "xyz", "<f4");
  const idx = bfa1Array(doc, "idx", "<u4");
  if (xyz.length !== vertices * 3 || idx.length !== triangles * 3) throw bad("числа вершин и треугольников не совпадают с массивами");
  for (let i = 0; i < idx.length; i++) if (idx[i] >= vertices) throw bad("номер вершины вне списка");
  return {
    triangles,
    vertices,
    closed: h.closed === true,
    volume: finite(h.volume) ? h.volume : null,
    warnings: Array.isArray(h.warnings) ? h.warnings.map(String) : [],
    mesh: { origin: { x: o[0] as number, y: o[1] as number, z: o[2] as number }, xyz, idx },
  };
}

const textOf = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** The colours of the preview from the theme (brand accent). */
function colors(): { fill: string; edge: string } {
  const css = typeof document === "undefined" ? null : getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  const accent = v("--accent", "#1f6bff");
  return { fill: accent, edge: accent };
}

let shown = 0;
/** The preview in the 3D window (a lazy chunk with three.js); null — away. */
export async function showPreview(mesh: PreviewMeshData | null): Promise<void> {
  const mine = ++shown;
  const scene = currentScene();
  if (!scene) return;
  if (!mesh) {
    scene.removeLayer(PREVIEW_KEY);
    scene.invalidate();
    return;
  }
  const mod = await import("../../viz/edit/previewMesh");
  if (mine !== shown || currentScene() !== scene) return;
  scene.addLayer(PREVIEW_KEY, new mod.PreviewMesh(mesh, scene.clipPlanes, colors()));
  scene.invalidate();
}

export interface AssembleDeps {
  fetch?: (body: PreviewBody, signal: AbortSignal) => Promise<ArrayBuffer>;
  show?: (mesh: PreviewMeshData | null) => unknown;
  delay?: number;
  timers?: Timers;
}

/** Follows one session while it assembles: its picks → the preview → the store and the 3D window. */
export class AssembleController {
  private readonly scheduler: PreviewScheduler<Preview>;
  private readonly off: () => void;
  private seen: { layer: unknown; order: string; closed: boolean } | null = null;
  private stopped = false;
  private readonly show: (mesh: PreviewMeshData | null) => unknown;

  constructor(
    readonly session: EditSession,
    deps: AssembleDeps = {},
  ) {
    this.show = deps.show ?? ((m) => void showPreview(m));
    const fetch = deps.fetch ?? ((body, signal) => stitchPreview(body, signal));
    this.scheduler = new PreviewScheduler<Preview>(
      async (body, signal) => readPreview(await fetch(body, signal)),
      {
        result: (p) => {
          const { mesh, ...info } = p;
          assembleStore.set({ preview: info, problem: null });
          this.show(mesh);
        },
        error: (e) => {
          assembleStore.set({ preview: null, problem: textOf(e) });
          this.show(null);
        },
        busy: (on) => assembleStore.set({ busy: on }),
      },
      deps.delay,
      deps.timers,
    );
    assembleStore.set({ ...IDLE, active: true });
    this.off = session.subscribe(() => this.update());
    this.update();
  }

  /** The inputs as they are now. */
  inputs(): AssemblyInputs | null {
    const a = this.session.assemble;
    return a ? assemblyInputs(this.session.layer, a.order, a.closed) : null;
  }

  /** After every change of the session; a hover changes neither the layer nor the picks — nothing is counted. */
  update(): void {
    if (this.stopped) return;
    const a = this.session.assemble;
    if (!a) return this.stop();
    const key = { layer: this.session.layer, order: a.order.join(","), closed: a.closed };
    const seen = this.seen;
    if (seen && seen.layer === key.layer && seen.order === key.order && seen.closed === key.closed) return;
    this.seen = key;
    const inputs = assemblyInputs(this.session.layer, a.order, a.closed);
    if (inputs.contours.length < 2) {
      assembleStore.set({ preview: null, problem: null });
      this.show(null);
    }
    assembleStore.set({ contours: inputs.contours.length, ties: inputs.ties.length });
    this.scheduler.request(inputs);
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.off();
    this.scheduler.dispose();
    this.show(null);
    assembleStore.set({ ...IDLE });
    if (controller === this) controller = null;
  }
}

let controller: AssembleController | null = null;
let offEditor: (() => void) | null = null;

/** The controller of the open assembly (for the tests and the panel). */
export const assembleController = (): AssembleController | null => controller;

/** The assembly starts in a session (another one in progress stops). */
export function beginAssembly(s: EditSession, deps: AssembleDeps = {}): AssembleController {
  controller?.stop();
  s.startAssemble();
  controller = new AssembleController(s, deps);
  // the editor finished or another layer opened: the assembly goes with its session
  offEditor ??= editStore.subscribe(() => {
    if (controller && editStore.get().session !== controller.session) controller.stop();
    if (!controller) {
      offEditor?.();
      offEditor = null;
    }
  });
  return controller;
}

/** «Отмена» of the panel, Esc. */
export function cancelAssembly(): void {
  const c = controller;
  if (!c) return;
  if (c.session.assemble) c.session.stopAssemble();
  c.stop();
}

/** `model.assemble {layer_id}`: in the named layer (opened on edit), else in the layer on edit, else in the layer
 * of lines chosen in the tree (opened on edit). */
export async function startAssembly(app: App, layerId: number | null = null, deps: AssembleDeps = {}): Promise<string> {
  let s = currentSession();
  if (!s || (layerId !== null && s.layerId !== layerId)) {
    const sel = app.store.get().selected;
    const id = layerId ?? (sel?.kind === "lines" ? sel.id : null);
    if (id === null) throw refusal("not_editing", "Не выбран слой с контурами.", "Выберите в дереве слой линий с замкнутыми контурами или откройте его на правку, затем повторите.");
    await openEditor(app, id);
    s = currentSession();
    if (!s || s.layerId !== id) throw refusal("not_editing", "Слой не открыт на правку.", "Откройте слой на правку и повторите.");
  }
  beginAssembly(s, deps);
  return `Сборка каркаса из контуров слоя «${s.layerName}»: щёлкайте замкнутые контуры по порядку; «Замкнуть торцы» — плоские крышки; «Создать каркас» — операция «Сшить контуры»`;
}

export interface CreateDeps {
  save?: (app: App) => Promise<string>;
  start?: (body: OperationBody) => Promise<Started>;
  after?: (s: Started) => Promise<void>;
}

/** «Создать каркас»: the layer is saved first, then the operation `stitch` is queued; the assembly ends. */
export async function createAssembled(app: App, deps: CreateDeps = {}): Promise<string> {
  const c = controller;
  const s = c?.session;
  if (!c || !s?.assemble) throw refusal("not_assembling", "Сборка каркаса не начата.", "Нажмите «Собрать из контуров» и щёлкните контуры по порядку.");
  if (assembleStore.get().creating) return "Каркас уже создаётся";
  const first = c.inputs();
  if (!first || first.contours.length < 2) throw refusal("too_few_contours", "Для каркаса нужно не меньше двух замкнутых контуров.", "Щёлкните контуры по порядку.");
  assembleStore.set({ creating: true });
  try {
    if (s.drawing) s.finishDrawing();
    if (s.dirty) {
      await (deps.save ?? saveEditor)(app);
      if (currentSession() !== s || s.dirty) throw refusal("not_saved", "Каркас не создан: слой не сохранён.", "Сохраните слой (Ctrl+S) и нажмите «Создать каркас» ещё раз.");
    }
    const inputs = c.inputs();
    const params = inputs && inputs.contours.length >= 2 ? stitchParams(s.layerId, inputs) : null;
    if (!params) throw refusal("not_saved", "Каркас не создан: у контуров нет сохранённых номеров.", "Сохраните слой (Ctrl+S) и повторите.");
    let started: Started;
    try {
      started = await (deps.start ?? startOperation)({ op: "stitch", params: params as unknown as Record<string, unknown> });
    } catch (e) {
      throw new Error(refusalOf(e).text);
    }
    cancelAssembly();
    showJobs(app);
    await (deps.after ?? afterStart)(started);
    const n = params.contours[0].lines.length - (inputs?.ties.length ?? 0);
    return `Каркас из ${n} контуров слоя «${s.layerName}» строится: задание №${started.id}, ${queueText(started.queue)}; отчёт откроется, когда задание будет готово`;
  } finally {
    assembleStore.set({ creating: false });
  }
}
