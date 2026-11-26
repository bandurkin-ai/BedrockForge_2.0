// The forms of the windows «Новый сценарий» and «Экономика» (Э21, 21.16; ADR 0028 решения 3, 5, 29; docs/progress/e21-api.md §2, §13, §16): the parameters of
// the window = the request the core takes (the canonical params of the cases of `core/tests/data/pit/params.json`), what is wrong is told in Russian with
// a hint in its place of the form before anything is sent, the flat arguments of the commands and the forms are the same request.
import { describe, expect, it } from "vitest";
import { validateArgs } from "../../src/shell/commands";
import { DEFAULT_LIMITS, readParams, type PitParams } from "../../src/modules/pit/answers";
import { commands } from "../../src/modules/pit/commands";
import {
  economicsArgs,
  economicsFormOfArgs,
  economicsOfParams,
  economicsPatch,
  elementsText,
  EMPTY_ECONOMICS,
  EMPTY_SCENARIO,
  hasEconomics,
  hasRelief,
  hasScenarioKeys,
  mergeParams,
  parseElements,
  parseRfList,
  proposedElements,
  rangeOfList,
  rfOfRange,
  scenarioArgs,
  scenarioFormOfArgs,
  scenarioFormOfParams,
  scenarioPatch,
  shellCount,
  type EconomicsForm,
} from "../../src/modules/pit/forms";
import { priceLabel } from "../../src/modules/pit/format";
import { caseParams, PARAMS } from "./harness";

const CASES = Object.keys(PARAMS.cases as Record<string, unknown>);
const by = Object.fromEntries(commands.map((c) => [c.id, c]));
const patchOf = (f: EconomicsForm) => {
  const r = economicsPatch(f);
  if (!r.ok) throw new Error(JSON.stringify(r.issue));
  return r.patch;
};
const wrong = (patch: Partial<EconomicsForm>) => {
  const r = economicsPatch({ ...EMPTY_ECONOMICS, ...patch });
  if (r.ok) throw new Error("the form must be wrong");
  return r.issue;
};

describe("the window «Экономика»: the parameters of the window = the request", () => {
  it.each(CASES)("%s: the form of the canonical params gives the same params back (the rest of the scenario stays as it is)", (name) => {
    const base = readParams(caseParams(name));
    const merged = mergeParams(base, patchOf(economicsOfParams(base)));
    expect(JSON.parse(JSON.stringify(merged))).toEqual(caseParams(name));
  });
  it.each(CASES)("%s: the flat arguments of `pit.economics` are the same request (and the declared parameters accept them)", (name) => {
    const base = readParams(caseParams(name));
    const form = economicsOfParams(base);
    const got = economicsArgs(form, 17);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect(validateArgs(by["pit.economics"], got.args), name).toBeNull();
    const back = economicsFormOfArgs(got.args, base);
    expect(JSON.parse(JSON.stringify(mergeParams(base, patchOf(back)))), name).toEqual(caseParams(name));
    expect(hasEconomics(got.args)).toBe(true);
    expect(hasEconomics({ scenario_id: 17 })).toBe(false);
  });
  it("the case of the task 8.11.7: the element and the price, the density as text, c_m0, the rocks stay", () => {
    const base = readParams(caseParams("t8117"));
    const f = economicsOfParams(base);
    expect(f.elements).toEqual([{ field: "au", unit: "g/t", price: "2000", sales: "5", royalty: "0" }]);
    expect(f).toMatchObject({ densityMode: "value", densityValue: "2,7", cM0: "2,5", zRef: "", hGl: "10", cGl: "0", rfMode: "range", rfFrom: "0,3", rfTo: "1,5", rfStep: "0,05", prefix: "" });
    const p = patchOf({ ...f, elements: [{ ...f.elements[0], price: "2 200,5" }] });
    expect(p.elements[0]).toEqual({ field: "au", unit: "g/t", price: 2200.5, sales: 5, royalty: 0 });
    expect(mergeParams(base, p).rocks).toEqual(base.rocks);
  });
  it("a price that is not set is null («не готов»), never 0; z_ref that is not set is null (the top of the grid)", () => {
    const p = patchOf({ ...EMPTY_ECONOMICS, elements: [{ field: "AU", unit: "", price: "", sales: "", royalty: "" }] });
    expect(p.elements).toEqual([{ field: "AU", unit: null, price: null, sales: 0, royalty: 0 }]);
    expect(p.mining).toEqual({ c_m0: null, z_ref: null, h_gl: 10, c_gl: 0 });
    expect(p.density).toBeNull();
    expect(p.default_density).toBeNull();
  });
  it("RF: the range is the canonical list (no noise of the sum), the list is sorted and without repeats, 1 shell is allowed", () => {
    expect(patchOf({ ...EMPTY_ECONOMICS, rfFrom: "0,5", rfTo: "1,5", rfStep: "0,25" }).rf).toEqual(PARAMS.cases.rf_range.canonical.rf);
    expect(patchOf(EMPTY_ECONOMICS).rf).toEqual(PARAMS.cases.minimal.canonical.rf);
    expect(patchOf({ ...EMPTY_ECONOMICS, rfMode: "list", rfList: "1,2; 0,8; 1; 0,8; 5" }).rf).toEqual([0.8, 1, 1.2, 5]);
    expect(patchOf({ ...EMPTY_ECONOMICS, rfMode: "list", rfList: "1" }).rf).toEqual([1]);
    expect(rangeOfList([0.3, 0.35, 0.4])).toEqual({ from: 0.3, to: 0.4, step: 0.05 });
    expect(rangeOfList([0.8, 1, 1.2, 5])).toBeNull();
    expect(shellCount(EMPTY_ECONOMICS)).toBe(25);
    expect(shellCount({ ...EMPTY_ECONOMICS, rfStep: "0" })).toBeNull();
    expect(parseRfList("0,8; abc").ok).toBe(false);
  });
});

