/**
 * Holes of the project's sets for the 3D window (ADR 0007 §1): manifest → collars (the first frame) → the
 * paths of the parts, those nearer the centre of the screen first, built in the Worker. At most 4 requests
 * at once with `If-None-Match` (LoadQueue): after an edit, an import, an audit or a removal the manifest is
 * asked again and a 304 changes nothing; a removed set leaves the scene at once (17.4.7). After a lost
 * context everything is fetched again in full.
 */
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { Scene } from "../../viz/Scene";
import { VizError } from "../../viz/error";
import { normalizeCollarStyle } from "../../viz/holes/collarStyle";
import type { HoleSetStyle, HoleSetVisibility } from "../../viz/holes/holeSet";
import { partKinds, readHolesManifest, type HolesManifest, type HolesPart } from "../../viz/holes/manifest";
import { normalizeTraceStyle } from "../../viz/holes/traceStyle";
import { LoadQueue } from "../../viz/load/loader";
import { GeometryWorker } from "../../viz/worker/client";
import type { BuiltPaths } from "../../viz/worker/paths";
import { sceneUi } from "./sceneHost";

const RELOAD_MS = 200;

export function setStyleOf(keys: Record<string, unknown>, setId: number): HoleSetStyle {
  const pick = (key: string) => ((keys[key] ?? {}) as Record<string, unknown>)[String(setId)];
  return { collar: normalizeCollarStyle(pick("collar_styles")), trace: normalizeTraceStyle(pick("trace_styles")) };
}

export function setVisibilityOf(hidden: readonly string[], setId: number): HoleSetVisibility {
  const h = new Set(hidden);
  return { set: !h.has(`set:${setId}`), collars: !h.has(`set:${setId}:collars`), paths: !h.has(`set:${setId}:paths`) };
}

export interface SourceInput {
  /** sets of the project (tablesStore) */
  sets: () => { id: number }[];
  hidden: () => readonly string[];
  /** view keys of the project (styles of the sets) */
  keys: () => Record<string, unknown>;
}

