/**
 * The answers of the core for the resource report (ТЗ 8.10; ADR 0027; docs/progress/e20-api.md §3–§9) and their readers. The forms are
 * those of the contract (`core/tests/data/resources/contract_answers.json`). A gap is `null` — never 0 (8.0.1): a number that is not a
 * number is `null`, a key that is missing is nothing, not a made-up value. The readers never throw: an answer that is not a report is `null`.
 * No React.
 */

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const num = (v: unknown): number | null => (isNum(v) ? v : null);
export const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
export const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const count = (v: unknown): number => (isNum(v) && v >= 0 ? v : 0);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** A warning of the core: `code` (`unit_unknown`, `sum_mismatch`, `no_density`, `wireframe_volume`, `template_other`, `rows_truncated`, …). */
export interface Warning {
  code: string;
  message: string;
  hint: string;
  param: string | null;
}

export function readWarnings(v: unknown): Warning[] {
  return list(v).map((x) => {
    if (typeof x === "string") return { code: "warning", message: x, hint: "", param: null };
    const w = obj(x);
    return { code: str(w.code) ?? "warning", message: str(w.message) ?? "", hint: str(w.hint) ?? "", param: str(w.param) };
  });
}

export interface ElementInfo {
  field: string;
  unit: string | null;
  unit_label: string;
  /** "g" (grams: г/т, ppm, ppb) / "t" (tonnes: %) / null (the unit is unknown: no metal) */
  metal_family: string | null;
  metal_base: string | null;
}

/** The numbers of an element of a row: `grade` and `metal` are null when no block of the row has the grade / the unit is unknown. */
export interface ElementCell {
  wt: number | null;
  sum: number | null;
  grade: number | null;
  metal: number | null;
  missing_t: number;
  missing_n: number;
}

export type RowKind = "cutoff" | "all" | "balance" | "offbalance";

export interface ResultRow {
  /** the labels of the keys (`«(пусто)»` is a label); null / [] — the total */
  g: (string | null)[];
  total: boolean;
  kind: RowKind;
  /** null — «без борта» */
  cutoff: number | null;
  cutoff_to: number | null;
  blocks: number;
  volume: number | null;
  tonnes: number | null;
  elements: Record<string, ElementCell>;
}

export interface ResultKey {
  kind: "field" | "bench";
  field: string | null;
  labels: string[];
}

export interface BenchInfo {
  h: number | null;
  z0: number | null;
  z_min: number | null;
  z_max: number | null;
  j0: number | null;
  n: number | null;
}

export interface CurveElement {
  grade: (number | null)[];
  metal: (number | null)[];
}

/** The curve «тоннаж — содержание» of the total: T, ḡ and M of each element above each cut-off. */
export interface Curve {
  field: string;
  cutoffs: number[];
  blocks: (number | null)[];
  volume: (number | null)[];
  tonnes: (number | null)[];
  elements: Record<string, CurveElement>;
}

export interface Diff {
  sum: number | null;
  total: number | null;
  rel: number | null;
}

export interface StatusItem {
  code: number;
  label: string;
  blocks: number;
  volume: number | null;
  share: number | null;
}

export interface WireframeCheck {
  wireframe_id: number | null;
  name: string;
  volume_restrictor: number | null;
  volume_wireframe: number | null;
  diff_pct: number | null;
  warning: boolean;
  outside_extent: boolean;
}

/** «Контроль» (ADR 0027 решение 13; Д-27). */
export interface Control {
  tolerance: number | null;
  sum_groups: { tonnes: Diff; volume: Diff; metal: Record<string, Diff>; ok: boolean } | null;
  no_density: { blocks: number; volume: number | null };
  default_density: { blocks: number; tonnes: number | null } | null;
  no_grade: Record<string, { blocks: number; tonnes: number | null }>;
  status: Record<string, { prefix: string; items: StatusItem[] }>;
  wireframes: WireframeCheck[];
  warnings: Warning[];
}

export interface FormCell {
  /** the number in the unit of the template, not rounded; null — no value */
  value: number | null;
  /** the text as shown: the digits of the template, a decimal comma, «—» for no value */
  text: string;
}

