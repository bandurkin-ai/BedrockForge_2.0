/**
 * The form of the wizard «Оценка содержаний» (ТЗ 8.8.4: модель, ограничитель, поля назначения → пробы → метод и модель
 * вариограммы → поиск и проходы → выходные поля → задание; docs/progress/e18-api.md §3). The numbers are typed text («0,5»); a
 * form is checked here with the words of the core and the place of the wrong thing (`param`, the path of the body): nothing wrong
 * reaches the core. Steps 1–3 are edited by the wizard of 18.19; what steps 4–6 will edit (`search`, `outputs`, `trend_id`, the
 * rules of the restrictor) is kept as it was stored in `rest` and goes back in the body as it is. The ranges mirror
 * `core/bf_core/geostat/estimate/spec_parts.RANGES` (a test compares them with `ranges` of the answer of the core). No React.
 */
import { numberText, parseNumber } from "../composites/params";
import { METHOD_SHORT, METHODS, VARIOGRAM_SCALE_HINT, VARIOGRAM_SCALE_MESSAGE, type DefaultsQuery, type MethodKind, type VarioScale } from "./api";

export const STEP_TITLES = ["Модель и цель", "Пробы", "Метод и вариограмма", "Поиск и проходы", "Выходные поля", "Задание"] as const;
export const LAST_EDITED_STEP = 3;

/** `lo`/`hi` — null: no bound; `open` — the lower bound itself is not allowed. */
interface Bounds {
  lo: number | null;
  hi: number | null;
  integer?: boolean;
  open?: boolean;
  unit?: string;
}

export const RANGES: Record<"power" | "disc" | "soft_dist" | "topcut" | "mean" | "threshold", Bounds> = {
  power: { lo: 0.5, hi: 5 },
  disc: { lo: 1, hi: 10, integer: true },
  soft_dist: { lo: 0, hi: 100_000, unit: "м" },
  topcut: { lo: null, hi: null },
  mean: { lo: null, hi: null },
  threshold: { lo: null, hi: null },
};

export const NAME_MAX = 200;
export const THRESHOLDS_MAX = 20;
export const DOMAIN_VALUES_MAX = 1000;

export type WeightsChoice = { kind: "auto" } | { kind: "none" } | { kind: "field"; field: string };

export interface DomainSel {
  /** the key of the table of the domain; "" — the table of the samples */
  table: string;
  field: string;
  values: string[];
}

export interface EstimationForm {
  name: string;
  // step 1
  modelId: number | null;
  /** the field of the block model with the domains; "" — every block */
  domainField: string;
  label: string;
  filterId: number | null;
  sub: "parent" | "own";
  disc: [string, string, string];
  // step 2
  setId: number | null;
  table: string | null;
  field: string | null;
  domain: DomainSel | null;
  soft: { on: boolean; domains: string[]; dist: string };
  topcut: { mode: "none" | "field" | "value"; field: string; value: string };
  weights: WeightsChoice;
  // step 3
  method: MethodKind;
  power: string;
  meanMode: "declus" | "value";
  meanValue: string;
  /** IK: «0,5; 1; 2,5» */
  thresholds: string;
  ikModels: "per" | "median";
  variogramId: number | null;
  /** IK «модель на порог»: the model of each threshold */
  variogramIds: (number | null)[];
  /** what steps 4–6 edit: `search`, `outputs`, `trend_id`, the rules of the restrictor — as stored */
  rest: Record<string, unknown>;
}

export const EMPTY_FORM: EstimationForm = {
  name: "",
  modelId: null,
  domainField: "",
  label: "",
  filterId: null,
  sub: "parent",
  disc: ["4", "4", "2"],
  setId: null,
  table: null,
  field: null,
  domain: null,
  soft: { on: false, domains: [], dist: "0" },
  topcut: { mode: "none", field: "", value: "" },
  weights: { kind: "auto" },
  method: "ok",
  power: "2",
  meanMode: "declus",
  meanValue: "",
  thresholds: "",
  ikModels: "per",
  variogramId: null,
  variogramIds: [],
  rest: {},
};

// ---- the words under a field ----

/** The first wrong thing of the form (or the refusal of the core): the words, the hint, the path of the body. */
export interface Issue {
  param: string | null;
  code: string;
  message: string;
  hint: string;
  /** a 409 `version_conflict`: «Перечитать» / «Сохранить как новые» */
  reread?: boolean;
}

