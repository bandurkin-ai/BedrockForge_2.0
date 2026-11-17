/**
 * Facade of the 3D window (ADR 0004, ADR 0007 §7): renderer, camera, frames on demand, the scene origin, the
 * shared clipping planes, the holes, the grid, the section, the gizmo. A lazy chunk (no three.js in the main
 * bundle). Spaces: absolute (float64) → scene (minus the integer scene origin, float64 on the CPU) → rendered
 * (the root scaled in Z by the exaggeration); buffers are float32 from the integer origin of their part.
 * Other layers join through `addLayer` (own integer origin, materials with `scene.clipPlanes`, `beforeRender`,
 * labels placed with everyone else's); after a lost context they are dropped and `onRestored` re-adds them.
 */
import * as THREE from "three";
import { attachPointer } from "./core/pointer";
import { CameraRig } from "./core/camera";
import { FrameLoop } from "./core/frame";
import { nonBackgroundPixels, probeWebGL2, Renderer, sceneBytes, type FrameStats, type GpuInfo } from "./core/renderer";
import { FpsTracker } from "./core/stats";
import { boxFromBbox6, contentBox, fitBox, scaleZ, union, windowTest, type Box3, type Extent } from "./core/bounds";
import { screenPriority } from "./core/cull";
import { rebaseCamera, SceneOrigin } from "./core/sceneOrigin";
import { TurnController } from "./core/turn";
import { changeVexag, fit, fitDepth, pixelsPerUnit, screenRay, toPlan, view, viewDirection, type CameraState, type ViewOptions, type ViewPreset } from "./core/views";
import { fromWorld, toWorld, type WorldCamera } from "./core/worldCamera";
import type { Vec3 } from "./core/vec3";
import { AxesGizmo } from "./gizmo/AxesGizmo";
import { Grid } from "./grid";
import { HoleSetLayer, type HoleSetStyle, type HoleSetVisibility } from "./holes/holeSet";
import { holeIndex, type HolesManifest } from "./holes/manifest";
import { MarkerTextures } from "./holes/markers";
import { useFatLines } from "./holes/paths";
import { pickHoles, SelectedHole } from "./holes/select";
import { LabelCache, placeLabels, type LabelItem } from "./labels/sprites";
import { projectToScreen, toNdc, viewProjection, type Mat4 } from "./pick/ray";
import { SharedClip } from "./section/clip";
import { DRAW_OFF, type DrawState } from "./section/draw";
import { viewPlanePoint, type SectionLine } from "./section/math";
import { buildOverview, type PlanOverview } from "./section/miniPlan";
import { collarsOnScreen, planSources, type ScreenCollar } from "./holes/screen";
import { SectionOverlay } from "./section/overlay";
import type { BuiltPaths } from "./worker/paths";
import type { FrameContext, SceneDebug, SceneEvents, SceneLayer } from "./sceneTypes";

export { probeWebGL2 } from "./core/renderer";
export type { HoleHit } from "./holes/select";
export type { FrameContext, SceneDebug, SceneEvents, SceneLayer } from "./sceneTypes";

interface Held {
  layer: SceneLayer;
  holder: THREE.Group;
}

export class Scene {
  readonly clipPlanes: THREE.Plane[];
  readonly origin = new SceneOrigin();
  readonly renderer: Renderer;
  private readonly rig: CameraRig;
  private readonly loop: FrameLoop;
  private readonly three = new THREE.Scene();
  private readonly root = new THREE.Group();
  private readonly clip = new SharedClip();
  /** the size of the view in px: the `resolution` of every LineMaterial (shared, updated on resize) */
  readonly resolution = new THREE.Vector2(1, 1);
  private textures = new MarkerTextures();
  private labels: LabelCache;
  private grid: Grid;
  private overlay = new SectionOverlay();
  private readonly gizmo: AxesGizmo;
  private readonly turn = new TurnController();
  private readonly holeSets = new Map<number, HoleSetLayer>();
  private readonly layers = new Map<string, Held>();
  private readonly selection: SelectedHole;
  private selected: { setId: number; name: string } | null = null;
  private section: SectionLine | null = null;
  private clipOn = false;
  private vexag = 1;
  private gridOn = true;
  private fitted = false;
  private fat = true;
  private drawing: DrawState = DRAW_OFF;
  private size = { w: 1, h: 1 };
  private lastCam: CameraState | null = null;
  private lastVp: Mat4 | null = null;
  /** time of the last pick on the CPU, ms (goal ≤ 100 ms with the selection) */
  private pickMs = 0;
  private stats: FrameStats = { calls: 0, triangles: 0, points: 0, lines: 0, geometries: 0, textures: 0 };
  private readonly restoredListeners = new Set<() => void>();
  private readonly frameListeners = new Set<(ctx: FrameContext) => void>();
  private readonly cleanup: (() => void)[] = [];
  private readonly light = new THREE.DirectionalLight(0xffffff, 1.6);
  /** frames per second and ms per frame (the counter Ctrl+Shift+F) */
  readonly fps = new FpsTracker();

