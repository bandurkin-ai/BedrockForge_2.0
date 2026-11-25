/**
 * What the window «Содержание–тоннаж» and the command `blocks.report` know (ТЗ 7.31; ADR 0023 §10): the form of the
 * request (the model, the grades, the density, the cut-offs, the filter and the quick restrictor, the categories, the
 * groups, the curve), the defaults of 7.31 as the core takes them, the request body of the arguments of the command
 * (every wrong thing is told here in Russian with a hint and the place in the form — nothing wrong reaches the core),
 * the course of the run (stages, the result, the refusal). One window per project. No React.
 */
import { Store } from "../../../shell/store";
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../filters/constructor";
import type { BlockField, BlockModelInfo } from "../api";
import { DEFAULT_CURVE, MAX_CURVE, MAX_CUTOFFS, MAX_GRADES, MIN_CURVE, type ReportBody, type ReportResult, type Refusal, type Started } from "./api";

export const REPORT_WINDOW = "blocks-report";

export type FormField = "model" | "grades" | "density" | "cutoffs" | "cutoff_field" | "filter" | "restrict" | "categories" | "groups" | "curve" | "general";

/** The first wrong thing: the place in the form, the words, the hint (as `{code, message, hint}` of the core). */
export interface FormIssue {
  field: FormField;
  message: string;
  hint: string;
}

const issue = (field: FormField, message: string, hint: string): FormIssue => ({ field, message, hint });

export type DensityMode = "auto" | "field" | "value";

export interface ReportForm {
  modelId: number | null;
  grades: string[];
  densityMode: DensityMode;
  densityField: string;
  /** the text of the number (t/m³), a decimal comma or point */
  densityValue: string;
  /** «0; 0,5; 1» */
  cutoffs: string;
  /** "" — the first of the grades */
  cutoffField: string;
  /** a filter of the library; null — none */
  filterId: number | null;
  restrictOn: boolean;
  restrict: Draft;
  catOn: boolean;
  catField: string;
  catValues: string[];
  /** "" — no groups */
  groups: string;
  curveOn: boolean;
  curvePoints: string;
}

export const EMPTY_FORM: ReportForm = {
  modelId: null,
  grades: [],
  densityMode: "auto",
  densityField: "",
  densityValue: "2,7",
  cutoffs: "0",
  cutoffField: "",
  filterId: null,
  restrictOn: false,
  restrict: EMPTY_DRAFT,
  catOn: false,
  catField: "",
  catValues: [],
  groups: "",
  curveOn: true,
  curvePoints: String(DEFAULT_CURVE),
};

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface ReportRun {
  phase: Phase;
  /** a run that was overtaken (another one started, the project changed) does not write its end */
  seq: number;
  modelId: number | null;
  modelName: string;
  job: Started | null;
  result: ReportResult | null;
  error: Refusal | null;
}

export const IDLE: ReportRun = { phase: "idle", seq: 0, modelId: null, modelName: "", job: null, result: null, error: null };

export interface ReportState {
  form: ReportForm;
  run: ReportRun;
  issue: FormIssue | null;
}

export const reportStore = new Store<ReportState>({ form: EMPTY_FORM, run: IDLE, issue: null });

export const currentForm = (): ReportForm => reportStore.get().form;
export const currentRun = (): ReportRun => reportStore.get().run;
export const isRunning = (): boolean => reportStore.get().run.phase === "running";

export function setForm(patch: Partial<ReportForm>): void {
  reportStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null }));
}

export const setIssue = (i: FormIssue | null): void => reportStore.set({ issue: i });

export function resetReport(): void {
  reportStore.set({ form: EMPTY_FORM, run: { ...IDLE, seq: reportStore.get().run.seq + 1 }, issue: null });
}

/** A run begins: the old result goes (a result of another request must not stand beside the new form). */
export function beginRun(modelId: number, modelName: string): number {
  const seq = reportStore.get().run.seq + 1;
  reportStore.set({ run: { phase: "running", seq, modelId, modelName, job: null, result: null, error: null }, issue: null });
  return seq;
}

/** Writes into the run `seq` (a newer run is not touched). */
export function patchRun(seq: number, patch: Partial<ReportRun>): boolean {
  if (reportStore.get().run.seq !== seq) return false;
  reportStore.set((s) => ({ run: { ...s.run, ...patch } }));
  return true;
}

// ---- the fields of a model and the defaults of 7.31 ----

export interface FieldInfo {
  name: string;
  kind: "number" | "category";
  unit: string | null;
  role: string | null;
  labels: string[];
  /** min = max: a constant field (a density `DENSITY` = 2,6 is skipped by default) */
  constant: boolean;
}

