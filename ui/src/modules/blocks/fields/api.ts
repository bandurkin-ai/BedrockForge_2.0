/**
 * The client of the operations on the fields of a block model (ТЗ 8.7.4, 8.7.5; ADR 0023 §7, §9, decisions 10 and 13;
 * docs/progress/e17-api.md §8, §9; core — `blocks/{routes_calc, routes_ops, routes_fields, routes_schemes}.py`). The
 * forms are those of the answers of the core (a run of the core through TestClient:
 * `core/tests/data/blocks/fields_window_answers.json`, `core/tests/test_blocks_fields_window_answers.py`):
 *
 * - `POST /blocks/calc/check {model_id, expr, dtype?}` → `{text, fields_used, out_kind, dtype}`; 422 `expr_syntax` /
 *   `expr_field` / `expr_type {pos, line, column, char, reason, param: "expr"}` (`pos` — the character from 0, in code
 *   points), `bad_param {param}`; 404 `block_model_not_found`;
 * - `POST /blocks/{id}/calc|density|classify {expected_version, …}` → `{source, id}` of the job (`blocks.calc`,
 *   `blocks.density`, `blocks.classify`); 409 `field_exists {field}` («Заменить?», № 69), `version_conflict {version}`;
 *   422 `bad_param {param}`, `density_field_missing`, `density_invalid`, `classify_field_missing`, `scheme_incomplete`,
 *   `filter_field_missing`; 404 `filter_not_found`, `scheme_not_found`, `wireframe_not_found`; 423 `project_read_only`;
 * - `GET /blocks/schemes` → `{schemes: [{id, name, version, rules: [{label, expr | null}]}]}`; `PUT /blocks/schemes/{id}
 *   {expected_version?, name, rules}` → the scheme (409 `version_conflict {version}`, 422 `expr_syntax {param:
 *   "rules.N.expr"}`, 423);
 * - `GET /blocks/{id}/fields` → `{id, name, version, stale, stale_reason, fields: [...]}`; `PATCH …/fields/{name}
 *   {expected_version, unit?, description?, role?}` → `{version, field}`; `DELETE …/fields/{name}?expected_version=N` →
 *   `{version, deleted}` (422 `field_in_use {usage}`, `field_required`); `POST …/fields/{name}/rebuild {expected_version}`
 *   → `{source, id}` (422 `not_rebuildable`, 409 `source_lost`).
 *
 * A number that is not known stays `null`, never 0. No React.
 */
import { api, CoreError } from "../../../shell/api";
import { startedOf, type Started } from "../../qaqc/setupApi";
import type { Rule } from "../filters/constructor";
import { refusalOf, type Refusal } from "../report/api";

export type { Refusal, Started };
export { refusalOf };

export const DTYPES = ["f8", "f4", "i4", "i2", "i1", "cat"] as const;
export type Dtype = (typeof DTYPES)[number];
export type OutKind = "number" | "bool" | "category";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const count = (v: unknown): number => (isNum(v) && v >= 0 ? v : 0);

// ---- the calculator ----

export interface CalcCheck {
  /** the canonical text of the expression (a bracketed name written without brackets when it can be) */
  text: string;
  fields_used: string[];
  out_kind: OutKind;
  dtype: Dtype;
}

export function readCheck(raw: unknown): CalcCheck | null {
  const c = obj(raw);
  const kind = c.out_kind;
  if (typeof c.text !== "string" || (kind !== "number" && kind !== "bool" && kind !== "category")) return null;
  return { text: c.text, fields_used: strs(c.fields_used), out_kind: kind, dtype: (DTYPES as readonly unknown[]).includes(c.dtype) ? (c.dtype as Dtype) : "f8" };
}

/** The place of a wrong expression: the words of the core, the character (from 0, code points), the line and the column (from 1). */
export interface ExprIssue {
  code: string;
  message: string;
  hint: string;
  pos: number;
  line: number;
  column: number;
  /** the unknown field of `expr_field` */
  field: string | null;
}

/** The place the refusal of the core names in the expression; null — it names none (a refusal about another param). */
export function exprIssueOf(e: unknown): ExprIssue | null {
  if (!(e instanceof CoreError) || !e.code.startsWith("expr_")) return null;
  const b = e.body ?? {};
  if (!isNum(b.pos)) return null;
  return { code: e.code, message: e.message, hint: e.hint, pos: b.pos, line: isNum(b.line) ? b.line : 1, column: isNum(b.column) ? b.column : b.pos + 1, field: str(b.field) };
}

export const checkExpr = async (modelId: number, expr: string, dtype?: Dtype | null): Promise<CalcCheck> => {
  const got = readCheck(await api.post("/blocks/calc/check", { model_id: modelId, expr, ...(dtype ? { dtype } : {}) }));
  if (!got) throw new CoreError(0, "bad_answer", "Ядро ответило на проверку выражения без типа результата.", "Повторите проверку; если повторится — обновите программу.");
  return got;
};

