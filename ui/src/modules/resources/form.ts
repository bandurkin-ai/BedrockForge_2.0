/**
 * The form of the window «Отчёт по ресурсам» (ТЗ 8.10; ADR 0027 решения 3–11, 29) and its conversions: the form → `params` of the request,
 * `params` (as the window or the core has them: a body or the resolved canonical params of a saved report) → the form, the flat
 * arguments of the command → the form. Everything wrong is told here in Russian with a hint and the place in the form — nothing wrong
 * reaches the core. A gap is never 0: «без борта» is `null`, a field without a value is not a number. No React.
 */
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../blocks/filters/constructor";
import { numberText, parseDecimal } from "../blocks/report/state";
import { DEFAULT_LIMITS, type CutoffsSpec, type GroupKey, type Limits, type ResourceParams } from "./api";

export type FormField = "model" | "elements" | "cutoff_field" | "cutoffs" | "balance" | "density" | "filter" | "restrict" | "pit" | "below" | "groups" | "bench" | "curve" | "template" | "name" | "general";

/** The first wrong thing: the place in the form, the words, the hint (as `{code, message, hint}` of the core). */
export interface FormIssue {
  field: FormField;
  message: string;
  hint: string;
}

export const issue = (field: FormField, message: string, hint: string): FormIssue => ({ field, message, hint });
export const isIssue = (v: unknown): v is FormIssue => !!v && typeof v === "object" && "field" in v && "hint" in v && "message" in v;

export type DensityMode = "auto" | "field" | "value";
export type CutoffMode = "list" | "range";
/** A key of the group: a category field or the bench («Уступ»). */
export type KeyChoice = { kind: "field"; field: string } | { kind: "bench" };

export interface ElementRow {
  field: string;
  /** "" — the unit of the field */
  unit: string;
}

/** «Карьер» (ADR 0027 решение 8): the field `PIT` up to a number (the field comes with Э21), or the wireframe of the pit. */
export interface PitForm {
  on: boolean;
  mode: "field" | "wireframe";
  n: string;
  wireframeId: number | null;
  by: "frac" | "centre";
}

/** «Ниже рельефа»: the blocks below a surface (a DTM), by the share or by the centre. */
export interface BelowForm {
  on: boolean;
  surfaceId: number | null;
  by: "frac" | "centre";
}

export interface ResourceForm {
  modelId: number | null;
  /** [] — the core takes the fields `*_CUT`, else those with the role «содержание» (7.31) */
  elements: ElementRow[];
  /** "" — the first element */
  cutoffField: string;
  cutoffMode: CutoffMode;
  /** «без борта; 0,5; 1»: the word «без борта» is the row of all the blocks (a block without the value of the cut-off field too) */
  cutoffs: string;
  rangeFrom: string;
  rangeTo: string;
  rangeStep: string;
  balanceOn: boolean;
  balanceCutoff: string;
  balanceOff: string;
  densityMode: DensityMode;
  densityField: string;
  densityValue: string;
  /** a filter of the library; null — none */
  filterId: number | null;
  restrictOn: boolean;
  restrict: Draft;
  pit: PitForm;
  below: BelowForm;
  /** ≤ 3 keys in the order of the columns of the table */
  groups: KeyChoice[];
  benchH: string;
  benchZ0: string;
  benchZMin: string;
  benchZMax: string;
  curveOn: boolean;
  curvePoints: string;
  /** "" — no form */
  templateId: string;
  /** the name of the saved report; "" — «Отчёт по ресурсам» */
  name: string;
}

