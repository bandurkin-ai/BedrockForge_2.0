/**
 * Download queue of the 3D window (ADR 0007 §1, ADR 0004 §4): at most 4 requests at once, the most
 * urgent first (parts nearer the centre of the screen), `If-None-Match` with the ETag seen before.
 *
 * A buffer belongs to one consumer: it is usually transferred to a Worker (and detached), so the queue
 * keeps the bytes only for `retain` requests (manifests decoded on the main thread). On 304 without kept
 * bytes the result has `buffer: null` — the consumer keeps what it already has. A consumer that dropped
 * its data calls `forget(url)` or asks with `revalidate: false`. `fetch` is a parameter: no globals.
 */
import { VizError } from "../error";

export interface FetchInit {
  method: "GET";
  headers: Record<string, string>;
  signal?: AbortSignal;
}

export interface ResponseLike {
  status: number;
  ok: boolean;
  headers: { get(name: string): string | null };
  arrayBuffer(): Promise<ArrayBuffer>;
  text(): Promise<string>;
}

export type FetchLike = (url: string, init: FetchInit) => Promise<ResponseLike>;

export interface LoadRequest {
  url: string;
  /** smaller first: e.g. the distance of the part from the screen centre (cull.screenPriority) */
  priority?: number;
  /** keep the bytes so that a 304 gives them back */
  retain?: boolean;
  /** send If-None-Match with the known ETag (default true) */
  revalidate?: boolean;
}

export interface Loaded {
  url: string;
  status: 200 | 304;
  etag: string | null;
  /** null on 304 when the bytes were not kept: nothing changed, keep the data you have */
  buffer: ArrayBuffer | null;
  changed: boolean;
}

export interface LoaderOptions {
  fetch: FetchLike;
  /** requests at once, 1–4 (default 4) */
  concurrency?: number;
  /** headers of every request (the X-BF-Token of the core) */
  headers?: () => Record<string, string>;
  /** bytes kept for `retain` requests, least recently used dropped first (default 64 MB) */
  retainBytes?: number;
}

export const MAX_CONCURRENCY = 4;
const MAX_ETAGS = 4096;

interface Task {
  req: Required<LoadRequest>;
  seq: number;
  promise: Promise<Loaded>;
  resolve: (r: Loaded) => void;
  reject: (e: unknown) => void;
  abort: AbortController | null;
}

function cancelled(url: string): VizError {
  return new VizError("cancelled", `Загрузка отменена: ${url}`, "");
}

const OFFLINE_HINT = "Подождите перезапуска ядра.";

export class LoadQueue {
  private readonly fetchFn: FetchLike;
  private readonly limit: number;
  private readonly headers: () => Record<string, string>;
  private readonly retainLimit: number;
  private readonly pending = new Map<string, Task>();
  private readonly running = new Map<string, Task>();
  private readonly etags = new Map<string, string>();
  private readonly kept = new Map<string, { etag: string; buffer: ArrayBuffer; bytes: number }>();
  private keptBytes = 0;
  private seq = 0;
  private scheduled = false;
  /** the largest number of requests seen at once (tests and the diagnostic counter) */
  peak = 0;

  constructor(opts: LoaderOptions) {
    this.fetchFn = opts.fetch;
    this.limit = Math.max(1, Math.min(MAX_CONCURRENCY, Math.floor(opts.concurrency ?? MAX_CONCURRENCY)));
    this.headers = opts.headers ?? (() => ({}));
    this.retainLimit = opts.retainBytes ?? 64 << 20;
  }

  get active(): number {
    return this.running.size;
  }

  get queued(): number {
    return this.pending.size;
  }

  /** Bytes kept for `retain` requests. */
  get retainedBytes(): number {
    return this.keptBytes;
  }

  /**
   * Queues a download. Asking again for a url already queued or running gives the same promise and only
   * raises its priority (the scene re-asks every frame for what it still misses).
   */
  load(request: LoadRequest): Promise<Loaded> {
    const priority = request.priority ?? 0;
    const known = this.pending.get(request.url) ?? this.running.get(request.url);
    if (known) {
      if (priority < known.req.priority) known.req.priority = priority;
      return known.promise;
    }
    let resolve!: (r: Loaded) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<Loaded>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    const req = { url: request.url, priority, retain: request.retain ?? false, revalidate: request.revalidate ?? true };
    this.pending.set(request.url, { req, seq: this.seq++, promise, resolve, reject, abort: null });
    this.schedule();
    return promise;
  }

  /** New priorities for everything still queued (after the camera moved); null leaves one as it is. */
  reprioritize(priority: (url: string) => number | null): void {
    for (const task of this.pending.values()) {
      const p = priority(task.req.url);
      if (p !== null) task.req.priority = p;
    }
  }

  /** Drops a queued request or aborts a running one; its promise rejects with code `cancelled`. */
  cancel(url: string): void {
    const queued = this.pending.get(url);
    if (queued) {
      this.pending.delete(url);
      queued.reject(cancelled(url));
    }
    this.running.get(url)?.abort?.abort();
  }

  /** Cancels everything (project closed, scene disposed). */
  cancelAll(): void {
    for (const url of [...this.pending.keys(), ...this.running.keys()]) this.cancel(url);
  }

