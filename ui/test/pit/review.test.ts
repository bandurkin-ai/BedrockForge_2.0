// The review of the interface of the pit (Э21, 21.21в; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 20, 29): a form of a window is applied only to the scenario it was read from, «Запустить» does not wait for
// a scenario that the windows do not show, a failed read is told in Russian (not «Сценарий загружается»), the panel's «Записать в сценарий» follows the read-only project, one writing of the numbers,
// a gap is «—» and never 0 or «замкнут: да», «Отменить расчёт» of the properties.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { readRunSummary, type ScenarioRow } from "../../src/modules/pit/answers";
import { openCalendarWindow, openEconomicsWindow, openRocksWindow, openSlopesWindow } from "../../src/modules/pit/bind";
import { CalendarWindow } from "../../src/modules/pit/CalendarWindow";
import { EconomicsWindow } from "../../src/modules/pit/EconomicsWindow";
import { checkText, runText, whole } from "../../src/modules/pit/format";
import { PitView, type PanelActions } from "../../src/modules/pit/PitPanel";
import { INITIAL_PANEL } from "../../src/modules/pit/panelState";
import { RocksWindow } from "../../src/modules/pit/RocksWindow";
import { formatNumber, readReport } from "../../src/modules/pit/reportModel";
import { readSolid } from "../../src/modules/pit/resultsAnswers";
import { solidText } from "../../src/modules/pit/resultsModel";
import { ScenarioProps } from "../../src/modules/pit/ScenarioProps";
import { selectScenarioRow } from "../../src/modules/pit/scenarioModel";
import { SlopesWindow } from "../../src/modules/pit/SlopesWindow";
import { SolidWindow } from "../../src/modules/pit/SolidWindow";
import { beginRun, forgetValidation, pitStore, refreshValidation, runReason, selectScenario } from "../../src/modules/pit/state";
import { answer, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(async () => {
  core = installCore();
  ({ app, unbind } = newApp());
  await waitFor(() => pitStore.get().scenariosLoaded);
  const row = pitStore.get().scenarios[0] as ScenarioRow;
  pitStore.set({ scenarios: [row, { ...row, id: ID.other, name: "Второй сценарий", has_result: false }] });
});
afterEach(() => teardown(unbind));

const withApp = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const plain = (s: string) => s.replace(/[\u00a0\u202f]/g, " ");
/** the tag of the element with the test id */
const tag = (html: string, testId: string): string => {
  const at = html.indexOf(`data-testid="${testId}"`);
  if (at < 0) throw new Error(`no ${testId}`);
  return html.slice(html.lastIndexOf("<", at), html.indexOf(">", at) + 1);
};
const putsTo = (id: number) => core.sent.filter((r) => r.method === "PUT" && r.path === `/pit/scenarios/${id}`);

describe("a form is applied to the scenario it was read from (the race of the windows)", () => {
  const WINDOWS = [
    { name: "Экономика", open: () => openEconomicsWindow(ID.scenario), el: () => createElement(EconomicsWindow, { winId: "w" }), apply: "pit-econ-apply" },
    { name: "Породы", open: () => openRocksWindow(ID.scenario), el: () => createElement(RocksWindow, { winId: "w" }), apply: "pit-rocks-apply" },
    { name: "Углы", open: () => openSlopesWindow(ID.scenario), el: () => createElement(SlopesWindow, { winId: "w" }), apply: "pit-slopes-apply" },
    { name: "Календарь", open: () => openCalendarWindow(ID.scenario), el: () => createElement(CalendarWindow, { winId: "w" }), apply: "pit-calendar-apply" },
  ];
  it.each(WINDOWS)("$name: opened for A, then B is chosen — «Применить» is off and says why; nothing is written into B", async ({ open, el, apply }) => {
    await open();
    expect(tag(withApp(el()), apply)).not.toContain("disabled");
    await selectScenario(ID.other); // the tree, `pit.economics {scenario_id: B}` or the selector of the results: the scenario of the windows is B now
    expect(pitStore.get().scenario?.id).toBe(ID.other);
    const html = plain(withApp(el()));
    expect(tag(html, apply)).toContain("disabled");
    expect(tag(html, apply)).toContain("Форма прочитана из сценария «Сценарий карьера», а выбран «Второй сценарий»");
    expect(html).toContain('data-testid="pit-stale-form"');
    expect(putsTo(ID.other)).toHaveLength(0);
    expect(putsTo(ID.scenario)).toHaveLength(0);
  });
  it("the window opened again for B reads B and is applicable; the form of A stays where it was read", async () => {
    await openRocksWindow(ID.scenario);
    await selectScenario(ID.other);
    await openRocksWindow(ID.other);
    expect(pitStore.get().formFor.rocks).toBe(ID.other);
    expect(pitStore.get().rocksForm.by).toBe("rules"); // the rules of B (`rocks_rules`), not the field of A
    const html = withApp(createElement(RocksWindow, { winId: "w" }));
    expect(tag(html, "pit-rocks-apply")).not.toContain("disabled");
    expect(html).not.toContain("pit-stale-form");
  });
  it("two windows keep their own scenario: the economics of A stays applicable while the rocks went to B", async () => {
    await openEconomicsWindow(ID.scenario);
    await openRocksWindow(ID.other); // the scenario of the windows is B now: the economics form still belongs to A
    expect(pitStore.get().formFor).toEqual({ econ: ID.scenario, rocks: ID.other });
    expect(tag(withApp(createElement(EconomicsWindow, { winId: "w" })), "pit-econ-apply")).toContain("disabled");
    expect(tag(withApp(createElement(RocksWindow, { winId: "w" })), "pit-rocks-apply")).not.toContain("disabled");
  });
  it("a command of another scenario does not take the windows away from the one they show", async () => {
    await openRocksWindow(ID.scenario);
    const r = await app.registry.execute("pit.rocks", { scenario_id: ID.other, rock_types: "[]" }, "claude");
    expect(r.ok).toBe(true);
    expect(putsTo(ID.other)).toHaveLength(1);
    expect(pitStore.get().scenario?.id).toBe(ID.scenario);
    expect(pitStore.get().validations[ID.other]).toBeDefined();
    expect(tag(withApp(createElement(RocksWindow, { winId: "w" })), "pit-rocks-apply")).not.toContain("disabled");
  });
});

describe("`pit.run` follows the scenario of the tree, not the one the windows show", () => {
  it("a window opened for another scenario does not leave «Сценарий загружается» for the chosen one", async () => {
    core.ready = true;
    selectScenarioRow(app, ID.scenario);
    await waitFor(() => !!pitStore.get().validations[ID.scenario]);
    expect(runReason(app)).toBeNull();
    await openRocksWindow(ID.other); // the windows show B, the tree has A
    expect(pitStore.get().scenario?.id).toBe(ID.other);
    expect(runReason(app)).toBeNull();
    expect(app.registry.reason("pit.run")).toBeNull();
  });
  it("the readiness of a scenario that changed elsewhere is not trusted: forgotten, then read again (`pit.changed`)", async () => {
    selectScenarioRow(app, ID.scenario);
    await waitFor(() => !!pitStore.get().validations[ID.scenario]);
    expect(runReason(app)).toMatch(/^Не выбрана/);
    core.ready = true;
    forgetValidation(ID.scenario);
    expect(runReason(app)).toBe("Сценарий загружается: подождите секунду");
    await refreshValidation(ID.scenario);
    expect(runReason(app)).toBeNull();
  });
});

describe("a failed read is told, not «Сценарий загружается»", () => {
  it("the model of the scenario is gone (404 of `validate`): the scenario stays, the words of the core are in `loadError`, in the reason and in the head of the windows", async () => {
    core.force("POST /pit/validate", { status: 404, body: { code: "block_model_not_found", message: "Блочная модель №12 не найдена.", hint: "Выберите другую модель командой «Рельеф и ограничитель»." } });
    selectScenarioRow(app, ID.scenario);
    await waitFor(() => pitStore.get().loadError !== null);
    expect(pitStore.get().scenario?.id).toBe(ID.scenario);
    expect(pitStore.get().loadError).toEqual({ id: ID.scenario, message: "Блочная модель №12 не найдена. Выберите другую модель командой «Рельеф и ограничитель»." });
    expect(runReason(app)).toBe("Блочная модель №12 не найдена. Выберите другую модель командой «Рельеф и ограничитель».");
    expect(plain(withApp(createElement(EconomicsWindow, { winId: "w" })))).toContain("Сценарий не прочитан: Блочная модель №12 не найдена.");
    expect(plain(withApp(createElement(RocksWindow, { winId: "w" })))).toContain('data-testid="pit-load-error"');
    // the next successful read takes the error away
    await selectScenario(ID.scenario);
    expect(pitStore.get().loadError).toBeNull();
    expect(runReason(app)).toMatch(/^Не выбрана/);
  });
  it("the scenario itself cannot be read (the core is out of reach): the error is told; a scenario that is gone leaves the state without an error", async () => {
    core.force(`GET /pit/scenarios/${ID.scenario}`, { status: 400, body: { code: "core_failed", message: "Ядро не ответило.", hint: "Повторите через секунду." } });
    await selectScenario(ID.scenario);
    expect(pitStore.get().loadError).toEqual({ id: ID.scenario, message: "Ядро не ответило. Повторите через секунду." });
    expect(runReason(app)).toBe("Ядро не ответило. Повторите через секунду.");
    core.force(`GET /pit/scenarios/${ID.scenario}`, { status: 404, body: { code: "pit_scenario_not_found", message: "Сценарий не найден.", hint: "Обновите список." } });
    await selectScenario(ID.scenario);
    expect(pitStore.get()).toMatchObject({ currentId: null, scenario: null, loadError: null });
  });
});

describe("«Свойства» and «Отменить расчёт»", () => {
  it("the buttons follow the readiness read after the render: «Запустить» is available when the scenario is ready", async () => {
    core.ready = true;
    selectScenarioRow(app, ID.scenario);
    expect(tag(withApp(createElement(ScenarioProps, { id: ID.scenario })), "props-pit-run")).toContain("disabled"); // not read yet
    await waitFor(() => !!pitStore.get().validations[ID.scenario]);
    expect(tag(withApp(createElement(ScenarioProps, { id: ID.scenario })), "props-pit-run")).not.toContain("disabled");
  });
  it("during the run of the scenario the button «Отменить расчёт» stops the job; another scenario's properties do not have it", async () => {
    expect(withApp(createElement(ScenarioProps, { id: ID.scenario }))).not.toContain("props-pit-cancel");
    beginRun(ID.scenario, "Сценарий карьера");
    pitStore.set((s) => ({ run: { ...s.run, job: ID.runJob } }));
    expect(withApp(createElement(ScenarioProps, { id: ID.scenario }))).toContain('data-testid="props-pit-cancel"');
    expect(withApp(createElement(ScenarioProps, { id: ID.other }))).not.toContain("props-pit-cancel");
    const { cancelRun } = await import("../../src/modules/pit/bind");
    await cancelRun();
    expect(core.sent.some((r) => r.method === "POST" && r.path === `/jobs/${ID.runJob.source}/${ID.runJob.id}/cancel`)).toBe(true);
  });
});

describe("the panel: «Записать в сценарий»", () => {
  const noop: PanelActions = { tab: () => undefined, mode: () => undefined, calendar: () => undefined, saveCalendar: () => undefined, show: () => undefined };
  const report = readReport(answer("report_get").body)!;
  const draw = (saveReason?: string | null) => renderToStaticMarkup(createElement(PitView, { report, panel: { ...INITIAL_PANEL }, actions: noop, saveReason }));
  it("a read-only project (423 after the click before): the button is off and the title is the reason of the command", () => {
    app.store.set({ projectReadOnly: true });
    const reason = app.registry.reason("pit.calendar");
    expect(reason).toBe("Проект открыт только для чтения");
    const t = tag(draw(reason), "pit-calendar-save");
    expect(t).toContain("disabled");
    expect(t).toContain('title="Проект открыт только для чтения"');
    expect(tag(draw(null), "pit-calendar-save")).not.toContain("disabled");
    expect(tag(draw(null), "pit-calendar-apply")).not.toContain("disabled"); // the view of NPV is a read: it stays
  });
});

describe("one writing of the numbers", () => {
  const run = readRunSummary(answer("run_result").body)!;
  it("the text of the run and the panel write a tonne and a dollar the same way (the one of the core)", () => {
    const last = run.shells[run.shells.length - 1];
    expect(runText(run)).toContain(`руда ${whole(last.ore_t)} т`);
    expect(whole(1234567.8)).toBe(formatNumber(1234567.8, 0));
    expect(plain(whole(1234567.8))).toBe("1 234 568");
    expect(plain(runText(run))).toContain(`ценность ${plain(formatNumber(last.value, 0))} $`);
  });
  it("a gap in a sentence is «—»: «руда — т», never «руда нет значения т»; a block count that is not known is not 0", () => {
    const gaps = { ...run, seconds: null, shells: [{ ...run.shells[0], blocks: null, ore_t: null, waste_t: null, value: null }], totals: { ore_t: null, waste_t: null, strip: null, cf: null } };
    const text = runText(gaps);
    expect(text).toContain("блоков —, руда — т, пустая — т, ценность — $");
    expect(text).not.toContain("нет значения");
    expect(checkText({ k: null, k_auto: false, tolerance: null, domains: [], warnings: [] } as never)).toBe("Шаблон проверен: K = —, допуск —.");
  });
  it("`blocks` of a shell that the answer does not have is `null`, not 0", () => {
    const r = readRunSummary({ ...(answer("run_result").body as Record<string, unknown>), shells: [{ k: 1, rf: 0.5 }] })!;
    expect(r.shells[0].blocks).toBeNull();
  });
  it("«замкнут» of the solid is told only when the answer says it: no key — «—», nothing claimed in the text", () => {
    const sample = answer("solid_result").body as Record<string, unknown>;
    expect(readSolid({ ...sample, closed: true })?.closed).toBe(true);
    expect(readSolid({ ...sample, closed: false })?.closed).toBe(false);
    const { closed: _omitted, ...without } = sample;
    const r = readSolid(without)!;
    expect(r.closed).toBeNull();
    expect(solidText(r)).not.toContain("замкнут");
    expect(solidText({ ...r, closed: true })).toContain(", замкнут");
    expect(solidText({ ...r, closed: false })).toContain(", не замкнут");
  });
  it("the window of the solid shows «—» for a closed that is not known", async () => {
    const { resultsStore } = await import("../../src/modules/pit/resultsState");
    const sample = answer("solid_result").body as Record<string, unknown>;
    const { closed: _omitted, ...without } = sample;
    const r = readSolid(without)!;
    resultsStore.set((s) => ({ solid: { ...s.solid, phase: "done", scenarioId: ID.scenario, result: r } }));
    pitStore.set({ currentId: ID.scenario });
    const html = withApp(createElement(SolidWindow, { winId: "w" }));
    expect(html).toMatch(/data-testid="pit-solid-r-closed">—</);
  });
});
