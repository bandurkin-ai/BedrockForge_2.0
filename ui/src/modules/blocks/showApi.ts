/**
 * Calls of the display of block models (ТЗ 8.7.6; ADR 0023 §11; docs/progress/e17-api.md §12): `POST
 * /viz/blocks/{id}/shell` answers BFA1 `blocks` (the faces; `channel` + `seq` let a newer request of the channel
 * overtake the older one — the older answers 409 `superseded`, which the display never shows), `POST
 * /viz/blocks/{id}/at` answers the block under a point (JSON). Errors are `CoreError` {code, message, hint} in
 * Russian. A missing value is `null` in the answer, never 0. No React, no three.js.
 */
import { api, CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { logRequest } from "../../shell/opLog";
import type { Rule } from "./filters/constructor";

const OFFLINE = () => new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");

export type Triple = [number, number, number];

/** The colour part of the body of the shell request (e17-api §12). */
export interface ColourBody {
  field: string | null;
  kind: "none" | "scale" | "classes" | "categories";
  from?: number;
  to?: number;
  log?: boolean;
  breaks?: number[];
  hidden?: number[];
}

/** The body of `POST /viz/blocks/{id}/shell` without the overtaking keys. */
export interface ShellBody {
  mode: "shell" | "all";
  limit: number;
  filter_id?: number;
  restrict?: Rule;
  colour: ColourBody;
  extent?: number[];
}

/** 409 `superseded`: a newer request of the channel took its place. */
export const isSuperseded = (e: unknown): boolean => e instanceof CoreError && e.status === 409 && e.code === "superseded";

export const isAborted = (e: unknown): boolean => typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";

/** BFA1 `blocks` as it came (decode with `decodeBlocks`). */
export async function postShell(modelId: number, body: ShellBody & { channel?: string; seq?: number }, signal?: AbortSignal): Promise<ArrayBuffer> {
  const target = coreTarget();
  if (!target) throw OFFLINE();
  const path = `/viz/blocks/${modelId}/shell`;
  const t0 = performance.now();
  let r: Response;
  try {
    r = await fetch(apiUrl(target, path), { method: "POST", signal, headers: authHeaders(target, { "Content-Type": "application/json" }), body: JSON.stringify(body) });
  } catch (e) {
    if (signal?.aborted) throw e;
    logRequest("POST", path, body, 0, performance.now() - t0, "");
    throw OFFLINE();
  }
  if (r.ok) {
    const buf = await r.arrayBuffer();
    logRequest("POST", path, body, r.status, performance.now() - t0, `BFA1, ${buf.byteLength} байт`);
    return buf;
  }
  const text = await r.text().catch(() => "");
  logRequest("POST", path, body, r.status, performance.now() - t0, text);
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

/** `POST /viz/blocks/{id}/at`: the point is float64 of the project, `normal` the outward normal of the face clicked. */
export interface AtBody {
  point: Triple;
  normal?: Triple;
  filter_id?: number;
  restrict?: Rule;
  /** names of the fields to read; none — all */
  fields?: string[];
}

/** The block of a point. `sub` — [sx, sy, sz, wx, wy, wz] in sub-cells; `values` — a number, a label or null (no value). */
export interface AtFound {
  found: true;
  row: number;
  p: number;
  ijk: Triple;
  sub: number[] | null;
  centre: Triple;
  size: Triple;
  /** false — the block is there, but the filter hides it */
  passed: boolean;
  f: number;
  values: Record<string, number | string | null>;
}
export type AtAnswer = AtFound | { found: false };

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null);

/** The answer checked: a damaged one is a Russian error, a missing value stays `null`. */
export function readAt(raw: unknown): AtAnswer {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  if (o.found === false) return { found: false };
  const ijk = triple(o.ijk);
  const centre = triple(o.centre);
  const size = triple(o.size);
  const values = o.values && typeof o.values === "object" && !Array.isArray(o.values) ? (o.values as Record<string, unknown>) : null;
  if (o.found !== true || !ijk || !centre || !size || !values || !isNum(o.row)) {
    throw new CoreError(0, "bad_answer", "Ядро ответило на запрос блока в непонятном виде.", "Повторите щелчок. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.");
  }
  const clean: Record<string, number | string | null> = {};
  for (const [k, v] of Object.entries(values)) clean[k] = isNum(v) || typeof v === "string" ? v : null;
  const sub = Array.isArray(o.sub) && o.sub.length === 6 && o.sub.every(isNum) ? (o.sub as number[]) : null;
  return { found: true, row: o.row, p: isNum(o.p) ? o.p : 0, ijk, sub, centre, size, passed: o.passed !== false, f: isNum(o.f) ? o.f : 1, values: clean };
}

export async function postAt(modelId: number, body: AtBody): Promise<AtAnswer> {
  return readAt(await api.post<unknown>(`/viz/blocks/${modelId}/at`, body));
}
