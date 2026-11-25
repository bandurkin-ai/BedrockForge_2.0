/**
 * The form of the calculator of fields (ТЗ 8.7.4; ADR 0023 §7, decision 10) and the arguments of the command
 * `blocks.calc`: the expression, the name, the type, the unit, the restrictor (a filter of the library and the quick
 * one), «оставить» / «NaN» outside it. Every wrong thing of the arguments is told here in Russian with a hint and the
 * place in the form — nothing wrong reaches the core. The place of an error in the text of the expression comes from the
 * core (`pos` in code points): `errorMarker` turns it into a line, a column and a caret. No React.
 */
import { DTYPES, type CalcBody, type Dtype } from "./api";
import { EMPTY_RESTRICT, given, isInt, issue, limitIssue, MAX_DESCRIPTION, MAX_EXPR, MAX_UNIT, nameIssue, restrictArgs, restrictFormOfArgs, restrictOfArgs, type Args, type Issue, type RestrictForm } from "./common";

export interface CalcForm {
  expr: string;
  /** the name of the field written */
  field: string;
  /** "" — by the expression (f8 for a number, i1 for a condition, cat for a text) */
  dtype: "" | Dtype;
  unit: string;
  description: string;
  r: RestrictForm;
  /** outside the restrictor: the old value stays (a new field — empty) or the field is empty (NaN) */
  outside: "keep" | "nan";
}

export const EMPTY_CALC: CalcForm = { expr: "", field: "", dtype: "", unit: "", description: "", r: EMPTY_RESTRICT, outside: "keep" };

export const DTYPE_LABEL: Record<Dtype, string> = {
  f8: "число, f8 (двойная точность)",
  f4: "число, f4 (одинарная точность)",
  i4: "целое, i4",
  i2: "целое, i2",
  i1: "целое, i1 (условие 0/1)",
  cat: "категория (текст из IF)",
};

/** The text of a name of a field in an expression: as it is when it is one word, else in brackets (8.7.4). */
export const fieldRef = (name: string): string => (/^[\p{L}\p{N}_]+$/u.test(name) && !/^\d/.test(name) ? name : `[${name}]`);

/** The functions of 8.7.4 with a pattern the window inserts (the cursor goes between the brackets by hand). */
export const FUNCTIONS: readonly { name: string; text: string; hint: string }[] = [
  { name: "IF", text: "IF(условие, a, b)", hint: "Если условие верно — a, иначе b; для категории — IF(AU > 1, 'руда', 'порода')" },
  { name: "ABS", text: "ABS(x)", hint: "Модуль" },
  { name: "SQRT", text: "SQRT(x)", hint: "Квадратный корень" },
  { name: "LN", text: "LN(x)", hint: "Натуральный логарифм" },
  { name: "LOG10", text: "LOG10(x)", hint: "Десятичный логарифм" },
  { name: "EXP", text: "EXP(x)", hint: "Экспонента" },
  { name: "MIN", text: "MIN(a, b)", hint: "Меньшее из двух" },
  { name: "MAX", text: "MAX(a, b)", hint: "Большее из двух" },
  { name: "ROUND", text: "ROUND(x, n)", hint: "Округление до n знаков" },
  { name: "FLOOR", text: "FLOOR(x)", hint: "Округление вниз" },
  { name: "CEIL", text: "CEIL(x)", hint: "Округление вверх" },
  { name: "CLIP", text: "CLIP(x, a, b)", hint: "Значение в пределах от a до b" },
  { name: "ISNULL", text: "ISNULL(x)", hint: "Истина, если значения нет" },
  { name: "COALESCE", text: "COALESCE(x, y)", hint: "x, а если его нет — y" },
];

// ---- the place of an error in the text ----

/** The index in a JS string (UTF-16) of the character `pos` counted in code points, as the core counts. */
export function utf16Index(text: string, pos: number): number {
  let i = 0;
  let n = 0;
  while (n < pos && i < text.length) {
    i += (text.codePointAt(i) ?? 0) > 0xffff ? 2 : 1;
    n += 1;
  }
  return i;
}

export interface Marker {
  /** the line of the text with the error (from 1) and its text */
  line: number;
  lineText: string;
  /** the column (from 1) of the character, in code points */
  column: number;
  /** spaces up to the character, then «^» (the line is monospace) */
  caret: string;
  /** the range [from, to) the textarea selects (UTF-16) */
  from: number;
  to: number;
}

/** Where the error is: the line, the column and a caret under the character (`pos` — from 0, code points; past the end — the end). */
export function errorMarker(text: string, pos: number): Marker {
  const from = Math.min(utf16Index(text, Math.max(0, pos)), text.length);
  const start = text.lastIndexOf("\n", from - 1) + 1;
  const end = text.indexOf("\n", from);
  const lineText = text.slice(start, end < 0 ? text.length : end);
  const before = Array.from(text.slice(start, from)).length;
  const next = from < text.length && text[from] !== "\n" ? (text.codePointAt(from) ?? 0) : 0;
  return {
    line: text.slice(0, start).split("\n").length,
    lineText,
    column: before + 1,
    caret: `${" ".repeat(before)}^`,
    from,
    to: from + (next > 0xffff ? 2 : next ? 1 : 0),
  };
}