const bad = (param: string, message: string, hint: string, code = "bad_param"): Issue => ({ param, code, message, hint });

/** The step a path of the body belongs to (1-based); the rest — the last step. */
export function stepOfParam(param: string | null): number {
  const p = param ?? "";
  if (/^(name|model_id|target|sub|disc)(\.|$)/.test(p)) return 1;
  if (/^samples(\.|$)/.test(p)) return 2;
  if (/^(method|variogram|variograms)(\.|$)/.test(p)) return 3;
  if (/^(search|trend_id)(\.|$)/.test(p)) return 4;
  if (/^outputs(\.|$)/.test(p)) return 5;
  return 6;
}

/** Does the issue belong to the field `path` (itself or inside it)? */
export const issueAt = (issue: Issue | null, ...paths: string[]): Issue | null => (issue && issue.param !== null && paths.some((p) => issue.param === p || issue.param!.startsWith(`${p}.`)) ? issue : null);

// ---- numbers ----

function numberIn(text: string, r: Bounds, label: string, param: string): number | Issue {
  const v = parseNumber(text);
  const range = `${r.lo !== null ? `от ${numberText(r.lo)}` : ""}${r.hi !== null ? ` до ${numberText(r.hi)}` : ""}${r.unit ? ` ${r.unit}` : ""}`.trim();
  if (v === null) return bad(param, `${label}: не число.`, range ? `Введите число ${range}.` : "Введите число, например 0,5.");
  if (r.integer && !Number.isInteger(v)) return bad(param, `${label}: нужно целое число.`, range ? `Введите целое ${range}.` : "Введите целое число.");
  if ((r.lo !== null && (r.open ? v <= r.lo : v < r.lo)) || (r.hi !== null && v > r.hi)) return bad(param, `${label}: значение вне диапазона${range ? ` ${range}` : ""}.`, range ? `Задайте число ${range}.` : "Задайте другое число.");
  return v;
}

/** «0,5; 1; 2,5» → the numbers; empty items are not allowed (an item that is not a number is an issue with its index). */
export function thresholdsOf(text: string): number[] | Issue {
  const parts = text.split(/[;\n]/).map((s) => s.trim()).filter((s) => s !== "");
  if (!parts.length) return bad("method.ik.thresholds", "Не заданы пороги индикаторов.", `Задайте от 1 до ${THRESHOLDS_MAX} порогов через «;», например 0,5; 1; 2,5.`);
  if (parts.length > THRESHOLDS_MAX) return bad("method.ik.thresholds", `Порогов больше ${THRESHOLDS_MAX}.`, `Оставьте не больше ${THRESHOLDS_MAX}.`);
  const out: number[] = [];
  for (let i = 0; i < parts.length; i++) {
    const v = numberIn(parts[i], RANGES.threshold, "Порог индикатора", `method.ik.thresholds.${i}`);
    if (typeof v !== "number") return v;
    if (out.length && !(v > out[out.length - 1])) return bad(`method.ik.thresholds.${i}`, "Пороги должны строго возрастать.", "Упорядочьте пороги по возрастанию.");
    out.push(v);
  }
  return out;
}

export const thresholdsText = (t: readonly number[]): string => t.map(numberText).join("; ");

// ---- the scale of a model of variogram (Д-38) ----

/** Z for a model of grades, `back` for a model of Y with the back transform stored, `y` for a model of Y only. */
export const scaleOf = (transform: string, hasBack: boolean): VarioScale => (transform === "none" ? "z" : hasBack ? "back" : "y");

const scaleIssue = (param: string): Issue => ({ param, code: "variogram_scale", message: VARIOGRAM_SCALE_MESSAGE, hint: VARIOGRAM_SCALE_HINT });

// ---- the form → the body of the core ----

export interface BodyContext {
  /** the scale of the stored models of variogram by id (unknown — taken for Z: the core checks it again) */
  scales: Record<number, VarioScale>;
}

export type Built = { ok: true; body: Record<string, unknown> } | { ok: false; issue: Issue };

const needsVariogram = (f: EstimationForm): boolean => f.method === "sk" || f.method === "ok" || (f.method === "ik" && f.ikModels === "median");

