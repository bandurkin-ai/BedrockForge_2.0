// The commands `pit.*` and the windows «Сценарий карьера» and «Экономика карьера» (Э21, 21.16; ADR 0028 решения 20, 28–30; docs/progress/e21-api.md §3, §5, §6,
// §13, §16) through a stub of the core that answers with the answers of `core/tests/data/pit/contract_answers.json`: the declarations (the ribbon, Ctrl+K and
// the assistant read them), «Запустить» is unavailable until `validate` is ready and names the first missing, the request of the window = the body the core
// gets (the canonical params), the refusals 404 / 409 / 422 / 423 in Russian with a hint in their place of the form, the run, the rename, the deletion.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { cancelRun, openEconomicsWindow, openScenarioWindow, renderPitWindow, windowOfEconomics, windowOfScenario } from "../../src/modules/pit/bind";
import { commands, PIT_GROUPS } from "../../src/modules/pit/commands";
import { EconomicsWindow } from "../../src/modules/pit/EconomicsWindow";
import { economicsArgs, EMPTY_SCENARIO, scenarioFormOfParams } from "../../src/modules/pit/forms";
import { readParams } from "../../src/modules/pit/answers";
import { ScenarioWindow } from "../../src/modules/pit/ScenarioWindow";
import { currentRun, PIT_ECONOMICS_WINDOW, PIT_SCENARIO_WINDOW, pitStore, selectScenario, setEconForm } from "../../src/modules/pit/state";
import { answer, caseParams, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(async () => {
  core = installCore();
  ({ app, unbind } = newApp());
  await waitFor(() => pitStore.get().scenariosLoaded);
});
afterEach(() => teardown(unbind));

const sentOf = (method: string, path: string) => core.sent.filter((r) => r.method === method && r.path === path);
const html = (kind: "scenario" | "economics") => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(kind === "scenario" ? ScenarioWindow : EconomicsWindow, { winId: "w" })));
const run = (id: string, args: Record<string, unknown> = {}, source: "user" | "claude" = "user") => app.registry.execute(id, args, source);
const missing0 = (answer("validate_new").body.missing as { message: string }[])[0].message;
const defaultParams = () => answer("defaults").body.params as Record<string, unknown>;

describe("the declarations", () => {
  const ids = commands.map((c) => c.id);
  const by = Object.fromEntries(commands.map((c) => [c.id, c]));
  it("the commands of ADR 0028 решение 29, in the order of the ribbon; the signs of the contract", () => {
    expect(ids).toEqual(["pit.new", "pit.economics", "pit.rocks", "pit.slopes", "pit.relief", "pit.calendar", "pit.check", "pit.run", "pit.report", "pit.surfaces", "pit.solid", "pit.compare", "pit.show", "pit.rename", "pit.delete"]);
    expect(by["pit.delete"].confirm).toBe(true);
    expect(by["pit.new"].required).toBeUndefined(); // without arguments the window opens
    expect(by["pit.economics"].required).toBeUndefined();
    expect(by["pit.rename"].required).toEqual(["name"]);
    expect(by["pit.solid"].required).toEqual(["shell"]);
    expect(by["pit.show"].required).toEqual(["shell"]);
    for (const c of commands) {
      expect(c.title, c.id).toMatch(/[А-Яа-я]/);
      expect(c.description, c.id).toMatch(/[А-Яа-я]/);
      expect(c.id.startsWith("pit."), c.id).toBe(true);
    }
  });
  it("the assistant sees all the commands, the parameters in flat words; no key is declared", () => {
    const tools = commandTools(commands);
    expect(tools).toHaveLength(15);
    const eco = tools.find((t) => t.name === "ui_pit_economics");
    expect(Object.keys(eco?.input_schema.properties ?? {})).toEqual(expect.arrayContaining(["scenario_id", "elements", "density", "default_density", "c_m0", "z_ref", "h_gl", "c_gl", "rf", "rf_from", "rf_to", "rf_step", "prefix"]));
    expect(tools.find((t) => t.name === "ui_pit_delete")?.description).toContain("после подтверждения");
    expect(JSON.stringify(commands)).not.toMatch(/"key"/);
  });
  it("the ribbon: «Сценарий», «Расчёт», «Результаты»; every button is a command with a Russian label and a hint", () => {
    expect(PIT_GROUPS.map((g) => g.title)).toEqual(["Сценарий", "Расчёт", "Результаты"]);
    const buttons = PIT_GROUPS.flatMap((g) => g.buttons);
    for (const b of buttons) {
      expect(ids, b.command).toContain(b.command);
      expect(b.label + b.hint, b.command).toMatch(/[А-Яа-я]/);
    }
    expect(PIT_GROUPS[0].buttons.map((b) => b.command)).toEqual(["pit.new", "pit.economics", "pit.rocks", "pit.slopes", "pit.relief", "pit.calendar"]);
    expect(PIT_GROUPS[1].buttons.map((b) => b.command)).toEqual(["pit.check", "pit.run"]);
  });
  it("the commands of the windows of 21.18–21.19 wait for their lanes: unavailable with the stage; the commands of 21.16–21.17 are bound", () => {
    for (const id of ["pit.report", "pit.surfaces", "pit.solid", "pit.compare", "pit.show"]) expect(app.registry.reason(id), id).toMatch(/появится на этапе Э21/);
    for (const id of ["pit.new", "pit.economics", "pit.rocks", "pit.slopes", "pit.relief", "pit.calendar", "pit.check", "pit.rename", "pit.delete"]) expect(app.registry.reason(id), id).toBeNull();
  });
});

