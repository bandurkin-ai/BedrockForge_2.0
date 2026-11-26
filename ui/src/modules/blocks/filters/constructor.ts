/**
 * The constructor of a filter: the tree of rules ↔ the text of 8.7.4 without loss (ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8;
 * contract — docs/progress/e17-api.md §13; core — `blocks/filters/schema.py`). The tree is the form of the core:
 *
 *     Rule = {op: "and" | "or" | "not", items: [Rule, …]} | {expr: "<8.7.4>"}
 *          | {cond: {field, op, value? | values?}} | {wireframe: {id, side, by, m}} | {surface: {id, side, by}}
 *
 * `ruleToText` writes the whole tree as one expression in the canonical form of `calc` (null — a wireframe or a
 * surface is in it, or the text would be longer than the parser takes); `textToRule` reads a text back: AND / OR / NOT
 * become groups, «поле — оператор — значение», `ISNULL(поле)` and `поле IN (…)` become conditions, anything else —
 * an expression leaf with its canonical text, so text → tree → text is the canonical text of the first (a string that
 * looks like a number, «'2'», stays an expression: a condition would turn it into the number 2 — the core does so —
 * and a category is compared with a text by its label, with a number by its value). A `Draft` is the tree as the window
 * edits it (a value as typed, a list one value per line); `checkDraft` is the check of the core in Russian with the
 * path of the place (`rules.items.1.cond.value` — the same `param` the core answers), so a wrong form never leaves the
 * window. No React.
 */
import { boolOf, exprDepth, exprFields, exprText, formatField, MAX_DEPTH, MAX_LENGTH, parseExpr, type Ex, type ExprIssue } from "./expr";

export type GroupOp = "and" | "or" | "not";
export type CondOp = "=" | "<>" | ">" | ">=" | "<" | "<=" | "empty" | "notempty" | "in";
export type Value = string | number;

export interface CondBody {
  field: string;
  op: CondOp;
  value?: Value;
  values?: Value[];
}
export interface WireBody {
  id: number | null;
  side: "inside" | "outside";
  by: "frac" | "centre";
  m: number;
}
export interface SurfaceBody {
  id: number | null;
  side: "above" | "below";
  by: "centre" | "frac";
}
/** The layer of the core (`slab`, Э20): from ≤ c < to along an axis; `frame` — the angles of the swath frame of the axes u, v. Read-only here (the drill of 20.15 and the swath give it). */
export interface SlabBody {
  axis: "x" | "y" | "z" | "u" | "v";
  from: number;
  to: number;
  by: "centre" | "share";
  frame?: { azimuth: number; dip: number; roll: number };
}
export type Rule = { op: GroupOp; items: Rule[] } | { expr: string } | { cond: CondBody } | { wireframe: WireBody } | { surface: SurfaceBody } | { slab: SlabBody };

const AXIS_TITLE: Record<SlabBody["axis"], string> = { x: "X", y: "Y", z: "Z", u: "U", v: "V" };
const slabNum = (v: number): string => String(Math.round(v * 1e6) / 1e6).replace(".", ",");

/** The layer in words: «Z от 0 до 10, по доле»; an axis of the swath frame names the angles. */
export function slabTitle(s: SlabBody): string {
  const frame = s.frame && (s.axis === "u" || s.axis === "v") ? `, система полосы: азимут ${slabNum(s.frame.azimuth)}°, падение ${slabNum(s.frame.dip)}°, поворот ${slabNum(s.frame.roll)}°` : "";
  return `${AXIS_TITLE[s.axis] ?? s.axis} от ${slabNum(s.from)} до ${slabNum(s.to)}, ${s.by === "share" ? "по доле высоты" : "по центру"}${frame}`;
}

export const COND_OPS: readonly { op: CondOp; label: string }[] = [
  { op: "=", label: "равно" },
  { op: "<>", label: "не равно" },
  { op: ">", label: "больше" },
  { op: ">=", label: "больше или равно" },
  { op: "<", label: "меньше" },
  { op: "<=", label: "меньше или равно" },
  { op: "empty", label: "пусто" },
  { op: "notempty", label: "не пусто" },
  { op: "in", label: "в списке" },
];
export const GROUP_LABEL: Record<GroupOp, string> = { and: "И — выполняются все правила", or: "ИЛИ — выполняется любое правило", not: "НЕ — правило не выполняется" };
export const ORDER_OPS: readonly CondOp[] = [">", ">=", "<", "<="];
export const NO_VALUE_OPS: readonly CondOp[] = ["empty", "notempty"];
export const MAX_RULE_DEPTH = 32;
export const MAX_NODES = 500;
export const MAX_VALUES = 1000;
export const MAX_FIELD = 100;
export const MAX_LABEL = 200;
export const DEFAULT_M = 4;

