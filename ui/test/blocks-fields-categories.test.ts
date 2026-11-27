// The categories of a block model, the schemes of the project and the commands `blocks.classify`, `blocks.scheme.save` (ТЗ 8.7.5;
// ADR 0023 §9, decision 13, № 68; Э17 17.36) on the REAL answers of the core (`core/tests/data/blocks/fields_window_answers.json`,
// made by `core/tests/test_blocks_fields_window_answers.py` through `POST /blocks/{id}/classify`, `GET/PUT /blocks/schemes` and
// the job `blocks.classify`): the request of the form = the body the core got (the scheme, or the rules when the table was
// edited); the scheme saved over its version, 409 asked in our own window, 423 told; the rules of a scheme checked as
// expressions; the refusals in Russian in their place (a rule of the table is marked by its `param`).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { answerClaude } from "./claude-confirm";
import { readResult, readScheme, type Scheme } from "../src/modules/blocks/fields/api";
import { openFieldsWindow, windowOfFields } from "../src/modules/blocks/fields/bind";
import { CalcWindow } from "../src/modules/blocks/fields/CalcWindow";
import { argsOfForm, EMPTY_CLASSIFY, formOfArgs, pickScheme, requestOfArgs, rowsOfScheme, sameRules, schemeArgsOfForm, schemeRequestOfArgs, type ClassifyForm } from "../src/modules/blocks/fields/classifyForm";
import { EMPTY_RESTRICT } from "../src/modules/blocks/fields/common";
import { checkRules, refreshSchemes } from "../src/modules/blocks/fields/opsBind";
import { currentRun, fieldsStore, setClassify } from "../src/modules/blocks/fields/state";
import { summaryText } from "../src/modules/blocks/fields/text";
import { sceneObjects } from "../src/modules/scene/objects";
import { A, answer, answerDialog, canon, IDS, install, json, posts, stub, uninstall, without } from "./blocks-fields-kit";

type Case = { request: Record<string, unknown>; status: number; body?: Record<string, any>; job?: Record<string, any> };
const K = A.classify as Record<string, Case>;
const S = A.schemes as Record<string, any>;
const CHECK = A.check as Record<string, Case>;
const draftCond = (field: string, op: ">" | "=", value: string) => ({ cond: { field, op, value, list: "" } }) as const;
const QUICK = { ...EMPTY_RESTRICT, restrictOn: true, restrict: draftCond("CU", ">", "0.5") };

const schemesOf = (body: { schemes: unknown[] }): Scheme[] => body.schemes.map((s) => readScheme(s)!);
const BEFORE = schemesOf(S.list.body);
const AFTER = schemesOf(S.list_after.body);
const RULES = [
  { label: "Руда", expr: "AU >= 1" },
  { label: "Бедная", expr: "AU >= 0" },
];

/** What the interface leaves out of the body when it has nothing to say (the core reads a missing key and `null`/0/[] the same). */
const compact = (v: unknown): unknown => {
  if (Array.isArray(v)) return v.length ? v.map(compact) : undefined;
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, compact(x)]).filter(([k, x]) => x !== null && x !== undefined && !(k === "majority" && x === 0)));
  return v;
};

let app: App;
beforeEach(() => {
  app = install();
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [{ id: IDS.wireframe, name: "Зона богатых", dtm: null } as never], loaded: true });
});
afterEach(() => uninstall());

