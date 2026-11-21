/**
 * The client of the swath API (ADR 0019 §5, §10; core — дорожка Х 13.18): `GET /stats/swath/sources` — sets with
 * their sample tables, block models with their numeric and category fields and grids; `POST /stats/swath
 * {settings | swath_id}` — the frame of the box, the cuboid, the swaths and the series. A series whose source is
 * gone carries `error` (not a refusal of the whole); 422 `too_many_swaths` (param `width`), `too_many_series`
 * (`series`), `bad_param {param}`, `no_data`. The fields of a sample table come from `GET /stats/fields`
 * (`modules/charts/api.ts`, cached there). The saved swaths are `objects(chart)` kind `swath`, `set_id = null`
 * (`/charts`, 13.13). No React.
 */
import { api, CoreError } from "../../shell/api";
import { errorText } from "../../shell/props";
import { createCalculator, createChart, listCharts, updateChart, type Calculator, type ChartRow } from "../charts/api";
import type { SwathSettings } from "./model";

// ---- sources ----

export interface SourceTable {
  /** `name_key` of the table (what a series keeps) */
  key: string;
  name: string;
  kind: string;
  rows: number;
}

export interface SourceSet {
  id: number;
  name: string;
  /** sample tables (assay first) */
  tables: SourceTable[];
}

export interface ModelNumber {
  name: string;
  unit: string | null;
}

export interface ModelCategory {
  name: string;
  /** ≤ 300 values */
  labels: string[];
  more: boolean;
}

export interface SourceModel {
  id: number;
  name: string;
  numbers: ModelNumber[];
  categories: ModelCategory[];
  /** the parent block, m (dx, dy, dz); null — unknown */
  block: [number, number, number] | null;
  /** the field of density the model names (its report), if any */
  density: string | null;
  /** grades of the model's report, in order (the default field of a series) */
  grades: string[];
}

export interface SwathSources {
  sets: SourceSet[];
  models: SourceModel[];
}

const asList = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** The answer as the windows use it: lists always present, unknown keys dropped. */
export function sourcesView(raw: unknown): SwathSources {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const sets = asList<Record<string, unknown>>(r.sets).flatMap((s) =>
    typeof s?.id === "number" ? [{ id: s.id, name: String(s.name ?? `Набор ${s.id}`), tables: asList<SourceTable>(s.tables).filter((t) => typeof t?.key === "string") }] : [],
  );
  const models = asList<Record<string, unknown>>(r.models).flatMap((m) => {
    if (typeof m?.id !== "number") return [];
    const block = Array.isArray(m.block) && m.block.length === 3 && m.block.every((x) => typeof x === "number" && x > 0) ? (m.block as [number, number, number]) : null;
    return [
      {
        id: m.id,
        name: String(m.name ?? `Модель ${m.id}`),
        numbers: asList<ModelNumber>(m.numbers).filter((x) => typeof x?.name === "string"),
        categories: asList<ModelCategory>(m.categories).filter((x) => typeof x?.name === "string").map((c) => ({ name: c.name, labels: asList<string>(c.labels).filter((l) => typeof l === "string"), more: c.more === true })),
        block,
        density: typeof m.density === "string" ? m.density : null,
        grades: asList<string>(m.grades).filter((g) => typeof g === "string"),
      },
    ];
  });
  return { sets, models };
}

let sourcesCache: Promise<SwathSources> | null = null;

/** The sources of the project (cached until an edit of data or another project). */
export function loadSources(): Promise<SwathSources> {
  if (!sourcesCache) {
    const p = api.get<unknown>("/stats/swath/sources").then(sourcesView);
    p.catch(() => {
      if (sourcesCache === p) sourcesCache = null;
    });
    sourcesCache = p;
  }
  return sourcesCache;
}

export function invalidateSources(): void {
  sourcesCache = null;
}

// ---- the answer of a calculation ----

export interface FrameAnswer {
  azimuth: number;
  dip: number;
  roll: number;
  u: [number, number, number];
  v: [number, number, number];
  w: [number, number, number];
}

export type Pair = [number, number];
export interface Rows3 {
  u: Pair;
  v: Pair;
  w: Pair;
}

export interface SeriesAnswer {
  index: number;
  kind: "blocks" | "samples";
  /** «Модель · AU», «Скважины · AU» */
  name: string;
  field: string | null;
  unit: string | null;
  weight: string;
  /** «объём», «тоннаж», «длина», «число»… */
  weight_name: string;
  weight_unit: string;
  /** the model or set of the series */
  source_id: number | null;
  /** samples: «box», or the number of the model series they were taken inside */
  within?: "box" | number | null;
  counts: Record<string, number>;
  /** per swath: the weighted mean (null — nothing in the swath, never 0), the number of values, their weight */
  mean: (number | null)[];
  count: number[];
  weights: (number | null)[];
  /** (m − m₁) / m₁ · 100 from the first series; null where either is empty */
  deviation?: (number | null)[];
  total: { mean: number | null; count: number; weight: number | null; deviation?: number | null };
  /** the source is gone or the field is missing: the series is not computed, the others are */
  error?: { code: string; message: string; hint?: string } | null;
}

export interface SwathResult {
  direction: SwathSettings["direction"];
  frame: FrameAnswer;
  /** the cuboid on whole swaths along u (what the 3D draws) and the limits of the comparison */
  box: Rows3;
  limits: Rows3;
  custom_limits: boolean;
  start: number;
  width: number;
  count: number;
  centres: number[];
  /** the side of the parent block of the first model along the swaths; null — no model */
  block: number | null;
  series: SeriesAnswer[];
  notes: string[];
  /** by `swath_id`: the swath computed */
  swath?: Pick<ChartRow, "id" | "name" | "version">;
}

/** Where a refusal of the core belongs in the window: `width` (over 2000 swaths — at the width field, Т7.30-1),
 * `series` (over 12), another param, or the whole window (null). */
export function swathError(e: unknown): { text: string; param: string | null } {
  const param = e instanceof CoreError ? (typeof e.body?.param === "string" ? e.body.param : e.code === "too_many_swaths" ? "width" : e.code === "too_many_series" || e.code === "too_much_data" ? "series" : null) : null;
  return { text: errorText(e), param };
}

/** The body of `POST /stats/swath`: the settings (only what changes the numbers), or a saved swath. */
export type SwathBody = { settings: object } | { swath_id: number };

export const computeSwath = (body: SwathBody): Promise<SwathResult> => api.post<SwathResult>("/stats/swath", body);

/** The newest answer only, 300 ms after the last edit (`createCalculator` of the charts). */
export const swathCalculator = (apply: (r: SwathResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void): Calculator<{ body: SwathBody; rev: number }> =>
  createCalculator<{ body: SwathBody; rev: number }, SwathResult>((b) => computeSwath(b.body), apply, fail, busy);

// ---- saved swaths: /charts kind `swath`, set_id null ----

export const listSwaths = (): Promise<{ charts: ChartRow[] }> => listCharts(null, "swath");

export const createSwath = (name: string, settings: SwathSettings): Promise<ChartRow> => createChart({ set_id: null, kind: "swath", name, settings, auto_name: true });

export const updateSwath = updateChart;
