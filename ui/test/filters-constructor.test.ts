// The constructor of a filter (ТЗ 7.35 Д-02; ADR 0023 §8; Э17 17.33) on the texts of the parser of 8.7.4
// (`core/tests/data/blocks/expr_texts.json`: 44 texts with their canonical form, 55 refusals with the place) and on the
// REAL answers of the core (`core/tests/data/blocks/filters_window_answers.json`, made by
// `core/tests/test_filters_window_answers.py` through the routes of `/filters`): the constructor ↔ the text without
// loss, the texts the core wrote for its trees, the refusals of the form = the words and the places of the core.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { readFilter, type Filter } from "../src/modules/blocks/filters/api";
import {
  addTo,
  astToRule,
  checkDraft,
  fieldsOfRule,
  newCond,
  newGroup,
  pathOfParam,
  removeAt,
  ruleToDraft,
  ruleToText,
  textToRule,
  updateAt,
  type Draft,
  type Rule,
} from "../src/modules/blocks/filters/constructor";
import { formatNumber, parseExpr } from "../src/modules/blocks/filters/expr";
import { editOf, newEdit, switchMode } from "../src/modules/blocks/filters/state";

const read = (name: string) => JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks", name), "utf-8"));
const EXPR = read("expr_texts.json") as { valid: { text: string; canonical: string; fields: string[]; functions: string[] }[]; invalid: { text: string; pos: number; line: number; column: number; char: string; reason: string }[] };
const A = read("filters_window_answers.json");
const FULL: Record<string, Record<string, unknown>> = Object.fromEntries(Object.entries(A.full as Record<string, { body: Record<string, unknown> }>).map(([k, v]) => [k, v.body]));

