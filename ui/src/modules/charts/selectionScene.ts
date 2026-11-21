/**
 * The selection of samples in the main 3D (ТЗ 8.3, № 237; ADR 0019 §8): for the selection of the application — the
 * layer `samples:selection` (`viz/holes/sampleMarks.ts`) of its intervals from `GET /stats/selection/{token}/
 * segments` by the desurvey method of its set in the scene; none — no layer. A selection gone (409, 404) is cleared
 * with the reason; the number of segments drawn goes to the store (the counter of the layer for the E2E). Only the
 * newest answer is drawn; the holes of the set in the scene by another method — the segments asked again. After a
 * lost context the layer comes again from the segments kept. A lazy chunk with three.js (`selection.ts` attaches it
 * to every scene).
 */
import { CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { logRequest } from "../../shell/opLog";
import { handleSelectionError, sampleSelectionStore } from "../../shell/sampleSelection";
import type { Scene } from "../../viz/Scene";
import { readSelectionSegments, SampleMarks, type SelectionSegments } from "../../viz/holes/sampleMarks";

export const SAMPLE_MARKS_KEY = "samples:selection";
const DEFAULT_METHOD = "minimum_curvature";

type SelScene = Pick<Scene, "addLayer" | "removeLayer" | "invalidate" | "clipPlanes" | "resolution" | "holes" | "onRestored"> & Partial<Pick<Scene, "onFrame">>;

/** BFA1 of the core; its error `{code, message, hint}` — a CoreError (409 `selection_stale` among them). */
export async function fetchSegments(token: string, method: string, f: typeof fetch = fetch): Promise<ArrayBuffer> {
  const target = coreTarget();
  if (!target) throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  const path = `/stats/selection/${encodeURIComponent(token)}/segments?method=${encodeURIComponent(method)}`;
  const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
  const r = await f(apiUrl(target, path), { headers: authHeaders(target) });
  const elapsed = (typeof performance !== "undefined" ? performance.now() : Date.now()) - t0;
  if (r.ok) {
    const buf = await r.arrayBuffer();
    logRequest("GET", path, undefined, r.status, elapsed, `BFA1, ${buf.byteLength} байт`);
    return buf;
  }
  const text = await r.text().catch(() => "");
  logRequest("GET", path, undefined, r.status, elapsed, text);
  let e: Record<string, unknown> = {};
  try {
    e = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    /* not JSON */
  }
  const err = new CoreError(r.status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${r.status}`), String(e.hint ?? ""));
  err.body = e;
  throw err;
}

export class SelectionScene {
  private token: string | null = null;
  private data: SelectionSegments | null = null;
  private layer: SampleMarks | null = null;
  /** the desurvey method the segments were asked by (the manifest of the set in the scene) */
  private method: string | null = null;
  /** grows with every request: an answer overtaken by a newer one is dropped */
  private seq = 0;
  private readonly stops: (() => void)[] = [];
  private disposed = false;

  constructor(
    private readonly scene: SelScene,
    private readonly load: (token: string, method: string) => Promise<ArrayBuffer> = (t, m) => fetchSegments(t, m),
  ) {
    this.stops.push(sampleSelectionStore.subscribe(() => this.sync()), scene.onRestored(() => this.restore()));
    // the holes of the set loaded or loaded again by another method of the trajectory: the segments are asked again
    if (scene.onFrame) this.stops.push(scene.onFrame(() => this.methodChanged()));
    this.sync();
  }

  /** The segments drawn now (null — no layer). */
  get count(): number | null {
    return this.layer ? this.layer.count : null;
  }

  sync(): void {
    if (this.disposed) return;
    const cur = sampleSelectionStore.get().current;
    const token = cur?.token ?? null;
    if (token === this.token) return;
    this.token = token;
    this.data = null;
    this.drop();
    if (!cur) {
      this.seq++; // an answer on its way is not for anything now
      this.method = null;
      return;
    }
    void this.ask(cur.token, this.methodOf(cur.set_id));
  }

  private methodOf(setId: number): string {
    return this.scene.holes(setId)?.manifest.method ?? DEFAULT_METHOD;
  }

  /** Before a frame: the manifest of the set in the scene has another method than the segments drawn. */
  private methodChanged(): void {
    const cur = sampleSelectionStore.get().current;
    if (this.disposed || !cur || cur.token !== this.token || this.method === null) return;
    const method = this.methodOf(cur.set_id);
    if (method !== this.method) void this.ask(cur.token, method);
  }

  /** The data of the set changed (a trajectory edited): the segments of its selection are read again. */
  refresh(setId?: number): void {
    const cur = sampleSelectionStore.get().current;
    if (!cur || cur.token !== this.token || (setId !== undefined && cur.set_id !== setId)) return;
    // the layer drawn stays until the new segments come
    void this.ask(cur.token, this.methodOf(cur.set_id));
  }

  private async ask(token: string, method: string): Promise<void> {
    const mine = ++this.seq;
    this.method = method;
    try {
      const data = readSelectionSegments(await this.load(token, method));
      if (this.disposed || this.token !== token || mine !== this.seq) return;
      this.data = data;
      this.show();
    } catch (e) {
      if (this.disposed || this.token !== token || mine !== this.seq) return;
      if (!handleSelectionError(token, e)) sampleSelectionStore.set({ segments: null });
    }
  }

  private show(): void {
    const data = this.data;
    if (!data) return;
    this.drop();
    this.layer = new SampleMarks(data, { clip: this.scene.clipPlanes, resolution: this.scene.resolution });
    this.scene.addLayer(SAMPLE_MARKS_KEY, this.layer);
    this.scene.invalidate();
    sampleSelectionStore.set({ segments: this.layer.count, noPosition: data.noPosition });
  }

  private drop(): void {
    if (!this.layer) return;
    this.layer = null;
    this.scene.removeLayer(SAMPLE_MARKS_KEY);
    this.scene.invalidate();
  }

  /** After a lost context the scene dropped every layer: the one kept comes again. */
  private restore(): void {
    this.layer = null;
    this.show();
  }

  dispose(): void {
    this.disposed = true;
    this.stops.forEach((fn) => fn());
    this.drop();
  }
}

/** Attaches the selection of samples to a scene; gives what detaches it. */
export function attachSelectionScene(scene: Scene): SelectionSceneHandle {
  const s = new SelectionScene(scene);
  return { detach: () => s.dispose(), count: () => s.count, refresh: (setId) => s.refresh(setId) };
}

export interface SelectionSceneHandle {
  detach: () => void;
  count: () => number | null;
  refresh: (setId?: number) => void;
}
