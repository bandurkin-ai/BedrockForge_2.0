/**
 * The answers of the core for the pit optimisation (ТЗ 8.11; ADR 0028; docs/progress/e21-api.md §2–§6, §13) and their readers. The forms are those of
 * the contract (`core/tests/data/pit/contract_answers.json`). A gap is `null` — never 0 (8.0.1): a number that is not a number is `null`, a key that
 * is missing is nothing or the default of the core, not a made-up value. The readers never throw: an answer that is not a scenario is `null`. No React.
 */
import type { Rule } from "../blocks/filters/constructor";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const num = (v: unknown): number | null => (isNum(v) ? v : null);
export const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
export const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
export const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

// ---- the parameters of a scenario (§2): the canonical form ----

export type Solver = "auto" | "hpf" | "ortools";
export const SOLVERS: readonly Solver[] = ["auto", "hpf", "ortools"];

export interface ElementParam {
  field: string;
  /** `g/t`, `ppm`, `ppb`, `%`; null — the unit of the field */
  unit: string | null;
  /** P_e, per unit of metal; null — not set («не готов») */
  price: number | null;
  /** S_e */
  sales: number;
  /** roy_e, a share [0; 1) */
  royalty: number;
}

export interface RockTypeParam {
  name: string;
  c_p: number | null;
  c_mr: number;
  rec: Record<string, number>;
  waste_only: boolean;
  air: boolean;
}

export interface SectorParam {
  az: number;
  angle: number;
}

export interface DomainParam {
  label: string;
  table: SectorParam[];
}

export interface RfRange {
  from: number;
  to: number;
  step: number;
}

export interface PitParams {
  model_id: number | null;
  elements: ElementParam[];
  /** a field of the model, a number (t/m³) or null — the field SG / with the role `density` */
  density: string | number | null;
  default_density: number | null;
  rocks: { by: Record<string, unknown> | null; types: RockTypeParam[] };
  mining: { c_m0: number | null; z_ref: number | null; h_gl: number; c_gl: number };
  /** the canonical form is the list; a request may carry the range (the core expands it) */
  rf: number[] | RfRange;
  slopes: { table: SectorParam[]; domain_field: string | null; domains: DomainParam[]; tolerance: number };
  k: number | null;
  terrain_id: number | null;
  filter_id: number | null;
  restrict: Rule | null;
  calendar: { q_ore: number | null; q_mass: number | null; rate: number };
  prefix: string;
  solver: Solver;
}

/** The list of RF of a range: the noise of the sum is removed (0,3 + 3·0,05 → 0,45), as the core does. */
export function rfValues(r: RfRange): number[] {
  if (!(r.step > 0) || !Number.isFinite(r.from) || !Number.isFinite(r.to) || r.to < r.from) return [];
  const n = Math.floor((r.to - r.from) / r.step + 1e-9);
  return Array.from({ length: n + 1 }, (_, i) => Math.round((r.from + i * r.step) * 1e10) / 1e10);
}

export const DEFAULT_RF: RfRange = { from: 0.3, to: 1.5, step: 0.05 };

/** The defaults of the core (`defaults.params`): what a key that is missing in an answer means. */
export const DEFAULT_PARAMS: PitParams = {
  model_id: null,
  elements: [],
  density: null,
  default_density: null,
  rocks: { by: null, types: [] },
  mining: { c_m0: null, z_ref: null, h_gl: 10, c_gl: 0 },
  rf: rfValues(DEFAULT_RF),
  slopes: { table: [{ az: 0, angle: 45 }], domain_field: null, domains: [], tolerance: 2.5 },
  k: null,
  terrain_id: null,
  filter_id: null,
  restrict: null,
  calendar: { q_ore: null, q_mass: null, rate: 0.08 },
  prefix: "",
  solver: "auto",
};

const sector = (v: unknown): SectorParam | null => {
  const s = obj(v);
  const angle = num(s.angle);
  return angle === null ? null : { az: num(s.az) ?? 0, angle };
};

const sectors = (v: unknown, otherwise: SectorParam[]): SectorParam[] => {
  const t = list(v).map(sector).filter((x): x is SectorParam => x !== null);
  return t.length ? t : otherwise;
};

