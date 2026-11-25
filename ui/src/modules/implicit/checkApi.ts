/**
 * The client of the check of an implicit model against its data (ТЗ 7.35 Д-32; ADR 0026 решение 28; docs/progress/e19-api.md §8.1, §8.2):
 * - `GET /implicit/models/{id}/check?group&page&size&sort=residual|source&highlight` → the table of the deviations at the data points (a gap
 *   is `null` — never 0, 8.0.1); `…/check.csv` — the same as a file (`;`, a decimal comma, UTF-8 with a BOM): the bytes are kept as they
 *   come, so the BOM is the core's;
 * - `POST /implicit/models/{id}/support {wireframe_id?, threshold?, format?}` → the surfaces with the share of the area beyond D_оп; with
 *   `wireframe_id` and `format: "f4"` the body is the distances of the vertices (float32 little-endian, NaN — no data points), the rest of
 *   the answer is the JSON of the header `X-BF-Support`.
 * Errors are `{code, message, hint, param?}` in Russian. No React.
 */
import { api, CoreError, coreTarget } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import { isNum, numOrNull, obj, str } from "../estimation/api";
import { IMPLICIT_API, type ModelState } from "./api";

const num = (v: unknown, dflt: number): number => (isNum(v) ? v : dflt);

export interface CheckModel {
  id: number;
  name: string;
  version: number;
  state: ModelState;
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
}

export function readModelRef(raw: unknown): CheckModel {
  const m = obj(raw);
  return {
    id: num(m.id, 0),
    name: str(m.name) ?? "",
    version: num(m.version, 0),
    state: m.state === "building" || m.state === "error" ? m.state : "ready",
    stale: m.stale === true,
    stale_reason: str(m.stale_reason),
    source_lost: m.source_lost === true,
  };
}

export interface Warning {
  code: string;
  message: string;
  hint: string;
}

export const readWarnings = (v: unknown): Warning[] => (Array.isArray(v) ? v.map((w) => ({ code: str(obj(w).code) ?? "", message: str(obj(w).message) ?? "", hint: str(obj(w).hint) ?? "" })).filter((w) => w.message) : []);

// ---- the table of the deviations (Д-32 п. 1) ----

export type CheckSort = "residual" | "source";
export const CHECK_SORTS: readonly CheckSort[] = ["residual", "source"];
export const CHECK_SORT_TITLES: Record<CheckSort, string> = { residual: "По невязке (большие — первыми)", source: "По источнику (скважина, глубина)" };

export interface CheckGroup {
  group: number | null;
  name: string;
  type: string;
  wing: number | null;
  threshold: number | null;
  points: number;
  max_abs_residual: number | null;
  wireframes: number[];
  method: string;
}

export interface CheckRow {
  i: number;
  group: number | null;
  group_name: string;
  wing: number | null;
  threshold: number | null;
  /** «контакт», «внутри», «снаружи», «ручная», «значение» */
  type: string;
  source: string;
  from: number | null;
  to: number | null;
  vertex: number | null;
  x: number | null;
  y: number | null;
  z: number | null;
  f_model: number | null;
  f_data: number | null;
  residual: number | null;
  /** to the surface of the group, m; null — there is none */
  distance: number | null;
  /** the residual is beyond the threshold of the highlight */
  over: boolean;
}

export interface CheckPage {
  model: CheckModel;
  warnings: Warning[];
  group: number | null;
  /** the points of the selection (all the groups, or one) */
  n: number;
  total: number;
  max_abs_residual: number | null;
  /** m: the threshold of `over` (the default is 0,1·h) */
  highlight: number | null;
  /** the points beyond it */
  over: number;
  page: number;
  size: number;
  sort: CheckSort;
  groups: CheckGroup[];
  rows: CheckRow[];
}

