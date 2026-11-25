/**
 * The client of the grade–tonnage report of a block model (ТЗ 7.31 «Содержание–тоннаж»; ADR 0023 §10, decision 15;
 * docs/progress/e17-api.md §10; core — `blocks/report.py`, `blocks/routes_report.py`). The forms are those of the
 * answers of the core (a run of the core through TestClient: `core/tests/data/blocks/report_window_answers.json`,
 * `core/tests/test_blocks_report_window_answers.py`):
 *
 * - `POST /blocks/{id}/report {grades?, density?, cutoffs?, cutoff_field?, filter_id?, restrict?, categories?, groups?,
 *   curve?, csv?}` → `{source, id}` of the job `blocks.report` (404 `block_model_not_found`, `block_field_not_found
 *   {param}`, `filter_not_found`; 422 `bad_param {param}`, `report_too_big`, `filter_field_missing {field, model}`);
 * - the job — `GET /jobs/{source}/{id}` → `{status, progress, message, result, error}`; the result is `ReportResult`.
 *
 * A grade with no value in a row is `null` («нет значения»), never 0; so is a metal of an unknown unit. No React.
 */
import { api, CoreError } from "../../../shell/api";
import type { Rule } from "../filters/constructor";
import { startedOf, waitJob, type Started } from "../../qaqc/setupApi";

export type { Started };

export const MAX_GRADES = 20;
export const MAX_CUTOFFS = 200;
export const MIN_CURVE = 2;
export const MAX_CURVE = 500;
export const DEFAULT_CURVE = 50;

/** The body of `POST /blocks/{id}/report`; a key that is absent (or null) is the default of 7.31 on the core's side. */
export interface ReportBody {
  grades?: string[] | null;
  /** a field of the model or a number (t/m³) */
  density?: string | number | null;
  cutoffs?: number[];
  cutoff_field?: string | null;
  filter_id?: number | null;
  restrict?: Rule | null;
  categories?: { field: string; values: string[] } | null;
  groups?: string | null;
  /** 0 — no curve, else 2…500 points */
  curve?: number;
  csv?: boolean;
}

/** The numbers of a grade of one row. `grade` and `metal` are null when no block of the row has the grade / the unit is unknown. */
export interface GradeCell {
  grade: number | null;
  unit: string | null;
  metal: number | null;
  metal_unit: string | null;
  /** the tonnes of the blocks of the row that have no value of the grade (outside the mean and the metal) */
  missing_tonnes: number;
  missing_blocks: number;
}

export interface ReportRow {
  /** null — the whole model (the rows of the groups follow) */
  group: string | null;
  cutoff: number;
  blocks: number;
  volume: number;
  tonnes: number;
  grades: Record<string, GradeCell>;
}

/** The curve over the points of the cut-off field: tonnes, grade and metal ABOVE each cut-off (null — no value). */
export interface ReportCurve {
  field: string;
  unit: string | null;
  cutoffs: number[];
  tonnes: (number | null)[];
  grade: (number | null)[];
  metal: (number | null)[];
  metal_unit: string | null;
}

/** What is not counted, each block once: the restrictor / categories, an empty FRAC, no density, no value of the cut-off field. */
export interface Excluded {
  filtered: number;
  no_volume: number;
  no_density: number;
  no_cutoff: number;
}

