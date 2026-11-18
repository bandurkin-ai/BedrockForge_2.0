/**
 * The main-thread side of the geometry Worker: requests with ids, the answer buffers come back by
 * transfer. Without a Worker (tests, a refused module worker) the same pure code runs here.
 */
import { VizError } from "../error";
import type { BuiltLines } from "../lines/build";
import type { BuiltMesh } from "../mesh/build";
import { buildLines } from "./lines";
import { buildMesh } from "./mesh";
import { buildPaths, type BuiltPaths } from "./paths";

export type WorkerRequest =
  | { id: number; kind: "paths"; buffer: ArrayBuffer; kinds: Uint8Array | null }
  | { id: number; kind: "lines"; buffer: ArrayBuffer }
  | { id: number; kind: "mesh"; buffer: ArrayBuffer };

type Built = BuiltPaths | BuiltLines | BuiltMesh;
export type WorkerResponse = { id: number; ok: true; result: Built } | { id: number; ok: false; error: { code: string; message: string; hint: string } };

export class GeometryWorker {
  private worker: Worker | null = null;
  private seq = 0;
  private readonly pending = new Map<number, { resolve: (b: Built) => void; reject: (e: unknown) => void }>();

  constructor(useWorker = typeof Worker !== "undefined") {
    if (!useWorker) return;
    try {
      this.worker = new Worker(new URL("./geometry.worker.ts", import.meta.url), { type: "module", name: "bf-geometry" });
      this.worker.onmessage = (e: MessageEvent<WorkerResponse>) => this.answer(e.data);
      this.worker.onerror = () => this.fallBack();
    } catch {
      this.worker = null;
    }
  }

  /** Line buffers of a part's traces; the buffer is transferred (detached here). */
  paths(buffer: ArrayBuffer, kinds: Uint8Array | null): Promise<BuiltPaths> {
    return this.run({ id: ++this.seq, kind: "paths", buffer, kinds }, () => buildPaths(buffer, kinds), kinds ? [buffer, kinds.buffer as ArrayBuffer] : [buffer]);
  }

  /** Segments, markers and text anchors of a part of a line layer; the buffer is transferred (detached here). */
  lines(buffer: ArrayBuffer): Promise<BuiltLines> {
    return this.run({ id: ++this.seq, kind: "lines", buffer }, () => buildLines(buffer), [buffer]);
  }

  /** Vertices and triangles of a part of a surface, checked; the buffer is transferred (detached here) and comes back. */
  mesh(buffer: ArrayBuffer): Promise<BuiltMesh> {
    return this.run({ id: ++this.seq, kind: "mesh", buffer }, () => buildMesh(buffer), [buffer]);
  }

  private run<T extends Built>(req: WorkerRequest, local: () => T, transfer: Transferable[]): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.worker) {
        queueMicrotask(() => {
          try {
            resolve(local());
          } catch (e) {
            reject(e);
          }
        });
        return;
      }
      this.pending.set(req.id, { resolve: resolve as (b: Built) => void, reject });
      this.worker.postMessage(req, transfer);
    });
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    for (const p of this.pending.values()) p.reject(new VizError("cancelled", "Построение геометрии отменено.", ""));
    this.pending.clear();
  }

  private answer(r: WorkerResponse): void {
    const p = this.pending.get(r.id);
    if (!p) return;
    this.pending.delete(r.id);
    if (r.ok) p.resolve(r.result);
    else p.reject(new VizError(r.error.code, r.error.message, r.error.hint));
  }

  /** The Worker failed to start: what was sent cannot be rebuilt (its buffers left), so those requests fail. */
  private fallBack(): void {
    this.worker?.terminate();
    this.worker = null;
    for (const p of this.pending.values()) p.reject(new VizError("worker_failed", "Фоновый поток геометрии не запустился.", "Повторите загрузку вида (Обновить)."));
    this.pending.clear();
  }
}
