/**
 * The client of the inspector of a block (ТЗ 7.35 Д-30; docs/progress/e18-api.md §7; core — `geostat/estimate/inspect.py`,
 * `routes_inspect.py`). `POST /geostat/estimate/inspect {model_id, block: [i, j, k] | xyz | row, params_id?, channel?, seq?}` →
 * `{model_id, model_version, block, results, warnings}`; the forms are those of the answers of the core
 * (`core/tests/data/estimate/inspect_answers.json`, `BF_REGEN_INSPECT=1`). The reasons come with their Russian label
 * (`reason_label`). A gap of an answer stays `null` — never 0 (8.0.1); the weight of a probe is `null` for IK and for the
 * blocks the core did not estimate (STATUS 2, 3). 409 `superseded` — an older request of the same channel: dropped. No React.
 */
import { api, CoreError } from "../../../shell/api";
import { isNum, numOrNull, obj, refusalOf, str, type Refusal, type Triple } from "../api";

export { refusalOf };
export type { Refusal, Triple };

export const INSPECT_PATH = "/geostat/estimate/inspect";
/** The channel of the requests of the window: a newer request overtakes an older one on the core's side. */
export const INSPECT_CHANNEL = "inspector";

/** What a block is asked by: its cell of the parent, a point, or the row of the model (the click of the 3D gives the row). */
export interface InspectRequest {
  model_id: number;
  block?: Triple;
  xyz?: Triple;
  row?: number;
  params_id?: number;
}

export interface Candidate {
  /** the number of the probe in the pool of the row */
  i: number;
  row_id: number | null;
  hole: string;
  from: number | null;
  to: number | null;
  xyz: Triple | null;
  /** the distance in the metric of the ellipsoid (h_s) */
  h: number | null;
  /** the Euclidean distance, m */
  dist: number | null;
  octant: number | null;
  z_raw: number | null;
  z: number | null;
  /** null — no weight (IK, STATUS 2, 3, not selected) */
  lambda: number | null;
  reason: string;
  reason_label: string;
  /** the number of coincident points averaged into this one */
  merged: number;
  domain: string | null;
}

export interface Segment {
  i: number;
  from: Triple;
  to: Triple;
  lambda: number | null;
  z: number | null;
}

export interface EllipsoidInfo {
  centre_xyz: Triple;
  ang1: number;
  ang2: number;
  ang3: number;
  r1: number;
  r2: number;
  r3: number;
  pass: number;
}

export interface PassInfo {
  pass: number;
  n: number;
  ok: boolean;
  reason: string | null;
}

export interface FieldCompare {
  key: string;
  name: string;
  unit: string | null;
  stored: number | null;
  calc: number | null;
  /** null — nothing to compare (the block is outside the domain of the row, the field is not in the model) */
  same: boolean | null;
  exists: boolean;
}

export interface Lineage {
  state: string;
  changed: boolean;
  reason: string | null;
}

export interface InspectResult {
  estimation_id: number;
  name: string;
  method: string;
  prefix: string;
  label: string | null;
  /** the domain of the row: the field and the value */
  domain: { field: string | null; label: string | null; row_label: string | null };
  source: { set_id: number | null; table: string | null; field: string | null };
  /** the row is applied to the block (the block is in its domain) */
  active: boolean;
  status: number | null;
  status_label: string;
  pass: number | null;
  values: Record<string, number | null>;
  mu: number | null;
  ellipsoid: EllipsoidInfo | null;
  passes: PassInfo[];
  counts: { candidates: number; selected: number; shown: number };
  candidates: Candidate[];
  segments: Segment[];
  fields: FieldCompare[];
  missing_fields: string[];
  disc: number[] | null;
  soft_dist: number | null;
  changed: boolean;
  /** «Параметры или входы изменились» or null */
  warning: string | null;
  warning_detail: string | null;
  stale_reason: string | null;
  lineage: Lineage | null;
  /** the JSON for «Скопировать снимок»: the parameters, the probes and the weights as the core wrote them */
  snapshot: unknown;
  version: number | null;
}

export interface InspectBlock {
  row: number;
  p: number | null;
  ijk: Triple;
  sub: number[] | null;
  centre: Triple;
  size: Triple;
  parent: { centre: Triple; size: Triple } | null;
  rows: number;
}

export interface InspectAnswer {
  model_id: number;
  model_version: number | null;
  block: InspectBlock;
  results: InspectResult[];
  warnings: string[];
}

const triple = (v: unknown): Triple | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function bad(): CoreError {
  return new CoreError(0, "bad_answer", "Ядро ответило инспектору блока в непонятном виде.", "Повторите щелчок по блоку. Если ошибка повторяется — перезапустите ядро и сообщите разработчику.");
}

function readCandidate(raw: unknown): Candidate {
  const o = obj(raw);
  return {
    i: Number(o.i),
    row_id: numOrNull(o.row_id),
    hole: str(o.hole) ?? "",
    from: numOrNull(o.from),
    to: numOrNull(o.to),
    xyz: triple(o.xyz),
    h: numOrNull(o.h),
    dist: numOrNull(o.dist),
    octant: numOrNull(o.octant),
    z_raw: numOrNull(o.z_raw),
    z: numOrNull(o.z),
    lambda: numOrNull(o.lambda),
    reason: str(o.reason) ?? "",
    reason_label: str(o.reason_label) ?? str(o.reason) ?? "",
    merged: isNum(o.merged) ? o.merged : 1,
    domain: str(o.domain),
  };
}