/** The parameters of an answer (`defaults.params`, `scenario.params`, `meta.params`): a key that is missing is the default of the core. */
export function readParams(raw: unknown): PitParams {
  const r = obj(raw);
  const d = DEFAULT_PARAMS;
  const mining = obj(r.mining);
  const cal = obj(r.calendar);
  const slopes = obj(r.slopes);
  const rocks = obj(r.rocks);
  const rf = list(r.rf).filter(isNum);
  const by = obj(rocks.by);
  return {
    model_id: num(r.model_id),
    elements: list(r.elements).flatMap((x): ElementParam[] => {
      const e = obj(x);
      return typeof e.field === "string" ? [{ field: e.field, unit: str(e.unit), price: num(e.price), sales: num(e.sales) ?? 0, royalty: num(e.royalty) ?? 0 }] : [];
    }),
    density: typeof r.density === "string" ? r.density : num(r.density),
    default_density: num(r.default_density),
    rocks: {
      by: Object.keys(by).length ? by : null,
      types: list(rocks.types).flatMap((x): RockTypeParam[] => {
        const t = obj(x);
        if (typeof t.name !== "string") return [];
        const rec: Record<string, number> = {};
        for (const [k, v] of Object.entries(obj(t.rec))) if (isNum(v)) rec[k] = v;
        return [{ name: t.name, c_p: num(t.c_p), c_mr: num(t.c_mr) ?? 0, rec, waste_only: t.waste_only === true, air: t.air === true }];
      }),
    },
    mining: { c_m0: num(mining.c_m0), z_ref: num(mining.z_ref), h_gl: num(mining.h_gl) ?? d.mining.h_gl, c_gl: num(mining.c_gl) ?? d.mining.c_gl },
    rf: rf.length ? rf : [...(d.rf as number[])],
    slopes: {
      table: sectors(slopes.table, d.slopes.table),
      domain_field: str(slopes.domain_field),
      domains: list(slopes.domains).flatMap((x): DomainParam[] => {
        const g = obj(x);
        return typeof g.label === "string" ? [{ label: g.label, table: sectors(g.table, []) }] : [];
      }),
      tolerance: num(slopes.tolerance) ?? d.slopes.tolerance,
    },
    k: num(r.k),
    terrain_id: num(r.terrain_id),
    filter_id: num(r.filter_id),
    restrict: r.restrict && typeof r.restrict === "object" && !Array.isArray(r.restrict) ? (r.restrict as Rule) : null,
    calendar: { q_ore: num(cal.q_ore), q_mass: num(cal.q_mass), rate: num(cal.rate) ?? d.calendar.rate },
    prefix: str(r.prefix) ?? "",
    solver: SOLVERS.includes(r.solver as Solver) ? (r.solver as Solver) : "auto",
  };
}

// ---- defaults (§2), readiness (§3) ----

export interface Range {
  lo: number | null;
  hi: number | null;
  lo_open: boolean;
  hi_open: boolean;
  integer: boolean;
  unit: string;
}

export interface Limits {
  elements: number;
  rock_types: number;
  rock_rules: number;
  sectors: number;
  domains: number;
  shells: number;
  scenarios: number;
  name: number;
  prefix: number;
}

export const DEFAULT_LIMITS: Limits = { elements: 20, rock_types: 100, rock_rules: 50, sectors: 16, domains: 16, shells: 200, scenarios: 200, name: 200, prefix: 40 };

export interface ModelDefaults {
  /** the grades the core proposes: `*_CUT`, else the role `grade`; the unit is the field's (`g/t`, `ppm`, `ppb`, `%`) or null */
  elements: { field: string; unit: string | null }[];
  density: string | null;
  categories: { field: string; labels: string[] }[];
  z_ref: number | null;
  k_auto: number | null;
  /** the DTMs of the project: the candidates of the relief */
  terrains: { id: number; name: string }[];
}

export interface Defaults {
  params: PitParams;
  /** `grade_labels`: `g/t` → «г/т»; `price_labels`: `g/t` → «$/унц» */
  gradeLabels: Record<string, string>;
  priceLabels: Record<string, string>;
  units: string[];
  ranges: Record<string, Range>;
  limits: Limits;
  model: ModelDefaults | null;
}

const labels = (v: unknown): Record<string, string> => Object.fromEntries(Object.entries(obj(v)).filter((e): e is [string, string] => typeof e[1] === "string"));

export const DEFAULT_GRADE_LABELS: Record<string, string> = { "g/t": "г/т", ppm: "ppm", ppb: "ppb", "%": "%" };
export const DEFAULT_PRICE_LABELS: Record<string, string> = { "g/t": "$/унц", ppm: "$/унц", ppb: "$/унц", "%": "$/т" };

