// The window «Лог-вероятностный» (ТЗ 8.3; ADR 0019 §4): the points of the domains on log10 × Φ⁻¹(P), a click picks
// the nearest point (else the grade under the pointer) → `composite.topcut {set_id, value}` — the window «Урезка»
// only, the threshold is not applied by itself (ADR 0018, decision 8); the table, the CSV, the command. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeScale, normInv } from "../src/charts/core/axisScale";
import { ProbPlotChart } from "../src/charts/probplot/ProbPlotChart";
import { openTopcut, ProbPlotWindow } from "../src/charts/probplot/ProbPlotWindow";
import { pickPoint, probCsv, probDomains, probRequest, probSummary, probTable, seriesPoints, topcutArgs } from "../src/charts/probplot/model";
import type { ProbPlotResult } from "../src/modules/charts/apiMore";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const COUNTS = { rows: 9, missing: 1, bad_weight: 0, negative: 0, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 0, top_excluded: 0, used: 8, no_domain: 0 };

const RES: ProbPlotResult = {
  set_id: 3,
  field: "AU",
  domain: "ROCK",
  weighted: true,
  counts: COUNTS,
  series: [
    { label: "OX", points: [[0.1, 10], [1, 50], [10, 90], [0, 95]], count: 3, nonpositive: 1, stats: { mean: 3.7, median: 1, cv: 1.5, p90: 10 } },
    { label: null, points: [[2, 25], [4, 75]], count: 2, nonpositive: 0, stats: { mean: 3, median: 3, cv: null, p90: 4 } },
  ],
};

describe("the log-probability plot", () => {
  it("settings → request; the points the axes can show; the ranges at least P1…P99", () => {
    expect(probRequest(defaultSettings("probplot", 3))).toBeNull();
    const s = { ...defaultSettings("probplot", 3), field: "AU", domain: "ROCK" };
    expect(probRequest(s)).toMatchObject({ field: "AU", domain: "ROCK", weight: "length", top_mode: "cap" });
    expect("view" in probRequest(s)!).toBe(false);
    expect(chartName("probplot", s)).toBe("Лог-вероятностный · AU · по ROCK");
    expect(seriesPoints(RES.series[0])).toEqual([{ x: 0.1, p: 0.1 }, { x: 1, p: 0.5 }, { x: 10, p: 0.9 }]);
    const d = probDomains(RES)!;
    expect(d.x.min).toBeLessThan(0.1);
    expect(d.x.max).toBeGreaterThan(10);
    expect(d.p.min).toBeLessThanOrEqual(0.01);
    expect(d.p.max).toBeGreaterThanOrEqual(0.99);
  });

  it("the probability axis is Φ⁻¹: P50 in the middle between P10 and P90", () => {
    const sy = makeScale("prob", { min: 0.01, max: 0.99 }, 400, 0);
    const y10 = sy.map(0.1)!;
    const y50 = sy.map(0.5)!;
    const y90 = sy.map(0.9)!;
    expect(y10 - y50).toBeCloseTo(y50 - y90, 6);
    expect((y10 - y90) / 400).toBeCloseTo((normInv(0.9) - normInv(0.1)) / (normInv(0.99) - normInv(0.01)), 6);
  });

  it("a click picks the nearest point within 12 px, else nothing", () => {
    const sx = makeScale("log", { min: 0.01, max: 100 }, 0, 400);
    const sy = makeScale("prob", { min: 0.01, max: 0.99 }, 400, 0);
    const at = { x: sx.map(10)! + 5, y: sy.map(0.9)! - 4 };
    expect(pickPoint(RES, sx, sy, at)).toEqual({ series: 0, point: { x: 10, p: 0.9 } });
    expect(pickPoint(RES, sx, sy, { x: sx.map(4)!, y: sy.map(0.75)! })).toEqual({ series: 1, point: { x: 4, p: 0.75 } });
    expect(pickPoint(RES, sx, sy, { x: 5, y: 5 })).toBeNull();
  });

  it("щелчок → composite.topcut {set_id, value}: the window only — without the table and the field no job starts", async () => {
    expect(topcutArgs({ set_id: 3 }, 2.345678)).toEqual({ set_id: 3, value: 2.346 });
    const app = createApp({ persist: false });
    const got: Record<string, unknown>[] = [];
    app.registry.bind(
      "composite.topcut",
      (a) => {
        got.push(a);
        return "Открыто окно «Урезка»";
      },
      () => null,
    );
    expect(await openTopcut(app, { set_id: 3 }, 12.3456)).toBeNull();
    expect(got).toEqual([{ set_id: 3, value: 12.35 }]);
    // the command of the composites opens its window without the table and the field (ТЗ 8.2.2: «Применить» applies)
    const { hasRequired } = await import("../src/modules/composites/bind");
    const { topcut } = await import("../src/modules/composites/tools/topcut");
    expect(hasRequired(topcut, got[0])).toBe(false);
  });

  it("the chart: a series per domain with the legend, the threshold marked; the table and the CSV", () => {
    const html = renderToStaticMarkup(createElement(ProbPlotChart, { result: RES, lines: false, xLabel: "AU", threshold: 5 }));
    expect(html).toContain('data-series="OX"');
    expect(html).toContain('data-series="без домена"');
    expect(html.match(/<circle/g)).toHaveLength(5);
    expect(html).toContain('data-threshold="5"');
    expect(html).toContain('data-legend="domains"');
    expect(html).not.toContain("NaN");
    const lines = renderToStaticMarkup(createElement(ProbPlotChart, { result: RES, lines: true, xLabel: "AU" }));
    expect(lines.match(/<polyline/g)).toHaveLength(2);
    expect(probTable(RES)[1]).toEqual(["без домена", "2", "0", "3", "3", "—", "4"]);
    expect(probCsv(RES, "AU, г/т").split("\r\n").slice(0, 2)).toEqual(['"Домен";"AU, г/т";"Вероятность, %"', '"OX";0.1;10']);
    expect(probSummary("Л", RES)).toBe("Л: OX: проб 3, медиана 1, P90 10; без домена: проб 2, медиана 3, P90 4");
  });
});

describe("the window and the command", () => {
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    windowStore.set({ windows: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        sent.push({ path, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {} });
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 9, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }], texts: [{ name: "ROCK", role: "value", values: 1, labels: ["OX"], more: false }] }] }));
        if (path === "/stats/probplot") return Promise.resolve(json(RES));
        return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindCharts(app);
    app.store.set({ projectName: "Проект", projectReadOnly: true, setName: "Набор" });
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("chart.probplot: the first component, one line without a domain; the assistant gets the domains", async () => {
    const html = renderToStaticMarkup(createElement(ProbPlotWindow, { winId: "w", setId: 3, setName: "Набор", settings: { ...defaultSettings("probplot", 3), field: "AU" } }));
    for (const label of ["Таблица", "Поле", "Домен", "Вес", "Отсечка сверху"]) expect(html, label).toContain(`aria-label="${label}"`);
    expect((await app.registry.execute("chart.probplot", {})).ok).toBe(true);
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "chart-probplot", title: "Лог-вероятностный · AU · Набор" });
    const a = await app.registry.execute("chart.probplot", { domain: "ROCK" }, "claude");
    expect(sent.find((x) => x.path === "/stats/probplot")?.body).toMatchObject({ field: "AU", domain: "ROCK", table: "пробы" });
    expect(a.result).toMatch(/: OX: проб 3, медиана 1, P90 10/);
  });
});
