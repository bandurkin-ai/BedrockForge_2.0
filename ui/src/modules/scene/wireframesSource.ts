/**
 * Wireframes and DTMs of the project in the 3D scene (ТЗ 7.20, ADR 0011 §3–§4, ADR 0004 §3), after
 * `LinesSource`: manifest (`If-None-Match`) → one scene layer per wireframe (`wf:<id>`, its extent counts at
 * once) → first the coarsest level (1/64) of every part — the first frame — then, for the parts in view, the
 * level their size on the screen asks for (at most 2 triangles per pixel, 20 % hysteresis), nearer the centre
 * of the screen first. Under clipping the parts that reach into the section window come in full, the others
 * are not asked. The triangle budget of the frame makes the smallest parts coarser («упрощено»); buffers are
 * accounted in `GpuMemory` (parts out of view and finer levels go first, 1/64 stays). Parts are checked in
 * the Worker and used as they came. Visibility follows the hidden key `wf:<id>`; a hidden wireframe that was
 * never shown is not loaded. Colour, opacity and the settings of a DTM follow the list of the project
 * without reloading anything; the image of a draped raster is the texture of the raster's own layer.
 * After a lost context everything comes again. A lazy chunk with three.js.
 */
import type * as THREE from "three";
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { FrameContext, Scene } from "../../viz/Scene";
import { boundingSphere, boxFromBbox6, scaleZ } from "../../viz/core/bounds";
import { GpuMemory, MEMORY_BUDGET, TRIANGLE_BUDGET } from "../../viz/core/budget";
import { boxInFrustum, frustumPlanes, screenPriority } from "../../viz/core/cull";
import { screenRadius, shownLevel } from "../../viz/core/lod";
import { VizError } from "../../viz/error";
import { LoadQueue } from "../../viz/load/loader";
import { levelTriangles, reachesClip, readMeshManifest, sameMeshGeometry, wantedLevels, type LevelPart, type MeshManifest } from "../../viz/mesh/build";
import { MeshLayerView, type MeshProbe } from "../../viz/mesh/layer";
import { GeometryWorker } from "../../viz/worker/client";
import type { RasterInfo, WireframeInfo } from "./objects";
import type { DrapeUse } from "./rastersSource";
import { sceneUi } from "./sceneHost";

/** The share of the video memory budget surfaces may take (ADR 0004 §3). */
const MESH_MEMORY_SHARE = 0.4;
/** The share of the triangle budget of a frame surfaces may take; the rest is for holes, displays and blocks. */
const MESH_TRIANGLE_SHARE = 0.5;
/** The coarsest level of every part goes before any finer level (smaller — sooner). */
const FIRST_FRAME = -2000;

export interface WireframesInput {
  wireframes: () => readonly WireframeInfo[];
  rasters: () => readonly RasterInfo[];
  /** hidden keys in force (`wf:<id>`) */
  hidden: () => readonly string[];
  /** «Непрозрачность каркасов» of the view, 0.1…1 */
  opacity: () => number;
  /** the image a raster shows now: the texture of its layer */
  texture: (rasterId: number) => THREE.Texture | null;
  /** the shown DTMs that carry rasters changed (the flat rasters follow) */
  onDrapes?: () => void;
}

interface PartState {
  /** the level wanted now; null — out of view */
  want: number | null;
  priority: number;
  loading: Set<number>;
  /** levels not to ask again: failed, or no room on the video card */
  failed: Set<number>;
  /** triangles of every level: real for the loaded ones, by the ratio of the level for the others */
  triangles: number[];
  /** the finest level worth asking: a coarser one that came back whole is the full part */
  floor: number;
}

interface LayerState {
  id: number;
  manifest: MeshManifest;
  view: MeshLayerView;
  coarsest: number;
  parts: PartState[];
  /** the raster the surface carries now (a plan raster of the project), null — none */
  rasterId: number | null;
  clip: boolean;
}

export const wireframeKey = (id: number) => `wf:${id}`;
const partId = (id: number, k: number) => `${id}:${k}`;

