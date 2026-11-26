/**
 * The forms of the windows «Новый сценарий» and «Экономика» (ТЗ 8.11.5; ADR 0028 решения 3, 5, 29) and their conversions: the form → the part of the
 * canonical `params` of the request, the `params` of a scenario → the form, the flat arguments of the commands → the form. Everything wrong is told
 * here in Russian with a hint and the place in the form — nothing wrong reaches the core. A gap is never 0: a price that is not set is `null` («не
 * готов»), `z_ref` that is not set is the top of the grid. The list of RF is sent as the canonical list (`rfValues`). No React.
 */
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../blocks/filters/constructor";
import { numberText, parseDecimal } from "../blocks/report/state";
import { DEFAULT_LIMITS, DEFAULT_PARAMS, DEFAULT_RF, rfValues, SOLVERS, type Limits, type ModelDefaults, type PitParams, type RfRange, type Solver } from "./answers";

export type FormField = "model" | "name" | "relief" | "filter" | "restrict" | "solver" | "elements" | "density" | "mining" | "rf" | "prefix" | "general" | "rock_by" | "rock_types" | "slopes" | "domains" | "k" | "tolerance" | "q_ore" | "q_mass" | "rate";

/** The first wrong thing: the place in the form (and the path in the params, to mark a row), the words, the hint (as `{code, message, hint}` of the core). */
export interface FormIssue {
  field: FormField;
  param: string | null;
  message: string;
  hint: string;
}

export const issue = (field: FormField, message: string, hint: string, param: string | null = null): FormIssue => ({ field, param, message, hint });
export const isIssue = (v: unknown): v is FormIssue => !!v && typeof v === "object" && "field" in v && "hint" in v && "message" in v;

export type Args = Record<string, unknown>;

/** The key is in the call (`null` is a value: it clears; a key that is not there leaves the scenario as it is). */
export const has = (a: Args, k: string): boolean => a[k] !== undefined;
export const txt = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v.trim() : "");
export const numOf = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" ? parseDecimal(v) : null);

// ---- the units ----

/** The codes of the core by what a person writes. */
const UNIT_ALIASES: Record<string, string> = { "g/t": "g/t", "г/т": "g/t", gpt: "g/t", ppm: "ppm", ppb: "ppb", "%": "%" };
export const UNIT_CODES: readonly string[] = ["g/t", "ppm", "ppb", "%"];

/** `""` — the unit of the field; a code; `false` — not a unit of the list. */
export function unitCode(text: string): string | null | false {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  return UNIT_ALIASES[t] ?? false;
}

/** The unit of a field of the model (`г/т`, `%`, …) as the core's code, else null. */
export const unitOfField = (unit: string | null | undefined): string | null => (unit ? (UNIT_ALIASES[unit.trim().toLowerCase()] ?? null) : null);

// ---- the form «Экономика» ----

export interface ElementRow {
  field: string;
  /** "" — the unit of the field; else the code of the core */
  unit: string;
  price: string;
  sales: string;
  royalty: string;
}

export type DensityMode = "auto" | "field" | "value";
export type RfMode = "range" | "list";

export interface EconomicsForm {
  elements: ElementRow[];
  densityMode: DensityMode;
  densityField: string;
  densityValue: string;
  defaultDensity: string;
  cM0: string;
  zRef: string;
  hGl: string;
  cGl: string;
  rfMode: RfMode;
  rfFrom: string;
  rfTo: string;
  rfStep: string;
  rfList: string;
  prefix: string;
}

export const EMPTY_ECONOMICS: EconomicsForm = {
  elements: [],
  densityMode: "auto",
  densityField: "",
  densityValue: "",
  defaultDensity: "",
  cM0: "",
  zRef: "",
  hGl: "10",
  cGl: "0",
  rfMode: "range",
  rfFrom: numberText(DEFAULT_RF.from),
  rfTo: numberText(DEFAULT_RF.to),
  rfStep: numberText(DEFAULT_RF.step),
  rfList: "",
  prefix: "",
};

export const MAX_MONEY = 1e12;
export const MAX_DENSITY = 1e6;
export const MIN_RF = 0.01;
export const MAX_RF = 5;
const PREFIX_RE = /^[\p{L}\p{N}_]*$/u;

/** A new element row (the unit of the field). */
export const newElement = (field: string): ElementRow => ({ field, unit: "", price: "", sales: "", royalty: "" });

