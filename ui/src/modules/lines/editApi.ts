/**
 * Calls of the string editor to the core (ADR 0015 §2): the exact geometry of a layer (BFA1 `linegeom`), a save
 * (`PUT …/geometry`, BFA1 with the base version; 409 `layer_changed`), the journal of versions, a return to a
 * version, a move of lines to another layer, a new layer, the snap targets of a set and the section of a
 * wireframe (BFA1), the triangle under a ray. Errors are `CoreError` {code, message, hint} in Russian. No React.
 */
import { api, CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { logRequest } from "../../shell/opLog";
import type { LineLayerInfo } from "../scene/objects";

const OFFLINE = () => new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");

/** A call whose body or answer is binary (BFA1). `answer: "bytes"` — the body of the answer as it came. */
async function raw(method: string, path: string, body: ArrayBuffer | { json: unknown } | null, answer: "json" | "bytes", signal?: AbortSignal): Promise<unknown> {
  const target = coreTarget();
  if (!target) throw OFFLINE();
  const t0 = performance.now();
  let r: Response;
  try {
    const json = body !== null && !(body instanceof ArrayBuffer);
    const type: Record<string, string> = body === null ? {} : { "Content-Type": json ? "application/json" : "application/octet-stream" };
    r = await fetch(apiUrl(target, path), { method, signal, headers: authHeaders(target, type), body: body === null ? undefined : json ? JSON.stringify(body.json) : body });
  } catch (e) {
    if (signal?.aborted) throw e;
    logRequest(method, path, undefined, 0, performance.now() - t0, "");
    throw OFFLINE();
  }
  if (r.ok && answer === "bytes") {
    const buf = await r.arrayBuffer();
    logRequest(method, path, undefined, r.status, performance.now() - t0, `BFA1, ${buf.byteLength} байт`);
    return buf;
  }
  const text = await r.text().catch(() => "");
  const sent = body instanceof ArrayBuffer ? `BFA1, ${body.byteLength} байт` : body?.json;
  logRequest(method, path, sent, r.status, performance.now() - t0, text);
  let data: Record<string, unknown> | null = null;
  try {
    data = text ? (JSON.parse(text) as Record<string, unknown>) : null;
  } catch {
    data = null;
  }
  if (!r.ok) {
    const e = data ?? {};
    const err = new CoreError(r.status, String(e.code ?? "error"), String(e.message ?? `Ядро ответило ${r.status}`), String(e.hint ?? ""));
    err.body = e;
    throw err;
  }
  return data;
}

/** The layer and its version after a save, a return or a move. */
export interface Saved {
  layer: LineLayerInfo;
  version: number;
}

/** The fields of a 409 `layer_changed` (the core puts them next to {code, message, hint}). */
export interface Conflict {
  layerId: number;
  version: number;
  author: string | null;
  at: string | null;
  message: string;
}

export function conflictOf(e: unknown): Conflict | null {
  if (!(e instanceof CoreError) || e.status !== 409 || e.code !== "layer_changed") return null;
  const b = e.body ?? {};
  return { layerId: Number(b.layer_id), version: Number(b.version), author: typeof b.author === "string" ? b.author : null, at: typeof b.at === "string" ? b.at : null, message: e.message };
}

export const getGeometry = (layerId: number, signal?: AbortSignal): Promise<ArrayBuffer> => raw("GET", `/lines/layers/${layerId}/geometry`, null, "bytes", signal) as Promise<ArrayBuffer>;

export const putGeometry = (layerId: number, body: ArrayBuffer): Promise<Saved> => raw("PUT", `/lines/layers/${layerId}/geometry`, body, "json") as Promise<Saved>;

export const moveLines = (layerId: number, body: ArrayBuffer): Promise<{ source: Saved; target: Saved }> => raw("POST", `/lines/layers/${layerId}/move-lines`, body, "json") as Promise<{ source: Saved; target: Saved }>;

/** A row of the journal of a layer (newest first). */
export interface VersionRow {
  version: number;
  action: string;
  note: string;
  author: string | null;
  at: string;
  line_count: number;
  point_count: number;
  prev_line_count: number;
  prev_point_count: number;
  current: boolean;
}

export interface Journal {
  layer_id: number;
  /** the current version (`line_layers.rev`) — the base of a save, a return or a move */
  version: number;
  versions: VersionRow[];
}

export const getVersions = (layerId: number): Promise<Journal> => api.get<Journal>(`/lines/layers/${layerId}/versions`);

export const restoreVersion = (layerId: number, version: number, expected: number): Promise<Saved> => api.post<Saved>(`/lines/layers/${layerId}/versions/${version}/restore`, { expected_version: expected });

/** `lines.new_layer`: an empty layer in a new file of lines, or in `file_id`; a taken name becomes «имя (2)». */
export const newLayer = (name: string, fileId: number | null = null): Promise<LineLayerInfo> => api.post<LineLayerInfo>("/lines/layers", fileId === null ? { name } : { name, file_id: fileId });

/** BFA1 `snap` of a set in the window of a section or the extent of the view. */
export const getSetSnap = (setId: number, query: string, signal?: AbortSignal): Promise<ArrayBuffer> => raw("GET", `/viz/sets/${setId}/snap?${query}`, null, "bytes", signal) as Promise<ArrayBuffer>;

/** BFA1 `section` of a wireframe on a section line. */
export const getWireframeSection = (id: number, query: string, signal?: AbortSignal): Promise<ArrayBuffer> => raw("GET", `/viz/wireframes/${id}/section?${query}`, null, "bytes", signal) as Promise<ArrayBuffer>;

/** The exact triangle a ray (project coordinates) meets first among the wireframes, or null. */
export async function pickTriangle(body: { origin: number[]; direction: number[]; wireframe_ids: number[]; clip: number[][] | null }, signal?: AbortSignal): Promise<{ corners: [number, number, number][]; name: string } | null> {
  const r = (await raw("POST", "/wireframes/pick", { json: body }, "json", signal)) as { hit: { vertices: [number, number, number][]; name?: string } | null } | null;
  return r?.hit ? { corners: r.hit.vertices, name: r.hit.name ?? "" } : null;
}
