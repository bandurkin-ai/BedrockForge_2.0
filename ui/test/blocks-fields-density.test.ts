// The density of a block model and the command `blocks.density` (ТЗ 8.7.5; ADR 0023 §9, decision 13; Э17 17.36) on the REAL
// answers of the core (`core/tests/data/blocks/fields_window_answers.json`, made by `core/tests/test_blocks_fields_window_answers.py`
// through `POST /blocks/{id}/density` and the job `blocks.density`): the request of the form = the body the core got; the
// result read as the core wrote it (a gap — not a zero); the refusals 404 / 409 / 422 in Russian in their place of the form
// (a row of the table is marked by its `param`); the replace of the fields after a question in our own window.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { readResult } from "../src/modules/blocks/fields/api";
import { chooseModel, openFieldsWindow, windowOfFields } from "../src/modules/blocks/fields/bind";
import { CalcWindow } from "../src/modules/blocks/fields/CalcWindow";
import { EMPTY_RESTRICT } from "../src/modules/blocks/fields/common";
import { argsOfForm, EMPTY_DENSITY, formOfArgs, requestOfArgs, rowsForLabels, type DensityForm } from "../src/modules/blocks/fields/densityForm";
import { commands } from "../src/modules/blocks/fields/commands";
import { currentRun, fieldsStore, modelFields, setDensity } from "../src/modules/blocks/fields/state";
import { summaryText } from "../src/modules/blocks/fields/text";
import { A, answer, answerDialog, canon, IDS, install, json, posts, stub, uninstall, without } from "./blocks-fields-kit";

type Case = { request: Record<string, unknown>; status: number; body?: Record<string, any>; job?: Record<string, any> };
const D = A.density as Record<string, Case>;
const draftCond = (field: string, op: ">" | "=", value: string) => ({ cond: { field, op, value, list: "" } }) as const;
const QUICK = { ...EMPTY_RESTRICT, restrictOn: true, restrict: draftCond("CU", ">", "0.5") };

/** What the interface leaves out of the body when it has nothing to say (the core reads a missing key and `null` the same). */
const compact = (v: unknown): unknown => {
  if (Array.isArray(v)) return v.map(compact);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([, x]) => x !== null && x !== undefined).map(([k, x]) => [k, compact(x)]));
  return v;
};

let app: App;
beforeEach(() => {
  app = install();
});
afterEach(() => uninstall());