const FORMS: Record<string, Partial<ClassifyForm>> = {
  jorc: { schemeId: "jorc", estimate: "AU", out: "CLASS" },
  jorc_majority: { schemeId: "jorc", estimate: "AU", out: "CLASS_M", majority: "1" },
  custom: { schemeId: "my", out: "K", r: { ...EMPTY_RESTRICT, filterId: IDS.filter } },
  rules: { schemeId: "", rules: RULES, estimate: "AU", out: "K2", r: QUICK },
  overrides: { schemeId: "", rules: RULES, out: "K3", overrides: [{ wireframeId: IDS.wireframe, label: "Бедная" }] },
};
const formOf = (name: string): ClassifyForm => {
  const f = { ...EMPTY_CLASSIFY, ...FORMS[name] };
  return f.schemeId && f.rules.length === 0 ? pickScheme(f, AFTER, f.schemeId) : f;
};

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the arguments → the body of the recorded request`, () => {
      const got = argsOfForm(IDS.model, formOf(name), AFTER);
      expect(got.ok, JSON.stringify(got)).toBe(true);
      if (!got.ok) return;
      const req = requestOfArgs(got.args);
      expect(req.ok, JSON.stringify(req)).toBe(true);
      if (!req.ok) return;
      expect(canon(compact(req.request.body))).toBe(canon(compact(without(K[name].request, "expected_version"))));
    });
    it(`${name}: the arguments of a call show in the form and come back as the same arguments`, () => {
      const got = argsOfForm(IDS.model, formOf(name), AFTER);
      if (!got.ok) throw new Error("form");
      const again = argsOfForm(IDS.model, formOfArgs(got.args, AFTER), AFTER);
      if (name === "rules" || name === "overrides") {
        // rules that are exactly the scheme «my» show as that scheme (the same request: the core reads the rules of the scheme)
        expect(again.ok && again.args).toMatchObject({ ...without(got.args as Record<string, unknown>, "rules"), scheme: "my" });
        expect(again.ok && "rules" in again.args).toBe(false);
        return;
      }
      expect(again).toEqual(got);
    });
  }

  it("the table is the scheme's until it is edited: the scheme is named; edited — the rules themselves go (exactly one of them)", () => {
    const f = formOf("jorc");
    const same = argsOfForm(IDS.model, f, AFTER);
    expect(same.ok && same.args).toMatchObject({ scheme: "jorc" });
    expect(same.ok && "rules" in same.args).toBe(false);
    const edited = { ...f, rules: f.rules.map((r, i) => (i === 0 ? { ...r, expr: "PASS = 1" } : r)) };
    expect(sameRules(edited.rules, AFTER.find((s) => s.id === "jorc"))).toBe(false);
    const got = argsOfForm(IDS.model, edited, AFTER);
    expect(got.ok && "scheme" in got.args).toBe(false);
    expect(got.ok && JSON.parse(got.args.rules as string)[0]).toEqual({ label: "Измеренные", expr: "PASS = 1" });
  });

  it("the template ГКЗ has no thresholds: its rules are empty in the table, the window says so, the core refuses with the row", () => {
    const gkz = AFTER.find((s) => s.id === "gkz")!;
    expect(gkz.rules.map((r) => r.expr)).toEqual([null, null, null, null]);
    expect(rowsOfScheme(gkz).map((r) => r.expr)).toEqual(["", "", "", ""]);
    const got = argsOfForm(IDS.model, pickScheme(EMPTY_CLASSIFY, AFTER, "gkz"), AFTER);
    expect(got.ok && got.args.scheme).toBe("gkz"); // the scheme as it is: the core says «порог не задан» with the place
    const own = argsOfForm(IDS.model, { ...pickScheme(EMPTY_CLASSIFY, AFTER, "gkz"), rules: [{ label: "A", expr: "" }] }, AFTER);
    expect(own.ok).toBe(false);
    if (!own.ok) expect(own.issue).toMatchObject({ tab: "classify", place: "rules", param: "rules.0.expr" });
  });

  it("a request that is not right is told in Russian, with a hint and the place, before the core is asked", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = requestOfArgs(a);
      expect(r.ok, JSON.stringify(a)).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(r.issue.tab).toBe("classify");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return [r.issue.place, r.issue.param];
    };
    const m = { model_id: IDS.model };
    const rules = JSON.stringify(RULES);
    expect(bad({})).toEqual(["model", null]);
    expect(bad({ ...m })).toEqual(["rules", null]);
    expect(bad({ ...m, scheme: "jorc", rules })).toEqual(["rules", null]);
    expect(bad({ ...m, rules: "{не список" })).toEqual(["rules", "rules"]);
    expect(bad({ ...m, rules: "[]" })).toEqual(["rules", "rules"]);
    expect(bad({ ...m, rules: JSON.stringify([{ label: "", expr: "AU > 1" }]) })).toEqual(["rules", "rules.0.label"]);
    expect(bad({ ...m, rules: JSON.stringify([{ label: "A", expr: "AU > 1" }, { label: "B", expr: " " }]) })).toEqual(["rules", "rules.1.expr"]);
    expect(bad({ ...m, rules: JSON.stringify([{ label: "A", expr: "AU > 1" }, { label: "a", expr: "AU > 0" }]) })).toEqual(["rules", "rules.1.label"]);
    expect(bad({ ...m, scheme: "jorc", majority: 11 })).toEqual(["majority", null]);
    expect(bad({ ...m, scheme: "jorc", majority: "один" })).toEqual(["majority", null]);
    expect(bad({ ...m, scheme: "jorc", out: "idx" })).toEqual(["out", null]);
    expect(bad({ ...m, scheme: "jorc", overrides: "{нет" })).toEqual(["overrides", "overrides"]);
    expect(bad({ ...m, scheme: "jorc", overrides: JSON.stringify([{ label: "Руда" }]) })).toEqual(["overrides", "overrides.0.wireframe_id"]);
    expect(bad({ ...m, scheme: "jorc", overrides: JSON.stringify([{ wireframe_id: 3, label: " " }]) })).toEqual(["overrides", "overrides.0.label"]);
    expect(bad({ ...m, scheme: "jorc", filter_id: 0.5 })).toEqual(["filter", null]);
    expect(bad({ ...m, scheme: "jorc", restrict: "[1]" })).toEqual(["restrict", null]);
    expect(stub.sent.filter((r) => r.method !== "GET")).toEqual([]);
    // the window: no model, no rules, an unfinished override, a number of passes that is not one
    const f = (patch: Partial<ClassifyForm>) => argsOfForm(IDS.model, { ...formOf("rules"), ...patch }, AFTER);
    const place = (r: ReturnType<typeof f>) => (r.ok ? "ok" : [r.issue.place, r.issue.param]);
    expect(argsOfForm(null, EMPTY_CLASSIFY, AFTER).ok).toBe(false);
    expect(place(f({ rules: [] }))).toEqual(["rules", null]);
    expect(place(f({ rules: [{ label: "A", expr: "" }] }))).toEqual(["rules", "rules.0.expr"]);
    expect(place(f({ overrides: [{ wireframeId: null, label: "Руда" }] }))).toEqual(["overrides", "overrides.0.wireframe_id"]);
    expect(place(f({ overrides: [{ wireframeId: 3, label: "" }] }))).toEqual(["overrides", "overrides.0.label"]);
    expect(place(f({ majority: "11" }))).toEqual(["majority", null]);
    expect(place(f({ majority: "1,5" }))).toEqual(["majority", null]);
    expect(place(f({ out: " " }))).toEqual(["out", null]);
  });
});

describe("the schemes of the project", () => {
  it("are read as the core wrote them: JORC and ГКЗ first, the user's own after; the version of each", () => {
    expect(BEFORE.map((s) => s.id)).toEqual(["jorc", "gkz"]);
    expect(AFTER.map((s) => s.id)).toEqual(["jorc", "gkz", "my"]);
    expect(BEFORE[0]).toMatchObject({ name: "JORC", version: 1 });
    expect(BEFORE[0].rules.map((r) => r.label)).toEqual(["Измеренные", "Выявленные", "Предполагаемые"]);
    expect(BEFORE[0].rules[2].expr).toBe("1 = 1"); // «оценка есть» is asked for by the field of the estimate
    expect(BEFORE[1].rules.map((r) => r.label)).toEqual(["A", "B", "C1", "C2"]);
    expect(readScheme({ id: 3 })).toBeNull();
    expect(readScheme(null)).toBeNull();
  });

  it("the editor of the window → the arguments of `blocks.scheme.save` → the body the core got; a new scheme goes without a version", () => {
    const f = { ...EMPTY_CLASSIFY, rules: RULES, editId: "my", editName: "Моя" };
    const got = schemeArgsOfForm(f, BEFORE);
    expect(got.ok).toBe(true);
    if (!got.ok) return;
    expect("expected_version" in got.args).toBe(false);
    const req = schemeRequestOfArgs(got.args, BEFORE);
    expect(req.ok && canon({ name: req.request.name, rules: req.request.rules })).toBe(canon(S.put_new.request));
    expect(req.ok && req.request.version).toBeUndefined();
  });

  it("an existing scheme (JORC too) is written over its version; the changed rule is the one the core got", () => {
    const jorc = BEFORE[0];
    const rows = rowsOfScheme(jorc).map((r, i) => (i === 0 ? { ...r, expr: "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 30" } : r));
    const got = schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: rows, editId: "jorc", editName: "JORC" }, BEFORE);
    expect(got.ok && got.args.expected_version).toBe(1);
    if (!got.ok) return;
    const req = schemeRequestOfArgs(got.args, BEFORE);
    expect(req.ok && canon({ expected_version: req.request.version, name: req.request.name, rules: req.request.rules })).toBe(canon(S.put_jorc_changed.request));
  });

  it("the template ГКЗ is saved with the thresholds not set (null), not with an empty text", () => {
    const gkz = BEFORE[1];
    const got = schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: rowsOfScheme(gkz), editId: "gkz", editName: "ГКЗ" }, BEFORE);
    if (!got.ok) throw new Error("form");
    expect(JSON.parse(got.args.rules as string)).toEqual(gkz.rules);
    expect(schemeRequestOfArgs(got.args, BEFORE)).toMatchObject({ ok: true, request: { rules: gkz.rules, version: 1 } });
  });

  it("a scheme that is not right is told in Russian, with a hint and the place, before the core is asked", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = schemeRequestOfArgs(a, BEFORE);
      expect(r.ok, JSON.stringify(a)).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return [r.issue.place, r.issue.param];
    };
    const ok = { id: "my", name: "Моя", rules: JSON.stringify(RULES) };
    expect(bad({})).toEqual(["scheme_id", null]);
    expect(bad({ ...ok, id: "моя схема" })).toEqual(["scheme_id", null]);
    expect(bad({ ...ok, id: "x".repeat(41) })).toEqual(["scheme_id", null]);
    expect(bad({ ...ok, name: " " })).toEqual(["scheme_name", null]);
    expect(bad({ ...ok, rules: "[]" })).toEqual(["rules", "rules"]);
    expect(bad({ ...ok, rules: "{" })).toEqual(["rules", "rules"]);
    expect(bad({ ...ok, rules: JSON.stringify([{ label: " ", expr: "AU > 1" }]) })).toEqual(["rules", "rules.0.label"]);
    const f = (patch: Partial<ClassifyForm>) => schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: RULES, editId: "my", editName: "Моя", ...patch }, BEFORE);
    const cases: [Partial<ClassifyForm>, string][] = [[{ editId: "" }, "scheme_id"], [{ editName: " " }, "scheme_name"], [{ rules: [] }, "rules"], [{ rules: [{ label: "", expr: "" }] }, "rules"]];
    for (const [patch, place] of cases) {
      const r = f(patch);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.issue.place).toBe(place);
    }
  });
});

describe("the command `blocks.classify`", () => {
  const args = (name: string) => {
    const got = argsOfForm(IDS.model, formOf(name), AFTER);
    if (!got.ok) throw new Error("form");
    return got.args;
  };
  const route = (c: Case) => stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/classify` ? answer(c as never) : undefined));
  beforeEach(() => {
    stub.schemes = S.list_after.body;
  });

  it("is in the registry: a Russian title and a description for the assistant; confirm; without model_id and a scheme the window opens", async () => {
    const c = app.registry.get("blocks.classify")!;
    expect(c.run).toBeTypeOf("function");
    expect(c.confirm).toBe(true);
    expect(c.title).toBe("Категории блоков");
    expect(c.description).toMatch(/Ровно одно из scheme/);
    expect(c.description).toMatch(/шаблон A, B, C1, C2 без порогов/);
    expect(Object.keys(c.params!)).toEqual(["model_id", "scheme", "rules", "estimate", "prefix", "overrides", "majority", "out", "filter_id", "restrict", "replace"]);
    expect(app.registry.get("blocks.scheme.save")!.confirm).toBe(true);
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    const r = await app.registry.execute("blocks.classify", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: категории" });
    expect(fieldsStore.get().tab).toBe("classify");
    expect(windowOfFields()).not.toBeNull();
    await new Promise((r2) => setTimeout(r2, 10));
    expect(fieldsStore.get().schemes.list.map((s) => s.id)).toEqual(["jorc", "gkz", "my"]); // the schemes are read when the tab opens
    expect(fieldsStore.get().classify.rules.length).toBe(3); // the rules of the chosen scheme are the table
    expect(posts(/classify$/)).toEqual([]);
  });

  for (const name of ["jorc", "jorc_majority", "custom", "rules", "overrides"]) {
    it(`${name}: the body, the job, the counts as the core wrote them, the line for the log`, async () => {
      route(K[name]);
      const r = await app.registry.execute("blocks.classify", args(name), "user");
      expect(r.ok, r.result).toBe(true);
      const sent = posts(/classify$/);
      expect(sent).toHaveLength(1);
      expect(sent[0].body.expected_version).toBe(stub.version);
      expect(canon(compact(without(sent[0].body, "expected_version")))).toBe(canon(compact(without(K[name].request, "expected_version"))));
      const res = readResult(K[name].job!.result)!;
      expect(currentRun().result).toEqual(res);
      expect(res.counts).not.toBeNull();
      expect(res.unclassified! + res.noEstimate! + Object.values(res.counts!).reduce((a, b) => a + b, 0)).toBe(res.passed); // outside the restrictor a block is not counted
      expect(r.result).toBe(summaryText("classify", res, "Месторождение"));
    });
  }

  it("a block with no estimate gets no category and is counted apart; a block with an estimate and no fitting rule is counted apart", () => {
    const j = readResult(K.jorc.job!.result)!;
    expect(j).toMatchObject({ field: "CLASS", rows: 24, noEstimate: 2, unclassified: 0 });
    expect(j.counts).toEqual({ Измеренные: 1, Выявленные: 5, Предполагаемые: 16 });
    expect(j.stats).toMatchObject({ count: 22, missing: 2 });
    const c = readResult(K.custom.job!.result)!;
    expect(c.unclassified).toBe(1); // the estimate is there, no rule fits
    expect(summaryText("classify", j, "М")).toContain("без оценки: 2");
    expect(summaryText("classify", j, "М")).toContain("Измеренные — 1; Выявленные — 5; Предполагаемые — 16");
  });

  it("the window shows the scheme, the rules, the counts of the run; the form of the call is what was asked", async () => {
    route(K.overrides);
    await app.registry.execute("blocks.classify", args("overrides"), "user");
    expect(fieldsStore.get().classify).toMatchObject({ schemeId: "my", out: "K3", overrides: [{ wireframeId: IDS.wireframe, label: "Бедная" }] });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    for (const id of ["classify-panel", "classify-scheme", "classify-rules", "classify-rule-0-label", "classify-rule-1-expr", "classify-estimate", "classify-majority", "classify-over-0-wireframe", "classify-restrict-filter", "classify-out", "classify-edit-id", "classify-save-scheme", "classify-build", "fields-result", "fields-result-unclassified", "fields-result-counts"]) expect(html, id).toContain(`data-testid="${id}"`);
    expect(html).toContain("Свои правила (не сохранены)");
    expect(html).toContain(`JORC (jorc, версия ${AFTER[0].version})`);
    expect(html).toContain("Зона богатых");
  });

  it("the table edited: the window says the run goes by the rules, not by the scheme of the project", async () => {
    openFieldsWindow(app, "classify", IDS.model);
    await refreshSchemes();
    setClassify(pickScheme(fieldsStore.get().classify, fieldsStore.get().schemes.list, "jorc"));
    const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html()).toContain("Первое выполненное правило задаёт категорию блока.");
    setClassify({ rules: fieldsStore.get().classify.rules.map((r, i) => (i === 0 ? { ...r, expr: "PASS = 1" } : r)) });
    expect(html()).toContain("Правила изменены: расчёт пойдёт по ним, а не по схеме проекта");
  });

  it("fields that are there: a question in our own window; «Заменить» → the same body with replace: true", async () => {
    stub.routes.push((m, p, b) => (m === "POST" && p === `/blocks/${IDS.model}/classify` ? (b.replace === true ? answer(K.rules as never) : answer(K.exists as never)) : undefined));
    const run = app.registry.execute("blocks.classify", args("rules"), "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Заменить поле|Поле «K2» в модели «Месторождение» уже есть. Заменить его новым? Прежние значения поля пропадут, единица и описание не переносятся.");
    expect((await run).ok).toBe(true);
    expect(posts(/classify$/).map((x) => x.body.replace)).toEqual([undefined, true]);
    expect(dialogStore.get().queue).toEqual([]);
  });

  const PLACE: Record<string, [string, string | null]> = {
    gkz: ["rules", "rules.0.expr"],
    scheme_missing: ["scheme", null],
    neither: ["rules", "rules"],
    both: ["rules", "rules"],
    estimate_missing: ["estimate", "estimate"],
    rule_field_missing: ["rules", "rules.0.expr"],
    override_label: ["overrides", "overrides.0.label"],
    override_wireframe: ["overrides", "overrides.0.wireframe_id"],
    majority_bad: ["majority", "majority"],
  };
  for (const [name, [place, param]] of Object.entries(PLACE)) {
    it(`${name}: the words and the hint of the core, under «${place}»`, async () => {
      const c = K[name];
      const e = c.body!;
      expect([404, 422]).toContain(c.status);
      expect(e.message).toMatch(/[А-Яа-яЁё]/);
      expect(e.hint).not.toBe("");
      stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/classify` ? json(c.body, c.status) : undefined));
      const r = await app.registry.execute("blocks.classify", args("custom"), "user");
      expect(r.ok).toBe(false);
      expect(r.result).toBe(`${e.message} ${e.hint}`);
      const issue = fieldsStore.get().issue!;
      expect(issue).toMatchObject({ tab: "classify", place, message: e.message });
      expect(issue.param).toBe(param);
    });
  }

  it("the template ГКЗ: the refusal of the core marks the rule of the table; the window shows the words under it", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/classify` ? json(K.gkz.body, K.gkz.status) : undefined));
    await app.registry.execute("blocks.classify", { model_id: IDS.model, scheme: "gkz" }, "user");
    expect(fieldsStore.get().classify.schemeId).toBe("gkz");
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toMatch(/aria-invalid="true"[^>]*data-testid="classify-rule-0-expr"/);
    expect(html).toContain('data-testid="classify-rule-0-issue"');
    expect(html).toContain(K.gkz.body!.message);
    expect(html).toContain("порог не задан");
  });

  it("the rules of the table are checked as expressions: the words of the core under the row, the empty condition told", async () => {
    stub.routes.push((m, p, b) => {
      if (m !== "POST" || p !== "/blocks/calc/check") return undefined;
      const hit = Object.values(CHECK).find((c) => canon(c.request) === canon(b));
      return hit ? json(hit.body, hit.status) : undefined;
    });
    openFieldsWindow(app, "classify", IDS.model);
    setClassify({ schemeId: "", rules: [{ label: "A", expr: "AU >= 1" }, { label: "B", expr: "NOPE >= 1" }, { label: "C", expr: "" }] });
    await checkRules();
    const c = fieldsStore.get().rulesCheck;
    expect(c[0]).toBeNull();
    expect(c[1]).toBe(`${CHECK.rule_field.body!.message} ${CHECK.rule_field.body!.hint}`);
    expect(c[2]).toBe("Условие не задано.");
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toContain('data-testid="classify-rule-1-check"');
    expect(html).not.toContain('data-testid="classify-rule-0-check"');
    expect(html).toContain("Неизвестное поле «NOPE»");
    // the first rule of JORC itself is an expression of 8.7.4 that this model can read
    setClassify({ rules: [{ label: "Измеренные", expr: "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 25" }] });
    await checkRules();
    expect(fieldsStore.get().rulesCheck).toEqual({ 0: null });
  });
});