describe("what is wrong is told in Russian with a hint in its place, nothing wrong is sent", () => {
  const row = (patch: Partial<EconomicsForm["elements"][number]>) => ({ ...EMPTY_ECONOMICS, elements: [{ field: "AU", unit: "", price: "1", sales: "", royalty: "", ...patch }] });
  it.each([
    ["a price that is not a number", row({ price: "abc" }), "elements.0.price", /Цена AU — не число/],
    ["a negative price", row({ price: "-1" }), "elements.0.price", /вне диапазона/],
    ["a sales that is not a number", row({ sales: "x" }), "elements.0.sales", /Сбыт AU — не число/],
    ["a royalty of 1 (the range is [0; 1))", row({ royalty: "1" }), "elements.0.royalty", /вне диапазона \[0; 1\)/],
    ["a royalty that is not a number", row({ royalty: "много" }), "elements.0.royalty", /Роялти AU — не число/],
    ["a unit that is not in the list", row({ unit: "кг" }), "elements.0.unit", /Единица «кг» не из списка/],
    ["a row without a field", row({ field: "" }), "elements.0.field", /не выбрано поле/],
  ])("%s", (_t, form, param, text) => {
    const i = wrong(form);
    expect(i).toMatchObject({ field: "elements", param });
    expect(i.message).toMatch(text);
    expect(i.hint).toMatch(/[А-Яа-я]/);
  });
  it("the same field twice; more than 20 elements", () => {
    const e = { field: "AU", unit: "", price: "1", sales: "", royalty: "" };
    expect(wrong({ elements: [e, { ...e, field: "au" }] }).message).toMatch(/выбрано дважды/);
    expect(wrong({ elements: Array.from({ length: 21 }, (_, i) => ({ ...e, field: `E${i}` })) }).message).toMatch(/больше 20/);
  });
  it("the density, the mining costs, RF and the prefix", () => {
    expect(wrong({ densityMode: "field" })).toMatchObject({ field: "density" });
    expect(wrong({ densityMode: "value", densityValue: "0" })).toMatchObject({ field: "density" });
    expect(wrong({ defaultDensity: "-1" })).toMatchObject({ field: "density", param: "default_density" });
    expect(wrong({ cM0: "x" })).toMatchObject({ field: "mining", param: "mining.c_m0" });
    expect(wrong({ zRef: "x" })).toMatchObject({ field: "mining", param: "mining.z_ref" });
    expect(wrong({ hGl: "0" })).toMatchObject({ field: "mining", param: "mining.h_gl" });
    expect(wrong({ cGl: "-2" })).toMatchObject({ field: "mining", param: "mining.c_gl" });
    expect(wrong({ rfStep: "0" })).toMatchObject({ field: "rf" });
    expect(wrong({ rfFrom: "2", rfTo: "1" }).message).toMatch(/«до» меньше, чем «от»/);
    expect(wrong({ rfFrom: "0,001" }).message).toMatch(/вне диапазона 0,01…5/);
    expect(wrong({ rfStep: "0,001" }).message).toBe("Оболочек больше 200.");
    expect(wrong({ rfMode: "list", rfList: "" }).message).toBe("Не задан список RF.");
    expect(wrong({ rfMode: "list", rfList: "6" }).message).toMatch(/вне диапазона/);
    expect(rfOfRange({ rfFrom: "0,3", rfTo: "1,5", rfStep: "0,05" }, { ...DEFAULT_LIMITS, shells: 10 }).ok).toBe(false);
    expect(wrong({ prefix: "a-b" })).toMatchObject({ field: "prefix" });
    expect(wrong({ prefix: "x".repeat(41) })).toMatchObject({ field: "prefix" });
    expect(patchOf({ ...EMPTY_ECONOMICS, prefix: "Вар_1" }).prefix).toBe("Вар_1");
    for (const f of [wrong({ cM0: "x" }), wrong({ rfStep: "0" })]) expect(f.message + f.hint).toMatch(/[А-Яа-я]/);
  });
});