describe("«Запустить» (`pit.run`) is unavailable until `validate` is ready; the hint is the first missing", () => {
  it("no scenario: the reason says to choose or create one", () => {
    expect(app.registry.reason("pit.run")).toBe("Выберите или создайте сценарий карьера");
  });
  it("a scenario that is not ready: the first `missing`; ready: available", async () => {
    await selectScenario(ID.scenario);
    expect(pitStore.get().validate?.ready).toBe(false);
    expect(app.registry.reason("pit.run")).toBe(missing0);
    expect(missing0).toBe("Не выбрана блочная модель.");
    const r = await run("pit.run");
    expect(r).toEqual({ ok: false, result: missing0 });
    expect(sentOf("POST", `/pit/scenarios/${ID.scenario}/run`)).toHaveLength(0);
    core.ready = true;
    await selectScenario(ID.scenario);
    expect(app.registry.reason("pit.run")).toBeNull();
    expect(sentOf("POST", "/pit/validate").at(-1)?.body.params).toEqual(core.scenario.params); // validate reads the params of the scenario as the core keeps them
  });
  it("a project open for reading: not available; a scenario that is chosen in the tree is read", async () => {
    core.ready = true;
    app.store.set({ selected: { kind: "pit_scenario", id: ID.scenario } as never });
    await waitFor(() => pitStore.get().validate !== null);
    expect(app.registry.reason("pit.run")).toBeNull();
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("pit.run")).toBe("Проект открыт только для чтения");
  });
});