export const NO_CUTOFF_WORD = "без борта";
export const EMPTY_FORM: ResourceForm = {
  modelId: null,
  elements: [],
  cutoffField: "",
  cutoffMode: "list",
  cutoffs: NO_CUTOFF_WORD,
  rangeFrom: "",
  rangeTo: "",
  rangeStep: "",
  balanceOn: false,
  balanceCutoff: "",
  balanceOff: "",
  densityMode: "auto",
  densityField: "",
  densityValue: "2,7",
  filterId: null,
  restrictOn: false,
  restrict: EMPTY_DRAFT,
  pit: { on: false, mode: "field", n: "1", wireframeId: null, by: "frac" },
  below: { on: false, surfaceId: null, by: "centre" },
  groups: [],
  benchH: "10",
  benchZ0: "",
  benchZMin: "",
  benchZMax: "",
  curveOn: true,
  curvePoints: "100",
  templateId: "",
  name: "",
};

/** The units the window offers (the core takes any text: another unit has no metal, a warning is told). */
export const UNITS: readonly string[] = ["г/т", "ppm", "ppb", "%"];
export const MAX_NAME = 200;
export const MAX_UNIT = 20;
const MAX_ABS = 1e12;
const MAX_CURVE = 500;
const MIN_CURVE = 2;

// ---- the numbers and the lists of the form ----

const WORD = /^(без\s+борта|без|все|all|null|—|-)$/i;

/** «без борта; 0,5; 1» → [null, 0.5, 1]. Parted by «;» or a new line (a decimal comma is a number). */
export function parseCutoffList(text: string, max = DEFAULT_LIMITS.cutoffs): { ok: true; values: (number | null)[] } | { ok: false; message: string; hint: string } {
  const parts = text.split(/[;\n]+/).map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return { ok: false, message: "Не заданы борта.", hint: "Введите «без борта» (все блоки) или числа через «;», например без борта; 0,5; 1." };
  const values: (number | null)[] = [];
  for (const p of parts) {
    if (WORD.test(p)) {
      values.push(null);
      continue;
    }
    const n = parseDecimal(p.replace(/,$/, ""));
    if (n === null) return { ok: false, message: `Борт «${p.slice(0, 40)}» — не число.`, hint: "Перечислите борта через «;», например 0,5; 1; 1,5. «Без борта» — строка по всем блокам." };
    if (Math.abs(n) > MAX_ABS) return { ok: false, message: `Борт «${p.slice(0, 40)}» слишком велик.`, hint: "Борт — число не больше 10¹² по модулю." };
    values.push(n);
  }
  const unique = values.filter((v, i) => values.indexOf(v) === i);
  if (unique.length > max) return { ok: false, message: `Бортов больше ${max}.`, hint: `Оставьте не больше ${max} бортов.` };
  return { ok: true, values: unique };
}

/** Elements as typed: «AU; CU:%» or «AU:г/т, CU:%» → rows; the unit follows a colon. */
export function parseElements(text: string): ElementRow[] {
  const seen = new Set<string>();
  const out: ElementRow[] = [];
  for (const part of text.split(/[;,\n]+/)) {
    const [f, ...u] = part.split(":");
    const field = f.trim();
    if (!field || seen.has(field)) continue;
    seen.add(field);
    out.push({ field, unit: u.join(":").trim() });
  }
  return out;
}

export const elementsText = (rows: readonly ElementRow[]): string => rows.map((r) => (r.unit ? `${r.field}:${r.unit}` : r.field)).join("; ");

/** «CLASS; уступ» → keys; «bench» / «уступ» / «Уступ» is the bench. */
export function parseKeys(text: string): KeyChoice[] {
  const out: KeyChoice[] = [];
  for (const part of text.split(/[;,\n]+/)) {
    const t = part.trim();
    if (!t) continue;
    out.push(/^(bench|уступ)$/i.test(t) ? { kind: "bench" } : { kind: "field", field: t });
  }
  return out;
}

export const keysText = (keys: readonly KeyChoice[]): string => keys.map((k) => (k.kind === "bench" ? "уступ" : k.field)).join("; ");

export const keyLabel = (k: KeyChoice): string => (k.kind === "bench" ? "Уступ" : k.field);
export const sameKey = (a: KeyChoice, b: KeyChoice): boolean => a.kind === b.kind && (a.kind === "bench" || (b.kind === "field" && a.field === b.field));