export interface FormColumn {
  key: string;
  /** the title of the column as the core says it: «Руда, тыс. т», «Au, г/т» */
  label: string;
  /** «Балансовые» / «Забалансовые» (the form with two blocks of columns), else null */
  group: string | null;
  /** `balance` / `offbalance` of the block, else null */
  part: string | null;
  /** `tonnes`, `volume`, `grade`, `metal` */
  metric: string | null;
  element: string | null;
  unit: string;
}

export interface FormRow {
  /** `row` is a group of the template (`group` of the core), `subtotal` / `total` / `other` («Прочие») as they are */
  kind: "row" | "subtotal" | "total" | "other";
  label: string;
  cells: Record<string, FormCell>;
  /** the groups a subtotal sums */
  members: string[];
}

/** The form of the template (JORC, ГКЗ, own): the core makes the cells, the interface shows `text` as it is (ADR 0027 решение 19). */
export interface Form {
  template_id: string;
  template_name: string;
  /** the cut-off of the form (`null` — «без борта» or the form with balance blocks) */
  cutoff: number | null;
  /** the cut-offs of the report the form can show (the rows of kind `cutoff`) */
  cutoffs: number[];
  columns: FormColumn[];
  sections: { label: string | null; rows: FormRow[] }[];
  warnings: Warning[];
}

export interface Excluded {
  filtered: number;
  no_volume: number;
  no_density: number;
}

/** The result of the job `resources.report` (the preview has no `report_id`). */
export interface ReportResult {
  report_id: number | null;
  name: string;
  version: number;
  saved: boolean;
  algo: string;
  /** what the core took (the resolved canonical params): the window of a saved report is filled from it */
  params: Record<string, unknown>;
  params_hash: string;
  model: { id: number; name: string; version: number | null; cells_sha: string | null };
  elements: ElementInfo[];
  cutoff_field: string;
  density: { field: string | null; value: number | null };
  keys: ResultKey[];
  bench: BenchInfo | null;
  rows: ResultRow[];
  rows_total: number;
  truncated: boolean;
  totals: ResultRow | null;
  curve: Curve | null;
  control: Control | null;
  excluded: Excluded;
  form: Form | null;
  warnings: Warning[];
  seconds: Record<string, number>;
  /** a saved report only (`GET /resources/reports/{id}`): the state in the graph, the files written, the date */
  stale: boolean;
  stale_reason: string | null;
  files: { format: string; path: string }[];
  created_at: string | null;
}

function readCell(raw: unknown): ElementCell {
  const c = obj(raw);
  return { wt: num(c.wt), sum: num(c.sum), grade: num(c.grade), metal: num(c.metal), missing_t: count(c.missing_t), missing_n: count(c.missing_n) };
}

const KINDS: readonly RowKind[] = ["cutoff", "all", "balance", "offbalance"];

/** A row; null — not a row (no `kind` and no blocks): such an answer is not a report. */
export function readRow(raw: unknown): ResultRow | null {
  const r = obj(raw);
  if (!isNum(r.blocks) || typeof r.kind !== "string" || !KINDS.includes(r.kind as RowKind)) return null;
  const elements: Record<string, ElementCell> = {};
  for (const [k, v] of Object.entries(obj(r.elements))) elements[k] = readCell(v);
  return {
    g: list(r.g).map((x) => (typeof x === "string" ? x : null)),
    total: r.total === true,
    kind: r.kind as RowKind,
    cutoff: num(r.cutoff),
    cutoff_to: num(r.cutoff_to),
    blocks: r.blocks,
    volume: num(r.volume),
    tonnes: num(r.tonnes),
    elements,
  };
}

const nums = (v: unknown): (number | null)[] => list(v).map(num);

export function readCurve(raw: unknown): Curve | null {
  const c = obj(raw);
  const cutoffs = list(c.cutoffs);
  if (cutoffs.length < 2 || !cutoffs.every(isNum)) return null;
  const elements: Record<string, CurveElement> = {};
  for (const [k, v] of Object.entries(obj(c.elements))) elements[k] = { grade: nums(obj(v).grade), metal: nums(obj(v).metal) };
  return { field: str(c.field) ?? "", cutoffs: cutoffs as number[], blocks: nums(c.blocks), volume: nums(c.volume), tonnes: nums(c.tonnes), elements };
}