// ---- numbers as the core reads them ----

const NUMBER = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$/;

/** A number typed («0,5», «0.5», «1e-7») or null: the same rule as the core's `_number`. */
export function numberOf(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && NUMBER.test(v.trim())) {
    const n = Number(v.trim().replace(",", "."));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// ---- the tree ↔ the expression of `calc` ----

const valueEx = (v: Value): Ex => (typeof v === "string" ? { k: "str", v } : { k: "num", v });

function condEx(c: CondBody): Ex {
  const field: Ex = { k: "field", name: c.field };
  if (c.op === "empty") return { k: "call", name: "ISNULL", args: [field] };
  if (c.op === "notempty") return { k: "not", a: { k: "call", name: "ISNULL", args: [field] } };
  if (c.op === "in") return { k: "in", a: field, values: [...(c.values ?? [])] };
  return { k: "cmp", op: c.op, l: field, r: valueEx(c.value ?? 0) };
}

/** The tree as an expression of `calc`; null when a wireframe or a surface is in it or an `expr` does not parse. */
export function ruleToAst(rule: Rule): Ex | null {
  if ("expr" in rule) {
    const p = parseExpr(rule.expr);
    return p.ok ? p.node : null;
  }
  if ("cond" in rule) return condEx(rule.cond);
  if ("items" in rule) {
    const parts = rule.items.map(ruleToAst);
    if (parts.some((p) => p === null) || !parts.length) return null;
    const kids = parts as Ex[];
    if (rule.op === "not") return { k: "not", a: kids[0] };
    return kids.length === 1 ? kids[0] : boolOf(rule.op === "and" ? "AND" : "OR", kids);
  }
  return null;
}

/** The record of the whole filter in the language of 8.7.4, or null (a wireframe, a surface, a text of the parser's refusal). */
export function ruleToText(rule: Rule): string | null {
  try {
    const tree = ruleToAst(rule);
    if (!tree || exprDepth(tree) > MAX_DEPTH) return null;
    const text = exprText(tree);
    return Array.from(text).length <= MAX_LENGTH ? text : null;
  } catch {
    return null;
  }
}

/** Can this comparison / list be a condition without changing its meaning (see the head of the file)? */
function plainValue(v: Value, numericOnly: boolean): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (numericOnly) return false;
  return v.length > 0 && Array.from(v).length <= MAX_LABEL && numberOf(v) === null;
}

function fieldOf(n: Ex): string | null {
  return n.k === "field" && n.name === n.name.trim() && n.name.length <= MAX_FIELD && formatField(n.name) !== null ? n.name : null;
}

function literal(n: Ex): Value | null {
  if (n.k === "num") return n.v;
  if (n.k === "str") return n.v;
  if (n.k === "neg" && n.a.k === "num" && n.a.v > 0) return -n.a.v;
  return null;
}

function condOf(n: Ex): CondBody | null {
  if (n.k === "call" && n.name === "ISNULL" && n.args.length === 1) {
    const f = fieldOf(n.args[0]);
    return f === null ? null : { field: f, op: "empty" };
  }
  if (n.k === "cmp") {
    const f = fieldOf(n.l);
    const v = literal(n.r);
    if (f === null || v === null || !plainValue(v, ORDER_OPS.includes(n.op as CondOp))) return null;
    return { field: f, op: n.op as CondOp, value: v };
  }
  if (n.k === "in") {
    const f = fieldOf(n.a);
    if (f === null || n.values.length > MAX_VALUES || !n.values.every((v) => plainValue(v, false))) return null;
    return { field: f, op: "in", values: [...n.values] };
  }
  return null;
}

/** A tree of the rules from a parsed expression: groups, conditions, the rest — expression leaves. */
export function astToRule(n: Ex): Rule {
  if (n.k === "bool") return { op: n.op === "AND" ? "and" : "or", items: n.items.map(astToRule) };
  if (n.k === "not") {
    const inner = n.a.k === "call" && n.a.name === "ISNULL" && n.a.args.length === 1 ? fieldOf(n.a.args[0]) : null;
    if (inner !== null) return { cond: { field: inner, op: "notempty" } };
    return { op: "not", items: [astToRule(n.a)] };
  }
  const c = condOf(n);
  return c ? { cond: c } : { expr: exprText(n) };
}

/** A text → the tree of the rules (a wrong text — the place and the words of the core). */
export function textToRule(text: string): { ok: true; rule: Rule } | { ok: false; issue: ExprIssue } {
  const p = parseExpr(text);
  return p.ok ? { ok: true, rule: astToRule(p.node) } : { ok: false, issue: p.issue };
}

