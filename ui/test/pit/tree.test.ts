// The section «Карьер» → «Сценарии» of the tree and the «Свойства» of a scenario (Э21, 21.18; ТЗ 8.11.5; ADR 0028 решения 20, 23, 29; Д-35) on the REAL answers of the core
// (`scenarios_list`, `scenario_get` of `core/tests/data/pit/contract_answers.json`): a row for a scenario with the badge of the graph and the state in words, the facts of the result
// (the model and its hash, the solver, K and the error of the angle, the objective, the time, the warnings), the menu of the commands of the registry (an unavailable one says why),
// a click chooses the scenario, a scenario that is gone is not selected any more.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { openMenu, menuStore } from "../../src/shell/ContextMenu";
import { readScenario, readScenarioRow, type ScenarioRow } from "../../src/modules/pit/answers";
import type { Dependent } from "../../src/modules/lineage/api";
import { ScenarioProps } from "../../src/modules/pit/ScenarioProps";
import { childrenOf, PitTree, ScenarioRows } from "../../src/modules/pit/ScenariosTree";
import { askRename, NODE_KIND, noResultReason, NO_RESULT_TEXT, pitField, rebuildReason, rowTitle, scenarioActions, scenarioFacts, scenarioLabel, scenariosByName, selectScenarioRow, shellCount, stateTitle } from "../../src/modules/pit/scenarioModel";
import { loadScenarios, pitStore, selectScenario } from "../../src/modules/pit/state";
import { answer, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(async () => {
  core = installCore();
  ({ app, unbind } = newApp());
  await waitFor(() => pitStore.get().scenariosLoaded);
});
afterEach(() => teardown(unbind));

const ROW: ScenarioRow = readScenarioRow((answer("scenarios_list").body as { scenarios: unknown[] }).scenarios[0])!;
const DETAIL = readScenario(answer("scenario_get").body)!;
const withApp = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const plain = (s: string) => s.replace(/[  ]/g, " ");
const graph = { stale: false, stale_reason: null, source_lost: false, pinned: false };

describe("the rows of the tree", () => {
  it("the real list: a row for the scenario with its name, the tooltip with the model, the shells and the value", () => {
    pitStore.set({ scenarios: [ROW], scenariosLoaded: true });
    const html = plain(withApp(createElement(PitTree)));
    expect(html).toContain('data-testid="tree-pit-scenarios"');
    expect(html).toContain("Сценарии");
    expect(html).toContain('data-testid="tree-pit-scenario-17"');
    expect(html).toContain('aria-label="Сценарий карьера"');
    expect(html).toContain("по модели «Рудное тело», оболочек 3; ценность последней оболочки 47 031 701 $");
    expect(html).toContain("двойной щелчок — pit-by-pit в нижней панели");
    expect(html).toMatch(/<span class="[^"]*count[^"]*">1<\/span>/);
  });

  it("the states: «считается», «ошибка», «нет результата», «параметры изменены» in the label, «устарел» and «источник удалён» in the place of the badge until the graph answers; by name", () => {
    const rows: ScenarioRow[] = [
      { ...ROW, id: 3, name: "Б считается", state: "running" },
      { ...ROW, id: 4, name: "В ошибка", state: "error", has_result: false },
      { ...ROW, id: 5, name: "А устарел", graph: { stale: true, stale_reason: "изменено поле AU", source_lost: false, pinned: false } },
      { ...ROW, id: 6, name: "Г без модели", graph: { ...graph, source_lost: true } },
      { ...ROW, id: 7, name: "Д новый", has_result: false, shells: null, value: null },
      { ...ROW, id: 8, name: "Е правка", params_changed: true },
    ];
    pitStore.set({ scenarios: rows, scenariosLoaded: true });
    const html = withApp(createElement(ScenarioRows));
    expect(html.indexOf("А устарел")).toBeLessThan(html.indexOf("Б считается")); // by name
    expect(html).toContain("Б считается · считается");
    expect(html).toContain("В ошибка · ошибка");
    expect(html).toContain("Д новый · нет результата");
    expect(html).toContain("Е правка · параметры изменены");
    expect(html).toMatch(/А устарел[\s\S]*устарел<\/span>/);
    expect(html).toContain("источник удалён");
    expect(scenarioLabel(ROW)).toBe("Сценарий карьера");
    expect(stateTitle(ROW)).toBe("");
    expect(scenariosByName([{ id: 2, name: "Б" }, { id: 1, name: "Б" }, { id: 3, name: "А" }]).map((r) => r.id)).toEqual([3, 1, 2]);
  });

  it("no scenarios: the word that says how to make one; before the list is read — «Чтение»", () => {
    pitStore.set({ scenarios: [], scenariosLoaded: true });
    expect(withApp(createElement(ScenarioRows))).toContain("Нет сценариев");
    pitStore.set({ scenarios: [], scenariosLoaded: false });
    expect(withApp(createElement(ScenarioRows))).toContain("Чтение сценариев…");
  });

  it("the tooltip tells a scenario that is running, has no result or has changed params", () => {
    expect(rowTitle({ ...ROW, state: "running" })).toContain("считается");
    expect(rowTitle({ ...ROW, has_result: false, shells: null, value: null })).toContain("нет результата");
    expect(rowTitle({ ...ROW, params_changed: true })).toContain("параметры изменены");
  });

  it("the children of a scenario are the wireframes built from it (depth 1): the DTMs of the shells and the solids; the fields and the deeper objects are not", () => {
    const dep = (kind: string, id: number, depth: number): Dependent => ({ kind, id, sub: "", name: `${kind}${id}`, depth });
    expect(childrenOf([dep("wireframe", 41, 1), dep("wireframe", 42, 1), dep("bm_field", 12, 1), dep("object:resource_report", 2, 2), dep("wireframe", 50, 2)]).map((n) => n.id)).toEqual([41, 42]);
    expect(childrenOf([])).toEqual([]);
  });
});

describe("a click and the menu", () => {
  it("a click chooses the scenario: its «Свойства» open; the kind is the tree's own", () => {
    selectScenarioRow(app, ID.scenario);
    expect(app.store.get().selected).toEqual({ kind: "pit_scenario", id: ID.scenario });
    expect(app.store.get().workspace).toMatchObject({ right: true, rightTab: "properties" });
  });

  it("a scenario that is gone from the list (deleted here or elsewhere) is not selected any more; one that is there stays", async () => {
    selectScenarioRow(app, ID.scenario);
    await loadScenarios();
    expect(app.store.get().selected).toEqual({ kind: "pit_scenario", id: ID.scenario });
    core.force("GET /pit/scenarios", { status: 200, body: { scenarios: [] } });
    await loadScenarios();
    expect(app.store.get().selected).toBeNull();
  });

  it("the menu: the commands of the registry; every item runs one", () => {
    const items = scenarioActions(app, ROW);
    expect(items.map((i) => i.label)).toEqual(["Pit-by-pit", "Запустить", "Проверить шаблон", "Экономика…", "Породы…", "Углы бортов…", "Рельеф и ограничитель…", "Календарь…", "Перестроить", "Переименовать…", "Удалить…"]);
    const calls: [string, Record<string, unknown>][] = [];
    vi.spyOn(app, "run").mockImplementation(async (id: string, args?: Record<string, unknown>) => {
      calls.push([id, args ?? {}]);
      return undefined as never;
    });
    for (const i of items) if (i.label !== "Переименовать…") i.run?.();
    expect(calls).toEqual([
      ["pit.report", { scenario_id: 17 }],
      ["pit.run", { scenario_id: 17 }],
      ["pit.check", { scenario_id: 17 }],
      ["pit.economics", { scenario_id: 17 }],
      ["pit.rocks", { scenario_id: 17 }],
      ["pit.slopes", { scenario_id: 17 }],
      ["pit.relief", { scenario_id: 17 }],
      ["pit.calendar", { scenario_id: 17 }],
      ["lineage.rebuild", { kind: NODE_KIND, id: 17 }],
      ["pit.delete", { scenario_id: 17 }],
    ]);
    expect(NODE_KIND).toBe("object:pit_scenario");
    vi.restoreAllMocks();
  });

  it("a scenario without a result cannot be shown or rebuilt (the words of `pit_no_result`); one that is running cannot be rebuilt, renamed or deleted; a lost source cannot be rebuilt", () => {
    const none = scenarioActions(app, { ...ROW, has_result: false, shells: null, value: null });
    expect(none.find((i) => i.label === "Pit-by-pit")?.reason).toBe(NO_RESULT_TEXT);
    expect(none.find((i) => i.label === "Перестроить")?.reason).toMatch(/нет результата/);
    const running = scenarioActions(app, { ...ROW, state: "running" });
    for (const l of ["Перестроить", "Переименовать…", "Удалить…", "Экономика…"]) expect(running.find((i) => i.label === l)?.reason, l).toBeTruthy();
    expect(noResultReason(ROW)).toBeNull();
    expect(noResultReason(undefined)).toBeNull();
    expect(noResultReason({ ...ROW, has_result: false, state: "running" })).toBe("Сценарий считается: результата ещё нет");
    expect(rebuildReason({ ...ROW, graph: { ...graph, source_lost: true } })).toMatch(/Источник сценария удалён/);
    expect(rebuildReason(ROW)).toBeNull();
  });

  it("in a project open for reading: rename, delete and the changes of the params are not available, the report is", () => {
    app.store.set({ projectReadOnly: true });
    const items = scenarioActions(app, ROW);
    for (const l of ["Переименовать…", "Удалить…", "Экономика…", "Календарь…"]) expect(items.find((i) => i.label === l)?.reason, l).toMatch(/только для чтения/);
    expect(items.find((i) => i.label === "Pit-by-pit")?.reason).toBeNull();
    openMenu(10, 10, items);
    expect(menuStore.get().items?.length).toBe(11);
  });

  it("«Переименовать…» asks the name in our own window; the answer runs `pit.rename`", async () => {
    const calls: [string, Record<string, unknown>][] = [];
    const run = askRename(ROW, (id, args) => calls.push([id, args]));
    await waitFor(() => dialogStore.get().queue.length === 1);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("text");
    if (d.kind === "text") {
      expect(d.options.title).toBe("Переименовать сценарий карьера");
      expect(d.options.initial).toBe("Сценарий карьера");
      d.resolve("Вариант 2");
    }
    await run;
    expect(calls).toEqual([["pit.rename", { scenario_id: 17, name: "Вариант 2" }]]);
  });
});

describe("the facts of a scenario", () => {
  it("the real scenario: the model and the hash of its cells, the params hash, the solver, the shells, the template K and the error of the angle, the objective, the time, the warnings", () => {
    const facts = Object.fromEntries(scenarioFacts(DETAIL, ROW).map((f) => [f.key, plain(f.value)]));
    expect(facts.model).toBe("«Рудное тело», хеш 363ac79c03d1");
    expect(facts.hash).toBe("e3be8bbd3c1d");
    expect(facts.solver).toBe("HPF");
    expect(facts.shells).toBe("3, RF 0,80 … 1,20");
    expect(facts.template).toBe("K = 8 (подобран автоматически), ошибка угла до 1,44°");
    expect(facts.objective).toBe("не больше 7,56 $ (округление до целых, q = 0,01)");
    expect(facts.totals).toBe("руда 552 823 т, пустая 1 931 896 т, вскрыша 3,49, CF 36 229 123 $");
    expect(facts.seconds).toBe("8,4 с");
    expect(facts.warnings).toContain("Рельеф не задан: блоки не обрезаются по поверхности.");
    expect(shellCount(DETAIL)).toBe(3);
    expect(pitField(DETAIL)).toBe("PIT");
    expect(pitField({ ...DETAIL, meta: null, params: { ...DETAIL.params, prefix: "P1_" } })).toBe("P1_PIT");
  });

  it("a scenario with no result has the facts of its params only; no value is made up", () => {
    const fresh = { ...DETAIL, meta: null, has_result: false };
    const facts = scenarioFacts(fresh, ROW);
    expect(facts.map((f) => f.key)).toEqual(["model", "hash"]);
    expect(facts[0].value).toBe("«Рудное тело»");
    expect(shellCount(fresh)).toBe(0);
    expect(scenarioFacts({ ...fresh, params_hash: null }).find((f) => f.key === "hash")?.value).toBe("—");
  });
});

describe("«Свойства» of a scenario", () => {
  it("the name, the actions, the state; before the scenario is read — «Чтение»; every action is a command of the registry", () => {
    pitStore.set({ scenarios: [ROW], scenariosLoaded: true });
    const html = plain(withApp(createElement(ScenarioProps, { id: ID.scenario })));
    expect(html).toContain('data-testid="props-pit-scenario"');
    expect(html).toContain("Сценарий карьера");
    expect(html).toContain("Чтение сценария…");
    for (const id of ["props-pit-report", "props-pit-run", "props-pit-rebuild", "props-pit-rename", "props-pit-delete"]) expect(html, id).toContain(`data-testid="${id}"`);
    expect(html).not.toContain("Параметры изменены");
  });

  it("a scenario with changed params and one without a result are told; the buttons that cannot work are off and say why", () => {
    pitStore.set({ scenarios: [{ ...ROW, params_changed: true }], scenariosLoaded: true });
    const changed = withApp(createElement(ScenarioProps, { id: ID.scenario }));
    expect(changed).toContain("Параметры изменены: результат устарел, нужен новый запуск.");
    pitStore.set({ scenarios: [{ ...ROW, has_result: false, shells: null, value: null }], scenariosLoaded: true });
    const fresh = withApp(createElement(ScenarioProps, { id: ID.scenario }));
    expect(fresh).toContain("Результата нет: задайте цены, породы и углы и запустите расчёт.");
    expect(fresh).toMatch(/<button[^>]*disabled=""[^>]*data-testid="props-pit-report"/);
    expect(fresh).toContain("Недоступно: У сценария нет результата. Запустите расчёт сценария.");
    pitStore.set({ scenarios: [], scenariosLoaded: true });
    expect(withApp(createElement(ScenarioProps, { id: 99 }))).toContain("Сценария нет в списке: обновите раздел «Карьер»");
  });

  it("the scenario is read through `selectScenario` as the tree does: its params are the ones of the core", async () => {
    core.scenario.has_result = true;
    const d = await selectScenario(ID.scenario);
    expect(d?.meta).not.toBeNull();
    expect(shellCount(d!)).toBe(3);
    expect(d?.has_result).toBe(true);
  });
});
