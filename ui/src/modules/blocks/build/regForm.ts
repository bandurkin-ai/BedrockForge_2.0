/**
 * The form of «Регуляризация» (ТЗ 8.7.5; ADR 0023 §9, decision 14, № 72; e17-api.md §9): the form of the window → the flat
 * arguments of `blocks.regularize` («AU:volume; CU:tonnes», «CODE; DOM») → the body of `POST /blocks/{id}/regularize`.
 * The new grid is regular, in the same axes (the origin — a multiple of the new step, φ the same); the grades are
 * averaged by the volume or by the tonnes (the tonnes need a density), the categories — the mode by volume and the
 * shares `P_<КОД>`. Every wrong thing is told here in Russian with a hint and the place in the form; a refusal of the
 * core is put in its place by `issueOfRegularize`. No React.
 */
import { numberText, parseDecimal } from "../report/state";
import type { Refusal, RegularizeBody, Triple, Weight } from "./api";
import { given, issue, numberOf, parseTriple, textOf, type Args, type FormIssue } from "./forms";

export interface GradeRow {
  field: string;
  weight: Weight;
}

export type DensityMode = "none" | "field" | "value";
export type CatMode = "all" | "some" | "none";

export interface RegForm {
  modelId: number | null;
  size: string[];
  /** true — every number field of the model, by the volume */
  gradesAll: boolean;
  grades: GradeRow[];
  densityMode: DensityMode;
  densityField: string;
  /** t/m³, a decimal comma or point */
  densityValue: string;
  catMode: CatMode;
  categories: string[];
  name: string;
}

export const EMPTY_REG: RegForm = {
  modelId: null,
  size: ["20", "20", "10"],
  gradesAll: true,
  grades: [],
  densityMode: "none",
  densityField: "",
  densityValue: "2,7",
  catMode: "all",
  categories: [],
  name: "",
};

const NONE_WORDS = ["none", "нет", "ничего", "-", "—"];
const WEIGHTS: Record<string, Weight> = { volume: "volume", объём: "volume", объему: "volume", по_объёму: "volume", tonnes: "tonnes", тоннаж: "tonnes", тоннам: "tonnes", по_тоннажу: "tonnes" };

/** «AU:volume; CU:tonnes» / «AU, CU» (by the volume) / JSON `[{field, weight}]` → rows; null — not a list of grades. */
export function parseGrades(raw: unknown): GradeRow[] | null {
  let items: unknown[];
  if (Array.isArray(raw)) items = raw;
  else if (typeof raw === "string" && raw.trim().startsWith("[")) {
    try {
      const v: unknown = JSON.parse(raw);
      items = Array.isArray(v) ? v : [];
    } catch {
      return null;
    }
  } else items = String(raw).split(/[;\n,]+/).map((s) => s.trim()).filter(Boolean);
  const rows: GradeRow[] = [];
  for (const x of items) {
    if (typeof x === "string") {
      const [field, w = "volume"] = x.split(":").map((p) => p.trim());
      const weight = WEIGHTS[w.toLowerCase()];
      if (!field || !weight) return null;
      rows.push({ field, weight });
    } else if (x && typeof x === "object" && typeof (x as Record<string, unknown>).field === "string") {
      const o = x as Record<string, unknown>;
      const weight = o.weight === undefined || o.weight === null ? "volume" : WEIGHTS[String(o.weight).toLowerCase()];
      if (!weight) return null;
      rows.push({ field: String(o.field).trim(), weight });
    } else return null;
  }
  return rows.length ? rows : null;
}

export const gradesArg = (rows: readonly GradeRow[]): string => rows.map((g) => `${g.field}:${g.weight}`).join("; ");

export function argsOfRegularize(f: RegForm): Args {
  const a: Args = { size: f.size.join("; ") };
  if (f.modelId !== null) a.model_id = f.modelId;
  if (!f.gradesAll) a.grades = gradesArg(f.grades);
  if (f.densityMode === "field") a.density = f.densityField;
  else if (f.densityMode === "value") a.density = f.densityValue.trim();
  if (f.catMode === "none") a.categories = "none";
  else if (f.catMode === "some") a.categories = f.categories.join("; ");
  if (f.name.trim()) a.name = f.name;
  return a;
}

