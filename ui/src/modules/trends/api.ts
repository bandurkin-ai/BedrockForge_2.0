/**
 * The client of `/api/v1/geostat/trends` (ТЗ 7.35 Д-15; ADR 0022 §10, §14; docs/progress/e16-api.md «Тренды»; core —
 * `geostat/{trend, trend_store, routes_trend}.py`, 16.16). The forms are those of the core's answers (checked by a
 * run of the core through TestClient, 16.20):
 *
 * - a trend `{id, name, kind: "global", ang1, ang2, ang3, ratios: [1, r2, r3], size, centre: [x, y, z] | null,
 *   color (lower case), origin {type: manual | model | ellipsoid, model_id? (null — the model deleted), structure?
 *   (from 1)}, used, matrix (A = diag(1, 1/r2, 1/r3)·R), version, created_at, updated_at}`;
 * - `GET /trends` → `{trends}` (by name); `GET /trends/{id}` → a trend, 404 `trend_not_found`;
 * - `POST /trends` → `{id, name}`; `PUT /trends/{id}` → `{id, name}` (`{model_id, structure?}` — «Обновить из
 *   модели»); `DELETE /trends/{id}` → `{deleted}`, 409 `in_use {usage}`; `GET /trends/{id}/usage` → `{usage}`.
 *
 * An unknown number of an answer stays `null`, never 0. No React.
 */
import { api, CoreError } from "../../shell/api";
import { GEOSTAT_API } from "../variography/api";

export const TREND_API = `${GEOSTAT_API}/trends`;

export type TrendOriginType = "manual" | "model" | "ellipsoid";

export interface TrendOrigin {
  type: TrendOriginType;
  /** the model of the copy; null — the model was deleted («Обновить из модели» unavailable); absent — not of a model */
  model_id?: number | null;
  /** the structure copied, from 1 */
  structure?: number;
}

export interface Trend {
  id: number;
  name: string;
  kind: "global";
  ang1: number;
  ang2: number;
  ang3: number;
  /** [1, a2 / a1, a3 / a1] */
  ratios: [number, number, number];
  /** the major axis shown, m */
  size: number;
  /** absolute, float64; null — not given (the 3D puts it at the centre of the view) */
  centre: [number, number, number] | null;
  color: string;
  origin: TrendOrigin;
  /** referred to by a consumer (Э18, Э19): not deleted */
  used: boolean;
  /** A, 3 × 3 rows (the only source of A — the core's `trend.matrix`) */
  matrix: number[][];
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

/** A consumer of a trend («Где используется»): a row of `object_refs(dst_id = trend)`. */
export interface TrendUse {
  id: number;
  kind: string;
  name: string;
  role: string;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown, dflt: number): number => (isNum(v) ? v : dflt);

function readCentre(v: unknown): [number, number, number] | null {
  return Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null;
}

export function readOrigin(raw: unknown): TrendOrigin {
  const o = obj(raw);
  const type: TrendOriginType = o.type === "model" || o.type === "ellipsoid" ? o.type : "manual";
  const out: TrendOrigin = { type };
  if ("model_id" in o) out.model_id = isNum(o.model_id) ? o.model_id : null;
  if (isNum(o.structure)) out.structure = o.structure;
  return out;
}

/** A trend of an answer of the core (junk in place of a number — its default, never a made-up 0 for a ratio). */
export function readTrend(raw: unknown): Trend {
  const t = obj(raw);
  const r = Array.isArray(t.ratios) ? t.ratios : [];
  return {
    id: num(t.id, 0),
    name: str(t.name) ?? "",
    kind: "global",
    ang1: num(t.ang1, 0),
    ang2: num(t.ang2, 0),
    ang3: num(t.ang3, 0),
    ratios: [1, num(r[1], 1), num(r[2], 1)],
    size: num(t.size, 100),
    centre: readCentre(t.centre),
    color: str(t.color) ?? "#f46a1a",
    origin: readOrigin(t.origin),
    used: t.used === true,
    matrix: Array.isArray(t.matrix) ? t.matrix.map((row) => (Array.isArray(row) ? row.map((x) => (isNum(x) ? x : NaN)) : [])) : [],
    version: num(t.version, 0),
    created_at: str(t.created_at),
    updated_at: str(t.updated_at),
  };
}

export function readUsage(raw: unknown): TrendUse[] {
  const list = obj(raw).usage;
  return Array.isArray(list) ? list.map((u) => obj(u)).map((u) => ({ id: num(u.id, 0), kind: str(u.kind) ?? "", name: str(u.name) ?? "", role: str(u.role) ?? "" })) : [];
}

/** The body of `POST` / `PUT` (numbers as numbers; the core takes «0,5» too). */
export interface TrendBody {
  name?: string;
  kind?: "global";
  ang1?: number;
  ang2?: number;
  ang3?: number;
  ratios?: [number, number, number];
  size?: number;
  centre?: [number, number, number] | null;
  color?: string;
  origin?: { type: "manual" | "ellipsoid" };
  model_id?: number;
  structure?: number;
}

export const loadTrends = async (): Promise<Trend[]> => {
  const raw = obj(await api.get(TREND_API)).trends;
  return Array.isArray(raw) ? raw.map(readTrend) : [];
};
export const loadTrend = async (id: number): Promise<Trend> => readTrend(await api.get(`${TREND_API}/${id}`));
export const createTrend = (body: TrendBody): Promise<{ id: number; name: string }> => api.post(TREND_API, body);
export const updateTrend = (id: number, body: TrendBody): Promise<{ id: number; name: string }> => api.put(`${TREND_API}/${id}`, body);
export const deleteTrend = (id: number): Promise<{ deleted: number }> => api.del(`${TREND_API}/${id}`);
export const trendUsage = async (id: number): Promise<TrendUse[]> => readUsage(await api.get(`${TREND_API}/${id}/usage`));

/** The consumers of a 409 `in_use` (its body `usage`); [] — another error. */
export function usageOfError(e: unknown): TrendUse[] {
  return e instanceof CoreError && e.code === "in_use" ? readUsage(e.body ?? {}) : [];
}

/** «Оценка AU», «RBF зона 1» — the consumers as a line of text (up to `max`, then «и ещё N»). */
export function usageText(list: readonly TrendUse[], max = 5): string {
  const names = list.slice(0, max).map((u) => `«${u.name}»`);
  return list.length > max ? `${names.join(", ")} и ещё ${list.length - max}` : names.join(", ");
}
