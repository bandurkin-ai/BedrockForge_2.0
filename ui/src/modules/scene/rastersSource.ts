/**
 * Rasters of the project in the 3D scene (ТЗ 7.19, ADR 0011 §4): a quad per raster (`rs:<id>`) on the corners
 * of its georeference — in plan at its elevation or under the lowest object, vertical by the heights of its
 * corners (a backdrop under clipping, `viz/rasters/layer.ts`) — with the display copy of the image as the
 * texture: the small one (≤ 1024 px) first and always, the large one (≤ 4096 px) when the raster is
 * stretched over more pixels than the small copy has and the video memory budget allows. The image comes
 * from `/rasters/{id}/image?level=` and is decoded off the main thread (`createImageBitmap`). Visibility
 * follows the hidden key `rs:<id>`; opacity, elevation and corners follow the list of the project. After a
 * lost context everything comes again. A lazy chunk with three.js.
 */
import * as THREE from "three";
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { FrameContext, Scene } from "../../viz/Scene";
import { boxFromBbox6, scaleZ } from "../../viz/core/bounds";
import { GpuMemory, MEMORY_BUDGET } from "../../viz/core/budget";
import { boxInFrustum, frustumPlanes } from "../../viz/core/cull";
import { VizError } from "../../viz/error";
import { LoadQueue } from "../../viz/load/loader";
import { RasterLayerView, type RasterStyle } from "../../viz/rasters/layer";
import { rasterPlacement, screenExtent, textureLevel, type RasterPlacement } from "../../viz/rasters/placement";
import type { RasterInfo } from "./objects";
import { sceneUi } from "./sceneHost";

/** The share of the video memory budget textures of rasters may take (ADR 0004 §3). */
const RASTERS_SHARE = 0.25;
const SMALL = 1;
const LARGE = 0;

export interface RastersInput {
  rasters: () => readonly RasterInfo[];
  /** hidden keys in force (`rs:<id>`) */
  hidden: () => readonly string[];
  /** the level of a raster without an elevation: under the lowest object */
  floor: () => number;
}

interface Tex {
  texture: THREE.Texture;
  bitmap: ImageBitmap;
  sha: string;
  bytes: number;
}

interface RasterState {
  id: number;
  info: RasterInfo;
  placement: RasterPlacement;
  view: RasterLayerView;
  /** the longer side of copy 0 and copy 1, px */
  sizes: number[];
  want: number | null;
  inView: boolean;
  textures: Map<number, Tex>;
  loading: Set<number>;
  failed: Set<number>;
}

export const rasterKey = (id: number) => `rs:${id}`;
const role = (level: number) => `display${level}`;

function styleOf(r: RasterInfo, visible: boolean, level: number | null): RasterStyle {
  const mime = r.displays.find((d) => d.level === (level ?? SMALL))?.mime;
  return { opacity: Math.min(1, Math.max(0.05, Number.isFinite(r.opacity) ? r.opacity : 1)), visible, vertical: r.vertical === true, alpha: mime === "image/png" };
}