/** The rows the core proposes for a model (`defaults.model.elements`: `*_CUT`, else the role `grade`). */
export const proposedElements = (m: ModelDefaults | null): ElementRow[] => (m?.elements ?? []).map((e) => ({ ...newElement(e.field), unit: "" }));

/** A list of RF as the arithmetic progression it is (the window shows a range then), or null. */
export function rangeOfList(rf: readonly number[]): RfRange | null {
  if (rf.length < 3) return null;
  const step = Math.round((rf[1] - rf[0]) * 1e10) / 1e10;
  if (!(step > 0)) return null;
  const r = { from: rf[0], to: rf[rf.length - 1], step };
  const back = rfValues(r);
  return back.length === rf.length && back.every((v, i) => Math.abs(v - rf[i]) < 1e-9) ? r : null;
}

/** The form that shows the economics of `params` (a body, the defaults or the resolved params of a scenario). */
export function economicsOfParams(p: PitParams, keep: EconomicsForm = EMPTY_ECONOMICS): EconomicsForm {
  const rf = Array.isArray(p.rf) ? p.rf : rfValues(p.rf);
  const range = Array.isArray(p.rf) ? rangeOfList(p.rf) : p.rf;
  return {
    elements: p.elements.map((e) => ({ field: e.field, unit: e.unit ?? "", price: e.price === null ? "" : numberText(e.price), sales: numberText(e.sales), royalty: numberText(e.royalty) })),
    densityMode: typeof p.density === "string" ? "field" : typeof p.density === "number" ? "value" : "auto",
    densityField: typeof p.density === "string" ? p.density : "",
    densityValue: typeof p.density === "number" ? numberText(p.density) : "",
    defaultDensity: p.default_density === null ? "" : numberText(p.default_density),
    cM0: p.mining.c_m0 === null ? "" : numberText(p.mining.c_m0),
    zRef: p.mining.z_ref === null ? "" : numberText(p.mining.z_ref),
    hGl: numberText(p.mining.h_gl),
    cGl: numberText(p.mining.c_gl),
    rfMode: range ? "range" : "list",
    rfFrom: range ? numberText(range.from) : keep.rfFrom,
    rfTo: range ? numberText(range.to) : keep.rfTo,
    rfStep: range ? numberText(range.step) : keep.rfStep,
    rfList: rf.map(numberText).join("; "),
    prefix: p.prefix,
  };
}

/** The part of `params` the window «Экономика» owns. */
export type EconomicsPatch = Pick<PitParams, "elements" | "density" | "default_density" | "mining" | "rf" | "prefix">;

export type Got<T> = { ok: true; patch: T } | { ok: false; issue: FormIssue };

/** «0,8; 1; 1,2» → the sorted list without repeats, or the words of what is wrong. */
export function parseRfList(text: string, limits: Limits = DEFAULT_LIMITS): { ok: true; values: number[] } | { ok: false; message: string; hint: string } {
  const parts = text.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return { ok: false, message: "Не задан список RF.", hint: "Перечислите коэффициенты через «;», например 0,8; 1; 1,2." };
  const values: number[] = [];
  for (const s of parts) {
    const n = parseDecimal(s);
    if (n === null) return { ok: false, message: `RF «${s.slice(0, 30)}» — не число.`, hint: "Перечислите коэффициенты через «;», например 0,8; 1; 1,2." };
    if (n < MIN_RF || n > MAX_RF) return { ok: false, message: `RF ${numberText(n)} вне диапазона ${numberText(MIN_RF)}…${numberText(MAX_RF)}.`, hint: "Коэффициент цены — от 0,01 до 5; 1 — базовая цена." };
    values.push(n);
  }
  const uniq = [...new Set(values)].sort((a, b) => a - b);
  if (uniq.length > limits.shells) return { ok: false, message: `Оболочек больше ${limits.shells}.`, hint: "Оставьте в списке не больше 200 значений RF." };
  return { ok: true, values: uniq };
}