const optNumber = (text: string): number | null => (text.trim() === "" ? null : parseDecimal(text));

// ---- the restrictor: the shortcuts «Карьер», «Ниже рельефа» and the tree of the window are one tree (ADR 0027 решение 8) ----

/** The leaves of the shortcuts, then the tree of the window: one rule, or null (nothing restricts). */
export function restrictOfForm(f: ResourceForm): { ok: true; rule: Rule | null } | { ok: false; issue: FormIssue } {
  const items: Rule[] = [];
  if (f.restrictOn) {
    const c = checkDraft(f.restrict);
    if (!c.ok) return { ok: false, issue: issue("restrict", c.issue.message, c.issue.hint) };
    items.push(c.rule);
  }
  if (f.pit.on) {
    if (f.pit.mode === "field") {
      const n = parseDecimal(f.pit.n);
      if (n === null || !(n > 0)) return { ok: false, issue: issue("pit", "Номер карьера — число больше нуля.", "Введите номер очереди (поле PIT), например 3: считаются блоки PIT ≤ 3.") };
      // PIT = 0 is «outside every shell» (ADR 0028 decision 22): the shortcut takes the blocks of the shells 1…n only
      items.push({ op: "and", items: [{ expr: "PIT >= 1" }, { expr: `PIT <= ${String(n)}` }] });
    } else {
      if (f.pit.wireframeId === null) return { ok: false, issue: issue("pit", "Не выбран каркас карьера.", "Выберите замкнутый каркас из списка.") };
      items.push({ wireframe: { id: f.pit.wireframeId, side: "inside", by: f.pit.by, m: 4 } });
    }
  }
  if (f.below.on) {
    if (f.below.surfaceId === null) return { ok: false, issue: issue("below", "Не выбран рельеф.", "Выберите поверхность (ЦТМ) из списка.") };
    items.push({ surface: { id: f.below.surfaceId, side: "below", by: f.below.by } });
  }
  if (!items.length) return { ok: true, rule: null };
  return { ok: true, rule: items.length === 1 ? items[0] : { op: "and", items } };
}

// ---- the form → the request ----

export interface Built {
  params: ResourceParams;
  /** the name of the saved report, if typed */
  name?: string;
}