// ---- the form → the arguments of the command ----

/** The arguments of `blocks.calc` from the form of the window; a wrong form — the first wrong thing in its place. */
export function argsOfForm(modelId: number | null, f: CalcForm, checkOnly = false): { ok: true; args: Args } | { ok: false; issue: Issue } {
  if (modelId === null) return { ok: false, issue: issue("calc", "model", "Не выбрана блочная модель.", "Выберите модель в списке.") };
  if (!f.expr.trim()) return { ok: false, issue: issue("calc", "expr", "Введите выражение.", "Например: IF(AU > 1, AU * 2, 0).") };
  const args: Args = { model_id: modelId, expr: f.expr };
  if (checkOnly) {
    if (f.dtype) args.dtype = f.dtype;
    return { ok: true, args };
  }
  const bad = nameIssue("calc", "field", f.field, "Имя нового поля");
  if (bad) return { ok: false, issue: bad };
  args.field = f.field.trim();
  if (f.dtype) args.dtype = f.dtype;
  if (f.unit.trim()) args.unit = f.unit.trim();
  if (f.description.trim()) args.description = f.description.trim();
  const r = restrictArgs("calc", f.r);
  if (!r.ok) return r;
  Object.assign(args, r.args);
  if (Object.keys(r.args).length) args.outside = f.outside;
  return { ok: true, args };
}

export interface CalcRequest {
  modelId: number;
  /** without `expected_version`: the version is the model's when the call is made */
  body: Omit<CalcBody, "expected_version">;
}

/** The request of the arguments (everything is checked here: the names, the type, the tree), or the first wrong thing. */
export function requestOfArgs(a: Args, onlyCheck = false): { ok: true; request: CalcRequest } | { ok: false; issue: Issue } {
  const fail = (place: string, message: string, hint: string) => ({ ok: false as const, issue: issue("calc", place, message, hint) });
  if (!isInt(a.model_id)) return fail("model", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.");
  if (!given(a, "expr")) return fail("expr", "Введите выражение.", "Например: IF(AU > 1, AU * 2, 0).");
  const expr = String(a.expr);
  if (expr.length > MAX_EXPR) return fail("expr", `Выражение длиннее ${MAX_EXPR} знаков.`, "Разбейте расчёт на несколько полей.");
  let dtype: Dtype | undefined;
  if (given(a, "dtype")) {
    if (!(DTYPES as readonly unknown[]).includes(a.dtype)) return fail("dtype", `Неизвестный тип поля «${String(a.dtype).slice(0, 20)}».`, `Допустимо: ${DTYPES.join(", ")}.`);
    dtype = a.dtype as Dtype;
  }
  const body: CalcRequest["body"] = { expr, field: "", ...(dtype ? { dtype } : {}) };
  if (!onlyCheck) {
    const bad = nameIssue("calc", "field", given(a, "field") ? String(a.field) : "", "Имя нового поля");
    if (bad) return { ok: false, issue: bad };
    body.field = String(a.field).trim();
    if (given(a, "unit")) {
      const u = String(a.unit).trim();
      const l = limitIssue("calc", "unit", u, MAX_UNIT, "Единица");
      if (l) return { ok: false, issue: l };
      body.unit = u;
    }
    if (given(a, "description")) {
      const d = String(a.description).trim();
      const l = limitIssue("calc", "description", d, MAX_DESCRIPTION, "Описание");
      if (l) return { ok: false, issue: l };
      body.description = d;
    }
    const r = restrictOfArgs("calc", a);
    if (!r.ok) return r;
    Object.assign(body, r.body);
    if (given(a, "outside")) {
      if (a.outside !== "keep" && a.outside !== "nan") return fail("outside", "Вне ограничителя: «keep» — оставить, «nan» — пусто.", "Передайте outside: keep или nan.");
      body.outside = a.outside;
    }
    if (a.replace !== undefined && a.replace !== null) {
      if (typeof a.replace !== "boolean") return fail("replace", "Замена — true или false.", "Передайте replace: true, чтобы заменить существующее поле.");
      body.replace = a.replace;
    }
  }
  return { ok: true, request: { modelId: a.model_id, body } };
}

/** The form that shows the arguments of a call (the window then reads the same request the core got). */
export function formOfArgs(a: Args, keep: CalcForm = EMPTY_CALC): CalcForm {
  const out: CalcForm = { ...keep, r: restrictFormOfArgs(a, keep.r) };
  if (given(a, "expr")) out.expr = String(a.expr);
  if (given(a, "field")) out.field = String(a.field).trim();
  if (given(a, "dtype") && (DTYPES as readonly unknown[]).includes(a.dtype)) out.dtype = a.dtype as Dtype;
  if (given(a, "unit")) out.unit = String(a.unit);
  if (given(a, "description")) out.description = String(a.description);
  if (a.outside === "keep" || a.outside === "nan") out.outside = a.outside;
  return out;
}
