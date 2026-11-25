/**
 * The form of the wizard «Моделирование» (ТЗ 8.8.1; Д-23, Д-38; docs/progress/e18-api.md §9). It extends the form of the estimation
 * (`estimation/form.ts`): the block model, the domain of the blocks, the samples, the soft boundary, the weights are the same
 * fields with the same words, the same ranges and the same body (`targetBody`, `samplesBody`). New here: the kind (SGS, SIS, co-SGS),
 * for SIS the thresholds or the categories, the one pass of the search, the nodes, the seed, the number of realizations, the tails
 * of the back transform and the secondary variable. The numbers are typed text («0,5»); a form is checked here with the words of
 * the core and the place of the wrong thing (`param`, the path of the body). No React.
 */
import { formOfMeta as estimationFormOfMeta, EMPTY_FORM, samplesBody, targetBody, thresholdsOf, thresholdsText, NAME_MAX, type EstimationForm, type Issue } from "../estimation/form";
import { bad, isIssue, numberIn, RANGES, type Bounds } from "../estimation/formBase";
import { searchBody, type SearchForm } from "../estimation/formSearch";
import { numberText } from "../composites/params";
import { obj } from "../estimation/api";
import type { ModelRow } from "../variography/api";
import { kindOf, KIND_SHORT, SIM_KINDS, type SimKind } from "./api";

export { SIM_KINDS };
export type { Issue };

export const SIM_STEP_TITLES = ["Вид и цель", "Пробы", "Вариограмма и метод", "Поиск", "Реализации", "Задание"] as const;
export const SIM_LAST_STEP = 6;

/** The ranges of the simulation (`simulation/spec.RANGES` of the core). */
export const SIM_RANGES = {
  seed: { lo: 0, hi: Number.MAX_SAFE_INTEGER, integer: true },
  n_real: { lo: 1, hi: 100, integer: true },
  nodes: { lo: 0, hi: 64, integer: true },
  n_max: { lo: 1, hi: 128, integer: true },
  rho: { lo: -1, hi: 1 },
  power: { lo: 0, hi: null, open: true },
  hyper: { lo: 1, hi: null },
} satisfies Record<string, Bounds>;

export const CATEGORIES_MAX = 20;
export const CODE_MAX = 255;
export const LABEL_MAX = 80;

export type LowerTail = "linear" | "power" | "none";
export type UpperTail = "linear" | "power" | "hyperbolic" | "none";

export interface NScoreForm {
  ltail: LowerTail;
  utail: UpperTail;
  ltpar: string;
  utpar: string;
}

export interface SimForm extends EstimationForm {
  kind: SimKind;
  /** SIS: a continuous variable by thresholds, or the codes of the numeric field as categories */
  sisBy: "thresholds" | "categories";
  /** «1; 2; 3» */
  categories: string;
  /** «Окисленные; Переходные; Первичные»; "" — the codes themselves */
  labels: string;
  /** the one pass of the search: the multiplier of the radii and the number of samples n_max */
  factor: string;
  nMax: string;
  nodes: string;
  nReal: string;
  seed: string;
  nscore: NScoreForm;
  /** co-SGS: the numeric field of the block model */
  secField: string;
  /** ρ: «по пробам» — the core computes it from the collocated pairs; «задать» — typed */
  rhoMode: "samples" | "value";
  rho: string;
}

export const EMPTY_NSCORE: NScoreForm = { ltail: "linear", utail: "linear", ltpar: "", utpar: "" };

/** The defaults of Д-23: the seed 12345, 20 realizations, 12 nodes simulated before, one pass of n_max 24. */
export const EMPTY_SIM_FORM: SimForm = {
  ...EMPTY_FORM,
  kind: "sgs",
  sisBy: "thresholds",
  categories: "",
  labels: "",
  factor: "1",
  nMax: "24",
  nodes: "12",
  nReal: "20",
  seed: "12345",
  nscore: EMPTY_NSCORE,
  secField: "",
  rhoMode: "samples",
  rho: "0",
};

/** The name the core gives a row without one: «‹поле› · ‹ВИД› · ‹домен›» (the placeholder of the field). */
export const simDefaultName = (f: EstimationForm & { kind?: SimKind }): string => [f.field?.trim() || "поле", KIND_SHORT[f.kind ?? "sgs"], f.label.trim()].filter(Boolean).join(" · ");