/** The request of the form (everything is checked here: a number, a list, a range, a tree), or the first wrong thing in its place. */
export function paramsOfForm(f: ResourceForm, limits: Limits = DEFAULT_LIMITS): { ok: true; built: Built } | { ok: false; issue: FormIssue } {
  if (f.modelId === null) return { ok: false, issue: issue("model", "Не выбрана блочная модель.", "Выберите модель в списке.") };
  const params: ResourceParams = { model_id: f.modelId };

  if (f.elements.length) {
    if (f.elements.length > limits.elements) return { ok: false, issue: issue("elements", `Элементов больше ${limits.elements}.`, `Оставьте не больше ${limits.elements} элементов.`) };
    for (const e of f.elements) if (e.unit.length > MAX_UNIT) return { ok: false, issue: issue("elements", `Единица «${e.unit.slice(0, 24)}…» длиннее ${MAX_UNIT} знаков.`, "Единица — короткий текст: г/т, ppm, ppb, %.") };
    params.elements = f.elements.map((e) => (e.unit ? { field: e.field, unit: e.unit } : e.field));
  }
  if (f.cutoffField) params.cutoff_field = f.cutoffField;

  if (f.cutoffMode === "range") {
    const from = parseDecimal(f.rangeFrom);
    const to = parseDecimal(f.rangeTo);
    const step = parseDecimal(f.rangeStep);
    if (from === null || to === null || step === null) return { ok: false, issue: issue("cutoffs", "Диапазон бортов: заполните «от», «до» и «шаг» числами.", "Например от 0,5 до 3 с шагом 0,5.") };
    if (!(step > 0)) return { ok: false, issue: issue("cutoffs", "Шаг бортов должен быть больше нуля.", "Например 0,5.") };
    if (to < from) return { ok: false, issue: issue("cutoffs", "Борт «до» меньше борта «от».", "Поменяйте границы диапазона местами.") };
    if (Math.abs(from) > MAX_ABS || Math.abs(to) > MAX_ABS) return { ok: false, issue: issue("cutoffs", "Борт слишком велик.", "Борт — число не больше 10¹² по модулю.") };
    const n = Math.floor((to - from) / step + 1e-9) + 1;
    if (n > limits.cutoffs) return { ok: false, issue: issue("cutoffs", `Бортов больше ${limits.cutoffs}.`, "Увеличьте шаг или сузьте диапазон.") };
    params.cutoffs = { from, to, step };
  } else {
    const c = parseCutoffList(f.cutoffs, limits.cutoffs);
    if (!c.ok) return { ok: false, issue: issue("cutoffs", c.message, c.hint) };
    params.cutoffs = c.values;
  }

  if (f.balanceOn) {
    const c = parseDecimal(f.balanceCutoff);
    if (c === null) return { ok: false, issue: issue("balance", "Не задан балансовый борт.", "Введите число, например 1: балансовые — блоки со значением поля борта не меньше него.") };
    const hasOff = f.balanceOff.trim() !== "";
    const o = hasOff ? parseDecimal(f.balanceOff) : null;
    if (hasOff && o === null) return { ok: false, issue: issue("balance", "Забалансовый борт — не число.", "Введите число меньше балансового борта или оставьте поле пустым.") };
    if (o !== null && !(o < c)) return { ok: false, issue: issue("balance", "Забалансовый борт должен быть меньше балансового.", "Например балансовый 1, забалансовый 0,5.") };
    params.balance = o === null ? { cutoff: c } : { cutoff: c, off_cutoff: o };
  }

  if (f.densityMode === "field") {
    if (!f.densityField) return { ok: false, issue: issue("density", "Не выбрано поле плотности.", "Выберите поле или задайте число, например 2,7.") };
    params.density = f.densityField;
  } else if (f.densityMode === "value") {
    const n = parseDecimal(f.densityValue);
    if (n === null || !(n > 0)) return { ok: false, issue: issue("density", "Плотность должна быть числом больше нуля.", "Например 2,7 (т/м³) или выберите поле плотности.") };
    params.density = n;
  }

  if (f.filterId !== null) params.filter_id = f.filterId;
  const r = restrictOfForm(f);
  if (!r.ok) return r;
  if (r.rule) params.restrict = r.rule;

  if (f.groups.length) {
    if (f.groups.length > limits.group_keys) return { ok: false, issue: issue("groups", `Ключей группы больше ${limits.group_keys}.`, `Оставьте не больше ${limits.group_keys} ключей.`) };
    const keys: GroupKey[] = [];
    for (const [i, k] of f.groups.entries()) {
      if (f.groups.findIndex((x) => sameKey(x, k)) !== i) return { ok: false, issue: issue("groups", `Ключ «${keyLabel(k)}» повторяется.`, "Уберите повтор: каждый ключ — один раз.") };
      keys.push(k.kind === "bench" ? { kind: "bench" } : k.field);
    }
    params.groups = keys;
    if (f.groups.some((k) => k.kind === "bench")) {
      const h = parseDecimal(f.benchH);
      if (h === null || !(h > 0) || h > 1000) return { ok: false, issue: issue("bench", "Высота уступа — число больше 0 и не больше 1000 м.", "Например 10.") };
      const z0 = optNumber(f.benchZ0);
      const zMin = optNumber(f.benchZMin);
      const zMax = optNumber(f.benchZMax);
      for (const [text, v, name] of [[f.benchZ0, z0, "Отметка Z₀"], [f.benchZMin, zMin, "Нижняя отметка"], [f.benchZMax, zMax, "Верхняя отметка"]] as const)
        if (text.trim() !== "" && v === null) return { ok: false, issue: issue("bench", `${name} — не число.`, "Введите число в метрах или оставьте поле пустым.") };
      if (zMin !== null && zMax !== null && !(zMin < zMax)) return { ok: false, issue: issue("bench", "Нижняя отметка должна быть меньше верхней.", "Или оставьте обе пустыми: берётся охват модели по Z.") };
      params.bench = { h, ...(z0 !== null ? { z0 } : {}), ...(zMin !== null ? { z_min: zMin } : {}), ...(zMax !== null ? { z_max: zMax } : {}) };
    }
  }

  if (f.curveOn) {
    const c = parseDecimal(f.curvePoints);
    if (c === null || !Number.isInteger(c) || c < MIN_CURVE || c > Math.min(MAX_CURVE, limits.curve)) return { ok: false, issue: curveIssue() };
    params.curve = c;
  } else params.curve = 0;

  if (f.templateId) params.template_id = f.templateId;

  const name = f.name.trim();
  if (Array.from(name).length > MAX_NAME) return { ok: false, issue: issue("name", `Название длиннее ${MAX_NAME} знаков.`, "Сократите название отчёта.") };
  return { ok: true, built: name ? { params, name } : { params } };
}