/** The range of RF of the form: its list, or the words of what is wrong (`from`, `to`, `step` are numbers; at most 200 shells). */
export function rfOfRange(f: Pick<EconomicsForm, "rfFrom" | "rfTo" | "rfStep">, limits: Limits = DEFAULT_LIMITS): { ok: true; values: number[] } | { ok: false; message: string; hint: string } {
  const from = parseDecimal(f.rfFrom);
  const to = parseDecimal(f.rfTo);
  const step = parseDecimal(f.rfStep);
  if (from === null || to === null || step === null) return { ok: false, message: "Диапазон RF задан не числами.", hint: "Заполните «от», «до» и «шаг», например 0,3; 1,5; 0,05." };
  if (from < MIN_RF || to > MAX_RF || from > MAX_RF || to < MIN_RF) return { ok: false, message: "RF вне диапазона 0,01…5.", hint: "Коэффициент цены — от 0,01 до 5; 1 — базовая цена." };
  if (!(step > 0)) return { ok: false, message: "Шаг RF должен быть больше нуля.", hint: "Например 0,05." };
  if (to < from) return { ok: false, message: "RF «до» меньше, чем «от».", hint: "Поменяйте значения местами." };
  if (Math.floor((to - from) / step + 1e-9) + 1 > limits.shells) return { ok: false, message: `Оболочек больше ${limits.shells}.`, hint: "Увеличьте шаг или сузьте диапазон RF." };
  return { ok: true, values: rfValues({ from, to, step }) };
}

/** The number of shells the form of RF gives (`null` — the form is wrong): the window shows it. */
export function shellCount(f: EconomicsForm, limits: Limits = DEFAULT_LIMITS): number | null {
  const r = f.rfMode === "list" ? parseRfList(f.rfList, limits) : rfOfRange(f, limits);
  return r.ok ? r.values.length : null;
}

export const money = (field: FormField, text: string, what: string, param: string, otherwise: number | null): { ok: true; value: number | null } | { ok: false; issue: FormIssue } => {
  if (!text.trim()) return { ok: true, value: otherwise };
  const n = parseDecimal(text);
  if (n === null) return { ok: false, issue: issue(field, `${what} — не число.`, "Введите число, например 12,5.", param) };
  if (n < 0 || n > MAX_MONEY) return { ok: false, issue: issue(field, `${what} вне диапазона 0…10¹².`, "Затраты и цены — неотрицательные числа.", param) };
  return { ok: true, value: n };
};

/** The form «Экономика» → the part of the params (the elements, the density, the mining costs, RF, the prefix). */
export function economicsPatch(f: EconomicsForm, limits: Limits = DEFAULT_LIMITS): Got<EconomicsPatch> {
  if (f.elements.length > limits.elements) return { ok: false, issue: issue("elements", `Элементов больше ${limits.elements}.`, "Уберите лишние элементы.") };
  const seen = new Set<string>();
  const elements: PitParams["elements"] = [];
  for (const [i, e] of f.elements.entries()) {
    const at = `elements.${i}`;
    const key = e.field.trim().toLowerCase();
    if (!key) return { ok: false, issue: issue("elements", "У элемента не выбрано поле.", "Выберите поле содержания из списка.", `${at}.field`) };
    if (seen.has(key)) return { ok: false, issue: issue("elements", `Поле ${e.field} выбрано дважды.`, "Каждое поле — один элемент.", `${at}.field`) };
    seen.add(key);
    const unit = unitCode(e.unit);
    if (unit === false) return { ok: false, issue: issue("elements", `Единица «${e.unit.slice(0, 20)}» не из списка.`, "Единица содержания — г/т, ppm, ppb или %; пусто — единица поля.", `${at}.unit`) };
    const price = money("elements", e.price, `Цена ${e.field}`, `${at}.price`, null);
    if (!price.ok) return price;
    const sales = money("elements", e.sales, `Сбыт ${e.field}`, `${at}.sales`, 0);
    if (!sales.ok) return sales;
    let royalty = 0;
    if (e.royalty.trim()) {
      const n = parseDecimal(e.royalty);
      if (n === null) return { ok: false, issue: issue("elements", `Роялти ${e.field} — не число.`, "Доля от выручки: от 0 до 1 (0,03 — 3 %).", `${at}.royalty`) };
      if (n < 0 || n >= 1) return { ok: false, issue: issue("elements", `Роялти ${e.field} вне диапазона [0; 1).`, "Доля от выручки: от 0 до 1 (0,03 — 3 %).", `${at}.royalty`) };
      royalty = n;
    }
    elements.push({ field: e.field.trim(), unit, price: price.value, sales: sales.value ?? 0, royalty });
  }

  let density: PitParams["density"] = null;
  if (f.densityMode === "field") {
    if (!f.densityField.trim()) return { ok: false, issue: issue("density", "Не выбрано поле плотности.", "Выберите поле или число, т/м³.") };
    density = f.densityField.trim();
  } else if (f.densityMode === "value") {
    const n = parseDecimal(f.densityValue);
    if (n === null || !(n > 0) || n > MAX_DENSITY) return { ok: false, issue: issue("density", "Плотность — число больше нуля, т/м³.", "Например 2,7.") };
    density = n;
  }
  let defaultDensity: number | null = null;
  if (f.defaultDensity.trim()) {
    const n = parseDecimal(f.defaultDensity);
    if (n === null || !(n > 0) || n > MAX_DENSITY) return { ok: false, issue: issue("density", "Плотность по умолчанию — число больше нуля, т/м³.", "Например 2,6; пусто — блоки без плотности не считаются.", "default_density") };
    defaultDensity = n;
  }

  const cM0 = money("mining", f.cM0, "Затраты на добычу c_m0", "mining.c_m0", null);
  if (!cM0.ok) return cM0;
  let zRef: number | null = null;
  if (f.zRef.trim()) {
    zRef = parseDecimal(f.zRef);
    if (zRef === null) return { ok: false, issue: issue("mining", "Отметка Z_ref — не число.", "Введите отметку, м; пусто — верх сетки модели.", "mining.z_ref") };
  }
  const hGl = parseDecimal(f.hGl.trim() ? f.hGl : String(DEFAULT_PARAMS.mining.h_gl));
  if (hGl === null || !(hGl > 0) || hGl > 1e6) return { ok: false, issue: issue("mining", "Глубина H_гл — число больше нуля, м.", "Каждые H_гл метров ниже Z_ref добавляют c_гл к затратам на добычу; по умолчанию 10.", "mining.h_gl") };
  const cGl = money("mining", f.cGl, "Прирост c_гл", "mining.c_gl", 0);
  if (!cGl.ok) return cGl;

  const rf = f.rfMode === "list" ? parseRfList(f.rfList, limits) : rfOfRange(f, limits);
  if (!rf.ok) return { ok: false, issue: issue("rf", rf.message, rf.hint, "rf") };

  if (f.prefix.length > limits.prefix || !PREFIX_RE.test(f.prefix)) return { ok: false, issue: issue("prefix", "Префикс — до 40 букв, цифр и «_».", "Он ставится перед именами полей PIT и ORE; пусто — без префикса.", "prefix") };

  return { ok: true, patch: { elements, density, default_density: defaultDensity, mining: { c_m0: cM0.value, z_ref: zRef, h_gl: hGl, c_gl: cGl.value ?? 0 }, rf: rf.values, prefix: f.prefix } };
}

