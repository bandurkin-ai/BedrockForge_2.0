/**
 * The client of `/api/v1/intercepts` — every form as docs/progress/e15-api.md (the contract; ADR 0021 §10; errors
 * `{code, message, hint, param?}` in Russian, `param` — a key of the body or «orientation.0.alpha»):
 *
 * - `POST /tt`, `POST /run` — the body of `interceptBody` → `{source, id}` (a job, queue `heavy`); `/run` — 409
 *   `table_exists {name, suggested, lost}` («Заменить?», again with `replace: true` or `name: suggested`); `/tt` — 409
 *   `column_exists {param, name}`; 422 `bad_param` / `no_orientation` / `not_ready`; 404; 423 read-only.
 * - `POST /tt/preview {params, hole_id}` → `{hole, rows: {from, to, length, TT, TT_REL, TT_FLAG, TT_SRC, ALPHA,
 *   DIPDIR, HOLE_THETA, HOLE_AZ}, counts: {src, flags: {"1", "2", "4", "8"}}, mesh_skipped, set_summary, notes}`.
 * - `POST /run/preview {params, hole_id}` → `{hole, unit, samples: {from, to, values, ore}, candidates: [{from, to,
 *   length, tt, grade, kind, accepted, reason}], result: {from, to, values}, summary, set_summary, notes}`;
 *   `summary` / `set_summary` — the form of `report` (`counts, rows, intercepts, included, length, tt, len_g, mean,
 *   waste_len, miss_len, excluded, skipped`; the core of 15.12).
 * - `POST /orientation/plane {wireframe_ids}` → `{alpha, dipdir, spread, triangles, area}` («Взять с каркаса»).
 * - `POST /results/{id}/rebuild` — through `composites/api.rebuildResult` (the prefix by the operation).
 * - the sources of the block: `GET /drillholes/sets/{id}/tables` (kinds `assay`, `interval`, `event` — замеры керна),
 *   `GET …/tables/{table}?limit=1` (fields), `GET /intercepts/codes?set_id=&table=&column=` (`{codes, total,
 *   truncated}` — every code of a field, sorted, up to 1 000), `GET /wireframes`.
 */
import { api } from "../../shell/api";
import { startTool, type OnExists, type Started, type TableRow } from "../composites/api";
import { INTERCEPTS_API } from "./params";

export const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

// ---- jobs ----

export const startTt = (body: Record<string, unknown>, onExists: OnExists): Promise<Started | null> => startTool("/tt", body, onExists, INTERCEPTS_API);
/** `/run` with `replace: false` (the contract); «Заменить» repeats it with `replace: true`. */
export const startRun = (body: Record<string, unknown>, onExists: OnExists): Promise<Started | null> => startTool("/run", { ...body, replace: false }, onExists, INTERCEPTS_API);

// ---- the sources of the block «Ориентировка» ----

/** Tables that may hold codes or core measurements: interval tables and tables of events. */
export async function loadOrientTables(setId: number): Promise<TableRow[]> {
  const r = await api.get<{ tables: TableRow[] }>(`/drillholes/sets/${setId}/tables`);
  return (r.tables ?? []).filter((t) => t.kind === "assay" || t.kind === "interval" || t.kind === "event");
}

/** The codes of a domain field for «Заполнить кодами поля»; `total` — every distinct code, `truncated` — more than given. */
export interface CodeList {
  codes: string[];
  total: number;
  truncated: boolean;
}

/** Every distinct code of a field (`GET /intercepts/codes`: written as the core writes a domain code, sorted, ≤ 1 000). */
export async function loadCodes(setId: number, table: string, column: string): Promise<CodeList> {
  const q = `set_id=${setId}&table=${encodeURIComponent(table)}&column=${encodeURIComponent(column)}`;
  const r = await api.get<{ codes?: unknown; total?: unknown; truncated?: unknown }>(`${INTERCEPTS_API}/codes?${q}`);
  const codes = (Array.isArray(r.codes) ? r.codes : []).filter((x): x is string => typeof x === "string" && x.trim() !== "");
  const total = typeof r.total === "number" && Number.isFinite(r.total) ? Math.max(r.total, codes.length) : codes.length;
  return { codes, total, truncated: r.truncated === true && total > codes.length };
}