/** The fields as the window lists them: the details of the model (labels, statistics) when read, else the list. */
export function fieldsOf(model: Pick<BlockModelInfo, "fields"> | undefined | null): FieldInfo[] {
  return (model?.fields ?? []).map((f: BlockField) => {
    const x = f as BlockField & { role?: string | null };
    const st = f.stats && "min" in f.stats ? f.stats : null;
    return { name: f.name, kind: f.kind, unit: f.unit ?? null, role: x.role ?? null, labels: f.labels ?? [], constant: !!st && st.min !== null && st.min === st.max };
  });
}

/** The grades by default (7.31): the fields `*_CUT`, else those with the role `grade`. */
export function defaultGrades(fields: readonly FieldInfo[]): string[] {
  const nums = fields.filter((f) => f.kind === "number");
  const cut = nums.filter((f) => f.name.toLowerCase().endsWith("_cut")).map((f) => f.name);
  return cut.length ? cut : nums.filter((f) => f.role === "grade").map((f) => f.name);
}

/** The density by default (7.31): the varying field `SG`, else a varying field with the role `density`; null — none. */
export function autoDensity(fields: readonly FieldInfo[]): string | null {
  const nums = fields.filter((f) => f.kind === "number" && !f.constant);
  return (nums.find((f) => f.name.toLowerCase() === "sg") ?? nums.find((f) => f.role === "density"))?.name ?? null;
}

/** The form of a model: its defaults; what does not depend on the model (cut-offs, curve, density number) stays. */
export function formForModel(id: number | null, fields: readonly FieldInfo[], keep: ReportForm = EMPTY_FORM): ReportForm {
  const same = keep.modelId === id;
  const has = (name: string, kind: FieldInfo["kind"]) => fields.some((f) => f.name === name && f.kind === kind);
  const grades = same ? keep.grades.filter((g) => has(g, "number")) : [];
  return {
    ...keep,
    modelId: id,
    grades: grades.length ? grades : defaultGrades(fields).slice(0, MAX_GRADES),
    densityMode: same ? keep.densityMode : "auto",
    densityField: same && has(keep.densityField, "number") ? keep.densityField : "",
    cutoffField: same && has(keep.cutoffField, "number") ? keep.cutoffField : "",
    catField: same && has(keep.catField, "category") ? keep.catField : "",
    catValues: same ? keep.catValues : [],
    catOn: same ? keep.catOn : false,
    groups: same && has(keep.groups, "category") ? keep.groups : "",
  };
}

// ---- numbers and lists of the form ----

/** A number of the form: a decimal comma or point, spaces are ignored; null — not a number. */
export function parseDecimal(text: string): number | null {
  const t = text.trim().replace(/\s+/g, "").replace(",", ".");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** «1 000», «2 500 000,5»: spaces as digit groups — told apart from a list «1 000» only by «;», so a list of this shape is not guessed. */
export const looksGrouped = (text: string): boolean => /^[1-9]\d{0,2}(?: \d{3})+(?:[.,]\d+)?$/.test(text.trim());

/**
 * «0; 0,5; 1» → [0, 0.5, 1]. The cut-offs are parted by «;», a new line or spaces (a decimal comma is a number);
 * a comma after a number («0, 1») is dropped. 1–200 numbers.
 */
export function parseCutoffs(text: string): { ok: true; values: number[] } | { ok: false; message: string; hint: string } {
  let parts = text.split(/[;\n]+/).map((p) => p.trim()).filter(Boolean);
  if (parts.length === 1 && looksGrouped(parts[0])) return { ok: false, message: `«${parts[0]}» — одно число или несколько?`, hint: "Пробел между разрядами и пробел между бортами не отличить: разделите борта знаком «;», например 1000; 2500." };
  if (parts.length === 1 && /\s/.test(parts[0])) parts = parts[0].split(/\s+/);
  const values: number[] = [];
  for (const p of parts) {
    const n = parseDecimal(p.replace(/,$/, ""));
    if (n === null) return { ok: false, message: `Борт «${p.slice(0, 40)}» — не число.`, hint: "Перечислите борта через «;», например 0; 0,5; 1." };
    values.push(n);
  }
  if (!values.length) return { ok: false, message: "Не заданы борта.", hint: "Введите хотя бы один борт, например 0 — это весь тоннаж." };
  if (values.length > MAX_CUTOFFS) return { ok: false, message: `Бортов больше ${MAX_CUTOFFS}.`, hint: "Оставьте не больше 200 бортов." };
  return { ok: true, values };
}

/** The text of a number as the form writes it (a decimal comma). */
export const numberText = (v: number): string => String(v).replace(".", ",");

export const cutoffsText = (values: readonly number[]): string => values.map(numberText).join("; ");

/** «AU_CUT, CU_CUT» (or a new line / «;») → names. */
export const parseNames = (text: string): string[] => [...new Set(text.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean))];

