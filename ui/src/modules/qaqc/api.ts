/**
 * The core's API of QAQC for the tab and the reports (ADR 0020 §10; core 14.10–14.14 `qaqc/router.py`,
 * `routes_calc.py`, `routes_reports.py`): `/qaqc/data`, `/qaqc/calc/{section}`, `/qaqc/records`, `/qaqc/reports`.
 * The setup (config, CRM, inspect, import) is in `setupApi.ts` (the window «Настройка»). Errors are
 * `{code, message, hint, param?}` in Russian. An undefined number is null — never 0; lists of values come as
 * columns (arrays of one length). With `report_id` the calculation takes the setup, CRM and filter of the report.
 */
import { api, coreTarget, CoreError } from "../../shell/api";
import { apiUrl, authHeaders } from "../../shell/coreClient";
import type { DupKind, QaqcType } from "./commands";

export interface QaqcElement {
  name: string;
  unit: string | null;
}

export interface QaqcFieldInfo {
  name: string;
  kind: "text" | "number" | "date";
}

export interface TypeCode {
  code: string;
  /** null — not recognized: not in any calculation */
  type: QaqcType | null;
  n: number;
}

export type QaqcSource = { kind: "file"; name: string; sha256?: string } | { kind: "table"; set_id: number; name_key: string; rev?: number };

/** Data of control samples (`objects(kind="qaqc_data")`). */
export interface QaqcData {
  id: number;
  name: string;
  version: number;
  rows: number;
  elements: QaqcElement[];
  fields: QaqcFieldInfo[];
  labs: string[];
  batches: string[] | number;
  date_from: string | null;
  date_to: string | null;
  type_codes: TypeCode[];
  source: QaqcSource | null;
  /** the table of the set changed after the import (ADR 0020 §2) */
  stale: boolean;
  sha256: string;
  counts: Record<string, number>;
}

/** The thresholds of the setup (ADR 0020 §3). */
export interface QaqcLimits {
  sigma_warn: number;
  sigma_fail: number;
  run: number;
  k_blank: number;
  k_dup: number;
  th_group: number;
  hard_p: number;
  hard_c: number;
  hard_f: number;
  hard_ump: number;
  hard_share: number;
  n_boot: number;
  seed: number;
}

export const DEFAULT_LIMITS: QaqcLimits = { sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 };

export type CalcSection = "summary" | "standards" | "blanks" | "duplicates" | "external" | "batches";

/** Records of the data, after the filter, control ones, codes not recognized, without a value of the element. */
export interface CalcCounts {
  rows: number;
  filtered: number;
  control: number;
  unmapped: number;
  missing: number;
}

/** The head of every answer of `/calc/*`. */
export interface CalcHead {
  element: string;
  unit: string | null;
  counts: CalcCounts;
  limits: QaqcLimits;
  notes: string[];
}

export interface CalcBody {
  data_id?: number;
  report_id?: number;
  element: string;
  filter?: Record<string, unknown>;
  group?: Record<string, string | null>;
  kind?: DupKind;
  offset?: number;
  limit?: number;
}

/** 0 — норма, 1 — предупреждение, 2 — отказ. */
export type Status = 0 | 1 | 2;
/** Bits of `rule[]` of a standard: 1 — two in a row, 2 — a run, 4 — a possible swap. */
export const RULE_PAIR = 1;
export const RULE_RUN = 2;
export const RULE_SWAP = 4;

export interface Thinned {
  shown: number;
  total: number;
}

export interface StandardGroup {
  crm: string;
  lab: string | null;
  /** `ok`; `no_reference` — no CRM in the reference; `unit_mismatch` — units differ */
  state: string;
  n: number;
  mu: number | null;
  sd: number | null;
  unit: string | null;
  mean: number | null;
  std: number | null;
  bias: number | null;
  /** 0 — ≤ 5 %, 1 — ≤ 10 %, 2 — more; null — not assessed */
  bias_class: number | null;
  norm: number;
  warn: number;
  fail: number;
  pair_fails: number;
  run_flags: number;
  swaps: number;
}

/** A dated value: ISO text or ms since 1970 (UTC); null — no date. */
export type DateValue = string | number | null;

export interface StandardSeries {
  row: number[];
  date: DateValue[];
  seq: (number | null)[];
  batch: (string | null)[];
  x: number[];
  z: (number | null)[];
  status: number[];
  rule: number[];
  ma: (number | null)[];
  /** the CRM it may be (up to three), or null */
  swap: (string | string[] | null)[];
}

export interface StandardsAnswer extends CalcHead {
  groups: StandardGroup[];
  /** with `group`: null — the group is gone under the filter */
  series?: StandardSeries | null;
  thinned?: Thinned;
}