export function readModelDefaults(raw: unknown): ModelDefaults | null {
  const m = obj(raw);
  if (!Object.keys(m).length) return null;
  return {
    elements: list(m.elements).flatMap((x) => (typeof obj(x).field === "string" ? [{ field: obj(x).field as string, unit: str(obj(x).unit) }] : [])),
    density: str(m.density),
    categories: list(m.categories).flatMap((x) => (typeof obj(x).field === "string" ? [{ field: obj(x).field as string, labels: list(obj(x).labels).filter((l): l is string => typeof l === "string") }] : [])),
    z_ref: num(m.z_ref),
    k_auto: num(m.k_auto),
    terrains: list(m.terrains).flatMap((x) => (isNum(obj(x).id) ? [{ id: obj(x).id as number, name: str(obj(x).name) ?? `ЦТМ №${obj(x).id as number}` }] : [])),
  };
}

export function readDefaults(raw: unknown): Defaults {
  const r = obj(raw);
  const c = obj(r.choices);
  const l = obj(r.limits);
  const ranges: Record<string, Range> = {};
  for (const [k, v] of Object.entries(obj(r.ranges))) {
    const x = obj(v);
    ranges[k] = { lo: num(x.lo), hi: num(x.hi), lo_open: x.lo_open === true, hi_open: x.hi_open === true, integer: x.integer === true, unit: str(x.unit) ?? "" };
  }
  const limits = { ...DEFAULT_LIMITS };
  for (const k of Object.keys(limits) as (keyof Limits)[]) limits[k] = num(l[k]) ?? limits[k];
  const units = list(c.grade_units).filter((u): u is string => typeof u === "string");
  return {
    params: readParams(r.params),
    gradeLabels: { ...DEFAULT_GRADE_LABELS, ...labels(c.grade_labels) },
    priceLabels: { ...DEFAULT_PRICE_LABELS, ...labels(c.price_labels) },
    units: units.length ? units : Object.keys(DEFAULT_GRADE_LABELS),
    ranges,
    limits,
    model: readModelDefaults(r.model),
  };
}

export interface Missing {
  /** the place in the params: `model_id`, `elements.0.price`, `rocks.types.Окисленная.rec.CU`, `mining.c_m0` */
  param: string;
  message: string;
}

export interface Warning {
  code: string;
  param: string | null;
  message: string;
  hint: string;
}

export interface Validate {
  ready: boolean;
  missing: Missing[];
  warnings: Warning[];
}

export function readWarnings(v: unknown): Warning[] {
  return list(v).map((x) => {
    if (typeof x === "string") return { code: "warning", param: null, message: x, hint: "" };
    const w = obj(x);
    return { code: str(w.code) ?? "warning", param: str(w.param), message: str(w.message) ?? "", hint: str(w.hint) ?? "" };
  });
}

/** `POST /pit/validate` → `{ready, missing, warnings}`: `ready` is true only when nothing is missing (the one source of «Запустить»). */
export function readValidate(raw: unknown): Validate {
  const r = obj(raw);
  const missing = list(r.missing).flatMap((x): Missing[] => {
    const m = obj(x);
    return typeof m.message === "string" ? [{ param: str(m.param) ?? "", message: m.message }] : [];
  });
  return { ready: r.ready === true && missing.length === 0, missing, warnings: readWarnings(r.warnings) };
}

// ---- scenarios (§5) ----

export interface GraphState {
  stale: boolean;
  stale_reason: string | null;
  source_lost: boolean;
  pinned: boolean;
}

const readGraph = (v: unknown): GraphState => {
  const g = obj(v);
  return { stale: g.stale === true, stale_reason: str(g.stale_reason), source_lost: g.source_lost === true, pinned: g.pinned === true };
};

export type ScenarioState = "ready" | "running" | "error";

export interface ScenarioRow {
  id: number;
  name: string;
  version: number;
  state: ScenarioState;
  has_result: boolean;
  params_changed: boolean;
  model: { id: number | null; name: string };
  shells: number | null;
  /** Σ v of the last shell, $; null — no result */
  value: number | null;
  graph: GraphState;
  job_id: number | null;
}

const stateOf = (v: unknown): ScenarioState => (v === "running" || v === "error" ? v : "ready");