export class HolesSource {
  private readonly queue: LoadQueue;
  private readonly worker = new GeometryWorker();
  private readonly manifests = new Map<number, HolesManifest>();
  private readonly built = new Map<string, { etag: string; paths: BuiltPaths }>();
  private readonly timers = new Map<number, ReturnType<typeof setTimeout>>();
  private inflight = 0;
  private generation = 0;
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: SourceInput,
  ) {
    this.queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  }

  get idle(): boolean {
    return this.inflight === 0 && this.timers.size === 0;
  }

  manifest(setId: number): HolesManifest | null {
    return this.manifests.get(setId) ?? null;
  }

  manifestList(): HolesManifest[] {
    return [...this.manifests.values()];
  }

  /** Sets added, removed, shown or hidden: the scene follows the list of the project. */
  sync(): void {
    const sets = this.input.sets();
    const ids = new Set(sets.map((s) => s.id));
    for (const id of [...this.manifests.keys()]) {
      if (ids.has(id)) continue;
      this.drop(id);
    }
    const hidden = this.input.hidden();
    for (const s of sets) {
      const vis = setVisibilityOf(hidden, s.id);
      if (this.manifests.has(s.id)) this.scene.setHoleVisibility(s.id, vis);
      else if (vis.set) this.schedule(s.id);
    }
    this.publish();
  }

  /** The styles of the sets changed in the project. */
  restyle(): void {
    const keys = this.input.keys();
    for (const id of this.manifests.keys()) this.scene.setHoleStyle(id, setStyleOf(keys, id));
  }

  /** Ask the manifests again (304 when nothing changed); null — every loaded set. */
  reload(setId: number | null = null): void {
    const ids = setId === null ? this.input.sets().map((s) => s.id) : [setId];
    const hidden = this.input.hidden();
    for (const id of ids) if (this.manifests.has(id) || setVisibilityOf(hidden, id).set) this.schedule(id);
  }

  /** After a lost context: the scene is empty, everything comes again in full. */
  restore(): void {
    this.generation++;
    this.manifests.clear();
    this.built.clear();
    this.queue.cancelAll();
    for (const s of this.input.sets()) if (setVisibilityOf(this.input.hidden(), s.id).set) void this.loadManifest(s.id, true);
    this.publish();
  }

  dispose(): void {
    this.disposed = true;
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
    this.queue.cancelAll();
    this.worker.dispose();
  }

  private drop(id: number): void {
    this.manifests.delete(id);
    for (const key of [...this.built.keys()]) if (key.startsWith(`${id}:`)) this.built.delete(key);
    this.scene.removeHoles(id);
  }

  private schedule(id: number): void {
    const t = this.timers.get(id);
    if (t) clearTimeout(t);
    this.timers.set(
      id,
      setTimeout(() => {
        this.timers.delete(id);
        void this.loadManifest(id, false);
      }, this.manifests.size ? RELOAD_MS : 0),
    );
  }

  private url(path: string): string {
    const t = coreTarget();
    if (!t) throw new VizError("core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.", 0);
    return apiUrl(t, path);
  }

  private async track<T>(fn: () => Promise<T>, setId: number): Promise<T | null> {
    this.inflight++;
    this.publish();
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof VizError && e.code === "cancelled") && !this.disposed) {
        // the set is gone (removed in another window): it leaves the scene at once
        if (e instanceof VizError && e.status === 404 && !String(e.code).startsWith("part")) this.drop(setId);
        sceneUi.set({ error: e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e) });
      }
      return null;
    } finally {
      this.inflight--;
      this.publish();
    }
  }

  private async loadManifest(setId: number, full: boolean): Promise<void> {
    const gen = this.generation;
    await this.track(async () => {
      const r = await this.queue.load({ url: this.url(`/viz/sets/${setId}/holes`), priority: -1, revalidate: !full && this.manifests.has(setId) });
      if (gen !== this.generation || this.disposed || !this.input.sets().some((s) => s.id === setId)) return;
      if (!r.buffer) return; // 304: nothing changed
      const m = readHolesManifest(r.buffer);
      this.manifests.set(setId, m);
      this.scene.setHoles(m, setStyleOf(this.input.keys(), setId), setVisibilityOf(this.input.hidden(), setId));
      sceneUi.set({ error: null });
      const parts = [...m.parts].sort((a, b) => this.scene.partPriority(a.bbox) - this.scene.partPriority(b.bbox));
      for (const part of parts) {
        const cached = this.built.get(`${setId}:${part.k}`);
        if (cached && cached.etag === part.etag && !full) this.scene.setPaths(setId, part.k, cached.paths);
        else void this.loadPart(setId, m, part, gen);
      }
    }, setId);
  }

  private async loadPart(setId: number, m: HolesManifest, part: HolesPart, gen: number): Promise<void> {
    const key = `${setId}:${part.k}`;
    await this.track(async () => {
      const cached = this.built.get(key);
      const r = await this.queue.load({ url: this.url(`/viz/sets/${setId}/paths/${part.k}`), priority: this.scene.partPriority(part.bbox), revalidate: cached !== undefined });
      if (gen !== this.generation || this.manifests.get(setId) !== m) return;
      if (!r.buffer) {
        if (cached) this.scene.setPaths(setId, part.k, cached.paths);
        return;
      }
      const built = await this.worker.paths(r.buffer, partKinds(m, part.k));
      if (gen !== this.generation || this.manifests.get(setId) !== m) return;
      this.built.set(key, { etag: r.etag ?? part.etag, paths: built });
      this.scene.setPaths(setId, part.k, built);
    }, setId);
  }

  private publish(): void {
    if (this.disposed) return;
    const loading = this.inflight + this.timers.size;
    const s = sceneUi.get();
    const sets = this.manifests.size;
    if (s.loading !== loading || s.sets !== sets) sceneUi.set({ loading, sets, content: s.content + 1 });
  }
}