// ---- the words under a field ----

/** The step a path of the body belongs to (1-based); the rest — the last step. */
export function simStepOfParam(param: string | null): number {
  const p = param ?? "";
  if (/^(name|model_id|target|kind)(\.|$)/.test(p)) return 1;
  if (/^samples(\.|$)/.test(p)) return 2;
  if (/^(variogram|variograms|method|secondary)(\.|$)/.test(p)) return 3;
  if (/^search(\.|$)/.test(p)) return 4;
  if (/^(nodes|seed|n_real|nscore)(\.|$)/.test(p)) return 5;
  return 6;
}

// ---- the models of variogram (Д-38) ----

/** What the wizard knows of the stored models of variogram (`readVariogramModels`): the rows and which have a back transform. */
export interface VarioInfo {
  rows: readonly Pick<ModelRow, "id" | "name" | "field" | "transform">[];
  backs: Record<number, boolean>;
}

/** SGS and co-SGS take a model in normal scores (a back transform is not needed: the simulation works in Y). */
export const isScoreModel = (transform: string): boolean => transform === "nscore";
/** SIS takes a model in the units of the field: of the field, of an indicator, or in normal scores with the back transform. */
export const isFieldModel = (transform: string, hasBack: boolean): boolean => transform !== "nscore" || hasBack;

/** The threshold of a model of an indicator (`indicator:0.7` → 0.7); null — not an indicator. */
export function indicatorOf(transform: string): number | null {
  if (!transform.startsWith("indicator:")) return null;
  const v = Number(transform.slice("indicator:".length).replace(",", "."));
  return Number.isFinite(v) ? v : null;
}

/** The models fit for the threshold `t` by the transform of an indicator, one for each; null for a threshold without one. */
export function matchIndicators(rows: VarioInfo["rows"], thresholds: readonly number[]): (number | null)[] {
  return thresholds.map((t) => rows.find((r) => indicatorOf(r.transform) === t)?.id ?? null);
}

const SCORE_ISSUE = (param: string): Issue => ({ param, code: "variogram_scale", message: "Для моделирования нужна модель вариограммы в нормальных оценках.", hint: "Постройте вариограмму нормальных оценок и сохраните модель." });
const FIELD_ISSUE = (param: string): Issue => ({ param, code: "variogram_scale", message: "Модель построена в нормальных оценках", hint: "Выполните обратное преобразование модели или выберите модель индикатора (в единицах поля)." });

// ---- SIS: thresholds, categories ----

/** «1; 2; 3» → the codes (whole numbers 0–255, 2–20, distinct); the words under the field otherwise. */
export function categoriesOf(text: string): number[] | Issue {
  const parts = text.split(/[;\n]/).map((s) => s.trim()).filter((s) => s !== "");
  if (parts.length < 2 || parts.length > CATEGORIES_MAX) return bad("method.ik.categories", `Категорий должно быть от 2 до ${CATEGORIES_MAX}.`, "Задайте коды категорий через «;», например 1; 2; 3.");
  const out: number[] = [];
  for (let i = 0; i < parts.length; i++) {
    const v = numberIn(parts[i], { lo: 0, hi: CODE_MAX, integer: true }, "Код категории", `method.ik.categories.${i}`);
    if (typeof v !== "number") return v;
    if (out.includes(v)) return bad(`method.ik.categories.${i}`, "Коды категорий должны быть различными.", "Уберите повторяющийся код.");
    out.push(v);
  }
  return out;
}

/** «Окисленные; Переходные; Первичные» → one name per code (≤ 80 signs); "" — none given; the words under the field otherwise. */
export function labelsOf(text: string, codes: readonly number[]): string[] | null | Issue {
  const parts = text.split(/[;\n]/).map((s) => s.trim());
  if (parts.every((s) => s === "")) return null;
  if (parts.length !== codes.length || parts.some((s) => s === "")) return bad("method.ik.labels", "Названий категорий должно быть столько же, сколько кодов.", "Задайте название для каждого кода через «;» или оставьте поле пустым.");
  const long = parts.findIndex((s) => s.length > LABEL_MAX);
  if (long >= 0) return bad(`method.ik.labels.${long}`, `Название категории длиннее ${LABEL_MAX} знаков.`, `Сократите название до ${LABEL_MAX} знаков.`);
  return parts;
}