/** The fields the tree reads, once each (compared without case), in the order of first use. */
export function fieldsOfRule(rule: Rule): string[] {
  const seen = new Map<string, string>();
  const add = (name: string) => {
    if (!seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name);
  };
  const walk = (r: Rule): void => {
    if ("cond" in r) add(r.cond.field);
    else if ("expr" in r) {
      const p = parseExpr(r.expr);
      if (p.ok) exprFields(p.node).forEach(add);
    } else if ("items" in r) r.items.forEach(walk);
  };
  walk(rule);
  return [...seen.values()];
}

/** The wireframes and surfaces the tree names, once each (null ids left out). */
export function objectIdsOfRule(rule: Rule): number[] {
  const out: number[] = [];
  const walk = (r: Rule): void => {
    for (const k of ["wireframe", "surface"] as const) {
      const body = (r as Record<string, { id: number | null } | undefined>)[k];
      if (body && body.id !== null && !out.includes(body.id)) out.push(body.id);
    }
    if ("items" in r) r.items.forEach(walk);
  };
  walk(rule);
  return out;
}

// ---- the tree as the window edits it ----

/** A condition as typed: `value` is the text of the field, `list` — one value per line («в списке»). */
export interface DraftCond {
  field: string;
  op: CondOp;
  value: string;
  list: string;
}
export type Draft = { op: GroupOp; items: Draft[] } | { expr: string } | { cond: DraftCond } | { wireframe: WireBody } | { surface: SurfaceBody } | { slab: SlabBody };

const show = (v: Value): string => String(v);

export function ruleToDraft(rule: Rule): Draft {
  if ("items" in rule) return { op: rule.op, items: rule.items.map(ruleToDraft) };
  if ("cond" in rule) {
    const c = rule.cond;
    return { cond: { field: c.field, op: c.op, value: c.value === undefined ? "" : show(c.value), list: (c.values ?? []).map(show).join("\n") } };
  }
  return rule;
}

export const newCond = (): Draft => ({ cond: { field: "", op: ">", value: "", list: "" } });
export const newGroup = (op: GroupOp = "and"): Draft => ({ op, items: [newCond()] });
export const newWireframe = (): Draft => ({ wireframe: { id: null, side: "inside", by: "frac", m: DEFAULT_M } });
export const newSurface = (): Draft => ({ surface: { id: null, side: "above", by: "centre" } });
export const newExpr = (): Draft => ({ expr: "" });
export const EMPTY_DRAFT: Draft = newGroup("and");

/** What is wrong in a tree: the place (`param` as the core writes it), the words, and — for an expression — its place in the text. */
export interface RuleIssue {
  /** «rules.items.1.cond.value» */
  param: string;
  message: string;
  hint: string;
  expr?: ExprIssue;
}

const bad = (param: string, message: string, hint = "Проверьте правило фильтра."): RuleIssue => ({ param, message, hint });

function scalar(text: string, param: string, numeric: boolean): Value | RuleIssue {
  const n = numberOf(text);
  if (n !== null) return n;
  if (numeric) return bad(param, "Значение для сравнения по порядку — число.", "Введите число, например 0,5 или 12.");
  const t = text.trim();
  if (t && Array.from(t).length <= MAX_LABEL) return t;
  return bad(param, "Значение условия — число или подпись категории (до 200 символов).", "Введите число или выберите подпись.");
}

function condOfDraft(c: DraftCond, path: string): CondBody | RuleIssue {
  const field = c.field.trim();
  if (!field || field.length > MAX_FIELD || field.includes("]")) return bad(`${path}.field`, "Имя поля — от 1 до 100 символов, без «]».", "Выберите поле модели.");
  if (NO_VALUE_OPS.includes(c.op)) return { field, op: c.op };
  if (c.op === "in") {
    const lines = c.list.split(/\r?\n/).filter((l) => l.trim() !== "");
    if (!lines.length || lines.length > MAX_VALUES) return bad(`${path}.values`, "Список значений — от 1 до 1000.", "Добавьте значения в список, по одному в строке.");
    const values: Value[] = [];
    for (const [i, line] of lines.entries()) {
      const v = scalar(line, `${path}.values.${i}`, false);
      if (typeof v === "object") return v;
      values.push(v);
    }
    return { field, op: "in", values };
  }
  if (!c.value.trim()) return bad(`${path}.value`, "У условия нет значения.", "Введите значение для сравнения.");
  const v = scalar(c.value, `${path}.value`, ORDER_OPS.includes(c.op));
  return typeof v === "object" ? v : { field, op: c.op, value: v };
}