describe("the elements as text: «AU:г/т:2000:5:0,03; CU:%:9000:300»", () => {
  const text = "AU:г/т:2000:5:0,03; CU:%:9000:300";
  it("the rows, the request, the text back", () => {
    const rows = parseElements(text);
    expect(rows).toEqual([
      { field: "AU", unit: "g/t", price: "2000", sales: "5", royalty: "0,03" },
      { field: "CU", unit: "%", price: "9000", sales: "300", royalty: "" },
    ]);
    expect(patchOf({ ...EMPTY_ECONOMICS, elements: rows }).elements).toEqual([
      { field: "AU", unit: "g/t", price: 2000, sales: 5, royalty: 0.03 },
      { field: "CU", unit: "%", price: 9000, sales: 300, royalty: 0 },
    ]);
    expect(elementsText(rows)).toBe(text);
  });
  it("an empty place is the default (the unit of the field, no sales, no royalty), the codes are told in any spelling", () => {
    expect(parseElements("AU::2000")).toEqual([{ field: "AU", unit: "", price: "2000", sales: "", royalty: "" }]);
    expect(parseElements("AU").map((r) => r.unit)).toEqual([""]);
    expect(parseElements("AU:gpt:1; CU:PPM:2; X:ppb:3").map((r) => r.unit)).toEqual(["g/t", "ppm", "ppb"]);
    expect(parseElements("AU:кг:1")[0].unit).toBe("кг"); // told by the check of the form, not guessed
    expect(elementsText([{ field: "AU", unit: "", price: "", sales: "", royalty: "" }])).toBe("AU");
  });
  it("the call changes what it names and leaves the rest as the scenario has it", () => {
    const base = readParams(caseParams("rocks_field"));
    const f = economicsFormOfArgs({ c_m0: 3, rf: "0,8; 1" }, base);
    const merged = mergeParams(base, patchOf(f));
    expect(merged.mining).toEqual({ ...base.mining, c_m0: 3 });
    expect(merged.rf).toEqual([0.8, 1]);
    expect(merged.elements).toEqual(base.elements);
    expect(merged.default_density).toBe(2.6);
    expect(economicsFormOfArgs({ density: null }, readParams(caseParams("t8117"))).densityMode).toBe("auto");
    expect(economicsFormOfArgs({ density: "SG" }, base)).toMatchObject({ densityMode: "field", densityField: "SG" });
    expect(economicsFormOfArgs({ density: "2,65" }, base)).toMatchObject({ densityMode: "value", densityValue: "2,65" });
    expect(economicsFormOfArgs({ z_ref: null, rf_to: 1.2 }, base)).toMatchObject({ zRef: "", rfMode: "range", rfTo: "1,2" });
  });
  it("the price label follows the unit: $/унц for г/т, ppm, ppb; $/т for %", () => {
    expect(priceLabel("g/t", null)).toBe("$/унц");
    expect(priceLabel(null, "%")).toBe("$/т");
    expect(priceLabel(null, "г/т")).toBe("$/унц");
    expect(priceLabel(null, null)).toBe("за единицу металла");
    expect(proposedElements({ elements: [{ field: "AU", unit: "g/t" }], density: null, categories: [], z_ref: null, k_auto: null, terrains: [] })).toEqual([{ field: "AU", unit: "", price: "", sales: "", royalty: "" }]);
    expect(proposedElements(null)).toEqual([]);
  });
});

