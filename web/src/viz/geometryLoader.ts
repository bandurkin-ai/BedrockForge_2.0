/**
 * Raw geometry buffers (/api/blobs/{sha256}): fetched in a Web Worker and handed over without
 * copying (transferable ArrayBuffers), so a 10-million-triangle mesh never blocks the interface.
 * The server sends little-endian float32 / uint32 / uint8 arrays; no decoding is needed.
 */

export interface Bounds {
  min: [number, number, number];
  max: [number, number, number];
}

export interface LoadRequest {
  id: number;
  urls: string[];
  /** Index of a float32 x, y, z buffer whose bounds to compute, or -1. */
  boundsOf: number;
}

export interface LoadResult {
  id: number;
  buffers?: ArrayBuffer[];
  bounds?: Bounds | null;
  error?: string;
}

/** Bounds of float32 x, y, z triples. */
export function bounds(v: Float32Array): Bounds | null {
  if (v.length < 3) return null;
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = 0; i < v.length; i += 3) {
    const x = v[i], y = v[i + 1], z = v[i + 2];
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
    if (z < z0) z0 = z;
    if (z > z1) z1 = z;
  }
  return { min: [x0, y0, z0], max: [x1, y1, z1] };
}

export async function fetchBuffer(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Геометрия не загрузилась: ошибка ${response.status}`);
  return response.arrayBuffer();
}

/** What the worker does; also the fallback where workers are unavailable (tests). */
export async function load(request: LoadRequest): Promise<LoadResult> {
  try {
    const buffers = await Promise.all(request.urls.map(fetchBuffer));
    const b = request.boundsOf >= 0 ? bounds(new Float32Array(buffers[request.boundsOf])) : null;
    return { id: request.id, buffers, bounds: b };
  } catch (e) {
    return { id: request.id, error: e instanceof Error ? e.message : String(e) };
  }
}

let worker: Worker | null | undefined;
let nextId = 1;
const pending = new Map<number, (r: LoadResult) => void>();

function getWorker(): Worker | null {
  if (worker !== undefined) return worker;
  try {
    worker = typeof Worker === "undefined" ? null : new Worker(new URL("./geometry.worker.ts", import.meta.url), { type: "module" });
    worker?.addEventListener("message", (e: MessageEvent<LoadResult>) => {
      pending.get(e.data.id)?.(e.data);
      pending.delete(e.data.id);
    });
  } catch {
    worker = null;
  }
  return worker;
}

/** Absolute URLs: a module worker resolves relative ones against its own script. */
const absolute = (url: string) => new URL(url, globalThis.location?.href ?? "http://localhost/").href;

/** Fetches buffers off the main thread; `boundsOf` names a float32 x, y, z buffer to measure. */
export async function loadBuffers(urls: string[], boundsOf = -1): Promise<{ buffers: ArrayBuffer[]; bounds: Bounds | null }> {
  const request: LoadRequest = { id: nextId++, urls: urls.map(absolute), boundsOf };
  const w = getWorker();
  const result = w
    ? await new Promise<LoadResult>((resolve) => {
        pending.set(request.id, resolve);
        w.postMessage(request);
      })
    : await load(request);
  if (result.error || !result.buffers) throw new Error(result.error ?? "Геометрия не загрузилась");
  return { buffers: result.buffers, bounds: result.bounds ?? null };
}
