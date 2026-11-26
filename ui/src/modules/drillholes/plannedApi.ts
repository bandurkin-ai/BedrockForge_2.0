/**
 * The client of the planned holes (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; docs/progress/e19-api.md §10): `/api/v1/planned_holes`. The forms are
 * the real answers of the core (`core/tests/data/implicit/contract_answers.json`, keys `planned_*`), read by readers that keep a gap as `null` — never 0
 * (8.0.1) — and take a missing key as «nothing». Errors are `{code, message, hint, param?}` in Russian.
 *
 * - `GET /planned_holes[?trajectory=true]`, `POST`, `GET/PUT/DELETE /{id}` — the rows: `{id, name, version, meta, trajectory {depth, x, y, z} every 1 m,
 *   target_depth}`; `PUT {expected_version, …}` — another version stored is a 409 `version_conflict {version}`;
 * - `POST /{id}/evaluate {model_id | mesh_ids, set_id?, point?}` — the forecast by the bodies and the nearest holes of a set;
 * - `POST /{id}/compare {set_id, hole}` — «План / факт», every 1 m;
 * - `POST /{id}/export {format: csv | dxf, path, overwrite?}` — the files (CSV: `path` is an existing folder; DXF — the file).
 * No React.
 */
import { api } from "../../shell/api";
import { isNum, numOrNull, obj, refusalOf, str } from "../estimation/api";

export { refusalOf };
export type { Refusal } from "../blocks/report/api";

export const PLANNED_API = "/planned_holes";
/** The kind of the event `implicit.changed` the planned holes publish. */
export const PLANNED_KIND = "planned_hole";

export type Triple = [number, number, number];
export const METHODS = ["minimum_curvature", "tangent", "balanced_tangent", "average_angle"] as const;
export type Method = (typeof METHODS)[number];
export const METHOD_TITLES: Record<Method, string> = {
  minimum_curvature: "Минимальная кривизна",
  tangent: "Касательный",
  balanced_tangent: "Сбалансированный касательный",
  average_angle: "Средний угол",
};

const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? ([v[0], v[1], v[2]] as Triple) : null);
const num = (v: unknown, dflt: number): number => (isNum(v) ? v : dflt);

// ---- the rows ----

export interface PlannedMeta {
  collar: Triple;
  azimuth: number;
  dip: number;
  length: number;
  /** the point the hole goes through; null — a straight hole or one that turns */
  target: Triple | null;
  /** deg per 100 m; null — none */
  change: { azimuth: number; dip: number } | null;
  method: Method;
  set_id: number | null;
}

/** The path every 1 m and at the end, float64 (the layer takes it as it is). */
export interface Trajectory {
  depth: Float64Array;
  x: Float64Array;
  y: Float64Array;
  z: Float64Array;
}

export interface PlannedRow {
  id: number;
  name: string;
  version: number;
  meta: PlannedMeta;
  /** null — the answer has none (the list without `?trajectory=true`) */
  trajectory: Trajectory | null;
  /** the depth of the target on the path; null — no target */
  target_depth: number | null;
}

export function readTrajectory(raw: unknown): Trajectory | null {
  const t = obj(raw);
  const cols = ["depth", "x", "y", "z"].map((k) => t[k]);
  if (!cols.every((c) => Array.isArray(c))) return null;
  const n = (cols[0] as unknown[]).length;
  if (!n || !cols.every((c) => (c as unknown[]).length === n && (c as unknown[]).every(isNum))) return null; // a gap in a path is not drawn as 0
  const [depth, x, y, z] = cols.map((c) => Float64Array.from(c as number[]));
  return { depth, x, y, z };
}

const methodOf = (v: unknown): Method => (METHODS as readonly unknown[]).includes(v) ? (v as Method) : "minimum_curvature";

export function readMeta(raw: unknown): PlannedMeta {
  const m = obj(raw);
  const c = obj(m.change);
  return {
    collar: triple(m.collar) ?? [0, 0, 0],
    azimuth: num(m.azimuth, 0),
    dip: num(m.dip, 0),
    length: num(m.length, 0),
    target: triple(m.target),
    change: m.change && isNum(c.azimuth) && isNum(c.dip) ? { azimuth: c.azimuth, dip: c.dip } : null,
    method: methodOf(m.method),
    set_id: numOrNull(m.set_id),
  };
}

export function readRow(raw: unknown): PlannedRow {
  const r = obj(raw);
  return { id: num(r.id, 0), name: str(r.name) ?? "", version: num(r.version, 0), meta: readMeta(r.meta), trajectory: readTrajectory(r.trajectory), target_depth: numOrNull(r.target_depth) };
}

export const readRows = (raw: unknown): PlannedRow[] => {
  const list = obj(raw).planned_holes;
  return Array.isArray(list) ? list.map(readRow).filter((r) => r.id > 0) : [];
};

// ---- the forecast and the nearest holes ----

