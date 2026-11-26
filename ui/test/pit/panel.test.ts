// The panel «Pit-by-pit» (Э21, 21.18; ADR 0028 решения 18, 19, 29; docs/progress/e21-api.md §7, §16) on the REAL answers of the core (`core/tests/data/pit/contract_answers.json` → `report_get`,
// `scenario_get`, `scenarios_list`, `pit_no_result`): the table is the text of the core (a gap is «—», never 0), the chart is made of `series`, the calendar of the panel is the query
// `?calendar=`, the commands `pit.report` and `pit.show` (the arguments, the unavailability with the words of the contract), the frame of 200 shells.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { BLOCK_STYLES_KEY, styleIn } from "../../src/modules/blocks/display";
import { blocksStore } from "../../src/modules/blocks/state";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { viewStore } from "../../src/modules/scene/bind";
import { onPitPanelEvent } from "../../src/modules/pit/bindPanel";
import { calendarQuery } from "../../src/modules/pit/reportApi";
import { chartCsv, PitChart, PIT_COLORS, shellTicks } from "../../src/modules/pit/PitChart";
import { PitPanel, PitView, type PanelActions } from "../../src/modules/pit/PitPanel";
import { calendarOfText } from "../../src/modules/pit/PitPanelParts";
import { INITIAL_PANEL, openReport, pitPanelStore, resetPanel, setMode, setTab, PIT_TAB } from "../../src/modules/pit/panelState";
import { chartData, columnTitle, formatNumber, readReport, reportText, viewTable, type PitReport } from "../../src/modules/pit/reportModel";
import { loadScenarios, pitStore, selectScenario } from "../../src/modules/pit/state";
import { NO_RESULT_TEXT } from "../../src/modules/pit/scenarioModel";
import { answer, ID, installCore, json, MODEL, newApp, teardown, waitFor, type Core } from "./harness";

type Obj = Record<string, unknown>;
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const body = (): Obj => clone(answer("report_get").body as Obj);
const report = (): PitReport => readReport(body())!;
const NBSP = " ";

/** The answer of the core to a report asked without a calendar: Q_р is not set — no NPV (null, `npv_ready: false`, the warning `no_calendar`). */
function withoutCalendar(): Obj {
  const b = body();
  b.npv_ready = false;
  b.calendar = { q_ore: null, q_mass: null, rate: 0.1 };
  const nullNpv = (s: Obj) => {
    s.best = { npv: null };
    s.worst = { npv: null };
  };
  for (const s of b.shells as Obj[]) nullNpv(s);
  nullNpv(b.totals as Obj);
  const ser = b.series as Obj;
  ser.npv_best = (ser.npv_best as unknown[]).map(() => null);
  ser.npv_worst = (ser.npv_worst as unknown[]).map(() => null);
  for (const r of (b.table as { rows: { cells: Record<string, Obj> }[] }).rows) {
    r.cells.best_npv = { value: null, text: "—" };
    r.cells.worst_npv = { value: null, text: "—" };
  }
  b.warnings = [{ code: "no_calendar", message: "Не задана производительность по руде Q_р: NPV не считается.", hint: "Задайте календарь." }];
  return b;
}

/** A report of `n` shells made of the three of the contract (the numbers repeat: the frame is what is measured). */
function shells(n: number): PitReport {
  const b = body();
  const src = b.shells as Obj[];
  const many: Obj[] = Array.from({ length: n }, (_, i) => ({ ...clone(src[i % src.length]), k: i + 1, rf: 0.3 + i * 0.01 }));
  b.shells = many;
  const col = (key: string) => many.map((s) => s[key]);
  b.series = { k: many.map((s) => s.k), rf: col("rf"), ore_t: col("ore_t"), waste_t: col("waste_t"), cf: col("cf"), npv_best: many.map((s) => (s.best as Obj).npv), npv_worst: many.map((s) => (s.worst as Obj).npv) };
  (b.table as Obj).rows = many.map((s) => ({ k: s.k, cells: { rf: { value: s.rf, text: "x" } } }));
  return readReport(b)!;
}