/** The params with the part of a window put in (the rest stays as the scenario has it). */
export const mergeParams = (base: PitParams, patch: Partial<PitParams>): PitParams => ({ ...base, ...patch });

// ---- the form «Новый сценарий» (and «Рельеф») ----

export interface ScenarioForm {
  /** null — a new scenario; else the scenario edited (the relief and the restrictor) */
  editId: number | null;
  name: string;
  modelId: number | null;
  terrainId: number | null;
  filterId: number | null;
  restrictOn: boolean;
  restrict: Draft;
  solver: Solver;
}

export const EMPTY_SCENARIO: ScenarioForm = { editId: null, name: "", modelId: null, terrainId: null, filterId: null, restrictOn: false, restrict: EMPTY_DRAFT, solver: "auto" };

export function scenarioFormOfParams(p: PitParams, name: string, editId: number | null): ScenarioForm {
  return { editId, name, modelId: p.model_id, terrainId: p.terrain_id, filterId: p.filter_id, restrictOn: p.restrict !== null, restrict: p.restrict ? ruleToDraft(p.restrict) : EMPTY_DRAFT, solver: p.solver };
}

export type ScenarioPatch = Pick<PitParams, "model_id" | "terrain_id" | "filter_id" | "restrict" | "solver">;

export function scenarioPatch(f: ScenarioForm): Got<ScenarioPatch> {
  let restrict: Rule | null = null;
  if (f.restrictOn) {
    const c = checkDraft(f.restrict);
    if (!c.ok) return { ok: false, issue: issue("restrict", c.issue.message, c.issue.hint, "restrict") };
    restrict = c.rule;
  }
  return { ok: true, patch: { model_id: f.modelId, terrain_id: f.terrainId, filter_id: f.filterId, restrict, solver: f.solver } };
}

export function nameIssue(name: string, limits: Limits = DEFAULT_LIMITS): FormIssue | null {
  return name.length > limits.name ? issue("name", `Имя длиннее ${limits.name} знаков.`, "Сократите имя сценария.", "name") : null;
}

// ---- the flat arguments of the commands ↔ the forms ----