const curveIssue = (): FormIssue => issue("curve", `Точек кривой: от ${MIN_CURVE} до ${MAX_CURVE}, либо кривая выключена.`, "Введите целое число, например 100.");

// ---- the request (or the resolved params of a report) → the form ----

const canonUnit = (u: string): string => (u === "g/t" ? "г/т" : u);

const txt = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v : "");

/**
 * The form that shows `params`: a body the window sent, or the resolved canonical params of a saved report (the window of a saved report is
 * filled from it). What a form cannot hold stays out (an unknown key); the restrictor is one tree (the shortcuts are in it).
 */
export function formOfParams(p: Record<string, unknown>, keep: ResourceForm = EMPTY_FORM): ResourceForm {
  const id = typeof p.model_id === "number" ? p.model_id : keep.modelId;
  const f: ResourceForm = { ...EMPTY_FORM, modelId: id, name: keep.modelId === id ? keep.name : "", pit: { ...EMPTY_FORM.pit }, below: { ...EMPTY_FORM.below } };
  if (Array.isArray(p.elements)) f.elements = p.elements.flatMap((e): ElementRow[] => (typeof e === "string" ? [{ field: e, unit: "" }] : e && typeof e === "object" && typeof (e as { field?: unknown }).field === "string" ? [{ field: (e as { field: string }).field, unit: canonUnit(txt((e as { unit?: unknown }).unit)) }] : []));
  if (typeof p.cutoff_field === "string") f.cutoffField = p.cutoff_field;
  const c = p.cutoffs as CutoffsSpec | null | undefined;
  if (Array.isArray(c)) Object.assign(f, { cutoffMode: "list", cutoffs: c.map((v) => (v === null ? NO_CUTOFF_WORD : numberText(typeof v === "number" ? v : (parseDecimal(String(v)) ?? 0)))).join("; ") });
  else if (c && typeof c === "object") Object.assign(f, { cutoffMode: "range", rangeFrom: txt(c.from), rangeTo: txt(c.to), rangeStep: txt(c.step) });
  const b = p.balance as { cutoff?: unknown; off_cutoff?: unknown } | null | undefined;
  if (b && typeof b === "object") Object.assign(f, { balanceOn: true, balanceCutoff: txt(b.cutoff), balanceOff: txt(b.off_cutoff) });
  if (typeof p.density === "string") Object.assign(f, { densityMode: "field", densityField: p.density });
  else if (typeof p.density === "number") Object.assign(f, { densityMode: "value", densityValue: numberText(p.density) });
  if (typeof p.filter_id === "number") f.filterId = p.filter_id;
  if (p.restrict && typeof p.restrict === "object" && !Array.isArray(p.restrict)) Object.assign(f, { restrictOn: true, restrict: ruleToDraft(p.restrict as Rule) });
  if (Array.isArray(p.groups)) f.groups = p.groups.flatMap((g): KeyChoice[] => (typeof g === "string" ? [{ kind: "field", field: g }] : g && typeof g === "object" && (g as { kind?: unknown }).kind === "bench" ? [{ kind: "bench" }] : g && typeof g === "object" && typeof (g as { field?: unknown }).field === "string" ? [{ kind: "field", field: (g as { field: string }).field }] : []));
  const bn = p.bench as Record<string, unknown> | null | undefined;
  if (bn && typeof bn === "object") Object.assign(f, { benchH: bn.h === undefined || bn.h === null ? EMPTY_FORM.benchH : txt(bn.h), benchZ0: txt(bn.z0), benchZMin: txt(bn.z_min), benchZMax: txt(bn.z_max) });
  if (typeof p.curve === "number") Object.assign(f, { curveOn: p.curve > 0, curvePoints: p.curve > 0 ? String(p.curve) : keep.curvePoints });
  f.templateId = typeof p.template_id === "string" ? p.template_id : "";
  return f;
}