  /** Throws `VizError("no_webgl2")` when the window gets no WebGL2 context. */
  constructor(
    private readonly host: HTMLElement,
    private readonly events: SceneEvents = {},
  ) {
    this.renderer = new Renderer(host);
    this.clipPlanes = this.clip.planes;
    this.labels = new LabelCache(this.clipPlanes);
    this.grid = new Grid(this.labels);
    this.selection = new SelectedHole(this.clipPlanes, this.resolution);
    this.rig = new CameraRig(this.renderer.canvas);
    this.loop = new FrameLoop((now) => this.draw(now));
    this.three.add(this.root, new THREE.HemisphereLight(0xffffff, 0x445566, 1.4), this.light, this.light.target);
    this.root.add(this.grid.group, this.overlay.group);
    this.gizmo = new AxesGizmo(host, (dir) => this.turnTo(dir));
    this.rig.onChange = () => {
      this.loop.invalidate();
      this.events.onCamera?.();
    };
    this.rig.onUserStart = () => this.turn.cancel();
    this.renderer.onLost = () => this.contextLost();
    this.renderer.onRestored = () => this.contextRestored();
    this.cleanup.push(
      attachPointer(this.renderer.canvas, {
        click: (x, y) => this.click(x, y),
        move: (x, y) => this.pointerMove(x, y),
        leave: () => undefined,
      }),
    );
    const ro = new ResizeObserver(() => this.resize());
    ro.observe(host);
    this.cleanup.push(() => ro.disconnect());
    const mo = new MutationObserver(() => this.applyTheme());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.cleanup.push(() => mo.disconnect());
    this.applyTheme();
    this.resize();
  }

  static probe = probeWebGL2;

  get gpu(): GpuInfo {
    return this.renderer.info;
  }

  get canvas(): HTMLCanvasElement {
    return this.renderer.canvas;
  }

  /** One more frame (a layer changed its objects). */
  invalidate(): void {
    this.loop.invalidate();
  }

  /** No frame pending and the last change drawn («кадр готов»). */
  get idle(): boolean {
    return this.loop.idle && !this.renderer.lost;
  }

  onRestored(fn: () => void): () => void {
    this.restoredListeners.add(fn);
    return () => this.restoredListeners.delete(fn);
  }

  /** Before every frame (levels of detail, parts in view): do not change the scene here — ask for a frame. */
  onFrame(fn: (ctx: FrameContext) => void): () => void {
    this.frameListeners.add(fn);
    return () => this.frameListeners.delete(fn);
  }

  // ---------------------------------------------------------------- view

  setView(v: { vexag: number; grid: boolean; perspective: boolean }): void {
    if (v.vexag !== this.vexag && v.vexag) {
      const cam = changeVexag(this.rig.state(), this.vexag, v.vexag);
      this.vexag = v.vexag;
      this.rig.vexag = v.vexag;
      this.root.scale.z = v.vexag;
      this.rig.apply(cam);
    }
    if (v.grid !== this.gridOn) {
      this.gridOn = v.grid;
      this.grid.group.visible = v.grid;
    }
    if (this.rig.setPerspective(v.perspective)) {
      this.rig.resize(this.size.w, this.size.h);
      this.fit();
    }
    this.loop.invalidate();
  }

