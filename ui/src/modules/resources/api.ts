/**
 * The client of the resource report (ТЗ 8.10 «Подсчёт ресурсов»; ADR 0027; docs/progress/e20-api.md §2–§9; core — `resources/router.py`).
 * The forms of the answers are in `answers.ts` (they are those of `core/tests/data/resources/contract_answers.json`):
 *
 * - `GET /resources/defaults` → `{params, ranges}` — the form of the window;
 * - `POST /resources/reports {params, name?, save}` → `{source, id}` of the job `resources.report` (`save: false` — the preview, nothing is
 *   written, it works in a project open for reading); 404 `block_model_not_found`, `block_field_not_found {param}`, `filter_not_found`;
 *   422 `bad_param {param}`, `report_too_big`, `filter_field_missing {field}`, `group_numeric {param}`; 423 `project_read_only` (`save` only);
 * - the job — `GET /jobs/{source}/{id}` → `{status, progress, message, result, error}`; the result is `ReportResult`;
 * - `GET /resources/reports`, `GET /resources/reports/{id}?template=&cutoff=`, `DELETE /resources/reports/{id}`;
 * - `POST /resources/reports/{id}/export {format, path, template?, cutoff?, overwrite?}` → the job `resources.export`;
 * - `POST /resources/reports/{id}/drill {group, cutoff, kind?}` (Д-36), `POST /resources/compare {report_a, report_b}` (Д-37).
 *
 * The body of a request leaves out what is default (the core takes it); a gap is `null` — never 0. No React.
 */
import { api, CoreError } from "../../shell/api";
import type { Rule } from "../blocks/filters/constructor";
import { startedOf, waitJob, type Started } from "../qaqc/setupApi";
import { obj, num, readCompare, readDrill, readListRow, readReportResult, readTemplateRow, str, type CompareAnswer, type DrillAnswer, type ReportListRow, type ReportResult, type TemplateRow } from "./answers";

export type { Started };
export { refusalOf, type Refusal } from "../blocks/report/api";

// ---- the request ----

export interface ElementSpec {
  field: string;
  unit?: string;
}
export type GroupKey = string | { kind: "field"; field: string } | { kind: "bench" };
export type CutoffsSpec = (number | null)[] | { from: number; to: number; step: number };
export interface BenchSpec {
  h?: number;
  z0?: number | null;
  z_min?: number | null;
  z_max?: number | null;
}

/** `params` of `POST /resources/reports`: a key that is absent is the default of the core (docs/progress/e20-api.md §2). */
export interface ResourceParams {
  model_id: number;
  elements?: (string | ElementSpec)[];
  cutoff_field?: string;
  /** a number, `null` — «без борта» (all the blocks of the group, those without the cut-off value too), or a range */
  cutoffs?: CutoffsSpec;
  balance?: { cutoff: number; off_cutoff?: number };
  /** a field of the model or a number, t/m³ */
  density?: string | number;
  filter_id?: number;
  restrict?: Rule;
  /** up to 3 keys: a category field or the bench */
  groups?: GroupKey[];
  bench?: BenchSpec;
  /** 0 — no curve, else 2…500 points */
  curve?: number;
  /** the form of the report (JORC, ГКЗ, own): a view only, not an input of the graph */
  template_id?: string | null;
}

export interface ReportRequest {
  params: ResourceParams;
  name?: string;
  save: boolean;
}

// ---- the defaults of the core ----

export interface Range {
  lo: number | null;
  hi: number | null;
  lo_open: boolean;
  integer: boolean;
  unit: string;
}

/** The limits of the request as the core states them (`defaults.params.limits`); the window checks the form against them before it sends. */
export interface Limits {
  elements: number;
  cutoffs: number;
  group_keys: number;
  benches: number;
  curve: number;
}

export const DEFAULT_LIMITS: Limits = { elements: 20, cutoffs: 200, group_keys: 3, benches: 1000, curve: 500 };

export interface Defaults {
  params: Record<string, unknown>;
  ranges: Record<string, Range>;
  limits: Limits;
}

export function readDefaults(raw: unknown): Defaults {
  const r = obj(raw);
  const p = obj(r.params);
  const l = obj(p.limits);
  const ranges: Record<string, Range> = {};
  for (const [k, v] of Object.entries(obj(r.ranges))) {
    const x = obj(v);
    ranges[k] = { lo: num(x.lo), hi: num(x.hi), lo_open: x.lo_open === true, integer: x.integer === true, unit: str(x.unit) ?? "" };
  }
  return {
    params: p,
    ranges,
    limits: { elements: num(l.elements) ?? DEFAULT_LIMITS.elements, cutoffs: num(l.cutoffs) ?? DEFAULT_LIMITS.cutoffs, group_keys: num(l.group_keys) ?? DEFAULT_LIMITS.group_keys, benches: num(l.benches) ?? DEFAULT_LIMITS.benches, curve: num(l.curve) ?? DEFAULT_LIMITS.curve },
  };
}

export async function loadDefaults(): Promise<Defaults> {
  return readDefaults(await api.get("/resources/defaults"));
}