// ---- the flat arguments of the command ↔ the form ----

export type Args = Record<string, unknown>;

const given = (a: Args, k: string): boolean => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

/** The arguments of `resources.report` from the form: the same request in flat words (the window runs the command; the log and the assistant see it). */
export function argsOfForm(f: ResourceForm, save: boolean): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = paramsOfForm(f);
  if (!got.ok) return got;
  const p = got.built.params;
  const args: Args = { model_id: f.modelId, save };
  if (f.elements.length) args.elements = elementsText(f.elements);
  if (p.cutoff_field) args.cutoff_field = p.cutoff_field;
  if (f.cutoffMode === "range" && p.cutoffs && !Array.isArray(p.cutoffs)) Object.assign(args, { cutoff_from: p.cutoffs.from, cutoff_to: p.cutoffs.to, cutoff_step: p.cutoffs.step });
  else args.cutoffs = f.cutoffs;
  if (p.balance) Object.assign(args, { balance_cutoff: p.balance.cutoff, ...(p.balance.off_cutoff !== undefined ? { balance_off: p.balance.off_cutoff } : {}) });
  if (p.density !== undefined) args.density = typeof p.density === "number" ? numberText(p.density) : p.density; // the parameter is text («2,7» or a field)
  if (p.filter_id !== undefined) args.filter_id = p.filter_id;
  if (p.restrict) args.restrict = JSON.stringify(p.restrict);
  if (f.groups.length) args.groups = keysText(f.groups);
  if (p.bench) Object.assign(args, { bench_h: p.bench.h, ...(p.bench.z0 !== undefined ? { bench_z0: p.bench.z0 } : {}), ...(p.bench.z_min !== undefined ? { bench_z_min: p.bench.z_min } : {}), ...(p.bench.z_max !== undefined ? { bench_z_max: p.bench.z_max } : {}) });
  args.curve = p.curve;
  if (p.template_id) args.template_id = p.template_id;
  if (got.built.name) args.name = got.built.name;
  return { ok: true, args };
}