/** Labels of categories: a JSON array of texts, or labels parted by «;» / a new line. */
export function parseLabels(text: string): string[] {
  const t = text.trim();
  if (t.startsWith("[")) {
    try {
      const v: unknown = JSON.parse(t);
      if (Array.isArray(v)) return [...new Set(v.filter((x): x is string => typeof x === "string"))];
    } catch {
      /* a text, not a list */
    }
  }
  return [...new Set(t.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean))];
}

// ---- the form → the arguments of the command; the arguments → the body of the request ----

export type Args = Record<string, unknown>;

/** The arguments of `blocks.report` from the form of the window; a wrong form — the first wrong thing in its place. */
export function argsOfForm(f: ReportForm): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  if (f.modelId === null) return { ok: false, issue: issue("model", "Не выбрана блочная модель.", "Выберите модель в списке.") };
  const args: Args = { model_id: f.modelId, csv: true };
  if (f.grades.length) args.grades = f.grades.join(", ");
  if (f.densityMode === "field") {
    if (!f.densityField) return { ok: false, issue: issue("density", "Не выбрано поле плотности.", "Выберите поле или задайте число, например 2,7.") };
    args.density = f.densityField;
  } else if (f.densityMode === "value") args.density = f.densityValue.trim();
  args.cutoffs = f.cutoffs;
  if (f.cutoffField) args.cutoff_field = f.cutoffField;
  if (f.filterId !== null) args.filter_id = f.filterId;
  if (f.restrictOn) {
    const c = checkDraft(f.restrict);
    if (!c.ok) return { ok: false, issue: issue("restrict", c.issue.message, c.issue.hint) };
    args.restrict = JSON.stringify(c.rule);
  }
  if (f.catOn) {
    if (!f.catField) return { ok: false, issue: issue("categories", "Не выбрано поле категорий для отбора.", "Выберите поле или выключите отбор.") };
    args.categories_field = f.catField;
    args.categories = JSON.stringify(f.catValues);
  }
  if (f.groups) args.groups = f.groups;
  if (f.curveOn) {
    const c = parseDecimal(f.curvePoints);
    if (c === null || !validCurve(c)) return { ok: false, issue: curveIssue() };
    args.curve = c;
  } else args.curve = 0;
  return { ok: true, args };
}

const validCurve = (c: number): boolean => Number.isInteger(c) && (c === 0 || (c >= MIN_CURVE && c <= MAX_CURVE));
const curveIssue = (): FormIssue => issue("curve", `Точек кривой: 0 (без кривой) или от ${MIN_CURVE} до ${MAX_CURVE}.`, "Введите целое число, например 50.");

const given = (a: Args, k: string): boolean => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

