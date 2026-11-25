// The window «Сводка реализаций» (Э18б, 18.35) on the real answers of the core (`summary`, `sis_cat_summary`, `summary_empty`): the reader
// (a gap stays a gap), the tables of a continuous variable and of the categories (the names of the stored row), the facts, `GET /simulations/{id}/summary`
// and its 409 `no_realizations`, the command `simulate.summary`, the window drawn.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { CoreError } from "../../src/shell/api";
import { categoriesOf, loadSummary, readSummary } from "../../src/modules/simulation/api";
import { loadDetails, loadRows } from "../../src/modules/simulation/state";
import { chooseSummary, num, pct, summaryFacts, summaryStore, summaryTable, SUMMARY_WINDOW } from "../../src/modules/simulation/summaryState";
import { renderSimulationWindow } from "../../src/modules/simulation/bind";
import { SummaryView } from "../../src/modules/simulation/SummaryWindow";
import { flat, INFOS, installSimCore, newApp, S, SID, teardown, tick, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(teardown);

const SUM = S.summary.body;
const CAT = S.sis_cat_summary.body;
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
/** The window as the layer of the shell draws it. */
const windowHtml = () => render(renderSimulationWindow({ id: "w", kind: SUMMARY_WINDOW, title: "", x: 0, y: 0, w: 1, h: 1, z: 1 } as never) as ReturnType<typeof createElement>);

describe("the reader and the tables", () => {
  it("a continuous variable: the statistics of each realization and of all together, as the core gave them", () => {
    const s = readSummary(SUM);
    expect(s).toMatchObject({ simulation_id: SID.sgs, nodes: 200, realizations: 5, samples: { rows: 500, used: 147 }, cond: { min: 24, mean: 24, max: 24 }, codes: null, perShares: null });
    expect(s.per).toHaveLength(5);
    expect(s.per![0]).toEqual(SUM.summary.per[0]);
    expect(s.all).toEqual(SUM.summary.all);
    expect(s.counts).toEqual({ degenerate: 0, free: 0, on_sample: 0, gap: null });
  });
  it("the categories: the shares of every category by realization, the codes in the order of the core", () => {
    const s = readSummary(CAT);
    expect(s.per).toBeNull();
    expect(s.codes).toEqual([1, 2, 3]);
    expect(s.perShares).toHaveLength(4);
    expect(s.perShares![0]).toEqual({ n: 200, shares: [0.2, 0.38, 0.42] });
    expect(s.shares).toEqual(CAT.summary.all.shares);
  });
  it("a gap of the answer is a gap: never 0", () => {
    const s = readSummary({ ...SUM, summary: { per: [{ n: 3, mean: null, std: null, min: 1, max: null }], all: { mean: null, std: null, min: null, max: null } }, cond: null, seconds: null });
    expect(s.per![0]).toEqual({ n: 3, mean: null, std: null, min: 1, max: null });
    expect(s.cond).toBeNull();
    const t = summaryTable(s, null);
    expect(t.rows[0].cells).toEqual(["1", "3", "—", "—", "1", "—"]);
    expect(t.rows[1].cells.slice(2)).toEqual(["—", "—", "—", "—"]);
    expect(num(null)).toBe("—");
    expect(pct(null)).toBe("—");
  });
  it("the table: n, mean, σ, min, max of every realization, «Все вместе» at the foot", () => {
    const t = summaryTable(readSummary(SUM), null);
    expect(t.head).toEqual(["Реализация", "Узлов", "Среднее", "σ", "Минимум", "Максимум"]);
    expect(t.rows).toHaveLength(6);
    expect(t.rows[0].cells.map(flat)).toEqual(["1", "200", "1,297", "0,5443", "0,3245", "2,799"]);
    expect(t.rows[5]).toMatchObject({ key: "all", total: true });
    expect(t.rows[5].cells.map(flat)).toEqual(["Все вместе", "", "1,311", "0,565", "0,2432", "4,628"]);
  });
  it("the categories table: the columns are the names of the stored row (the code when there is no name)", () => {
    const cats = categoriesOf(INFOS[SID.cat].meta)!;
    expect(cats).toEqual({ codes: [1, 2, 3], labels: ["Окисленные", "Переходные", "Первичные"] });
    const t = summaryTable(readSummary(CAT), cats);
    expect(t.head).toEqual(["Реализация", "Узлов", "Окисленные", "Переходные", "Первичные"]);
    expect(t.rows[0].cells.map(flat)).toEqual(["1", "200", "20 %", "38 %", "42 %"]);
    expect(t.rows[4]).toMatchObject({ key: "all", total: true });
    expect(t.rows[4].cells.map(flat)).toEqual(["Все вместе", "", "28,8 %", "33,6 %", "37,6 %"]);
    expect(summaryTable(readSummary(CAT), null).head.slice(2)).toEqual(["1", "2", "3"]);
    expect(categoriesOf(INFOS[SID.sgs].meta)).toBeNull();
  });
  it("the facts: the nodes, the realizations, the samples, the conditioning data, the singular systems, the nodes with no datum", () => {
    const f = Object.fromEntries(summaryFacts(readSummary(SUM)).map(([k, v]) => [k, flat(v)]));
    expect(f).toMatchObject({ "Узлов (родителей)": "200", Реализаций: "5", "Время расчёта, с": "8,86", "Проб в расчёте": "147 из 500", "Вырожденных систем": "0", "Узлов без проб в окрестности": "0", "Узлов на пробах": "0" });
    expect(f["Условных данных в узле"]).toBe("24 · 24 · 24 (мин · среднее · макс)");
    expect(f).not.toHaveProperty("Узлов без вторичной переменной");
    const co = Object.fromEntries(summaryFacts(readSummary({ ...SUM, counts: { ...SUM.counts, gap: 4 } })));
    expect(co["Узлов без вторичной переменной"]).toBe("4");
  });
});

describe("the route", () => {
  it("`GET /simulations/{id}/summary`; before the first run the core says 409 `no_realizations`", async () => {
    const s = await loadSummary(SID.sgs);
    expect(s.realizations).toBe(5);
    expect(core.sent.some((r) => r.method === "GET" && r.path === `/geostat/simulations/${SID.sgs}/summary`)).toBe(true);
    await expect(loadSummary(SID.co)).rejects.toMatchObject({ code: "no_realizations", message: S.summary_empty.body.message, hint: S.summary_empty.body.hint });
    await expect(loadSummary(SID.co)).rejects.toBeInstanceOf(CoreError);
  });
});

describe("simulate.summary and the window", () => {
  it("opens the window with the row asked; a row that is not there is refused with the way to find the numbers", async () => {
    const r = await app.registry.execute("simulate.summary", { id: SID.cat }, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Сводка реализаций»" });
    expect(summaryStore.get().simId).toBe(SID.cat);
    expect(windowStore.get().windows.filter((w) => w.kind === SUMMARY_WINDOW)).toHaveLength(1);
    await app.registry.execute("simulate.summary", { id: SID.sgs }, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === SUMMARY_WINDOW)).toHaveLength(1);
    expect(summaryStore.get().simId).toBe(SID.sgs);
    const none = await app.registry.execute("simulate.summary", { id: SID.none }, "user");
    expect(none).toMatchObject({ ok: false, result: expect.stringContaining(`№${SID.none}`) });
  });
  it("without an id: the one the window has", async () => {
    chooseSummary(SID.co);
    await app.registry.execute("simulate.summary", {}, "user");
    expect(summaryStore.get().simId).toBe(SID.co);
  });
  it("the window: the list of the rows, the facts and the table of the summary read, the buttons that run commands", async () => {
    await loadDetails(SID.cat);
    chooseSummary(SID.cat);
    await app.registry.execute("simulate.summary", { id: SID.cat }, "user");
    const w = windowStore.get().windows.find((x) => x.kind === SUMMARY_WINDOW)!;
    let html = render(renderSimulationWindow(w) as ReturnType<typeof createElement>);
    expect(html).toContain('data-testid="sum-select"');
    for (const r of [SID.sgs, SID.sis, SID.cat, SID.co]) expect(html).toContain(`<option value="${r}"`);
    expect(html).toContain("LITH · SIS · окисл · SIS");
    expect(html).toContain('data-testid="sum-post"');
    expect(html).toContain('data-testid="sum-run"');
    // the table itself (the read of the summary is asynchronous: it is drawn from what the read gave)
    html = render(createElement(SummaryView, { summary: readSummary(CAT), cats: categoriesOf(INFOS[SID.cat].meta) }));
    expect(html).toContain("Окисленные");
    expect(html).toContain('data-testid="sum-row-r4"');
    expect(html).toContain('data-testid="sum-row-all"');
    expect(flat(html)).toContain("28,8 %");
    expect(html).toContain("Условных данных в узле");
  });
  it("a row «устарел» is told so; a row that was never run says how to run it", async () => {
    core.list = { simulations: [{ ...S.simulations.body.simulations[0], stale: true, stale_reason: "изменена таблица «Композиты»" }, { ...S.simulations.body.simulations[0], id: SID.co, name: "Без запуска", last_run: null }] };
    await loadRows();
    chooseSummary(SID.sgs);
    let html = windowHtml();
    expect(html).toContain("изменена таблица «Композиты»");
    chooseSummary(SID.co);
    html = windowHtml();
    expect(html).toContain('data-testid="sum-empty"');
    expect(html).toContain("У моделирования «Без запуска» нет реализаций");
    await tick();
  });
});