const readGroup = (raw: unknown): CheckGroup => {
  const g = obj(raw);
  return {
    group: numOrNull(g.group),
    name: str(g.name) ?? "",
    type: str(g.type) ?? "",
    wing: numOrNull(g.wing),
    threshold: numOrNull(g.threshold),
    points: num(g.points, 0),
    max_abs_residual: numOrNull(g.max_abs_residual),
    wireframes: Array.isArray(g.wireframes) ? g.wireframes.filter(isNum) : [],
    method: str(g.method) ?? "",
  };
};

export const readRow = (raw: unknown): CheckRow => {
  const r = obj(raw);
  return {
    i: num(r.i, 0),
    group: numOrNull(r.group),
    group_name: str(r.group_name) ?? "",
    wing: numOrNull(r.wing),
    threshold: numOrNull(r.threshold),
    type: str(r.type) ?? "",
    source: str(r.source) ?? "",
    from: numOrNull(r.from),
    to: numOrNull(r.to),
    vertex: numOrNull(r.vertex),
    x: numOrNull(r.x),
    y: numOrNull(r.y),
    z: numOrNull(r.z),
    f_model: numOrNull(r.f_model),
    f_data: numOrNull(r.f_data),
    residual: numOrNull(r.residual),
    distance: numOrNull(r.distance),
    over: r.over === true,
  };
};

export function readCheck(raw: unknown): CheckPage {
  const r = obj(raw);
  return {
    model: readModelRef(r.model),
    warnings: readWarnings(r.warnings),
    group: numOrNull(r.group),
    n: num(r.n, 0),
    total: num(r.total, 0),
    max_abs_residual: numOrNull(r.max_abs_residual),
    highlight: numOrNull(r.highlight),
    over: num(r.over, 0),
    page: num(r.page, 1),
    size: num(r.size, 500),
    sort: r.sort === "source" ? "source" : "residual",
    groups: Array.isArray(r.groups) ? r.groups.map(readGroup) : [],
    rows: Array.isArray(r.rows) ? r.rows.map(readRow) : [],
  };
}

export interface CheckQuery {
  /** null — all the groups */
  group: number | null;
  page: number;
  size: number;
  sort: CheckSort;
  /** m; null — the default of the core (0,1·h) */
  highlight: number | null;
}

export const CHECK_SIZES: readonly number[] = [100, 250, 500, 1000, 2500];
export const DEFAULT_QUERY: CheckQuery = { group: null, page: 1, size: 500, sort: "residual", highlight: null };

/** The query string of the route (the page and the size only for the table; the file takes all the rows). */
export function checkQuery(q: CheckQuery, file = false): string {
  const p = new URLSearchParams();
  if (q.group !== null) p.set("group", String(q.group));
  p.set("sort", q.sort);
  if (q.highlight !== null) p.set("highlight", String(q.highlight));
  if (!file) {
    p.set("page", String(q.page));
    p.set("size", String(q.size));
  }
  return p.toString();
}

export const loadCheck = async (id: number, q: CheckQuery): Promise<CheckPage> => readCheck(await api.get(`${IMPLICIT_API}/models/${id}/check?${checkQuery(q)}`));

