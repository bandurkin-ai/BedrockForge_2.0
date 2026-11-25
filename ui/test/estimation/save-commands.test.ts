// Э18, 18.27в — the rule «команда = запись реестра»: the save of a row of «Параметры оценки» and of a plan are the commands `estimate.save` and
// `estimate.plan_save` (the ribbon, Ctrl+K, the keys, the assistant); the buttons of the wizard, of «Параметры оценки» and of the plan window run
// them through `app.registry`, the commands end in the same `saveWizard` and `writePlan`. The answers are the real ones of the core.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { openWindow, windowStore } from "../../src/shell/windows";
import { commands } from "../../src/modules/estimation/commands";
import { EMPTY_FORM, type EstimationForm } from "../../src/modules/estimation/form";
import { PARAMS_WINDOW } from "../../src/modules/estimation/ParamsWindow";
import { addItem, choosePlan, moveItem, planSaveArgs, planStore, setPlanName } from "../../src/modules/estimation/planState";
import { saveRowByCommand, savePlanByCommand } from "../../src/modules/estimation/saveCommands";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { loadVariogramModels, putForm, resetWizard, setForm, WIZARD_WINDOW, wizardStore } from "../../src/modules/estimation/wizardState";
import { A, FIELD, ID, installCore, newApp, PLANS, teardown, type Core } from "./harness";
import { PlanWindow } from "../../src/modules/estimation/PlanWindow";
import { Wizard } from "../../src/modules/estimation/Wizard";

let app: App;
let core: Core;
beforeEach(async () => {
  core = installCore();
  app = newApp();
  resetWizard();
  planStore.set({ modelId: null, planId: null, name: "", version: null, items: [], picked: [], dirty: false, saving: false, issue: null });
  await loadRows();
});
afterEach(() => {
  resetWizard();
  windowStore.set({ windows: [] });
  teardown();
});

const form = (patch: Partial<EstimationForm> = {}): EstimationForm => ({ ...EMPTY_FORM, modelId: ID.model, domainField: "ZONE", label: "окисл", setId: ID.set, table: ID.table, field: FIELD, domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] }, variogramId: ID.vario, ...patch });
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const exec = (id: string, args: Record<string, unknown> = {}, source: "user" | "claude" = "user") => app.registry.execute(id, args, source);

describe("the declarations", () => {
  it("both are in the catalog with their parameters; neither asks (`confirm`) and neither is for the interface only", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(Object.keys(by["estimate.save"].params!)).toEqual(["id", "as_new"]);
    expect(Object.keys(by["estimate.plan_save"].params!)).toEqual(["id", "model_id", "name", "items", "as_new"]);
    for (const id of ["estimate.save", "estimate.plan_save"]) {
      expect(by[id].confirm).toBeFalsy();
      expect(by[id].uiOnly).toBeFalsy();
      expect(by[id].title).toMatch(/[А-Яа-я]/);
    }
    const names = commandTools(commands).map((t) => t.name);
    expect(names).toEqual(expect.arrayContaining(["ui_estimate_save", "ui_estimate_plan_save"]));
  });
  it("they write to the project: not available while it is open for reading only", () => {
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("estimate.save")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("estimate.plan_save")).toBe("Проект открыт только для чтения");
    app.store.set({ projectReadOnly: false });
    expect(app.registry.reason("estimate.save")).toBeNull();
  });
});

