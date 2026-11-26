// The rocks of the pit (Э21, 21.17; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 6, 29; docs/progress/e21-api.md §2, §3, §16): the parameters of the window = the request the core takes
// (`rocks` of the canonical params of the cases of `core/tests/data/pit/params.json`), what is wrong is told in Russian with a hint in its place before anything is sent, the types met in
// the data (the labels of the field, the names of the rules, what `validate` calls), the flat arguments of `pit.rocks` and the form are the same request, the window and the command.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { DEFAULT_LIMITS, readParams } from "../../src/modules/pit/answers";
import { openRocksWindow, renderPitWindow, windowOfRocks } from "../../src/modules/pit/bind";
import { rocksIssueOf } from "../../src/modules/pit/bindSetup";
import { commands } from "../../src/modules/pit/commands";
import {
  ALL_LABEL,
  byKey,
  byKeyOfParams,
  EMPTY_LABEL,
  EMPTY_ROCKS,
  encounteredTypes,
  hasRocks,
  newRock,
  newRule,
  OTHER_LABEL,
  rocksArgs,
  rocksFormOfArgs,
  rocksFormOfParams,
  rocksPatch,
  rowsOf,
  typeNamesOfMissing,
  withRow,
  type RocksForm,
} from "../../src/modules/pit/formsRocks";
import { RocksWindow } from "../../src/modules/pit/RocksWindow";
import { PIT_ROCKS_WINDOW, pitStore, selectScenario, setRocksForm } from "../../src/modules/pit/state";
import { answer, caseParams, ID, installCore, newApp, PARAMS, teardown, waitFor, type Core } from "./harness";

const CASES = Object.keys(PARAMS.cases as Record<string, unknown>);
const ELEMENTS = ["AU", "CU"];
const patchOf = (f: RocksForm, elements = ELEMENTS) => {
  const r = rocksPatch(f, elements);
  if (!r.ok) throw new Error(JSON.stringify(r.issue));
  return r.patch.rocks;
};
const wrong = (f: RocksForm, elements = ELEMENTS) => {
  const r = rocksPatch(f, elements);
  if (r.ok) throw new Error("the form must be wrong");
  return r.issue;
};
const form = (patch: Partial<RocksForm>): RocksForm => ({ ...EMPTY_ROCKS, ...patch });
const row = (name: string, patch: Partial<ReturnType<typeof newRock>> = {}) => ({ ...newRock(name), ...patch });

describe("the window «Породы»: the parameters of the window = the request", () => {
  it.each(CASES)("%s: the form of the canonical params gives the same `rocks` back", (name) => {
    const p = readParams(caseParams(name));
    expect(patchOf(rocksFormOfParams(p), p.elements.map((e) => e.field))).toEqual(p.rocks);
  });
  it("the two cases of the task: the field (any case of the names) and the rules (the order is kept; air and «Прочие» are types too)", () => {
    const byField = readParams(caseParams("rocks_field"));
    const f = rocksFormOfParams(byField);
    expect(f).toMatchObject({ by: "field", field: "rock" });
    expect(f.types.map((t) => [t.name, t.cP, t.cMr, t.mode])).toEqual([["(пусто)", "", "0", "waste"], ["Окисленная", "12", "0", "rock"], ["первичная", "18", "0,5", "rock"]]);
    const byRules = readParams(caseParams("rocks_rules"));
    const r = rocksFormOfParams(byRules);
    expect(r.by).toBe("rules");
    expect(r.rules.map((x) => x.name)).toEqual(["Богатая", "Воздух"]);
    expect(r.types.map((t) => [t.name, t.mode])).toEqual([["Богатая", "rock"], ["Воздух", "air"], ["Прочие", "waste"]]);
    expect(patchOf(r, ["AU"])).toEqual(byRules.rocks);
  });
  it("a number is typed with a decimal comma; c_p that is not set is `null` and not 0; a recovery that is not set is absent, not 0", () => {
    const rocks = patchOf(form({ types: [row("Все породы", { cP: "12,5", cMr: "", rec: { AU: "0,85", CU: "" } })] }));
    expect(rocks.types).toEqual([{ name: "Все породы", c_p: 12.5, c_mr: 0, rec: { AU: 0.85 }, waste_only: false, air: false }]);
    expect(patchOf(form({ types: [row("Все породы")] })).types[0]).toMatchObject({ c_p: null, rec: {} });
    expect(patchOf(EMPTY_ROCKS)).toEqual({ by: null, types: [] });
  });
  it("the kinds: «только пустая» and «воздух» are marks that exclude each other; the keys of the recovery are in the writing of the element", () => {
    const rocks = patchOf(form({ types: [row("A", { mode: "waste" }), row("B", { mode: "air" }), row("C", { rec: { au: "0,5" } })] }));
    expect(rocks.types.map((t) => [t.waste_only, t.air])).toEqual([[true, false], [false, true], [false, false]]);
    expect(rocks.types[2].rec).toEqual({ AU: 0.5 });
  });
});