const TABLE = [
  { value: "Руда", density: "3,1" },
  { value: "Порода", density: "2,6" },
];
const FORMS: Record<string, Partial<DensityForm>> = {
  constant: { method: "constant", value: "2,7", out: "DENS", srcField: "DENS_SRC" },
  field: { method: "field", field: "SG", defaultText: "2,5", out: "DENS_F", srcField: "DENS_F_SRC" },
  table: { method: "table", tableField: "DOM", rows: TABLE, tableDefault: "2,4", out: "DENS_T", srcField: "DENS_T_SRC", r: QUICK },
  formula: { method: "formula", expr: "2.5 + CU / 10", defaultText: "2,5", out: "DENS_E", srcField: "DENS_E_SRC" },
  no_default: { method: "field", field: "SG", out: "DENS_G", srcField: "DENS_G_SRC" },
  replace: { method: "constant", value: "2,8", out: "DENS", srcField: "DENS_SRC" },
};
const formOf = (name: string): DensityForm => ({ ...EMPTY_DENSITY, ...FORMS[name] });
const real = () => modelFields(IDS.model);

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the arguments → the body of the recorded request`, () => {
      const got = argsOfForm(IDS.model, formOf(name), real());
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      const req = requestOfArgs(got.args);
      expect(req.ok, JSON.stringify(req)).toBe(true);
      if (!req.ok) return;
      const recorded = compact(without(D[name].request, "expected_version", "replace"));
      expect(canon(compact(req.request.body))).toBe(canon(recorded));
    });
    it(`${name}: the arguments of a call show in the form and come back as the same arguments`, () => {
      const got = argsOfForm(IDS.model, formOf(name), real());
      if (!got.ok) throw new Error("form");
      const again = argsOfForm(IDS.model, formOfArgs(got.args), real());
      expect(again).toEqual(got);
    });
  }

  it("a table by a number field takes the numbers of the domain, by a category — the labels as they are", () => {
    const f = { ...EMPTY_DENSITY, method: "table" as const, tableField: "PASS", rows: [{ value: "1", density: "2,5" }, { value: "2,5", density: "3" }] };
    const got = argsOfForm(IDS.model, f, real());
    expect(got.ok).toBe(true);
    if (got.ok) expect(JSON.parse(got.args.table as string)).toEqual([{ value: 1, density: 2.5 }, { value: 2.5, density: 3 }]);
    const cat = argsOfForm(IDS.model, formOf("table"), real());
    expect(cat.ok && JSON.parse(cat.args.table as string)).toEqual([{ value: "Руда", density: 3.1 }, { value: "Порода", density: 2.6 }]);
  });

  it("the rows for the labels of a category: every label once, what was typed stays", () => {
    const labels = real().find((f) => f.name === "DOM")!.labels;
    expect(labels).toEqual(["Руда", "Порода", "Окисл."]);
    const rows = rowsForLabels(labels, [{ value: "порода", density: "2,6" }]);
    expect(rows).toEqual([{ value: "Руда", density: "" }, { value: "порода", density: "2,6" }, { value: "Окисл.", density: "" }]);
  });

  it("a request that is not right is told in Russian, with a hint and the place, before the core is asked", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = requestOfArgs(a);
      expect(r.ok, JSON.stringify(a)).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(r.issue.tab).toBe("density");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return [r.issue.place, r.issue.param];
    };
    const m = { model_id: IDS.model };
    expect(bad({})).toEqual(["model", null]);
    expect(bad({ ...m })).toEqual(["method", null]);
    expect(bad({ ...m, method: "weird" })).toEqual(["method", null]);
    expect(bad({ ...m, method: "constant" })).toEqual(["value", null]);
    expect(bad({ ...m, method: "constant", value: "0" })).toEqual(["value", null]);
    expect(bad({ ...m, method: "constant", value: "-2" })).toEqual(["value", null]);
    expect(bad({ ...m, method: "constant", value: "много" })).toEqual(["value", null]);
    expect(bad({ ...m, method: "constant", value: 2.7, default: "0" })).toEqual(["default", null]);
    expect(bad({ ...m, method: "field" })).toEqual(["field", null]);
    expect(bad({ ...m, method: "formula" })).toEqual(["expr", null]);
    expect(bad({ ...m, method: "formula", expr: "A".repeat(4001) })).toEqual(["expr", null]);
    expect(bad({ ...m, method: "table" })).toEqual(["table", null]);
    expect(bad({ ...m, method: "table", table_field: "DOM", table: "{не список" })).toEqual(["table", "table"]);
    expect(bad({ ...m, method: "table", table_field: "DOM", table: "[]" })).toEqual(["table", "table.values"]);
    expect(bad({ ...m, method: "table", table_field: "DOM", table: JSON.stringify([{ value: "Руда", density: 3 }, { value: "", density: 2 }]) })).toEqual(["table", "table.values.1.value"]);
    expect(bad({ ...m, method: "table", table_field: "DOM", table: JSON.stringify([{ value: "Руда", density: 3 }, { value: "Порода", density: 0 }]) })).toEqual(["table", "table.values.1.density"]);
    expect(bad({ ...m, method: "table", table_field: "DOM", table: JSON.stringify([{ value: "Руда", density: 3 }]), table_default: "-1" })).toEqual(["table", "table.default"]);
    expect(bad({ ...m, method: "constant", value: 2.7, out: "idx" })).toEqual(["out", null]);
    expect(bad({ ...m, method: "constant", value: 2.7, out: "D", src_field: "d" })).toEqual(["src_field", null]);
    expect(bad({ ...m, method: "constant", value: 2.7, filter_id: 1.5 })).toEqual(["filter", null]);
    expect(bad({ ...m, method: "constant", value: 2.7, restrict: "[1]" })).toEqual(["restrict", null]);
    expect(bad({ ...m, method: "constant", value: 2.7, replace: "да" })).toEqual(["general", null]);
    expect(stub.sent.filter((r) => r.method !== "GET")).toEqual([]);
    // the window: no model, an empty value, no field, an empty table, a row without a density, a name that is taken
    const f = (patch: Partial<DensityForm>) => argsOfForm(IDS.model, { ...EMPTY_DENSITY, ...patch }, real());
    const place = (r: ReturnType<typeof f>) => (r.ok ? "ok" : [r.issue.place, r.issue.param]);
    expect(argsOfForm(null, EMPTY_DENSITY, real()).ok).toBe(false);
    expect(place(f({ value: " " }))).toEqual(["value", null]);
    expect(place(f({ method: "field" }))).toEqual(["field", null]);
    expect(place(f({ method: "formula" }))).toEqual(["expr", null]);
    expect(place(f({ method: "table" }))).toEqual(["table", null]);
    expect(place(f({ method: "table", tableField: "DOM", rows: [] }))).toEqual(["table", "table.values"]);
    expect(place(f({ method: "table", tableField: "DOM", rows: [{ value: "Руда", density: "" }] }))).toEqual(["table", "table.values.0.density"]);
    expect(place(f({ method: "table", tableField: "PASS", rows: [{ value: "много", density: "2" }] }))).toEqual(["table", "table.values.0.value"]);
    expect(place(f({ out: " " }))).toEqual(["out", null]);
    expect(place(f({ r: { ...EMPTY_RESTRICT, restrictOn: true } }))).toEqual(["restrict", null]);
  });
});

describe("the result of the core is read as it was written", () => {
  it("the fields written, the counts, the defaulted blocks; a block with no density and no default stays empty (a warning, not a 0)", () => {
    const r = readResult(D.field.job!.result)!;
    expect(r).toMatchObject({ field: "DENS_F", fields: ["DENS_F", "DENS_F_SRC"], rows: 24, defaulted: 2, empty: 0, dtype: "f8" });
    const none = readResult(D.no_default.job!.result)!;
    expect(none.empty).toBe(2);
    expect(none.warnings.length).toBeGreaterThan(0);
    expect(none.warnings[0]).toMatch(/[А-Яа-яЁё]/);
    expect(none.stats).toMatchObject({ count: 22, missing: 2 }); // the two blocks have NO density: not 0
    const t = readResult(D.table.job!.result)!;
    expect(t.passed).toBeLessThan(t.rows); // outside the restrictor both fields are empty
    expect(t.defaulted).toBeGreaterThan(0);
  });

  it("the line of the log: the fields, the counts, the blocks that took the default, the blocks with no density", () => {
    expect(summaryText("density", readResult(D.field.job!.result)!, "Месторождение")).toBe(
      "Плотность «DENS_F» и источник «DENS_F_SRC» записана в модель «Месторождение»: блоков: 24, прошло ограничитель: 24, подставлено по умолчанию: 2, без плотности: 0; версия модели " + D.field.job!.result.version,
    );
    expect(summaryText("density", readResult(D.no_default.job!.result)!, "М")).toContain("без плотности: 2");
  });
});

describe("the command `blocks.density`", () => {
  const args = (name: string) => {
    const got = argsOfForm(IDS.model, formOf(name), real());
    if (!got.ok) throw new Error("form");
    return got.args;
  };
  const route = (c: Case) => stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/density` ? answer(c as never) : undefined));

  it("is in the registry: a Russian title and a description for the assistant; confirm; without model_id and method the window opens", async () => {
    const c = app.registry.get("blocks.density")!;
    expect(commands.map((x) => x.id)).toContain("blocks.density");
    expect(c.run).toBeTypeOf("function");
    expect(c.confirm).toBe(true);
    expect(c.title).toBe("Плотность блоков");
    expect(c.description).toMatch(/остаётся пустой \(не 0\)/);
    expect(c.required ?? []).toEqual([]);
    expect(Object.keys(c.params!)).toEqual(["model_id", "method", "value", "field", "table_field", "table", "table_default", "expr", "default", "out", "src_field", "filter_id", "restrict", "replace"]);
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    const r = await app.registry.execute("blocks.density", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Поля блочной модели»: плотность" });
    expect(fieldsStore.get().tab).toBe("density");
    expect(windowOfFields()).not.toBeNull();
    const tab = await app.registry.execute("blocks.density", { model_id: IDS.model }, "user");
    expect(tab.ok).toBe(true);
    expect(posts(/\/density$/)).toEqual([]);
  });

  for (const name of ["constant", "field", "table", "formula", "no_default"]) {
    it(`${name}: the body, the job, the result in the window, the line for the log`, async () => {
      route(D[name]);
      const r = await app.registry.execute("blocks.density", args(name), "user");
      expect(r.ok, r.result).toBe(true);
      const sent = posts(/\/density$/);
      expect(sent).toHaveLength(1);
      expect(canon(compact(without(sent[0].body, "expected_version")))).toBe(canon(compact(without(D[name].request, "expected_version"))));
      expect(sent[0].body.expected_version).toBe(stub.version);
      expect(currentRun().phase).toBe("done");
      expect(currentRun().result).toEqual(readResult(D[name].job!.result));
      expect(r.result).toMatch(/^Плотность «DENS[_A-Z]*» и источник «DENS[_A-Z]*» записана в модель «Месторождение»/);
      const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
      for (const id of ["density-panel", "density-method-table", "density-default", "density-out", "density-src", "density-build", "fields-result", "fields-result-defaulted"]) expect(html, id).toContain(`data-testid="${id}"`);
    });
  }

  it("the table of the form: the rows of the call show in the window, a row by label is a choice of the labels of the category", async () => {
    route(D.table);
    await app.registry.execute("blocks.density", args("table"), "user");
    expect(fieldsStore.get().density).toMatchObject({ method: "table", tableField: "DOM", tableDefault: "2,4", rows: TABLE });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toContain('data-testid="density-row-0-value"');
    expect(html).toContain('data-testid="density-row-1-density"');
    expect(html).toContain('data-testid="density-fill-labels"');
    expect(html).toContain("Руда");
  });

  it("fields that are there: a question in our own window; «Заменить» → the same body with replace: true", async () => {
    stub.routes.push((m, p, b) => (m === "POST" && p === `/blocks/${IDS.model}/density` ? (b.replace === true ? answer(D.replace as never) : answer(D.exists as never)) : undefined));
    const run = app.registry.execute("blocks.density", args("replace"), "user");
    const asked = await answerDialog(true);
    expect(asked).toBe("Заменить поле|Поле «DENS» в модели «Месторождение» уже есть. Заменить его новым? Прежние значения поля пропадут, единица и описание не переносятся.");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    const sent = posts(/\/density$/);
    expect(sent.map((x) => x.body.replace)).toEqual([undefined, true]);
    expect(canon(compact(sent[1].body))).toBe(canon(compact({ ...D.replace.request, expected_version: stub.version })));
    expect(dialogStore.get().queue).toEqual([]);
    // «Не заменять»
    stub.sent.length = 0;
    const no = app.registry.execute("blocks.density", args("replace"), "user");
    await answerDialog(false);
    expect(await no).toEqual({ ok: true, result: "Поле «DENS» оставлено без изменений" });
    expect(posts(/\/density$/)).toHaveLength(1);
  });

  it("a block with a density that is not above zero stops the job: the words of the core, the place, nothing is written", async () => {
    route(D.invalid);
    const r = await app.registry.execute("blocks.density", { model_id: IDS.model, method: "field", field: "AU", default: "2,5", out: "DENS_X", src_field: "DENS_X_SRC" }, "user");
    const e = D.invalid.job!.error;
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${e.message} ${e.hint}`);
    expect(e.message).toMatch(/больше нуля/);
    expect(fieldsStore.get().issue).toMatchObject({ tab: "density", place: "general", message: e.message });
    expect(currentRun().phase).toBe("failed");
    expect(currentRun().result).toBeNull();
  });

  const PLACE: Record<string, [string, string | null]> = {
    field_missing: ["field", "field"],
    field_category: ["field", "field"],
    bad_method: ["method", "method"],
    no_value: ["value", "value"],
    bad_default: ["default", "default"],
    same_names: ["src_field", "src_field"],
    bad_formula: ["expr", "expr"],
    table_empty: ["table", "table.values"],
  };
  for (const [name, [place, param]] of Object.entries(PLACE)) {
    it(`${name}: the words and the hint of the core, under «${place}»`, async () => {
      const c = D[name];
      const e = c.body!;
      expect([404, 422]).toContain(c.status);
      expect(e.message).toMatch(/[А-Яа-яЁё]/);
      expect(e.hint).not.toBe("");
      stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/density` ? json(c.body, c.status) : undefined));
      const r = await app.registry.execute("blocks.density", args("constant"), "user");
      expect(r.ok).toBe(false);
      expect(r.result).toBe(`${e.message} ${e.hint}`);
      const issue = fieldsStore.get().issue!;
      expect(issue.tab).toBe("density");
      expect(issue.message).toBe(e.message);
      expect(issue.param).toBe(e.param ?? null);
      expect(issue.param).toBe(param);
      expect(issue.place).toBe(place);
    });
  }

  it("a row of the table that the core refuses is marked in the window (its `param`), the words stand under the table", async () => {
    stub.routes.push((m, p) => (m === "POST" && p === `/blocks/${IDS.model}/density` ? json({ code: "bad_param", message: "Плотность должна быть больше нуля.", hint: "Пустое значение задаёт «по умолчанию».", param: "table.values.1.density" }, 422) : undefined));
    await app.registry.execute("blocks.density", args("table"), "user");
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toMatch(/aria-invalid="true"[^>]*data-testid="density-row-1-density"/);
    expect(html).not.toMatch(/aria-invalid="true"[^>]*data-testid="density-row-0-density"/);
    expect(html).toContain('data-testid="fields-issue-density-table"');
    expect(html).toContain("Плотность должна быть больше нуля.");
  });

  it("the density by field: the model without the field, the unit of the field — the words of the core; the window lists the number fields only", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.density", {}, "user");
    setDensity({ method: "field" });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CalcWindow, { winId: windowOfFields()! })));
    expect(html).toContain('data-testid="density-field"');
    expect(html).toContain("SG, т/м³");
    expect(html).not.toMatch(/<option value="DOM">/); // a category is not a density
  });

  it("another model: the field and the table of the first one go; the window keeps one run", async () => {
    openFieldsWindow(app, "density", IDS.model);
    setDensity({ method: "field", field: "SG", tableField: "DOM", rows: [{ value: "Руда", density: "3" }] });
    chooseModel(IDS.model); // the same model keeps the form
    expect(fieldsStore.get().density.field).toBe("SG");
    chooseModel(null);
    expect(fieldsStore.get().density).toMatchObject({ field: "", tableField: "", rows: [] });
  });
});