export interface BlankGroup {
  lab: string | null;
  n: number;
  dl: number | null;
  limit: number | null;
  norm: number;
  fail: number;
  not_assessed: number;
  /** samples without a value (counted, in no series) */
  missing?: number;
  /** the blanks of the lab have different DL: `dl`, `limit` are null */
  mixed?: boolean;
}

export interface BlanksAnswer extends CalcHead {
  groups: BlankGroup[];
  /** with `group`: `limit[i]` — k_б·DL of the sample (null — no DL); null — the lab is gone under the filter */
  series?: { row: number[]; date: DateValue[]; seq: (number | null)[]; batch: (string | null)[]; x: number[]; status: number[]; prev: (number | null)[]; limit?: (number | null)[] } | null;
  carryover?: { n: number; pearson: number | null };
  thinned?: Thinned;
}

export interface Excluded {
  no_parent: number;
  missing: number;
  negative: number;
  zero: number;
  below: number;
}

export interface PairPoints {
  row: number[];
  x1: number[];
  x2: number[];
  hard: number[];
}

export interface ThompsonHoward {
  /** `ok`; `few_pairs` — fewer than 50 pairs */
  state: string;
  group: number;
  a: number | null;
  b: number | null;
  k: number | null;
  groups: { m: number[]; med: number[] };
  precision: { c: number; p: number | null }[];
}

export interface DuplicatesAnswer extends CalcHead {
  pairs: number;
  used: number;
  excluded: Excluded;
  hard_limit: number;
  share_limit: number;
  share10: number | null;
  share20: number | null;
  share30: number | null;
  share_at_limit: number | null;
  passed: boolean | null;
  stats: { mean1: number | null; mean2: number | null; pearson: number | null; rma: { a: number | null; b: number | null } };
  ranked: { p: number[]; hard: number[] };
  points: PairPoints;
  thinned?: Thinned;
  th: ThompsonHoward;
}

export interface ExternalGroup {
  lab_main: string | null;
  lab_check: string | null;
  pairs: number;
  used: number;
  excluded: Excluded;
  mean_main: number | null;
  mean_check: number | null;
  bias: number | null;
  t: number | null;
  p: number | null;
  df: number | null;
  pearson: number | null;
  rma: { a: number | null; b: number | null; a_ci: [number, number] | null; b_ci: [number, number] | null; n_boot: number; seed: number };
  share10: number | null;
  share20: number | null;
  share_at_limit: number | null;
  passed: boolean | null;
  notes: string[];
}

export interface ExternalAnswer extends CalcHead {
  groups: ExternalGroup[];
  points?: PairPoints;
  ranked?: { p: number[]; hard: number[] };
}

export interface BatchRow {
  lab: string | null;
  batch: string;
  date_from: string | null;
  date_to: string | null;
  n: number;
  crm: number;
  blanks: number;
  crm_fail: number;
  blank_fail: number;
  status: number;
  /** CRM and blanks that have a status */
  assessed?: number;
}

export interface BatchesAnswer extends CalcHead {
  total: number;
  failed: number;
  not_assessed?: number;
  no_batch: number;
  offset?: number;
  batches: BatchRow[];
}

/** One line per section of the active element (the head of the tab, the sheet «Сводка»). */
export interface SummaryAnswer extends CalcHead {
  [section: string]: unknown;
}

export interface CalcAnswers {
  summary: SummaryAnswer;
  standards: StandardsAnswer;
  blanks: BlanksAnswer;
  duplicates: DuplicatesAnswer;
  external: ExternalAnswer;
  batches: BatchesAnswer;
}

export const calc = <S extends CalcSection>(section: S, body: CalcBody): Promise<CalcAnswers[S]> => api.post<CalcAnswers[S]>(`/qaqc/calc/${section}`, body);

/** The list of an answer: a bare array or `{items}` / `{data}` / `{reports}`. */
function listOf<T>(a: unknown, key: string): T[] {
  if (Array.isArray(a)) return a as T[];
  const r = (a ?? {}) as Record<string, unknown>;
  const v = r[key] ?? r.items ?? r.data;
  return Array.isArray(v) ? (v as T[]) : [];
}

export const listData = async (): Promise<QaqcData[]> => listOf<QaqcData>(await api.get<unknown>("/qaqc/data"), "data");
export const getData = (id: number): Promise<QaqcData> => api.get<QaqcData>(`/qaqc/data/${id}`);
export const renameData = (id: number, name: string, expected_version: number): Promise<QaqcData> => api.put<QaqcData>(`/qaqc/data/${id}`, { name, expected_version });
export const deleteData = (id: number): Promise<unknown> => api.del<unknown>(`/qaqc/data/${id}`);

export interface FieldValues {
  values: { value: string | number; n: number }[];
  more: boolean;
}

/** Values of a field for the filter (≤ 1000), with the text `q` in them when given. */
export function fieldValues(id: number, field: string, q = "", limit = 1000): Promise<FieldValues> {
  const p = new URLSearchParams({ field, limit: String(limit) });
  if (q) p.set("q", q);
  return api.get<FieldValues>(`/qaqc/data/${id}/values?${p.toString()}`);
}

