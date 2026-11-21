/**
 * The client of `/api/v1/intercepts` (ADR 0021 §10; errors `{code, message, hint, param?}` in Russian). Every JSON
 * form the interface relies on is here (and in `params.ts` for the body), to be checked against the core:
 *
 * - `POST /tt`, `POST /run` — body of `interceptBody` → `{source, id}` (a job, queue `heavy`); 409 `table_exists
 *   {name, suggested?, lost?}` (`/run`); 422 `bad_param {param}`, 422 `no_orientation`; 404; 423 read-only.
 * - `POST /tt/preview {params, hole_id}` → `TtPreview` (one hole, synchronous): `hole`, `intervals {from, to,
 *   values: {LENGTH?, TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ}}`, `counts? {src: {TT_SRC: n}, flags:
 *   {"1": n, "2": n, "4": n, "8": n}}`, `mesh_skipped?: boolean` (more than 2 млн triangles), `notes?: string[]`.
 * - `POST /run/preview {params, hole_id}` — the window of 15.16 reads it (`runPreviewRaw`).
 * - `POST /orientation/plane {wireframe_ids}` → `{alpha, dipdir, spread}` («Взять с каркаса»).
 * - `POST /results/{id}/rebuild` — through `composites/api.rebuildResult` (the prefix by the operation).
 * - the sources of the block: `GET /drillholes/sets/{id}/tables` (kinds `assay`, `interval`, `event` — замеры керна),
 *   `GET …/tables/{table}?limit=1` (fields), `GET …/tables/{table}/stats?column=` (`top` — codes of a field),
 *   `GET /wireframes`.
 */
import { api } from "../../shell/api";
import { startTool, type OnExists, type Started, type TableRow } from "../composites/api";
import { INTERCEPTS_API } from "./params";

// ---- jobs ----

export const startTt = (body: Record<string, unknown>, onExists: OnExists): Promise<Started | null> => startTool("/tt", body, onExists, INTERCEPTS_API);
export const startRun = (body: Record<string, unknown>, onExists: OnExists): Promise<Started | null> => startTool("/run", body, onExists, INTERCEPTS_API);

// ---- the sources of the block «Ориентировка» ----

/** Tables that may hold codes or core measurements: interval tables and tables of events. */
export async function loadOrientTables(setId: number): Promise<TableRow[]> {
  const r = await api.get<{ tables: TableRow[] }>(`/drillholes/sets/${setId}/tables`);
  return (r.tables ?? []).filter((t) => t.kind === "assay" || t.kind === "interval" || t.kind === "event");
}

/** The values met in a field (`stats.top`, most frequent first); a numeric field gives none. */
export async function loadCodes(setId: number, table: string, column: string): Promise<string[]> {
  const r = await api.get<{ top?: { value: unknown }[] }>(`/drillholes/sets/${setId}/tables/${encodeURIComponent(table)}/stats?column=${encodeURIComponent(column)}`);
  return (r.top ?? []).map((x) => (x.value === null || x.value === undefined ? "" : String(x.value))).filter((x) => x.trim());
}

export interface Plane {
  alpha: number;
  dipdir: number;
  /** the spread of the normals, ° (null — not given) */
  spread: number | null;
}

/** The mean plane of wireframes (`POST /orientation/plane`) — «Взять с каркаса». */
export async function meanPlane(wireframeIds: number[]): Promise<Plane> {
  const r = await api.post<{ alpha: number; dipdir: number; spread?: number | null }>(`${INTERCEPTS_API}/orientation/plane`, { wireframe_ids: wireframeIds });
  return { alpha: r.alpha, dipdir: r.dipdir, spread: typeof r.spread === "number" ? r.spread : null };
}

// ---- the preview of «Истинная мощность» ----

/** TT_SRC (ADR 0021 §4): the seven texts of the core, in the order the window counts them. */
export const TT_SOURCES = ["глобальная", "домен", "каркас: пересечение", "каркас: ближайшая", "керн: замер", "керн: ближайший", "нет"] as const;

/** TT_FLAG bits (ADR 0021 §4). */
export const FLAG_BITS: readonly { bit: number; title: string; hint: string }[] = [
  { bit: 1, title: "вдоль тела", hint: "Скважина почти параллельна телу: TT / LENGTH меньше ε_в" },
  { bit: 2, title: "нет ориентировки", hint: "Ориентировка не найдена: TT пусто" },
  { bit: 4, title: "керн не пересчитан", hint: "Замер керна не пересчитан в мировую нормаль (θ < 1°): ALPHA и DIPDIR пусты" },
  { bit: 8, title: "нет траектории", hint: "У скважины нет траектории: TT пусто" },
];

