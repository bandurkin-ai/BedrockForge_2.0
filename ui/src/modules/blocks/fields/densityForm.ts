/**
 * The form of the density of a block model (ТЗ 8.7.5; ADR 0023 §9, decision 13) and the arguments of the command
 * `blocks.density`: a constant, a number field, a table «домен → плотность» (a category by its label, a number by the
 * exact value), a formula of 8.7.4; the density «по умолчанию» for a block with no value of its own (it gets `DENS_SRC` =
 * 1; without a default the density stays empty, never 0), the names of the two fields written (the density and its
 * source), the restrictor. Every wrong thing of the arguments is told here in Russian with a hint and the place in the
 * form — nothing wrong reaches the core. No React.
 */
import { parseDecimal } from "../report/state";
import type { FieldInfo } from "../report/state";
import type { DensityBody, TableRow } from "./api";
import { EMPTY_RESTRICT, given, isInt, issue, jsonArg, MAX_EXPR, nameIssue, restrictArgs, restrictFormOfArgs, restrictOfArgs, type Args, type Issue, type RestrictForm } from "./common";

export type Method = DensityBody["method"];

export const METHODS: readonly { method: Method; label: string; hint: string }[] = [
  { method: "constant", label: "Постоянная", hint: "Одно число т/м³ для всех блоков" },
  { method: "field", label: "Поле модели", hint: "Плотность блока — значение числового поля" },
  { method: "table", label: "Таблица по домену", hint: "Плотность по подписи категории или по числу поля домена" },
  { method: "formula", label: "Формула", hint: "Выражение языка 8.7.4 над полями модели" },
];

export interface DensityRow {
  /** the label of a category or the number of a number field, as typed */
  value: string;
  /** t/m³, as typed (a decimal comma) */
  density: string;
}

export interface DensityForm {
  method: Method;
  value: string;
  field: string;
  /** the field of the domain of the table */
  tableField: string;
  rows: DensityRow[];
  /** the density of a domain the table does not name ("" — the general default) */
  tableDefault: string;
  expr: string;
  /** the density of a block with no value of its own ("" — none: the density stays empty) */
  defaultText: string;
  out: string;
  srcField: string;
  r: RestrictForm;
}

export const DEFAULT_OUT = "DENS";
export const DEFAULT_SRC = "DENS_SRC";

export const EMPTY_DENSITY: DensityForm = { method: "constant", value: "2,7", field: "", tableField: "", rows: [], tableDefault: "", expr: "", defaultText: "", out: DEFAULT_OUT, srcField: DEFAULT_SRC, r: EMPTY_RESTRICT };

const bad = (place: string, message: string, hint: string, param: string | null = null) => ({ ok: false as const, issue: issue("density", place, message, hint, param) });

/** A density: a number above zero (a decimal comma or point); null — not one. */
function positive(raw: unknown): number | null {
  const n = typeof raw === "number" ? raw : typeof raw === "string" ? parseDecimal(raw) : null;
  return n !== null && Number.isFinite(n) && n > 0 ? n : null;
}

/** The rows of the table for the labels of a category field (what the model has); the rows typed already stay. */
export function rowsForLabels(labels: readonly string[], keep: readonly DensityRow[]): DensityRow[] {
  const have = new Map(keep.map((r) => [r.value.trim().toLowerCase(), r]));
  return labels.map((l) => have.get(l.toLowerCase()) ?? { value: l, density: "" });
}

// ---- the form → the arguments of the command ----

