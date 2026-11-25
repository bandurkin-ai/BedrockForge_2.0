/**
 * The client of the cross-validation of an estimation (ТЗ 8.8.5; docs/progress/e18-api.md §8.1): `POST /estimate/xval
 * {estimation_id, by?}` → the job `estimate.xval` (its result: `{estimation_id, by, n, summary, events}`) and
 * `GET /estimations/{id}/xval?page&size` → the page of the table with the summary and the histogram of the errors (404
 * `xval_not_found` while there is no calculation). A gap of an answer is `null` — never 0 (8.0.1). No React.
 */
import { api, CoreError } from "../../shell/api";
import { GEOSTAT_API, isNum, numOrNull, obj, startedOf, str, type Started } from "./api";

export type XvalBy = "hole" | "sample";
export const XVAL_BY: readonly XvalBy[] = ["hole", "sample"];
export const BY_TITLES: Record<XvalBy, string> = { hole: "по скважине", sample: "по пробе" };
export const BY_HINTS: Record<XvalBy, string> = {
  hole: "Скважина убирается целиком: так проверяется оценка в месте, где рядом нет проб той же скважины",
  sample: "Убирается одна проба: оценка по соседним пробам, в том числе из той же скважины",
};

/** The rows asked in one request (the core's maximum). */
export const PAGE_MAX = 5000;

/** The code of «нет расчёта». */
export const NOT_FOUND = "xval_not_found";

/** A row of the table: «true» of the core is `truth` here (a reserved word is no name for a field). */
export interface XvalRow {
  i: number;
  row_id: number | null;
  hole: string | null;
  from: number | null;
  to: number | null;
  x: number | null;
  y: number | null;
  z: number | null;
  truth: number | null;
  est: number | null;
  error: number | null;
  kv: number | null;
  z_error: number | null;
  pass: number | null;
  ns: number | null;
  status: number;
}

export interface XvalSummary {
  n: number;
  mean_error: number | null;
  std_error: number | null;
  mae: number | null;
  r: number | null;
  slope: number | null;
  mean_ratio: number | null;
  n_ratio: number;
  n_kv_zero: number;
  n_skipped: number;
  /** the rows by the status 0…3 */
  by_status: number[];
}

export interface XvalHistogram {
  edges: number[];
  counts: number[];
}

export interface XvalPage {
  estimation_id: number;
  by: XvalBy;
  at: string | null;
  stale: boolean;
  total: number;
  page: number;
  pages: number;
  size: number;
  rows: XvalRow[];
  summary: XvalSummary;
  histogram: XvalHistogram;
}

const count = (v: unknown): number => (isNum(v) ? v : 0);
const nums = (v: unknown): number[] => (Array.isArray(v) ? v.filter(isNum) : []);

export function readRow(raw: unknown): XvalRow {
  const o = obj(raw);
  return {
    i: count(o.i),
    row_id: numOrNull(o.row_id),
    hole: str(o.hole) ?? (isNum(o.hole) ? String(o.hole) : null),
    from: numOrNull(o.from),
    to: numOrNull(o.to),
    x: numOrNull(o.x),
    y: numOrNull(o.y),
    z: numOrNull(o.z),
    truth: numOrNull(o.true),
    est: numOrNull(o.est),
    error: numOrNull(o.error),
    kv: numOrNull(o.kv),
    z_error: numOrNull(o.z_error),
    pass: numOrNull(o.pass),
    ns: numOrNull(o.ns),
    status: count(o.status),
  };
}

export function readSummary(raw: unknown): XvalSummary {
  const o = obj(raw);
  return {
    n: count(o.n),
    mean_error: numOrNull(o.mean_error),
    std_error: numOrNull(o.std_error),
    mae: numOrNull(o.mae),
    r: numOrNull(o.r),
    slope: numOrNull(o.slope),
    mean_ratio: numOrNull(o.mean_ratio),
    n_ratio: count(o.n_ratio),
    n_kv_zero: count(o.n_kv_zero),
    n_skipped: count(o.n_skipped),
    by_status: nums(o.by_status),
  };
}

/** `GET /estimations/{id}/xval` (the form of `contract_answers.xval`); null — the answer has no number of the estimation. */
export function readXvalPage(raw: unknown): XvalPage | null {
  const o = obj(raw);
  if (!isNum(o.estimation_id)) return null;
  const h = obj(o.histogram);
  return {
    estimation_id: o.estimation_id,
    by: o.by === "sample" ? "sample" : "hole",
    at: str(o.at),
    stale: o.stale === true,
    total: count(o.total),
    page: count(o.page) || 1,
    pages: count(o.pages),
    size: count(o.size),
    rows: (Array.isArray(o.rows) ? o.rows : []).map(readRow),
    summary: readSummary(o.summary),
    histogram: { edges: nums(h.edges), counts: nums(h.counts) },
  };
}

/** The result of the job (`estimate.xval`): the row, what was excluded, the rows and the summary. */
export interface XvalResult {
  estimation_id: number;
  by: XvalBy;
  n: number;
  summary: XvalSummary;
}

export function readXvalResult(raw: unknown): XvalResult | null {
  const o = obj(raw);
  if (!isNum(o.estimation_id)) return null;
  return { estimation_id: o.estimation_id, by: o.by === "sample" ? "sample" : "hole", n: count(o.n), summary: readSummary(o.summary) };
}

export async function startXval(body: { estimation_id: number; by?: XvalBy }): Promise<Started> {
  return startedOf(obj(await api.post(`${GEOSTAT_API}/estimate/xval`, body)));
}

export async function loadXvalPage(id: number, page = 1, size = PAGE_MAX): Promise<XvalPage> {
  const p = readXvalPage(await api.get(`${GEOSTAT_API}/estimations/${id}/xval?page=${page}&size=${size}`));
  if (!p) throw new CoreError(0, "bad_answer", "Ядро вернуло перекрёстную проверку без номера строки оценки.", "Выполните проверку заново; если повторится — обновите программу.");
  return p;
}