/** A call that answers with bytes: the refusal is read the way `call` of the shell reads it. */
export async function callBytes(method: "GET" | "POST", path: string, body?: unknown): Promise<Response> {
  const target = coreTarget();
  if (!target) throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  let r: Response;
  try {
    r = await fetch(apiUrl(target, path), { method, headers: authHeaders(target, body === undefined ? {} : { "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  }
  if (r.ok) return r;
  const e = obj(await r.json().catch(() => ({})));
  const err = new CoreError(r.status, str(e.code) ?? "error", str(e.message) ?? `Ядро ответило ${r.status}`, str(e.hint) ?? "");
  err.body = e;
  throw err;
}

/** The file of all the rows (`check.csv`), as the core wrote it: the BOM is in it. */
export async function loadCheckCsv(id: number, q: CheckQuery): Promise<Uint8Array> {
  const r = await callBytes("GET", `${IMPLICIT_API}/models/${id}/check.csv?${checkQuery(q, true)}`);
  return new Uint8Array(await r.arrayBuffer());
}

/** «Проверка модели «Литология».csv» — a name the system accepts. */
export const csvName = (model: string): string => `Проверка модели ${model.replace(/[\\/:*?"<>|]+/g, "_").trim() || "без названия"}.csv`;

// ---- the map of the support (Д-32 п. 2) ----

export interface SupportSurface {
  wireframe_id: number;
  name: string;
  group: number | null;
  wing: number | null;
  /** `surface` or `body` */
  kind: string;
  vertices: number;
  area: number | null;
  area_beyond: number | null;
  /** 0…1 of the area; null — a surface without area */
  share_beyond: number | null;
  max_distance: number | null;
}

export interface SupportAnswer {
  model: CheckModel;
  warnings: Warning[];
  /** D_оп, m */
  threshold: number;
  d_max: number;
  /** the share is written into the statistics of the model (not in a project open for reading) */
  recorded: boolean;
  surfaces: SupportSurface[];
}

export const readSurface = (raw: unknown): SupportSurface => {
  const s = obj(raw);
  return {
    wireframe_id: num(s.wireframe_id, 0),
    name: str(s.name) ?? "",
    group: numOrNull(s.group),
    wing: numOrNull(s.wing),
    kind: str(s.kind) ?? "surface",
    vertices: num(s.vertices, 0),
    area: numOrNull(s.area),
    area_beyond: numOrNull(s.area_beyond),
    share_beyond: numOrNull(s.share_beyond),
    max_distance: numOrNull(s.max_distance),
  };
};

export function readSupport(raw: unknown): SupportAnswer {
  const r = obj(raw);
  const surfaces = Array.isArray(r.surfaces) ? r.surfaces.map(readSurface) : r.surface ? [readSurface(r.surface)] : [];
  return { model: readModelRef(r.model), warnings: readWarnings(r.warnings), threshold: num(r.threshold, 0), d_max: num(r.d_max, 0), recorded: r.recorded === true, surfaces };
}

/** `POST …/support` without a surface: the entries of every surface, no vertices. */
export const loadSupport = async (id: number, threshold: number | null, wireframeId?: number): Promise<SupportAnswer> =>
  readSupport(await api.post(`${IMPLICIT_API}/models/${id}/support`, { ...(threshold !== null ? { threshold } : {}), ...(wireframeId !== undefined ? { wireframe_id: wireframeId } : {}) }));

export interface SupportValues {
  threshold: number;
  surface: SupportSurface | null;
  warnings: Warning[];
  recorded: boolean;
  /** m, the order of the vertices of the wireframe; NaN — no data points */
  values: Float32Array;
}

/** The body of the f4 answer and the header: `values` (a copy aligned to 4 bytes) and what the header says. */
export function readSupportF4(body: ArrayBuffer, header: string | null): SupportValues {
  if (body.byteLength % 4 !== 0) throw new CoreError(0, "bad_answer", "Ядро вернуло карту опоры неверной длины.", "Повторите расчёт; если повторится — обновите программу.");
  let head: Record<string, unknown> = {};
  try {
    head = obj(JSON.parse(header ?? "{}"));
  } catch {
    head = {};
  }
  return {
    threshold: num(head.threshold, 0),
    surface: head.surface ? readSurface(head.surface) : null,
    warnings: readWarnings(head.warnings),
    recorded: head.recorded === true,
    values: new Float32Array(body.slice(0)),
  };
}

/** `POST …/support {wireframe_id, threshold, format: "f4"}`: the distances of the vertices of one surface. */
export async function loadSupportValues(id: number, wireframeId: number, threshold: number | null): Promise<SupportValues> {
  const r = await callBytes("POST", `${IMPLICIT_API}/models/${id}/support`, { wireframe_id: wireframeId, format: "f4", ...(threshold !== null ? { threshold } : {}) });
  return readSupportF4(await r.arrayBuffer(), r.headers.get("X-BF-Support"));
}