describe("`pit.new`", () => {
  it("without arguments the window opens (the model chosen in the tree, else the first); the window shows the form", async () => {
    const r = await run("pit.new");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Сценарий карьера»" });
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_SCENARIO_WINDOW)).toHaveLength(1);
    expect(pitStore.get().scenarioForm).toEqual({ ...EMPTY_SCENARIO, modelId: ID.model });
    const text = html("scenario");
    for (const id of ["pit-scenario-window", "pit-name", "pit-model", "pit-solver", "pit-terrain", "pit-filter", "pit-restrict-on", "pit-apply", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("Рудное тело");
    expect(text).toContain("нет ЦТМ в проекте");
    expect(text).toContain("Создать");
    await run("pit.new");
    expect(windowStore.get().windows.filter((w) => w.kind === PIT_SCENARIO_WINDOW)).toHaveLength(1); // one window
    expect(renderPitWindow(windowStore.get().windows[0])).not.toBeNull();
    expect(renderPitWindow({ ...windowStore.get().windows[0], kind: "other" })).toBeNull();
  });
  it("with arguments the scenario is created: the body is the defaults of the core with the model, the relief and the restrictor put in", async () => {
    const r = await run("pit.new", { name: "Вариант 1", model_id: ID.model, terrain_id: ID.dtm, filter_id: ID.filter, restrict: JSON.stringify({ cond: { field: "DOM", op: "=", value: "A" } }), solver: "hpf" });
    expect(r.ok).toBe(true);
    expect(sentOf("POST", "/pit/scenarios")[0].body).toEqual({
      name: "Вариант 1",
      params: { ...defaultParams(), model_id: ID.model, terrain_id: ID.dtm, filter_id: ID.filter, restrict: { cond: { field: "DOM", op: "=", value: "A" } }, solver: "hpf" },
    });
    expect(r.result).toContain("Сценарий «Вариант 1» №17 создан.");
    expect(r.result).toContain("Для запуска не хватает: Не выбрана блочная модель");
    expect(pitStore.get().currentId).toBe(ID.scenario);
    expect(pitStore.get().scenario?.id).toBe(ID.scenario);
  });
  it("without a name the core names it; a model that is not there, a wrong tree, a name that is too long are told in Russian before anything is sent", async () => {
    expect((await run("pit.new", { model_id: ID.model })).ok).toBe(true);
    expect(sentOf("POST", "/pit/scenarios")[0].body).not.toHaveProperty("name");
    const n = sentOf("POST", "/pit/scenarios").length;
    const gone = await run("pit.new", { model_id: 99 });
    expect(gone).toEqual({ ok: false, result: "Блочная модель №99 не найдена. Номера моделей — в разделе «Блочные модели» дерева." });
    expect(pitStore.get().scenarioIssue).toMatchObject({ field: "model" });
    const bad = await run("pit.new", { model_id: ID.model, restrict: "не json" });
    expect(bad.result).toMatch(/^Ограничитель — не дерево правил в JSON\. Пример:/);
    expect((await run("pit.new", { name: "я".repeat(201) })).result).toMatch(/Имя длиннее 200 знаков/);
    expect(sentOf("POST", "/pit/scenarios")).toHaveLength(n);
  });
  it("the refusal of the core is told with its hint and put in its place of the form; a project open for reading cannot create", async () => {
    core.force("POST /pit/scenarios", { status: 422, body: { code: "bad_param", message: "Рельеф не годится.", hint: "Выберите ЦТМ.", param: "terrain_id" } });
    const r = await run("pit.new", { model_id: ID.model, terrain_id: 3 });
    expect(r).toEqual({ ok: false, result: "Рельеф не годится. Выберите ЦТМ." });
    expect(pitStore.get().scenarioIssue).toMatchObject({ field: "relief", param: "terrain_id" });
    app.store.set({ projectReadOnly: true });
    expect((await run("pit.new", { model_id: ID.model })).result).toBe("Проект открыт только для чтения");
  });
});

