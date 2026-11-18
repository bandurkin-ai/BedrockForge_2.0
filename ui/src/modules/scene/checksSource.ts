/**
 * The red triangles of checked wireframes in the 3D scene (ТЗ 7.21; ADR 0013 §3, §5–§6): for every shown
 * wireframe whose checks found crossing triangles (`stats.checks` of the list of the project) —
 * `GET /wireframes/{id}/checks` with `If-None-Match` → a scene layer `chk:<id>` over its surface. A new check
 * (`checked_at` of the list moved) asks again: the same triangles are a 304 and the layer stays. The layer
 * follows the hidden key of its wireframe and is not loaded for a wireframe that is not shown; it leaves with
 * its wireframe and when a check finds nothing. When the core has no list to show (a deleted cache, a project
 * from a `.bfggis`) the wireframe is named in `checksLayerStore`: «Свойства» say to press «Проверить». After a
 * lost context everything comes again. A lazy chunk with three.js.
 */
import { coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { Scene } from "../../viz/Scene";
import { VizError } from "../../viz/error";
import { LoadQueue } from "../../viz/load/loader";
import { ChecksView, readChecks } from "../../viz/mesh/checks";
import { checksLayerStore, doneChecks } from "../wireframes/checks";
import type { WireframeInfo } from "./objects";
import { sceneUi } from "./sceneHost";

export const checksKey = (id: number) => `chk:${id}`;
const wireframeKey = (id: number) => `wf:${id}`;
const pathOf = (id: number) => `/wireframes/${id}/checks`;

export interface ChecksInput {
  wireframes: () => readonly WireframeInfo[];
  /** hidden keys in force (`wf:<id>`) */
  hidden: () => readonly string[];
}

interface LayerState {
  /** the check the layer is (or is being) asked for: `checked_at` of the list */
  sig: string;
  view: ChecksView | null;
  loading: boolean;
  /** asked again while its request was in flight: the answer may be of the check before */
  stale: boolean;
  failed: boolean;
}

export class ChecksSource {
  private readonly queue = new LoadQueue({ fetch: (url, init) => fetch(url, init), headers: () => (coreTarget() ? authHeaders(coreTarget()!) : {}) });
  private readonly layers = new Map<number, LayerState>();
  private inflight = 0;
  private generation = 0;
  private disposed = false;

  constructor(
    private readonly scene: Scene,
    private readonly input: ChecksInput,
  ) {}

  get idle(): boolean {
    return this.inflight === 0;
  }

  /** For the E2E hook: the red layers now in the scene. */
  stats(): ReturnType<ChecksView["stats"]>[] {
    return [...this.layers.values()].flatMap((st) => (st.view ? [st.view.stats()] : []));
  }

  /** The list of wireframes (their checks) or the hidden keys changed. */
  sync(): void {
    if (this.disposed) return;
    const hidden = new Set(this.input.hidden());
    const wanted = new Set<number>();
    let changed = false;
    for (const w of this.input.wireframes()) {
      const c = doneChecks(w.stats);
      if (!c || !((c.listed ?? 0) > 0)) continue;
      wanted.add(w.id);
      const shown = !hidden.has(wireframeKey(w.id));
      const sig = c.checked_at ?? "";
      const st = this.layers.get(w.id);
      if (!st) {
        if (shown) void this.load(w.id, { sig, view: null, loading: false, stale: false, failed: false }, false); // never shown: not loaded
        continue;
      }
      if (st.view?.setVisible(shown)) changed = true;
      if (st.sig !== sig || st.failed) {
        st.sig = sig;
        if (st.loading) st.stale = true;
        else if (shown || st.view) void this.load(w.id, st, true);
      }
    }
    for (const id of [...this.layers.keys()]) if (!wanted.has(id)) this.drop(id);
    const missing = checksLayerStore.get().missing;
    if (missing.some((id) => !wanted.has(id))) checksLayerStore.set({ missing: missing.filter((id) => wanted.has(id)) });
    if (changed) this.scene.invalidate();
  }

  /** After a lost context: the scene dropped the layers, everything comes again. */
  restore(): void {
    this.generation++;
    this.queue.cancelAll();
    for (const id of this.layers.keys()) this.forget(id); // the layers went with the context: asked in full
    this.layers.clear();
    this.sync();
  }

  dispose(): void {
    this.disposed = true;
    this.queue.cancelAll();
    for (const id of [...this.layers.keys()]) this.drop(id);
    if (checksLayerStore.get().missing.length) checksLayerStore.set({ missing: [] });
  }

  // ---------------------------------------------------------------- internals

  private url(id: number): string {
    const t = coreTarget();
    if (!t) throw new VizError("core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.", 0);
    return apiUrl(t, pathOf(id));
  }

  /** The ETag of a layer that is not in the scene any more must not turn its next answer into a 304. */
  private forget(id: number): void {
    try {
      this.queue.forget(this.url(id));
    } catch {
      /* the core is away: nothing is remembered about it */
    }
  }

  private drop(id: number): void {
    const st = this.layers.get(id);
    if (!st) return;
    try {
      if (st.loading) this.queue.cancel(this.url(id));
    } catch {
      /* the core is away: nothing is in flight */
    }
    if (st.view) this.scene.removeLayer(checksKey(id), false);
    this.forget(id);
    this.layers.delete(id);
    this.scene.invalidate();
  }

  private setMissing(id: number, missing: boolean): void {
    const now = checksLayerStore.get().missing;
    if (now.includes(id) === missing) return;
    checksLayerStore.set({ missing: missing ? [...now, id] : now.filter((x) => x !== id) });
  }

  private async load(id: number, st: LayerState, revalidate: boolean): Promise<void> {
    this.layers.set(id, st);
    const gen = this.generation;
    st.loading = true;
    st.failed = false;
    this.inflight++;
    try {
      const r = await this.queue.load({ url: this.url(id), revalidate: revalidate && st.view !== null });
      if (gen !== this.generation || this.disposed || this.layers.get(id) !== st) return;
      if (!r.buffer) return; // 304: the same triangles
      const checks = readChecks(r.buffer);
      if (st.view) this.scene.removeLayer(checksKey(id), false);
      st.view = null;
      this.setMissing(id, checks.state === "missing");
      if (checks.state === "done" && checks.triangles > 0) {
        st.view = new ChecksView(id, checks, { clip: this.scene.clipPlanes });
        st.view.setVisible(!this.input.hidden().includes(wireframeKey(id)));
        this.scene.addLayer(checksKey(id), st.view);
      } else this.forget(id); // nothing to keep: the next answer is asked in full
      this.scene.invalidate();
    } catch (e) {
      if (this.layers.get(id) === st) st.failed = true; // asked again with the next change of the list
      if (!(e instanceof VizError && (e.code === "cancelled" || e.status === 404)) && !this.disposed) {
        sceneUi.set({ error: `Слой самопересечений каркаса не показан: ${e instanceof VizError ? e.full : e instanceof Error ? e.message : String(e)}` });
      }
    } finally {
      st.loading = false;
      this.inflight--;
      if (st.stale && this.layers.get(id) === st && gen === this.generation && !this.disposed) {
        st.stale = false;
        void this.load(id, st, true);
      }
    }
  }
}
