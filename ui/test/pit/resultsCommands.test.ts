// The commands `pit.surfaces`, `pit.solid`, `pit.compare` and their windows (Э21, 21.19; ТЗ 8.11.5; ADR 0028 решения 25–27, 29, 30; docs/progress/e21-api.md §9–§11, §13, §16) through
// the stub of the core that answers with `core/tests/data/pit/contract_answers.json`: the declarations, the commands are unavailable without a result (with the reason), the
// arguments of the command = the body the core gets, the refusals in Russian with a hint, the course of the quick jobs, the windows drawn from the answers.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { renderPitWindow } from "../../src/modules/pit/bind";
import { windowOfCompare, windowOfSolid, windowOfSurfaces } from "../../src/modules/pit/bindResults";
import { commands, PIT_GROUPS } from "../../src/modules/pit/commands";
import { CompareWindow } from "../../src/modules/pit/CompareWindow";
import { PIT_COMPARE_WINDOW, PIT_SOLID_WINDOW, PIT_SURFACES_WINDOW, pickShells, resultsStore } from "../../src/modules/pit/resultsState";
import { val } from "../../src/modules/pit/resultsModel";
import { SolidWindow } from "../../src/modules/pit/SolidWindow";
import { SurfacesWindow } from "../../src/modules/pit/SurfacesWindow";
import { loadScenarios, pitStore, selectScenario } from "../../src/modules/pit/state";
import { answer, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";
import { installResults, type ResultsCore } from "./resultsHarness";

let app: App;
let core: Core;
let rc: ResultsCore;
let unbind: () => void;
beforeEach(async () => {
  core = installCore();
  rc = installResults(core);
  core.scenario.has_result = true;
  ({ app, unbind } = newApp());
  await waitFor(() => pitStore.get().scenariosLoaded);
});
afterEach(() => teardown(unbind));

const run = (id: string, args: Record<string, unknown> = {}, source: "user" | "claude" = "user") => app.registry.execute(id, args, source);
const sentTo = (suffix: string) => rc.sent.filter((r) => r.path.endsWith(suffix));
const rows = () => (answer("scenarios_list").body.scenarios as Record<string, unknown>[])[0];
const withRows = async (list: Record<string, unknown>[]) => {
  // the stub of the first file answers the list with its sample: the rows of a test are put into the store
  const base = pitStore.get().scenarios[0];
  pitStore.set({ scenarios: list.map((r) => ({ ...base, ...r })) as never, scenariosLoaded: true });
};
const html = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const drawn = (kind: "surfaces" | "solid" | "compare") => html(createElement(kind === "surfaces" ? SurfacesWindow : kind === "solid" ? SolidWindow : CompareWindow, { winId: "w" }));
const second = () => ({ ...rows(), id: 18, name: "Вариант 2" });

describe("the declarations", () => {
  const by = Object.fromEntries(commands.map((c) => [c.id, c]));
  it("the three commands are not staged; `pit.compare {a, b}`; `pit.solid` opens its window without `shell`", () => {
    for (const id of ["pit.surfaces", "pit.solid", "pit.compare"]) expect(by[id].stage, id).toBeUndefined();
    expect(Object.keys(by["pit.compare"].params ?? {})).toEqual(["a", "b"]);
    expect(Object.keys(by["pit.surfaces"].params ?? {})).toEqual(["scenario_id", "shells"]);
    expect(Object.keys(by["pit.solid"].params ?? {})).toEqual(["scenario_id", "shell", "name"]);
    expect(by["pit.solid"].required).toBeUndefined();
    for (const id of ["pit.surfaces", "pit.solid", "pit.compare"]) expect(by[id].description, id).toMatch(/[А-Яа-я]/);
  });
  it("the ribbon has the three buttons in «Результаты»; the assistant sees them with their parameters", () => {
    const names = PIT_GROUPS[2].buttons.map((b) => b.command);
    expect(names).toEqual(expect.arrayContaining(["pit.surfaces", "pit.solid", "pit.compare"]));
    const tools = commandTools(commands);
    expect(Object.keys(tools.find((t) => t.name === "ui_pit_compare")?.input_schema.properties ?? {})).toEqual(["a", "b"]);
  });
  it("the shell binds them: no reason with the stage; the window layer draws their windows", () => {
    for (const id of ["pit.surfaces", "pit.solid", "pit.compare"]) expect(app.registry.reason(id), id).toBeNull();
    for (const kind of [PIT_SURFACES_WINDOW, PIT_SOLID_WINDOW, PIT_COMPARE_WINDOW]) expect(renderPitWindow({ id: "w", kind } as never), kind).not.toBeNull();
  });
});

describe("unavailable without a result: the reason says what to do", () => {
  it("no scenario chosen: not a reason while a scenario has a result (the command asks for `scenario_id`); none has: run one; a scenario without a result: run it", async () => {
    for (const id of ["pit.surfaces", "pit.solid"]) expect(app.registry.reason(id), id).toBeNull();
    const none = await run("pit.surfaces", {}, "claude");
    expect(none.result).toContain("Не задан сценарий карьера");
    await withRows([{ has_result: false }]);
    for (const id of ["pit.surfaces", "pit.solid"]) expect(app.registry.reason(id), id).toBe("Нет сценариев с результатом: запустите расчёт");
    core.scenario.has_result = false;
    await selectScenario(ID.scenario);
    for (const id of ["pit.surfaces", "pit.solid"]) expect(app.registry.reason(id), id).toBe("У сценария нет результата: запустите расчёт");
    core.scenario.has_result = true;
    await withRows([{ has_result: true }]);
    await selectScenario(ID.scenario);
    for (const id of ["pit.surfaces", "pit.solid"]) expect(app.registry.reason(id), id).toBeNull();
  });
  it("a scenario that is being calculated is told so", async () => {
    await selectScenario(ID.scenario);
    await withRows([{ state: "running" }]);
    expect(app.registry.reason("pit.surfaces")).toBe("Сценарий считается: дождитесь конца расчёта");
  });
  it("a project open for reading: the surfaces and the solid are unavailable, the comparison works", async () => {
    await selectScenario(ID.scenario);
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("pit.surfaces")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("pit.solid")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("pit.compare")).toBeNull();
  });
  it("the comparison: unavailable when no scenario has a result", async () => {
    expect(app.registry.reason("pit.compare")).toBeNull();
    await withRows([{ has_result: false }]);
    expect(app.registry.reason("pit.compare")).toBe("Нет сценариев с результатом: запустите расчёт");
  });
  it("the buttons of the windows say the same in their title (disabled with the reason)", async () => {
    await withRows([{ has_result: false }]);
    await selectScenario(ID.scenario);
    core.scenario.has_result = false;
    await selectScenario(ID.scenario);
    for (const kind of ["surfaces", "solid"] as const) {
      const h = drawn(kind);
      expect(h).toContain("У сценария нет результата");
      expect(h).toMatch(/data-testid="pit-(surf|solid)-run"[^>]*disabled=""|disabled=""[^>]*data-testid="pit-(surf|solid)-run"/);
    }
  });
});