describe("`pit.economics`", () => {
  beforeEach(async () => {
    core.scenario.params = caseParams("t8117");
    await selectScenario(ID.scenario);
  });

  it("with the economics in the call the scenario is changed: the whole canonical params go with the version read, what the call does not name stays", async () => {
    const r = await run("pit.economics", { elements: "AU:г/т:2200:5; CU:%:9000:300", c_m0: 3, rf_from: 0.5, rf_to: 1.5, rf_step: 0.25 });
    expect(r.ok).toBe(true);
    const put = sentOf("PUT", `/pit/scenarios/${ID.scenario}`)[0].body;
    expect(put.expected_version).toBe(2);
    expect(put.params).toEqual({
      ...caseParams("t8117"),
      elements: [
        { field: "AU", unit: "g/t", price: 2200, sales: 5, royalty: 0 },
        { field: "CU", unit: "%", price: 9000, sales: 300, royalty: 0 },
      ],
      mining: { c_m0: 3, z_ref: null, h_gl: 10, c_gl: 0 },
      rf: [0.5, 0.75, 1, 1.25, 1.5],
    });
    expect(r.result).toContain("Сценарий «Сценарий карьера» обновлён (версия 3).");
    expect(r.result).toContain("Для запуска не хватает:");
    expect(pitStore.get().scenario?.version).toBe(3);
  });
  it("a scenario with a result becomes «параметры изменены»: the answer says that a new run is needed", async () => {
    core.scenario.has_result = true;
    const r = await run("pit.economics", { density: "SG" });
    expect(r.result).toContain("результат устарел, нужен новый запуск");
    expect(sentOf("PUT", `/pit/scenarios/${ID.scenario}`)[0].body.params).toMatchObject({ density: "SG" });
  });
  it("a wrong argument is told with its hint in its place and nothing is sent; the scenario that is not there is a refusal", async () => {
    const bad = await run("pit.economics", { elements: "AU:кг:1" });
    expect(bad.ok).toBe(false);
    expect(bad.result).toMatch(/^Единица «кг» не из списка\. Единица содержания — г\/т, ppm, ppb или %/);
    expect(pitStore.get().econIssue).toMatchObject({ field: "elements", param: "elements.0.unit" });
    expect((await run("pit.economics", { rf: "0,8; 9" })).result).toMatch(/RF 9 вне диапазона 0,01…5/);
    expect((await run("pit.economics", { rf_step: 0 })).result).toMatch(/Шаг RF должен быть больше нуля/);
    expect(sentOf("PUT", `/pit/scenarios/${ID.scenario}`)).toHaveLength(0);
    const no = await run("pit.economics", { scenario_id: ID.noScenario, c_m0: 1 });
    expect(no.result).toBe("Сценарий карьера №999 не найден. Номера сценариев — в разделе «Карьер» → «Сценарии» дерева.");
  });
  it("409 `version_conflict` and 422 of the core are told in Russian with the hint; the place is kept", async () => {
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 409, body: { code: "version_conflict", message: "Сценарий изменён в другом окне.", hint: "Откройте его заново.", version: 5 } });
    expect(await run("pit.economics", { c_m0: 2 })).toEqual({ ok: false, result: "Сценарий изменён в другом окне. Откройте его заново." });
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 422, body: answer("bad_param").body });
    const r = await run("pit.economics", { c_m0: 2 });
    expect(r.result).toBe("Оболочек больше 200. Увеличьте шаг или сузьте диапазон RF.");
    expect(pitStore.get().econIssue).toMatchObject({ field: "rf", param: "rf.step" });
  });
  it("without the economics the window opens with the scenario's params in the form; «Применить» = the same request as the command", async () => {
    core.scenario.params = caseParams("rocks_field");
    await selectScenario(ID.scenario);
    expect((await run("pit.economics")).result).toBe("Открыто окно «Экономика карьера»");
    expect(windowOfEconomics()).not.toBeNull();
    const form = pitStore.get().econForm;
    expect(form.elements.map((e) => [e.field, e.unit, e.price, e.sales, e.royalty])).toEqual([["AU", "", "2000", "5", "0,03"], ["CU", "%", "9000", "300", "0"]]);
    expect(form).toMatchObject({ densityMode: "auto", defaultDensity: "2,6", cM0: "2,5", zRef: "30", hGl: "10", cGl: "0,02", rfMode: "range" });
    const text = html("economics");
    for (const id of ["pit-econ-window", "pit-elements", "pit-el-AU", "pit-el-unit-AU", "pit-el-price-AU", "pit-el-sales-AU", "pit-el-roy-AU", "pit-el-CU", "pit-density-auto", "pit-density-field-on", "pit-density-value-on", "pit-default-density", "pit-c-m0", "pit-z-ref", "pit-h-gl", "pit-c-gl", "pit-rf-range", "pit-rf-list", "pit-rf-from", "pit-rf-to", "pit-rf-step", "pit-rf-count", "pit-prefix", "pit-econ-apply", "pit-ready", "pit-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("Сценарий «Сценарий карьера»");
    expect(text).toContain("Модель «Рудное тело»".replace("Модель", "модель"));
    expect(text).toContain("Оболочек: 25 из 200.");
    expect(text).toContain("$/унц");
    expect(text).toContain("Для запуска не хватает: Не выбрана блочная модель.");
    // the window runs the command with the arguments of its form: the body is the params of the scenario with the price changed
    setEconForm({ elements: form.elements.map((e) => (e.field === "AU" ? { ...e, price: "2 100" } : e)) });
    const got = economicsArgs(pitStore.get().econForm, ID.scenario);
    if (!got.ok) throw new Error(JSON.stringify(got.issue));
    expect((await run("pit.economics", got.args)).ok).toBe(true);
    const params = sentOf("PUT", `/pit/scenarios/${ID.scenario}`)[0].body.params as Record<string, unknown>;
    const want = caseParams("rocks_field") as { elements: { price: number }[] };
    expect(params).toEqual({ ...want, elements: [{ ...want.elements[0], price: 2100 }, want.elements[1]] });
  });
  it("a scenario without a model: the window says so and the proposal of the core is taken with a button; the rows of a field that is not in the model are shown", async () => {
    core.scenario.params = { ...caseParams("t8117"), elements: [] };
    await selectScenario(ID.scenario);
    await openEconomicsWindow(ID.scenario);
    expect(html("economics")).toContain("Взять поля по умолчанию");
    core.scenario.params = { ...caseParams("t8117"), elements: [{ field: "ZN", unit: null, price: 1, sales: 0, royalty: 0 }] };
    await openEconomicsWindow(ID.scenario);
    expect(html("economics")).toContain("(нет в модели)");
  });
});

