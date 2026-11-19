/**
 * The client of `/api/v1/section-series` (ТЗ 8.1; ADR 0016 §3; `core/bf_core/sections/router.py` is the source of
 * truth): the list, create, change with `expected_version`, copy, delete, the table of sections. Errors of the core
 * stay `CoreError` with `{code, message, hint}`; `seriesFault` turns any error into what a window shows — the text,
 * the hint, the parameter (`field` of a 422 `bad_series`) and, on a 409 `version_conflict`, the current series.
 */
import { api, CoreError } from "../../shell/api";
import type { ConfirmOptions } from "../../shell/dialogs";
import { count, plural } from "../../shell/treeActions";
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
  /** What the parameters would do to the contours of the series (ADR 0016 decision 4); nothing is stored. */
  impact: (id: number, params: SeriesParamsIn) => api.post<SeriesImpact>(`/section-series/${id}/impact`, { params }),
};

/** `POST /section-series/{id}/impact`: of `total` contours in the windows now, `outside` leave every new window,
 * `moved` go to the window of another section (their attribute `SECTION` is not recomputed). */
export interface SeriesImpact {
  series_id: number;
  version: number;
  layer_id: number | null;
  layer: string | null;
  total: number;
  outside: number;
  moved: number;
  /** «Разрез 5050N: линия 12 слоя «Контуры»» — the first ones outside */
  examples: string[];
}

/**
 * Our own question before new geometry is saved (ТЗ 7.0.11: never window.confirm): «2 из 5 контуров слоя
 * «Контуры» выпадут из окон разрезов»; null — nothing to ask (no contours, or they all stay where they are).
 */
export function impactQuestion(i: Pick<SeriesImpact, "total" | "outside" | "moved" | "layer" | "examples">): ConfirmOptions | null {
  if (!i.total || (!i.outside && !i.moved)) return null;
  const layer = i.layer ? ` слоя «${i.layer}»` : "";
  const of = `из ${count(i.total)} ${plural(i.total, "контура", "контуров", "контуров")}${layer}`;
  const parts: string[] = [];
  if (i.outside) parts.push(`${count(i.outside)} ${of} ${plural(i.outside, "выпадет", "выпадут", "выпадут")} из окон разрезов.`);
  if (i.moved) {
    const verb = plural(i.moved, "окажется", "окажутся", "окажутся");
    parts.push(`${i.outside ? "Ещё" : ""} ${count(i.moved)} ${i.outside ? "" : `${of} `}${verb} в окне другого разреза — атрибут SECTION у них не пересчитывается.`.replace(/\s+/g, " ").trim());
  }
  const shown = i.examples.slice(0, 5).map((e) => `• ${e}`);
  if (i.outside > shown.length && shown.length) shown.push(`… и ещё ${count(i.outside - shown.length)}`);
  return {
    title: "Контуры вне окон",
    message: [...parts, ...shown, "Контуры не удаляются. Чтобы оставить серию как есть, отмените и сделайте «Копировать серию» с новой геометрией.", "Сохранить новые параметры?"].join("\n"),
    okLabel: "Сохранить",
    danger: i.outside > 0,
  };
}

/** Asks about the contours before a change of the parameters is saved: true — save. A failed check does not stop
 * the save: the core tells the same error on the PATCH, with its field. */
export async function confirmImpact(id: number, params: SeriesParamsIn, ask: (q: ConfirmOptions) => Promise<boolean>): Promise<boolean> {
  let i: SeriesImpact;
  try {
    i = await seriesApi.impact(id, params);
  } catch {
    return true;
  }
  const q = impactQuestion(i);
  return q ? ask(q) : true;
}

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
