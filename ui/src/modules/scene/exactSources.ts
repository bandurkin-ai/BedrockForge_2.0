/**
 * What the core computes exactly for the 3D window (ADR 0013 §5–§6, ADR 0007 §5; ТЗ 7.14, 7.15, 7.21):
 *
 *  - the lines a wireframe is shown by when the view is clipped by a section — for every shown wireframe the
 *    plane of the section line passes through, `GET /viz/wireframes/{id}/section`; a new line or window cancels
 *    the requests of the old one and its lines leave at once (they would stand in the wrong place);
 *  - the marks where the holes of a set cross wireframes — when the style of its traces asks for them
 *    («Пересечения», «с каркасами»; with the shown wireframes only or with all of them; the DTMs only with
 *    «с ЦТМ»: a relief built through the collars would mark every hole at 0 m — `crossedWireframes`),
 *    `GET /viz/sets/{id}/intersections` with `If-None-Match`: after an edit of the set the marks are asked again
 *    and a 304 changes nothing;
 *  - the exact triangle under a click on a surface, `POST /wireframes/pick` with the planes of the section
 *    window: only the answer to the latest click is taken.
 *
 * Lines of sections are one layer of the scene (`sec`), marks — a layer per set (`xm:<set>`). After a lost
 * context everything comes again. A lazy chunk with three.js.
 */