  /** A standard view: «В план» keeps the centre and the scale, the others fit the content. */
  view(preset: ViewPreset): void {
    this.turn.cancel();
    const opts = this.rig.options();
    if (preset === "top") {
      this.rig.apply(toPlan(this.rig.state(), contentBox(this.extents()), opts));
      return;
    }
    const origin = this.origin.value;
    const frame = preset === "section" && this.section && origin ? { line: this.section, origin } : null;
    this.rig.apply(view(preset, this.fitBox(), opts, frame));
  }

  /** «Вписать»: everything visible (with clipping — in the window) without turning the view. */
  fit(): void {
    this.turn.cancel();
    this.rig.apply(fit(this.rig.state(), this.fitBox(), this.rig.options()));
  }

  /** A smooth turn to look along `from` (gizmo): centre and scale kept, a new turn stops the old. */
  turnTo(from: Vec3): void {
    const opts = this.rig.options();
    const token = this.turn.begin(this.rig.state(), from, performance.now(), { ortho: !opts.perspective, box: contentBox(this.extents()), vexag: this.vexag });
    if (token === null) return;
    this.loop.animate((now) => {
      const r = this.turn.frame(token, now);
      if (!r) return false;
      this.rig.apply(r.camera);
      return !r.done;
    });
  }

  camera(): CameraState {
    return this.rig.state();
  }

  /** The camera of a saved view, absolute (null before the first object); the projection comes with the keys. */
  worldCamera(): WorldCamera | null {
    const o = this.origin.value;
    return o ? toWorld(this.rig.state(), o, this.vexag, !this.rig.perspective, this.rig.persp.fov) : null;
  }
  setWorldCamera(c: WorldCamera): void {
    this.turn.cancel();
    this.rig.apply(fromWorld(c, this.origin.value ?? this.origin.ensure([...c.target, ...c.target]), this.vexag, this.rig.state()));
  }

  /** Turns the view by `rad` about the vertical through its centre (smoke scenario, frame benchmark). */
  orbit(rad: number): void {
    const c = this.rig.state();
    const dx = c.position[0] - c.target[0];
    const dy = c.position[1] - c.target[1];
    const [s, k] = [Math.sin(rad), Math.cos(rad)];
    this.rig.apply({ ...c, position: [c.target[0] + dx * k - dy * s, c.target[1] + dx * s + dy * k, c.position[2]] });
  }

  /** Runs `step` before every frame until it returns false (frames go on meanwhile). */
  animate(step: (now: number) => boolean): void {
    this.loop.animate(step);
  }

  /** One frame now, the GPU waited for: its ms (the frame benchmark of perf3d). */
  drawNow(): number {
    const t0 = performance.now();
    this.draw(t0);
    this.renderer.finish();
    return performance.now() - t0;
  }

  /** Estimated bytes of buffers and textures on the video card. */
  gpuBytes(): number {
    return sceneBytes(this.three);
  }

  /** Loading order of a part (smaller — sooner): parts nearer the centre of the screen first. */
  partPriority(bbox: ArrayLike<number>): number {
    const o = this.origin.value;
    if (!o) return 0;
    return screenPriority(scaleZ(boxFromBbox6(bbox, o), this.vexag), this.rig.state(), this.rig.options());
  }

  viewOptions(): ViewOptions {
    return this.rig.options();
  }

  // ---------------------------------------------------------------- section

  setSection(line: SectionLine | null, clip: boolean): void {
    this.section = line;
    this.clipOn = clip && line !== null;
    this.clip.update(line, this.clipOn, this.origin.value);
    this.overlay.setLine(line, this.clipOn, this.origin.value, contentBox(this.extents()));
    this.loop.invalidate();
  }

  /** The drawing state of a section line (the first point marked: the rubber band follows the cursor). */
  setDrawing(state: DrawState): void {
    this.drawing = state;
    if (state.stage !== "second") this.overlay.setDraft(null, null, 0);
    else this.pointerMove(-1, -1);
    this.renderer.canvas.style.cursor = state.stage === "off" ? "" : "crosshair";
    this.loop.invalidate();
  }

  /** Collars and traces of the visible sets for the mini-plan (≤ 20 000 points). */
  planOverview(): PlanOverview {
    return buildOverview(planSources(this.holeSets.values()));
  }

  // ---------------------------------------------------------------- holes

