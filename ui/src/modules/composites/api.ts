/**
 * The client of `/api/v1/composites` (ADR 0018 §11; errors `{code, message, hint}`) and of what a tool window reads
 * about its source: sets, the interval tables of a set with their fields, holes, wireframes. Jobs: `POST /run`,
 * `/coding`, `/merge`, `/coords`, `/topcut`, `/equivalent`, `/declus`, `/ore`, `/conditions` → `{source, id}`;
 * 409 `table_exists` — the window asks «Заменить?» in its own window and repeats with `replace: true`, the
 * assistant repeats with the free name the core offers (« (2)»). 422 — a value out of the ranges of 8.2.2.
 * Previews: `POST /{op}/preview {params, hole_id}` — synchronous, one hole; `createPreviewer` sends the newest
 * request after a short pause and drops an answer that a newer request has overtaken.
 */
import { api, CoreError } from "../../shell/api";

// ---- source of a tool ----

export interface SetRow {
  id: number;
  name: string;
  holes: number;
}

export interface TableRow {
  /** The key of the table in `/drillholes/sets/{id}/tables/{table}` (the id of `dh_tables` as text). */
  table: string;
  kind: string;
  name: string;
  rows: number;
  rev: number;
  /** A result of sample processing (`GET …/tables` → `origin`, ADR 0018 §2). */
  origin?: { result_id: number; op: string; stale: boolean; reason: string | null } | null;
}

export interface FieldRow {
  key: string;
  name: string;
  kind: "number" | "text";
  unit: string | null;
}

export interface HoleRow {
  id: number;
  name: string;
}

export interface WireframeRow {
  id: number;
  name: string;
  folder?: string | null;
}

export async function loadSets(): Promise<SetRow[]> {
  return (await api.get<{ sets: SetRow[] }>("/drillholes/sets")).sets ?? [];
}

/** The interval tables of a set (assay first): the source of every tool. */
export async function loadTables(setId: number): Promise<TableRow[]> {
  const r = await api.get<{ tables: TableRow[] }>(`/drillholes/sets/${setId}/tables`);
  return (r.tables ?? []).filter((t) => t.kind === "assay" || t.kind === "interval");
}

/** The fields of a table without HOLE, FROM, TO and the source columns. */
export async function loadFields(setId: number, table: string): Promise<FieldRow[]> {
  const r = await api.get<{ columns: (FieldRow & { role?: string })[] }>(`/drillholes/sets/${setId}/tables/${encodeURIComponent(table)}?limit=1`);
  return (r.columns ?? []).filter((c) => c.role !== "fixed" && c.role !== "source").map((c) => ({ key: c.key, name: c.name, kind: c.kind === "number" ? "number" : "text", unit: c.unit ?? null }));
}

export const HOLES_LIMIT = 1000;

/** The holes of a set by the collar table (first 1 000 — the preview picks one). */
export async function loadHoles(setId: number): Promise<HoleRow[]> {
  const r = await api.get<{ rows: { hole_id: number; hole: string }[] }>(`/drillholes/sets/${setId}/tables/collar?limit=${HOLES_LIMIT}`);
  return (r.rows ?? []).map((x) => ({ id: x.hole_id, name: x.hole }));
}

export async function loadWireframes(): Promise<WireframeRow[]> {
  return ((await api.get<{ wireframes: WireframeRow[] }>("/wireframes")).wireframes ?? []).map((w) => ({ id: w.id, name: w.name, folder: w.folder ?? null }));
}

/** A table given by its key or by its name (the assistant names tables) → the key; unknown — null. */
export function resolveTable(tables: readonly TableRow[], given: string): string | null {
  const t = given.trim();
  const hit = tables.find((x) => x.table === t) ?? tables.find((x) => x.name === t) ?? tables.find((x) => x.name.toLowerCase() === t.toLowerCase());
  return hit?.table ?? null;
}

// ---- results ----

/** A row of `GET /composites/results?set_id=` (core `composites/results.py::list_results`). */
export interface ResultRow {
  id: number;
  set_id: number;
  table_id: number | null;
  /** the name of the result table; null — fields written in place or a report only */
  table: string | null;
  op: string;
  op_title: string;
  makes_table: boolean;
  name: string;
  params: Record<string, unknown>;
  inputs: Record<string, unknown>;
  sha256: string;
  algo: string;
  report: Record<string, unknown>;
  /** columns written in place */
  written: string[];
  job_id: number | null;
  created_at: string | null;
  stale: boolean;
  /** «изменена таблица „Пробы“», «каркас удалён»; null — fresh */
  reason: string | null;
}

export async function loadResults(setId: number): Promise<ResultRow[]> {
  return (await api.get<{ results: ResultRow[] }>(`/composites/results?set_id=${setId}`)).results ?? [];
}

export const deleteResult = (id: number): Promise<unknown> => api.del(`/composites/results/${id}`);

export interface Started {
  source: string;
  id: number;
}

export const rebuildResult = (id: number): Promise<Started> => api.post(`/composites/results/${id}/rebuild`, {});

export const exportProtocol = (id: number, body: { format: string; holes?: string[] }): Promise<Started> => api.post(`/composites/results/${id}/protocol`, body);

export const editOre = (id: number, body: { hole: string; action: string; from: number; to: number }): Promise<Record<string, unknown>> => api.post(`/composites/results/${id}/edit`, body);

// ---- jobs ----