const readDiff = (raw: unknown): Diff => ({ sum: num(obj(raw).sum), total: num(obj(raw).total), rel: num(obj(raw).rel) });

export function readControl(raw: unknown): Control | null {
  const c = obj(raw);
  if (!Object.keys(c).length) return null;
  const sg = obj(c.sum_groups);
  const metal: Record<string, Diff> = {};
  for (const [k, v] of Object.entries(obj(sg.metal))) metal[k] = readDiff(v);
  const dd = obj(c.default_density);
  const nd = obj(c.no_density);
  const noGrade: Control["no_grade"] = {};
  for (const [k, v] of Object.entries(obj(c.no_grade))) noGrade[k] = { blocks: count(obj(v).blocks), tonnes: num(obj(v).tonnes) };
  const status: Control["status"] = {};
  for (const [k, v] of Object.entries(obj(c.status)))
    status[k] = {
      prefix: str(obj(v).prefix) ?? "",
      items: list(obj(v).items).map((i) => ({ code: num(obj(i).code) ?? 0, label: str(obj(i).label) ?? "", blocks: count(obj(i).blocks), volume: num(obj(i).volume), share: num(obj(i).share) })),
    };
  return {
    tolerance: num(c.tolerance),
    sum_groups: Object.keys(sg).length ? { tonnes: readDiff(sg.tonnes), volume: readDiff(sg.volume), metal, ok: sg.ok !== false } : null,
    no_density: { blocks: count(nd.blocks), volume: num(nd.volume) },
    default_density: Object.keys(dd).length ? { blocks: count(dd.blocks), tonnes: num(dd.tonnes) } : null,
    no_grade: noGrade,
    status,
    wireframes: list(c.wireframes).map((w) => ({
      wireframe_id: num(obj(w).wireframe_id),
      name: str(obj(w).name) ?? "",
      volume_restrictor: num(obj(w).volume_restrictor),
      volume_wireframe: num(obj(w).volume_wireframe),
      diff_pct: num(obj(w).diff_pct),
      warning: obj(w).warning === true,
      outside_extent: obj(w).outside_extent === true,
    })),
    warnings: readWarnings(c.warnings),
  };
}

export function readForm(raw: unknown): Form | null {
  const f = obj(raw);
  const cols = list(f.columns);
  if (!cols.length) return null;
  const tpl = obj(f.template);
  const cut = f.cutoff && typeof f.cutoff === "object" ? num(obj(f.cutoff).value) : num(f.cutoff);
  return {
    template_id: str(f.template_id) ?? str(tpl.id) ?? "",
    template_name: str(tpl.name) ?? str(f.template_id) ?? str(tpl.id) ?? "",
    cutoff: cut,
    cutoffs: list(f.cutoffs).filter(isNum),
    columns: cols.map((c) => {
      const x = obj(c);
      return { key: str(x.key) ?? "", label: str(x.title) ?? str(x.label) ?? "", group: str(x.group), part: str(x.part), metric: str(x.metric), element: str(x.element), unit: str(x.unit) ?? "" };
    }),
    sections: list(f.sections).map((s) => ({
      label: str(obj(s).title) ?? str(obj(s).label),
      rows: list(obj(s).rows).map((r) => {
        const cells: Record<string, FormCell> = {};
        for (const [k, v] of Object.entries(obj(obj(r).cells))) cells[k] = { value: num(obj(v).value), text: str(obj(v).text) ?? "—" };
        const kind = str(obj(r).type) ?? str(obj(r).kind);
        return { kind: kind === "subtotal" || kind === "total" || kind === "other" ? kind : "row", label: str(obj(r).label) ?? "", cells, members: list(obj(r).members).filter((m): m is string => typeof m === "string") };
      }),
    })),
    warnings: readWarnings(f.warnings),
  };
}