/** The tree of the window as the rules of the core, or the first thing the core would refuse (the same words). */
export function checkDraft(draft: Draft): { ok: true; rule: Rule } | { ok: false; issue: RuleIssue } {
  let nodes = 0;
  const walk = (d: Draft, path: string, depth: number): Rule | RuleIssue => {
    if (depth > MAX_RULE_DEPTH) return bad(path, "Правило вложено глубже 32 уровней.", "Упростите дерево правил.");
    if (++nodes > MAX_NODES) return bad(path, "В фильтре больше 500 правил.", "Разделите фильтр на несколько.");
    if ("items" in d) {
      if (!d.items.length) return bad(`${path}.items`, "В группе нет правил.", "Добавьте правило или удалите группу.");
      if (d.op === "not" && d.items.length !== 1) return bad(`${path}.items`, "«НЕ» относится ровно к одному правилу.", "Оставьте одно правило внутри «НЕ».");
      const items: Rule[] = [];
      for (const [i, it] of d.items.entries()) {
        const r = walk(it, `${path}.items.${i}`, depth + 1);
        if ("param" in r) return r;
        items.push(r);
      }
      return { op: d.op, items };
    }
    if ("expr" in d) {
      const p = parseExpr(d.expr);
      return p.ok ? { expr: d.expr } : { param: `${path}.expr`, message: p.issue.message, hint: p.issue.hint, expr: p.issue };
    }
    if ("cond" in d) {
      const c = condOfDraft(d.cond, `${path}.cond`);
      return "param" in c ? c : { cond: c };
    }
    if ("wireframe" in d) {
      const w = d.wireframe;
      if (w.m < 1 || w.m > 16 || !Number.isInteger(w.m)) return bad(`${path}.wireframe.m`, "Подсетка доли — целое от 1 до 16.", "Укажите m, по умолчанию 4.");
      return { wireframe: { ...w } };
    }
    if ("slab" in d) return { slab: { ...d.slab, ...(d.slab.frame && { frame: { ...d.slab.frame } }) } }; // read-only: the layer goes as it came, `frame` kept
    return { surface: { ...d.surface } };
  };
  const out = walk(draft, "rules", 1);
  if ("param" in out) return { ok: false, issue: out };
  const missing = firstLeafWithoutObject(out);
  if (missing) return { ok: false, issue: missing };
  return { ok: true, rule: out };
}

/** A new leaf without an object is not a rule yet (the core takes a null id only of an object an archive lost). */
function firstLeafWithoutObject(rule: Rule, path = "rules"): RuleIssue | null {
  if ("items" in rule) {
    for (const [i, it] of rule.items.entries()) {
      const r = firstLeafWithoutObject(it, `${path}.items.${i}`);
      if (r) return r;
    }
    return null;
  }
  for (const k of ["wireframe", "surface"] as const) {
    const body = (rule as Record<string, { id: number | null } | undefined>)[k];
    if (body && body.id === null) return bad(`${path}.${k}.id`, k === "wireframe" ? "Не выбран каркас." : "Не выбрана поверхность.", "Выберите объект из списка.");
  }
  return null;
}

// ---- editing a tree by the path of the indexes ----

export type Path = readonly number[];

export function nodeAt(root: Draft, path: Path): Draft | null {
  let cur: Draft = root;
  for (const i of path) {
    if (!("items" in cur) || !cur.items[i]) return null;
    cur = cur.items[i];
  }
  return cur;
}

/** The tree with the node at `path` replaced by `next(old)`; a path that does not exist leaves the tree as it is. */
export function updateAt(root: Draft, path: Path, next: (d: Draft) => Draft): Draft {
  if (!path.length) return next(root);
  if (!("items" in root) || !root.items[path[0]]) return root;
  return { ...root, items: root.items.map((it, i) => (i === path[0] ? updateAt(it, path.slice(1), next) : it)) };
}

/** The tree without the node at `path` (the root is not removed; an emptied group stays — the check names it). */
export function removeAt(root: Draft, path: Path): Draft {
  if (!path.length) return root;
  const parent = path.slice(0, -1);
  const at = path[path.length - 1];
  return updateAt(root, parent, (d) => ("items" in d ? { ...d, items: d.items.filter((_, i) => i !== at) } : d));
}

export function addTo(root: Draft, path: Path, node: Draft): Draft {
  return updateAt(root, path, (d) => ("items" in d && !(d.op === "not" && d.items.length >= 1) ? { ...d, items: [...d.items, node] } : d));
}

/** The path of the node an error names: «rules.items.1.items.0.cond.value» → [1, 0]. */
export function pathOfParam(param: string): number[] {
  const parts = param.split(".");
  const out: number[] = [];
  for (let i = 1; i + 1 < parts.length; i += 2) {
    if (parts[i] !== "items" || !/^\d+$/.test(parts[i + 1])) break;
    out.push(Number(parts[i + 1]));
  }
  return out;
}

export const sameParam = (a: Path, b: Path): boolean => a.length === b.length && a.every((v, i) => v === b[i]);