/** What to do with 409 `table_exists`: replace the table of that name, take the free name, or nothing. */
export type OnExists = (name: string, suggested: string | null) => Promise<"replace" | "rename" | "cancel">;

/** «Композиты 2 м» → «Композиты 2 м (2)»; «… (2)» → «… (3)». */
export function nextName(name: string): string {
  const m = /^(.*) \((\d+)\)$/.exec(name);
  return m ? `${m[1]} (${Number(m[2]) + 1})` : `${name} (2)`;
}

/**
 * Starts the job of a tool. 409 `table_exists {name, suggested?}` → `onExists`: «replace» repeats with
 * `replace: true`, «rename» — with the free name; null — cancelled, nothing started.
 */
export async function startTool(endpoint: string, body: Record<string, unknown>, onExists: OnExists): Promise<Started | null> {
  try {
    return await api.post<Started>(`/composites${endpoint}`, body);
  } catch (e) {
    if (!(e instanceof CoreError) || e.status !== 409 || e.code !== "table_exists") throw e;
    const name = String(e.body?.name ?? body.name ?? "");
    const suggested = typeof e.body?.suggested === "string" ? e.body.suggested : null;
    const what = await onExists(name, suggested);
    if (what === "cancel") return null;
    const again = what === "replace" ? { ...body, replace: true } : { ...body, name: suggested ?? nextName(name) };
    return api.post<Started>(`/composites${endpoint}`, again);
  }
}

/** The assistant never replaces: it takes the free name (ADR 0018 §10). */
export const renameOnExists: OnExists = async () => "rename";

// ---- previews ----

/** A column of intervals of one hole: FROM, TO and the values by field. */
export interface IntervalColumns {
  from: number[];
  to: number[];
  values: Record<string, (number | string | null)[]>;
}

/** «До/после» of one hole or of the set: count, total length, mean and metal Σ l·g by field. */
export interface Summary {
  count: number;
  length: number;
  fields: Record<string, { mean: number | null; metal: number | null }>;
}

export interface PreviewAnswer {
  hole: string;
  samples: IntervalColumns;
  result: IntervalColumns;
  summary: { before: Summary; after: Summary } | null;
  /** The summary of the set (≤ 200 тыс. проб), otherwise null — after the job, from its report. */
  set_summary: { before: Summary; after: Summary } | null;
  /** Lines of the log of the kernel for this hole (dropped remainders, overlaps…). */
  notes: string[];
}

const EMPTY_COLUMNS: IntervalColumns = { from: [], to: [], values: {} };

function columns(raw: unknown): IntervalColumns {
  const r = (raw ?? {}) as Partial<IntervalColumns>;
  return { from: Array.isArray(r.from) ? r.from : [], to: Array.isArray(r.to) ? r.to : [], values: r.values && typeof r.values === "object" ? r.values : {} };
}

/** The answer of a preview made safe to draw (a part the core leaves out is empty). */
export function toPreview(raw: Record<string, unknown>): PreviewAnswer {
  return {
    hole: String(raw.hole ?? ""),
    samples: raw.samples ? columns(raw.samples) : EMPTY_COLUMNS,
    result: raw.result ? columns(raw.result) : EMPTY_COLUMNS,
    summary: (raw.summary as PreviewAnswer["summary"]) ?? null,
    set_summary: (raw.set_summary as PreviewAnswer["set_summary"]) ?? null,
    notes: Array.isArray(raw.notes) ? raw.notes.map(String) : [],
  };
}

export async function preview(op: string, params: Record<string, unknown>, holeId: number): Promise<PreviewAnswer> {
  return toPreview(await api.post<Record<string, unknown>>(`/composites/${op}/preview`, { params, hole_id: holeId }));
}

export const PREVIEW_DELAY_MS = 150;

export interface Previewer {
  /** A new request; an earlier one that has not answered yet is dropped. */
  request(op: string, params: Record<string, unknown>, holeId: number): void;
  /** No more answers are applied. */
  stop(): void;
}

type Clock = { set: (fn: () => void, ms: number) => unknown; clear: (t: unknown) => void };
const REAL_CLOCK: Clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };

/**
 * The preview of a window (criterion 12.26: ≤ 300 ms after an edit): the request goes `delay` ms after the last
 * edit; only the answer of the newest request is applied — an overtaken one is dropped, as is everything after
 * `stop`. The same request twice in a row is sent once.
 */
export function createPreviewer<T = PreviewAnswer>(
  apply: (answer: T) => void,
  fail: (e: unknown) => void,
  load: (op: string, params: Record<string, unknown>, holeId: number) => Promise<T> = preview as unknown as (op: string, params: Record<string, unknown>, holeId: number) => Promise<T>,
  delay = PREVIEW_DELAY_MS,
  clock: Clock = REAL_CLOCK,
): Previewer {
  let seq = 0;
  let alive = true;
  let timer: unknown = null;
  let last = "";
  return {
    request(op, params, holeId) {
      const key = JSON.stringify([op, params, holeId]);
      if (key === last) return;
      last = key;
      const mine = ++seq;
      if (timer !== null) clock.clear(timer);
      timer = clock.set(() => {
        timer = null;
        load(op, params, holeId).then(
          (a) => {
            if (alive && mine === seq) apply(a);
          },
          (e: unknown) => {
            if (alive && mine === seq) fail(e);
          },
        );
      }, delay);
    },
    stop() {
      alive = false;
      if (timer !== null) clock.clear(timer);
      timer = null;
    },
  };
}