  setHoles(manifest: HolesManifest, style: HoleSetStyle, visibility: HoleSetVisibility): void {
    this.removeHoles(manifest.setId, false);
    if (!manifest.parts.length) {
      this.contentChanged();
      return;
    }
    this.origin.ensure(manifest.parts[0].bbox);
    const layer = new HoleSetLayer(manifest, { clip: this.clipPlanes, resolution: this.resolution, textures: this.textures, labels: this.labels, pixelRatio: this.renderer.pixelRatio }, style, visibility);
    layer.place(this.origin);
    this.holeSets.set(manifest.setId, layer);
    this.root.add(layer.group);
    this.applySelection();
    this.contentChanged();
  }

  setPaths(setId: number, k: number, built: BuiltPaths): void {
    const layer = this.holeSets.get(setId);
    if (!layer) return;
    layer.setPaths(k, built, this.fat);
    this.updateFat();
    if (this.selected?.setId === setId) this.applySelection();
    this.loop.invalidate();
  }

  holes(setId: number): HoleSetLayer | null {
    return this.holeSets.get(setId) ?? null;
  }

  removeHoles(setId: number, update = true): void {
    const layer = this.holeSets.get(setId);
    if (!layer) return;
    if (this.selected?.setId === setId) this.selection.clear();
    this.root.remove(layer.group);
    layer.dispose();
    this.holeSets.delete(setId);
    if (update) this.contentChanged();
  }

  setHoleStyle(setId: number, style: HoleSetStyle): void {
    const layer = this.holeSets.get(setId);
    if (!layer) return;
    layer.setStyle(style, useFatLines(style.trace, this.totalSegments()));
    if (this.selected?.setId === setId) this.applySelection();
    this.loop.invalidate();
  }

  setHoleVisibility(setId: number, v: HoleSetVisibility): void {
    const layer = this.holeSets.get(setId);
    if (!layer) return;
    layer.setVisibility(v);
    this.refreshGrid();
    this.loop.invalidate();
  }

  select(hole: { setId: number; name: string } | null): void {
    this.selected = hole;
    this.applySelection();
    this.loop.invalidate();
  }

  // ---------------------------------------------------------------- other layers

  addLayer(key: string, layer: SceneLayer): void {
    this.removeLayer(key, false);
    if (layer.bbox) this.origin.ensure(layer.bbox);
    else this.origin.ensure([layer.origin.x, layer.origin.y, layer.origin.z, layer.origin.x, layer.origin.y, layer.origin.z]);
    const holder = new THREE.Group();
    holder.add(layer.object);
    const off = this.origin.offset(layer.origin);
    holder.position.set(off[0], off[1], off[2]);
    this.root.add(holder);
    this.layers.set(key, { layer, holder });
    this.contentChanged();
  }

  removeLayer(key: string, update = true): void {
    const held = this.layers.get(key);
    if (!held) return;
    this.root.remove(held.holder);
    held.layer.dispose?.();
    this.layers.delete(key);
    if (update) this.contentChanged();
  }

  // ---------------------------------------------------------------- E2E and diagnostics

  debug(): SceneDebug {
    const cam = this.rig.state();
    return {
      frames: this.loop.frames,
      idle: this.idle,
      lost: this.renderer.lost,
      stats: this.stats,
      camera: { ...cam, dir: viewDirection(cam) },
      perspective: this.rig.perspective,
      vexag: this.vexag,
      clipPlanes: this.clipPlanes.length,
      section: this.section,
      gpu: this.renderer.info,
      sets: [...this.holeSets.keys()],
      segments: this.totalSegments(),
      pickMs: this.pickMs,
    };
  }

  /** A snapshot pass: pixels that differ from the background. */
  pixels(): { width: number; height: number; nonBackground: number } | null {
    const snap = this.renderer.snapshot(this.three, this.rig.camera);
    return snap ? { width: snap.width, height: snap.height, nonBackground: nonBackgroundPixels(snap.data) } : null;
  }

  /** Collars on the screen (client px), for the E2E: by set and name. */
  collarsOnScreen(limit = 500): ScreenCollar[] {
    if (!this.lastVp) return [];
    const r = this.renderer.canvas.getBoundingClientRect();
    return collarsOnScreen(this.holeSets.values(), this.lastVp, this.vexag, { w: this.size.w, h: this.size.h, left: r.left, top: r.top }, (x, y) => this.clip.inWindow(x, y), limit);
  }