describe("`pit.relief`", () => {
  beforeEach(async () => {
    core.scenario.params = caseParams("restricted");
    await selectScenario(ID.scenario);
  });
  it("without arguments the window of the scenario opens with its relief and restrictor; the form is the one of the params", async () => {
    const r = await run("pit.relief");
    expect(r.result).toBe("Открыто окно «Сценарий карьера» для сценария «Сценарий карьера»");
    expect(pitStore.get().scenarioForm).toMatchObject({ editId: ID.scenario, terrainId: 5, filterId: 5, restrictOn: true });
    const text = html("scenario");
    expect(text).toContain("Сценарий №17");
    expect(text).toContain("Применить");
    expect(text).not.toContain('data-testid="pit-name"'); // the name, the model and the solver are the window of a new scenario
    expect(windowOfScenario()).not.toBeNull();
  });
  it("with arguments the relief and the restrictor are changed (null takes them away); the rest of the params is the scenario's", async () => {
    const r = await run("pit.relief", { terrain_id: null, restrict: null });
    expect(r.ok).toBe(true);
    expect(sentOf("PUT", `/pit/scenarios/${ID.scenario}`)[0].body.params).toEqual({ ...caseParams("restricted"), terrain_id: null, restrict: null });
    expect((await run("pit.relief", { terrain_id: ID.dtm })).ok).toBe(true);
    expect((sentOf("PUT", `/pit/scenarios/${ID.scenario}`).at(-1)?.body.params as { terrain_id: number }).terrain_id).toBe(ID.dtm);
  });
  it("the window of the new scenario can be opened over the window of the relief (one window, its title follows)", async () => {
    await run("pit.relief");
    await openScenarioWindow({ ...EMPTY_SCENARIO, modelId: ID.model });
    const w = windowStore.get().windows.filter((x) => x.kind === PIT_SCENARIO_WINDOW);
    expect(w).toHaveLength(1);
    expect(w[0].title).toBe("Сценарий карьера");
    expect(scenarioFormOfParams(readParams(caseParams("restricted")), "Н", 17).editId).toBe(17);
  });
});

describe("`pit.run`, `pit.check`", () => {
  beforeEach(async () => {
    core.ready = true;
    core.scenario.params = caseParams("t8117");
    await selectScenario(ID.scenario);
  });

  it("the job is followed to its end: the answer is the text of the result, the run keeps it", async () => {
    const r = await run("pit.run", { solver: "ortools" });
    expect(r.ok).toBe(true);
    expect(sentOf("POST", `/pit/scenarios/${ID.scenario}/run`)[0].body).toEqual({ solver: "ortools" });
    expect(r.result).toContain("Сценарий «Сценарий карьера» рассчитан (решатель HPF): оболочек 3");
    expect(currentRun()).toMatchObject({ phase: "done", scenarioId: ID.scenario });
    expect(currentRun().result?.shells).toHaveLength(3);
    const again = await run("pit.run");
    expect(again.ok).toBe(true);
    expect(sentOf("POST", `/pit/scenarios/${ID.scenario}/run`)[1].body).toEqual({});
  });
  it("a refusal before the start (422 `pit_not_ready`) and a job that failed are told with their words; nothing is kept as a result", async () => {
    core.force(`POST /pit/scenarios/${ID.scenario}/run`, { status: 422, body: answer("pit_not_ready").body });
    const r = await run("pit.run");
    expect(r).toEqual({ ok: false, result: "Сценарий не готов к расчёту: Не задана цена элемента AU. Задайте недостающие параметры и повторите." });
    expect(currentRun()).toMatchObject({ phase: "failed", result: null });
    core.job = { status: "failed", result: null, error: { code: "pit_empty", message: "Нет блоков с положительной ценностью.", hint: "Поднимите цены или RF." } };
    expect(await run("pit.run")).toEqual({ ok: false, result: "Нет блоков с положительной ценностью. Поднимите цены или RF." });
  });
  it("«Отменить»: the job is cancelled, nothing is written; a second start while it goes is refused", async () => {
    core.job = { status: "running", result: null, error: null };
    const first = run("pit.run");
    await vi.waitFor(() => expect(currentRun().job).toEqual(ID.runJob));
    const busy = await run("pit.run", {}, "claude");
    expect(busy.result).toBe("Сценарий уже считается");
    await cancelRun();
    expect(await first).toEqual({ ok: true, result: "Расчёт сценария «Сценарий карьера» отменён: ничего не записано" });
    expect(currentRun()).toMatchObject({ phase: "cancelled", result: null });
  });
  it("`pit.check`: the template of the scenario is checked, the text tells K, the offsets and the angle error", async () => {
    const r = await run("pit.check");
    expect(r.ok).toBe(true);
    expect(sentOf("POST", "/pit/check")[0].body).toEqual({ params: caseParams("t8117") });
    expect(r.result).toContain("Шаблон проверен: K = 8 (подобран автоматически)");
    expect(r.result).toContain("смещений 17, ошибка угла до 1,44°");
    core.checkJob = { status: "failed", result: null, error: { code: "variable_grid", message: "Оптимизация карьера требует постоянного шага сетки.", hint: "Регуляризуйте модель." } };
    expect((await run("pit.check")).result).toBe("Оптимизация карьера требует постоянного шага сетки. Регуляризуйте модель.");
  });
  it("another project: the windows start clean (the scenario and the result of the first one do not stand beside the second)", async () => {
    await run("pit.run");
    expect(currentRun().phase).toBe("done");
    const { projectStore } = await import("../../src/modules/projects/state");
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 1 });
    expect(currentRun().phase).toBe("idle");
    expect(pitStore.get()).toMatchObject({ currentId: null, scenario: null, validate: null });
  });
});