describe("what is wrong is told in Russian with a hint and the place", () => {
  it("a field of the rocks that is not chosen; no rules; a rule with no name, with a taken name, named «Прочие», with a wrong tree; more than 50 rules", () => {
    expect(wrong(form({ by: "field" }))).toMatchObject({ field: "rock_by", param: "rocks.by.field", message: "Не выбрано поле пород." });
    expect(wrong(form({ by: "rules" }))).toMatchObject({ field: "rock_by", message: "Нет правил пород." });
    const rule = (name: string, expr = "AU > 1") => ({ name, draft: { expr } });
    expect(wrong(form({ by: "rules", rules: [rule("")] }))).toMatchObject({ param: "rocks.by.rules.0.name", message: "У правила 1 нет имени." });
    expect(wrong(form({ by: "rules", rules: [rule("Богатая"), rule("богатая")] }))).toMatchObject({ param: "rocks.by.rules.1.name", message: "Имя «богатая» повторяется." });
    const other = wrong(form({ by: "rules", rules: [rule(OTHER_LABEL)] }));
    expect(other).toMatchObject({ param: "rocks.by.rules.0.name" });
    expect(other.message).toBe("Имя «Прочие» правилу дать нельзя.");
    expect(wrong(form({ by: "rules", rules: [{ name: "Х", draft: { expr: "" } }] })).message).toMatch(/^Правило «Х»: /);
    expect(wrong(form({ by: "rules", rules: Array.from({ length: 51 }, (_, i) => rule(`Т${i}`)) })).message).toBe("Правил больше 50.");
  });
  it("c_p, c_mр and the recovery: not a number, negative, above 1, an element that the scenario does not have; the name of a type", () => {
    expect(wrong(form({ types: [row("А", { cP: "много" })] }))).toMatchObject({ field: "rock_types", param: "rocks.types.0.c_p" });
    expect(wrong(form({ types: [row("А", { cP: "-1" })] })).message).toBe("Затраты c_p типа «А» вне диапазона 0…10¹².");
    expect(wrong(form({ types: [row("А", { cMr: "-0,5" })] })).param).toBe("rocks.types.0.c_mr");
    const rec = wrong(form({ types: [row("А", { rec: { AU: "1,2" } })] }));
    expect(rec).toMatchObject({ param: "rocks.types.0.rec.AU", message: "Извлечение AU типа «А» вне диапазона 0…1." });
    expect(rec.hint).toBe("Доля от 0 до 1: 0,85 — это 85 %.");
    expect(wrong(form({ types: [row("А", { rec: { AU: "x" } })] })).message).toBe("Извлечение AU типа «А» — не число.");
    expect(wrong(form({ types: [row("А", { rec: { ZN: "0,5" } })] })).message).toBe("Извлечение для «ZN»: такого элемента нет в сценарии.");
    expect(wrong(form({ types: [row(" ")] })).message).toBe("У типа 1 нет имени.");
    expect(wrong(form({ types: [row("А"), row("а")] })).message).toBe("Тип «а» повторяется.");
    expect(wrong(form({ types: Array.from({ length: 101 }, (_, i) => row(`Т${i}`)) })).message).toBe("Типов пород больше 100.");
  });
});