describe("the command `blocks.scheme.save`", () => {
  const put = (id: string, c: { status: number; body: any }, when?: (b: Record<string, unknown>) => boolean) =>
    stub.routes.push((m, p, b) => (m === "PUT" && p === `/blocks/schemes/${id}` && (!when || when(b)) ? json(c.body, c.status) : undefined));
  beforeEach(async () => {
    stub.schemes = S.list.body;
    await refreshSchemes();
  });

  it("a new scheme: the body of the recorded request, the line for the log, the list read again, the table is the saved scheme", async () => {
    put("my", S.put_new);
    stub.routes.push((m, p) => (m === "GET" && p === "/blocks/schemes" && stub.sent.some((x) => x.method === "PUT") ? json(S.list_after.body) : undefined));
    setClassify({ rules: RULES, editId: "my", editName: "Моя", schemeId: "" });
    const got = schemeArgsOfForm(fieldsStore.get().classify, BEFORE);
    if (!got.ok) throw new Error("form");
    const r = await app.registry.execute("blocks.scheme.save", got.args, "user");
    expect(r, r.result).toEqual({ ok: true, result: "Схема «Моя» (my) сохранена: правил 2, версия 1" });
    const sent = stub.sent.filter((x) => x.method === "PUT");
    expect(sent).toHaveLength(1);
    expect(canon(sent[0].body)).toBe(canon(S.put_new.request));
    expect(fieldsStore.get().schemes.list.map((s) => s.id)).toEqual(["jorc", "gkz", "my"]);
    expect(fieldsStore.get().classify.schemeId).toBe("my");
  });

  it("an existing scheme is written over the version read; 409 is asked in our own window: «Записать поверх» → the version of the core", async () => {
    put("jorc", S.put_conflict, (b) => b.expected_version === 1);
    put("jorc", S.put_jorc_changed, (b) => b.expected_version === S.put_conflict.body.version);
    const got = schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: rowsOfScheme(BEFORE[0]).map((r, i) => (i === 0 ? { ...r, expr: "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 30" } : r)), editId: "jorc", editName: "JORC" }, BEFORE);
    if (!got.ok) throw new Error("form");
    const run = app.registry.execute("blocks.scheme.save", got.args, "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Схема изменена в другом окне|Схему «JORC» изменили в другом окне, пока вы её правили. Записать правила этого окна поверх изменений? Иначе схемы будут перечитаны, а правила этого окна останутся в таблице.");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    expect(stub.sent.filter((x) => x.method === "PUT").map((x) => x.body.expected_version)).toEqual([1, S.put_conflict.body.version]);
    expect(r.result).toBe(`Схема «JORC» (jorc) сохранена: правил 3, версия ${S.put_jorc_changed.body.version}`);
  });

  it("409 and «Не записывать»: nothing is written, the schemes are read again, the words of the core say so", async () => {
    put("jorc", S.put_conflict);
    const got = schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: rowsOfScheme(BEFORE[0]), editId: "jorc", editName: "JORC" }, BEFORE);
    if (!got.ok) throw new Error("form");
    const run = app.registry.execute("blocks.scheme.save", got.args, "user");
    await answerDialog(false);
    const r = await run;
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${S.put_conflict.body.message} ${S.put_conflict.body.hint} Схемы перечитаны.`);
    expect(stub.sent.filter((x) => x.method === "PUT")).toHaveLength(1);
    expect(stub.sent.filter((x) => x.method === "GET" && x.path === "/blocks/schemes").length).toBeGreaterThan(1);
    // the assistant is not asked: it is told and repeats over the new version
    stub.sent.length = 0;
    const claude = app.registry.execute("blocks.scheme.save", got.args, "claude");
    await answerClaude(true); // the confirmation of the assistant
    expect((await claude).ok).toBe(false);
    expect(dialogStore.get().queue).toEqual([]);
  });

  const BAD: Record<string, [string, string | null]> = {
    syntax: ["rules", "rules.0.expr"],
    label_twice: ["rules", "rules.1.label"],
  };
  for (const [name, [place, param]] of Object.entries(BAD)) {
    it(`${name}: the words of the core under «${place}»`, async () => {
      const c = S[name];
      expect(c.status).toBe(422);
      put(c.id, c);
      const rules = JSON.stringify(c.request.rules);
      const r = await app.registry.execute("blocks.scheme.save", { id: c.id, name: c.request.name, rules }, "user");
      expect(r.ok).toBe(false);
      expect(r.result).toBe(`${c.body.message} ${c.body.hint}`);
      const issue = fieldsStore.get().issue!;
      expect(issue.tab).toBe("classify");
      expect(issue).toMatchObject({ place, param, message: c.body.message });
    });
  }

  it("the refusals of the core with a text of their own: the id, the version, the rules", () => {
    for (const name of ["bad_id", "no_rules", "no_version"]) {
      const c = S[name];
      expect([409, 422]).toContain(c.status);
      expect(c.body.message).toMatch(/[А-Яа-яЁё]/);
      expect(c.body.hint).not.toBe("");
    }
    expect(S.bad_id.body.param).toBe("id");
    expect(S.no_version.body.code).toBe("version_conflict"); // an existing scheme without a version: the interface always sends one
  });

  it("a project open for reading: told before the core is asked; the core's 423 is told when the project turns read-only meanwhile", async () => {
    const got = schemeArgsOfForm({ ...EMPTY_CLASSIFY, rules: RULES, editId: "my", editName: "Моя" }, BEFORE);
    if (!got.ok) throw new Error("form");
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("blocks.scheme.save", got.args, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/только для чтения/);
    expect(stub.sent.filter((x) => x.method === "PUT")).toEqual([]);
    app.store.set({ projectReadOnly: false });
    put("my", A.read_only.scheme);
    const late = await app.registry.execute("blocks.scheme.save", got.args, "user");
    expect(late.result).toBe(`${A.read_only.scheme.body.message} ${A.read_only.scheme.body.hint}`);
    expect(A.read_only.schemes.status).toBe(200); // the list is read in a project open for reading
  });

  it("without id and rules the window opens on the categories", async () => {
    const r = await app.registry.execute("blocks.scheme.save", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: категории и схемы" });
    expect(fieldsStore.get().tab).toBe("classify");
  });
});
