/**
 * Line and point layers of the project in the 3D scene (ТЗ 7.18, ADR 0011 §3–§4), after `HolesSource`:
 * manifest (`If-None-Match`) → one scene layer per layer of the project (`ln:<id>`, its extent counts at
 * once) → the parts in view at the level of detail their distance asks for, nearer the centre of the screen
 * first, built in the Worker. A coarser or finer loaded level is shown until the wanted one comes; a level
 * coarser than full is «упрощено». Buffers are accounted in `GpuMemory` (parts out of view and finer levels go
 * first). Visibility follows the hidden key `ln:<id>`; a hidden layer that was never shown is not loaded.
 * A rename or a new colour keeps what is built while the parts reload. After a lost context everything comes
 * again. A lazy chunk with three.js.
 */
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { FrameContext, Scene } from "../../viz/Scene";
import { boxFromBbox6, scaleZ } from "../../viz/core/bounds";
import { GpuMemory, MEMORY_BUDGET } from "../../viz/core/budget";
import { boxInFrustum, frustumPlanes, screenPriority } from "../../viz/core/cull";
import { shownLevel } from "../../viz/core/lod";
import { VizError } from "../../viz/error";
import { THIN_SEGMENTS } from "../../viz/holes/paths";
import { LabelCache } from "../../viz/labels/sprites";
import { lineBoundaries, lineLevel, nearestInBox, readLinesManifest, sameLayout, type LinesManifest } from "../../viz/lines/build";
import { LINE_WIDTH_PX, LineLayerView } from "../../viz/lines/layer";
import { LoadQueue } from "../../viz/load/loader";
import { GeometryWorker } from "../../viz/worker/client";
import type { LineLayerInfo } from "./objects";
import { sceneUi } from "./sceneHost";

/** The share of the video memory budget lines may take (ADR 0004 §3). */
const LINES_SHARE = 0.25;

export interface LinesInput {
  /** line layers of the project */
  layers: () => readonly LineLayerInfo[];
  /** hidden keys in force (`ln:<id>`) */
  hidden: () => readonly string[];
  /** the level of what has no height: under the lowest object */
  floor: () => number;
  /** the layers in the scene changed (others lie under the lowest of them) */
  onContent?: () => void;
}

interface PartState {
  want: number | null;
  priority: number;
  loading: Set<number>;
  /** levels not to ask again: failed, or no room on the video card */
  failed: Set<number>;
}

interface LayerState {
  id: number;
  manifest: LinesManifest;
  view: LineLayerView;
  bounds: number[];
  parts: PartState[];
  /** `k:lod` of the levels built from this manifest's version of the layer */
  fresh: Set<string>;
}

export const lineKey = (id: number) => `ln:${id}`;
const partId = (id: number, k: number) => `${id}:${k}`;