const noop: PanelActions = { tab: () => undefined, mode: () => undefined, calendar: () => undefined, saveCalendar: () => undefined, show: () => undefined };

describe("the number as the core writes it", () => {
  it("half away from zero, the decimal comma, no-break spaces; a negative that rounds to zero has no sign; a gap is «—»", () => {
    expect(formatNumber(542056.715128458, 0)).toBe(`542${NBSP}057`);
    expect(formatNumber(-1765.9662707817697, 0)).toBe(`-1${NBSP}766`);
    expect(formatNumber(-0.4, 0)).toBe("0");
    expect(formatNumber(1.005, 2)).toBe("1,01");
    expect(formatNumber(3.5240923899705265, 2)).toBe("3,52");
    expect(formatNumber(36229123.10460041, 0)).toBe(`36${NBSP}229${NBSP}123`);
    expect(formatNumber(null, 0)).toBe("—");
    expect(formatNumber(Number.NaN, 2)).toBe("—");
    expect(formatNumber(undefined, 2)).toBe("—");
  });
});

describe("the answer of the core", () => {
  it("the real report is read: the shells, the totals, the series, the table, the calendar", () => {
    const r = report();
    expect(r.scenario_id).toBe(ID.scenario);
    expect(r.shells.map((s) => s.k)).toEqual([1, 2, 3]);
    expect(r.npv_ready).toBe(true);
    expect(r.calendar).toEqual({ q_ore: 1e6, q_mass: null, rate: 0.1 });
    expect(r.elements[0]).toMatchObject({ field: "AU", unit_label: "г/т", metal_unit: "унц" });
    expect(r.totals.best).toBe(r.shells[2].best);
    expect(r.series.npv_best).toEqual(r.shells.map((s) => s.best));
    expect(r.table.columns.map((c) => c.key)).toEqual(["k", "rf", "blocks", "ore_t", "waste_t", "strip", "AU:grade", "AU:rec_metal", "cf", "best_npv", "worst_npv"]);
    expect(readReport({})).toBeNull();
    expect(readReport({ scenario_id: 1 })).toBeNull();
  });
  it("the identity of the contract: the sum of the increments is `cum` of the last shell and `totals`", () => {
    const r = report();
    const sum = (key: "ore_t" | "waste_t" | "revenue" | "cf") => r.shells.reduce((a, s) => a + (s[key] ?? 0), 0);
    for (const key of ["ore_t", "waste_t", "revenue", "cf"] as const) {
      expect(sum(key)).toBeCloseTo(r.totals[key] ?? NaN, 3);
      expect(r.shells[2].cum[key]).toBeCloseTo(r.totals[key] ?? NaN, 3);
    }
  });
});