export interface Interval {
  from: number;
  to: number;
  /** null — between the bodies (never a code of 0) */
  code: string | null;
}
export interface CodeLength {
  code: string;
  length: number;
}
export interface Nearest {
  hole: string;
  hole_id: number;
  distance: number;
  depth: number;
}
export interface EvaluateAnswer {
  id: number;
  /** null — the request named no bodies */
  intervals: Interval[] | null;
  codes: CodeLength[] | null;
  /** m of the path inside the bodies; null — no bodies named */
  inside: number | null;
  length: number | null;
  /** null — no set was named */
  nearest: Nearest[] | null;
  target: { point: Triple | null; depth: number | null };
  set_id: number | null;
  journal: string[];
}

const readList = <T>(v: unknown, one: (x: unknown) => T | null): T[] | null => (Array.isArray(v) ? v.map(one).filter((x): x is T => x !== null) : null);

export function readEvaluate(raw: unknown): EvaluateAnswer {
  const a = obj(raw);
  const t = obj(a.target);
  return {
    id: num(a.id, 0),
    intervals: readList(a.intervals, (x) => {
      const i = obj(x);
      return isNum(i.from) && isNum(i.to) ? { from: i.from, to: i.to, code: str(i.code) } : null;
    }),
    codes: readList(a.codes, (x) => {
      const c = obj(x);
      return str(c.code) !== null && isNum(c.length) ? { code: c.code as string, length: c.length } : null;
    }),
    inside: numOrNull(a.inside),
    length: numOrNull(a.length),
    nearest: readList(a.nearest, (x) => {
      const n = obj(x);
      return str(n.hole) !== null && isNum(n.distance) ? { hole: n.hole as string, hole_id: num(n.hole_id, 0), distance: n.distance, depth: num(n.depth, 0) } : null;
    }),
    target: { point: triple(t.point), depth: numOrNull(t.depth) },
    set_id: numOrNull(a.set_id),
    journal: Array.isArray(a.journal) ? a.journal.filter((x): x is string => typeof x === "string") : [],
  };
}

// ---- «План / факт» ----

export interface CompareRow {
  depth: number;
  dx: number;
  dy: number;
  dz: number;
  d: number;
}
export interface CompareAnswer {
  id: number;
  set_id: number | null;
  rows: CompareRow[];
  /** the largest deviation, m; null — nothing compared */
  max: number | null;
  max_depth: number | null;
  length: { planned: number | null; actual: number | null; compared: number | null };
  hole: string;
  hole_id: number;
}

export function readCompare(raw: unknown): CompareAnswer {
  const a = obj(raw);
  const l = obj(a.length);
  return {
    id: num(a.id, 0),
    set_id: numOrNull(a.set_id),
    rows: readList(a.rows, (x) => {
      const r = obj(x);
      return [r.depth, r.dx, r.dy, r.dz, r.d].every(isNum) ? { depth: r.depth as number, dx: r.dx as number, dy: r.dy as number, dz: r.dz as number, d: r.d as number } : null;
    }) ?? [],
    max: numOrNull(a.max),
    max_depth: numOrNull(a.max_depth),
    length: { planned: numOrNull(l.planned), actual: numOrNull(l.actual), compared: numOrNull(l.compared) },
    hole: str(a.hole) ?? "",
    hole_id: num(a.hole_id, 0),
  };
}

// ---- «Экспорт» ----

export type ExportFormat = "csv" | "dxf";
export interface ExportAnswer {
  format: ExportFormat;
  files: string[];
  points: number;
  size: number;
}

export const readExport = (raw: unknown): ExportAnswer => {
  const a = obj(raw);
  return { format: a.format === "dxf" ? "dxf" : "csv", files: Array.isArray(a.files) ? a.files.filter((f): f is string => typeof f === "string") : [], points: num(a.points, 0), size: num(a.size, 0) };
};

// ---- the calls ----

export type PlannedBody = Record<string, unknown>;

export async function listPlanned(withPath = true): Promise<PlannedRow[]> {
  return readRows(await api.get<unknown>(`${PLANNED_API}${withPath ? "?trajectory=true" : ""}`));
}
export const getPlanned = async (id: number): Promise<PlannedRow> => readRow(await api.get<unknown>(`${PLANNED_API}/${id}`));
export const createPlanned = async (body: PlannedBody): Promise<PlannedRow> => readRow(await api.post<unknown>(PLANNED_API, body));
export const updatePlanned = async (id: number, expectedVersion: number, body: PlannedBody): Promise<PlannedRow> => readRow(await api.put<unknown>(`${PLANNED_API}/${id}`, { expected_version: expectedVersion, ...body }));
export const deletePlanned = async (id: number): Promise<number> => num(obj(await api.del<unknown>(`${PLANNED_API}/${id}`)).deleted, id);
export const evaluatePlanned = async (id: number, body: PlannedBody): Promise<EvaluateAnswer> => readEvaluate(await api.post<unknown>(`${PLANNED_API}/${id}/evaluate`, body));
export const comparePlanned = async (id: number, body: PlannedBody): Promise<CompareAnswer> => readCompare(await api.post<unknown>(`${PLANNED_API}/${id}/compare`, body));
export const exportPlanned = async (id: number, body: PlannedBody): Promise<ExportAnswer> => readExport(await api.post<unknown>(`${PLANNED_API}/${id}/export`, body));
