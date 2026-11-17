/**
 * The main-thread side of the geometry Worker: requests with ids, the answer buffers come back by
 * transfer. Without a Worker (tests, a refused module worker) the same pure code runs here.
 */
import { VizError } from "../error";
import { buildPaths, type BuiltPaths } from "./paths";

export interface WorkerRequest {
  id: number;
  kind: "paths";
  buffer: ArrayBuffer;
  kinds: Uint8Array | null;
}

export type WorkerResponse = { id: number; ok: true; result: BuiltPaths } | { id: number; ok: false; error: { code: string; message: string; hint: string } };

export class GeometryWorker {
  private worker: Worker | null = null;
  private seq = 0;
  private readonly pending = new Map<number, { resolve: (b: BuiltPaths) => void; reject: (e: unknown) => void; req: WorkerRequest }>();

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
    const id = ++this.seq;
    const req: WorkerRequest = { id, kind: "paths", buffer, kinds };
    return new Promise<BuiltPaths>((resolve, reject) => {
      if (!this.worker) {
        queueMicrotask(() => {
          try {
            resolve(buildPaths(buffer, kinds));
          } catch (e) {
            reject(e);
          }
        });
        return;
      }
      this.pending.set(id, { resolve, reject, req });
      this.worker.postMessage(req, kinds ? [buffer, kinds.buffer as ArrayBuffer] : [buffer]);
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