/** Is the form a categorical SIS (no thresholds, no tails of the back transform, no top cut)? */
export const isCategorical = (f: SimForm): boolean => f.kind === "sis" && f.sisBy === "categories";

/** Does the form have a model of variogram the search can take its ellipsoid from? */
export const hasModel = (f: SimForm): boolean => (f.kind === "sis" && f.ikModels === "per" ? f.variogramIds.some((id) => id !== null) : f.variogramId !== null);

// ---- the form → the body of the core ----

export type Built = { ok: true; body: Record<string, unknown> } | { ok: false; issue: Issue };

function nscoreBody(n: NScoreForm): Record<string, unknown> | Issue {
  const par = (text: string, r: Bounds, label: string, param: string) => numberIn(text, r, label, param);
  const out: Record<string, unknown> = { ltail: n.ltail, utail: n.utail, ltpar: null, utpar: null };
  if (n.ltail === "power") {
    const v = par(n.ltpar, SIM_RANGES.power, "Показатель нижнего хвоста", "nscore.ltpar");
    if (typeof v !== "number") return v;
    out.ltpar = v;
  }
  if (n.utail === "power" || n.utail === "hyperbolic") {
    const v = par(n.utpar, n.utail === "power" ? SIM_RANGES.power : SIM_RANGES.hyper, "Показатель верхнего хвоста", "nscore.utpar");
    if (typeof v !== "number") return v;
    out.utpar = v;
  }
  return out;
}

/** The body of `POST /simulations` and of the `simulation` of `POST /simulate/run` (what is not typed is absent: the core gives its defaults). */
export function bodyOf(f: SimForm, vario: VarioInfo = { rows: [], backs: {} }): Built {
  const fail = (issue: Issue): Built => ({ ok: false, issue });
  if (f.modelId === null) return fail(bad("model_id", "Не выбрана блочная модель.", "Выберите блочную модель, для которой строятся реализации."));
  if (f.name.trim().length > NAME_MAX) return fail(bad("name", `Имя длиннее ${NAME_MAX} знаков.`, `Сократите имя до ${NAME_MAX} знаков.`));
  const target = targetBody(f);
  if (isIssue(target)) return fail(target);
  const samples = samplesBody(f);
  if (isIssue(samples)) return fail(samples);
  const cat = isCategorical(f);
  if (cat && f.topcut.mode !== "none") return fail(bad("samples.topcut", "Для категорий урезка значений не применяется.", "Уберите урезку."));
  const row = (id: number) => vario.rows.find((r) => r.id === id);
  const body: Record<string, unknown> = { ...(f.name.trim() ? { name: f.name.trim() } : {}), kind: f.kind, model_id: f.modelId, target, samples };

  // the models of variogram
  const score = (id: number, param: string): Issue | null => {
    const r = row(id);
    return r && !isScoreModel(r.transform) ? SCORE_ISSUE(param) : null;
  };
  const field = (id: number, param: string): Issue | null => {
    const r = row(id);
    return r && !isFieldModel(r.transform, vario.backs[id] === true) ? FIELD_ISSUE(param) : null;
  };
  if (f.kind === "sis") {
    let thresholds: number[] = [];
    let codes: number[] = [];
    const ik: Record<string, unknown> = { models: f.ikModels };
    if (f.sisBy === "thresholds") {
      const t = thresholdsOf(f.thresholds);
      if (!Array.isArray(t)) return fail(t);
      thresholds = t;
      ik.thresholds = t;
    } else {
      const c = categoriesOf(f.categories);
      if (!Array.isArray(c)) return fail(c);
      codes = c;
      ik.categories = c;
      const l = labelsOf(f.labels, c);
      if (isIssue(l)) return fail(l);
      if (l) ik.labels = l;
    }
    body.method = { ik };
    if (f.ikModels === "per") {
      const list: Record<string, unknown>[] = [];
      const count = thresholds.length || codes.length;
      for (let i = 0; i < count; i++) {
        const id = f.variogramIds[i] ?? null;
        const what = thresholds.length ? `порога ${numberText(thresholds[i])}` : `категории ${codes[i]}`;
        if (id === null) return fail(bad(`variograms.${i}.model_id`, `Не выбрана модель для ${what}.`, "Выберите модель индикатора для каждого порога (категории)."));
        const s = field(id, `variograms.${i}.model_id`);
        if (s) return fail(s);
        list.push({ model_id: id, ...(thresholds.length ? { t: thresholds[i] } : {}) });
      }
      body.variograms = list;
    } else {
      if (f.variogramId === null) return fail(bad("variogram.model_id", "Не выбрана модель вариограммы.", "Выберите сохранённую модель индикатора."));
      const s = field(f.variogramId, "variogram.model_id");
      if (s) return fail(s);
      body.variogram = { model_id: f.variogramId };
    }
  } else {
    if (f.variogramId === null) return fail(bad("variogram.model_id", "Не выбрана модель вариограммы.", "Выберите сохранённую модель в нормальных оценках."));
    const s = score(f.variogramId, "variogram.model_id");
    if (s) return fail(s);
    body.variogram = { model_id: f.variogramId };
  }

  // the secondary variable of the co-SGS
  if (f.kind === "cosgs") {
    if (!f.secField.trim()) return fail(bad("secondary.field", "Для ко-SGS не выбрана вторичная переменная.", "Выберите числовое поле блочной модели, например плотность."));
    let rho: number | null = null;
    if (f.rhoMode === "value") {
      const v = numberIn(f.rho, SIM_RANGES.rho, "Коэффициент корреляции ρ", "secondary.rho");
      if (typeof v !== "number") return fail(v);
      rho = v;
    }
    body.secondary = { field: f.secField.trim(), rho };
  }

  // the search: the ellipsoid (the model or by hand) and the one pass
  const sb = searchBody({ ...f.search, passes: null }, hasModel(f));
  if (isIssue(sb)) return fail(sb);
  const factor = numberIn(f.factor, RANGES.factor, "Множитель радиусов", "search.passes.0.factor");
  if (typeof factor !== "number") return fail(factor);
  const nMax = numberIn(f.nMax, SIM_RANGES.n_max, "Число проб в окрестности узла n_max", "search.passes.0.n_max");
  if (typeof nMax !== "number") return fail(nMax);
  body.search = { ...(sb.search ?? {}), passes: [{ factor, n_max: nMax }] };

  // the realizations
  for (const [key, text, label] of [["nodes", f.nodes, "Число ранее смоделированных узлов"], ["n_real", f.nReal, "Число реализаций"], ["seed", f.seed, "Зерно"]] as const) {
    const v = numberIn(text, SIM_RANGES[key], label, key);
    if (typeof v !== "number") return fail(v);
    body[key] = v;
  }
  if (!cat) {
    const n = nscoreBody(f.nscore);
    if (isIssue(n)) return fail(n);
    body.nscore = n;
  }
  return { ok: true, body };
}