// ---- the job ----

/** `POST /resources/reports`: the job started (the shapes and the fields are checked by the core before it starts). */
export async function startReport(req: ReportRequest): Promise<Started> {
  const body: Record<string, unknown> = { params: req.params, save: req.save };
  if (req.name) body.name = req.name;
  return startedOf(await api.post<Record<string, unknown>>("/resources/reports", body));
}

/** The record of a job from the core: its status, the stage, the result (read in full — the list of jobs may be short). */
export interface ReportJob {
  status: string;
  progress: number;
  message: string | null;
  error: { code: string; message: string; hint: string } | null;
  result: ReportResult | null;
}

export async function loadReportJob(job: Started): Promise<ReportJob> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return {
    status: str(j.status) ?? "",
    progress: num(j.progress) ?? 0,
    message: str(j.message),
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    result: readReportResult(j.result),
  };
}

/** The wait for the end of the job (the list of the «Задания» tab follows the events of the core); `signal` stops the wait only. */
export const waitJobEnd = (job: Started, signal?: AbortSignal): ReturnType<typeof waitJob> => waitJob(job, signal);

// ---- reports, templates ----

export async function listReports(): Promise<ReportListRow[]> {
  const r = obj(await api.get("/resources/reports"));
  return (Array.isArray(r.reports) ? r.reports : []).map(readListRow).filter((x): x is ReportListRow => x !== null);
}

export interface ReportQuery {
  /** the form of this template instead of the one saved with the report */
  template?: string;
  /** the cut-off of the form (`null` — «без борта»); absent — the first */
  cutoff?: number | null;
  page?: number;
  size?: number;
}

/** A saved report; `form` is there when a template is asked or the report has one. A page of the rows only — `rows_total` is the count. */
export async function loadReport(id: number, q: ReportQuery = {}): Promise<ReportResult> {
  const qs = new URLSearchParams();
  if (q.template) qs.set("template", q.template);
  if (q.cutoff !== undefined && q.cutoff !== null) qs.set("cutoff", String(q.cutoff));
  if (q.page) qs.set("page", String(q.page));
  if (q.size) qs.set("size", String(q.size));
  const raw = await api.get(`/resources/reports/${id}${qs.size ? `?${qs.toString()}` : ""}`);
  const r = readReportResult(raw);
  if (!r) throw new CoreError(0, "bad_answer", "Ядро вернуло отчёт без таблицы.", "Обновите программу или построите отчёт заново.");
  return r;
}

export async function deleteReport(id: number): Promise<void> {
  await api.del(`/resources/reports/${id}`);
}

export async function listTemplates(): Promise<TemplateRow[]> {
  const r = obj(await api.get("/resources/templates"));
  return (Array.isArray(r.templates) ? r.templates : []).map(readTemplateRow).filter((x): x is TemplateRow => x !== null);
}

// ---- export, drill, compare ----

export interface ExportBody {
  format: "xlsx" | "csv";
  path: string;
  template?: string;
  cutoff?: number | null;
  overwrite?: boolean;
}

/** `POST /resources/reports/{id}/export`: the job `resources.export` (409 `files_exist {files}`: the file is not replaced unless `overwrite`). */
export async function startExport(id: number, body: ExportBody): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/resources/reports/${id}/export`, body));
}

export interface ExportResult {
  path: string;
  format: string;
  bytes: number | null;
  rows: number | null;
}

/** The result of the job `resources.export` (`GET /jobs/…`); null — not finished or not an export. */
export async function loadExportJob(job: Started): Promise<{ status: string; error: { code: string; message: string; hint: string } | null; result: ExportResult | null }> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  const r = obj(j.result);
  return {
    status: str(j.status) ?? "",
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    result: typeof r.path === "string" ? { path: r.path, format: str(r.format) ?? "", bytes: num(r.bytes), rows: num(r.rows) } : null,
  };
}

export interface DrillBody {
  /** the labels of the keys of the cell («(пусто)» is a label); null / [] — the total */
  group: string[] | null;
  /** the cut-off of the row; null — «без борта» */
  cutoff: number | null;
  kind?: "cutoff" | "all" | "balance" | "offbalance";
}

/** `POST /resources/reports/{id}/drill`: the rules of the cell for `blocks.filter` and its numbers (synchronous; works in a project open for reading). */
export async function drillCell(id: number, body: DrillBody): Promise<DrillAnswer> {
  const r = readDrill(await api.post(`/resources/reports/${id}/drill`, body));
  if (!r) throw new CoreError(0, "bad_answer", "Ядро не вернуло правила ячейки.", "Обновите программу.");
  return r;
}

/** `POST /resources/compare`: two reports row by row (synchronous). */
export async function compareReports(a: number, b: number): Promise<CompareAnswer> {
  const r = readCompare(await api.post("/resources/compare", { report_a: a, report_b: b }));
  if (!r) throw new CoreError(0, "bad_answer", "Ядро не вернуло сравнение отчётов.", "Обновите программу.");
  return r;
}