describe("the types met in the data", () => {
  const cats = [{ field: "ROCK", labels: ["Окисленная", "Первичная"] }, { field: "DOM", labels: ["A", "B", "C"] }];
  it("one type for all; the labels of the field and «(пусто)»; the names of the rules and «Прочие»; what `validate` calls comes last", () => {
    expect(encounteredTypes(EMPTY_ROCKS, cats)).toEqual([ALL_LABEL]);
    expect(encounteredTypes(form({ by: "field", field: "rock" }), cats)).toEqual(["Окисленная", "Первичная", EMPTY_LABEL]);
    expect(encounteredTypes(form({ by: "field", field: "" }), cats)).toEqual([]);
    expect(encounteredTypes(form({ by: "rules", rules: [{ name: "Богатая", draft: newRule().draft }, { name: "", draft: newRule().draft }] }), cats)).toEqual(["Богатая", OTHER_LABEL]);
    expect(encounteredTypes(EMPTY_ROCKS, cats, ["Окисленная", "все породы"])).toEqual([ALL_LABEL, "Окисленная"]);
  });
  it("the names of the types of `validate.missing`: the path is by the name (a name may hold a space or a dot); the answer of the contract names «Все породы»", () => {
    const miss = answer("validate_new").body.missing as { param: string; message: string }[];
    expect(typeNamesOfMissing(miss)).toEqual(["Все породы"]);
    const m = (param: string) => ({ param, message: "" });
    expect(typeNamesOfMissing([m("rocks.types.Окисленная"), m("rocks.types.Окисленная.c_p"), m("rocks.types.Окисленная.rec.CU"), m("rocks.types.Руда 1.5.c_p"), m("mining.c_m0")])).toEqual(["Окисленная", "Руда 1.5"]);
  });
  it("the rows: the types met first (the row of the form, else an empty one), then the rows of the scenario the data does not have", () => {
    const f = form({ types: [row("первичная", { cP: "18" }), row("Старая", { cP: "1" })] });
    const rows = rowsOf(f, ["Окисленная", "Первичная"]);
    expect(rows.map((r) => [r.row.name, r.met, r.touched])).toEqual([["Окисленная", true, false], ["первичная", true, true], ["Старая", false, true]]);
    expect(withRow(f, "ПЕРВИЧНАЯ", { cP: "20" }).types.map((t) => t.cP)).toEqual(["20", "1"]); // the same type by the name without the case
    expect(withRow(f, "Новая", { cP: "5" }).types.map((t) => t.name)).toEqual(["первичная", "Старая", "Новая"]);
  });
  it("`validate` is about the saved way of typing: the key of the way tells whether the form still has it", () => {
    const p = readParams(caseParams("rocks_field"));
    expect(byKey(rocksFormOfParams(p))).toBe(byKeyOfParams(p));
    expect(byKey({ ...rocksFormOfParams(p), field: "DOM" })).not.toBe(byKeyOfParams(p));
    expect(byKey(EMPTY_ROCKS)).toBe("");
  });
});

describe("the flat arguments of `pit.rocks`", () => {
  const base = readParams(caseParams("rocks_field"));
  it("the form → the arguments → the form → the same `rocks`: the field, the rules, the types as JSON of the API", () => {
    for (const name of CASES) {
      const p = readParams(caseParams(name));
      const f = rocksFormOfParams(p);
      const els = p.elements.map((e) => e.field);
      const got = rocksArgs(f, ID.scenario, els);
      if (!got.ok) throw new Error(JSON.stringify(got.issue));
      expect(got.args.scenario_id).toBe(ID.scenario);
      const back = rocksFormOfArgs(got.args, readParams(caseParams("minimal")));
      if (!back.ok) throw new Error(JSON.stringify(back.issue));
      expect(patchOf(back.form, els), name).toEqual(p.rocks);
    }
    const got = rocksArgs(rocksFormOfParams(base), null, ELEMENTS);
    if (!got.ok) throw new Error("wrong");
    expect(got.args).not.toHaveProperty("scenario_id");
    expect(got.args.rock_field).toBe("rock");
    expect(got.args.rock_rules).toBeNull();
    expect(JSON.parse(got.args.rock_types as string)[1]).toEqual({ name: "Окисленная", c_p: 12, c_mr: 0, rec: { AU: 0.7, CU: 0.5 }, waste_only: false, air: false });
  });
  it("a key that is not in the call leaves the scenario as it is; null takes the way of typing away; `rock_types` replaces all the types", () => {
    expect(hasRocks({})).toBe(false);
    expect(hasRocks({ rock_types: "[]" })).toBe(true);
    const types = rocksFormOfArgs({ rock_types: JSON.stringify([{ name: "Окисленная", c_p: "13,5", rec: { AU: 0.7 } }]) }, base);
    if (!types.ok) throw new Error("wrong");
    expect(types.form).toMatchObject({ by: "field", field: "rock" }); // the way stays
    expect(types.form.types).toEqual([{ name: "Окисленная", cP: "13,5", cMr: "", rec: { AU: "0,7" }, mode: "rock" }]);
    const clear = rocksFormOfArgs({ rock_field: null }, base);
    if (!clear.ok) throw new Error("wrong");
    expect(clear.form).toMatchObject({ by: "all", field: "", rules: [] });
    expect(clear.form.types).toHaveLength(3); // the types stay
  });
  it("wrong arguments are told: both ways at once, rules and types that are not JSON, a type with two marks or with no name", () => {
    const both = rocksFormOfArgs({ rock_field: "ROCK", rock_rules: "[]" }, base);
    expect(both.ok).toBe(false);
    if (!both.ok) expect(both.issue).toMatchObject({ field: "rock_by", message: "Заданы и поле пород, и правила." });
    const rules = rocksFormOfArgs({ rock_rules: "не json" }, base);
    if (!rules.ok) expect(rules.issue.message).toBe("Правила пород — не список правил в JSON.");
    expect(rules.ok).toBe(false);
    const types = rocksFormOfArgs({ rock_types: "{}" }, base);
    if (!types.ok) expect(types.issue.message).toBe("Типы пород — не список в JSON.");
    const two = rocksFormOfArgs({ rock_types: JSON.stringify([{ name: "А", waste_only: true, air: true }]) }, base);
    if (!two.ok) expect(two.issue).toMatchObject({ param: "rocks.types.0.air", message: "Тип «А» отмечен и «только пустая», и «воздух»." });
    const noName = rocksFormOfArgs({ rock_types: JSON.stringify([{ c_p: 1 }]) }, base);
    if (!noName.ok) expect(noName.issue.message).toBe("У типа 1 нет имени.");
    expect([types.ok, two.ok, noName.ok]).toEqual([false, false, false]);
  });
  it("a refusal of the core is put in its place of the form", () => {
    const r = (code: string, param: string | null) => rocksIssueOf({ code, message: "М", hint: "П", param }).field;
    expect(r("bad_param", "rocks.by.rules.1.name")).toBe("rock_by");
    expect(r("block_field_not_found", "rocks.by.field")).toBe("rock_by");
    expect(r("bad_param", "rocks.types.0.rec.AU")).toBe("rock_types");
    expect(r("bad_param", "elements")).toBe("general");
  });
});