/** The body of `POST /blocks/{id}/calc` (e17-api §8); a key that is absent is the default of the core. */
export interface CalcBody {
  expected_version: number;
  expr: string;
  field: string;
  dtype?: Dtype;
  unit?: string;
  description?: string;
  filter_id?: number;
  restrict?: Rule;
  outside?: "keep" | "nan";
  replace?: boolean;
}

/** The body the core shares between the operations on a field: the restrictor, the replace flag. */
export interface Common {
  expected_version: number;
  filter_id?: number;
  restrict?: Rule;
  replace?: boolean;
  description?: string;
}

export interface TableRow {
  value: string | number;
  density: number;
}

export interface DensityBody extends Common {
  method: "constant" | "field" | "table" | "formula";
  value?: number;
  field?: string;
  table?: { field: string; default?: number | null; values: TableRow[] };
  expr?: string;
  default?: number | null;
  out: string;
  src_field: string;
  unit?: string;
}

export interface SchemeRule {
  label: string;
  /** null — the threshold is not set (the template ГКЗ): such a scheme cannot classify */
  expr: string | null;
}

export interface ClassifyBody extends Common {
  scheme?: string;
  rules?: { label: string; expr: string }[];
  overrides?: { wireframe_id: number; label: string }[];
  majority?: number;
  estimate?: string | null;
  /** «Префикс полей оценки» (№ 78): `PASS` схемы читается как `<префикс>PASS`; пусто — без префикса */
  prefix?: string;
  out: string;
}

export const startCalc = async (modelId: number, body: CalcBody): Promise<Started> => startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/calc`, body));
export const startDensity = async (modelId: number, body: DensityBody): Promise<Started> => startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/density`, body));
export const startClassify = async (modelId: number, body: ClassifyBody): Promise<Started> => startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/classify`, body));
export const startRebuild = async (modelId: number, field: string, version: number): Promise<Started> =>
  startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/fields/${encodeURIComponent(field)}/rebuild`, { expected_version: version }));

// ---- the job and its result ----

/** What the three jobs answer: the field(s) written, the new version of the model, the counts (a gap is never 0). */
export interface OpResult {
  modelId: number;
  field: string;
  fields: string[];
  version: number | null;
  rows: number;
  /** blocks the restrictor lets pass */
  passed: number | null;
  /** calc: blocks with no value in the new field */
  missing: number | null;
  /** density: blocks that took the default / have no density at all */
  defaulted: number | null;
  empty: number | null;
  /** classify: blocks per label, blocks no rule fits, blocks with no estimate */
  counts: Record<string, number> | null;
  unclassified: number | null;
  noEstimate: number | null;
  dtype: string | null;
  stats: { count: number | null; missing: number | null; min: number | null; max: number | null; mean: number | null } | null;
  warnings: string[];
}

const nul = (v: unknown): number | null => (isNum(v) ? v : null);

export function readResult(raw: unknown): OpResult | null {
  const r = obj(raw);
  if (!isNum(r.model_id) || typeof r.field !== "string") return null;
  const s = obj(r.stats);
  const counts = r.counts && typeof r.counts === "object" && !Array.isArray(r.counts) ? Object.fromEntries(Object.entries(r.counts as Record<string, unknown>).filter((e): e is [string, number] => isNum(e[1]))) : null;
  return {
    modelId: r.model_id,
    field: r.field,
    fields: strs(r.fields).length ? strs(r.fields) : [r.field],
    version: nul(r.version),
    rows: count(r.rows),
    passed: nul(r.passed),
    missing: nul(r.missing),
    defaulted: nul(r.defaulted),
    empty: nul(r.empty),
    counts,
    unclassified: nul(r.unclassified),
    noEstimate: nul(r.no_estimate),
    dtype: str(r.dtype),
    stats: r.stats ? { count: nul(s.count), missing: nul(s.missing), min: nul(s.min), max: nul(s.max), mean: nul(s.mean) } : null,
    warnings: strs(r.warnings),
  };
}

export interface OpJob {
  status: string;
  progress: number;
  message: string | null;
  error: Refusal | null;
  result: OpResult | null;
}

/** The record of a job: the stage, the end (the error with its `param`), the result read in full. */
export async function loadOpJob(job: Started): Promise<OpJob> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return {
    status: str(j.status) ?? "",
    progress: nul(j.progress) ?? 0,
    message: str(j.message),
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "", param: str(e.param) } : null,
    result: readResult(j.result),
  };
}

// ---- the schemes of categories ----

export interface Scheme {
  id: string;
  name: string;
  /** the version a new write must carry (`expected_version`) */
  version: number;
  rules: SchemeRule[];
}

export function readScheme(raw: unknown): Scheme | null {
  const s = obj(raw);
  if (typeof s.id !== "string" || !Array.isArray(s.rules)) return null;
  const rules = s.rules.map(obj).map((r) => ({ label: str(r.label) ?? "", expr: str(r.expr) }));
  return { id: s.id, name: str(s.name) ?? s.id, version: nul(s.version) ?? 1, rules };
}