/** The body of `POST /estimations` of the steps 1–3 (the rest goes as stored; absent — the defaults of the core). */
export function bodyOf(f: EstimationForm, ctx: BodyContext = { scales: {} }): Built {
  const fail = (issue: Issue): Built => ({ ok: false, issue });
  // step 1
  if (f.modelId === null) return fail(bad("model_id", "Не выбрана блочная модель.", "Выберите блочную модель, в которую пишется оценка."));
  if (f.name.trim().length > NAME_MAX) return fail(bad("name", `Имя длиннее ${NAME_MAX} знаков.`, `Сократите имя до ${NAME_MAX} знаков.`));
  if (f.domainField && !f.label.trim()) return fail(bad("target.label", "Не выбран домен блоков.", "Выберите значение поля домена модели."));
  if (!f.domainField && f.label.trim()) return fail(bad("target.domain_field", "Домен блоков без поля домена.", "Выберите поле домена модели."));
  const disc: number[] = [];
  for (let i = 0; i < 3; i++) {
    const v = numberIn(f.disc[i], RANGES.disc, `Дискретизация по ${"XYZ"[i]}`, `disc.${i}`);
    if (typeof v !== "number") return fail(v);
    disc.push(v);
  }
  // step 2
  if (f.setId === null) return fail(bad("samples.set_id", "Не выбран набор скважин.", "Выберите набор скважин с пробами."));
  if (!f.table) return fail(bad("samples.table", "Не выбрана таблица проб.", "Выберите таблицу проб или композитов."));
  if (!f.field?.trim()) return fail(bad("samples.field", "Не выбрано поле содержания.", "Выберите числовое поле."));
  let domain: Record<string, unknown> | null = null;
  if (f.domain) {
    if (!f.domain.field.trim()) return fail(bad("samples.domain.field", "Домен проб: не выбрано поле.", "Выберите поле домена проб."));
    const values = [...new Set(f.domain.values.map((v) => v.trim()).filter(Boolean))];
    if (!values.length) return fail(bad("samples.domain.values", "Домен проб: не выбраны значения.", "Отметьте одно или несколько значений."));
    if (values.length > DOMAIN_VALUES_MAX) return fail(bad("samples.domain.values", `Домен проб: больше ${DOMAIN_VALUES_MAX} значений.`, `Оставьте не больше ${DOMAIN_VALUES_MAX}.`));
    domain = { ...(f.domain.table && f.domain.table !== f.table ? { table: f.domain.table } : {}), field: f.domain.field.trim(), values };
  }
  let soft: Record<string, unknown> | null = null;
  if (f.soft.on) {
    if (!domain) return fail(bad("samples.soft", "Мягкая граница без домена проб.", "Выберите домен проб или уберите мягкую границу."));
    const dist = numberIn(f.soft.dist, RANGES.soft_dist, "Расстояние мягкой границы", "samples.soft.dist");
    if (typeof dist !== "number") return fail(dist);
    const doms = [...new Set(f.soft.domains.map((v) => v.trim()).filter(Boolean))];
    if (dist > 0 && !doms.length) return fail(bad("samples.soft.domains", "Мягкая граница: не выбраны соседние домены.", "Отметьте домены, пробы которых допускаются у границы, или задайте расстояние 0."));
    if (doms.some((v) => (domain!.values as string[]).includes(v))) return fail(bad("samples.soft.domains", "Домен мягкой границы совпадает с доменом проб.", "Уберите его из соседних доменов."));
    soft = { domains: doms, dist };
  }
  let topcut: Record<string, unknown> | null = null;
  if (f.topcut.mode === "field") {
    if (!f.topcut.field.trim()) return fail(bad("samples.topcut.field", "Урезка: не выбрано поле.", "Выберите поле урезанных содержаний или уберите урезку."));
    topcut = { field: f.topcut.field.trim() };
  } else if (f.topcut.mode === "value") {
    const v = numberIn(f.topcut.value, RANGES.topcut, "Порог урезки", "samples.topcut.value");
    if (typeof v !== "number") return fail(v);
    topcut = { value: v };
  }
  if (f.weights.kind === "field" && !f.weights.field.trim()) return fail(bad("samples.weights", "Веса: не выбрано поле.", "Выберите поле весов или «нет»."));
  const weights = f.weights.kind === "auto" ? undefined : f.weights.kind === "none" ? false : f.weights.field.trim();
  // step 3
  const method: Record<string, unknown> = { kind: f.method };
  if (f.method === "idw") {
    const p = numberIn(f.power, RANGES.power, "Степень IDW", "method.power");
    if (typeof p !== "number") return fail(p);
    method.power = p;
  }
  let thresholds: number[] = [];
  if (f.method === "sk") {
    if (f.meanMode === "value") {
      const m = numberIn(f.meanValue, RANGES.mean, "Среднее простого кригинга", "method.mean.value");
      if (typeof m !== "number") return fail(m);
      method.mean = { mode: "value", value: m };
    } else method.mean = { mode: "declus" };
  }
  if (f.method === "ik") {
    const t = thresholdsOf(f.thresholds);
    if (!Array.isArray(t)) return fail(t);
    thresholds = t;
    method.ik = { thresholds, models: f.ikModels, class_means: "declus" };
  }
  const body: Record<string, unknown> = {
    ...(f.name.trim() ? { name: f.name.trim() } : {}),
    model_id: f.modelId,
    target: { domain_field: f.domainField || null, label: f.label.trim() || null, filter_id: f.filterId, ...(f.filterId === null && f.rest.rules ? { rules: f.rest.rules } : {}) },
    sub: f.sub,
    disc,
    samples: { set_id: f.setId, table: f.table, field: f.field.trim(), ...(domain ? { domain } : {}), ...(soft ? { soft } : {}), ...(topcut ? { topcut } : {}), ...(weights !== undefined ? { weights } : {}) },
    method,
  };
  const checkScale = (id: number, param: string): Issue | null => (ctx.scales[id] === "y" ? scaleIssue(param) : null);
  if (f.method === "ik" && f.ikModels === "per") {
    const list: Record<string, unknown>[] = [];
    for (let i = 0; i < thresholds.length; i++) {
      const id = f.variogramIds[i] ?? null;
      if (id === null) return fail(bad(`variograms.${i}.model_id`, `Не выбрана модель для порога ${numberText(thresholds[i])}.`, "Выберите модель вариограммы для каждого порога."));
      const s = checkScale(id, `variograms.${i}.model_id`);
      if (s) return fail(s);
      list.push({ model_id: id, t: thresholds[i] });
    }
    body.variograms = list;
  } else if (f.variogramId !== null) {
    const s = checkScale(f.variogramId, "variogram.model_id");
    if (s) return fail(s);
    body.variogram = { model_id: f.variogramId };
  } else if (needsVariogram(f)) {
    return fail(bad("variogram.model_id", "Не выбрана модель вариограммы.", "Выберите сохранённую модель вариограммы."));
  }
  for (const k of ["search", "outputs", "trend_id"]) if (f.rest[k] !== undefined && f.rest[k] !== null) body[k] = f.rest[k];
  return { ok: true, body };
}