/** «Кодов в поле: 12; …» / «Кодов в поле: 1 500, показаны 1 000 из 1 500; …» — the note of «Заполнить кодами поля». */
export function codesNote(got: CodeList): string {
  if (!got.codes.length) return "В поле нет кодов";
  const n = (x: number) => x.toLocaleString("ru-RU");
  const head = got.truncated ? `Кодов в поле: ${n(got.total)}, показаны ${n(got.codes.length)} из ${n(got.total)}` : `Кодов в поле: ${n(got.codes.length)}`;
  return `${head}; α и Aп новых строк — введите`;
}

export interface Plane {
  alpha: number;
  dipdir: number;
  /** the spread of the normals, ° (null — not given) */
  spread: number | null;
  triangles: number | null;
  /** м² */
  area: number | null;
}

/** The mean plane of wireframes (`POST /orientation/plane`) — «Взять с каркаса». */
export async function meanPlane(wireframeIds: number[]): Promise<Plane> {
  const r = await api.post<Record<string, unknown>>(`${INTERCEPTS_API}/orientation/plane`, { wireframe_ids: wireframeIds });
  return { alpha: Number(r.alpha), dipdir: Number(r.dipdir), spread: numOrNull(r.spread), triangles: numOrNull(r.triangles), area: numOrNull(r.area) };
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
  /** the counts of the whole table (≤ 200 000 rows), else null — after the job, from its report */
  setCounts: Counts | null;
  notes: string[];
}

export interface Counts {
  /** TT_SRC → rows */
  bySrc: Record<string, number>;
  /** bit → rows with it */
  byFlag: Record<number, number>;
}

/** `counts` of the contract (`{src: {TT_SRC: n}, flags: {"1": n, …}}`); not an object — null. */
export function toCounts(raw: unknown): Counts | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as { src?: unknown; flags?: unknown };
  const bySrc: Record<string, number> = {};
  const byFlag: Record<number, number> = {};
  if (c.src && typeof c.src === "object") for (const [k, n] of Object.entries(c.src)) if (typeof n === "number") bySrc[k] = n;
  if (c.flags && typeof c.flags === "object") for (const [k, n] of Object.entries(c.flags)) if (typeof n === "number" && Number.isFinite(Number(k))) byFlag[Number(k)] = n;
  return { bySrc, byFlag };
}

const at = (col: unknown, i: number): unknown => (Array.isArray(col) ? col[i] : undefined);

/**
 * The answer of `POST /intercepts/tt/preview` (e15-api.md: `rows` — columns `from, to, length, TT, TT_REL, TT_FLAG,
 * TT_SRC, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ`) made safe to draw: a `null` stays empty (not zero), a row without
 * FROM or TO is not drawn; TT_REL is the core's (computed here only when the core leaves it out); the counts are the
 * core's when given, else counted from the rows.
 */
export function toTtPreview(raw: Record<string, unknown>): TtPreview {
  const c = (raw.rows && typeof raw.rows === "object" ? raw.rows : {}) as Record<string, unknown>;
  const from = Array.isArray(c.from) ? c.from : [];
  const rows: TtRow[] = [];
  for (let i = 0; i < from.length; i += 1) {
    const f = numOrNull(from[i]);
    const t = numOrNull(at(c.to, i));
    if (f === null || t === null) continue;
    const length = numOrNull(at(c.length, i)) ?? (t >= f ? t - f : null);
    const tt = numOrNull(at(c.TT, i));
    const src = at(c.TT_SRC, i);
    rows.push({
      from: f,
      to: t,
      length,
      tt,
      ratio: c.TT_REL !== undefined ? numOrNull(at(c.TT_REL, i)) : tt !== null && length !== null && length > 0 ? tt / length : null,
      alpha: numOrNull(at(c.ALPHA, i)),
      dipdir: numOrNull(at(c.DIPDIR, i)),
      theta: numOrNull(at(c.HOLE_THETA, i)),
      azimuth: numOrNull(at(c.HOLE_AZ, i)),
      src: typeof src === "string" ? src : "",
      flag: numOrNull(at(c.TT_FLAG, i)) ?? 0,
    });
  }
  const given = toCounts(raw.counts);
  const own: Counts = { bySrc: {}, byFlag: {} };
  if (!given)
    for (const r of rows) {
      if (r.src) own.bySrc[r.src] = (own.bySrc[r.src] ?? 0) + 1;
      for (const b of FLAG_BITS) if (r.flag & b.bit) own.byFlag[b.bit] = (own.byFlag[b.bit] ?? 0) + 1;
    }
  const counts = given ?? own;
  return { hole: String(raw.hole ?? ""), rows, bySrc: counts.bySrc, byFlag: counts.byFlag, meshSkipped: raw.mesh_skipped === true, setCounts: toCounts(raw.set_summary), notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [] };
}