function readBench(raw: unknown): BenchInfo | null {
  const b = obj(raw);
  if (!Object.keys(b).length) return null;
  return { h: num(b.h), z0: num(b.z0), z_min: num(b.z_min), z_max: num(b.z_max), j0: num(b.j0), n: num(b.n) };
}

/** The result of the job; null when the answer is not a report (no rows, a row that is not a row, no model). */
export function readReportResult(raw: unknown): ReportResult | null {
  const r = obj(raw);
  if (!Array.isArray(r.rows) || !isNum(obj(r.model).id)) return null;
  const rows: ResultRow[] = [];
  for (const x of r.rows) {
    const row = readRow(x);
    if (!row) return null;
    rows.push(row);
  }
  const m = obj(r.model);
  const d = obj(r.density);
  const e = obj(r.excluded);
  const seconds: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(r.seconds))) if (isNum(v)) seconds[k] = v;
  return {
    report_id: num(r.report_id),
    name: str(r.name) ?? "",
    version: num(r.version) ?? 1,
    saved: r.saved === true,
    algo: str(r.algo) ?? "",
    params: obj(r.params),
    params_hash: str(r.params_hash) ?? "",
    model: { id: m.id as number, name: str(m.name) ?? "", version: num(m.version), cells_sha: str(m.cells_sha) },
    elements: list(r.elements).map((x) => ({ field: str(obj(x).field) ?? "", unit: str(obj(x).unit), unit_label: str(obj(x).unit_label) ?? str(obj(x).unit) ?? "", metal_family: str(obj(x).metal_family), metal_base: str(obj(x).metal_base) })),
    cutoff_field: str(r.cutoff_field) ?? "",
    density: { field: str(d.field), value: num(d.value) },
    keys: list(r.keys).map((k) => ({ kind: obj(k).kind === "bench" ? "bench" : "field", field: str(obj(k).field), labels: list(obj(k).labels).filter((l): l is string => typeof l === "string") })),
    bench: readBench(r.bench),
    rows,
    rows_total: num(r.rows_total) ?? rows.length,
    truncated: r.truncated === true,
    totals: r.totals && typeof r.totals === "object" ? readRow(r.totals) : null,
    curve: readCurve(r.curve),
    control: readControl(r.control),
    excluded: { filtered: count(e.filtered), no_volume: count(e.no_volume), no_density: count(e.no_density) },
    form: readForm(r.form),
    warnings: readWarnings(r.warnings),
    seconds,
    stale: r.stale === true,
    stale_reason: str(r.stale_reason),
    files: list(r.files).map((x) => ({ format: str(obj(x).format) ?? "", path: str(obj(x).path) ?? "" })),
    created_at: str(r.created_at),
  };
}

/** A line of the list of reports (the tree «Отчёты по ресурсам»): `totals` for the tree, the state in the graph. */
export interface ReportListRow {
  id: number;
  name: string;
  state: "ready" | "building" | "error";
  version: number;
  model: { id: number; name: string };
  totals: ResultRow | null;
  graph: { stale: boolean; stale_reason: string | null; source_lost: boolean; pinned: boolean };
  files: { format: string; path: string }[];
  created_at: string;
}

export function readListRow(raw: unknown): ReportListRow | null {
  const r = obj(raw);
  if (!isNum(r.id)) return null;
  const g = obj(r.graph);
  const state = r.state === "building" || r.state === "error" ? r.state : "ready";
  const t = obj(r.totals);
  return {
    id: r.id,
    name: str(r.name) ?? "",
    state,
    version: num(r.version) ?? 1,
    model: { id: num(obj(r.model).id) ?? 0, name: str(obj(r.model).name) ?? "" },
    // the totals of the list are a short row (no `kind` of the table): kept as the row of the first cut-off
    totals: Object.keys(t).length ? readRow({ g: [], total: true, ...t, kind: str(t.kind) ?? "cutoff" }) : null,
    graph: { stale: g.stale === true, stale_reason: str(g.stale_reason), source_lost: g.source_lost === true, pinned: g.pinned === true },
    files: list(r.files).map((f) => ({ format: str(obj(f).format) ?? "", path: str(obj(f).path) ?? "" })),
    created_at: str(r.created_at) ?? "",
  };
}