/** «AU:г/т:2000:5:0,03; CU:%:9000:300» — the field, the unit, the price, the sales, the royalty (an empty place is the default). */
export function elementsText(rows: readonly ElementRow[], labels: Record<string, string> = { "g/t": "г/т" }): string {
  return rows.map((r) => [r.field, r.unit ? (labels[r.unit] ?? r.unit) : "", r.price, r.sales, r.royalty].join(":").replace(/:+$/, "")).join("; ");
}

export function parseElements(text: string): ElementRow[] {
  return text.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean).map((part) => {
    const [field = "", unit = "", price = "", sales = "", royalty = ""] = part.split(":").map((x) => x.trim());
    const code = unitCode(unit);
    return { field, unit: code === false ? unit : (code ?? ""), price, sales, royalty };
  });
}

/** The arguments of `pit.economics` from the form: the same request in flat words (the window runs the command; the log and the assistant see it). */
export function economicsArgs(f: EconomicsForm, scenarioId: number | null, labels?: Record<string, string>, limits: Limits = DEFAULT_LIMITS): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = economicsPatch(f, limits);
  if (!got.ok) return got;
  const p = got.patch;
  const a: Args = { elements: elementsText(f.elements, labels) };
  if (scenarioId !== null) a.scenario_id = scenarioId;
  a.density = typeof p.density === "number" ? numberText(p.density) : p.density; // the parameter is text («2,7» or a field)
  a.default_density = p.default_density;
  Object.assign(a, { c_m0: p.mining.c_m0, z_ref: p.mining.z_ref, h_gl: p.mining.h_gl, c_gl: p.mining.c_gl });
  if (f.rfMode === "list") a.rf = (p.rf as number[]).map(numberText).join("; ");
  else Object.assign(a, { rf_from: parseDecimal(f.rfFrom), rf_to: parseDecimal(f.rfTo), rf_step: parseDecimal(f.rfStep) });
  a.prefix = p.prefix;
  return { ok: true, args: a };
}

/** Does a call carry anything of the economics (else the command opens its window)? */
export const ECONOMICS_KEYS = ["elements", "density", "default_density", "c_m0", "z_ref", "h_gl", "c_gl", "rf", "rf_from", "rf_to", "rf_step", "prefix"] as const;
export const hasEconomics = (a: Args): boolean => ECONOMICS_KEYS.some((k) => has(a, k));

/** The form that shows the economics of `base` with what the call changes (a key that is not in the call stays as the scenario has it). */
export function economicsFormOfArgs(a: Args, base: PitParams): EconomicsForm {
  const f = economicsOfParams(base);
  if (has(a, "elements")) f.elements = typeof a.elements === "string" ? parseElements(a.elements) : [];
  if (has(a, "density")) {
    const d = a.density;
    const n = typeof d === "number" ? d : typeof d === "string" ? parseDecimal(d) : null;
    if (d === null || (typeof d === "string" && !d.trim())) Object.assign(f, { densityMode: "auto", densityField: "", densityValue: "" });
    else if (n !== null) Object.assign(f, { densityMode: "value", densityValue: numberText(n), densityField: "" });
    else Object.assign(f, { densityMode: "field", densityField: String(d).trim(), densityValue: "" });
  }
  if (has(a, "default_density")) f.defaultDensity = txt(a.default_density);
  if (has(a, "c_m0")) f.cM0 = txt(a.c_m0);
  if (has(a, "z_ref")) f.zRef = txt(a.z_ref);
  if (has(a, "h_gl")) f.hGl = txt(a.h_gl);
  if (has(a, "c_gl")) f.cGl = txt(a.c_gl);
  if (has(a, "rf_from") || has(a, "rf_to") || has(a, "rf_step")) {
    const r = Array.isArray(base.rf) ? rangeOfList(base.rf) : base.rf;
    Object.assign(f, { rfMode: "range", rfFrom: has(a, "rf_from") ? txt(a.rf_from) : r ? numberText(r.from) : f.rfFrom, rfTo: has(a, "rf_to") ? txt(a.rf_to) : r ? numberText(r.to) : f.rfTo, rfStep: has(a, "rf_step") ? txt(a.rf_step) : r ? numberText(r.step) : f.rfStep });
  } else if (has(a, "rf")) {
    const l = a.rf;
    Object.assign(f, { rfMode: "list", rfList: Array.isArray(l) ? l.map((x) => txt(x)).join("; ") : txt(l) });
  }
  if (has(a, "prefix")) f.prefix = typeof a.prefix === "string" ? a.prefix.trim() : "";
  return f;
}