describe("`pit.surfaces`", () => {
  it("from the interface without `shells` the window opens, nothing is sent", async () => {
    await selectScenario(ID.scenario);
    const r = await run("pit.surfaces", { scenario_id: ID.scenario });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Открыто окно «Поверхности оболочек карьера»");
    expect(windowOfSurfaces()).not.toBeNull();
    expect(rc.sent).toHaveLength(0);
  });
  it("`shells` «1; 3»: the body is the numbers; the job is followed; the answer lists the DTMs; the state keeps the result", async () => {
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1; 3" });
    expect(r.ok).toBe(true);
    expect(sentTo("/surfaces").map((x) => [x.method, x.path, x.body])).toEqual([["POST", `/pit/scenarios/${ID.scenario}/surfaces`, { shells: [1, 3] }]]);
    expect(r.result).toContain("Поверхности оболочек сценария «Сценарий карьера» построены: ЦТМ 3");
    expect(r.result).toContain("Оболочка 1 (RF 0,8)");
    const s = resultsStore.get().surfaces;
    expect([s.phase, s.scenarioId, s.result?.wireframes.length]).toEqual(["done", ID.scenario, 3]);
  });
  it("from the assistant without `shells`: all the shells (an empty body); the interface is not opened", async () => {
    const r = await run("pit.surfaces", { scenario_id: ID.scenario }, "claude");
    expect(r.ok).toBe(true);
    expect(sentTo("/surfaces")[0].body).toEqual({});
    expect(windowOfSurfaces()).toBeNull();
  });
  it.each([["9", "Оболочки «9» нет: в результате их 3"], ["x", "«x» — не номер оболочки."], ["", "Не заданы номера оболочек."]])("`shells` = «%s»: told in Russian with a hint, nothing is sent", async (shells, text) => {
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells });
    expect(r.ok).toBe(false);
    expect(r.result).toContain(text);
    expect(r.result).toMatch(/Номера оболочек — целые от 1 до 3/);
    expect(rc.sent).toHaveLength(0);
  });
  it("a scenario without a result: 409 `pit_no_result` is told before the request", async () => {
    core.scenario.has_result = false;
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("нет результата");
    expect(r.result).toContain("Запустите расчёт");
    expect(rc.sent).toHaveLength(0);
  });
  it("a project open for reading: 423, nothing is sent", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(r.ok).toBe(false);
    expect(rc.sent).toHaveLength(0);
  });
  it("a refusal of the core stands in the window with its words and its code (409 `pit_running`)", async () => {
    rc.force(`POST /pit/scenarios/${ID.scenario}/surfaces`, { status: 409, body: { code: "pit_running", message: "Сценарий считается.", hint: "Дождитесь конца расчёта." } });
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Сценарий считается. Дождитесь конца расчёта.");
    const s = resultsStore.get().surfaces;
    expect([s.phase, s.error?.code]).toEqual(["failed", "pit_running"]);
  });
  it("a job that failed (`pit_empty`) is a refusal with the words of the core; a job that was cancelled is not an error", async () => {
    rc.jobs.surfaces = { status: "failed", result: null, error: { code: "pit_empty", message: "У выбранных оболочек нет поверхности.", hint: "Проверьте каркас рельефа в параметрах сценария." } };
    const bad = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Проверьте каркас рельефа");
    rc.jobs.surfaces = { status: "cancelled", result: null, error: null };
    const cut = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(cut.ok).toBe(true);
    expect(cut.result).toContain("отменено");
    expect(resultsStore.get().surfaces.phase).toBe("cancelled");
  });
  it("a second run while one goes is refused", async () => {
    resultsStore.set((s) => ({ surfaces: { ...s.surfaces, phase: "running" } }));
    const r = await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("уже строятся");
  });
});

