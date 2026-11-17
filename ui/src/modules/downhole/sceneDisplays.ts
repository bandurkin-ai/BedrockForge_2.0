/**
 * The displays along holes in the 3D scene (ТЗ 7.16, ADR 0007 §1, §7): for every shown display
 * (`displayList.ts`) the intervals of the parts in view are loaded — only the fields the displays use, one
 * request per part and table, `If-None-Match` — and built part by part on the facade `viz/Scene`
 * (`addLayer`, shared clipping planes, labels with everyone else's). Paths are the scene's own (the hole
 * traces of the part, float32 from its origin + float64 depths). Three levels of detail per part — full,
 * merged to 4 m, to 32 m — by pixels per metre, «упрощено» when coarser; colours and ranges always come from
 * the full rows loaded. Video memory is accounted in GpuMemory (eviction of parts out of view first).
 * A lazy chunk with three.js; after a lost context everything is fetched again.
 */
import type { App } from "../../shell/app";
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { FrameContext, Scene } from "../../viz/Scene";
import { boxCenter, boxFromBbox6, scaleZ } from "../../viz/core/bounds";
import { GpuMemory, MEMORY_BUDGET } from "../../viz/core/budget";
import { boxInFrustum, frustumPlanes, screenPriority } from "../../viz/core/cull";
import { buildDownhole } from "../../viz/downhole/build";
import type { DownholeBuild, LegendEntry } from "../../viz/downhole/geometry";
import { DownholeMesh, PANEL_MIN_PX, sideUniforms, updateSide } from "../../viz/downhole/layer";
import { displayLevel, LOD_STEPS, mergeData } from "../../viz/downhole/lod";
import { readIntervals, type PartIntervals } from "../../viz/downhole/parts";
import type { DownholeData, TrajectoryHole } from "../../viz/downhole/types";
import { VizError } from "../../viz/error";
import type { HolesManifest, HolesPart } from "../../viz/holes/manifest";
import { MarkerTextures } from "../../viz/holes/markers";
import { LabelCache } from "../../viz/labels/sprites";
import { LoadQueue } from "../../viz/load/loader";
import type { BuiltPaths } from "../../viz/worker/paths";
import { viewStore } from "../scene/bind";
import { sceneUi } from "../scene/sceneHost";
import { fieldsOfSets, panelWidthOf, shownDisplays, type ShownDisplay } from "./displayList";
import { displaysUi, type LegendDisplay } from "./displaysUi";
import { draftList, draftStore } from "./drafts";
import { allStyles, libraryStore } from "./library";

/** Building stops after this many ms and goes on in the next task (the window stays responsive). */
const SLICE_MS = 30;

interface PartData {
  traces: BuiltPaths;
  trajs: TrajectoryHole[];
  holes: DownholeData["holes"];
}

interface SetState {
  setId: number;
  manifest: HolesManifest;
  fields: Record<string, string[]>;
  fieldsKey: string;
  parts: Map<number, PartData>;
  loading: Set<number>;
  failed: Set<number>;
  version: number;
  tables: DownholeData["tables"];
  panel: number;
  scaleData: Map<string, DownholeData>;
}

interface Counts {
  sig: string;
  legend: LegendEntry[];
  holes: number;
  intervals: number;
}

interface Built {
  sig: string;
  level: number;
  layerKey: string;
  mesh: DownholeMesh;
}

interface DisplayState {
  shown: ShownDisplay;
  sig: string;
  scale: { value: number };
  built: Map<number, Built>;
  counts: Map<number, Counts>;
}

const partId = (setId: number, k: number) => `${setId}:${k}`;

/** Paths of a part as the model wants them: absolute float64 [x, y, z, md] per slot. */
function trajectories(m: HolesManifest, part: HolesPart, b: BuiltPaths): TrajectoryHole[] {
  const [ox, oy, oz] = part.origin;
  const idx = m.byPart[part.k];
  return Array.from(idx, (i, s) => {
    const path: number[][] = [];
    for (let p = b.offsets[s]; p < b.offsets[s + 1]; p++) path.push([ox + b.xyz[p * 3], oy + b.xyz[p * 3 + 1], oz + b.xyz[p * 3 + 2], b.md[p]]);
    return { hole_id: m.names[i], td: Number.isNaN(m.td[i]) ? null : m.td[i], source: "survey" as const, path };
  });
}