// ---- the defaults of the core for the data of the form ----

/** The query of `defaults`; null — the model, the set, the table or the field is not chosen yet. */
export function defaultsQueryOf(f: EstimationForm): DefaultsQuery | null {
  if (f.modelId === null || f.setId === null || !f.table || !f.field?.trim()) return null;
  const q: DefaultsQuery = { model_id: f.modelId, set_id: f.setId, table: f.table, field: f.field.trim(), method: f.method };
  if (f.domainField) q.block_domain = f.domainField;
  if (f.domain?.field.trim() && f.domain.values.length) {
    if (f.domain.table && f.domain.table !== f.table) q.domain_table = f.domain.table;
    q.domain_field = f.domain.field.trim();
    q.domain_values = f.domain.values;
  }
  if (f.weights.kind === "none") q.weights = "";
  else if (f.weights.kind === "field" && f.weights.field.trim()) q.weights = f.weights.field.trim();
  const v = f.method === "ik" && f.ikModels === "per" ? undefined : f.variogramId;
  if (v !== null && v !== undefined) q.variogram_id = v;
  return q;
}

/** The name the core gives a row without one: «‹поле› · ‹МЕТОД› · ‹домен›» (the placeholder of the field). */
export const defaultName = (f: EstimationForm): string => [f.field?.trim() || "поле", METHOD_SHORT[f.method], f.label.trim()].filter(Boolean).join(" · ");

// ---- the stored parameters → the form; the arguments of a command → the form ----

const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const text = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v : "");