export class LinesSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private readonly worker = new GeometryWorker();
  private labels: LabelCache;
  private memory: GpuMemory;
  private readonly layers = new Map<number, LayerState>();
  /** layers without a single vertex: id → version (nothing to show, not asked again) */
  private readonly empty = new Map<number, number>();
  private readonly asking = new Set<number>();
  private readonly off: (() => void)[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = 0;
  private generation = 0;
  private lastFrame = 0;
  private fat = true;
  private over = false;
  private selected: { layerId: number; line: number } | null = null;
  private disposed = false;
  private publishing = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: LinesInput,
  ) {
    this.labels = new LabelCache(scene.clipPlanes);
    this.memory = new GpuMemory(this.memoryLimit());
    this.off.push(scene.onFrame((ctx) => this.frame(ctx)));
  }

  get idle(): boolean {
    return this.inflight === 0 && this.asking.size === 0 && this.timer === null;
  }

  /** Layers in the scene, parts and segments shown, the share of simplified parts (E2E, the status note). */
  stats(): { layers: number; parts: number; segments: number; points: number; simplified: number; over: boolean } {
    const out = { layers: this.layers.size, parts: 0, segments: 0, points: 0, simplified: 0, over: this.over };
    for (const st of this.layers.values()) {
      if (!st.view.visible) continue;
      const s = st.view.stats();
      out.parts += s.parts;
      out.segments += s.segments;
      out.points += s.points;
      out.simplified += s.simplified;
    }
    return out;
  }

  /** Lowest heights of the layers that have heights, for the floor of what has none. */
  extents(): { zmin: number; visible: boolean }[] {
    const out: { zmin: number; visible: boolean }[] = [];
    for (const st of this.layers.values()) if (st.view.zmin !== null) out.push({ zmin: st.view.zmin, visible: st.view.visible });
    return out;
  }

  /** The list of layers, their colours or the hidden keys changed. */
  sync(): void {
    if (this.disposed) return;
    const list = this.input.layers();
    const hidden = new Set(this.input.hidden());
    const ids = new Set(list.map((l) => l.id));
    let changed = false;
    for (const id of [...this.layers.keys()]) {
      if (ids.has(id)) continue;
      this.drop(id);
      changed = true;
    }
    for (const id of [...this.empty.keys()]) if (!ids.has(id)) this.empty.delete(id);
    for (const l of list) {
      const visible = !hidden.has(lineKey(l.id));
      const st = this.layers.get(l.id);
      if (st) {
        if (st.view.visible !== visible) changed = true;
        st.view.setVisible(visible);
        st.view.setColor(l.color);
        if (visible && st.manifest.version !== l.version) this.ask(l.id);
      } else if (visible && this.empty.get(l.id) !== l.version) this.ask(l.id);
    }
    if (changed) this.contentChanged();
    this.scene.invalidate();
  }

  /** Ask the manifests of the shown layers again (304 when nothing changed). */
  reload(): void {
    for (const id of this.layers.keys()) this.ask(id);
  }

  /** The floor moved (the lowest object changed): flat parts follow. */
  refloor(): void {
    const z = this.input.floor();
    let moved = false;
    for (const st of this.layers.values()) moved = st.view.setFloor(z) || moved;
    if (moved) this.scene.layerChanged();
  }

  /** The picked line of a layer, drawn over in orange (null — none). */
  select(pick: { layerId: number; line: number } | null): void {
    this.selected = pick;
    for (const st of this.layers.values()) st.view.select(pick && pick.layerId === st.id ? pick.line : null);
    this.scene.invalidate();
  }

  /** After a lost context: the scene dropped the layers, everything comes again in full. */
  restore(): void {
    this.generation++;
    this.queue.cancelAll();
    this.layers.clear();
    this.empty.clear();
    this.asking.clear();
    this.labels.dispose();
    this.labels = new LabelCache(this.scene.clipPlanes);
    this.memory = new GpuMemory(this.memoryLimit());
    this.over = false;
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.off.forEach((fn) => fn());
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.queue.cancelAll();
    this.worker.dispose();
    for (const id of [...this.layers.keys()]) this.scene.removeLayer(lineKey(id), false);
    this.layers.clear();
    this.labels.dispose();
  }

  // ---------------------------------------------------------------- internals

  private memoryLimit(): number {
    return MEMORY_BUDGET[this.scene.gpu.gpuClass] * this.scene.renderer.budgetFactor() * LINES_SHARE;
  }

  private contentChanged(): void {
    this.scene.layerChanged();
    this.input.onContent?.();
  }

  private drop(id: number): void {
    const st = this.layers.get(id);
    if (!st) return;
    st.parts.forEach((_, k) => this.memory.removePart(partId(id, k)));
    this.layers.delete(id);
    this.scene.removeLayer(lineKey(id));
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

  /** The manifest of a layer; a changed one replaces the layer of the scene or, with the same parts, renews it. */
  private ask(id: number): void {
    if (this.asking.has(id) || this.disposed) return;
    this.asking.add(id);
    this.publish();
    const gen = this.generation;
    const known = this.layers.has(id);
    void Promise.resolve()
      .then(() => this.queue.load({ url: this.url(`/viz/lines/${id}`), priority: -1, revalidate: known }))
      .then((r) => {
        const ref = this.input.layers().find((l) => l.id === id);
        if (gen !== this.generation || this.disposed || !ref || !r.buffer) return;
        const m = readLinesManifest(r.buffer);
        const visible = !this.input.hidden().includes(lineKey(id));
        const old = this.layers.get(id);
        if (old && sameLayout(old.manifest, m)) {
          // the same parts: what is shown stays, the levels of another version reload behind it
          for (const key of [...old.fresh]) {
            const [k, lod] = key.split(":").map(Number);
            if (old.manifest.parts[k].etags[lod] !== m.parts[k].etags[lod]) old.fresh.delete(key);
          }
          old.manifest = m;
          old.view.setColor(ref.color ?? m.color);
          for (const p of old.parts) p.failed.clear();
        } else {
          if (old) this.drop(id);
          this.empty.delete(id);
          if (!m.parts.length) this.empty.set(id, m.version);
          else {
            const view = new LineLayerView(m, { clip: this.scene.clipPlanes, resolution: this.scene.resolution, labels: this.labels, pixelRatio: this.scene.renderer.pixelRatio }, { color: ref.color ?? m.color, visible }, this.input.floor());
            view.setFat(this.fat);
            if (this.selected?.layerId === id) view.select(this.selected.line);
            this.layers.set(id, { id, manifest: m, view, bounds: lineBoundaries(m.tols), parts: m.parts.map(() => ({ want: null, priority: 0, loading: new Set(), failed: new Set() })), fresh: new Set() });
            this.scene.addLayer(lineKey(id), view);
          }
          this.input.onContent?.();
        }
        sceneUi.set({ error: null });
        this.scene.invalidate();
      })
      .catch((e: unknown) => {
        // the layer is gone (removed in another window): it leaves the scene at once
        if (e instanceof VizError && e.status === 404 && gen === this.generation) {
          this.drop(id);
          this.input.onContent?.();
        } else this.fail(e, "Слой линий не загружен");
      })
      .finally(() => {
        this.asking.delete(id);
        this.publish();
      });
  }

  /** Before every frame: the parts in view, their level, what to show now and what to load. */
  private frame(ctx: FrameContext): void {
    const origin = this.scene.origin.value;
    if (!origin || !this.layers.size) return;
    const planes = frustumPlanes(ctx.camera, ctx.options);
    const now = performance.now();
    this.lastFrame = now;
    const inView: string[] = [];
    let work = false;
    let changed = false;
    for (const st of this.layers.values()) {
      if (!st.view.visible) continue;
      st.parts.forEach((p, k) => {
        const box = scaleZ(boxFromBbox6(st.view.partBbox(k), origin), ctx.vexag);
        if (!boxInFrustum(box, planes)) {
          p.want = null;
          return;
        }
        const id = partId(st.id, k);
        inView.push(id);
        // the level by the place of the part nearest to the camera: where it is seen largest
        p.want = lineLevel(ctx.pixelsPerUnit(nearestInBox(box, ctx.camera.position)), st.bounds, p.want ?? st.view.shown(k));
        p.priority = screenPriority(box, ctx.camera, ctx.options, planes);
        const show = shownLevel(p.want, st.view.levels(k));
        if (st.view.show(k, show)) changed = true;
        if (show !== null) this.memory.touch(id, show, now);
        if (!st.fresh.has(`${k}:${p.want}`) && !p.loading.has(p.want) && !p.failed.has(p.want)) work = true;
      });
    }
    this.memory.setVisible(inView);
    // a loaded level is shown here, a frame after its load: the segments of the frame are counted with it
    if (changed) this.updateFat();
    if (work) this.schedule();
    this.publish();
  }

  private schedule(): void {
    if (this.timer || this.disposed) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.pump();
    }, 0);
  }

  /** Starts the loads of the wanted levels (the queue runs four at once, by priority). */
  private pump(): void {
    if (this.disposed) return;
    for (const st of this.layers.values()) {
      if (!st.view.visible) continue;
      st.parts.forEach((p, k) => {
        const lod = p.want;
        if (lod === null || st.fresh.has(`${k}:${lod}`) || p.loading.has(lod) || p.failed.has(lod)) return;
        void this.loadPart(st, k, lod, p);
      });
    }
    this.publish();
  }

  private async loadPart(st: LayerState, k: number, lod: number, p: PartState): Promise<void> {
    const gen = this.generation;
    const manifest = st.manifest;
    const stale = () => gen !== this.generation || this.disposed || this.layers.get(st.id) !== st || st.manifest !== manifest;
    p.loading.add(lod);
    this.inflight++;
    this.publish();
    try {
      const r = await this.queue.load({ url: this.url(`/viz/lines/${st.id}/parts/${k}?lod=${lod}`), priority: p.priority, revalidate: false });
      if (stale() || !r.buffer) return;
      const built = await this.worker.lines(r.buffer);
      if (stale()) return;
      const id = partId(st.id, k);
      const bytes = built.positions.byteLength + built.dots.byteLength;
      this.memory.limit = this.memoryLimit();
      this.memory.remove(id, lod);
      const room = this.memory.makeRoom(bytes, this.lastFrame);
      for (const e of room.evicted) {
        const [layerId, part] = e.part.split(":").map(Number);
        const owner = this.layers.get(layerId);
        owner?.view.dropLevel(part, e.level);
        owner?.fresh.delete(`${part}:${e.level}`);
      }
      // no room for a finer level: the part keeps what it shows; with nothing to show it is taken anyway
      if (!room.ok && st.view.levels(k).length) {
        p.failed.add(lod);
        this.over = true;
        return;
      }
      st.view.setLevel(k, built);
      st.fresh.add(`${k}:${lod}`);
      this.memory.add(id, lod, bytes, lod === manifest.tols.length - 1, performance.now());
      this.updateFat();
      this.scene.invalidate();
    } catch (e) {
      if (!stale()) p.failed.add(lod);
      this.fail(e, "Часть слоя линий не загружена");
    } finally {
      p.loading.delete(lod);
      this.inflight--;
      this.publish();
    }
  }

  /** Lines one pixel wide when the scene holds more than a million segments (ADR 0007 §7). */
  private updateFat(): void {
    let total = this.scene.debug().segments;
    for (const st of this.layers.values()) total += st.view.stats().segments;
    const fat = LINE_WIDTH_PX > 1 && total <= THIN_SEGMENTS;
    if (fat === this.fat) return;
    this.fat = fat;
    for (const st of this.layers.values()) st.view.setFat(fat);
  }

  /** The state for the notes of the window; set after the frame that found it out, once. */
  private publish(): void {
    if (this.disposed || this.publishing) return;
    this.publishing = true;
    queueMicrotask(() => {
      this.publishing = false;
      if (this.disposed) return;
      const loading = this.inflight + this.asking.size;
      const s = this.stats();
      const simplified = s.parts ? s.simplified / s.parts : 0;
      const ui = sceneUi.get();
      if (ui.linesLoading === loading && ui.lines === s.layers && Math.abs(ui.linesSimplified - simplified) < 1e-9 && ui.linesOver === s.over) return;
      sceneUi.set({ linesLoading: loading, lines: s.layers, linesSimplified: simplified, linesOver: s.over });
    });
  }
}