describe("the table is the text of the core", () => {
  it("«По оболочкам»: every cell is the `text` of the core as it is; the totals row is made of `totals`", () => {
    const raw = body();
    const rawRows = (raw.table as { rows: { k: number; cells: Record<string, { text: string }> }[] }).rows;
    const t = viewTable(report(), "shells");
    expect(t.columns.map(columnTitle)).toEqual(["№", "RF", "Блоки, шт", "Руда, т", "Пустая, т", "Вскрыша, т/т", "AU, содержание, г/т", "AU, извлекаемый металл, унц", "CF, $", "NPV лучший, $", "NPV худший, $"]);
    expect(t.rows).toHaveLength(4);
    rawRows.forEach((row, i) => {
      for (const [key, cell] of Object.entries(row.cells)) expect(t.rows[i].cells[key].text, `${i}:${key}`).toBe(cell.text);
      expect(t.rows[i].cells.k.text).toBe(String(row.k));
    });
    const total = t.rows[3];
    expect(total).toMatchObject({ total: true, k: null });
    expect(total.cells.k.text).toBe("Итого");
    expect(total.cells.cf.text).toBe(`36${NBSP}229${NBSP}123`);
    expect(total.cells.strip.text).toBe("3,49");
    expect(total.cells.best_npv.text).toBe(`35${NBSP}308${NBSP}810`);
    expect(t.rows[0].cells.ore_t.text).toBe(`542${NBSP}057`);
  });
  it("a gap is «—», never 0: no calendar (NPV null), no ore (W/O null), a cell the core did not send", () => {
    const b = withoutCalendar();
    (b.shells as Obj[])[1].strip = null;
    ((b.table as { rows: { cells: Record<string, Obj> }[] }).rows[1]).cells.strip = { value: null, text: "—" };
    delete ((b.table as { rows: { cells: Record<string, Obj> }[] }).rows[2]).cells["AU:grade"];
    const r = readReport(b)!;
    const t = viewTable(r, "shells");
    for (const row of t.rows) {
      expect(row.cells.best_npv).toEqual({ text: "—", missing: true });
      expect(row.cells.worst_npv).toEqual({ text: "—", missing: true });
    }
    expect(t.rows[1].cells.strip).toEqual({ text: "—", missing: true });
    expect(t.rows[2].cells["AU:grade"]).toEqual({ text: "—", missing: true });
    expect(t.rows[0].cells.cf.missing).toBe(false); // a value of the core stays a value
    expect(r.shells.every((s) => s.best === null && s.worst === null)).toBe(true);
    expect(r.series.npv_best.every((v) => v === null)).toBe(true);
    expect(r.npv_ready).toBe(false);
    // the table on the screen: the gap cells are drawn as gaps, not as 0
    const html = renderToStaticMarkup(createElement(PitView, { report: r, panel: { ...INITIAL_PANEL }, actions: noop }));
    const cell = (col: string) => [...html.matchAll(new RegExp(`<td class="[^"]*" data-col="${col}">([^<]*)</td>`, "g"))].map((m) => m[1]);
    expect(cell("best_npv")).toEqual(["—", "—", "—", "—"]);
    expect(cell("worst_npv")).toEqual(["—", "—", "—", "—"]);
    expect(html).toContain("Не задана производительность по руде Q_р");
    expect(html).toContain('data-npv="none"');
  });
  it("«Выручка и затраты» and «Накопленные» are made of `shells` with the same rules of the text", () => {
    const r = report();
    const money = viewTable(r, "money");
    expect(money.columns.map((c) => c.key)).toEqual(["k", "rf", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty", "cf"]);
    expect(money.rows[0].cells.revenue.text).toBe(formatNumber(r.shells[0].revenue, 0));
    expect(money.rows[0].cells.cf.text).toBe(`36${NBSP}214${NBSP}824`);
    expect(money.rows[2].cells.cf.text).toBe(`-1${NBSP}766`); // the CF of the shell 3 is negative: the sign is kept
    expect(money.rows[3].cells.revenue.text).toBe(formatNumber(r.totals.revenue, 0));
    const cum = viewTable(r, "cum");
    expect(cum.rows).toHaveLength(3);
    expect(cum.columns.map((c) => c.key)).toContain("AU:rec_metal");
    expect(cum.rows[2].cells.cf.text).toBe(formatNumber(r.totals.cf, 0));
    expect(cum.rows[1].cells.ore_t.text).toBe(formatNumber(r.shells[1].cum.ore_t, 0));
    expect(cum.rows[1].cells["AU:grade"].text).toBe(formatNumber(r.shells[1].cum.elements.AU.grade, 2));
  });
});

describe("the chart is made of `series`", () => {
  it("the bars of the ore and of the waste by shells, the lines of the NPV of the best and of the worst case and of the CF, the colours of the brand", () => {
    const r = report();
    const html = renderToStaticMarkup(createElement(PitChart, { report: r }));
    const group = (name: string) => html.slice(html.indexOf(`data-series="${name}"`));
    expect(html).toContain('data-series="ore" data-points="3"');
    expect((html.slice(html.indexOf('data-series="ore"'), html.indexOf('data-series="waste"')).match(/<rect/g) ?? []).length).toBe(3);
    expect((html.slice(html.indexOf('data-series="waste"'), html.indexOf('data-series="best"')).match(/<rect/g) ?? []).length).toBe(3);
    for (const name of ["best", "worst", "cf"]) expect(group(name)).toContain(`data-pieces="1"`);
    expect(html).toContain(`fill="${PIT_COLORS.ore}"`);
    expect(html).toContain(`stroke="${PIT_COLORS.best}"`);
    expect(html).toContain('stroke-dasharray="6 3"'); // the worst case is dashed: it is told by more than a colour
    expect(PIT_COLORS.ore).not.toBe(PIT_COLORS.waste);
    expect(html).toContain("Pit-by-pit: руда и пустая порода по оболочкам, NPV и CF");
    // the shells on the axis with their RF (≤ 30 shells)
    expect(html).toContain('data-axis="x" data-shells="3"');
    expect(html).toContain(">0,80<");
    expect(html).toContain(">1,20<");
    const d = chartData(r);
    expect(d.k).toEqual([1, 2, 3]);
    expect(d.best).toEqual(r.shells.map((s) => s.best));
    expect(d.cf).toEqual(r.shells.map((s) => s.cf));
  });
  it("no NPV: the lines of the NPV are not drawn (a gap is not 0), the bars and the CF are; the chart says so in words", () => {
    const r = readReport(withoutCalendar())!;
    const html = renderToStaticMarkup(createElement(PitChart, { report: r }));
    expect(html).toContain('data-series="best" data-pieces="0"');
    expect(html).toContain('data-series="worst" data-pieces="0"');
    expect(html).toContain('data-series="cf" data-pieces="1"');
    expect(html).toContain("NPV не считается: не задана производительность по руде");
    expect(chartCsv(r).split("\r\n")[1]).toBe("1;0.8;542056.715128458;1910257.9447166205;36214824.36849924;;"); // a gap stays an empty cell
  });
  it("a gap in the middle of a line breaks it (two pieces)", () => {
    const b = body();
    (b.series as { npv_best: unknown[] }).npv_best[1] = null;
    const html = renderToStaticMarkup(createElement(PitChart, { report: readReport(b)! }));
    expect(html).toContain('data-series="best" data-pieces="2"');
  });
  it("`series` that is shorter than the shells: the chart takes the numbers of `shells`; no shells — no chart, the words", () => {
    const b = body();
    b.series = { k: [], rf: [], ore_t: [], waste_t: [], cf: [], npv_best: [], npv_worst: [] };
    expect(chartData(readReport(b)!).ore).toEqual(report().shells.map((s) => s.ore_t));
    const empty = body();
    empty.shells = [];
    empty.series = { k: [], rf: [], ore_t: [], waste_t: [], cf: [], npv_best: [], npv_worst: [] };
    expect(renderToStaticMarkup(createElement(PitChart, { report: readReport(empty)! }))).toContain("График не построен");
  });
  it("the shells on the axis: at most about twelve ticks", () => {
    expect(shellTicks(3)).toEqual([0, 1, 2]);
    expect(shellTicks(200).length).toBeLessThanOrEqual(13);
    expect(shellTicks(200)[0]).toBe(0);
  });
  it("a frame of 200 shells is drawn in 16 ms (the best of 30: the machine of the test may stall)", () => {
    const r = shells(200);
    for (let i = 0; i < 5; i++) renderToStaticMarkup(createElement(PitChart, { report: r })); // warm up
    let best = Infinity;
    let html = "";
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      html = renderToStaticMarkup(createElement(PitChart, { report: r }));
      best = Math.min(best, performance.now() - t0);
    }
    expect(best).toBeLessThanOrEqual(16);
    expect(html).toContain('data-series="ore" data-points="200"');
    expect(html).toContain('data-series="best" data-pieces="1"');
    expect((html.match(/<rect/g) ?? []).length).toBeGreaterThanOrEqual(400);
  });
});

describe("the panel", () => {
  const draw = (r: PitReport, panel: Partial<Parameters<typeof PitView>[0]["panel"]> = {}) => renderToStaticMarkup(createElement(PitView, { report: r, panel: { ...INITIAL_PANEL, ...panel }, actions: noop }));
  it("the table tab: the title, the facts, the three views, the calendar, the rows in the text of the core, «3D» of a shell", () => {
    const html = draw(report());
    for (const id of ["pit-panel", "pit-panel-title", "pit-panel-facts", "pit-calendar", "pit-tab-table", "pit-tab-chart", "pit-mode-shells", "pit-mode-money", "pit-mode-cum", "pit-table", "pit-show-1", "pit-show-3", "pit-calendar-apply", "pit-calendar-save"]) expect(html, id).toContain(`data-testid="${id}"`);
    expect(html).toContain("Pit-by-pit «Сценарий карьера»");
    expect(html).toContain("модель «Рудное тело» · оболочек 3 · RF 0,80 … 1,20");
    expect(html).toContain(`542${NBSP}057`);
    expect(html).toContain("Итого");
    expect(html).toContain('data-rows="3"');
    expect(html).toContain('value="1000000"'); // the calendar of the report starts in the fields
    expect(html).toContain('value="10"'); // r = 0,1 is 10 %
    expect(html).toContain(`Q_р 1${NBSP}000${NBSP}000 т/год, Q_гм без ограничения, r = 10 %`);
  });
  it("the chart tab: the legend of the five series, the chart, the toolbar PNG / SVG / CSV", () => {
    const html = draw(report(), { tab: "chart" });
    expect(html).toContain('data-testid="pit-chart"');
    for (const s of ["ore", "waste", "best", "worst", "cf"]) expect(html).toContain(`data-series="${s}"`);
    expect(html).toContain("NPV лучший, $");
    expect(html).toContain("CSV графика");
    expect(html).not.toContain('data-testid="pit-table"');
  });
  it("the modes change the table; a calendar of the panel is told in words", () => {
    expect(draw(report(), { mode: "money" })).toContain(">Выручка<");
    expect(draw(report(), { mode: "cum" })).toContain("Накопленные");
    expect(draw(report(), { calendar: { q_ore: 2e6, q_mass: null, rate: 0.05 } })).toContain("Показан календарь панели");
    expect(draw(report(), { error: { code: "pit_no_result", message: "У сценария нет результата.", hint: "Запустите расчёт сценария.", param: null } })).toContain('role="alert"');
  });
  it("the calendar fields: Q_р and Q_гм — empty or more than 0, r — 0…50 %; nothing wrong goes to the core", () => {
    expect(calendarOfText({ qOre: "1 000 000", qMass: "", rate: "10" })).toEqual({ ok: true, calendar: { q_ore: 1e6, q_mass: null, rate: 0.1 } });
    expect(calendarOfText({ qOre: "2,5e6", qMass: "3000000", rate: "7,5" })).toEqual({ ok: true, calendar: { q_ore: 2.5e6, q_mass: 3e6, rate: 0.075 } });
    expect(calendarOfText({ qOre: "", qMass: "", rate: "" })).toEqual({ ok: true, calendar: { q_ore: null, q_mass: null, rate: 0.08 } });
    for (const bad of [{ qOre: "0", qMass: "", rate: "8" }, { qOre: "-5", qMass: "", rate: "8" }, { qOre: "abc", qMass: "", rate: "8" }]) expect(calendarOfText(bad)).toMatchObject({ ok: false, message: expect.stringContaining("Q_р") });
    expect(calendarOfText({ qOre: "1", qMass: "0", rate: "8" })).toMatchObject({ ok: false, message: expect.stringContaining("Q_гм") });
    expect(calendarOfText({ qOre: "1", qMass: "", rate: "51" })).toMatchObject({ ok: false, message: expect.stringContaining("Ставка") });
    expect(calendarOfText({ qOre: "1", qMass: "", rate: "x" })).toMatchObject({ ok: false });
  });
  it("the request: all three keys, a gap is `null`", () => {
    const q = calendarQuery({ q_ore: 1e6, q_mass: null, rate: 0.1 });
    expect(q.startsWith("?calendar=")).toBe(true);
    expect(JSON.parse(decodeURIComponent(q.slice("?calendar=".length)))).toEqual({ q_ore: 1e6, q_mass: null, rate: 0.1 });
  });
  it("the text of the report for the log and for the assistant: the totals and the NPV; without Q_р it says how to get the NPV", () => {
    const t = reportText(report());
    expect(t).toContain("Pit-by-pit сценария «Сценарий карьера»: оболочек 3 (RF 0,80 … 1,20).");
    expect(t).toContain(`руда 552${NBSP}823 т, пустая 1${NBSP}931${NBSP}896 т, вскрыша 3,49, CF 36${NBSP}229${NBSP}123 $`);
    expect(t).toContain(`лучший случай 35${NBSP}308${NBSP}810 $, худший 35${NBSP}148${NBSP}584 $`);
    expect(t).toContain("r = 10 %");
    expect(reportText(readReport(withoutCalendar())!)).toContain("NPV не считается: не задана производительность по руде Q_р");
  });
});

describe("the commands `pit.report` and `pit.show`", () => {
  let app: App;
  let core: Core;
  let unbind: () => void;
  let reportAsks: { query: string }[];
  let reportAnswer: () => Response;
  const withPit = (fields: string[]): BlockModelInfo => ({ ...MODEL, fields: [...MODEL.fields, ...fields.map((name) => ({ name, kind: "number" as const, unit: null }))] }) as BlockModelInfo;
  beforeEach(async () => {
    core = installCore();
    ({ app, unbind } = newApp());
    resetPanel();
    reportAsks = [];
    reportAnswer = () => json(body());
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        if (path.startsWith("/view/")) return json({ version: 1, value: init?.body ? (JSON.parse(String(init.body)) as Obj).value : null });
        if (path === `/pit/scenarios/${ID.scenario}/report`) {
          reportAsks.push({ query: u.search });
          return reportAnswer();
        }
        return base(url, init);
      }),
    );
    core.scenario.has_result = true;
    await waitFor(() => pitStore.get().scenariosLoaded);
  });
  afterEach(() => {
    resetPanel();
    blocksStore.set({ list: [MODEL], loaded: true, error: null, details: { [ID.model]: MODEL }, detailErrors: {} });
    teardown(unbind);
  });
  const run = (id: string, args: Record<string, unknown> = {}) => app.registry.execute(id, args, "user");
  const rowWithoutResult = async () => {
    const list = clone(answer("scenarios_list").body as { scenarios: Obj[] });
    Object.assign(list.scenarios[0], { has_result: false, shells: null, value: null });
    core.force("GET /pit/scenarios", { status: 200, body: list });
    await loadScenarios();
  };

  it("`pit.report` is unavailable without a scenario and without a result; the hint is `pit_no_result` of the contract", async () => {
    // no scenario at hand: available (the call names its own `scenario_id`); without one the handler says what is missing
    expect(app.registry.reason("pit.report")).toBeNull();
    expect(app.registry.reason("pit.show")).toBeNull();
    const nothing = await run("pit.report");
    expect(nothing.ok).toBe(false);
    expect(nothing.result).toContain("Не задан сценарий карьера.");
    const contract = answer("pit_no_result").body as { message: string; hint: string };
    expect(NO_RESULT_TEXT).toBe(`${contract.message} ${contract.hint}`);
    await selectScenario(ID.scenario);
    expect(app.registry.reason("pit.report")).toBeNull();
    expect(app.registry.reason("pit.show")).toBeNull();
    await rowWithoutResult();
    expect(app.registry.reason("pit.report")).toBe(NO_RESULT_TEXT);
    expect(app.registry.reason("pit.show")).toBe(NO_RESULT_TEXT);
    const r = await run("pit.report", { scenario_id: ID.scenario });
    expect(r).toEqual({ ok: false, result: NO_RESULT_TEXT });
    expect(reportAsks).toHaveLength(0); // nothing is asked of the core
    const s = await run("pit.show", { scenario_id: ID.scenario, shell: 1 });
    expect(s).toEqual({ ok: false, result: NO_RESULT_TEXT });
  });

  it("`pit.report`: the report is read, the bottom dock opens on «Pit-by-pit», the answer is the totals and the NPV", async () => {
    const r = await run("pit.report", { scenario_id: ID.scenario });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Pit-by-pit сценария «Сценарий карьера»: оболочек 3");
    expect(r.result).toContain("NPV");
    expect(reportAsks).toEqual([{ query: "" }]); // the calendar of the scenario: no query
    const p = pitPanelStore.get();
    expect(p.scenarioId).toBe(ID.scenario);
    expect(p.report?.shells).toHaveLength(3);
    expect(p.loading).toBe(false);
    expect(p.error).toBeNull();
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: PIT_TAB });
    // the panel in the dock shows it
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(PitPanel)));
    expect(html).toContain('data-testid="pit-panel"');
    expect(html).toContain(`542${NBSP}057`);
  });

  it("without arguments the scenario of the tree is shown; a scenario that is not in the project is told", async () => {
    app.store.set({ selected: { kind: "pit_scenario", id: ID.scenario } as never });
    await waitFor(() => pitStore.get().validate !== null);
    expect((await run("pit.report")).ok).toBe(true);
    const gone = await run("pit.report", { scenario_id: 999 });
    expect(gone).toEqual({ ok: false, result: "Сценарий карьера №999 не найден. Номера сценариев — в разделе «Карьер» → «Сценарии» дерева." });
  });

  it("the refusal of the core (409 `pit_no_result`: the list was not read yet) is told in its words and shown in the panel; a wrong answer is told", async () => {
    const contract = answer("pit_no_result").body as { message: string; hint: string };
    reportAnswer = () => json(contract, 409);
    const r = await run("pit.report", { scenario_id: ID.scenario });
    expect(r).toEqual({ ok: false, result: `${contract.message} ${contract.hint}` });
    expect(pitPanelStore.get().error).toMatchObject({ code: "pit_no_result" });
    expect(pitPanelStore.get().report).toBeNull();
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(PitPanel)));
    expect(html).toContain("У сценария нет результата. Запустите расчёт сценария.");
    reportAnswer = () => json({ nonsense: true });
    const bad = await run("pit.report", { scenario_id: ID.scenario });
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("в непонятной форме");
  });

  it("the calendar of the panel: the query of the report, the NPV is read again; «Как в сценарии» drops it; the scenario is not changed", async () => {
    await run("pit.report", { scenario_id: ID.scenario });
    const cal = { q_ore: 2e6, q_mass: 5e6, rate: 0.05 };
    reportAnswer = () => json({ ...body(), calendar: cal });
    const r = await openReport(ID.scenario, cal);
    expect(r?.calendar).toEqual(cal);
    expect(JSON.parse(decodeURIComponent(reportAsks.at(-1)!.query.slice("?calendar=".length)))).toEqual({ q_ore: 2e6, q_mass: 5e6, rate: 0.05 });
    expect(pitPanelStore.get().calendar).toEqual(cal);
    await openReport(ID.scenario); // the same scenario read again keeps the calendar of the panel
    expect(reportAsks.at(-1)!.query).toContain("calendar=");
    await openReport(ID.scenario, null);
    expect(reportAsks.at(-1)!.query).toBe("");
    expect(pitPanelStore.get().calendar).toBeNull();
    expect(core.sent.filter((s) => s.method === "PUT")).toHaveLength(0);
  });

  it("another scenario starts clean; a read that was overtaken does not write its end; another project clears the panel", async () => {
    await run("pit.report", { scenario_id: ID.scenario });
    expect(pitPanelStore.get().report).not.toBeNull();
    const slow = openReport(ID.scenario);
    const quick = openReport(ID.scenario, { q_ore: 3e6, q_mass: null, rate: 0.1 });
    await Promise.all([slow, quick]);
    expect(pitPanelStore.get().calendar).toEqual({ q_ore: 3e6, q_mass: null, rate: 0.1 });
    resetPanel();
    expect(pitPanelStore.get()).toMatchObject({ report: null, scenarioId: null, calendar: null });
    setTab("chart");
    setMode("cum");
    expect(pitPanelStore.get()).toMatchObject({ tab: "chart", mode: "cum" });
  });

  it("`pit.changed` of the shown scenario reads the report again; a deleted one leaves the panel; another scenario is not touched", async () => {
    await run("pit.report", { scenario_id: ID.scenario });
    const n = reportAsks.length;
    onPitPanelEvent("pit.changed", JSON.stringify({ kind: "pit_scenario", id: 99 }));
    await new Promise((r) => setTimeout(r, 30));
    expect(reportAsks).toHaveLength(n);
    onPitPanelEvent("pit.changed", JSON.stringify({ kind: "pit_scenario", id: ID.scenario }));
    await waitFor(() => reportAsks.length === n + 1);
    onPitPanelEvent("pit.changed", JSON.stringify({ kind: "pit_scenario", id: ID.scenario, deleted: true }));
    await waitFor(() => pitPanelStore.get().scenarioId === null);
    expect(pitPanelStore.get().report).toBeNull();
  });

  it("`pit.show {shell}`: the model is shown with the quick restrictor `PIT <= n` (the display of Э17); the answer says what was done", async () => {
    blocksStore.set({ list: [withPit(["PIT", "ORE"])], loaded: true, error: null, details: {}, detailErrors: {} });
    const r = await run("pit.show", { scenario_id: ID.scenario, shell: 2 });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("В 3D показаны блоки модели «Рудное тело» с PIT ≤ 2 (оболочка 2 из 3)");
    const style = styleIn(viewStore.get(), ID.model);
    expect(style.visible).toBe(true);
    expect(style.quick).toMatchObject({ items: [{ field: "PIT", op: "<=", value: 2 }], join: "and", invert: false });
    const raw = (viewStore.get()[BLOCK_STYLES_KEY] as Record<string, { quick: { items: unknown[] } }>)[String(ID.model)];
    expect(raw.quick.items).toEqual([{ field: "PIT", op: "<=", value: 2 }]); // kept in the view of the project: «blocks.quick clear» drops it
    await run("pit.show", { scenario_id: ID.scenario, shell: 3 });
    expect(styleIn(viewStore.get(), ID.model).quick.items).toEqual([{ field: "PIT", op: "<=", value: 3 }]); // replaced, not added
  });

  it("`pit.show`: the field of the prefix of the scenario, a shell that is not there, a wrong number, a model without the field — told in Russian before anything is drawn", async () => {
    blocksStore.set({ list: [withPit(["PIT", "ORE"])], loaded: true, error: null, details: {}, detailErrors: {} });
    const none = styleIn(viewStore.get(), ID.model).quick.items;
    expect(await run("pit.show", { scenario_id: ID.scenario, shell: 4 })).toEqual({ ok: false, result: "Оболочки №4 нет: у сценария «Сценарий карьера» их 3. Номера оболочек — от 1 до 3." });
    for (const shell of [0, -1]) {
      const r = await run("pit.show", { scenario_id: ID.scenario, shell });
      expect(r, String(shell)).toMatchObject({ ok: false, result: expect.stringContaining("Номер оболочки — целое число от 1.") });
    }
    for (const shell of [1.5, "x", null]) expect((await run("pit.show", { scenario_id: ID.scenario, shell })).ok, String(shell)).toBe(false); // the registry: «должен иметь тип integer»
    blocksStore.set({ list: [MODEL], loaded: true, error: null, details: {}, detailErrors: {} }); // the model has no PIT
    const noField = await run("pit.show", { scenario_id: ID.scenario, shell: 1 });
    expect(noField.ok).toBe(false);
    expect(noField.result).toContain("В модели «Рудное тело» нет поля PIT.");
    blocksStore.set({ list: [], loaded: true, error: null, details: {}, detailErrors: {} });
    expect((await run("pit.show", { scenario_id: ID.scenario, shell: 1 })).result).toContain("не найдена в проекте");
    expect(styleIn(viewStore.get(), ID.model).quick.items).toEqual(none);
  });

  it("the tab «Pit-by-pit» is a tab of the bottom dock: `PIT_TAB` is the id the shell gets", () => {
    expect(String(PIT_TAB)).toBe("pit");
  });
});