/** The form of a stored row (`meta` of `GET /estimations/{id}`, §3). */
export function formOfMeta(meta: Record<string, unknown>, base: EstimationForm = EMPTY_FORM): EstimationForm {
  const target = rec(meta.target);
  const s = rec(meta.samples);
  const d = rec(s.domain);
  const so = rec(s.soft);
  const tc = rec(s.topcut);
  const m = rec(meta.method);
  const ik = rec(m.ik);
  const mean = rec(m.mean);
  const kind = (METHODS as readonly string[]).includes(String(m.kind)) ? (m.kind as MethodKind) : "ok";
  const ids = (Array.isArray(meta.variograms) ? meta.variograms : []).map((v) => (typeof rec(v).model_id === "number" ? (rec(v).model_id as number) : null));
  const disc = Array.isArray(meta.disc) && meta.disc.length === 3 ? (meta.disc.map(text) as [string, string, string]) : base.disc;
  const rest: Record<string, unknown> = {};
  for (const k of ["search", "outputs", "trend_id"]) if (meta[k] !== undefined && meta[k] !== null) rest[k] = meta[k];
  if (target.rules) rest.rules = target.rules;
  const w = s.weights;
  return {
    ...base,
    name: typeof meta.name === "string" ? meta.name : "",
    modelId: typeof meta.model_id === "number" ? meta.model_id : null,
    domainField: typeof target.domain_field === "string" ? target.domain_field : "",
    label: typeof target.label === "string" ? target.label : "",
    filterId: typeof target.filter_id === "number" ? target.filter_id : null,
    sub: meta.sub === "own" ? "own" : "parent",
    disc,
    setId: typeof s.set_id === "number" ? s.set_id : null,
    table: typeof s.table === "string" ? s.table : null,
    field: typeof s.field === "string" ? s.field : null,
    domain: Object.keys(d).length ? { table: typeof d.table === "string" ? d.table : "", field: String(d.field ?? ""), values: Array.isArray(d.values) ? d.values.map(String) : [] } : null,
    soft: Object.keys(so).length ? { on: true, domains: Array.isArray(so.domains) ? so.domains.map(String) : [], dist: text(so.dist) || "0" } : { on: false, domains: [], dist: "0" },
    topcut: typeof tc.field === "string" ? { mode: "field", field: tc.field, value: "" } : typeof tc.value === "number" ? { mode: "value", field: "", value: numberText(tc.value) } : { mode: "none", field: "", value: "" },
    weights: w === false ? { kind: "none" } : typeof w === "string" && w ? { kind: "field", field: w } : { kind: "auto" },
    method: kind,
    power: text(m.power) || "2",
    meanMode: mean.mode === "value" ? "value" : "declus",
    meanValue: text(mean.value),
    thresholds: Array.isArray(ik.thresholds) ? thresholdsText(ik.thresholds.filter((x): x is number => typeof x === "number")) : "",
    ikModels: ik.models === "median" ? "median" : "per",
    variogramId: typeof rec(meta.variogram).model_id === "number" ? (rec(meta.variogram).model_id as number) : null,
    variogramIds: ids,
    rest,
  };
}

/** The «Задать мягкую границу…» of the contact analysis (Д-28): the pair A, B and the distance → the domain of the samples A, the neighbour B. */
export interface SoftArgs {
  a: string;
  b: string;
  distance: number;
}

export function applySoft(f: EstimationForm, s: SoftArgs): EstimationForm {
  const d = f.domain ?? { table: "", field: "", values: [] };
  return { ...f, domain: { ...d, values: [s.a] }, soft: { on: true, domains: [s.b], dist: numberText(s.distance) } };
}

/** The arguments of `estimate.open` over the form (the flat ones of the command; what is not given stays). */
export function formOfArgs(a: Record<string, unknown>, base: EstimationForm): EstimationForm {
  let f = base;
  if (typeof a.method === "string" && (METHODS as readonly string[]).includes(a.method)) f = { ...f, method: a.method as MethodKind };
  if (typeof a.model_id === "number" && a.model_id !== f.modelId) f = { ...f, modelId: a.model_id, domainField: "", label: "" };
  if (typeof a.soft_a === "string" && typeof a.soft_b === "string") f = applySoft(f, { a: a.soft_a, b: a.soft_b, distance: typeof a.soft_distance === "number" ? a.soft_distance : 0 });
  return f;
}

/** The value of a label in the samples' domain by name (no regard of the case) — the preselection of the domain values. */
export function sameName(label: string, codes: readonly string[]): string[] {
  const l = label.trim().toLowerCase();
  return l ? codes.filter((c) => c.trim().toLowerCase() === l) : [];
}