export function readScenarioRow(raw: unknown): ScenarioRow | null {
  const r = obj(raw);
  if (!isNum(r.id) || typeof r.name !== "string") return null;
  const m = obj(r.model);
  return {
    id: r.id,
    name: r.name,
    version: num(r.version) ?? 1,
    state: stateOf(r.state),
    has_result: r.has_result === true,
    params_changed: r.params_changed === true,
    model: { id: num(m.id), name: str(m.name) ?? "" },
    shells: num(r.shells),
    value: num(r.value),
    graph: readGraph(r.graph),
    job_id: num(r.job_id),
  };
}

export interface ScenarioError {
  code: string;
  message: string;
  hint: string;
}

export interface ScenarioDetail {
  id: number;
  name: string;
  version: number;
  state: ScenarioState;
  error: ScenarioError | null;
  has_result: boolean;
  params_changed: boolean;
  /** the canonical params (`resolved`, when there is a result) */
  params: PitParams;
  params_hash: string | null;
  /** `meta` as the core wrote it (the template, the totals, the shells); null — no result yet */
  meta: Record<string, unknown> | null;
  graph: GraphState;
  job_id: number | null;
}

export function readScenario(raw: unknown): ScenarioDetail | null {
  const r = obj(raw);
  if (!isNum(r.id) || typeof r.name !== "string") return null;
  const e = obj(r.error);
  const meta = obj(r.meta);
  return {
    id: r.id,
    name: r.name,
    version: num(r.version) ?? 1,
    state: stateOf(r.state),
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    has_result: r.has_result === true,
    params_changed: r.params_changed === true,
    params: readParams(r.params),
    params_hash: str(r.params_hash),
    meta: Object.keys(meta).length ? meta : null,
    graph: readGraph(r.graph),
    job_id: num(r.job_id),
  };
}

// ---- the result of the job `pit.run` (§6) ----

export interface RunSummary {
  scenario_id: number;
  name: string;
  version: number;
  solver: string;
  shells: { k: number; rf: number; blocks: number | null; ore_t: number | null; waste_t: number | null; value: number | null }[];
  totals: { ore_t: number | null; waste_t: number | null; strip: number | null; cf: number | null };
  q: number | null;
  objective_error: number | null;
  template: { k: number | null; error_max: number | null };
  warnings: Warning[];
  seconds: number | null;
}

export function readRunSummary(raw: unknown): RunSummary | null {
  const r = obj(raw);
  if (!isNum(r.scenario_id)) return null;
  const t = obj(r.totals);
  const tpl = obj(r.template);
  const doms = list(tpl.domains).map((d) => num(obj(obj(d).error).max)).filter(isNum);
  return {
    scenario_id: r.scenario_id,
    name: str(r.name) ?? "",
    version: num(r.version) ?? 1,
    solver: str(r.solver) ?? "",
    shells: list(r.shells).flatMap((x) => {
      const s = obj(x);
      return isNum(s.k) ? [{ k: s.k, rf: num(s.rf) ?? NaN, blocks: num(s.blocks), ore_t: num(s.ore_t), waste_t: num(s.waste_t), value: num(s.value) }] : [];
    }),
    totals: { ore_t: num(t.ore_t), waste_t: num(t.waste_t), strip: num(t.strip), cf: num(t.cf) },
    q: num(r.q),
    objective_error: num(r.objective_error),
    template: { k: num(tpl.k), error_max: doms.length ? Math.max(...doms) : null },
    warnings: readWarnings(r.warnings),
    seconds: num(obj(r.seconds).total),
  };
}

// ---- the result of the job `pit.check` (§4): the summary for the text of the command ----

export interface CheckSummary {
  k: number | null;
  k_auto: boolean;
  tolerance: number | null;
  domains: { label: string | null; n_offsets: number | null; error_max: number | null; error_mean: number | null; warning: boolean }[];
  warnings: Warning[];
}

export function readCheckSummary(raw: unknown): CheckSummary | null {
  const r = obj(raw);
  if (!Array.isArray(r.domains)) return null;
  return {
    k: num(r.k),
    k_auto: r.k_auto === true,
    tolerance: num(r.tolerance),
    domains: r.domains.map((x) => {
      const d = obj(x);
      const e = obj(d.error);
      return { label: str(d.label), n_offsets: num(d.n_offsets), error_max: num(e.max), error_mean: num(e.mean), warning: d.warning === true };
    }),
    warnings: readWarnings(r.warnings),
  };
}
