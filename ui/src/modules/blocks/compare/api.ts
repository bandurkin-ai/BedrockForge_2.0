/**
 * The client of the comparison of two estimates of a block model (ТЗ 7.35 Д-37; ADR 0027 решение 25; docs/progress/e20-api.md §9.2): `POST /blocks/{id}/compare
 * {field_a, field_b, domain?, out?, expected_version?}` → the job `estimate.compare` (404 `block_field_not_found`; 409 `version_conflict`; 422 `bad_param`; 423 the project is
 * open for reading), the result is read from `GET /jobs/{source}/{id}`: the statistics of Δ = A − B, the histogram (by the number of blocks), the table by the domains, the
 * change of `STATUS`. Δ is NaN where A or B has a gap: a gap is `null` here — never 0 (8.0.1). The forms are those of the contract
 * (`core/tests/data/resources/contract_answers.json`: `estimate_compare`). No React.
 */
import { api, CoreError } from "../../../shell/api";
import { startedOf, waitJob, type Started } from "../../qaqc/setupApi";

export type { Started };
export { refusalOf, type Refusal } from "../report/api";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const num = (v: unknown): number | null => (isNum(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** The body of `POST /blocks/{id}/compare`; a key that is absent is the default of the core (the name of Δ is `<A>_<B>_D`). */
export interface CompareBody {
  field_a: string;
  field_b: string;
  domain?: string;
  out?: string;
  expected_version?: number;
}

export const PERCENTS = ["1", "5", "25", "50", "75", "95", "99"] as const;

export interface DeltaStats {
  n: number;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  percentiles: Record<string, number | null>;
}

export interface DomainRow {
  domain: string;
  /** the blocks with both values (they make the means) */
  blocks: number;
  /** the blocks of the domain with a gap in A or in B (outside the means) */
  missing: number;
  volume: number | null;
  mean_a: number | null;
  mean_b: number | null;
  mean_delta: number | null;
  /** the blocks whose `STATUS` changed; null — the fields are not of an estimate (no status) */
  status_changed: number | null;
}

export interface EstimateCompare {
  model_id: number;
  /** the name of the field Δ in the model */
  field: string;
  a: string;
  b: string;
  field_written: boolean;
  version: number | null;
  rows: number | null;
  stats: DeltaStats;
  histogram: { edges: number[]; counts: number[] };
  domains: DomainRow[];
  status: { a: string; b: string; changed: number } | null;
  warnings: string[];
  seconds: number | null;
}

/** The words of a warning of the core: a text, or an object with `message` (and `hint`). */
export function wordsOf(v: unknown): string[] {
  return list(v)
    .map((x) => {
      if (typeof x === "string") return x;
      const w = obj(x);
      const m = str(w.message) ?? "";
      const h = str(w.hint);
      return h ? `${m} ${h}`.trim() : m;
    })
    .filter(Boolean);
}

/** The result of the job; null — not a comparison (no name of the field Δ or no statistics). */
export function readCompareResult(raw: unknown): EstimateCompare | null {
  const r = obj(raw);
  const s = obj(r.stats);
  if (typeof r.field !== "string" || !isNum(r.model_id) || !isNum(s.n)) return null;
  const p = obj(s.percentiles);
  const h = obj(r.histogram);
  const st = obj(r.status);
  const stages = obj(r.stages);
  return {
    model_id: r.model_id,
    field: r.field,
    a: str(r.a) ?? "",
    b: str(r.b) ?? "",
    field_written: r.field_written === true,
    version: num(r.version),
    rows: num(r.rows),
    stats: { n: s.n, mean: num(s.mean), std: num(s.std), min: num(s.min), max: num(s.max), percentiles: Object.fromEntries(PERCENTS.map((k) => [k, num(p[k])])) },
    histogram: { edges: list(h.edges).filter(isNum), counts: list(h.counts).map((c) => (isNum(c) ? c : 0)) },
    domains: list(r.domains).map((x) => {
      const d = obj(x);
      return { domain: str(d.domain) ?? "", blocks: num(d.blocks) ?? 0, missing: num(d.missing) ?? 0, volume: num(d.volume), mean_a: num(d.mean_a), mean_b: num(d.mean_b), mean_delta: num(d.mean_delta), status_changed: num(d.status_changed) };
    }),
    status: typeof st.a === "string" && typeof st.b === "string" ? { a: st.a, b: st.b, changed: num(st.changed) ?? 0 } : null,
    warnings: wordsOf(r.warnings),
    seconds: num(stages.total),
  };
}

/** `POST /blocks/{id}/compare`: the job started (the fields are checked by the core before it starts). */
export async function startCompare(modelId: number, body: CompareBody): Promise<Started> {
  return startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/compare`, body));
}

export interface CompareJob {
  status: string;
  error: { code: string; message: string; hint: string } | null;
  result: EstimateCompare | null;
}

/** The record of the job: its status, the error, the result read in full. */
export async function loadCompareJob(job: Started): Promise<CompareJob> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return {
    status: str(j.status) ?? "",
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    result: readCompareResult(j.result),
  };
}

/** The wait for the end of the job (the list of the «Задания» tab follows the events of the core). */
export const waitCompareEnd = (job: Started, signal?: AbortSignal): ReturnType<typeof waitJob> => waitJob(job, signal);

export const badAnswer = (): CoreError => new CoreError(0, "bad_answer", "Ядро вернуло сравнение без результата.", "Повторите сравнение; если повторится — обновите программу.");