  /** Forgets the ETag and kept bytes of a url: its next load is a full one. */
  forget(url: string): void {
    this.etags.delete(url);
    this.dropKept(url);
  }

  etag(url: string): string | null {
    return this.etags.get(url) ?? null;
  }

  private dropKept(url: string): void {
    const k = this.kept.get(url);
    if (!k) return;
    this.keptBytes -= k.bytes;
    this.kept.delete(url);
  }

  private keep(url: string, etag: string, buffer: ArrayBuffer): void {
    this.dropKept(url);
    if (buffer.byteLength > this.retainLimit) return;
    this.kept.set(url, { etag, buffer, bytes: buffer.byteLength });
    this.keptBytes += buffer.byteLength;
    for (const [old, k] of this.kept) {
      if (this.keptBytes <= this.retainLimit) break;
      this.keptBytes -= k.bytes;
      this.kept.delete(old);
    }
  }

  /** Kept bytes still usable: not transferred away (a detached buffer has length 0). */
  private keptFor(url: string, etag: string): ArrayBuffer | null {
    const k = this.kept.get(url);
    if (!k || k.etag !== etag) return null;
    if ((k.buffer as { detached?: boolean }).detached === true || (k.bytes > 0 && k.buffer.byteLength === 0)) {
      this.dropKept(url);
      return null;
    }
    // Least recently used order: a hit moves the entry to the end.
    this.kept.delete(url);
    this.kept.set(url, k);
    return k.buffer;
  }

  private next(): Task | null {
    let best: Task | null = null;
    for (const t of this.pending.values()) {
      if (!best || t.req.priority < best.req.priority || (t.req.priority === best.req.priority && t.seq < best.seq)) best = t;
    }
    return best;
  }

  /** Starts on the next microtask, so that requests queued together go by priority. */
  private schedule(): void {
    if (this.scheduled) return;
    this.scheduled = true;
    queueMicrotask(() => {
      this.scheduled = false;
      this.pump();
    });
  }

  private pump(): void {
    while (this.running.size < this.limit) {
      const task = this.next();
      if (!task) return;
      this.pending.delete(task.req.url);
      this.running.set(task.req.url, task);
      this.peak = Math.max(this.peak, this.running.size);
      void this.run(task);
    }
  }

  private async run(task: Task): Promise<void> {
    try {
      task.resolve(await this.request(task));
    } catch (e) {
      task.reject(e);
    } finally {
      this.running.delete(task.req.url);
      this.pump();
    }
  }

  private async request(task: Task): Promise<Loaded> {
    const { url, retain, revalidate } = task.req;
    const known = this.etags.get(url);
    // With `retain`, revalidate only while the bytes to give back are still there.
    const sendTag = revalidate && known !== undefined && (!retain || this.keptFor(url, known) !== null) ? known : null;
    const headers: Record<string, string> = { ...this.headers() };
    if (sendTag) headers["If-None-Match"] = sendTag;
    task.abort = typeof AbortController === "undefined" ? null : new AbortController();
    let r: ResponseLike;
    try {
      r = await this.fetchFn(url, { method: "GET", headers, signal: task.abort?.signal });
    } catch {
      if (task.abort?.signal.aborted) throw cancelled(url);
      throw new VizError("core_offline", "Ядро недоступно.", OFFLINE_HINT, 0);
    }
    if (r.status === 304) {
      if (!sendTag) throw new VizError("bad_response", `Ядро ответило 304 на запрос без версии: ${url}`, "Повторите загрузку вида.", 304);
      return { url, status: 304, etag: sendTag, buffer: retain ? this.keptFor(url, sendTag) : null, changed: false };
    }
    if (!r.ok) throw await httpError(r, url);
    let buffer: ArrayBuffer;
    try {
      buffer = await r.arrayBuffer();
    } catch {
      if (task.abort?.signal.aborted) throw cancelled(url);
      throw new VizError("core_offline", "Связь с ядром прервалась во время загрузки.", OFFLINE_HINT, 0);
    }
    const etag = r.headers.get("ETag");
    if (etag) {
      this.etags.delete(url);
      this.etags.set(url, etag);
      if (this.etags.size > MAX_ETAGS) this.etags.delete(this.etags.keys().next().value as string);
      if (retain) this.keep(url, etag, buffer);
      else this.dropKept(url);
    } else {
      this.forget(url);
    }
    return { url, status: 200, etag, buffer, changed: true };
  }
}

/** The core's `{code, message, hint}` of a failed request, or a generic one. */
async function httpError(r: ResponseLike, url: string): Promise<VizError> {
  const text = await r.text().catch(() => "");
  try {
    const e = JSON.parse(text) as Record<string, unknown>;
    if (typeof e.message === "string") {
      return new VizError(String(e.code ?? "error"), e.message, typeof e.hint === "string" ? e.hint : "", r.status);
    }
  } catch {
    // not JSON: below
  }
  return new VizError("http_error", `Ядро ответило ${r.status} на ${url}.`, "Повторите действие; если не помогает — перезапустите ядро.", r.status);
}