export class WireframesSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private readonly worker = new GeometryWorker();
  private memory: GpuMemory;
  private readonly layers = new Map<number, LayerState>();
  /** wireframes without a triangle to show: id → version (not asked again) */
  private readonly empty = new Map<number, number>();
  private readonly asking = new Set<number>();
  private readonly off: (() => void)[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = 0;
  private generation = 0;
  private lastFrame = 0;
  private over = false;
  private budgeted = false;
  private selected: number | null = null;
  private forced: number | null = null;
  private drapeKey = "";
  private disposed = false;
  private publishing = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: WireframesInput,
  ) {
    this.memory = new GpuMemory(this.memoryLimit());
    this.off.push(scene.onFrame((ctx) => this.frame(ctx)));
  }

  get idle(): boolean {
    return this.inflight === 0 && this.asking.size === 0 && this.timer === null;
  }

  /**
   * Surfaces in the scene; of the visible ones — parts and triangles shown, how many parts are shown at every
   * level, the DTMs that show an image (E2E, the status notes).
   */
  stats(): { layers: number; parts: number; triangles: number; simplified: number; shown: number[]; draped: number[]; budgeted: boolean; over: boolean } {
    const out = { layers: this.layers.size, parts: 0, triangles: 0, simplified: 0, shown: [] as number[], draped: [] as number[], budgeted: this.budgeted, over: this.over };
    for (const st of this.layers.values()) {
      if (!st.view.visible) continue;
      const s = st.view.stats();
      out.parts += s.parts;
      out.triangles += s.triangles;
      out.simplified += s.simplified;
      st.parts.forEach((_, k) => {
        const lod = st.view.shown(k);
        if (lod !== null) out.shown[lod] = (out.shown[lod] ?? 0) + 1;
      });
      if (st.view.draped) out.draped.push(st.id);
    }
    out.shown = Array.from(out.shown, (n) => n ?? 0);
    return out;
  }

  /** The shown DTMs that carry rasters: their flat rasters are hidden or cut out under them. */
  drapes(): DrapeUse[] {
    const out: DrapeUse[] = [];
    for (const st of this.layers.values()) if (st.rasterId !== null && st.view.visible) out.push({ rasterId: st.rasterId, clip: st.clip, bbox: st.view.bbox });
    return out;
  }

  /** The list of wireframes, their colours and DTM settings, the rasters, the opacity or the hidden keys changed. */
  sync(): void {
    if (this.disposed) return;
    const list = this.input.wireframes();
    const hidden = new Set(this.input.hidden());
    const ids = new Set(list.map((w) => w.id));
    let changed = false;
    for (const id of [...this.layers.keys()]) {
      if (ids.has(id)) continue;
      this.drop(id);
      changed = true;
    }
    for (const id of [...this.empty.keys()]) if (!ids.has(id)) this.empty.delete(id);
    for (const w of list) {
      const visible = !hidden.has(wireframeKey(w.id));
      const st = this.layers.get(w.id);
      if (st) {
        if (st.view.visible !== visible) changed = true;
        this.style(st, w, visible);
        if (visible && st.manifest.version !== w.version) this.ask(w.id);
      } else if (visible && this.empty.get(w.id) !== w.version) this.ask(w.id);
    }
    if (changed) this.scene.layerChanged();
    this.drapesChanged();
    this.scene.invalidate();
    this.publish();
  }

  /** The picked surface glows (null — none). */
  select(id: number | null): void {
    this.selected = id;
    for (const st of this.layers.values()) st.view.select(st.id === id);
    this.scene.invalidate();
  }

  /** The surface of a wireframe over a plan point as it is drawn now (E2E: the drape on two levels). */
  probe(id: number, x: number, y: number): MeshProbe | null {
    return this.layers.get(id)?.view.probe(x, y) ?? null;
  }

  /** E2E: every part in view at this level (null — by its size on the screen again). */
  forceLevel(level: number | null): void {
    this.forced = level;
    this.scene.invalidate();
  }

  /** After a lost context: the scene dropped the layers, everything comes again. */
  restore(): void {
    this.generation++;
    this.queue.cancelAll();
    this.layers.clear();
    this.empty.clear();
    this.asking.clear();
    this.memory = new GpuMemory(this.memoryLimit());
    this.over = false;
    this.budgeted = false;
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.off.forEach((fn) => fn());
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.queue.cancelAll();
    this.worker.dispose();
    for (const id of [...this.layers.keys()]) this.scene.removeLayer(wireframeKey(id), false);
    this.layers.clear();
  }

  // ---------------------------------------------------------------- internals

  private memoryLimit(): number {
    return MEMORY_BUDGET[this.scene.gpu.gpuClass] * this.scene.renderer.budgetFactor() * MESH_MEMORY_SHARE;
  }

  /** Colour, opacity, visibility and the raster of a DTM from the list of the project: nothing is reloaded. */
  private style(st: LayerState, w: WireframeInfo, visible: boolean): void {
    st.view.setStyle({ color: w.color ?? st.manifest.color, opacity: this.input.opacity(), visible });
    const raster = w.dtm?.raster_id ? this.input.rasters().find((r) => r.id === w.dtm!.raster_id) : undefined;
    // a vertical raster is never laid on a relief (ТЗ 7.20); one without a georeference has no place on it
    const corners = raster && !raster.vertical ? raster.georef?.corners3d : null;
    st.rasterId = w.dtm && raster && corners ? raster.id : null;
    st.clip = w.dtm?.clip === true;
    st.view.setDrape(w.dtm && corners ? { corners, tint: w.dtm.tint, opacity: w.dtm.opacity, shade: w.dtm.shade } : null);
    st.view.setDrapeTexture(st.rasterId === null ? null : this.input.texture(st.rasterId));
  }

  private drapesChanged(): void {
    const key = JSON.stringify(this.drapes().map((d) => [d.rasterId, d.clip, Array.from(d.bbox)]));
    if (key === this.drapeKey) return;
    this.drapeKey = key;
    this.input.onDrapes?.();
  }

  private drop(id: number): void {
    const st = this.layers.get(id);
    if (!st) return;
    st.parts.forEach((_, k) => this.memory.removePart(partId(id, k)));
    this.layers.delete(id);
    this.scene.removeLayer(wireframeKey(id));
  }

  private url(path: string): string {
    const t = coreTarget();
    if (!t) throw new VizError("core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.", 0);
    return apiUrl(t, path);
  }

  private fail(e: unknown, what: string): void {
    if ((e instanceof VizError && e.code === "cancelled") || this.disposed) return;
    sceneUi.set({ error: `${what}: ${e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e)}` });
  }

  /** The manifest of a wireframe; another geometry replaces the layer of the scene, the same one keeps what is built. */
  private ask(id: number): void {
    if (this.asking.has(id) || this.disposed) return;
    this.asking.add(id);
    this.publish();
    const gen = this.generation;
    const known = this.layers.has(id);
    void Promise.resolve()
      .then(() => this.queue.load({ url: this.url(`/viz/wireframes/${id}`), priority: FIRST_FRAME - 1, revalidate: known }))
      .then((r) => {
        const ref = this.input.wireframes().find((w) => w.id === id);
        if (gen !== this.generation || this.disposed || !ref || !r.buffer) return;
        const m = readMeshManifest(r.buffer);
        const visible = !this.input.hidden().includes(wireframeKey(id));
        const old = this.layers.get(id);
        if (old && sameMeshGeometry(old.manifest, m)) {
          old.manifest = m; // a new name, colour or DTM setting: what is built stays
          for (const p of old.parts) p.failed.clear();
          this.style(old, ref, visible);
        } else {
          if (old) this.drop(id);
          this.empty.delete(id);
          if (!m.parts.length) this.empty.set(id, m.version);
          else {
            const view = new MeshLayerView(m, { clip: this.scene.clipPlanes }, { color: ref.color ?? m.color, opacity: this.input.opacity(), visible });
            view.select(this.selected === id);
            const parts = m.parts.map((p) => ({ want: null, priority: 0, loading: new Set<number>(), failed: new Set<number>(), triangles: levelTriangles(p.n, m.ratios), floor: 0 }));
            const st: LayerState = { id, manifest: m, view, coarsest: Math.max(0, m.ratios.length - 1), parts, rasterId: null, clip: false };
            this.layers.set(id, st);
            this.style(st, ref, visible);
            this.scene.addLayer(wireframeKey(id), view);
          }
        }
        this.drapesChanged();
        sceneUi.set({ error: null });
        this.scene.invalidate();
      })
      .catch((e: unknown) => {
        // the wireframe is gone (removed in another window): it leaves the scene at once
        if (e instanceof VizError && e.status === 404 && gen === this.generation) {
          this.drop(id);
          this.drapesChanged();
        } else this.fail(e, "Каркас не загружен");
      })
      .finally(() => {
        this.asking.delete(id);
        this.publish();
      });
  }

  /** Before every frame: the parts in view, the level each is wanted at, what to show now and what to load. */
  private frame(ctx: FrameContext): void {
    const origin = this.scene.origin.value;
    if (!origin || !this.layers.size) return;
    const planes = frustumPlanes(ctx.camera, ctx.options);
    const clip = this.scene.clipPlanes;
    const now = performance.now();
    this.lastFrame = now;
    const refs: { st: LayerState; k: number }[] = [];
    const measured: LevelPart[] = [];
    for (const st of this.layers.values()) {
      // the image of the draped raster as its layer shows it now (the small copy, then the large one)
      if (st.rasterId !== null) st.view.setDrapeTexture(this.input.texture(st.rasterId));
      if (!st.view.visible) continue;
      st.parts.forEach((p, k) => {
        const at = boxFromBbox6(st.manifest.parts[k].bbox, origin);
        const box = scaleZ(at, ctx.vexag);
        // under clipping only the parts that reach into the section window are seen — and they come in full
        const seen = boxInFrustum(box, planes) && reachesClip(at, clip);
        p.priority = screenPriority(box, ctx.camera, ctx.options, planes);
        refs.push({ st, k });
        measured.push({ screen: seen ? screenRadius(boundingSphere(box), ctx.camera, ctx.options, ctx.height) : null, triangles: p.triangles, current: p.want ?? st.view.shown(k), floor: p.floor, exact: clip.length > 0 });
      });
    }
    const budget = TRIANGLE_BUDGET[this.scene.gpu.gpuClass] * this.scene.renderer.budgetFactor() * MESH_TRIANGLE_SHARE;
    const wanted = wantedLevels(measured, budget);
    this.budgeted = wanted.budgeted;
    const inView: string[] = [];
    let work = false;
    refs.forEach(({ st, k }, i) => {
      const p = st.parts[k];
      let want = wanted.levels[i];
      if (want !== null && this.forced !== null) want = Math.min(Math.max(this.forced, p.floor), st.coarsest);
      p.want = want;
      const id = partId(st.id, k);
      // out of view the part keeps what it shows; the first level to come is shown at once
      const show = want !== null ? shownLevel(want, st.view.levels(k)) : st.view.shown(k) ?? shownLevel(st.coarsest, st.view.levels(k));
      st.view.show(k, show);
      if (show !== null) this.memory.touch(id, show, now);
      if (want !== null) inView.push(id);
      if (this.next(st, k) !== null) work = true;
    });
    this.memory.setVisible(inView);
    if (work) this.schedule();
    this.publish();
  }

  /** The level of part k to load now: 1/64 first — the first frame of the whole object — then the wanted one. */
  private next(st: LayerState, k: number): number | null {
    const p = st.parts[k];
    const can = (lod: number) => st.view.levelTriangles(k, lod) === null && !p.loading.has(lod) && !p.failed.has(lod);
    if (can(st.coarsest)) return st.coarsest;
    return p.want !== null && can(p.want) ? p.want : null;
  }

  private schedule(): void {
    if (this.timer || this.disposed) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.disposed) return;
      for (const st of this.layers.values()) {
        if (!st.view.visible) continue;
        st.parts.forEach((_, k) => {
          const lod = this.next(st, k);
          if (lod !== null) void this.loadPart(st, k, lod);
        });
      }
      this.publish();
    }, 0);
  }

  private async loadPart(st: LayerState, k: number, lod: number): Promise<void> {
    const gen = this.generation;
    const etag = st.manifest.parts[k].etags[lod];
    const p = st.parts[k];
    const stale = () => gen !== this.generation || this.disposed || this.layers.get(st.id) !== st || st.manifest.parts[k]?.etags[lod] !== etag;
    p.loading.add(lod);
    this.inflight++;
    this.publish();
    try {
      const priority = lod === st.coarsest ? FIRST_FRAME + Math.min(p.priority, 999) : p.priority;
      const r = await this.queue.load({ url: this.url(`/viz/wireframes/${st.id}/parts/${k}?lod=${lod}`), priority, revalidate: false });
      if (stale() || !r.buffer) return;
      const built = await this.worker.mesh(r.buffer);
      if (stale()) return;
      const id = partId(st.id, k);
      const bytes = built.positions.byteLength + built.index.byteLength;
      this.memory.limit = this.memoryLimit();
      this.memory.remove(id, lod);
      const room = this.memory.makeRoom(bytes, this.lastFrame);
      for (const e of room.evicted) {
        const [layerId, part] = e.part.split(":").map(Number);
        this.layers.get(layerId)?.view.dropLevel(part, e.level);
      }
      // no room for a finer level: the part keeps what it shows; with nothing to show it is taken anyway
      if (!room.ok && st.view.levels(k).length) {
        p.failed.add(lod);
        this.over = true;
        return;
      }
      st.view.setLevel(k, built);
      p.triangles[lod] = built.triangles;
      if (!built.simplified) {
        // the level came back whole (a small part, or one that cannot be simplified): nothing finer to ask
        p.floor = Math.max(p.floor, lod);
        for (let l = 0; l < lod; l++) p.triangles[l] = built.triangles;
      }
      this.memory.add(id, lod, bytes, lod === st.coarsest, performance.now());
      this.scene.invalidate();
    } catch (e) {
      if (!stale()) p.failed.add(lod);
      this.fail(e, "Часть каркаса не загружена");
    } finally {
      p.loading.delete(lod);
      this.inflight--;
      this.publish();
    }
  }

  /** The state for the notes of the window; set after the frame that found it out, once. */
  private publish(): void {
    if (this.disposed || this.publishing) return;
    this.publishing = true;
    queueMicrotask(() => {
      this.publishing = false;
      if (this.disposed) return;
      const loading = this.inflight + this.asking.size;
      const ui = sceneUi.get();
      if (ui.wireframesLoading === loading && ui.wireframes === this.layers.size && ui.wireframesSimplified === this.budgeted && ui.wireframesOver === this.over) return;
      sceneUi.set({ wireframesLoading: loading, wireframes: this.layers.size, wireframesSimplified: this.budgeted, wireframesOver: this.over });
    });
  }
}
