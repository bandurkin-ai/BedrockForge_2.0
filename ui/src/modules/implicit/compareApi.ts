/**
 * The client of «Сверка данных и модели» (ТЗ 7.35 Д-33; docs/progress/e19-api.md §8.3) and of «Модель на скважины» (Д-34 ч. 1, §8.4):
 * - `POST /implicit/models/{id}/compare_holes {set_id, table, field, page?, size?}` → the matrices «код данных × код модели» (length, m, and
 *   the number of intervals), the intervals that do not agree with the hole and the depth for the window 7.17;
 * - `POST /drillholes/from_model {set_id, name, model_id | mesh_ids | block_model_id + field + table, codes?}` → `{source, id}` of the job
 *   `drillholes.from_model`; 409 `table_exists {name, suggested}` before the job is made.
 * A gap is `null` — never 0 (8.0.1). No React.
 */
import { api } from "../../shell/api";
import { isNum, numOrNull, obj, startedOf, str, type Started } from "../estimation/api";
import { IMPLICIT_API } from "./api";

const num = (v: unknown, dflt: number): number => (isNum(v) ? v : dflt);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const grid = <T>(v: unknown, read: (x: unknown) => T): T[][] => (Array.isArray(v) ? v.map((row) => (Array.isArray(row) ? row.map(read) : [])) : []);

export interface Mismatch {
  hole: string;
  hole_id: number;
  from: number | null;
  to: number | null;
  /** the middle of the interval, m: where the window 7.17 is put */
  depth: number | null;
  data_code: string;
  model_code: string;
  length: number | null;
}

export interface Tally {
  count: number;
  length: number | null;
}

export interface CompareAnswer {
  model: { id: number; name: string; stale_reason: string | null };
  table: { id: number; name: string; field: string };
  /** the columns: the names of the groups, «Прочее», «Вне модели» */
  model_codes: string[];
  /** the column of «Прочее» and of «Вне модели» */
  other_col: number | null;
  outside_col: number | null;
  /** the rows: the codes of the data (the last may be «Нет кода») */
  data_codes: string[];
  no_code_row: number | null;
  /** m, [row of the data][column of the model]; a gap is null */
  length: (number | null)[][];
  count: number[][];
  /** the cells on the diagonal: the code of the data is one of the codes of the group */
  agree: boolean[][];
  agree_length: number | null;
  total_length: number | null;
  coded_length: number | null;
  /** 0…1 of the length that has a code */
  agree_share: number | null;
  no_code: Tally;
  no_trajectory: Tally & { holes: number };
  intervals: number;
  holes: number;
  notes: string[];
  mismatches: { page: number; size: number; total: number; rows: Mismatch[] };
}

const readTally = (v: unknown): Tally => ({ count: num(obj(v).count, 0), length: numOrNull(obj(v).length) });

export const readMismatch = (raw: unknown): Mismatch => {
  const r = obj(raw);
  return { hole: str(r.hole) ?? "", hole_id: num(r.hole_id, 0), from: numOrNull(r.from), to: numOrNull(r.to), depth: numOrNull(r.depth), data_code: str(r.data_code) ?? "", model_code: str(r.model_code) ?? "", length: numOrNull(r.length) };
};

export function readCompare(raw: unknown): CompareAnswer {
  const r = obj(raw);
  const m = obj(r.model);
  const t = obj(r.table);
  const q = obj(r.mismatches);
  const lost = obj(r.no_trajectory);
  return {
    model: { id: num(m.id, 0), name: str(m.name) ?? "", stale_reason: str(m.stale_reason) },
    table: { id: num(t.id, 0), name: str(t.name) ?? "", field: str(t.field) ?? "" },
    model_codes: strs(r.model_codes),
    other_col: numOrNull(r.other_col),
    outside_col: numOrNull(r.outside_col),
    data_codes: strs(r.data_codes),
    no_code_row: numOrNull(r.no_code_row),
    length: grid(r.length, numOrNull),
    count: grid(r.count, (x) => num(x, 0)),
    agree: grid(r.agree, (x) => x === true),
    agree_length: numOrNull(r.agree_length),
    total_length: numOrNull(r.total_length),
    coded_length: numOrNull(r.coded_length),
    agree_share: numOrNull(r.agree_share),
    no_code: readTally(r.no_code),
    no_trajectory: { ...readTally(lost), holes: num(lost.holes, 0) },
    intervals: num(r.intervals, 0),
    holes: num(r.holes, 0),
    notes: strs(r.notes),
    mismatches: { page: num(q.page, 1), size: num(q.size, 500), total: num(q.total, 0), rows: Array.isArray(q.rows) ? q.rows.map(readMismatch) : [] },
  };
}

export interface CompareQuery {
  set_id: number;
  table: string;
  field: string;
  page?: number;
  size?: number;
}

export const MISMATCH_SIZES: readonly number[] = [50, 100, 250, 500, 1000];

export const loadCompare = async (id: number, q: CompareQuery): Promise<CompareAnswer> => readCompare(await api.post(`${IMPLICIT_API}/models/${id}/compare_holes`, q));

// ---- «Модель на скважины» ----

/** The request of the route: the bodies of a model, or of closed wireframes, or the field of a block model at the middles of the intervals. */
export type FromModelBody =
  | { set_id: number; name: string; model_id: number; codes?: Record<string, string> }
  | { set_id: number; name: string; mesh_ids: number[]; codes?: Record<string, string> }
  | { set_id: number; name: string; block_model_id: number; field: string; table: string };

export const startFromModel = async (body: FromModelBody): Promise<Started> => startedOf(obj(await api.post("/drillholes/from_model", body)));

export interface CodeRow {
  code: string;
  rows: number;
  length: number | null;
}

export interface FromModelResult {
  set_id: number;
  table_id: number;
  name: string;
  rows: number;
  holes: number;
  no_trajectory: number;
  /** `bodies` or `field` */
  mode: string;
  codes: CodeRow[];
  journal: string[];
  length: number | null;
  length_inside: number | null;
}

export function readFromModelResult(raw: unknown): FromModelResult | null {
  const r = obj(raw);
  if (!isNum(r.table_id)) return null;
  const rep = obj(r.report);
  return {
    set_id: num(r.set_id, 0),
    table_id: r.table_id,
    name: str(r.name) ?? "",
    rows: num(r.rows, 0),
    holes: num(r.holes, 0),
    no_trajectory: num(r.no_trajectory, 0),
    mode: str(rep.mode) ?? "bodies",
    codes: Array.isArray(rep.codes) ? rep.codes.map((c) => ({ code: str(obj(c).code) ?? "", rows: num(obj(c).rows, 0), length: numOrNull(obj(c).length) })) : [],
    journal: strs(r.journal).length ? strs(r.journal) : strs(rep.journal),
    length: numOrNull(rep.length),
    length_inside: numOrNull(rep.length_inside),
  };
}