/** A line of the list of templates (`GET /resources/templates`). */
export interface TemplateRow {
  id: string;
  name: string;
  builtin: boolean;
  version: number;
}

export function readTemplateRow(raw: unknown): TemplateRow | null {
  const t = obj(raw);
  return typeof t.id === "string" ? { id: t.id, name: str(t.name) ?? t.id, builtin: t.builtin === true, version: num(t.version) ?? 1 } : null;
}

/** The answer of `POST /resources/reports/{id}/drill` (Д-36): the rules for `blocks.filter` and the numbers of the cell. */
export interface DrillAnswer {
  rules: Record<string, unknown>;
  cell: { blocks: number; volume: number | null; tonnes: number | null };
  stale: boolean;
  stale_reason: string | null;
  model_id: number | null;
}

export function readDrill(raw: unknown): DrillAnswer | null {
  const d = obj(raw);
  if (!Object.keys(obj(d.rules)).length) return null;
  const c = obj(d.cell);
  return { rules: obj(d.rules), cell: { blocks: count(c.blocks), volume: num(c.volume), tonnes: num(c.tonnes) }, stale: d.stale === true, stale_reason: str(d.stale_reason), model_id: num(d.model_id) };
}

/** A line of the comparison of two reports (`POST /resources/compare`, ADR 0027 решение 26). */
export interface CompareRow {
  g: (string | null)[];
  total: boolean;
  kind: RowKind;
  cutoff: number | null;
  /** "a" / "b" — the row is only in one report (no difference, not 0) */
  only_in: "a" | "b" | null;
  a: ResultRow | null;
  b: ResultRow | null;
  delta: { blocks: number | null; volume: number | null; tonnes: number | null; elements: Record<string, { grade: number | null; metal: number | null }> } | null;
}

export interface CompareAnswer {
  a: { id: number; name: string; stale: boolean };
  b: { id: number; name: string; stale: boolean };
  grids_differ: boolean;
  warnings: Warning[];
  elements: string[];
  rows: CompareRow[];
  curves: { a: Curve | null; b: Curve | null };
}

export function readCompare(raw: unknown): CompareAnswer | null {
  const c = obj(raw);
  if (!Array.isArray(c.rows) || !isNum(obj(c.a).id) || !isNum(obj(c.b).id)) return null;
  const side = (s: unknown) => ({ id: obj(s).id as number, name: str(obj(s).name) ?? "", stale: obj(s).stale === true });
  const withKey = (v: unknown, g: unknown, kind: RowKind) => (v && typeof v === "object" ? readRow({ g, total: false, kind, ...obj(v) }) : null);
  return {
    a: side(c.a),
    b: side(c.b),
    grids_differ: c.grids_differ === true,
    warnings: readWarnings(c.warnings),
    elements: list(c.elements).filter((e): e is string => typeof e === "string"),
    rows: list(c.rows).map((x): CompareRow => {
      const r = obj(x);
      const d = obj(r.delta);
      const elements: Record<string, { grade: number | null; metal: number | null }> = {};
      for (const [k, v] of Object.entries(obj(d.elements))) elements[k] = { grade: num(obj(v).grade), metal: num(obj(v).metal) };
      const kind = KINDS.includes(r.kind as RowKind) ? (r.kind as RowKind) : "cutoff";
      return {
        g: list(r.g).map((s) => (typeof s === "string" ? s : null)),
        total: r.total === true,
        kind,
        cutoff: num(r.cutoff),
        only_in: r.only_in === "a" || r.only_in === "b" ? r.only_in : null,
        a: withKey(r.a, r.g, kind),
        b: withKey(r.b, r.g, kind),
        delta: r.delta && typeof r.delta === "object" ? { blocks: num(d.blocks), volume: num(d.volume), tonnes: num(d.tonnes), elements } : null,
      };
    }),
    curves: { a: readCurve(obj(c.curves).a), b: readCurve(obj(c.curves).b) },
  };
}