/** 5 → «вдоль тела, керн не пересчитан»; 0 or empty — «». */
export function flagText(flag: number | null): string {
  if (flag === null || !Number.isFinite(flag) || flag <= 0) return "";
  return FLAG_BITS.filter((b) => (flag & b.bit) !== 0)
    .map((b) => b.title)
    .join(", ");
}

export interface TtRow {
  from: number;
  to: number;
  /** null — empty (never 0 in place of empty) */
  length: number | null;
  tt: number | null;
  /** TT / LENGTH; null — TT or LENGTH empty, or LENGTH = 0 */
  ratio: number | null;
  alpha: number | null;
  dipdir: number | null;
  theta: number | null;
  azimuth: number | null;
  src: string;
  flag: number;
}

export interface TtPreview {
  hole: string;
  rows: TtRow[];
  /** TT_SRC → rows */
  bySrc: Record<string, number>;
  /** bit → rows with it */
  byFlag: Record<number, number>;
  /** the orientation by wireframes was not computed in the preview (more than 2 млн triangles) */
  meshSkipped: boolean;
  notes: string[];
}

const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const at = (col: unknown, i: number): unknown => (Array.isArray(col) ? col[i] : undefined);

/**
 * The answer of `POST /intercepts/tt/preview` made safe to draw: what the core leaves out is empty, a NaN or a
 * `null` stays empty (not zero), TT / LENGTH is computed here when the core does not give it; the counts are the
 * core's when given, else counted from the rows.
 */
export function toTtPreview(raw: Record<string, unknown>): TtPreview {
  const iv = (raw.intervals ?? raw.result ?? {}) as { from?: unknown; to?: unknown; values?: Record<string, unknown> };
  const from = Array.isArray(iv.from) ? iv.from : [];
  const to = Array.isArray(iv.to) ? iv.to : [];
  const v = iv.values && typeof iv.values === "object" ? iv.values : {};
  const rows: TtRow[] = [];
  for (let i = 0; i < from.length; i += 1) {
    const f = numOrNull(from[i]);
    const t = numOrNull(to[i]);
    if (f === null || t === null) continue;
    const length = numOrNull(at(v.LENGTH, i)) ?? (t >= f ? t - f : null);
    const tt = numOrNull(at(v.TT, i));
    const given = numOrNull(at(v.TT_RATIO, i));
    rows.push({
      from: f,
      to: t,
      length,
      tt,
      ratio: given ?? (tt !== null && length !== null && length > 0 ? tt / length : null),
      alpha: numOrNull(at(v.ALPHA, i)),
      dipdir: numOrNull(at(v.DIPDIR, i)),
      theta: numOrNull(at(v.HOLE_THETA, i)),
      azimuth: numOrNull(at(v.HOLE_AZ, i)),
      src: typeof at(v.TT_SRC, i) === "string" ? (at(v.TT_SRC, i) as string) : "",
      flag: numOrNull(at(v.TT_FLAG, i)) ?? 0,
    });
  }
  const counts = (raw.counts ?? {}) as { src?: Record<string, unknown>; flags?: Record<string, unknown> };
  const bySrc: Record<string, number> = {};
  const byFlag: Record<number, number> = {};
  if (counts.src && typeof counts.src === "object") {
    for (const [k, n] of Object.entries(counts.src)) if (typeof n === "number") bySrc[k] = n;
  } else {
    for (const r of rows) if (r.src) bySrc[r.src] = (bySrc[r.src] ?? 0) + 1;
  }
  if (counts.flags && typeof counts.flags === "object") {
    for (const [k, n] of Object.entries(counts.flags)) if (typeof n === "number" && Number.isFinite(Number(k))) byFlag[Number(k)] = n;
  } else {
    for (const r of rows) for (const b of FLAG_BITS) if (r.flag & b.bit) byFlag[b.bit] = (byFlag[b.bit] ?? 0) + 1;
  }
  return { hole: String(raw.hole ?? ""), rows, bySrc, byFlag, meshSkipped: raw.mesh_skipped === true, notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [] };
}

export async function ttPreview(params: Record<string, unknown>, holeId: number): Promise<TtPreview> {
  return toTtPreview(await api.post<Record<string, unknown>>(`${INTERCEPTS_API}/tt/preview`, { params, hole_id: holeId }));
}

/** The raw answer of `POST /intercepts/run/preview` (its form — the window of 15.16). */
export const runPreviewRaw = (params: Record<string, unknown>, holeId: number): Promise<Record<string, unknown>> => api.post(`${INTERCEPTS_API}/run/preview`, { params, hole_id: holeId });