/** The request of the arguments (everything is checked here: a number, a list, a JSON tree), or the first wrong thing. */
export function bodyOfArgs(a: Args): { ok: true; modelId: number; body: ReportBody } | { ok: false; issue: FormIssue } {
  const id = a.model_id;
  if (typeof id !== "number" || !Number.isInteger(id)) return { ok: false, issue: issue("model", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.") };
  const body: ReportBody = {};
  if (given(a, "grades")) {
    const names = parseNames(String(a.grades));
    if (names.length > MAX_GRADES) return { ok: false, issue: issue("grades", `Содержаний больше ${MAX_GRADES}.`, "Оставьте не больше 20 содержаний.") };
    body.grades = names;
  }
  if (given(a, "density")) {
    const raw = typeof a.density === "number" ? a.density : String(a.density).trim();
    const n = typeof raw === "number" ? raw : parseDecimal(raw);
    if (n !== null) {
      if (!(n > 0)) return { ok: false, issue: issue("density", "Плотность должна быть больше нуля.", "Например 2,7 или имя поля плотности.") };
      body.density = n;
    } else body.density = String(raw);
  }
  const cuts = parseCutoffs(given(a, "cutoffs") ? String(a.cutoffs) : "0");
  if (!cuts.ok) return { ok: false, issue: issue("cutoffs", cuts.message, cuts.hint) };
  body.cutoffs = cuts.values;
  if (given(a, "cutoff_field")) body.cutoff_field = String(a.cutoff_field).trim();
  if (given(a, "filter_id")) {
    if (typeof a.filter_id !== "number" || !Number.isInteger(a.filter_id)) return { ok: false, issue: issue("filter", "Номер фильтра — целое число.", "Номера фильтров — в разделе «Фильтры» дерева.") };
    body.filter_id = a.filter_id;
  }
  if (given(a, "restrict")) {
    try {
      const v: unknown = typeof a.restrict === "string" ? JSON.parse(a.restrict) : a.restrict;
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not a tree");
      body.restrict = v as Rule;
    } catch {
      return { ok: false, issue: issue("restrict", "Ограничитель — не дерево правил в JSON.", 'Пример: {"cond": {"field": "AU", "op": ">", "value": 1}}.') };
    }
  }
  if (given(a, "categories") || given(a, "categories_field")) {
    const values = given(a, "categories") ? parseLabels(String(a.categories)) : [];
    if (!given(a, "categories_field")) return { ok: false, issue: issue("categories", "Не названо поле категорий для отбора.", "Передайте categories_field — имя категорийного поля.") };
    if (!values.length) return { ok: false, issue: issue("categories", "Не выбрано ни одной категории для отбора.", "Отметьте категории или выключите отбор.") };
    body.categories = { field: String(a.categories_field).trim(), values };
  }
  if (given(a, "groups")) body.groups = String(a.groups).trim();
  const curve = given(a, "curve") ? (typeof a.curve === "number" ? a.curve : parseDecimal(String(a.curve))) : DEFAULT_CURVE;
  if (curve === null || !validCurve(curve)) return { ok: false, issue: curveIssue() };
  body.curve = curve;
  body.csv = a.csv === true;
  return { ok: true, modelId: id, body };
}

// ---- the form from the arguments (what a call of the command shows in the window) ----

/** The form that shows the arguments of a call (the window then reads the same request the core got). */
export function formOfArgs(a: Args, fields: readonly FieldInfo[], keep: ReportForm = EMPTY_FORM): ReportForm {
  const id = typeof a.model_id === "number" ? a.model_id : keep.modelId;
  const base = formForModel(id, fields, { ...keep, modelId: null });
  const out: ReportForm = { ...base };
  if (given(a, "grades")) out.grades = parseNames(String(a.grades));
  if (given(a, "density")) {
    const n = typeof a.density === "number" ? a.density : parseDecimal(String(a.density));
    if (n !== null) Object.assign(out, { densityMode: "value", densityValue: numberText(n) });
    else Object.assign(out, { densityMode: "field", densityField: String(a.density).trim() });
  }
  if (given(a, "cutoffs")) out.cutoffs = String(a.cutoffs);
  if (given(a, "cutoff_field")) out.cutoffField = String(a.cutoff_field).trim();
  if (given(a, "filter_id") && typeof a.filter_id === "number") out.filterId = a.filter_id;
  if (given(a, "restrict")) {
    try {
      const v: unknown = typeof a.restrict === "string" ? JSON.parse(a.restrict) : a.restrict;
      if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, { restrictOn: true, restrict: ruleToDraft(v as Rule) });
    } catch {
      /* told by `bodyOfArgs` */
    }
  }
  if (given(a, "categories_field")) Object.assign(out, { catOn: true, catField: String(a.categories_field).trim(), catValues: given(a, "categories") ? parseLabels(String(a.categories)) : [] });
  if (given(a, "groups")) out.groups = String(a.groups).trim();
  if (given(a, "curve")) {
    const c = typeof a.curve === "number" ? a.curve : parseDecimal(String(a.curve));
    if (c !== null) Object.assign(out, { curveOn: c > 0, curvePoints: c > 0 ? String(c) : keep.curvePoints });
  }
  return out;
}

// ---- a refusal of the core in its place in the form ----

/** Where in the form a refusal of the core belongs (`param` of the body, or the code when it names no place). */
export function issueOfRefusal(r: Refusal, form: ReportForm): FormIssue {
  const head = (r.param ?? "").split(".")[0];
  const byParam: Record<string, FormField> = { grades: "grades", density: "density", cutoffs: "cutoffs", cutoff_field: "cutoff_field", filter_id: "filter", restrict: "restrict", categories: "categories", groups: "groups", curve: "curve" };
  let field: FormField = byParam[head] ?? "general";
  if (r.code === "block_model_not_found") field = "model";
  else if (r.code === "filter_not_found") field = "filter";
  else if (r.code === "filter_field_missing") field = form.restrictOn && form.filterId === null ? "restrict" : "filter";
  return issue(field, r.message, r.hint);
}