/** The arguments of `pit.new` (a new scenario) or of `pit.relief` (an existing one: the relief and the restrictor only) from the form. */
export function scenarioArgs(f: ScenarioForm): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = scenarioPatch(f);
  if (!got.ok) return got;
  const a: Args = {};
  if (f.editId !== null) a.scenario_id = f.editId;
  else {
    if (f.name.trim()) a.name = f.name.trim();
    if (f.modelId !== null) a.model_id = f.modelId;
    a.solver = f.solver;
  }
  a.terrain_id = got.patch.terrain_id;
  a.filter_id = got.patch.filter_id;
  a.restrict = got.patch.restrict ? JSON.stringify(got.patch.restrict) : null;
  return { ok: true, args: a };
}

export const SCENARIO_KEYS = ["name", "model_id", "terrain_id", "filter_id", "restrict", "solver"] as const;
export const hasScenarioKeys = (a: Args): boolean => SCENARIO_KEYS.some((k) => has(a, k));
export const RELIEF_KEYS = ["terrain_id", "filter_id", "restrict"] as const;
export const hasRelief = (a: Args): boolean => RELIEF_KEYS.some((k) => has(a, k));

const idArg = (a: Args, k: string): { ok: true; value: number | null } | { ok: false; issue: FormIssue } => {
  const v = a[k];
  if (v === null || v === "") return { ok: true, value: null };
  const n = numOf(v);
  if (n === null || !Number.isInteger(n) || n < 1) return { ok: false, issue: issue(k === "model_id" ? "model" : k === "terrain_id" ? "relief" : "filter", `Номер «${String(v).slice(0, 20)}» — не целое число больше нуля.`, "Номера объектов — в дереве проекта.") };
  return { ok: true, value: n };
};

/** The form that shows the arguments of a call (a key that is not in it stays as `keep` has it); a wrong argument is told. */
export function scenarioFormOfArgs(a: Args, keep: ScenarioForm): { ok: true; form: ScenarioForm } | { ok: false; issue: FormIssue } {
  const f: ScenarioForm = { ...keep };
  if (has(a, "name")) f.name = typeof a.name === "string" ? a.name.trim() : "";
  for (const [k, set] of [["model_id", "modelId"], ["terrain_id", "terrainId"], ["filter_id", "filterId"]] as const) {
    if (!has(a, k)) continue;
    const r = idArg(a, k);
    if (!r.ok) return r;
    f[set] = r.value;
  }
  if (has(a, "restrict")) {
    if (a.restrict === null || (typeof a.restrict === "string" && !a.restrict.trim())) Object.assign(f, { restrictOn: false, restrict: EMPTY_DRAFT });
    else {
      try {
        const v: unknown = typeof a.restrict === "string" ? JSON.parse(a.restrict) : a.restrict;
        if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not a tree");
        Object.assign(f, { restrictOn: true, restrict: ruleToDraft(v as Rule) });
      } catch {
        return { ok: false, issue: issue("restrict", "Ограничитель — не дерево правил в JSON.", 'Пример: {"cond": {"field": "DOM", "op": "=", "value": "A"}}.', "restrict") };
      }
    }
  }
  if (has(a, "solver")) {
    if (!SOLVERS.includes(a.solver as Solver)) return { ok: false, issue: issue("solver", "Решатель — auto, hpf или ortools.", "auto берёт HPF; ortools — запасной решатель.", "solver") };
    f.solver = a.solver as Solver;
  }
  return { ok: true, form: f };
}

// ---- a refusal of the core in its place in the form ----

/** Where in the form a refusal of the core belongs (`param` of the body, or the code when it names no place). */
export function issueOfRefusal(r: { code: string; message: string; hint: string; param: string | null }): FormIssue {
  const head = (r.param ?? "").split(".")[0];
  const byParam: Record<string, FormField> = { model_id: "model", name: "name", terrain_id: "relief", filter_id: "filter", restrict: "restrict", solver: "solver", elements: "elements", density: "density", default_density: "density", mining: "mining", rf: "rf", prefix: "prefix" };
  let field: FormField = byParam[head] ?? "general";
  if (r.code === "block_model_not_found") field = "model";
  else if (r.code === "wireframe_not_found") field = "relief";
  else if (r.code === "filter_not_found") field = "filter";
  else if (r.code === "filter_field_missing") field = "restrict";
  else if (r.code === "name_taken") field = "name";
  return issue(field, r.message, r.hint, r.param);
}