function holeFilter(d: ShownDisplay): ((name: string) => boolean) | null {
  if (d.settings.allHoles === true) return null;
  const set = new Set(d.settings.holes.map((h) => h.trim().toUpperCase()));
  return (name) => set.has(name.trim().toUpperCase());
}

export class DisplaysSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private readonly side = sideUniforms();
  private readonly labels: LabelCache;
  private readonly markers = new MarkerTextures();
  private memory: GpuMemory;
  private readonly sets = new Map<number, SetState>();
  private readonly displays = new Map<string, DisplayState>();
  private readonly inView = new Set<string>();
  private readonly levels = new Map<string, number>();
  private readonly priority = new Map<string, number>();
  private readonly off: (() => void)[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = 0;
  private pending = true;
  /** time of the last frame: what was drawn then is not evicted */
  private lastFrame = 0;
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly app: App,
  ) {
    this.labels = new LabelCache(scene.clipPlanes);
    this.memory = new GpuMemory(this.memoryLimit());
    const refresh = () => this.refresh();
    this.off.push(viewStore.subscribe(refresh), libraryStore.subscribe(refresh), draftStore.subscribe(refresh));
    let hidden = app.store.get().view.hidden;
    this.off.push(
      app.store.subscribe(() => {
        const h = app.store.get().view.hidden;
        if (h === hidden) return;
        hidden = h;
        this.refresh();
      }),
    );
    this.off.push(scene.onFrame((ctx) => this.frame(ctx)));
    this.off.push(scene.onRestored(() => this.restore()));
    this.refresh();
  }

  /** Nothing loading or waiting to be built for the parts in view. */
  get idle(): boolean {
    return this.inflight === 0 && !this.pending && this.timer === null;
  }

  dispose(): void {
    this.disposed = true;
    this.off.forEach((fn) => fn());
    if (this.timer) clearTimeout(this.timer);
    this.queue.cancelAll();
    for (const d of this.displays.values()) this.dropDisplay(d);
    this.labels.dispose();
    this.markers.dispose();
    displaysUi.set({ legend: [], simplified: 0, loading: 0, over: false });
  }

  private memoryLimit(): number {
    return MEMORY_BUDGET[this.scene.gpu.gpuClass] * this.scene.renderer.budgetFactor();
  }

  /** The list of displays, their settings and the fields to load changed. */
  private refresh(): void {
    if (this.disposed) return;
    const shown = shownDisplays({
      displays: viewStore.get().displays,
      charts: libraryStore.get().charts,
      drafts: draftList(),
      styles: allStyles(),
      hidden: this.app.store.get().view.hidden,
    });
    const keys = new Set(shown.map((d) => d.key));
    for (const [key, d] of this.displays) {
      if (keys.has(key)) continue;
      this.dropDisplay(d);
      this.displays.delete(key);
    }
    for (const s of shown) {
      const sig = JSON.stringify([s.settings, s.start]);
      const d = this.displays.get(s.key);
      if (d) {
        d.shown = s;
        d.sig = sig;
      } else {
        this.displays.set(s.key, { shown: s, sig, scale: { value: 1 }, built: new Map(), counts: new Map() });
      }
    }
    const fields = fieldsOfSets(shown);
    for (const id of [...this.sets.keys()]) if (!fields.has(id)) this.sets.delete(id);
    for (const [setId, f] of fields) {
      const key = JSON.stringify(f);
      const st = this.sets.get(setId);
      if (st && st.fieldsKey === key) continue;
      if (st) this.sets.delete(setId); // other fields: the parts load again
      this.ensureSet(setId, f, key);
    }
    // a frame finds the parts in view of the new sets and their levels, then the pump loads them
    this.scene.invalidate();
    this.schedule();
  }

  private ensureSet(setId: number, fields: Record<string, string[]>, key: string): SetState | null {
    const manifest = this.scene.holes(setId)?.manifest;
    if (!manifest) return null;
    const st: SetState = {
      setId, manifest, fields, fieldsKey: key, parts: new Map(), loading: new Set(), failed: new Set(), version: 0, tables: [],
      panel: panelWidthOf(manifest.length), scaleData: new Map(),
    };
    this.sets.set(setId, st);
    return st;
  }

  /** Before every frame: parts in view, their level of detail, the side of the panels, the memory account. */
  private frame(ctx: FrameContext): void {
    updateSide(this.side, ctx);
    const origin = this.scene.origin.value;
    if (!origin) return;
    const planes = frustumPlanes(ctx.camera, ctx.options);
    this.inView.clear();
    let shown = 0;
    let coarse = 0;
    let work = false;
    for (const st of this.sets.values()) {
      const m = this.scene.holes(st.setId)?.manifest;
      if (m && m !== st.manifest) {
        work = true; // the set changed: its parts load again
        continue;
      }
      for (const part of st.manifest.parts) {
        const id = partId(st.setId, part.k);
        const box = scaleZ(boxFromBbox6(part.bbox, origin), ctx.vexag);
        if (!boxInFrustum(box, planes)) continue;
        this.inView.add(id);
        this.priority.set(id, screenPriority(box, ctx.camera, ctx.options, planes));
        const level = displayLevel(ctx.pixelsPerUnit(boxCenter(box)), this.levels.get(id) ?? null);
        if (this.levels.get(id) !== level) work = true;
        this.levels.set(id, level);
        if (!st.parts.has(part.k) && !st.loading.has(part.k) && !st.failed.has(part.k) && m?.parts[part.k] && this.scene.holes(st.setId)?.hasPaths(part.k)) work = true;
        shown++;
        if (level > 0 && st.parts.has(part.k)) coarse++;
      }
    }
    for (const d of this.displays.values()) {
      const st = this.sets.get(d.shown.setId);
      const panel = d.shown.settings.panel && d.shown.settings.panel > 0 ? d.shown.settings.panel : (st?.panel ?? 10);
      d.scale.value = Math.max(1, PANEL_MIN_PX / Math.max(panel * ctx.pixelsPerUnit(ctx.camera.target), 1e-9));
      for (const [k, b] of d.built) {
        const id = partId(d.shown.setId, k);
        if (this.inView.has(id) && b.level !== this.levels.get(id)) work = true;
      }
    }
    const now = performance.now();
    this.memory.setVisible([...this.inView]);
    this.lastFrame = now;
    for (const d of this.displays.values()) for (const [k] of d.built) if (this.inView.has(partId(d.shown.setId, k))) this.memory.touch(`${d.shown.key}#${k}`, 0, now);
    const simplified = shown ? coarse / shown : 0;
    if (Math.abs(displaysUi.get().simplified - simplified) > 1e-9) queueMicrotask(() => displaysUi.set({ simplified }));
    if (work || [...this.sets.keys()].length < new Set([...this.displays.values()].map((d) => d.shown.setId)).size) this.schedule();
  }

  private schedule(): void {
    this.pending = true;
    if (this.timer || this.disposed) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.pump();
    }, 0);
  }

  /** Loads what is in view and builds what is stale, a slice at a time. */
  private pump(): void {
    if (this.disposed) return;
    const t0 = performance.now();
    // sets whose manifest came or changed
    for (const d of this.displays.values()) {
      const st = this.sets.get(d.shown.setId);
      const m = this.scene.holes(d.shown.setId)?.manifest;
      if (st && m && st.manifest !== m) {
        for (const x of this.displays.values()) if (x.shown.setId === st.setId) this.dropDisplay(x);
        this.sets.delete(st.setId);
      }
      if (!this.sets.has(d.shown.setId)) {
        const f = fieldsOfSets([...this.displays.values()].map((x) => x.shown)).get(d.shown.setId) ?? {};
        if (this.ensureSet(d.shown.setId, f, JSON.stringify(f))) this.scene.invalidate();
      }
    }
    // loads: parts in view, nearer the centre of the screen first
    for (const st of this.sets.values()) {
      const parts = st.manifest.parts.filter((p) => this.inView.has(partId(st.setId, p.k)) && !st.parts.has(p.k) && !st.loading.has(p.k) && !st.failed.has(p.k));
      parts.sort((a, b) => (this.priority.get(partId(st.setId, a.k)) ?? 0) - (this.priority.get(partId(st.setId, b.k)) ?? 0));
      for (const p of parts) {
        const traces = this.scene.holes(st.setId)?.builtPaths(p.k);
        if (traces) void this.load(st, p, traces);
      }
    }
    // builds
    let more = false;
    let changed = false;
    for (const d of this.displays.values()) {
      const st = this.sets.get(d.shown.setId);
      if (!st) continue;
      for (const [k, b] of [...d.built]) {
        const id = partId(st.setId, k);
        if (!this.inView.has(id) && (b.sig !== this.sigOf(d, st, b.level) || !st.parts.has(k))) {
          this.removeBuilt(d, k);
          changed = true;
        }
      }
      for (const [k] of st.parts) {
        const id = partId(st.setId, k);
        if (!this.inView.has(id)) continue;
        const level = this.levels.get(id) ?? 0;
        if (d.built.get(k)?.sig === this.sigOf(d, st, level)) continue;
        if (performance.now() - t0 > SLICE_MS) {
          more = true;
          break;
        }
        this.buildPart(d, st, k, level);
        changed = true;
      }
    }
    this.publish();
    if (changed) this.scene.invalidate();
    this.pending = more;
    if (more) this.schedule();
  }

  private sigOf(d: DisplayState, st: SetState, level: number): string {
    return `${d.sig}|${st.fieldsKey}|${st.version}|${level}`;
  }

  private async load(st: SetState, part: HolesPart, traces: BuiltPaths): Promise<void> {
    const t = coreTarget();
    if (!t) return;
    st.loading.add(part.k);
    this.inflight++;
    this.publishLoading();
    try {
      const got: PartIntervals[] = [];
      const tables = Object.entries(st.fields);
      for (const [table, fields] of tables) {
        const q = `?table=${encodeURIComponent(table)}&fields=${fields.map(encodeURIComponent).join(",")}&method=${encodeURIComponent(st.manifest.method)}`;
        const r = await this.queue.load({ url: apiUrl(t, `/viz/sets/${st.setId}/intervals/${part.k}${q}`), priority: this.priority.get(partId(st.setId, part.k)) ?? 0, revalidate: false });
        if (!r.buffer) return;
        got.push(readIntervals(r.buffer, part.n));
      }
      if (this.disposed || this.sets.get(st.setId) !== st) return;
      const m = st.manifest;
      const idx = m.byPart[part.k];
      const holes: DownholeData["holes"] = Array.from(idx, (i) => {
        const td = Number.isNaN(m.td[i]) ? null : m.td[i];
        return { hole_id: m.names[i], found: true, td, max_depth: td ?? 0, tables: {} };
      });
      tables.forEach(([table], j) => {
        const p = got[j];
        if (!st.tables.some((x) => x.key === table)) st.tables.push({ key: table, name: p.table.name, columns: p.fields, kind: p.table.kind === "event" ? "event" : "interval" });
        p.rows.forEach((rows, s) => {
          const h = holes[s];
          if (!h) return;
          h.tables[table] = rows;
          for (const r of rows) if (r.to !== null && r.to > h.max_depth) h.max_depth = r.to;
        });
      });
      st.parts.set(part.k, { traces, trajs: trajectories(m, part, traces), holes });
      st.version++;
      st.scaleData.clear();
    } catch (e) {
      if (!(e instanceof VizError && e.code === "cancelled") && !this.disposed) {
        st.failed.add(part.k);
        sceneUi.set({ error: `Отображения по стволу не загружены: ${e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e)}` });
      }
    } finally {
      st.loading.delete(part.k);
      this.inflight--;
      this.publishLoading();
      this.schedule();
    }
  }

  /** The full rows of every loaded part of the set (of the display's holes): scales, categories, ranges. */
  private scaleData(st: SetState, d: DisplayState): DownholeData {
    const filter = holeFilter(d.shown);
    const key = filter ? JSON.stringify(d.shown.settings.holes) : "*";
    let data = st.scaleData.get(key);
    if (!data) {
      const holes: DownholeData["holes"] = [];
      for (const p of st.parts.values()) for (const h of p.holes) if (!filter || filter(h.hole_id)) holes.push(h);
      data = { tables: st.tables, holes };
      st.scaleData.set(key, data);
    }
    return data;
  }

  private build(d: DisplayState, st: SetState, k: number, level: number): DownholeBuild {
    const p = st.parts.get(k)!;
    const filter = holeFilter(d.shown);
    const part = st.manifest.parts[k];
    let data: DownholeData = { tables: st.tables, holes: filter ? p.holes.filter((h) => filter(h.hole_id)) : p.holes };
    if (level > 0) data = mergeData(data, LOD_STEPS[level]);
    const s = d.shown.settings;
    const settings = s.panel && s.panel > 0 ? s : { ...s, panel: st.panel };
    return buildDownhole(p.trajs, data, settings, part.origin, d.shown.start, this.scaleData(st, d));
  }

  private buildPart(d: DisplayState, st: SetState, k: number, level: number): void {
    const part = st.manifest.parts[k];
    const build = this.build(d, st, k, level);
    const sig0 = this.sigOf(d, st, 0);
    if (level === 0) d.counts.set(k, { sig: sig0, legend: build.legend, holes: build.holes, intervals: build.intervals });
    else if (d.counts.get(k)?.sig !== sig0) {
      const full = this.build(d, st, k, 0); // the legend counts the rows, not the merged spans
      d.counts.set(k, { sig: sig0, legend: full.legend, holes: full.holes, intervals: full.intervals });
    }
    const mesh = new DownholeMesh(build, { clip: this.scene.clipPlanes, resolution: this.scene.resolution, side: this.side, labels: this.labels, markers: this.markers, pixelRatio: this.scene.renderer.pixelRatio }, d.scale);
    const id = `${d.shown.key}#${k}`;
    this.memory.limit = this.memoryLimit();
    this.memory.remove(id, 0);
    const room = this.memory.makeRoom(mesh.bytes, this.lastFrame);
    for (const r of room.evicted) {
      const [key, kk] = r.part.split("#");
      const owner = this.displays.get(key);
      if (owner) this.removeBuilt(owner, Number(kk));
    }
    if (!room.ok) {
      mesh.dispose();
      this.removeBuilt(d, k);
      displaysUi.set({ over: true });
      return;
    }
    this.memory.add(id, 0, mesh.bytes, false, performance.now());
    const layerKey = `dh:${d.shown.key}:${k}`;
    const origin = { x: part.origin[0], y: part.origin[1], z: part.origin[2] };
    this.scene.addLayer(layerKey, {
      object: mesh.object,
      origin,
      bbox: null,
      labels: () => mesh.labels(),
      beforeRender: (ctx) => mesh.frame(ctx, this.scene.origin.offset(origin), this.side),
      dispose: () => mesh.dispose(),
    });
    d.built.set(k, { sig: this.sigOf(d, st, level), level, layerKey, mesh });
  }

  private removeBuilt(d: DisplayState, k: number): void {
    const b = d.built.get(k);
    if (!b) return;
    this.scene.removeLayer(b.layerKey);
    this.memory.remove(`${d.shown.key}#${k}`, 0);
    d.built.delete(k);
  }

  private dropDisplay(d: DisplayState): void {
    for (const k of [...d.built.keys()]) this.removeBuilt(d, k);
    d.counts.clear();
  }

  /** After a lost context: the scene dropped the layers; the data comes again from the core. */
  private restore(): void {
    for (const d of this.displays.values()) {
      d.built.clear();
      d.counts.clear();
    }
    this.sets.clear();
    this.memory = new GpuMemory(this.memoryLimit());
    this.refresh();
  }

  private publishLoading(): void {
    if (displaysUi.get().loading !== this.inflight) displaysUi.set({ loading: this.inflight });
  }

  /** The legend: the layers of every display with the intervals of the loaded parts in each class. */
  private publish(): void {
    const legend: LegendDisplay[] = [];
    for (const d of this.displays.values()) {
      const st = this.sets.get(d.shown.setId);
      if (!st) continue;
      const sig0 = this.sigOf(d, st, 0);
      let entries: LegendEntry[] | null = null;
      let holes = 0;
      let intervals = 0;
      for (const c of d.counts.values()) {
        if (c.sig !== sig0) continue;
        holes += c.holes;
        intervals += c.intervals;
        if (!entries) entries = c.legend.map((e) => ({ ...e, counts: [...e.counts] }));
        else for (const e of c.legend) entries.find((x) => x.trackId === e.trackId)?.counts.forEach((_, i, arr) => (arr[i] += e.counts[i] ?? 0));
      }
      if (entries) legend.push({ key: d.shown.key, chartId: d.shown.chartId, draftKey: d.shown.draftKey, name: d.shown.name, setId: d.shown.setId, holes, intervals, entries });
    }
    if (JSON.stringify(legend) !== JSON.stringify(displaysUi.get().legend)) displaysUi.set({ legend });
  }
}
