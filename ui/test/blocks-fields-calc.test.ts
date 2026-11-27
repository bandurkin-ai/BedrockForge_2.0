// The calculator of the fields of a block model and the commands `blocks.calc`, `blocks.calc.check` (ТЗ 8.7.4; ADR 0023 §7,
// decision 10, № 69; Э17 17.36) on the REAL answers of the core (`core/tests/data/blocks/fields_window_answers.json`, made by
// `core/tests/test_blocks_fields_window_answers.py` through `POST /blocks/calc/check`, `POST /blocks/{id}/calc` and the job
// `blocks.calc`): the request of the form = the body the core got; the place of an error in the text as the core counted it;
// the refusals 404 / 409 / 422 / 423 in Russian in their place of the form; the replace of a field after a question in our
// own window; the run of the command through a stubbed core that answers with those answers.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { CoreError } from "../src/shell/api";
import type { App } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { answerClaude } from "./claude-confirm";
import { windowStore } from "../src/shell/windows";
import { blocksStore, loadBlocks } from "../src/modules/blocks/state";
import { checkExpr, exprIssueOf, readCheck, readResult } from "../src/modules/blocks/fields/api";
import { chooseModel, checkNow, openFieldsWindow, renderFieldsWindow, windowOfFields } from "../src/modules/blocks/fields/bind";
import { argsOfForm, errorMarker, fieldRef, formOfArgs, requestOfArgs, utf16Index, EMPTY_CALC, type CalcForm } from "../src/modules/blocks/fields/calcForm";
import { EMPTY_RESTRICT } from "../src/modules/blocks/fields/common";
import { CalcWindow } from "../src/modules/blocks/fields/CalcWindow";
import { commands, FIELD_BUTTONS } from "../src/modules/blocks/fields/commands";
import { cancelRun } from "../src/modules/blocks/fields/run";
import { currentRun, FIELDS_WINDOW, fieldsStore, setCalc } from "../src/modules/blocks/fields/state";
import { A, answer, answerDialog, canon, IDS, install, json, posts, startJob, stub, uninstall, without } from "./blocks-fields-kit";

const CALC = A.calc as Record<string, { request: Record<string, unknown>; status: number; body?: Record<string, any>; job?: Record<string, any> }>;
const CHECK = A.check as Record<string, { request: Record<string, unknown>; status: number; body: Record<string, any> }>;
const draftCond = (field: string, op: ">" | "=", value: string) => ({ cond: { field, op, value, list: "" } }) as const;
const QUICK = { ...EMPTY_RESTRICT, restrictOn: true, restrict: draftCond("CU", ">", "0.5") };

let app: App;

beforeEach(() => {
  app = install();
  stub.routes.push((method, path, body) => {
    if (method === "POST" && path === "/blocks/calc/check") {
      const hit = Object.values(CHECK).find((c) => canon(c.request) === canon(body));
      return hit ? json(hit.body, hit.status) : undefined;
    }
    return undefined;
  });
});

afterEach(() => {
  uninstall();
});