export function bodyOfRegularize(a: Args): { ok: true; modelId: number; body: RegularizeBody } | { ok: false; issue: FormIssue } {
  const fail = (field: string, message: string, hint: string): { ok: false; issue: FormIssue } => ({ ok: false, issue: issue(field, message, hint) });
  const id = numberOf(a.model_id);
  if (id === null || !Number.isInteger(id)) return fail("model", "Не выбрана блочная модель.", "Выберите модель, которую нужно регуляризовать.");
  const size: Triple | null = given(a, "size") ? parseTriple(String(a.size)) : null;
  if (!size) return fail("size", "Размер нового блока — одно число или три (X; Y; Z).", "Например 20 или 20; 20; 10.");
  if (size.some((v) => !(v > 0))) return fail("size", "Размер блока должен быть больше нуля.", "Введите положительные числа, например 20; 20; 10.");
  const body: RegularizeBody = { size };
  if (given(a, "grades")) {
    const rows = parseGrades(a.grades);
    if (!rows) return fail("grades", "Поля заданы неверно.", "Перечислите поля через «;», у каждого можно указать вес: AU:volume или CU:tonnes.");
    body.grades = rows;
  }
  if (given(a, "density")) {
    const n = typeof a.density === "number" ? a.density : parseDecimal(String(a.density));
    if (n !== null) {
      if (!(n > 0)) return fail("density", "Плотность должна быть больше нуля.", "Например 2,7 (т/м³) или имя поля плотности.");
      body.density = n;
    } else body.density = String(a.density).trim();
  }
  if (body.grades?.some((g) => g.weight === "tonnes") && body.density === undefined) return fail("density", "Для веса по тоннажу нужна плотность.", "Задайте число т/м³ или поле плотности, либо выберите вес по объёму.");
  if (given(a, "categories")) {
    const text = String(a.categories).trim();
    body.categories = NONE_WORDS.includes(text.toLowerCase()) ? [] : [...new Set(text.split(/[;\n,]+/).map((s) => s.trim()).filter(Boolean))];
  }
  for (const k of ["name", "folder"] as const) {
    const t = textOf(a, k);
    if (!t.ok) return { ok: false, issue: t.issue };
    if (t.value) body[k] = t.value;
  }
  return { ok: true, modelId: id, body };
}

export function regFormOfArgs(a: Args, keep: RegForm = EMPTY_REG): RegForm {
  const f: RegForm = { ...keep };
  if (given(a, "model_id")) f.modelId = numberOf(a.model_id);
  const size = given(a, "size") ? parseTriple(String(a.size)) : null;
  if (size) f.size = size.map(numberText);
  const rows = given(a, "grades") ? parseGrades(a.grades) : null;
  if (rows) Object.assign(f, { gradesAll: false, grades: rows });
  if (given(a, "density")) {
    const n = typeof a.density === "number" ? a.density : parseDecimal(String(a.density));
    if (n !== null) Object.assign(f, { densityMode: "value", densityValue: numberText(n) });
    else Object.assign(f, { densityMode: "field", densityField: String(a.density).trim() });
  }
  if (given(a, "categories")) {
    const text = String(a.categories).trim();
    if (NONE_WORDS.includes(text.toLowerCase())) Object.assign(f, { catMode: "none", categories: [] });
    else Object.assign(f, { catMode: "some", categories: text.split(/[;\n,]+/).map((s) => s.trim()).filter(Boolean) });
  }
  if (given(a, "name")) f.name = String(a.name).trim();
  return f;
}

/** Where a refusal of the regularization belongs (`param` of the body; the code when it names no place). */
export function issueOfRegularize(r: Refusal): FormIssue {
  const parts = (r.param ?? "").split(".");
  const index = /^\d+$/.test(parts[1] ?? "") ? Number(parts[1]) : undefined;
  const place: Record<string, string> = { size: "size", grades: "grades", density: "density", categories: "categories", name: "name", folder: "name", expected_version: "general" };
  let field = place[parts[0]] ?? "general";
  if (r.code === "block_model_not_found" || r.code === "block_model_empty" || r.code === "variable_grid") field = "model";
  else if (r.code === "block_field_not_found") field = "grades";
  else if (r.code === "block_model_too_large") field = "size";
  else if (r.code === "version_conflict") return { ...issue("general", r.message, r.hint), reread: true };
  return issue(field, r.message, r.hint, index);
}