/** The arguments of `blocks.density` from the form of the window (`fields` — of the model: the kind of the domain field). */
export function argsOfForm(modelId: number | null, f: DensityForm, fields: readonly FieldInfo[]): { ok: true; args: Args } | { ok: false; issue: Issue } {
  if (modelId === null) return bad("model", "Не выбрана блочная модель.", "Выберите модель в списке.");
  const args: Args = { model_id: modelId, method: f.method };
  if (f.method === "constant") {
    if (!f.value.trim()) return bad("value", "Не задано число плотности.", "Введите число т/м³, например 2,7.");
    args.value = f.value.trim();
  } else if (f.method === "field") {
    if (!f.field) return bad("field", "Не выбрано поле плотности.", "Выберите числовое поле модели.");
    args.field = f.field;
  } else if (f.method === "formula") {
    if (!f.expr.trim()) return bad("expr", "Не задана формула плотности.", "Например: 2.5 + CU / 10.");
    args.expr = f.expr;
  } else {
    if (!f.tableField) return bad("table", "Не выбрано поле домена таблицы.", "Выберите категорию или числовое поле, по которому плотность задана.");
    const kind = fields.find((x) => x.name === f.tableField)?.kind;
    const rows: TableRow[] = [];
    for (const [i, r] of f.rows.entries()) {
      const key = r.value.trim();
      const num = kind === "number" ? parseDecimal(key) : null;
      if (!key || (kind === "number" && num === null)) return bad("table", `Строка ${i + 1}: домен «${key}» ${key ? "— не число" : "не задан"}.`, kind === "number" ? "Для числового поля домен — число." : "Выберите подпись категории.", `table.values.${i}.value`);
      const d = positive(r.density);
      if (d === null) return bad("table", `Строка ${i + 1}: плотность должна быть числом больше нуля.`, "Например 2,7.", `table.values.${i}.density`);
      rows.push({ value: kind === "number" ? (num as number) : key, density: d });
    }
    if (!rows.length) return bad("table", "В таблице нет строк.", "Добавьте строку «домен — плотность» или заполните таблицу подписями категории.", "table.values");
    args.table_field = f.tableField;
    args.table = JSON.stringify(rows);
    if (f.tableDefault.trim()) args.table_default = f.tableDefault.trim();
  }
  if (f.defaultText.trim()) args.default = f.defaultText.trim();
  const n = nameIssue("density", "out", f.out, "Имя поля плотности");
  if (n) return { ok: false, issue: n };
  const s = nameIssue("density", "src_field", f.srcField, "Имя поля источника");
  if (s) return { ok: false, issue: s };
  args.out = f.out.trim();
  args.src_field = f.srcField.trim();
  const r = restrictArgs("density", f.r);
  if (!r.ok) return r;
  return { ok: true, args: { ...args, ...r.args } };
}

export interface DensityRequest {
  modelId: number;
  body: Omit<DensityBody, "expected_version">;
}