describe("the window «Сценарий»: the relief, the restrictor, the solver", () => {
  const cases = CASES.map((n) => [n, readParams(caseParams(n))] as const);
  it.each(cases)("%s: the form of the canonical params gives the same relief, restrictor and solver back", (_n, base: PitParams) => {
    const got = scenarioPatch(scenarioFormOfParams(base, "Н", null));
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect(got.patch).toEqual({ model_id: base.model_id, terrain_id: base.terrain_id, filter_id: base.filter_id, restrict: base.restrict, solver: base.solver });
  });
  it("the restrictor of the case «restricted»: a tree of rules goes through the constructor and comes back the same", () => {
    const base = readParams(caseParams("restricted"));
    const f = scenarioFormOfParams(base, "Вариант", 17);
    expect(f).toMatchObject({ editId: 17, terrainId: 5, filterId: 5, restrictOn: true, solver: "hpf" });
    const p = scenarioPatch(f);
    expect(p.ok && p.patch.restrict).toEqual(base.restrict);
  });
  it("the flat arguments: a new scenario → `pit.new`, an existing one → `pit.relief` (the relief and the restrictor only); the declared parameters accept them", () => {
    const base = readParams(caseParams("restricted"));
    const fresh = scenarioArgs({ ...scenarioFormOfParams(base, " Вариант ", null) });
    const edit = scenarioArgs(scenarioFormOfParams(base, "Вариант", 17));
    if (!fresh.ok || !edit.ok) throw new Error("the form is wrong");
    expect(fresh.args).toEqual({ name: "Вариант", model_id: 12, solver: "hpf", terrain_id: 5, filter_id: 5, restrict: JSON.stringify(base.restrict) });
    expect(edit.args).toEqual({ scenario_id: 17, terrain_id: 5, filter_id: 5, restrict: JSON.stringify(base.restrict) });
    expect(validateArgs(by["pit.new"], fresh.args)).toBeNull();
    expect(validateArgs(by["pit.relief"], edit.args)).toBeNull();
    const back = scenarioFormOfArgs(fresh.args, EMPTY_SCENARIO);
    expect(back.ok && back.form).toMatchObject({ name: "Вариант", modelId: 12, terrainId: 5, filterId: 5, restrictOn: true, solver: "hpf" });
    const none = scenarioArgs(EMPTY_SCENARIO);
    expect(none.ok && none.args).toEqual({ solver: "auto", terrain_id: null, filter_id: null, restrict: null });
    expect(hasScenarioKeys({})).toBe(false);
    expect(hasScenarioKeys({ name: "Н" })).toBe(true);
    expect(hasRelief({ scenario_id: 17 })).toBe(false);
    expect(hasRelief({ terrain_id: null })).toBe(true);
  });
  it("a call clears with null, keeps what it does not name, and tells a wrong argument", () => {
    const keep = scenarioFormOfParams(readParams(caseParams("restricted")), "Н", 17);
    const cleared = scenarioFormOfArgs({ terrain_id: null, restrict: null }, keep);
    expect(cleared.ok && cleared.form).toMatchObject({ terrainId: null, filterId: 5, restrictOn: false });
    const bad = scenarioFormOfArgs({ restrict: "не json" }, keep);
    expect(bad).toMatchObject({ ok: false, issue: { field: "restrict" } });
    expect(!bad.ok && bad.issue.hint).toMatch(/Пример/);
    expect(scenarioFormOfArgs({ restrict: "[]" }, keep).ok).toBe(false);
    expect(scenarioFormOfArgs({ solver: "gurobi" }, keep)).toMatchObject({ ok: false, issue: { field: "solver" } });
    expect(scenarioFormOfArgs({ terrain_id: 0 }, keep)).toMatchObject({ ok: false, issue: { field: "relief" } });
    expect(scenarioFormOfArgs({ model_id: 1.5 }, keep)).toMatchObject({ ok: false, issue: { field: "model" } });
  });
  it("a restrictor with a wrong rule is told and not sent", () => {
    const f = scenarioFormOfParams(readParams(caseParams("restricted")), "Н", 17);
    const r = scenarioPatch({ ...f, restrict: { cond: { field: "", op: ">", value: "", list: "" } } });
    expect(r).toMatchObject({ ok: false, issue: { field: "restrict", param: "restrict" } });
    expect(!r.ok && r.issue.message).toMatch(/[А-Яа-я]/);
  });
});