describe("estimate.save", () => {
  it("without an open form there is nothing to save: a refusal in words, the core is not asked", async () => {
    putForm(form());
    const r = await exec("estimate.save");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Нет открытой формы оценки");
    expect(requests("POST /geostat/estimations")).toHaveLength(0);
  });
  it("the form of the wizard is written (POST), the row is edited next (PUT over the version); the log has the command", async () => {
    await loadVariogramModels();
    openWindow(WIZARD_WINDOW, "Оценка содержаний", "normal", {});
    putForm(form());
    const r = await exec("estimate.save");
    expect(r).toEqual({ ok: true, result: `Сохранено: «${A.estimation_post.body.name}», версия ${A.estimation_post.body.version}` });
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    expect(wizardStore.get()).toMatchObject({ dirty: false, editing: { id: A.estimation_post.body.id } });
    setForm({ name: "Другое имя" });
    expect((await exec("estimate.save", { id: A.estimation_post.body.id })).ok).toBe(true);
    expect(requests(`PUT /geostat/estimations/${A.estimation_post.body.id}`)).toHaveLength(1);
    expect(app.registry.entries().filter((e) => e.commandId === "estimate.save")).toHaveLength(2);
    // «Сохранить как новую»: a POST beside the open row
    expect((await exec("estimate.save", { as_new: true })).ok).toBe(true);
    expect(requests("POST /geostat/estimations")).toHaveLength(2);
  });
  it("the window «Параметры оценки» holds the same form; another `id` than the open row is refused before the core", async () => {
    await loadVariogramModels();
    openWindow(PARAMS_WINDOW, "Параметры оценки", "normal", {});
    putForm(form());
    const r = await exec("estimate.save", { id: 424242 });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("В форме открыта не строка №424242");
    expect(requests("POST /geostat/estimations")).toHaveLength(0);
  });
  it("a refusal of the core stands under its field (the issue of the wizard) and in the answer of the command", async () => {
    await loadVariogramModels();
    openWindow(WIZARD_WINDOW, "Оценка содержаний", "normal", {});
    putForm(form());
    core.force("POST /geostat/estimations", { status: 422, body: { code: "bad_param", param: "search.passes.0.n_max", message: "n_max: от 1 до 10 000.", hint: "Задайте n_max." } });
    const r = await exec("estimate.save");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("n_max: от 1 до 10 000.");
    expect(wizardStore.get().issue).toMatchObject({ code: "bad_param" });
  });
  it("the buttons of the windows go through the registry: «Сохранить» and «Сохранить как новую» are the command, in the log", async () => {
    await loadVariogramModels();
    openWindow(WIZARD_WINDOW, "Оценка содержаний", "normal", {});
    putForm(form());
    expect(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(Wizard, { winId: "w1" })))).toContain('data-testid="est-save"');
    await saveRowByCommand(app, false);
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    await saveRowByCommand(app, true);
    expect(requests("POST /geostat/estimations")).toHaveLength(2);
    const log = app.registry.entries().filter((e) => e.commandId === "estimate.save");
    expect(log.map((e) => [e.source, e.ok, e.args])).toEqual([["user", true, { as_new: true }], ["user", true, {}]]);
  });
});