/** The request of the arguments (everything is checked here: the numbers, the table, the names), or the first wrong thing. */
export function requestOfArgs(a: Args): { ok: true; request: DensityRequest } | { ok: false; issue: Issue } {
  if (!isInt(a.model_id)) return bad("model", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.");
  const method = a.method;
  if (method !== "constant" && method !== "field" && method !== "table" && method !== "formula") return bad("method", "Неизвестный способ задания плотности.", "Допустимо: constant, field, table, formula.");
  const body: DensityRequest["body"] = { method, out: DEFAULT_OUT, src_field: DEFAULT_SRC };
  if (method === "constant") {
    const v = given(a, "value") ? positive(a.value) : null;
    if (v === null) return bad("value", "Плотность должна быть числом больше нуля.", "Передайте value, например 2,7.");
    body.value = v;
  } else if (method === "field") {
    if (!given(a, "field")) return bad("field", "Не названо поле плотности.", "Передайте field — имя числового поля модели.");
    body.field = String(a.field).trim();
  } else if (method === "formula") {
    if (!given(a, "expr")) return bad("expr", "Не задана формула плотности.", "Передайте expr, например 2.5 + CU / 10.");
    if (String(a.expr).length > MAX_EXPR) return bad("expr", `Формула длиннее ${MAX_EXPR} знаков.`, "Сократите формулу.");
    body.expr = String(a.expr);
  } else {
    if (!given(a, "table_field")) return bad("table", "Не назван домен таблицы.", "Передайте table_field — поле, по которому плотность задана.");
    const j = jsonArg(a.table);
    if (!j.ok || !Array.isArray(j.value)) return bad("table", "Таблица плотности — не список в JSON.", 'Пример: [{"value": "Руда", "density": 3.1}, {"value": "Порода", "density": 2.6}].', "table");
    const values: TableRow[] = [];
    for (const [i, row] of (j.value as unknown[]).entries()) {
      const r = row && typeof row === "object" ? (row as Record<string, unknown>) : {};
      const key = r.value;
      if (key === undefined || key === null || key === "" || typeof key === "boolean" || typeof key === "object") return bad("table", `Строка ${i + 1}: не задан домен.`, "Домен — подпись категории или число.", `table.values.${i}.value`);
      const d = positive(r.density);
      if (d === null) return bad("table", `Строка ${i + 1}: плотность должна быть числом больше нуля.`, "Например 2,7.", `table.values.${i}.density`);
      values.push({ value: typeof key === "string" ? key.trim() : (key as number), density: d });
    }
    if (!values.length) return bad("table", "В таблице нет строк.", "Добавьте хотя бы одну строку «домен — плотность».", "table.values");
    const t: NonNullable<DensityBody["table"]> = { field: String(a.table_field).trim(), values };
    if (given(a, "table_default")) {
      const td = positive(a.table_default);
      if (td === null) return bad("table", "Плотность таблицы по умолчанию должна быть числом больше нуля.", "Например 2,7.", "table.default");
      t.default = td;
    }
    body.table = t;
  }
  if (given(a, "default")) {
    const d = positive(a.default);
    if (d === null) return bad("default", "Плотность по умолчанию должна быть числом больше нуля.", "Например 2,5; без значения плотность пропущенных блоков останется пустой.");
    body.default = d;
  }
  for (const [key, place, dflt, what] of [["out", "out", DEFAULT_OUT, "Имя поля плотности"], ["src_field", "src_field", DEFAULT_SRC, "Имя поля источника"]] as const) {
    if (!given(a, key)) continue;
    const n = nameIssue("density", place, String(a[key]), what);
    if (n) return { ok: false, issue: n };
    body[key] = String(a[key]).trim() || dflt;
  }
  if (body.out.toLowerCase() === body.src_field.toLowerCase()) return bad("src_field", "Поле плотности и поле источника не могут называться одинаково.", "Задайте разные имена, например DENS и DENS_SRC.");
  const r = restrictOfArgs("density", a);
  if (!r.ok) return r;
  Object.assign(body, r.body);
  if (a.replace !== undefined && a.replace !== null) {
    if (typeof a.replace !== "boolean") return bad("general", "Замена — true или false.", "Передайте replace: true, чтобы заменить существующие поля.");
    body.replace = a.replace;
  }
  return { ok: true, request: { modelId: a.model_id, body } };
}

/** The form that shows the arguments of a call (the window then reads the same request the core got). */
export function formOfArgs(a: Args, keep: DensityForm = EMPTY_DENSITY): DensityForm {
  const out: DensityForm = { ...keep, r: restrictFormOfArgs(a, keep.r) };
  if (a.method === "constant" || a.method === "field" || a.method === "table" || a.method === "formula") out.method = a.method;
  if (given(a, "value")) out.value = String(a.value).replace(".", ",");
  if (given(a, "field")) out.field = String(a.field).trim();
  if (given(a, "expr")) out.expr = String(a.expr);
  if (given(a, "default")) out.defaultText = String(a.default).replace(".", ",");
  if (given(a, "out")) out.out = String(a.out).trim();
  if (given(a, "src_field")) out.srcField = String(a.src_field).trim();
  if (given(a, "table_field")) out.tableField = String(a.table_field).trim();
  if (given(a, "table_default")) out.tableDefault = String(a.table_default).replace(".", ",");
  if (given(a, "table")) {
    const j = jsonArg(a.table);
    if (j.ok && Array.isArray(j.value)) {
      out.rows = (j.value as unknown[]).map((row) => {
        const r = row && typeof row === "object" ? (row as Record<string, unknown>) : {};
        return { value: String(r.value ?? ""), density: String(r.density ?? "").replace(".", ",") };
      });
    }
  }
  return out;
}