export async function ttPreview(params: Record<string, unknown>, holeId: number): Promise<TtPreview> {
  return toTtPreview(await api.post<Record<string, unknown>>(`${INTERCEPTS_API}/tt/preview`, { params, hole_id: holeId }));
}

// ---- the preview of «Рудные пересечения» ----

/** Columns of intervals: FROM, TO and the values by name (`charts/column` draws them). */
export interface RunColumns {
  from: (number | null)[];
  to: (number | null)[];
  values: Record<string, (number | string | null)[]>;
}

export interface Candidate {
  from: number;
  to: number;
  length: number | null;
  /** null — TT empty (no orientation) */
  tt: number | null;
  grade: number | null;
  /** «основное» / «в том числе» */
  kind: string;
  accepted: boolean;
  /** «короче L_min», «ИМ меньше m_min»; null — accepted */
  reason: string | null;
}

/** `summary` / `set_summary` of `run/preview` — the form of `report` of the record (15.12); left out — null. */
export interface RunSummary {
  intercepts: number | null;
  included: number | null;
  length: number | null;
  len_g: number | null;
  /** Σ TT of the main intercepts; null — none has TT */
  tt: number | null;
  /** len_g / length; null — no intercepts */
  mean: number | null;
  waste_len: number | null;
  miss_len: number | null;
  rows: number | null;
  excluded: number | null;
  skipped: number | null;
}

export interface RunPreview {
  hole: string;
  /** the unit of the grade field («г/т»); null — not given */
  unit: string | null;
  samples: RunColumns;
  /** g ≥ c per sample (step 1 of 8.5.3); null — the grade is empty */
  ore: (boolean | null)[];
  candidates: Candidate[];
  result: RunColumns;
  summary: RunSummary | null;
  /** the summary of the set (≤ 200 000 samples), else null — after the job, from its report */
  setSummary: RunSummary | null;
  notes: string[];
}

const cell = (v: unknown): number | string | null => (typeof v === "number" ? (Number.isFinite(v) ? v : null) : typeof v === "string" ? v : null);

function runColumns(raw: unknown): RunColumns {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const values: Record<string, (number | string | null)[]> = {};
  if (r.values && typeof r.values === "object") for (const [k, v] of Object.entries(r.values)) if (Array.isArray(v)) values[k] = v.map(cell);
  return { from: Array.isArray(r.from) ? r.from.map(numOrNull) : [], to: Array.isArray(r.to) ? r.to.map(numOrNull) : [], values };
}

function runSummary(raw: unknown): RunSummary | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const n = (k: string) => numOrNull(r[k]);
  return { intercepts: n("intercepts"), included: n("included"), length: n("length"), len_g: n("len_g"), tt: n("tt"), mean: n("mean"), waste_len: n("waste_len"), miss_len: n("miss_len"), rows: n("rows"), excluded: n("excluded"), skipped: n("skipped") };
}

/** The answer of `POST /intercepts/run/preview` (e15-api.md) made safe to draw: what is left out is empty, never 0. */
export function toRunPreview(raw: Record<string, unknown>): RunPreview {
  const samples = runColumns(raw.samples);
  const oreRaw = (raw.samples as { ore?: unknown } | undefined)?.ore;
  const candidates: Candidate[] = [];
  for (const c of Array.isArray(raw.candidates) ? raw.candidates : []) {
    if (!c || typeof c !== "object") continue;
    const x = c as Record<string, unknown>;
    const from = numOrNull(x.from);
    const to = numOrNull(x.to);
    if (from === null || to === null) continue;
    candidates.push({ from, to, length: numOrNull(x.length), tt: numOrNull(x.tt), grade: numOrNull(x.grade), kind: typeof x.kind === "string" ? x.kind : "основное", accepted: x.accepted === true, reason: typeof x.reason === "string" && x.reason ? x.reason : null });
  }
  return {
    hole: String(raw.hole ?? ""),
    unit: typeof raw.unit === "string" && raw.unit.trim() ? raw.unit : null,
    samples,
    ore: Array.isArray(oreRaw) ? oreRaw.map((v) => (typeof v === "boolean" ? v : null)) : [],
    candidates,
    result: runColumns(raw.result),
    summary: runSummary(raw.summary),
    setSummary: runSummary(raw.set_summary),
    notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [],
  };
}

export async function runPreview(params: Record<string, unknown>, holeId: number): Promise<RunPreview> {
  return toRunPreview(await api.post<Record<string, unknown>>(`${INTERCEPTS_API}/run/preview`, { params, hole_id: holeId }));
}
