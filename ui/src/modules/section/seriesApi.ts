/**
 * The client of `/api/v1/section-series` (ТЗ 8.1; ADR 0016 §3; `core/bf_core/sections/router.py` is the source of
 * truth): the list, create, change with `expected_version`, copy, delete, the table of sections. Errors of the core
 * stay `CoreError` with `{code, message, hint}`; `seriesFault` turns any error into what a window shows — the text,
 * the hint, the parameter (`field` of a 422 `bad_series`) and, on a 409 `version_conflict`, the current series.
 */
import { api, CoreError } from "../../shell/api";
import type { SeriesKind, SeriesParams } from "../../viz/section/series";

export type ShadowMode = "none" | "prev" | "next" | "both";

/** `params` of a series as the core gives them (`schema.params_of` without `number`). */
export interface SeriesMeta extends SeriesParams {
  type: SeriesKind;
  template: string | null;
  shadow: ShadowMode;
  contour_layer: number | null;
  layout: unknown;
}

export interface SeriesRow {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  /** the number of the series in the project (1, 2, …): never changes */
  number: number;
  /** K: sections in the series (0 — a meta this version cannot read) */
  count: number;
  params: SeriesMeta;
  created_at: string;
  updated_at: string;
}

export interface SeriesSectionRow {
  /** from 0; the interface shows k + 1 */
  k: number;
  name: string;
  s: number;
  origin: [number, number, number];
  towards: number;
  away: number;
}

export interface SeriesTable {
  version: number;
  count: number;
  sections: SeriesSectionRow[];
  /** a polyline section: its segments as the core gives them */
  segments?: unknown;
}

/** What `POST` and `PATCH` send: a parameter left out keeps its value (`PATCH`) or its default (`POST`). */
export type SeriesParamsIn = Partial<Omit<SeriesMeta, "layout" | "p1">> & { p1?: number[] | null };

export interface SeriesFault {
  status: number;
  code: string;
  message: string;
  hint: string;
  /** the parameter the core named (422 `bad_series`) */
  field: string | null;
  /** 409 `version_conflict`: the series as it is now */
  current: SeriesRow | null;
}

export const seriesApi = {
  list: () => api.get<{ series: SeriesRow[]; limit: number }>("/section-series"),
  get: (id: number) => api.get<SeriesRow>(`/section-series/${id}`),
  create: (params: SeriesParamsIn, name?: string | null, folder?: string | null) =>
    api.post<SeriesRow>("/section-series", { params, ...(name ? { name } : {}), ...(folder ? { folder } : {}) }),
  patch: (id: number, expectedVersion: number, change: { name?: string; folder?: string | null; params?: SeriesParamsIn }) =>
    api.patch<SeriesRow>(`/section-series/${id}`, { expected_version: expectedVersion, ...change }),
  remove: (id: number) => api.del<{ deleted: number }>(`/section-series/${id}`),
  copy: (id: number, name?: string) => api.post<SeriesRow>(`/section-series/${id}/copy`, name ? { name } : {}),
  table: (id: number) => api.get<SeriesTable>(`/section-series/${id}/table`),
};

/** Any error as a window shows it: a `CoreError` keeps its code, hint and field; anything else is a plain text. */
export function seriesFault(e: unknown): SeriesFault {
  if (e instanceof CoreError) {
    const body = e.body ?? {};
    const field = typeof body.field === "string" ? body.field : null;
    const current = e.status === 409 && body.series && typeof body.series === "object" ? (body.series as SeriesRow) : null;
    return { status: e.status, code: e.code, message: e.message, hint: e.hint, field, current };
  }
  return { status: 0, code: "error", message: e instanceof Error ? e.message : String(e), hint: "", field: null, current: null };
}

/** «Текст. Подсказка.» — for the command log, Claude and the status bar. */
export const faultText = (f: SeriesFault): string => (f.hint ? `${f.message} ${f.hint}` : f.message);