import { api, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { Scene } from "../../viz/Scene";
import { VizError } from "../../viz/error";
import { HoleMarksView, marksQuery, MAX_MARK_LABELS, readHoleMarks, type HoleMarksContext } from "../../viz/holes/marks";
import { MarkerTextures } from "../../viz/holes/markers";
import { crossedWireframes } from "../../viz/holes/traceStyle";
import { LabelCache } from "../../viz/labels/sprites";
import { LoadQueue } from "../../viz/load/loader";
import { ExactPicker, pickRequest, type AbsRay, type ExactPick } from "../../viz/mesh/exact";
import { planeReaches, readSection, sectionQuery, SectionLinesView } from "../../viz/mesh/section";
import { sectionOf } from "../section/state";
import { setStyleOf, setVisibilityOf } from "./loaders";
import type { WireframeInfo } from "./objects";
import { sceneUi } from "./sceneHost";

export const SECTIONS_KEY = "sec";
export const marksKey = (setId: number) => `xm:${setId}`;
const wireframeKey = (id: number) => `wf:${id}`;

export interface ExactInput {
  wireframes: () => readonly WireframeInfo[];
  /** hidden keys in force (`wf:<id>`, `set:<id>`, `set:<id>:paths`) */
  hidden: () => readonly string[];
  /** sets of the project */
  sets: () => readonly { id: number }[];
  /** view keys of the project: the section and the styles of the traces */
  keys: () => Record<string, unknown>;
}

interface SectionState {
  /** the request these lines are the answer to */
  path: string;
  loading: boolean;
}

interface MarksState {
  path: string;
  /** versions of the wireframes in the request: a new name or colour asks again */
  sig: string;
  view: HoleMarksView | null;
  loading: boolean;
  failed: boolean;
  /** asked again while its request was in flight: the answer may be of the data before the change */
  stale: boolean;
}

export class ExactSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private readonly picker = new ExactPicker((body) => api.post("/wireframes/pick", body));
  /** shared by the marks of every set; its label cache is replaced when it has grown (`trimLabels`) */
  private readonly ctx: HoleMarksContext;
  private textures = new MarkerTextures();
  private lines: SectionLinesView | null = null;
  private readonly sections = new Map<number, SectionState>();
  private readonly marks = new Map<number, MarksState>();
  private inflight = 0;
  private generation = 0;
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: ExactInput,
  ) {
    this.ctx = { clip: scene.clipPlanes, textures: this.textures, labels: new LabelCache(scene.clipPlanes), pixelRatio: scene.renderer.pixelRatio };
  }

  get idle(): boolean {
    return this.inflight === 0;
  }

  /** For the E2E hook: the section lines per wireframe and the marks per set now in the scene. */
  stats(): { sections: ReturnType<SectionLinesView["stats"]>; marks: { set: number; marks: number; labels: number; objects: number[] }[] } {
    const marks = [...this.marks].flatMap(([set, st]) => (st.view ? [{ set, ...st.view.stats(), objects: st.view.marks.objects.map((o) => o.id) }] : []));
    return { sections: this.lines?.stats() ?? [], marks };
  }

  /** The lists, the hidden keys, the section or the styles of the traces changed. */
  sync(): void {
    if (this.disposed) return;
    this.syncSections();
    this.syncMarks(false);
    this.scene.invalidate();
  }

  /** The data of the sets may have changed (an edit, an import): the marks are asked again, a 304 keeps them. */
  reload(): void {
    if (!this.disposed) this.syncMarks(true);
  }

  /**
   * The exact triangle under a click: the ray in project coordinates against every shown wireframe, within
   * the section window. Null — nothing under the ray; undefined — a newer click took over, or the core failed
   * (the pick of the level in view stays).
   */
  async pick(ray: AbsRay): Promise<ExactPick | null | undefined> {
    const hidden = new Set(this.input.hidden());
    const ids = this.input.wireframes().filter((w) => !hidden.has(wireframeKey(w.id))).map((w) => w.id);
    const { line, clip } = sectionOf(this.input.keys());
    try {
      return await this.picker.pick(pickRequest(ray, ids, line, clip));
    } catch {
      return undefined; // the surface stays selected by the level in view
    }
  }

  cancelPick(): void {
    this.picker.cancel();
  }

  /** After a lost context: the scene dropped the layers, everything comes again. */
  restore(): void {
    this.generation++;
    this.queue.cancelAll();
    this.lines = null;
    this.sections.clear();
    this.marks.clear();
    this.ctx.labels.dispose();
    this.ctx.labels = new LabelCache(this.scene.clipPlanes);
    this.textures.dispose();
    this.textures = new MarkerTextures();
    this.ctx.textures = this.textures;
    this.ctx.pixelRatio = this.scene.renderer.pixelRatio;
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.picker.cancel();
    this.queue.cancelAll();
    this.scene.removeLayer(SECTIONS_KEY, false);
    for (const id of this.marks.keys()) this.scene.removeLayer(marksKey(id), false);
    this.sections.clear();
    this.marks.clear();
    this.ctx.labels.dispose();
    this.textures.dispose();
  }

  // ---------------------------------------------------------------- internals

  private url(path: string): string {
    const t = coreTarget();
    if (!t) throw new VizError("core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.", 0);
    return apiUrl(t, path);
  }

  private fail(e: unknown, what: string): void {
    if ((e instanceof VizError && (e.code === "cancelled" || e.status === 404)) || this.disposed) return; // gone: its layer leaves by the list
    sceneUi.set({ error: `${what}: ${e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e)}` });
  }

  private cancel(path: string): void {
    try {
      this.queue.cancel(this.url(path));
    } catch {
      /* the core is away: nothing is in flight */
    }
  }

  // ---- lines of sections

  private syncSections(): void {
    const { line, clip } = sectionOf(this.input.keys());
    const hidden = new Set(this.input.hidden());
    const wanted = new Map<number, { path: string; color: string | null }>();
    if (line && clip) {
      const query = sectionQuery(line);
      for (const w of this.input.wireframes()) {
        if (hidden.has(wireframeKey(w.id)) || !(w.triangle_count > 0) || !planeReaches(w.extent, line)) continue;
        wanted.set(w.id, { path: `/viz/wireframes/${w.id}/section?${query}`, color: w.color });
      }
    }
    for (const [id, st] of [...this.sections]) {
      if (wanted.get(id)?.path === st.path) continue;
      if (st.loading) this.cancel(st.path); // the request of a line that is not the section any more
      this.lines?.remove(id);
      this.sections.delete(id);
    }
    if (!wanted.size) {
      if (this.lines) this.scene.removeLayer(SECTIONS_KEY, false);
      this.lines = null;
      return;
    }
    if (!this.lines && line) {
      this.lines = new SectionLinesView({ x: Math.round(line.x0), y: Math.round(line.y0), z: 0 }, { resolution: this.scene.resolution });
      this.scene.addLayer(SECTIONS_KEY, this.lines);
    }
    for (const [id, want] of wanted) {
      if (this.sections.has(id)) this.lines?.setColor(id, want.color);
      else void this.loadSection(id, want.path);
    }
  }

  private async loadSection(id: number, path: string): Promise<void> {
    const st: SectionState = { path, loading: true };
    this.sections.set(id, st);
    const gen = this.generation;
    this.inflight++;
    try {
      const r = await this.queue.load({ url: this.url(path), revalidate: false });
      if (gen !== this.generation || this.disposed || this.sections.get(id) !== st || !r.buffer || !this.lines) return;
      const color = this.input.wireframes().find((w) => w.id === id)?.color ?? null;
      this.lines.set(id, readSection(r.buffer), color);
      this.scene.invalidate();
    } catch (e) {
      if (this.sections.get(id) === st) this.sections.delete(id); // asked again with the next change
      this.fail(e, "Сечение каркаса не построено");
    } finally {
      st.loading = false;
      this.inflight--;
    }
  }

  // ---- marks of crossings

  private syncMarks(revalidate: boolean): void {
    const hidden = this.input.hidden();
    const off = new Set(hidden);
    const keys = this.input.keys();
    const all = this.input.wireframes();
    const colorOf = (id: number) => this.input.wireframes().find((w) => w.id === id)?.color ?? null;
    const sets = new Set<number>();
    for (const s of this.input.sets()) {
      const style = setStyleOf(keys, s.id).trace;
      const vis = setVisibilityOf(hidden, s.id);
      const used = crossedWireframes(all, style, (id) => !off.has(wireframeKey(id)));
      if (!vis.set || !vis.paths || !used.length) continue;
      sets.add(s.id);
      const path = `/viz/sets/${s.id}/intersections?${marksQuery(used.map((w) => w.id))}`;
      const sig = used.map((w) => `${w.id}:${w.version}`).join(",");
      const st = this.marks.get(s.id);
      if (st && st.path === path) {
        st.view?.apply(style, colorOf);
        if (revalidate || st.sig !== sig || st.failed) {
          if (st.loading) st.stale = true;
          else void this.loadMarks(s.id, st, sig, true);
        }
        continue;
      }
      if (st) this.dropMarks(s.id);
      const next: MarksState = { path, sig, view: null, loading: false, failed: false, stale: false };
      this.marks.set(s.id, next);
      void this.loadMarks(s.id, next, sig, false);
    }
    for (const id of [...this.marks.keys()]) if (!sets.has(id)) this.dropMarks(id);
  }

  /**
   * The textures of the labels are kept by their text; marks that were replaced leave theirs behind. When the
   * cache has grown well past what the marks in the scene can use, it is made anew and the labels with it.
   */
  private trimLabels(): void {
    const views = [...this.marks].flatMap(([id, st]) => (st.view ? [{ id, view: st.view }] : []));
    if (this.ctx.labels.size <= 2 * MAX_MARK_LABELS * (views.length + 1)) return;
    this.ctx.labels.dispose();
    this.ctx.labels = new LabelCache(this.scene.clipPlanes);
    const keys = this.input.keys();
    const colorOf = (id: number) => this.input.wireframes().find((w) => w.id === id)?.color ?? null;
    for (const { id, view } of views) view.apply(setStyleOf(keys, id).trace, colorOf);
  }

  private dropMarks(setId: number): void {
    const st = this.marks.get(setId);
    if (!st) return;
    if (st.loading) this.cancel(st.path);
    if (st.view) this.scene.removeLayer(marksKey(setId), false);
    this.marks.delete(setId);
  }

  private async loadMarks(setId: number, st: MarksState, sig: string, revalidate: boolean): Promise<void> {
    const gen = this.generation;
    st.loading = true;
    st.failed = false;
    this.inflight++;
    try {
      const r = await this.queue.load({ url: this.url(st.path), revalidate: revalidate && st.view !== null });
      if (gen !== this.generation || this.disposed || this.marks.get(setId) !== st) return;
      st.sig = sig;
      if (!r.buffer) return; // 304: the same marks
      const marks = readHoleMarks(r.buffer);
      const style = setStyleOf(this.input.keys(), setId).trace;
      const colorOf = (id: number) => this.input.wireframes().find((w) => w.id === id)?.color ?? null;
      if (st.view) this.scene.removeLayer(marksKey(setId), false);
      st.view = null;
      this.trimLabels();
      st.view = marks.n ? new HoleMarksView(marks, this.ctx, style, colorOf) : null;
      if (st.view) this.scene.addLayer(marksKey(setId), st.view);
      this.scene.invalidate();
    } catch (e) {
      if (this.marks.get(setId) === st) st.failed = true;
      this.fail(e, "Пересечения стволов с каркасами не построены");
    } finally {
      st.loading = false;
      this.inflight--;
      if (st.stale && this.marks.get(setId) === st && gen === this.generation && !this.disposed) {
        st.stale = false;
        this.syncMarks(true);
      }
    }
  }
}