  dispose(): void {
    this.loop.dispose();
    this.cleanup.forEach((fn) => fn());
    this.disposeContent();
    this.rig.dispose();
    this.gizmo.dispose();
    this.renderer.dispose();
  }

  // ---------------------------------------------------------------- internals

  private extents(): Extent[] {
    const o = this.origin.value;
    if (!o) return [];
    const out: Extent[] = [];
    for (const l of this.holeSets.values()) out.push({ box: l.extent(o), visible: l.visible });
    for (const { layer, holder } of this.layers.values()) if (layer.bbox) out.push({ box: boxFromBbox6(layer.bbox, o), visible: holder.visible && layer.object.visible });
    return out;
  }

  private fitBox(): Box3 {
    return fitBox(this.extents(), windowTest(this.section, this.clipOn, this.origin.value));
  }

  private totalSegments(): number {
    let n = 0;
    for (const l of this.holeSets.values()) n += l.segments;
    return n;
  }

  private updateFat(): void {
    const total = this.totalSegments();
    for (const l of this.holeSets.values()) l.setFat(useFatLines(l.style.trace, total));
  }

  private applySelection(): void {
    const s = this.selected;
    const layer = s ? this.holeSets.get(s.setId) ?? null : null;
    const index = layer && s ? holeIndex(layer.manifest, s.name) : -1;
    this.selection.update(layer, index, this.renderer.pixelRatio, layer?.style.collar.size ?? 10, this.textures.get("circle"));
  }

  private contentChanged(): void {
    this.refreshGrid();
    this.clip.update(this.section, this.clipOn, this.origin.value);
    this.overlay.setLine(this.section, this.clipOn, this.origin.value, contentBox(this.extents()));
    if (!this.fitted && this.extents().length) {
      this.fitted = true;
      this.view("iso");
    }
    this.loop.invalidate();
  }

  private refreshGrid(): void {
    const css = getComputedStyle(this.host);
    this.grid.update(contentBox(this.extents()), this.origin.value, { line: css.getPropertyValue("--text-dim").trim() || "#8a99a8", text: css.getPropertyValue("--text-faint").trim() || "#7d8d9e" });
    this.grid.group.visible = this.gridOn;
  }

  /** Part offsets, the planes and the overlay after the scene origin moved. */
  private placeAll(): void {
    for (const l of this.holeSets.values()) l.place(this.origin);
    for (const { layer, holder } of this.layers.values()) {
      const off = this.origin.offset(layer.origin);
      holder.position.set(off[0], off[1], off[2]);
    }
    this.contentChanged();
  }

  private depthBox(): Box3 | null {
    const box = contentBox(this.extents());
    if (box && this.grid.box && this.gridOn) return union(box, this.grid.box);
    return box;
  }

  private draw(now: number): void {
    if (this.renderer.lost) return;
    const t0 = performance.now();
    const opts = this.rig.options();
    let cam = this.rig.state();
    const delta = this.origin.follow(cam.target, this.vexag);
    if (delta) {
      this.rig.apply(rebaseCamera(cam, delta, this.vexag));
      this.placeAll();
      cam = this.rig.state();
    }
    cam = fitDepth(cam, this.depthBox(), opts);
    this.rig.setDepth(cam);
    this.lastCam = cam;
    const vp = viewProjection(cam, opts);
    this.lastVp = vp;
    const { w, h } = this.size;
    const ppuAt = (at: Vec3) => pixelsPerUnit(cam, at, h, opts);
    const ctx: FrameContext = { camera: cam, options: opts, width: w, height: h, vexag: this.vexag, pixelsPerUnit: ppuAt };
    const ppu = ppuAt(cam.target);
    for (const l of this.holeSets.values()) l.beforeRender(ppu);
    for (const fn of this.frameListeners) fn(ctx);
    for (const { layer } of this.layers.values()) layer.beforeRender?.(ctx);
    this.light.position.set(...cam.position);
    this.light.target.position.set(...cam.target);
    this.three.updateMatrixWorld();
    const items: LabelItem[] = [];
    for (const l of this.holeSets.values()) l.labelItems(items);
    for (const { layer, holder } of this.layers.values()) if (holder.visible && layer.labels) items.push(...layer.labels());
    // the grid seen edge-on (a side view, a section) keeps its lines, its values would be a heap
    if (Math.abs(viewDirection(cam)[2]) > 0.15) this.grid.labelItems(items);
    else for (const l of this.grid.labels) l.sprite.visible = false;
    placeLabels(items, {
      width: w,
      height: h,
      project: (p) => {
        const s = projectToScreen(vp, p.x, p.y, p.z, w, h);
        return s ? { x: s[0], y: s[1], ppu: ppuAt([p.x, p.y, p.z]) } : null;
      },
      inWindow: (p) => this.clip.inWindow(p.x, p.y),
    });
    this.renderer.render(this.three, this.rig.camera);
    this.stats = this.renderer.stats();
    this.gizmo.update(cam, this.vexag < 0);
    this.fps.record(now, performance.now() - t0);
  }