describe("`pit.solid`", () => {
  it("from the interface without `shell` the window opens; from the assistant — a refusal that names the number of the shell", async () => {
    await selectScenario(ID.scenario);
    expect((await run("pit.solid", { scenario_id: ID.scenario })).result).toContain("Открыто окно «Каркас карьера»");
    expect(windowOfSolid()).not.toBeNull();
    const r = await run("pit.solid", { scenario_id: ID.scenario }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Не задан номер оболочки.");
    expect(r.result).toContain("от 1 до 3");
    expect(rc.sent).toHaveLength(0);
  });
  it("`shell` and `name`: the body of the request; the answer — the volume, the volume of the blocks, the difference, the method", async () => {
    const r = await run("pit.solid", { scenario_id: ID.scenario, shell: 2, name: " Мой каркас " });
    expect(r.ok).toBe(true);
    expect(sentTo("/solid").map((x) => [x.path, x.body])).toEqual([[`/pit/scenarios/${ID.scenario}/solid`, { shell: 2, name: "Мой каркас" }]]);
    expect(r.result).toContain(`объём ${val(41000000)} м³, замкнут`);
    expect(r.result).toContain(`объём блоков оболочки ${val(41200000)} м³, расхождение ${val(0.49)} %`);
    expect(r.result).toContain("тело между рельефом и поверхностью оболочки");
    expect(resultsStore.get().solid.result?.parts).toBe(1);
  });
  it("without a name the body has none (the core names the solid)", async () => {
    await run("pit.solid", { scenario_id: ID.scenario, shell: 1 });
    expect(sentTo("/solid")[0].body).toEqual({ shell: 1 });
  });
  it.each([[7, "Оболочки «7» нет: в результате их 3"], [0, "Оболочки «0» нет"]])("`shell` = %s is wrong: told in Russian with a hint, nothing is sent", async (shell, text) => {
    const r = await run("pit.solid", { scenario_id: ID.scenario, shell });
    expect(r.ok).toBe(false);
    expect(r.result).toContain(text);
    expect(r.result).toContain("Передайте shell");
    expect(rc.sent).toHaveLength(0);
  });
  it("`pit_solid_failed` (the solid is not closed): in Russian with a hint, in the request and in the job", async () => {
    const failed = { code: "pit_solid_failed", message: "Не удалось построить замкнутый каркас карьера: шов не сварен.", hint: "Выберите другую оболочку или проверьте рельеф сценария." };
    rc.force(`POST /pit/scenarios/${ID.scenario}/solid`, { status: 422, body: failed });
    const r1 = await run("pit.solid", { scenario_id: ID.scenario, shell: 2 });
    expect(r1.ok).toBe(false);
    expect(r1.result).toContain("Выберите другую оболочку");
    rc.jobs.solid = { status: "failed", result: null, error: failed };
    const r2 = await run("pit.solid", { scenario_id: ID.scenario, shell: 2 });
    expect(r2.ok).toBe(false);
    expect(r2.result).toContain(failed.message);
    expect(resultsStore.get().solid.error?.code).toBe("pit_solid_failed");
    // the job that fails with no words gets the Russian ones of the interface
    rc.jobs.solid = { status: "failed", result: null, error: { code: "pit_solid_failed", message: "", hint: "" } };
    const r3 = await run("pit.solid", { scenario_id: ID.scenario, shell: 2 });
    expect(r3.result).toMatch(/Не удалось построить каркас карьера\. Повторите команду/);
  });
});

describe("`pit.compare`", () => {
  it("without parameters the window opens; one scenario is not enough", async () => {
    expect((await run("pit.compare")).result).toContain("Открыто окно «Сравнить сценарии карьера»");
    expect(windowOfCompare()).not.toBeNull();
    const r = await run("pit.compare", { a: ID.scenario });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Нужны оба сценария");
    expect(rc.sent).toHaveLength(0);
  });
  it("`a` and `b`: the body of the request is {a, b}; the answer tells the counts, the NPV and the blocks; the state keeps the answer", async () => {
    await withRows([rows(), second()]);
    const r = await run("pit.compare", { a: ID.scenario, b: 18 });
    expect(r.ok).toBe(true);
    expect(sentTo("/compare").map((x) => x.body)).toEqual([{ a: ID.scenario, b: 18 }]);
    expect(r.result).toContain("«Сценарий карьера» (A, №17) и «Вариант 2» (B, №18)");
    expect(r.result).toContain("только в B 1");
    expect(r.result).toContain("NPV лучшего случая A");
    const c = resultsStore.get().compare;
    expect([c.phase, c.a, c.b, c.answer?.shells.length]).toEqual(["done", ID.scenario, 18, 4]);
  });
  it("A = A is allowed", async () => {
    const r = await run("pit.compare", { a: ID.scenario, b: ID.scenario });
    expect(r.ok).toBe(true);
    expect(sentTo("/compare")[0].body).toEqual({ a: ID.scenario, b: ID.scenario });
  });
  it("a scenario that is not in the project: 404 in Russian; a scenario without a result: 409 in words; nothing is sent", async () => {
    const gone = await run("pit.compare", { a: ID.scenario, b: 999 });
    expect(gone.ok).toBe(false);
    expect(gone.result).toContain("Сценарий карьера №999 не найден.");
    await withRows([rows(), { ...second(), has_result: false }]);
    const none = await run("pit.compare", { a: ID.scenario, b: 18 });
    expect(none.ok).toBe(false);
    expect(none.result).toContain("У сценария «Вариант 2» нет результата.");
    expect(rc.sent).toHaveLength(0);
  });
  it("a refusal of the core stands in the window (409 `pit_no_result`)", async () => {
    await withRows([rows(), second()]);
    rc.force("POST /pit/compare", { status: 409, body: answer("pit_no_result").body });
    const r = await run("pit.compare", { a: ID.scenario, b: 18 });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("У сценария нет результата.");
    expect(resultsStore.get().compare.error?.code).toBe("pit_no_result");
  });
  it("works in a project open for reading", async () => {
    await withRows([rows(), second()]);
    app.store.set({ projectReadOnly: true });
    expect((await run("pit.compare", { a: ID.scenario, b: 18 })).ok).toBe(true);
  });
});

describe("the windows are drawn from the answers", () => {
  it("«Поверхности»: the shells of the scenario with their RF, all picked; a pick is kept; the result below", async () => {
    await selectScenario(ID.scenario);
    let h = drawn("surfaces");
    expect(h).toContain("Выбрано оболочек: 3 из 3");
    for (const k of [1, 2, 3]) expect(h).toContain(`pit-surf-shell-${k}`);
    expect(h).toContain("0,8");
    expect(h).not.toContain("pit-surf-result");
    pickShells(ID.scenario, [2]);
    h = drawn("surfaces");
    expect(h).toContain("Выбрано оболочек: 1 из 3");
    await run("pit.surfaces", { scenario_id: ID.scenario, shells: "2" });
    h = drawn("surfaces");
    expect(h).toContain("pit-surf-result");
    expect(h).toContain("Построено ЦТМ: 3");
    expect(h).toContain("Сценарий карьера — RF 1,0");
    expect(h).toContain("19 602".replace(" ", " "));
  });
  it("«Поверхности»: a pick of another scenario is not this one's", async () => {
    await selectScenario(ID.scenario);
    pickShells(999, [1]);
    expect(drawn("surfaces")).toContain("Выбрано оболочек: 3 из 3");
  });
  it("«Каркас»: the shell at RF = 1 is proposed; the result — the volume, the volume of the blocks, the difference, the parts, the method; the error in words", async () => {
    await selectScenario(ID.scenario);
    expect(drawn("solid")).toMatch(/<option value="2" selected="">2 · RF 1/);
    await run("pit.solid", { scenario_id: ID.scenario, shell: 2 });
    const h = drawn("solid");
    for (const id of ["volume", "blocks", "diff", "parts", "method", "closed"]) expect(h).toContain(`pit-solid-r-${id}`);
    expect(h).toContain(val(41000000));
    expect(h).toContain("тело между рельефом и поверхностью оболочки");
    rc.jobs.solid = { status: "failed", result: null, error: { code: "pit_solid_failed", message: "Каркас не замкнут.", hint: "Выберите другую оболочку." } };
    await run("pit.solid", { scenario_id: ID.scenario, shell: 2 });
    const e = drawn("solid");
    expect(e).toContain('data-code="pit_solid_failed"');
    expect(e).toContain("Каркас не замкнут. Выберите другую оболочку.");
    expect(e).not.toContain("pit-solid-r-volume");
  });
  it("«Сравнить»: the choice of A and B, then the heads, the params, the shells by RF, the totals, the blocks and the chart; «только в B» is «—»", async () => {
    await withRows([rows(), second()]);
    expect(drawn("compare")).toContain("Выберите два сценария");
    await run("pit.compare", { a: ID.scenario, b: 18 });
    const h = drawn("compare");
    for (const id of ["card-A", "card-B", "params", "shells", "totals", "blocks", "chart", "row-1.3", "summary"]) expect(h, id).toContain(`pit-cmp-${id}`);
    expect(h).toContain("только в B");
    expect(h).toContain("Вариант 2");
    expect(h).toMatch(/data-testid="pit-cmp-row-1.3"[^>]*>.*?<td class="[^"]*num[^"]*">—<\/td>/);
    expect(h).not.toContain("pit-cmp-grids-differ");
  });
  it("«Сравнить»: the grids differ — the banner and no blocks (the reason is in words)", async () => {
    await withRows([rows(), second()]);
    const b = structuredClone(answer("compare_result").body) as Record<string, unknown>;
    b.same_grid = false;
    b.blocks = null;
    b.warnings = [{ code: "grids_differ", message: "Сетки различаются.", hint: "Блоки не сравниваются." }];
    rc.compare = { status: 200, body: b };
    await run("pit.compare", { a: ID.scenario, b: 18 });
    const h = drawn("compare");
    expect(h).toContain("pit-cmp-grids-differ");
    expect(h).toContain("Сетки различаются");
    expect(h).toContain("pit-cmp-blocks-none");
    expect(h).not.toContain("pit-cmp-warnings"); // the banner is the one word about it
  });
  it("another project is a clean window: the answers of the old one are gone", async () => {
    await withRows([rows(), second()]);
    await run("pit.compare", { a: ID.scenario, b: 18 });
    await run("pit.surfaces", { scenario_id: ID.scenario, shells: "1" });
    expect(resultsStore.get().compare.answer).not.toBeNull();
    const { projectStore } = await import("../../src/modules/projects/state");
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    await waitFor(() => resultsStore.get().compare.answer === null);
    expect([resultsStore.get().surfaces.result, resultsStore.get().picked]).toEqual([null, null]);
  });
});

describe("the list of the scenarios is read again", () => {
  it("`loadScenarios` brings the rows of the sample (the comparison offers only those with a result)", async () => {
    await loadScenarios();
    expect(pitStore.get().scenarios.filter((r) => r.has_result)).toHaveLength(1);
    expect(drawn("compare")).toContain("№17 · Сценарий карьера");
  });
});