// ---- the stored parameters → the form; the arguments of a command → the form ----

const text = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v : "");

/** The form of a stored row (`meta` of `GET /simulations/{id}`, §9). */
export function formOfMeta(meta: Record<string, unknown>, base: SimForm = EMPTY_SIM_FORM): SimForm {
  const common = estimationFormOfMeta({ ...meta, method: undefined }, base) as SimForm;
  const ik = obj(obj(meta.method).ik);
  const cats = Array.isArray(ik.categories) ? ik.categories.map(text) : [];
  const labels = Array.isArray(ik.labels) ? ik.labels.map(text) : [];
  const ns = obj(meta.nscore);
  const sec = obj(meta.secondary);
  const pass = obj(Array.isArray(obj(meta.search).passes) ? (obj(meta.search).passes as unknown[])[0] : null);
  const search: SearchForm = { ...common.search, passes: null, ...(common.search.from === "trend" ? { from: null } : {}) };
  return {
    ...common,
    // what an estimation form has and a simulation does not: back to the defaults
    method: EMPTY_FORM.method,
    sub: "parent",
    outputs: base.outputs,
    search,
    kind: kindOf(meta.kind),
    sisBy: cats.length ? "categories" : "thresholds",
    thresholds: Array.isArray(ik.thresholds) ? thresholdsText(ik.thresholds.filter((x): x is number => typeof x === "number")) : "",
    categories: cats.join("; "),
    labels: labels.join("; "),
    ikModels: ik.models === "median" ? "median" : "per",
    factor: text(pass.factor) || base.factor,
    nMax: text(pass.n_max) || base.nMax,
    nodes: text(meta.nodes) || base.nodes,
    nReal: text(meta.n_real) || base.nReal,
    seed: typeof meta.seed === "number" ? String(meta.seed) : base.seed,
    nscore: {
      ltail: ns.ltail === "power" || ns.ltail === "none" ? ns.ltail : "linear",
      utail: ns.utail === "power" || ns.utail === "hyperbolic" || ns.utail === "none" ? ns.utail : "linear",
      ltpar: text(ns.ltpar),
      utpar: text(ns.utpar),
    },
    secField: typeof sec.field === "string" ? sec.field : "",
    rhoMode: typeof sec.rho === "number" ? "value" : "samples",
    rho: typeof sec.rho === "number" ? numberText(sec.rho) : "0",
  };
}