  private resize(): void {
    const w = Math.max(this.host.clientWidth, 1);
    const h = Math.max(this.host.clientHeight, 1);
    this.size = { w, h };
    this.renderer.setSize(w, h);
    this.rig.resize(w, h);
    this.resolution.set(w, h);
    this.loop.invalidate();
    this.events.onCamera?.();
  }

  private applyTheme(): void {
    const css = getComputedStyle(this.host);
    this.renderer.setClearColor(css.getPropertyValue("--scene-bg").trim() || "#11161d");
    this.refreshGrid();
    this.loop.invalidate();
  }

  /** A point of the screen on the plane through the view centre (BF pickPoint), rendered space. */
  private viewPoint(px: number, py: number): Vec3 | null {
    const cam = this.lastCam ?? this.rig.state();
    const [nx, ny] = toNdc(px, py, this.size.w, this.size.h);
    return viewPlanePoint(screenRay(cam, this.rig.options(), nx, ny), cam.target, viewDirection(cam));
  }

  private click(px: number, py: number): void {
    if (this.drawing.stage !== "off") {
      const p = this.viewPoint(px, py);
      const o = this.origin.value;
      if (p && o) this.events.onGroundClick?.({ x: p[0] + o.x, y: p[1] + o.y });
      return;
    }
    const vp = this.lastVp;
    if (!vp) return;
    const t0 = performance.now();
    const hit = pickHoles(this.holeSets.values(), vp, this.vexag, px, py, this.size.w, this.size.h, (x, y) => this.clip.inWindow(x, y));
    this.pickMs = performance.now() - t0;
    this.events.onPick?.(hit);
  }

  private pointerMove(px: number, py: number): void {
    const first = this.drawing.stage === "second" ? this.drawing.first : null;
    const o = this.origin.value;
    if (!first || !o) return;
    const cam = this.lastCam ?? this.rig.state();
    const start: Vec3 = [first.x - o.x, first.y - o.y, cam.target[2] / (this.vexag || 1)];
    const p = px >= 0 ? this.viewPoint(px, py) : null;
    const end: Vec3 | null = p ? [p[0], p[1], start[2]] : null;
    const mark = 6 / Math.max(pixelsPerUnit(cam, cam.target, this.size.h, this.rig.options()), 1e-9);
    this.overlay.setDraft(start, end, mark);
    this.loop.invalidate();
  }

  /** Drops everything with GPU resources (the context is gone or the scene closes). */
  private disposeContent(): void {
    for (const id of [...this.holeSets.keys()]) this.removeHoles(id, false);
    for (const key of [...this.layers.keys()]) this.removeLayer(key, false);
    this.selection.clear();
    this.grid.dispose();
    this.overlay.dispose();
    this.labels.dispose();
    this.textures.dispose();
  }

  private contextLost(): void {
    this.loop.pause(true);
    this.turn.cancel();
    this.disposeContent();
    this.events.onContextLost?.();
  }

  private contextRestored(): void {
    // three.js re-creates its GL state; the clear colour and the scene's own resources come back here,
    // the data of the layers — from the core, by their owners.
    this.applyTheme();
    this.loop.pause(false);
    this.events.onContextRestored?.();
    for (const fn of this.restoredListeners) fn();
  }
}