/** The form that shows the arguments of a call (the window then reads the same request the core gets); a wrong argument is told. */
export function formOfArgs(a: Args, keep: ResourceForm = EMPTY_FORM): { ok: true; form: ResourceForm } | { ok: false; issue: FormIssue } {
  const id = a.model_id;
  if (typeof id !== "number" || !Number.isInteger(id)) return { ok: false, issue: issue("model", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.") };
  const f: ResourceForm = { ...EMPTY_FORM, modelId: id, pit: { ...EMPTY_FORM.pit }, below: { ...EMPTY_FORM.below }, templateId: "", name: "" };
  if (given(a, "elements")) f.elements = parseElements(String(a.elements));
  if (given(a, "cutoff_field")) f.cutoffField = String(a.cutoff_field).trim();
  if (given(a, "cutoff_from") || given(a, "cutoff_to") || given(a, "cutoff_step")) Object.assign(f, { cutoffMode: "range", rangeFrom: txt(a.cutoff_from), rangeTo: txt(a.cutoff_to), rangeStep: txt(a.cutoff_step) });
  else if (given(a, "cutoffs")) f.cutoffs = String(a.cutoffs);
  if (given(a, "balance_cutoff")) Object.assign(f, { balanceOn: true, balanceCutoff: txt(a.balance_cutoff), balanceOff: given(a, "balance_off") ? txt(a.balance_off) : "" });
  if (given(a, "density")) {
    const n = typeof a.density === "number" ? a.density : parseDecimal(String(a.density));
    if (n !== null) Object.assign(f, { densityMode: "value", densityValue: numberText(n) });
    else Object.assign(f, { densityMode: "field", densityField: String(a.density).trim() });
  }
  if (given(a, "filter_id")) {
    if (typeof a.filter_id !== "number" || !Number.isInteger(a.filter_id)) return { ok: false, issue: issue("filter", "Номер фильтра — целое число.", "Номера фильтров — в разделе «Фильтры» дерева.") };
    f.filterId = a.filter_id;
  }
  if (given(a, "restrict")) {
    try {
      const v: unknown = typeof a.restrict === "string" ? JSON.parse(a.restrict) : a.restrict;
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not a tree");
      Object.assign(f, { restrictOn: true, restrict: ruleToDraft(v as Rule) });
    } catch {
      return { ok: false, issue: issue("restrict", "Ограничитель — не дерево правил в JSON.", 'Пример: {"cond": {"field": "DOM", "op": "=", "value": "A"}}.') };
    }
  }
  if (given(a, "groups")) f.groups = parseKeys(String(a.groups));
  if (given(a, "bench_h")) f.benchH = txt(a.bench_h);
  if (given(a, "bench_z0")) f.benchZ0 = txt(a.bench_z0);
  if (given(a, "bench_z_min")) f.benchZMin = txt(a.bench_z_min);
  if (given(a, "bench_z_max")) f.benchZMax = txt(a.bench_z_max);
  if (given(a, "curve")) {
    const c = typeof a.curve === "number" ? a.curve : parseDecimal(String(a.curve));
    if (c === null || !(c === 0 || (Number.isInteger(c) && c >= MIN_CURVE && c <= MAX_CURVE))) return { ok: false, issue: curveIssue() };
    Object.assign(f, { curveOn: c > 0, curvePoints: c > 0 ? String(c) : keep.curvePoints });
  }
  if (given(a, "template_id")) f.templateId = String(a.template_id).trim();
  if (given(a, "name")) f.name = String(a.name).trim();
  return { ok: true, form: f };
}

// ---- a refusal of the core in its place in the form ----

/** Where in the form a refusal of the core belongs (`param` of the body, or the code when it names no place). */
export function issueOfRefusal(r: { code: string; message: string; hint: string; param: string | null }, form: ResourceForm): FormIssue {
  const head = (r.param ?? "").split(".")[0];
  const byParam: Record<string, FormField> = { elements: "elements", cutoff_field: "cutoff_field", cutoffs: "cutoffs", balance: "balance", density: "density", filter_id: "filter", restrict: "restrict", groups: "groups", bench: "bench", curve: "curve", template_id: "template", name: "name", model_id: "model" };
  let field: FormField = byParam[head] ?? "general";
  if (r.code === "block_model_not_found") field = "model";
  else if (r.code === "filter_not_found") field = "filter";
  else if (r.code === "template_not_found") field = "template";
  else if (r.code === "group_numeric" || r.code === "report_too_big") field = "groups";
  else if (r.code === "filter_field_missing") field = form.pit.on && form.pit.mode === "field" ? "pit" : form.restrictOn || form.filterId === null ? "restrict" : "filter";
  return issue(field, r.message, r.hint);
}