/** The form of a recorded request: what the person sets in the window to ask for it. */
const FORMS: Record<string, Partial<CalcForm>> = {
  new: { expr: "IF(AU > 1, AU * 2, 0)", field: "AU2", dtype: "f8", unit: "г/т", description: "Удвоенное AU" },
  exists: { expr: "IF(AU > 1, AU * 2, 0)", field: "AU2", dtype: "f8" },
  replace: { expr: "IF(AU > 1, AU * 3, 0)", field: "AU2", dtype: "f8" },
  category: { expr: "IF(AU > 1, 'богатая', 'бедная')", field: "GRADE", dtype: "cat" },
  bool_i1: { expr: "AU > 1 AND DOM = 'Руда'", field: "ORE", dtype: "i1" },
  round_i2: { expr: "ROUND(AU * 10, 0)", field: "AU10", dtype: "i2" },
  outside_nan: { expr: "AU + 1", field: "AU_N", r: QUICK, outside: "nan" },
  outside_keep: { expr: "AU + 1", field: "AU_K", r: QUICK, outside: "keep" },
  with_filter: { expr: "CU * 10", field: "CU10", r: { ...QUICK, filterId: IDS.filter }, outside: "nan" },
};
const formOf = (name: string): CalcForm => ({ ...EMPTY_CALC, ...FORMS[name] });

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the arguments → the body of the recorded request`, () => {
      const got = argsOfForm(IDS.model, formOf(name));
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      const req = requestOfArgs(got.args);
      expect(req.ok).toBe(true);
      if (!req.ok) return;
      expect(req.request.modelId).toBe(IDS.model);
      // «replace» is not a key of the form: the run adds it after the question (№ 69)
      const recorded = without(CALC[name].request, "expected_version", "replace");
      expect(canon(req.request.body)).toBe(canon(recorded));
    });
    it(`${name}: the arguments of a call show in the form and come back as the same arguments`, () => {
      const got = argsOfForm(IDS.model, formOf(name));
      if (!got.ok) throw new Error("form");
      const again = argsOfForm(IDS.model, formOfArgs(got.args));
      expect(again).toEqual(got);
    });
  }

  it("the check asks only for the expression and the type; the form without a name can be checked", () => {
    const got = argsOfForm(IDS.model, { ...EMPTY_CALC, expr: "AU + 1", dtype: "f4" }, true);
    expect(got).toEqual({ ok: true, args: { model_id: IDS.model, expr: "AU + 1", dtype: "f4" } });
    expect(requestOfArgs({ model_id: IDS.model, expr: "AU + 1" }, true)).toMatchObject({ ok: true, request: { modelId: IDS.model, body: { expr: "AU + 1" } } });
  });

  it("a request that is not right is told in Russian, with a hint and the place, before the core is asked", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = requestOfArgs(a);
      expect(r.ok, JSON.stringify(a)).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(r.issue.tab).toBe("calc");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return r.issue.place;
    };
    const ok = { model_id: IDS.model, expr: "AU + 1", field: "X" };
    expect(bad({})).toBe("model");
    expect(bad({ model_id: 1.5, expr: "AU" })).toBe("model");
    expect(bad({ model_id: IDS.model })).toBe("expr");
    expect(bad({ model_id: IDS.model, expr: "   " })).toBe("expr");
    expect(bad({ model_id: IDS.model, expr: "A".repeat(4001), field: "X" })).toBe("expr");
    expect(bad({ model_id: IDS.model, expr: "AU" })).toBe("field");
    expect(bad({ ...ok, field: "idx" })).toBe("field");
    expect(bad({ ...ok, field: "Я".repeat(101) })).toBe("field");
    expect(bad({ ...ok, dtype: "f16" })).toBe("dtype");
    expect(bad({ ...ok, unit: "я".repeat(41) })).toBe("unit");
    expect(bad({ ...ok, description: "я".repeat(501) })).toBe("description");
    expect(bad({ ...ok, filter_id: 1.5 })).toBe("filter");
    expect(bad({ ...ok, restrict: "{не json" })).toBe("restrict");
    expect(bad({ ...ok, restrict: "[1]" })).toBe("restrict");
    expect(bad({ ...ok, outside: "zero" })).toBe("outside");
    expect(bad({ ...ok, replace: "yes" })).toBe("replace");
    expect(stub.sent.filter((r) => r.method !== "GET")).toEqual([]);
    // the window: no model, an empty expression, an unfinished restrictor, no name
    const f = (modelId: number | null, patch: Partial<CalcForm>) => argsOfForm(modelId, { ...formOf("new"), ...patch });
    for (const [model, patch, place] of [
      [null, {}, "model"],
      [IDS.model, { expr: " " }, "expr"],
      [IDS.model, { field: " " }, "field"],
      [IDS.model, { r: { ...EMPTY_RESTRICT, restrictOn: true } }, "restrict"],
    ] as const) {
      const r = f(model, patch);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.issue.place).toBe(place);
    }
  });

  it("the name of a field in an expression: as it is, or in brackets when it has a space or a sign", () => {
    expect(fieldRef("AU")).toBe("AU");
    expect(fieldRef("Au_1")).toBe("Au_1");
    expect(fieldRef("Мощность")).toBe("Мощность");
    expect(fieldRef("Au ppm")).toBe("[Au ppm]");
    expect(fieldRef("1AU")).toBe("[1AU]");
    expect(fieldRef("AU-2")).toBe("[AU-2]");
  });
});

describe("the check of the expression on the real answers", () => {
  for (const name of ["number", "bool", "category", "bracket"]) {
    it(`${name}: the kind of the result, the type, the fields read, the canonical text`, () => {
      const c = readCheck(CHECK[name].body)!;
      expect(c).toEqual(CHECK[name].body);
    });
  }
  it("the types: a number is f8, a condition i1, a text cat; a bracketed name is written without brackets", () => {
    expect(readCheck(CHECK.number.body)).toMatchObject({ out_kind: "number", dtype: "f8", fields_used: ["AU", "CU"] });
    expect(readCheck(CHECK.bool.body)).toMatchObject({ out_kind: "bool", dtype: "i1" });
    expect(readCheck(CHECK.category.body)).toMatchObject({ out_kind: "category", dtype: "cat" });
    expect(readCheck(CHECK.bracket.body)?.text).toBe("AU + 1");
    expect(readCheck(CHECK.dtype_i2.body)).toMatchObject({ dtype: "i2" });
    expect(readCheck({ text: "x" })).toBeNull();
    expect(readCheck(null)).toBeNull();
  });

  const WRONG = ["syntax", "syntax_import", "syntax_second_line", "field", "type_text", "dtype_cat_from_number", "dtype_number_from_text"];
  for (const name of WRONG) {
    it(`${name}: the place of the error as the core counted it — the line, the column, the character under the caret`, async () => {
      const c = CHECK[name];
      expect(c.status).toBe(422);
      const expr = String(c.request.expr);
      let err: unknown;
      await checkExpr(IDS.model, expr, (c.request.dtype as never) ?? null).catch((e: unknown) => (err = e));
      expect(err).toBeInstanceOf(CoreError);
      const issue = exprIssueOf(err)!;
      expect(issue).toMatchObject({ code: c.body.code, pos: c.body.pos, line: c.body.line, column: c.body.column });
      expect(issue.message).toBe(c.body.message);
      expect(issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(issue.hint).not.toBe("");
      const m = errorMarker(expr, issue.pos);
      expect(m.line).toBe(c.body.line);
      expect(m.column).toBe(c.body.column);
      expect(expr.slice(m.from, m.to)).toBe(c.body.char); // «» at the end of the text
      expect(m.caret).toBe(`${" ".repeat(m.column - 1)}^`);
      expect(m.lineText).toBe(expr.split("\n")[c.body.line - 1]);
    });
  }

  it("an unknown field is named; a refusal that names no place (the empty expression, the type) has none", async () => {
    let err: unknown;
    await checkExpr(IDS.model, "AU + NOPE").catch((e: unknown) => (err = e));
    expect(exprIssueOf(err)).toMatchObject({ code: "expr_field", field: "NOPE", column: 6 });
    const none = [CHECK.empty, CHECK.dtype_unknown, CHECK.model_missing];
    for (const c of none) {
      const e = Object.assign(new CoreError(c.status, c.body.code, c.body.message, c.body.hint), { body: c.body });
      expect(exprIssueOf(e)).toBeNull();
      expect(c.body.message).toMatch(/[А-Яа-яЁё]/);
    }
    expect(exprIssueOf(new Error("x"))).toBeNull();
  });

  it("the position counts characters, not UTF-16 units: a letter outside the plane before the error does not shift it", () => {
    const text = "IF('𝔄' = 'x', AU +, 0)";
    const pos = Array.from(text).indexOf(",", 12); // in code points
    expect(utf16Index(text, 5)).toBe(6); // the letter takes two units
    const m = errorMarker(text, pos);
    expect(text.slice(m.from, m.to)).toBe(",");
    expect(m.column).toBe(pos + 1);
    expect(errorMarker("AU +", 4)).toMatchObject({ line: 1, column: 5, caret: "    ^", from: 4, to: 4 });
    expect(errorMarker("a\nb", 99)).toMatchObject({ line: 2, from: 3, to: 3 });
    expect(errorMarker("a\n\nb", 2)).toMatchObject({ line: 2, lineText: "", column: 1, from: 2, to: 2 });
  });
});

describe("the live check of the window", () => {
  it("asks the core, keeps the answer for the text it was for, and marks the error where the core said", async () => {
    chooseModel(IDS.model);
    setCalc({ expr: "AU +" });
    await checkNow();
    let c = fieldsStore.get().check;
    expect(c.phase).toBe("error");
    expect(c.issue).toMatchObject({ pos: 4, line: 1, column: 5 });
    setCalc({ expr: "AU * 2 + CU" });
    await checkNow();
    c = fieldsStore.get().check;
    expect(c.phase).toBe("ok");
    expect(c.result).toEqual(CHECK.number.body);
    expect(posts(/calc\/check$/).map((r) => r.body)).toEqual([{ model_id: IDS.model, expr: "AU +" }, { model_id: IDS.model, expr: "AU * 2 + CU" }]);
    setCalc({ expr: "" });
    await checkNow();
    expect(fieldsStore.get().check.phase).toBe("idle");
  });

  it("the window shows the caret under the error, the words of the core, and «К месту ошибки»", async () => {
    await app.registry.execute("blocks.calc", {}, "user");
    const win = windowOfFields()!;
    setCalc({ expr: "IF(AU > 1,\n AU +, 0)" });
    await checkNow();
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: win })));
    expect(html).toContain('data-testid="calc-marker"');
    expect(html).toContain(" AU +, 0)\n");
    expect(html).toContain(`<b>${"     "}^</b>`);
    expect(html).toContain(CHECK.syntax_second_line.body.message);
    expect(html).toContain('data-testid="calc-to-error"');
    expect(html).toContain('aria-invalid="true"');
  });

  it("a right expression is told with the kind of the result and the fields read", async () => {
    await app.registry.execute("blocks.calc", {}, "user");
    setCalc({ expr: "IF(AU > 1, 'богатая', 'бедная')" });
    await checkNow();
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toContain('data-testid="calc-ok"');
    expect(html).toContain("результат — категория, тип поля cat; читает поля: AU");
  });
});

describe("the commands", () => {
  it("are in the registry with Russian titles and descriptions for the assistant; blocks.calc writes (confirm), the check does not", () => {
    expect(commands.map((c) => c.id).slice(0, 2)).toEqual(["blocks.calc", "blocks.calc.check"]);
    const calc = app.registry.get("blocks.calc")!;
    expect(calc.run).toBeTypeOf("function");
    expect(calc.confirm).toBe(true);
    expect(calc.uiOnly).toBeUndefined();
    expect(calc.required ?? []).toEqual([]); // without model_id — the window
    expect(calc.title).toBe("Калькулятор полей");
    expect(calc.description).toMatch(/Пропуск .*не превращается в ноль/);
    expect(Object.keys(calc.params!)).toEqual(["model_id", "expr", "field", "dtype", "unit", "description", "filter_id", "restrict", "outside", "replace"]);
    const check = app.registry.get("blocks.calc.check")!;
    expect(check.confirm).toBeUndefined();
    expect(check.required).toEqual(["model_id", "expr"]);
    for (const b of FIELD_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
  });

  it("are unavailable without a project and without block models", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("blocks.calc", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект" });
    blocksStore.set({ list: [], loaded: true });
    expect((await app.registry.execute("blocks.calc", {})).result).toBe("В проекте нет блочных моделей");
    expect((await app.registry.execute("blocks.calc.check", { model_id: 1, expr: "AU" })).ok).toBe(false); // the model is not in the list
  });

  it("without model_id the window opens on its tab with the model of the tree; once; the window is the shell's", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    const r = await app.registry.execute("blocks.calc", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: калькулятор" });
    const win = windowStore.get().windows.filter((w) => w.kind === FIELDS_WINDOW);
    expect(win).toHaveLength(1);
    expect(win[0].title).toBe("Калькулятор · Месторождение");
    expect(fieldsStore.get()).toMatchObject({ tab: "calc", modelId: IDS.model });
    expect(openFieldsWindow(app, "calc")).toBe(windowOfFields());
    expect(windowStore.get().windows.filter((w) => w.kind === FIELDS_WINDOW)).toHaveLength(1);
    expect(renderFieldsWindow({ ...win[0], kind: "other" })).toBeNull();
    expect(renderFieldsWindow(win[0])).not.toBeNull();
    expect(stub.sent.filter((r) => r.method === "POST")).toEqual([]);
  });
});

describe("the run of the calculator", () => {
  const args = (name: string) => {
    const got = argsOfForm(IDS.model, formOf(name));
    if (!got.ok) throw new Error("form");
    return got.args;
  };

  it("a new field: the body, the job, the result as the core wrote it, the line for the log; the form shows what was asked", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC.new) : undefined));
    const r = await app.registry.execute("blocks.calc", args("new"), "user");
    expect(r.ok, r.result).toBe(true);
    const sent = posts(/\/calc$/);
    expect(sent).toHaveLength(1);
    expect(sent[0].path).toBe(`/blocks/${IDS.model}/calc`);
    expect(sent[0].body).toEqual({ ...without(CALC.new.request, "expected_version"), expected_version: stub.version });
    expect(canon(sent[0].body)).toBe(canon(CALC.new.request));
    expect(r.result).toMatch(/^Поле «AU2» записано в модель «Месторождение»: блоков: 24, прошло ограничитель: 24, без значения: 2; от 0 до 10, среднее 2,681818; версия модели 2$/);
    const run = currentRun();
    expect(run.phase).toBe("done");
    expect(run.result).toEqual(readResult(CALC.new.job!.result));
    expect(run.result).toMatchObject({ field: "AU2", rows: 24, missing: 2, dtype: "f8", version: 2 });
    expect(run.result!.stats).toMatchObject({ count: 22, missing: 2, min: 0, max: 10 }); // the gap is not a zero
    expect(fieldsStore.get().calc).toMatchObject({ expr: "IF(AU > 1, AU * 2, 0)", field: "AU2", dtype: "f8", unit: "г/т" });
    expect(windowOfFields()).not.toBeNull(); // a call of the user shows the window
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    for (const id of ["fields-window", "calc-panel", "calc-expr", "calc-name", "calc-dtype", "calc-build", "fields-result", "fields-result-rows", "fields-result-missing", "calc-restrict-filter"]) expect(html, id).toContain(`data-testid="${id}"`);
    expect(html).toContain("Записано поле «AU2»");
  });

  it("the assistant does not open windows; the list of models is read again after the write", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC.new) : undefined));
    stub.version = 2;
    await loadBlocks(); // the interface runs over the version it read
    const run = app.registry.execute("blocks.calc", args("new"), "claude");
    await answerClaude(true); // the confirmation of the assistant
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    expect(windowOfFields()).toBeNull();
    expect(posts(/\/calc$/)[0].body.expected_version).toBe(2);
    expect(stub.sent.filter((x) => x.method === "GET" && x.path === "/blocks").length).toBeGreaterThan(0);
  });

  it("the outside of the restrictor, the filter of the library and the quick restrictor go in the body as the form set them", async () => {
    for (const name of ["outside_nan", "outside_keep", "with_filter"]) {
      stub.sent.length = 0;
      stub.routes = [(m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC[name]) : undefined)];
      const r = await app.registry.execute("blocks.calc", args(name), "user");
      expect(r.ok, `${name}: ${r.result}`).toBe(true);
      expect(canon(posts(/\/calc$/)[0].body)).toBe(canon({ ...CALC[name].request, expected_version: stub.version }));
      expect(currentRun().result).toEqual(readResult(CALC[name].job!.result));
    }
    expect(currentRun().result!.passed).toBeLessThan(currentRun().result!.rows);
  });

  it("the types of the field: i1 from a condition, i2 from a number, cat from a text", async () => {
    for (const name of ["category", "bool_i1", "round_i2"]) {
      stub.routes = [(m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC[name]) : undefined)];
      const r = await app.registry.execute("blocks.calc", args(name), "user");
      expect(r.ok, `${name}: ${r.result}`).toBe(true);
      expect(currentRun().result?.dtype).toBe(CALC[name].job!.result.dtype);
    }
  });

  it("a field that is there: a question in our own window; «Заменить» → the same body with replace: true", async () => {
    stub.routes.push((m, p, b) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? (b.replace === true ? answer(CALC.replace) : answer(CALC.exists)) : undefined));
    const run = app.registry.execute("blocks.calc", args("replace"), "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Заменить поле|Поле «AU2» в модели «Месторождение» уже есть. Заменить его новым? Прежние значения поля пропадут, единица и описание не переносятся.");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    const sent = posts(/\/calc$/);
    expect(sent.map((x) => x.body.replace)).toEqual([undefined, true]);
    expect(canon(sent[1].body)).toBe(canon({ ...CALC.replace.request, expected_version: stub.version }));
    expect(currentRun().phase).toBe("done");
    expect(r.result).toMatch(/^Поле «AU2» записано/);
  });

  it("«Не заменять»: nothing is written, the field is left as it is, the run is told as cancelled", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC.exists) : undefined));
    const run = app.registry.execute("blocks.calc", args("exists"), "user");
    await answerDialog(false);
    const r = await run;
    expect(r).toEqual({ ok: true, result: "Поле «AU2» оставлено без изменений" });
    expect(posts(/\/calc$/)).toHaveLength(1);
    expect(currentRun().phase).toBe("cancelled");
    expect(dialogStore.get().queue).toEqual([]);
  });

  it("the assistant is not asked to replace: it is told to pass replace or change the name; the field stands under «Имя»", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC.exists) : undefined));
    const run = app.registry.execute("blocks.calc", args("exists"), "claude");
    await answerClaude(true);
    const r = await run;
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${CALC.exists.body!.message} Задайте другое имя или передайте replace: true, чтобы заменить поле.`);
    expect(posts(/\/calc$/)).toHaveLength(1);
    expect(fieldsStore.get().issue).toMatchObject({ tab: "calc", place: "field" });
    expect(dialogStore.get().queue).toEqual([]);
    // with replace: true it is replaced without a question
    stub.sent.length = 0;
    stub.routes = [(m, p, b) => (m === "POST" && p === `/blocks/${IDS.model}/calc` && b.replace === true ? answer(CALC.replace) : undefined)];
    const again = app.registry.execute("blocks.calc", { ...args("replace"), replace: true }, "claude");
    await answerClaude(true);
    expect((await again).ok).toBe(true);
    expect(posts(/\/calc$/).map((x) => x.body.replace)).toEqual([true]);
    expect(dialogStore.get().queue).toEqual([]);
  });

  it("the errors of the expression: the place goes to the window, the words are the core's; nothing is written", async () => {
    const cases: [string, string, string][] = [
      ["bad_syntax", "expr_syntax", "AU +"],
      ["bad_field", "expr_field", "AU + NOPE"],
      ["bad_type", "expr_type", "AU * 2"],
    ];
    for (const [name, code, expr] of cases) {
      stub.routes = [(m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? answer(CALC[name]) : undefined)];
      const r = await app.registry.execute("blocks.calc", { model_id: IDS.model, expr, field: "AU_C", ...(name === "bad_type" ? { dtype: "cat" } : {}) }, "user");
      const e = CALC[name].job ? CALC[name].job!.error : CALC[name].body!;
      expect(r.ok, name).toBe(false);
      expect(r.result).toBe(`${e.message} ${e.hint}`);
      expect(fieldsStore.get().check).toMatchObject({ phase: "error", issue: { code, pos: e.pos, line: e.line, column: e.column } });
      expect(currentRun().phase).toBe("failed");
    }
  });

  it("the other refusals of the core stand in their place: the filter, the restrictor, the type, the name, the outside", async () => {
    const PLACE: Record<string, string> = { bad_restrict_field: "restrict", bad_filter: "filter", bad_outside: "outside", bad_name: "field", bad_dtype: "dtype", model_missing: "model" };
    for (const [name, place] of Object.entries(PLACE)) {
      const c = CALC[name];
      const e = (c.body ?? c.job!.error) as Record<string, string>;
      expect(e.message, name).toMatch(/[А-Яа-яЁё]/);
      expect(e.hint, name).not.toBe("");
      stub.routes = [(m, p) => (m === "POST" && p.endsWith("/calc") ? json(c.body, c.status) : undefined)];
      const r = await app.registry.execute("blocks.calc", { model_id: IDS.model, expr: "AU", field: "AU_C", ...(name === "bad_restrict_field" ? { restrict: JSON.stringify({ cond: { field: "ZZZ", op: ">", value: 1 } }) } : {}) }, "user");
      expect(r.ok, name).toBe(false);
      expect(r.result).toBe(`${e.message} ${e.hint}`);
      expect(fieldsStore.get().issue, name).toMatchObject({ tab: "calc", place: name === "bad_restrict_field" ? "restrict" : place, message: e.message });
    }
  });

  it("the model changed elsewhere (409 version_conflict): the list is read again and the person is told to repeat", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? json(CALC.conflict.body, CALC.conflict.status) : undefined));
    const r = await app.registry.execute("blocks.calc", args("new"), "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${CALC.conflict.body!.message} ${CALC.conflict.body!.hint} Данные модели обновлены — повторите действие.`);
    expect(stub.sent.filter((x) => x.method === "GET" && x.path === "/blocks").length).toBeGreaterThan(0);
    expect(fieldsStore.get().issue).toMatchObject({ place: "general" });
  });

  it("a project open for reading: the core's 423 is told; the check works", async () => {
    const ro = A.read_only;
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("blocks.calc", args("new"), "user");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/только для чтения/);
    expect(posts(/\/calc$/)).toEqual([]);
    app.store.set({ projectReadOnly: false });
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? json(ro.calc.body, ro.calc.status) : undefined));
    const late = await app.registry.execute("blocks.calc", args("new"), "user");
    expect(late.result).toBe(`${ro.calc.body.message} ${ro.calc.body.hint}`);
    app.store.set({ projectReadOnly: true });
    const check = await app.registry.execute("blocks.calc.check", { model_id: IDS.model, expr: "AU * 2 + CU" }, "claude");
    expect(check.ok, check.result).toBe(true);
  });

  it("a wrong request never reaches the core; the model that is not there is told", async () => {
    const run = app.registry.execute("blocks.calc", { model_id: IDS.model, expr: "AU", field: "idx" }, "claude");
    await answerClaude(true); // the confirmation of the assistant
    const r = await run;
    expect(r.ok).toBe(false);
    expect(fieldsStore.get().issue).toMatchObject({ tab: "calc", place: "field" });
    const gone = app.registry.execute("blocks.calc", { model_id: 999, expr: "AU", field: "X" }, "claude");
    await answerClaude(true);
    expect((await gone).result).toBe("Блочная модель №999 не найдена. Номера моделей — в разделе «Блочные модели» дерева.");
    expect(posts(/\/calc$/)).toEqual([]);
  });

  it("«Проверить» (blocks.calc.check): the kind of the result and the canonical text, or the place; nothing is written", async () => {
    const ok = await app.registry.execute("blocks.calc.check", { model_id: IDS.model, expr: "[AU] + 1" }, "claude");
    expect(ok).toEqual({ ok: true, result: "Выражение верно: результат — число, тип поля f8; читает поля: AU; запись: AU + 1" });
    const bad = await app.registry.execute("blocks.calc.check", { model_id: IDS.model, expr: "AU +" }, "claude");
    expect(bad.ok).toBe(false);
    expect(bad.result).toBe(`${CHECK.syntax.body.message} ${CHECK.syntax.body.hint}`);
    expect(windowOfFields()).toBeNull();
    const typed = await app.registry.execute("blocks.calc.check", { model_id: IDS.model, expr: "AU * 2", dtype: "cat" }, "user");
    expect(typed.result).toBe(`${CHECK.dtype_cat_from_number.body.message} ${CHECK.dtype_cat_from_number.body.hint}`);
    expect(windowOfFields()).not.toBeNull(); // the window of the user shows the place
    expect(posts(/\/calc$/)).toEqual([]);
    const syntax = await app.registry.execute("blocks.calc.check", { model_id: IDS.model, expr: "" }, "claude");
    expect(syntax.ok).toBe(false);
  });

  it("the job that is cancelled leaves nothing: «Отменить» → the job is cancelled, the run is told so", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/calc` ? startJob(CALC.new as { job: Record<string, any> }, "running") : undefined));
    const run = app.registry.execute("blocks.calc", args("new"), "user");
    await vi.waitFor(() => expect(currentRun().job).not.toBeNull());
    expect(currentRun().phase).toBe("running");
    const busy = await app.registry.execute("blocks.calc", args("category"), "user");
    expect(busy.result).toBe("Поля уже считаются. Дождитесь конца или отмените расчёт в окне «Поля блочной модели».");
    await cancelRun();
    const r = await run;
    expect(stub.sent.some((x) => x.method === "POST" && /^\/jobs\/proj\/\d+\/cancel$/.test(x.path))).toBe(true);
    expect(r).toEqual({ ok: true, result: "Расчёт поля «AU2» отменён: в модель «Месторождение» ничего не записано" });
    expect(currentRun().phase).toBe("cancelled");
    expect(currentRun().result).toBeNull();
  });
});
