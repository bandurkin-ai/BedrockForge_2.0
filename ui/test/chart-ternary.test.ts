// The window «Тернарная» (ТЗ 8.3; ADR 0019 §4): shares → a point of the triangle and back, the grid of 10 %, the
// points in SVG and on the canvas layer, the CSV, the notes, the command and the assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TernaryChart } from "../src/charts/ternary/TernaryChart";
import { defaultTriple, TernaryWindow } from "../src/charts/ternary/TernaryWindow";
import { meanShares, ternaryCsv, ternaryGrid, ternaryNotes, ternaryPoint, ternaryRequest, ternaryShares, ternarySummary, triangleIn } from "../src/charts/ternary/model";
import type { TernaryResult } from "../src/modules/charts/apiMore";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const RES: TernaryResult = {
  set_id: 3,
  fields: ["CU", "PB", "ZN"],
  color_field: null,
  counts: { rows: 5, missing: 1, nonpositive_sum: 1, used: 3 },
  a: [1, 0, 0.2],
  b: [0, 1, 0.3],
  c: [0, 0, 0.5],
  row: [7, 8, 9],
  total: 3,
};

describe("the triangle", () => {
  const t = triangleIn(0, 0, 200, 200);

  it("equilateral, A on top; the vertices and the centre; shares and back", () => {
    expect(t.C.x - t.B.x).toBeCloseTo(200, 9);
    expect(t.B.y - t.A.y).toBeCloseTo(100 * Math.sqrt(3), 9);
    expect(ternaryPoint(t, 1, 0, 0)).toEqual(t.A);
    expect(ternaryPoint(t, 0, 2, 0)).toEqual(t.B);
    const m = ternaryPoint(t, 1, 1, 1)!;
    expect(m.x).toBeCloseTo(100, 9);
    const sh = ternaryShares(t, ternaryPoint(t, 0.2, 0.3, 0.5)!);
    sh.forEach((v, i) => expect(v).toBeCloseTo([0.2, 0.3, 0.5][i], 12));
    // a missing share or a sum ≤ 0 — no point (never at a vertex)
    expect(ternaryPoint(t, 0, 0, 0)).toBeNull();
    expect(ternaryPoint(t, Number.NaN, 1, 1)).toBeNull();
    expect(ternaryPoint(t, -1, 1, 1)).toBeNull();
  });

  it("the grid: 9 lines per vertex; a line of A keeps its share", () => {
    const g = ternaryGrid(t);
    expect(g).toHaveLength(27);
    for (const l of g.filter((x) => x.vertex === "a")) {
      expect(ternaryShares(t, l.from)[0]).toBeCloseTo(l.share, 12);
      expect(ternaryShares(t, l.to)[0]).toBeCloseTo(l.share, 12);
    }
  });

  it("the chart: points in SVG up to 5000, more on the canvas layer", () => {
    const html = renderToStaticMarkup(createElement(TernaryChart, { result: RES, color: "#1f6bff" }));
    expect(html).toContain('data-points="3"');
    expect(html.match(/<circle/g)).toHaveLength(3);
    expect(html).toContain(">CU<");
    expect(html).not.toContain("NaN");
    const big = 6000;
    const many: TernaryResult = { ...RES, a: new Array(big).fill(0.2), b: new Array(big).fill(0.3), c: new Array(big).fill(0.5), row: new Array(big).fill(1), total: big };
    const html2 = renderToStaticMarkup(createElement(TernaryChart, { result: many, color: "#1f6bff" }));
    expect(html2).toContain('data-raster="1"');
    expect(html2).not.toContain("<circle");
  });

  it("CSV, notes, the assistant's text", () => {
    expect(ternaryCsv(RES).split("\r\n")).toEqual(['"Строка";"Доля CU";"Доля PB";"Доля ZN"', "7;1;0;0", "8;0;1;0", "9;0.2;0.3;0.5"]);
    expect(ternaryNotes(RES)).toEqual(["строк 5, точек 3", "с пропуском поля 1", "сумма ≤ 0: 1"]);
    // the core tells the two apart
    expect(ternaryNotes({ ...RES, counts: { rows: 6, missing: 0, nonpositive_sum: 3, negative: 2, zero_sum: 1, used: 3 } })).toEqual(["строк 6, точек 3", "с отрицательным значением 2", "сумма 0: 1"]);
    expect(ternaryNotes({ ...RES, counts: { rows: 4, missing: 0, nonpositive_sum: 1, negative: 0, zero_sum: 1, used: 3 } })).toEqual(["строк 4, точек 3", "сумма 0: 1"]);
    expect(ternaryNotes({ ...RES, total: 300_000 })[3]).toMatch(/^показано 3 из/);
    expect(meanShares({ a: [], b: [], c: [] })).toEqual([null, null, null]);
    expect(ternarySummary("Т", RES)).toBe("Т: точек 3; средние доли CU 40 %, PB 43,3 %, ZN 16,7 %");
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
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 5, rev: 1, numbers: ["CU", "PB", "ZN"].map((name) => ({ name, unit: "%", role: "component" })), texts: [] }] }));
        if (path === "/stats/ternary") return Promise.resolve(json(RES));
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

  it("three fields are needed; the defaults are the first three components", async () => {
    expect(ternaryRequest(defaultSettings("ternary", 3))).toBeNull();
    expect(defaultTriple({ numeric: [{ name: "length", kind: "length", unit: "м", role: "length" }, { name: "CU", kind: "component", unit: null, role: "component" }] })).toEqual(["CU", null, null]);
    const html = renderToStaticMarkup(createElement(TernaryWindow, { winId: "w", setId: 3, setName: "Набор", settings: defaultSettings("ternary", 3) }));
    for (const label of ["Вершина A", "Вершина B", "Вершина C", "Цвет по полю"]) expect(html).toContain(`aria-label="${label}"`);
    expect((await app.registry.execute("chart.ternary", {})).ok).toBe(true);
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "chart-ternary", title: "Тернарная · CU / PB / ZN · Набор" });
    const a = await app.registry.execute("chart.ternary", { a: "ZN", b: "PB", c: "CU" }, "claude");
    expect(sent.find((x) => x.path === "/stats/ternary")?.body).toMatchObject({ a: "ZN", b: "PB", c: "CU", table: "пробы" });
    expect(a.result).toMatch(/: точек 3; средние доли CU 40 %/);
    expect(chartName("ternary", { ...defaultSettings("ternary", 3), a: "A", b: "B", c: "C" })).toBe("Тернарная · A / B / C");
  });
});