function readSegment(raw: unknown): Segment | null {
  const o = obj(raw);
  const from = triple(o.from);
  const to = triple(o.to);
  return from && to ? { i: Number(o.i), from, to, lambda: numOrNull(o.lambda), z: numOrNull(o.z) } : null;
}

function readEllipsoid(raw: unknown): EllipsoidInfo | null {
  const o = obj(raw);
  const c = triple(o.centre_xyz);
  const nums = [o.ang1, o.ang2, o.ang3, o.r1, o.r2, o.r3];
  if (!c || !nums.every(isNum)) return null;
  const [ang1, ang2, ang3, r1, r2, r3] = nums as number[];
  return { centre_xyz: c, ang1, ang2, ang3, r1, r2, r3, pass: isNum(o.pass) ? o.pass : 0 };
}

function readField(raw: unknown): FieldCompare {
  const o = obj(raw);
  return {
    key: str(o.key) ?? "",
    name: str(o.name) ?? str(o.key) ?? "",
    unit: str(o.unit),
    stored: numOrNull(o.stored),
    calc: numOrNull(o.calc),
    same: typeof o.same === "boolean" ? o.same : null,
    exists: o.exists !== false,
  };
}

export function readResult(raw: unknown): InspectResult {
  const o = obj(raw);
  const dom = obj(o.domain);
  const src = obj(o.source);
  const counts = obj(o.counts);
  const lin = obj(o.lineage);
  const values: Record<string, number | null> = {};
  for (const [k, v] of Object.entries(obj(o.values))) values[k] = numOrNull(v);
  const candidates = list(o.candidates).map(readCandidate);
  return {
    estimation_id: Number(o.estimation_id),
    name: str(o.name) ?? `Строка №${String(o.estimation_id)}`,
    method: str(o.method) ?? "",
    prefix: str(o.prefix) ?? "",
    label: str(o.label),
    domain: { field: str(dom.field), label: str(dom.label), row_label: str(dom.row_label) },
    source: { set_id: numOrNull(src.set_id), table: str(src.table) ?? (isNum(src.table) ? String(src.table) : null), field: str(src.field) },
    active: o.active !== false,
    status: numOrNull(o.status),
    status_label: str(o.status_label) ?? "",
    pass: numOrNull(o.pass),
    values,
    mu: numOrNull(o.mu),
    ellipsoid: readEllipsoid(o.ellipsoid),
    passes: list(o.passes).map((p) => {
      const x = obj(p);
      return { pass: Number(x.pass), n: isNum(x.n) ? x.n : 0, ok: x.ok === true, reason: str(x.reason) };
    }),
    counts: { candidates: isNum(counts.candidates) ? counts.candidates : candidates.length, selected: isNum(counts.selected) ? counts.selected : candidates.filter((c) => c.reason === "selected").length, shown: isNum(counts.shown) ? counts.shown : candidates.length },
    candidates,
    segments: list(o.segments).flatMap((s) => readSegment(s) ?? []),
    fields: list(o.fields).map(readField),
    missing_fields: list(o.missing_fields).filter((x): x is string => typeof x === "string"),
    disc: Array.isArray(o.disc) && o.disc.every(isNum) ? (o.disc as number[]) : null,
    soft_dist: numOrNull(o.soft_dist),
    changed: o.changed === true,
    warning: str(o.warning),
    warning_detail: str(o.warning_detail),
    stale_reason: str(o.stale_reason),
    lineage: o.lineage ? { state: str(lin.state) ?? "", changed: lin.changed === true, reason: str(lin.reason) } : null,
    snapshot: o.snapshot ?? null,
    version: numOrNull(o.version),
  };
}

export function readInspect(raw: unknown): InspectAnswer {
  const o = obj(raw);
  const b = obj(o.block);
  const ijk = triple(b.ijk);
  const centre = triple(b.centre);
  const size = triple(b.size);
  if (!isNum(o.model_id) || !ijk || !centre || !size || !Array.isArray(o.results)) throw bad();
  const par = obj(b.parent);
  const pc = triple(par.centre);
  const ps = triple(par.size);
  return {
    model_id: o.model_id,
    model_version: numOrNull(o.model_version),
    block: {
      row: isNum(b.row) ? b.row : -1,
      p: numOrNull(b.p),
      ijk,
      sub: Array.isArray(b.sub) && b.sub.every(isNum) ? (b.sub as number[]) : null,
      centre,
      size,
      parent: pc && ps ? { centre: pc, size: ps } : null,
      rows: isNum(b.rows) ? b.rows : 1,
    },
    results: o.results.map(readResult),
    warnings: list(o.warnings).filter((x): x is string => typeof x === "string"),
  };
}

let seq = 0;

/** The next number of a request of the channel (an older one answers 409 `superseded`). */
export const nextSeq = (): number => ++seq;

export async function postInspect(req: InspectRequest, mine: number = nextSeq()): Promise<InspectAnswer> {
  return readInspect(await api.post<unknown>(INSPECT_PATH, { ...req, channel: INSPECT_CHANNEL, seq: mine }));
}

/** An answer of the core that an older request got: not a failure of the window. */
export const isSuperseded = (e: unknown): boolean => e instanceof CoreError && e.code === "superseded";