export class RastersSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private memory: GpuMemory;
  private readonly items = new Map<number, RasterState>();
  private readonly off: (() => void)[] = [];
  private readonly background = (out: THREE.Color) => this.scene.renderer.three.getClearColor(out);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = 0;
  private generation = 0;
  private lastFrame = 0;
  private over = false;
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: RastersInput,
  ) {
    this.memory = new GpuMemory(this.memoryLimit());
    this.off.push(scene.onFrame((ctx) => this.frame(ctx)));
  }

  get idle(): boolean {
    return this.inflight === 0 && this.timer === null;
  }

  /** Rasters in the scene with the copy each shows (null — the image is not there yet) and the backdrops. */
  stats(): { count: number; levels: Record<string, number | null>; backdrops: number[]; over: boolean } {
    const levels: Record<string, number | null> = {};
    const backdrops: number[] = [];
    for (const st of this.items.values()) {
      levels[st.id] = this.shownLevel(st);
      if (st.view.visible && st.view.isBackdrop) backdrops.push(st.id);
    }
    return { count: this.items.size, levels, backdrops, over: this.over };
  }

  /** The list of rasters, their placement or opacity, the hidden keys or the floor changed. */
  sync(): void {
    if (this.disposed) return;
    const list = this.input.rasters();
    const hidden = new Set(this.input.hidden());
    const floor = this.input.floor();
    const ids = new Set(list.map((r) => r.id));
    let changed = false;
    for (const id of [...this.items.keys()]) {
      if (ids.has(id)) continue;
      this.drop(id);
      changed = true;
    }
    for (const r of list) {
      const visible = !hidden.has(rasterKey(r.id));
      const p = rasterPlacement({ corners3d: r.georef?.corners3d, elevation: r.elevation, vertical: r.vertical }, floor);
      const st = this.items.get(r.id);
      if (!p) {
        // no georeference: nothing to show until the raster is placed (ТЗ 7.19 «На текущий разрез»)
        if (st) this.drop(r.id);
        changed = changed || st !== undefined;
        continue;
      }
      if (!st) {
        if (!visible) continue; // a hidden raster that was never shown is not loaded
        const view = new RasterLayerView(r.id, p, { clip: this.scene.clipPlanes, background: this.background }, styleOf(r, visible, null));
        const size = (level: number) => {
          const d = r.displays.find((x) => x.level === level);
          return d ? Math.max(d.width, d.height) : 0;
        };
        this.items.set(r.id, { id: r.id, info: r, placement: p, view, sizes: [size(LARGE), size(SMALL)], want: null, inView: true, textures: new Map(), loading: new Set(), failed: new Set() });
        this.scene.addLayer(rasterKey(r.id), view);
        continue;
      }
      st.info = r;
      if (st.view.visible !== visible) changed = true;
      st.view.setStyle(styleOf(r, visible, this.shownLevel(st)));
      if (p.corners.some((v, i) => v !== st.placement.corners[i])) {
        st.placement = p;
        st.view.setPlacement(p);
        changed = true;
      }
      // another image under the same id (never the case for a re-import, but cheap to be sure of)
      for (const [level, t] of [...st.textures]) if (r.images[role(level)] !== t.sha) this.dropTexture(st, level);
    }
    if (changed) this.scene.layerChanged();
    this.scene.invalidate();
    this.publish();
  }

  /** After a lost context: the scene dropped the layers and the textures are gone. */
  restore(): void {
    this.generation++;
    this.queue.cancelAll();
    for (const st of this.items.values()) for (const level of [...st.textures.keys()]) this.dropTexture(st, level);
    this.items.clear();
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
    for (const id of [...this.items.keys()]) this.drop(id, false);
  }

  // ---------------------------------------------------------------- internals

  private memoryLimit(): number {
    return MEMORY_BUDGET[this.scene.gpu.gpuClass] * this.scene.renderer.budgetFactor() * RASTERS_SHARE;
  }

  private shownLevel(st: RasterState): number | null {
    for (const [level, t] of st.textures) if (t.texture === st.view.texture) return level;
    return null;
  }

  private drop(id: number, update = true): void {
    const st = this.items.get(id);
    if (!st) return;
    for (const level of [...st.textures.keys()]) this.dropTexture(st, level);
    this.items.delete(id);
    this.scene.removeLayer(rasterKey(id), update);
  }

  private dropTexture(st: RasterState, level: number): void {
    const t = st.textures.get(level);
    if (!t) return;
    if (st.view.texture === t.texture) st.view.setTexture(null);
    t.texture.dispose();
    t.bitmap.close();
    st.textures.delete(level);
    this.memory.remove(String(st.id), level);
  }

  /** Before every frame: the copy each raster needs at its size on the screen, what to show and what to load. */
  private frame(ctx: FrameContext): void {
    const origin = this.scene.origin.value;
    if (!origin || !this.items.size) return;
    const planes = frustumPlanes(ctx.camera, ctx.options);
    const now = performance.now();
    this.lastFrame = now;
    const inView: string[] = [];
    let work = false;
    for (const st of this.items.values()) {
      if (!st.view.visible) continue;
      st.inView = boxInFrustum(scaleZ(boxFromBbox6(st.placement.bbox, origin), ctx.vexag), planes);
      if (st.inView) {
        inView.push(String(st.id));
        st.want = textureLevel(screenExtent(st.placement.local, this.scene.origin.offset(st.view.origin), ctx.vexag, ctx.pixelsPerUnit), st.sizes, st.want);
      }
      const want = st.want ?? SMALL;
      const show = st.textures.has(want) ? want : st.textures.has(SMALL) ? SMALL : st.textures.has(LARGE) ? LARGE : null;
      if (show !== null) {
        const t = st.textures.get(show)!;
        if (st.view.texture !== t.texture) {
          st.view.setTexture(t.texture);
          st.view.setStyle(styleOf(st.info, true, show));
        }
        this.memory.touch(String(st.id), show, now);
      }
      if (this.next(st) !== null) work = true;
    }
    this.memory.setVisible(inView);
    if (work) this.schedule();
  }

  /** The copy to load now: the small one first, then the large one when it is wanted and the raster is in view. */
  private next(st: RasterState): number | null {
    const can = (level: number) => st.info.images[role(level)] !== undefined && !st.textures.has(level) && !st.loading.has(level) && !st.failed.has(level);
    const hasSmall = st.info.images[role(SMALL)] !== undefined;
    if (hasSmall && !st.textures.has(SMALL)) return can(SMALL) ? SMALL : null;
    if ((st.want === LARGE || !hasSmall) && st.inView && can(LARGE)) return LARGE;
    return null;
  }

  private schedule(): void {
    if (this.timer || this.disposed) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      for (const st of this.items.values()) {
        if (!st.view.visible) continue;
        const level = this.next(st);
        if (level !== null) void this.load(st, level);
      }
      this.publish();
    }, 0);
  }

  private async load(st: RasterState, level: number): Promise<void> {
    const gen = this.generation;
    const sha = st.info.images[role(level)];
    const stale = () => gen !== this.generation || this.disposed || this.items.get(st.id) !== st || st.info.images[role(level)] !== sha;
    st.loading.add(level);
    this.inflight++;
    this.publish();
    try {
      const t = coreTarget();
      if (!t) throw new VizError("core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.", 0);
      const r = await this.queue.load({ url: apiUrl(t, `/rasters/${st.id}/image?level=${level}`), priority: level === SMALL ? -1 : 0, revalidate: false });
      if (stale() || !r.buffer) return;
      if (typeof createImageBitmap !== "function") throw new VizError("no_image_decoder", "Изображение растра нечем разобрать.", "Обновите программу.");
      const mime = st.info.displays.find((d) => d.level === level)?.mime ?? "image/jpeg";
      let bitmap: ImageBitmap;
      try {
        bitmap = await createImageBitmap(new Blob([r.buffer], { type: mime }));
      } catch {
        throw new VizError("raster_image_broken", `Изображение растра «${st.info.name}» не читается.`, "Загрузите растр заново.");
      }
      if (stale()) {
        bitmap.close();
        return;
      }
      const bytes = Math.round(bitmap.width * bitmap.height * 4 * 1.34); // RGBA with mipmaps
      this.memory.limit = this.memoryLimit();
      const room = this.memory.makeRoom(bytes, this.lastFrame);
      for (const e of room.evicted) {
        const owner = this.items.get(Number(e.part));
        if (owner) this.dropTexture(owner, e.level);
      }
      // no room for the large copy: the small one stays; the small one is always taken
      if (!room.ok && level === LARGE && st.textures.size) {
        bitmap.close();
        st.failed.add(level);
        this.over = true;
        return;
      }
      // an ImageBitmap is uploaded as it is (row 0 at the top): the UVs of the quad are laid out for that
      const texture = new THREE.Texture(bitmap);
      texture.flipY = false;
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = this.scene.renderer.three.capabilities.getMaxAnisotropy();
      texture.needsUpdate = true;
      st.textures.set(level, { texture, bitmap, sha, bytes });
      this.memory.add(String(st.id), level, bytes, level === SMALL, performance.now());
      sceneUi.set({ error: null });
      this.scene.invalidate();
    } catch (e) {
      if (!(e instanceof VizError && e.code === "cancelled") && !this.disposed) {
        if (!stale()) st.failed.add(level);
        sceneUi.set({ error: `Растр не загружен: ${e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e)}` });
      }
    } finally {
      st.loading.delete(level);
      this.inflight--;
      this.publish();
    }
  }

  private publish(): void {
    if (this.disposed) return;
    const ui = sceneUi.get();
    if (ui.rastersLoading !== this.inflight || ui.rasters !== this.items.size || ui.rastersOver !== this.over) sceneUi.set({ rastersLoading: this.inflight, rasters: this.items.size, rastersOver: this.over });
  }
}