describe("`pit.rename`, `pit.delete`", () => {
  beforeEach(async () => {
    await selectScenario(ID.scenario);
  });
  it("the new name goes with the version read and no params; a name that is taken is told", async () => {
    const r = await run("pit.rename", { name: " Вариант 2 " });
    expect(r).toEqual({ ok: true, result: "Сценарий «Сценарий карьера» переименован в «Вариант 2»" });
    expect(sentOf("PUT", `/pit/scenarios/${ID.scenario}`)[0].body).toEqual({ name: "Вариант 2", expected_version: 2 });
    core.force(`PUT /pit/scenarios/${ID.scenario}`, { status: 409, body: { code: "name_taken", message: "Имя «Вариант» уже занято.", hint: "Выберите другое имя.", param: "name" } });
    expect((await run("pit.rename", { name: "Вариант" })).result).toBe("Имя «Вариант» уже занято. Выберите другое имя.");
    expect((await run("pit.rename", { name: "  " })).result).toBe("Не задано имя сценария. Передайте name — новое имя.");
  });
  it("`pit.delete`: the user is asked, «Отмена» leaves the scenario; «Удалить» asks the graph and deletes it; the state forgets it", async () => {
    const answerDialog = async (ok: boolean) => {
      await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
      const d = dialogStore.get().queue[0];
      expect(d.kind).toBe("confirm");
      if (d.kind === "confirm") {
        expect(d.options.title).toBe("Удалить сценарий карьера");
        expect(d.options.message).toContain("поля PIT и ORE в модели и ЦТМ оболочек останутся");
        d.resolve(ok);
      }
      closeDialog(d.id);
    };
    const first = run("pit.delete", { scenario_id: ID.scenario });
    await answerDialog(false);
    expect((await first).result).toBe("Отменено");
    expect(sentOf("DELETE", `/pit/scenarios/${ID.scenario}`)).toHaveLength(0);
    const again = run("pit.delete", { scenario_id: ID.scenario });
    await answerDialog(true);
    const r = await again;
    expect(r).toEqual({ ok: true, result: "Сценарий «Сценарий карьера» удалён" });
    expect(sentOf("DELETE", `/pit/scenarios/${ID.scenario}`)).toHaveLength(1);
    expect(sentOf("GET", "/lineage/impact").some((x) => x.query.includes("pit_scenario"))).toBe(true); // what is built from the scenario is asked first
    expect(pitStore.get()).toMatchObject({ currentId: null, scenario: null });
  });
  it("a project open for reading cannot rename or delete", async () => {
    app.store.set({ projectReadOnly: true });
    expect((await run("pit.rename", { name: "Н" }, "claude")).result).toMatch(/только для чтения/);
    expect((await run("pit.delete", {}, "claude")).result).toMatch(/только для чтения/);
  });
});

describe("the windows are plain forms of the module", () => {
  it("the window of the economics of a scenario that is not chosen says so; both windows are told the kind of the shell's layer", async () => {
    expect(html("economics")).toContain("Сценарий не выбран");
    await openEconomicsWindow(ID.scenario);
    expect(windowStore.get().windows.some((w) => w.kind === PIT_ECONOMICS_WINDOW)).toBe(true);
    expect(renderPitWindow(windowStore.get().windows.find((w) => w.kind === PIT_ECONOMICS_WINDOW)!)).not.toBeNull();
  });
});