export const loadSchemes = async (): Promise<Scheme[]> => {
  const list = obj(await api.get("/blocks/schemes")).schemes;
  return Array.isArray(list) ? list.map(readScheme).filter((s): s is Scheme => s !== null) : [];
};

/** A new scheme goes without a version, an existing one (the boxed ones too) with the version read. */
export const putScheme = async (id: string, body: { name: string; rules: SchemeRule[]; expected_version?: number }): Promise<Scheme> => {
  const got = readScheme(await api.put(`/blocks/schemes/${encodeURIComponent(id)}`, body));
  if (!got) throw new CoreError(0, "bad_answer", "Ядро ответило без схемы категорий.", "Перечитайте список схем.");
  return got;
};

// ---- the properties of the fields ----

/** «Где используется»: another field whose rule reads this one, a view of the project, a saved view. */
export interface Usage {
  kind: "field" | "view";
  id: number | null;
  name: string;
  role: string;
}

export interface FieldSource {
  op: string;
  algo: string | null;
  params: Record<string, unknown>;
  at: string | null;
}

export interface FieldRow {
  name: string;
  kind: "number" | "category";
  dtype: string;
  unit: string | null;
  description: string | null;
  role: string | null;
  labelsCount: number;
  file: string | null;
  source: FieldSource | null;
  stale: boolean;
  staleReason: string | null;
  rebuildable: boolean;
  /** the field of the volume (role `frac`): the model needs it */
  required: boolean;
  deletable: boolean;
  usage: Usage[];
}

export interface FieldsList {
  id: number;
  name: string;
  version: number;
  stale: boolean;
  staleReason: string | null;
  fields: FieldRow[];
}

export function readUsage(raw: unknown): Usage[] {
  if (!Array.isArray(raw)) return [];
  const out: Usage[] = [];
  const seen = new Set<string>();
  for (const x of raw) {
    const u = obj(x);
    const name = str(u.name);
    if (name === null) continue;
    const row: Usage = { kind: u.kind === "view" ? "view" : "field", id: nul(u.id), name, role: str(u.role) ?? "" };
    const key = `${row.kind}:${row.id}:${row.name}:${row.role}`;
    if (!seen.has(key)) out.push(row), seen.add(key); // the core may name a field twice (the filter and the rule): one line
  }
  return out;
}

export function readFieldRow(raw: unknown): FieldRow | null {
  const f = obj(raw);
  if (typeof f.name !== "string") return null;
  const s = f.source && typeof f.source === "object" ? obj(f.source) : null;
  return {
    name: f.name,
    kind: f.kind === "category" ? "category" : "number",
    dtype: str(f.dtype) ?? "f8",
    unit: str(f.unit),
    description: str(f.description),
    role: str(f.role),
    labelsCount: count(f.labels_count),
    file: str(f.file),
    source: s && typeof s.op === "string" ? { op: s.op, algo: str(s.algo), params: obj(s.params), at: str(s.at) } : null,
    stale: f.stale === true,
    staleReason: str(f.stale_reason),
    rebuildable: f.rebuildable === true,
    required: f.required === true,
    deletable: f.deletable !== false,
    usage: readUsage(f.usage),
  };
}

export function readFields(raw: unknown): FieldsList | null {
  const r = obj(raw);
  if (!isNum(r.id) || !Array.isArray(r.fields)) return null;
  return {
    id: r.id,
    name: str(r.name) ?? "",
    version: nul(r.version) ?? 0,
    stale: r.stale === true,
    staleReason: str(r.stale_reason),
    fields: r.fields.map(readFieldRow).filter((f): f is FieldRow => f !== null),
  };
}

export const loadFields = async (modelId: number): Promise<FieldsList> => {
  const got = readFields(await api.get(`/blocks/${modelId}/fields`));
  if (!got) throw new CoreError(0, "bad_answer", "Ядро ответило без списка полей.", "Повторите чтение; если повторится — обновите программу.");
  return got;
};

/** The body of `PATCH`: the keys present change, `null` clears (the unit and the description may also be ""). */
export interface FieldPatch {
  unit?: string | null;
  description?: string | null;
  role?: string | null;
}

export async function patchField(modelId: number, name: string, version: number, patch: FieldPatch): Promise<{ version: number; field: FieldRow | null }> {
  const r = obj(await api.patch(`/blocks/${modelId}/fields/${encodeURIComponent(name)}`, { expected_version: version, ...patch }));
  return { version: nul(r.version) ?? version, field: readFieldRow(r.field) };
}

export async function deleteField(modelId: number, name: string, version: number): Promise<number> {
  const r = obj(await api.del(`/blocks/${modelId}/fields/${encodeURIComponent(name)}?expected_version=${version}`));
  return nul(r.version) ?? version;
}

/** The consumers of a 422 `field_in_use` (its body `usage`); [] — another refusal. */
export const usageOfError = (e: unknown): Usage[] => (e instanceof CoreError && e.code === "field_in_use" ? readUsage(e.body?.usage) : []);