describe("the language of 8.7.4 on the client = the parser of the core", () => {
  it("the canonical form of the 44 valid texts, their fields", () => {
    expect(EXPR.valid.length).toBe(44);
    for (const v of EXPR.valid) {
      const p = parseExpr(v.text);
      expect(p.ok, v.text).toBe(true);
      if (!p.ok) continue;
      expect(textToRule(v.text).ok, v.text).toBe(true);
      const rule = (textToRule(v.text) as { rule: Rule }).rule;
      expect(ruleToText(rule), v.text).toBe(v.canonical);
      expect(fieldsOfRule(rule), v.text).toEqual(v.fields);
    }
  });

  it("the 55 refusals: the place (pos, line, column, char) and the kind of the core", () => {
    expect(EXPR.invalid.length).toBe(55);
    for (const bad of EXPR.invalid) {
      const p = parseExpr(bad.text);
      expect(p.ok, JSON.stringify(bad.text)).toBe(false);
      if (p.ok) continue;
      const { pos, line, column, char, reason } = p.issue;
      expect({ pos, line, column, char, reason }, JSON.stringify(bad.text)).toEqual({ pos: bad.pos, line: bad.line, column: bad.column, char: bad.char, reason: bad.reason });
      expect(p.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(p.issue.hint).not.toBe("");
    }
    expect(parseExpr("a".repeat(4001))).toMatchObject({ ok: false, issue: { reason: "too_long" } });
    expect(parseExpr("(".repeat(70) + "a" + ")".repeat(70))).toMatchObject({ ok: false, issue: { reason: "too_deep" } });
  });

  it("numbers are written as Python writes them (the text the core stores)", () => {
    const table: [number, string][] = [
      [0.1, "0.1"],
      [1e-7, "1e-07"],
      [1e20, "1e+20"],
      [123456789012345678, "1.2345678901234568e+17"],
      [1e15, "1000000000000000.0"],
      [1e16, "1e+16"],
      [0.0001, "0.0001"],
      [0.00001, "1e-05"],
      [1.5e-7, "1.5e-07"],
      [5e-324, "5e-324"],
      [1.7976931348623157e308, "1.7976931348623157e+308"],
      [999999999999999, "999999999999999"],
      [1234567.125, "1234567.125"],
      [2.5e15, "2500000000000000.0"],
      [0.00030000000000000003, "0.00030000000000000003"],
    ];
    for (const [v, text] of table) expect(formatNumber(v), String(v)).toBe(text);
  });
});

describe("the constructor ↔ the text without loss", () => {
  it("text → tree → text is the canonical text; the tree of the canonical text is the same tree", () => {
    for (const v of EXPR.valid) {
      const first = (textToRule(v.text) as { rule: Rule }).rule;
      const again = textToRule(v.canonical);
      expect(again, v.text).toEqual({ ok: true, rule: first });
      expect(ruleToText(first)).toBe(v.canonical);
    }
  });

  it("AND / OR / NOT become groups, comparisons — conditions, the rest — expression leaves", () => {
    const t = (text: string) => (textToRule(text) as { rule: Rule }).rule;
    expect(t("AU > 1 AND DOMAIN = 2")).toEqual({ op: "and", items: [{ cond: { field: "AU", op: ">", value: 1 } }, { cond: { field: "DOMAIN", op: "=", value: 2 } }] });
    expect(t("(a OR b) AND c")).toEqual({ op: "and", items: [{ op: "or", items: [{ expr: "a" }, { expr: "b" }] }, { expr: "c" }] });
    expect(t("NOT (a AND b)")).toEqual({ op: "not", items: [{ op: "and", items: [{ expr: "a" }, { expr: "b" }] }] });
    expect(t("ISNULL(Au) OR NOT ISNULL([Au ppm])")).toEqual({ op: "or", items: [{ cond: { field: "Au", op: "empty" } }, { cond: { field: "Au ppm", op: "notempty" } }] });
    expect(t("ROCK IN ('руда', 'O''Z', 3, -4.5)")).toEqual({ cond: { field: "ROCK", op: "in", values: ["руда", "O'Z", 3, -4.5] } });
    expect(t("[Порода] <> 'руда'")).toEqual({ cond: { field: "Порода", op: "<>", value: "руда" } });
    expect(t("CU >= -1.25")).toEqual({ cond: { field: "CU", op: ">=", value: -1.25 } });
    expect(t("IF(a > 1, 1, 0) = 1")).toEqual({ expr: "IF(a > 1, 1, 0) = 1" });
    expect(t("1 < AU")).toEqual({ expr: "1 < AU" });
  });

  it("a text that a condition would change stays an expression: «'2'» is a label, not the number 2", () => {
    const t = (text: string) => (textToRule(text) as { rule: Rule }).rule;
    expect(t("DOMAIN = '2'")).toEqual({ expr: "DOMAIN = '2'" });
    expect(t("DOMAIN IN ('1', 'B')")).toEqual({ expr: "DOMAIN IN ('1', 'B')" });
    expect(t("AU > 'x'")).toEqual({ expr: "AU > 'x'" });
    expect(t("[ x ] = 1")).toEqual({ expr: "[ x ] = 1" });
    for (const text of ["DOMAIN = '2'", "AU > 'x'", "[ x ] = 1"]) expect(ruleToText(t(text))).toBe(text);
  });

  it("the tree of the form ↔ the tree of the core: a draft checked is the rules it was made of", () => {
    for (const v of EXPR.valid) {
      const rule = (textToRule(v.text) as { rule: Rule }).rule;
      expect(checkDraft(ruleToDraft(rule)), v.text).toEqual({ ok: true, rule });
    }
  });

  it("a wireframe or a surface has no text; the text of a tree with them is null, the rest is not lost", () => {
    const wire: Rule = { op: "and", items: [{ cond: { field: "AU", op: ">", value: 1 } }, { wireframe: { id: 7, side: "inside", by: "frac", m: 4 } }] };
    expect(ruleToText(wire)).toBeNull();
    expect(ruleToText({ surface: { id: 8, side: "above", by: "centre" } })).toBeNull();
    expect(fieldsOfRule(wire)).toEqual(["AU"]);
    const e = editOf({ ...(readFilter(FULL.wire) as Filter) }, {});
    expect(e.text).toBe("");
    const refused = switchMode(newEdit({ draft: ruleToDraft(readFilter(FULL.wire).rules) }), "text");
    expect(refused).toMatchObject({ refusal: expect.stringContaining("каркас или поверхность") });
  });

  it("the texts the core wrote for its trees: the same text, the same fields, the same tree after the cycle", () => {
    for (const [name, f] of Object.entries(FULL)) {
      const filter = readFilter(f);
      expect(ruleToText(filter.rules), name).toBe(filter.text);
      expect(fieldsOfRule(filter.rules).map((x) => x.toLowerCase()), name).toEqual(filter.fields.map((x) => x.toLowerCase()));
      const back = checkDraft(ruleToDraft(filter.rules));
      expect(back, name).toEqual({ ok: true, rule: filter.rules });
      if (filter.text !== null) {
        const parsed = textToRule(filter.text);
        expect(parsed.ok, name).toBe(true);
        expect(ruleToText((parsed as { rule: Rule }).rule), name).toBe(filter.text);
      }
    }
    // the created trees of the core (the conditions the constructor makes): «NOT NOT ISNULL», lists, numbers in the Python form
    for (const [name, c] of Object.entries(A.created as Record<string, { body: Filter }>)) {
      const f = readFilter(c.body);
      expect(ruleToText(f.rules), name).toBe(f.text);
      if (f.text !== null) expect(ruleToText((textToRule(f.text) as { rule: Rule }).rule), name).toBe(f.text);
    }
  });

  it("the form refuses what the core refuses, with the same words and the same place", () => {
    const bad = (draft: Draft) => (checkDraft(draft) as { ok: false; issue: { param: string; message: string; hint: string } }).issue;
    const core = (k: string) => A.refused[k].body as { param: string; message: string; hint: string };
    // an order operator with a label — `filter_rules rules.items.1.cond.value`
    const order: Draft = { op: "and", items: [{ cond: { field: "A", op: "=", value: "1", list: "" } }, { cond: { field: "B", op: ">", value: "много", list: "" } }] };
    expect(bad(order)).toMatchObject({ param: core("filter_rules").param, message: core("filter_rules").message, hint: core("filter_rules").hint });
    // an expression of the tree — `expr_syntax rules.items.1.expr`
    const expr: Draft = { op: "and", items: [{ cond: { field: "A", op: "=", value: "1", list: "" } }, { expr: "AU >" }] };
    expect(bad(expr)).toMatchObject({ param: core("expr_in_tree").param, message: core("expr_in_tree").message });
    expect(bad(expr).message).toMatch(/Выражение оборвано/);
    // the text of the body — `expr_syntax expr`
    const p = parseExpr("AU > (1") as { ok: false; issue: { message: string; hint: string; pos: number; line: number; column: number; reason: string } };
    const e = core("expr_syntax") as unknown as { message: string; hint: string; pos: number; line: number; column: number; reason: string };
    expect({ m: p.issue.message, h: p.issue.hint, pos: p.issue.pos, line: p.issue.line, col: p.issue.column, r: p.issue.reason }).toEqual({ m: e.message, h: e.hint, pos: e.pos, line: e.line, col: e.column, r: e.reason });
    // the rest of the places of the core's checks
    expect(bad({ cond: { field: "", op: "=", value: "1", list: "" } }).param).toBe("rules.cond.field");
    expect(bad({ cond: { field: "B]", op: "=", value: "1", list: "" } }).param).toBe("rules.cond.field");
    expect(bad({ cond: { field: "A", op: ">", value: "", list: "" } }).param).toBe("rules.cond.value");
    expect(bad({ cond: { field: "A", op: "in", value: "", list: " \n" } }).param).toBe("rules.cond.values");
    expect(bad({ op: "and", items: [] }).param).toBe("rules.items");
    expect(bad({ op: "not", items: [newCond(), newCond()] }).param).toBe("rules.items");
    expect(bad({ wireframe: { id: null, side: "inside", by: "frac", m: 4 } }).param).toBe("rules.wireframe.id");
    expect(bad({ wireframe: { id: 3, side: "inside", by: "frac", m: 17 } }).param).toBe("rules.wireframe.m");
    let deep: Draft = newCond();
    for (let i = 0; i < 32; i++) deep = { op: "not", items: [deep] };
    expect((checkDraft(deep) as { issue: { message: string } }).issue.message).toMatch(/глубже 32/);
    expect(checkDraft({ op: "and", items: Array.from({ length: 500 }, newCond) }).ok).toBe(false); // 501 with the group
  });

  it("the values: «0,5» is a number, a label stays a label, a list is one value per line", () => {
    const ok = (c: Draft) => (checkDraft(c) as { ok: true; rule: Rule }).rule;
    expect(ok({ cond: { field: " AU ", op: ">=", value: "0,5", list: "" } })).toEqual({ cond: { field: "AU", op: ">=", value: 0.5 } });
    expect(ok({ cond: { field: "ROCK", op: "=", value: "Руда", list: "" } })).toEqual({ cond: { field: "ROCK", op: "=", value: "Руда" } });
    expect(ok({ cond: { field: "ROCK", op: "in", value: "", list: "A\n\n 3 \n4,5\nO'Z" } })).toEqual({ cond: { field: "ROCK", op: "in", values: ["A", 3, 4.5, "O'Z"] } });
    expect(ok({ cond: { field: "AU", op: "empty", value: "junk", list: "" } })).toEqual({ cond: { field: "AU", op: "empty" } });
  });

  it("editing a tree by the path: add, change, remove; the path of an error names the node", () => {
    let d: Draft = newGroup("and");
    d = addTo(d, [], { op: "or", items: [newCond()] });
    d = addTo(d, [1], newCond());
    expect(d).toMatchObject({ items: [{}, { op: "or", items: [{}, {}] }] });
    d = updateAt(d, [1, 1], (n) => ("cond" in n ? { cond: { ...n.cond, field: "AU" } } : n));
    expect(((d as { items: Draft[] }).items[1] as { items: Draft[] }).items[1]).toMatchObject({ cond: { field: "AU" } });
    d = removeAt(d, [1, 0]);
    expect(((d as { items: Draft[] }).items[1] as { items: Draft[] }).items).toHaveLength(1);
    expect(addTo({ op: "not", items: [newCond()] }, [], newCond())).toEqual({ op: "not", items: [newCond()] }); // NOT holds one
    expect(removeAt(d, [])).toBe(d);
    expect(pathOfParam("rules.items.1.items.0.cond.value")).toEqual([1, 0]);
    expect(pathOfParam("rules")).toEqual([]);
    expect(pathOfParam("rules.items.2.wireframe.id")).toEqual([2]);
    expect(astToRule({ k: "field", name: "x" })).toEqual({ expr: "x" });
  });
});