export interface ReportResult {
  model_id: number;
  density: { field: string | null; value: number | null };
  cutoff_field: string;
  units: { volume: string; tonnes: string };
  rows: ReportRow[];
  curve: ReportCurve | null;
  excluded: Excluded;
  warnings: string[];
  /** the table as text («;», decimal comma, CRLF) when `csv: true` was asked */
  csv: string | null;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const num = (v: unknown): number | null => (isNum(v) ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const count = (v: unknown): number => (isNum(v) && v >= 0 ? v : 0);
const numbers = (v: unknown): number[] => (Array.isArray(v) ? v.filter(isNum) : []);

function readCell(raw: unknown): GradeCell {
  const g = obj(raw);
  return { grade: num(g.grade), unit: str(g.unit), metal: num(g.metal), metal_unit: str(g.metal_unit), missing_tonnes: count(g.missing_tonnes), missing_blocks: count(g.missing_blocks) };
}

/** A row; null — not a row (no cut-off or no tonnes): such an answer is not a report. */
function readRow(raw: unknown): ReportRow | null {
  const r = obj(raw);
  if (!isNum(r.cutoff) || !isNum(r.tonnes) || !isNum(r.volume) || !isNum(r.blocks)) return null;
  const grades: Record<string, GradeCell> = {};
  for (const [k, v] of Object.entries(obj(r.grades))) grades[k] = readCell(v);
  return { group: str(r.group), cutoff: r.cutoff, blocks: r.blocks, volume: r.volume, tonnes: r.tonnes, grades };
}

function readCurve(raw: unknown): ReportCurve | null {
  const c = obj(raw);
  if (!Array.isArray(c.cutoffs) || !Array.isArray(c.tonnes) || !Array.isArray(c.grade)) return null;
  const cutoffs = numbers(c.cutoffs);
  if (cutoffs.length !== c.cutoffs.length || cutoffs.length < 2 || c.tonnes.length !== cutoffs.length || c.grade.length !== cutoffs.length) return null;
  const metal = Array.isArray(c.metal) && c.metal.length === cutoffs.length ? c.metal.map(num) : cutoffs.map(() => null);
  return { field: str(c.field) ?? "", unit: str(c.unit), cutoffs, tonnes: c.tonnes.map(num), grade: c.grade.map(num), metal, metal_unit: str(c.metal_unit) };
}

/** The result of the job; null when the answer is not a report (no rows, a row without numbers). */
export function readReport(raw: unknown): ReportResult | null {
  const r = obj(raw);
  if (!Array.isArray(r.rows) || !isNum(r.model_id)) return null;
  const rows: ReportRow[] = [];
  for (const x of r.rows) {
    const row = readRow(x);
    if (!row) return null;
    rows.push(row);
  }
  const d = obj(r.density);
  const e = obj(r.excluded);
  const u = obj(r.units);
  return {
    model_id: r.model_id,
    density: { field: str(d.field), value: num(d.value) },
    cutoff_field: str(r.cutoff_field) ?? "",
    units: { volume: str(u.volume) ?? "м³", tonnes: str(u.tonnes) ?? "т" },
    rows,
    curve: readCurve(r.curve),
    excluded: { filtered: count(e.filtered), no_volume: count(e.no_volume), no_density: count(e.no_density), no_cutoff: count(e.no_cutoff) },
    warnings: Array.isArray(r.warnings) ? r.warnings.filter((w): w is string => typeof w === "string") : [],
    csv: str(r.csv),
  };
}

/** `POST /blocks/{id}/report`: the job started (the shapes and the fields are checked by the core before it starts). */
export async function startReport(modelId: number, body: ReportBody): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/report`, body));
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
    result: readReport(j.result),
  };
}

/** The wait for the end of the job (the list of the «Задания» tab follows the core's events); `signal` stops the wait only. */
export const waitReport = (job: Started, signal?: AbortSignal): ReturnType<typeof waitJob> => waitJob(job, signal);

/** A refusal of the core as the form needs it: the words, the hint, the place (`param`, or the field a filter needs). */
export interface Refusal {
  code: string;
  message: string;
  hint: string;
  param: string | null;
}

export function refusalOf(e: unknown): Refusal {
  if (e instanceof CoreError) {
    const p = e.body?.param;
    // `filter_field_missing` names a field (the restrictor or the filter reads it), not a param of the body
    return { code: e.code, message: e.message, hint: e.hint, param: typeof p === "string" ? p : null };
  }
  const message = e instanceof Error ? e.message : String(e);
  return { code: "error", message, hint: "", param: null };
}