/** Every field of a record (the card of a sample). */
export type QaqcRecord = Record<string, unknown>;

export type RecordsBody = ({ data_id: number } | { report_id: number }) & ({ rows: number[] } | { batch: string; lab: string | null });

/** Every field of the records chosen: ≤ 1000 rows, a batch — ≤ 5000 (the card of a sample, the batch shown). */
export async function loadRecords(body: RecordsBody): Promise<QaqcRecord[]> {
  return listOf<QaqcRecord>(await api.post<unknown>("/qaqc/records", body), "records");
}

export type ReportStale = null | "data" | "config" | "source_deleted";

export interface QaqcReport {
  id: number;
  name: string;
  version: number;
  /** `building`, `ready`, `failed` */
  state: string;
  stale: ReportStale;
  data_name: string;
  data_sha256: string;
  inputs_sha256: string;
  elements: string[];
  filter: Record<string, unknown>;
  /** the charts of the report: all, flagged, none */
  charts?: "all" | "flagged" | "none";
  created_at: string;
  updated_at?: string;
  built_at?: string | null;
  /** the job building it now (null — none) */
  job_id?: number | null;
  files: { kind: string; size: number }[];
  images: number;
  summary?: Record<string, unknown>;
}

/** A job started by the core: `{source, id}` of the list of jobs (`job_id` = `id`). */
export interface StartedJob {
  job_id: number;
  source?: string;
  id?: number;
}

export interface ReportChart {
  key: string;
  section: string;
  element: string;
  kind?: string;
  /** duplicates: `scatter`, `ranked`, `th` */
  view?: string;
  group?: Record<string, string | null>;
  title: string;
}

/** The charts to draw (≤ 200) and those left out over the limit. */
export interface ReportChartList {
  charts: ReportChart[];
  total: number;
  omitted: number;
}

export const listReports = async (): Promise<QaqcReport[]> => listOf<QaqcReport>(await api.get<unknown>("/qaqc/reports"), "reports");
export const getReport = (id: number): Promise<QaqcReport> => api.get<QaqcReport>(`/qaqc/reports/${id}`);
export const renameReport = (id: number, name: string, expected_version: number): Promise<QaqcReport> => api.put<QaqcReport>(`/qaqc/reports/${id}`, { name, expected_version });
export const deleteReport = (id: number): Promise<unknown> => api.del<unknown>(`/qaqc/reports/${id}`);
export type ReportStarted = StartedJob & { report_id: number };
export const createReport = (body: { data_id: number; name?: string; elements?: string[]; filter: Record<string, unknown>; charts: "all" | "flagged" | "none" }): Promise<ReportStarted> => api.post("/qaqc/reports", body);
export async function reportCharts(id: number): Promise<ReportChartList> {
  const a = (await api.get<unknown>(`/qaqc/reports/${id}/charts`)) as Partial<ReportChartList> | ReportChart[];
  const charts = listOf<ReportChart>(a, "charts");
  const r = Array.isArray(a) ? {} : a;
  return { charts, total: typeof r.total === "number" ? r.total : charts.length, omitted: typeof r.omitted === "number" ? r.omitted : 0 };
}
/** A binary body (the PNG of a chart): the errors of the core as with `api`. */
async function apiBytes(method: string, path: string, bytes: Uint8Array, type: string): Promise<unknown> {
  const target = coreTarget();
  if (!target) throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  let r: Response;
  try {
    r = await fetch(apiUrl(target, path), { method, headers: authHeaders(target, { "Content-Type": type }), body: bytes as unknown as BodyInit });
  } catch {
    throw new CoreError(0, "core_offline", "Ядро недоступно.", "Подождите перезапуска ядра.");
  }
  const text = await r.text().catch(() => "");
  let data: Record<string, unknown> = {};
  try {
    data = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    data = {};
  }
  if (!r.ok) throw new CoreError(r.status, String(data.code ?? "error"), String(data.message ?? `Ядро ответило ${r.status}`), String(data.hint ?? ""));
  return data;
}

/** A PNG of a chart of the report (≤ 2 МБ). */
export const putReportImage = (id: number, key: string, png: Uint8Array): Promise<unknown> => apiBytes("PUT", `/qaqc/reports/${id}/images/${encodeURIComponent(key)}`, png, "image/png");
export const reportFile = (id: number, kind: "xlsx" | "pdf"): Promise<StartedJob> => api.post(`/qaqc/reports/${id}/files`, { kind });
export const saveReport = (id: number, kind: "xlsx" | "pdf", path: string): Promise<{ path?: string; size?: number }> => api.post(`/qaqc/reports/${id}/save`, { kind, path });
export const rebuildReport = (id: number): Promise<ReportStarted> => api.post(`/qaqc/reports/${id}/rebuild`, {});