describe("`pit.rocks` and the window", () => {
  let app: App;
  let core: Core;
  let unbind: () => void;
  beforeEach(async () => {
    core = installCore();
    ({ app, unbind } = newApp());
    await waitFor(() => pitStore.get().scenariosLoaded);
    core.scenario.params = caseParams("rocks_field");
    await selectScenario(ID.scenario);
  });
  afterEach(() => teardown(unbind));
  const sentPuts = () => core.sent.filter((r) => r.method === "PUT" && r.path === `/pit/scenarios/${ID.scenario}`);
  const run = (args: Record<string, unknown> = {}) => app.registry.execute("pit.rocks", args, "user");
  const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(RocksWindow, { winId: "w" })));

  it("the command is declared with flat parameters and is bound (it is not unavailable)", () => {
    const c = commands.find((x) => x.id === "pit.rocks");
    expect(Object.keys(c?.params ?? {})).toEqual(["scenario_id", "rock_field", "rock_rules", "rock_types"]);
    expect(c?.stage).toBeUndefined();
    expect(app.registry.reason("pit.rocks")).toBeNull();
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("pit.rocks")).toBe("Проект открыт только для чтения");
  });
  it("without arguments the window opens with the rocks of the scenario in the form and the types of the data in the table", async () => {
    expect((await run()).result).toBe("Открыто окно «Породы карьера»");
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_ROCKS_WINDOW)).toHaveLength(1);
    expect(windowStore.get().windows[0].title).toBe("Породы карьера · Сценарий карьера");
    await waitFor(() => !!pitStore.get().models[ID.model]);
    expect(pitStore.get().rocksForm).toMatchObject({ by: "field", field: "rock" });
    const text = html();
    for (const id of ["pit-rocks-window", "pit-by-all", "pit-by-field", "pit-by-rules", "pit-rock-field", "pit-types", "pit-type-Окисленная", "pit-type-первичная", "pit-type-(пусто)", "pit-mode-Окисленная", "pit-cp-Окисленная", "pit-cmr-Окисленная", "pit-rec-Окисленная-AU", "pit-rec-Окисленная-CU", "pit-type-new", "pit-type-add", "pit-rocks-apply", "pit-ready", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("Сценарий «Сценарий карьера»");
    expect(text).toContain("rec CU");
    expect(text).toContain('value="12"'); // c_p of «Окисленная»
    expect(text).toContain("Для запуска не хватает:");
    expect(renderPitWindow(windowStore.get().windows[0])).not.toBeNull();
    await run();
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_ROCKS_WINDOW)).toHaveLength(1);
    expect(windowOfRocks()).not.toBeNull();
  });
  it("«по правилам»: the rules are listed in their order with the constructor, the types are the rules and «Прочие»", async () => {
    core.scenario.params = caseParams("rocks_rules");
    await openRocksWindow(ID.scenario);
    const text = html();
    for (const id of ["pit-rules", "pit-rule-0", "pit-rule-name-0", "pit-rule-up-1", "pit-rule-down-0", "pit-rule-del-1", "pit-rule-add", "pit-type-Богатая", "pit-type-Воздух", "pit-type-Прочие"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("Богатая");
    expect(text).toContain('data-testid="pit-rec-Богатая-AU"');
    expect(text).not.toContain('data-testid="pit-rec-Богатая-CU"'); // the scenario has the element AU only
  });
  it("with the types in the call the scenario is changed: the whole canonical params go with the version read, the way and what the call does not name stay", async () => {
    const r = await run({ rock_types: JSON.stringify([{ name: "Окисленная", c_p: 13, rec: { AU: 0.75, CU: 0.5 } }, { name: "(пусто)", waste_only: true }]) });
    expect(r.ok).toBe(true);
    const put = sentPuts()[0].body;
    expect(put.expected_version).toBe(2);
    expect(put.params).toEqual({
      ...caseParams("rocks_field"),
      rocks: {
        by: { kind: "field", field: "rock" },
        types: [
          { name: "Окисленная", c_p: 13, c_mr: 0, rec: { AU: 0.75, CU: 0.5 }, waste_only: false, air: false },
          { name: "(пусто)", c_p: null, c_mr: 0, rec: {}, waste_only: true, air: false },
        ],
      },
    });
    expect(r.result).toContain("Сценарий «Сценарий карьера» обновлён (версия 3).");
  });
  it("«Применить» of the window is the same request as the command: the types typed in the window go with the elements of the scenario", async () => {
    await run();
    await waitFor(() => !!pitStore.get().models[ID.model]);
    setRocksForm((f) => withRow(f, "Первичная", { cP: "19", rec: { AU: "0,9", CU: "0,9" } })); // the same type as «первичная» of the scenario (the name has no case)
    const got = rocksArgs(pitStore.get().rocksForm, ID.scenario, ELEMENTS);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect((await run(got.args)).ok).toBe(true);
    const types = (sentPuts()[0].body.params as { rocks: { types: { name: string; c_p: number | null; rec: Record<string, number> }[] } }).rocks.types;
    expect(types.map((t) => [t.name, t.c_p])).toEqual([["(пусто)", null], ["Окисленная", 12], ["первичная", 19]]);
    expect(types[2]).toMatchObject({ c_p: 19, rec: { AU: 0.9, CU: 0.9 } });
  });
  it("a wrong argument is told with its hint in its place and nothing is sent; a refusal of the core is told and put in its place", async () => {
    const bad = await run({ rock_types: JSON.stringify([{ name: "А", rec: { AU: 2 } }]) });
    expect(bad.ok).toBe(false);
    expect(bad.result).toMatch(/^Извлечение AU типа «А» вне диапазона 0…1\. Доля от 0 до 1/);
    expect(pitStore.get().rocksIssue).toMatchObject({ field: "rock_types", param: "rocks.types.0.rec.AU" });
    expect((await run({ rock_field: "ROCK", rock_rules: "[]" })).result).toMatch(/^Заданы и поле пород, и правила\./);
    expect(sentPuts()).toHaveLength(0);
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 422, body: { code: "bad_param", message: "Тип «Окисленная» не найден в данных.", hint: "Проверьте поле пород.", param: "rocks.types.0.name" } });
    const r = await run({ rock_types: "[]" });
    expect(r).toEqual({ ok: false, result: "Тип «Окисленная» не найден в данных. Проверьте поле пород." });
    expect(pitStore.get().rocksIssue).toMatchObject({ field: "rock_types", param: "rocks.types.0.name" });
    const no = await run({ scenario_id: ID.noScenario, rock_types: "[]" });
    expect(no.result).toBe("Сценарий карьера №999 не найден. Номера сценариев — в разделе «Карьер» → «Сценарии» дерева.");
  });
  it("a window of a wrong form shows the words under the part and marks the row", async () => {
    await run();
    await waitFor(() => !!pitStore.get().models[ID.model]);
    pitStore.set({ rocksIssue: { field: "rock_types", param: "rocks.types.0.c_p", message: "Затраты c_p типа «(пусто)» — не число.", hint: "Введите число." } });
    expect(html()).toContain('data-testid="pit-issue-rock_types"');
    expect(DEFAULT_LIMITS.rock_types).toBe(100);
  });
});