describe("estimate.plan_save", () => {
  it("a new plan from the arguments: POST with the model and the rows; no window needed", async () => {
    const r = await exec("estimate.plan_save", { model_id: ID.model, items: `${ID.row}; ${ID.row2}`, name: "Мой план" });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("сохранён");
    expect(requests("POST /geostat/estimation-plans")[0].body).toEqual({ name: "Мой план", model_id: ID.model, items: [ID.row, ID.row2] });
    expect(planStore.get().planId).toBeNull(); // the window of the plan was not holding it
  });
  it("a stored plan: PUT over its version, the order of the rows; without `items` the rows stay", async () => {
    expect((await exec("estimate.plan_save", { id: ID.plan, items: `${ID.row2}; ${ID.row}` })).ok).toBe(true);
    expect(requests(`PUT /geostat/estimation-plans/${ID.plan}`)[0].body).toEqual({ items: [ID.row2, ID.row], expected_version: PLANS.plans[0].version });
    expect((await exec("estimate.plan_save", { id: ID.plan, name: "Новое название" })).ok).toBe(true);
    expect(requests(`PUT /geostat/estimation-plans/${ID.plan}`)[1].body).toEqual({ name: "Новое название", expected_version: PLANS.plans[0].version });
  });
  it("the window follows when it holds the plan that was written; the working copy is clean after it", async () => {
    choosePlan(ID.plan);
    moveItem(ID.row2, -1);
    setPlanName("Порядок 2");
    expect(planStore.get().dirty).toBe(true);
    core.plans = { plans: [{ ...PLANS.plans[0], name: "Порядок 2", items: [ID.row2, ID.row], version: 2 }] };
    const args = planSaveArgs();
    expect(args).toEqual({ id: ID.plan, model_id: ID.model, name: "Порядок 2", items: `${ID.row2}; ${ID.row}` });
    expect((await exec("estimate.plan_save", args)).ok).toBe(true);
    expect(planStore.get()).toMatchObject({ planId: ID.plan, version: 2, dirty: false, saving: false, issue: null });
  });
  it("a new plan made in the window: the same command, then the window holds the plan written", async () => {
    planStore.set({ modelId: ID.model });
    addItem(ID.row3 ?? ID.row);
    core.plans = { plans: [...PLANS.plans, { id: ID.plan1, name: "План · Модель", model_id: ID.model, items: [planStore.get().items[0]], version: 1 }] };
    expect((await exec("estimate.plan_save", planSaveArgs())).ok).toBe(true);
    expect(planStore.get()).toMatchObject({ planId: ID.plan1, dirty: false });
  });
  it("«Сохранить как новый»: `as_new` writes a copy beside `id` under another name", async () => {
    choosePlan(ID.plan);
    moveItem(ID.row, 1);
    core.plans = { plans: [...PLANS.plans, { id: ID.plan1, name: "x", model_id: ID.model, items: [ID.row2, ID.row], version: 1 }] };
    expect((await exec("estimate.plan_save", planSaveArgs(planStore.get(), true))).ok).toBe(true);
    expect(requests(`PUT /geostat/estimation-plans/${ID.plan}`)).toHaveLength(0);
    expect(requests("POST /geostat/estimation-plans")[0].body).toMatchObject({ name: `${A.plan.body.name} (копия)`, model_id: ID.model });
  });
  it("a conflict of versions stands under the bar of the window with «Перечитать» and in the answer of the command", async () => {
    choosePlan(ID.plan);
    moveItem(ID.row, 1);
    core.force(`PUT /geostat/estimation-plans/${ID.plan}`, { status: 409, body: { code: "version_conflict", version: 2, message: "План изменён в другом окне.", hint: "Перечитайте план." } });
    const r = await exec("estimate.plan_save", planSaveArgs());
    expect(r.ok).toBe(false);
    expect(r.result).toContain("План изменён в другом окне.");
    expect(planStore.get().issue).toMatchObject({ code: "version_conflict", reread: true });
  });
  it("refusals before the core: no model, a plan or a model that is not there, a row number that is not a number", async () => {
    let r = await exec("estimate.plan_save", { items: "1; 2" });
    expect(r.result).toContain("Блочная модель не выбрана");
    r = await exec("estimate.plan_save", { id: 777777, items: "1" });
    expect(r.result).toContain("План оценки №777777 не найден");
    r = await exec("estimate.plan_save", { model_id: 999999, items: "1" });
    expect(r.result).toContain("Блочная модель №999999 не найдена");
    r = await exec("estimate.plan_save", { model_id: ID.model, items: "41; x" });
    expect(r.result).toContain("Номера строк плана: нужны целые числа");
    expect(requests("POST /geostat/estimation-plans")).toHaveLength(0);
  });
  it("the plan of another window is not shown the refusal of a plan it does not hold", async () => {
    choosePlan(ID.plan);
    core.force("POST /geostat/estimation-plans", { status: 422, body: { code: "bad_param", message: "Нет такой строки.", hint: "Проверьте номера." } });
    const r = await exec("estimate.plan_save", { model_id: ID.model, items: "5" });
    expect(r.ok).toBe(false);
    expect(planStore.get().issue).toBeNull();
    expect(planStore.get().saving).toBe(false);
  });
  it("the buttons of the plan window go through the registry with the working copy as the arguments", async () => {
    choosePlan(ID.plan);
    moveItem(ID.row, 1);
    expect(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(PlanWindow, { winId: "w1" })))).toContain('data-testid="plan-save"');
    await savePlanByCommand(app, false);
    expect(requests(`PUT /geostat/estimation-plans/${ID.plan}`)).toHaveLength(1);
    const entry = app.registry.entries().find((e) => e.commandId === "estimate.plan_save");
    expect(entry).toMatchObject({ source: "user", ok: true, args: { id: ID.plan, model_id: ID.model, items: `${ID.row2}; ${ID.row}` } });
    expect(estimationStore.get().plans.length).toBeGreaterThan(0);
  });
});