const TAIL_L = ["linear", "power", "none"];
const TAIL_U = ["linear", "power", "hyperbolic", "none"];

/** The flat arguments of `simulate.sgs`, `simulate.sis`, `simulate.cosgs` over the form (what is not given stays). */
export function formOfArgs(kind: SimKind, a: Record<string, unknown>, base: SimForm): SimForm {
  const num = (k: string): string | null => (typeof a[k] === "number" ? String(a[k]) : typeof a[k] === "string" && (a[k] as string).trim() ? (a[k] as string).trim() : null);
  const list = (k: string): string[] => (typeof a[k] === "string" ? (a[k] as string).split(/[;\n]/).map((s) => s.trim()).filter(Boolean) : []);
  const ids = (k: string): (number | null)[] => list(k).map((s) => (Number.isInteger(Number(s)) ? Number(s) : null));
  let f: SimForm = { ...base, kind };
  if (typeof a.model_id === "number" && a.model_id !== f.modelId) f = { ...f, modelId: a.model_id, domainField: "", label: "" };
  if (typeof a.domain_field === "string") f = { ...f, domainField: a.domain_field };
  if (typeof a.label === "string") f = { ...f, label: a.label };
  if (typeof a.name === "string") f = { ...f, name: a.name };
  if (typeof a.set_id === "number") f = { ...f, setId: a.set_id };
  if (typeof a.table === "string" || typeof a.table === "number") f = { ...f, table: String(a.table) };
  if (typeof a.field === "string") f = { ...f, field: a.field };
  if (typeof a.sample_domain_field === "string") f = { ...f, domain: { table: typeof a.domain_table === "string" ? a.domain_table : "", field: a.sample_domain_field, values: list("domain_values") } };
  if (typeof a.variogram_id === "number") f = { ...f, variogramId: a.variogram_id };
  if (typeof a.variogram_ids === "string") f = { ...f, variogramIds: ids("variogram_ids") };
  if (kind === "sis") {
    if (typeof a.thresholds === "string" && a.thresholds.trim()) f = { ...f, sisBy: "thresholds", thresholds: list("thresholds").join("; ") };
    if (typeof a.categories === "string" && a.categories.trim()) f = { ...f, sisBy: "categories", categories: list("categories").join("; "), labels: list("labels").join("; ") };
    if (a.models === "per" || a.models === "median") f = { ...f, ikModels: a.models };
  }
  if (kind === "cosgs") {
    if (typeof a.secondary_field === "string") f = { ...f, secField: a.secondary_field };
    if (typeof a.rho === "number") f = { ...f, rhoMode: "value", rho: String(a.rho) };
  }
  const search = a.search_from === "manual" || a.search_from === "model" ? { ...f.search, from: a.search_from as "model" | "manual" } : f.search;
  const r = [num("r1"), num("r2"), num("r3")];
  const ang = [num("ang1"), num("ang2"), num("ang3")];
  f = { ...f, search: { ...search, ...(r.some(Boolean) ? { from: "manual" as const, r: r.map((x) => x ?? "") as [string, string, string] } : {}), ...(ang.some(Boolean) ? { ang: ang.map((x) => x ?? "") as [string, string, string] } : {}) } };
  for (const [key, prop] of [["n_real", "nReal"], ["seed", "seed"], ["nodes", "nodes"], ["n_max", "nMax"], ["factor", "factor"]] as const) {
    const v = num(key);
    if (v !== null) f = { ...f, [prop]: v };
  }
  if (typeof a.ltail === "string" && TAIL_L.includes(a.ltail)) f = { ...f, nscore: { ...f.nscore, ltail: a.ltail as LowerTail } };
  if (typeof a.utail === "string" && TAIL_U.includes(a.utail)) f = { ...f, nscore: { ...f.nscore, utail: a.utail as UpperTail } };
  return f;
}
