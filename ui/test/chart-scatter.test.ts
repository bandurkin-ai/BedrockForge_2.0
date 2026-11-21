// The window «Рассеяние» (ТЗ 8.3; ADR 0019 §4, §11): the lines of the fits straight in the space of the axes, the
// density cells, the frame and the lasso in the coordinates of the axes (log ones too), the canvas layer
// (200 000 points ≤ 100 ms), the CSV, the command and the assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeScale } from "../src/charts/core/axisScale";
import { insidePolygon, rectPath, selectInside, simplifyPath, toAxisPolygon, toViewBox } from "../src/charts/core/lasso";
import { rgba32 } from "../src/charts/core/palette";
import { axisMap, mapValue, rasterPoints, rasterRects, rasterSize, rasterUrls } from "../src/charts/core/PointsCanvas";
import { ScatterChart } from "../src/charts/scatter/ScatterChart";
import { secondField, ScatterWindow } from "../src/charts/scatter/ScatterWindow";
import { colorLegend, densityRects, diagonalPoints, fitSegment, pointColors, pointPixels, scatterCsv, scatterDomains, scatterNotes, scatterRequest, scatterSummary, scatterTable, selectedRows } from "../src/charts/scatter/model";
import { plotBox } from "../src/charts/core/PlotFrame";
import type { ScatterResult } from "../src/modules/charts/apiMore";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings, normalizeSettings, type ScatterSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const COUNTS = { rows: 6, missing_x: 1, missing_y: 0, unmatched: 0, bad_weight: 0, negative: 0, zero_excluded: 0, nonpositive_log: 0, used: 5 };

// y = 2x + 1 (Т8.3-1: МНК and RMA give b = 2, a = 1)
const POINTS: ScatterResult = {
  set_id: 3,
  x_field: "AU",
  y_field: "AG",
  color_field: null,
  log_x: false,
  log_y: false,
  weighted: false,
  counts: COUNTS,
  mode: "points",
  x: [0, 1, 2, 3, 4],
  y: [1, 3, 5, 7, 9],
  row: [101, 102, 103, 104, 105],
  c: null,
  color: null,
  pearson: 1,
  spearman: 1,
  n: 5,
  fits: { ols: { a: 1, b: 2 }, rma: { a: 1, b: 2 }, weighted: null },
  x_stats: { mean: 2, std: 1.414, min: 0, max: 4 },
  y_stats: { mean: 5, std: 2.828, min: 1, max: 9 },
};

const DENSITY: ScatterResult = {
  ...POINTS,
  mode: "density",
  x: undefined,
  y: undefined,
  row: undefined,
  n: 300_000,
  counts: { ...COUNTS, rows: 300_000, used: 300_000 },
  density: { nx: 4, ny: 2, x: [0, 8], y: [0, 4], counts: [5, 0, 0, 1, 0, 2, 0, 0], max: 5 },
};

const base = (): ScatterSettings => ({ ...defaultSettings("scatter", 3), x: "AU", y: "AG" });

describe("settings → request", () => {
  it("waits for both fields and a weight field; the view is not sent; the name follows the fields", () => {
    expect(scatterRequest(defaultSettings("scatter", 3))).toBeNull();
    expect(scatterRequest({ ...base(), weight: "field" })).toBeNull();
    const r = scatterRequest(base())!;
    expect(r).toMatchObject({ v: 1, set_id: 3, x: "AU", y: "AG", y_table: null, log_x: false, log_y: false, weight: "none", negatives: "keep", regression: "ols" });
    expect("view" in r).toBe(false);
    expect(chartName("scatter", base())).toBe("Рассеяние · AU / AG");
    const s = normalizeSettings("scatter", { x: "AU", regression: "rma", log_x: true, view: { diagonal: true, color: "#ABCDEF" } }, 3);
    expect(s).toMatchObject({ x: "AU", regression: "rma", log_x: true, view: { diagonal: true, color: "#abcdef" } });
    expect(secondField({ numeric: [{ name: "AU", kind: "component", unit: null, role: "component" }, { name: "length", kind: "length", unit: "м", role: "length" }, { name: "DENS", kind: "attribute", unit: null, role: "value" }, { name: "AG", kind: "component", unit: null, role: "component" }] }, "AU")).toBe("AG");
  });
});

describe("lines of the fits", () => {
  it("y = 2x + 1 inside both ranges; a line missing the plot — none", () => {
    const seg = fitSegment({ a: 1, b: 2 }, { min: 0, max: 4 }, { min: 0, max: 5 }, "linear", "linear")!;
    // clipped by the top of the plot: x from 0 to 2
    expect(seg).toEqual([
      { x: 0, y: 1 },
      { x: 2, y: 5 },
    ]);
    expect(fitSegment({ a: 10, b: 0 }, { min: 0, max: 4 }, { min: 0, max: 5 }, "linear", "linear")).toBeNull();
    expect(fitSegment({ a: null, b: 2 }, { min: 0, max: 4 }, { min: 0, max: 5 }, "linear", "linear")).toBeNull();
  });

  it("log axes: the line is straight in log10 (y = 10·x² ↔ log y = 1 + 2·log x)", () => {
    const seg = fitSegment({ a: 1, b: 2 }, { min: 0.1, max: 10 }, { min: 0.01, max: 10_000 }, "log", "log")!;
    expect(seg[0].x).toBeCloseTo(0.1, 12);
    expect(seg[0].y).toBeCloseTo(0.1, 12);
    expect(seg[1].x).toBeCloseTo(10, 12);
    expect(seg[1].y).toBeCloseTo(1000, 9);
    // 1:1 — straight with one kind of axes, a curve inside the ranges with a log X and a linear Y
    expect(diagonalPoints({ min: 1, max: 5 }, { min: 0, max: 3 }, "linear", "linear")).toEqual([
      { x: 1, y: 1 },
      { x: 3, y: 3 },
    ]);
    const curve = diagonalPoints({ min: 0.1, max: 100 }, { min: 0, max: 50 }, "log", "linear");
    expect(curve.length).toBeGreaterThan(10);
    expect(curve.every((q) => q.x === q.y && q.x >= 0.1 && q.x <= 50 + 1e-9)).toBe(true);
  });

  it("the chart draws the chosen line, 1:1 and the points; the table lists r and all fits", () => {
    const html = renderToStaticMarkup(createElement(ScatterChart, { result: POINTS, kx: "linear", ky: "linear", regression: "rma", diagonal: true, color: "#1f6bff", xLabel: "AU", yLabel: "AG" }));
    expect(html).toContain('data-line="rma"');
    expect(html).toContain('data-line="1:1"');
    expect(html).toContain('data-points="5"');
    expect(html.match(/<circle/g)).toHaveLength(5);
    expect(html).not.toContain("NaN");
    expect(scatterTable(POINTS)).toContainEqual(["МНК", "b 2, a 1"]);
    expect(scatterTable(POINTS)).toContainEqual(["Взвешенная", "без веса"]);
    expect(scatterSummary("Р", POINTS, "ols")).toBe("Р: пар 5, r Пирсона 1, r Спирмена 1; МНК: b 2, a 1");
    expect(scatterNotes(POINTS)).toEqual(["строк 6, пар в расчёте 5", "без X 1"]);
  });
});

describe("density", () => {
  it("> 200 000 pairs: only the non-empty cells, equal in the space of the axes, in place", () => {
    const d = scatterDomains(DENSITY, "linear", "linear")!;
    const box = plotBox(400, 200, { left: 0, right: 0, top: 0, bottom: 0 });
    const sx = makeScale("linear", { min: 0, max: 8 }, box.x0, box.x1);
    const sy = makeScale("linear", { min: 0, max: 4 }, box.y1, box.y0);
    const rects = densityRects(DENSITY.density!, axisMap(sx, box.x0, 1), axisMap(sy, box.y0, 1));
    expect(rects).toHaveLength(3);
    // cell (ix 0, iy 0): x 0…100 px, y — the bottom row (100…200 px)
    expect(rects[0]).toMatchObject({ x0: 0, x1: 100, y0: 200, y1: 100 });
    expect(rects[1]).toMatchObject({ x0: 300, x1: 400 });
    expect(rects[2]).toMatchObject({ x0: 100, x1: 200, y0: 100, y1: 0 });
    expect(rects[0].color).not.toBe(rects[1].color);
    expect(d.x.min).toBeLessThan(0);
    const buf = new Uint32Array(400 * 200);
    rasterRects(buf, 400, 200, rects);
    expect(buf[150 * 400 + 50]).toBe(rects[0].color);
    expect(buf[150 * 400 + 150]).toBe(0);
    // CSV — the cells with their counts
    const csv = scatterCsv(DENSITY, { x: "AU", y: "AG" }).split("\r\n");
    expect(csv).toEqual(['"AU от";"AU до";"AG от";"AG до";"Проб"', "0;2;0;2;5", "6;8;0;2;1", "2;4;2;4;2"]);
    const html = renderToStaticMarkup(createElement(ScatterChart, { result: DENSITY, kx: "linear", ky: "linear", regression: "ols", diagonal: false, color: "#1f6bff", xLabel: "AU", yLabel: "AG" }));
    expect(html).toContain('data-density="4x2"');
    expect(html).not.toContain("<circle");
  });

  it("log axes: the cells are equal decades", () => {
    const res: ScatterResult = { ...DENSITY, log_x: true, density: { nx: 2, ny: 1, x: [1, 100], y: [0, 1], counts: [1, 1], max: 1 } };
    const sx = makeScale("log", { min: 1, max: 100 }, 0, 200);
    const sy = makeScale("linear", { min: 0, max: 1 }, 100, 0);
    const rects = densityRects(res.density!, axisMap(sx, 0, 1), axisMap(sy, 0, 1));
    expect(rects.map((r) => [r.x0, r.x1])).toEqual([
      [0, 100],
      [100, 200],
    ]);
  });
});

describe("frame and lasso in the coordinates of the axes", () => {
  const box = plotBox(640, 520, { left: 66, right: 22, top: 18, bottom: 50 });

  it("pixels of the viewBox → data units; the even-odd rule", () => {
    expect(toViewBox(150, 60, { left: 50, top: 10, width: 320, height: 260 }, 640, 520)).toEqual({ x: 200, y: 100 });
    const sx = makeScale("linear", { min: 0, max: 10 }, box.x0, box.x1);
    const sy = makeScale("linear", { min: 0, max: 10 }, box.y1, box.y0);
    const poly = toAxisPolygon(rectPath({ x: sx.map(1)!, y: sy.map(1)! }, { x: sx.map(3)!, y: sy.map(4)! }), sx, sy)!;
    poly.x.forEach((v, i) => expect(v).toBeCloseTo([1, 3, 3, 1][i], 12));
    poly.y.forEach((v, i) => expect(v).toBeCloseTo([1, 1, 4, 4][i], 12));
    expect(selectInside([2, 0.5, 2, 2.9, null], [2, 2, 5, 3.9, 2], poly)).toEqual([0, 3]);
    // a click or a line has no area
    expect(toAxisPolygon([{ x: 100, y: 100 }, { x: 101, y: 100 }, { x: 102, y: 100 }], sx, sy)).toBeNull();
    // a concave lasso (a «U»): the notch is outside
    const ux = [0, 3, 3, 2, 2, 1, 1, 0];
    const uy = [0, 0, 3, 3, 1, 1, 3, 3];
    expect(insidePolygon(1.5, 2, ux, uy)).toBe(false);
    expect(insidePolygon(0.5, 2, ux, uy)).toBe(true);
    expect(simplifyPath([{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 4, y: 0 }], 3)).toEqual([{ x: 0, y: 0 }, { x: 4, y: 0 }]);
  });

  it("log axes: the polygon is straight in log10; ≤ 0 is never selected", () => {
    const sx = makeScale("log", { min: 0.01, max: 100 }, box.x0, box.x1);
    const sy = makeScale("log", { min: 0.01, max: 100 }, box.y1, box.y0);
    // a triangle drawn on the screen: (0.1, 0.1), (10, 0.1), (0.1, 10) — its long edge is y = 1 / x in log space
    const path = [
      { x: sx.map(0.1)!, y: sy.map(0.1)! },
      { x: sx.map(10)!, y: sy.map(0.1)! },
      { x: sx.map(0.1)!, y: sy.map(10)! },
    ];
    const poly = toAxisPolygon(path, sx, sy)!;
    expect(poly.x[1]).toBeCloseTo(10, 9);
    // (2, 2) is above y = 1 / x — outside on the screen; the chord in data units (y = 10,1 − x) would take it
    expect(selectInside([2, 0.5, 0, -1, 0.5], [2, 1, 0.5, 0.5, null], poly, "log", "log")).toEqual([1]);
    expect(selectInside([2, 0.5], [2, 1], poly, "linear", "linear")).toEqual([0, 1]);
    expect(selectedRows(POINTS, [0, 3])).toEqual([101, 104]);
  });

  it("the selected points are marked; the polygon is drawn", () => {
    const poly = { x: [-1, 1.5, 1.5, -1], y: [0, 0, 10, 10] };
    const sel = selectInside(POINTS.x!, POINTS.y!, poly);
    expect(sel).toEqual([0, 1]);
    const html = renderToStaticMarkup(createElement(ScatterChart, { result: POINTS, kx: "linear", ky: "linear", regression: "none", diagonal: false, color: "#1f6bff", xLabel: "AU", yLabel: "AG", tool: "lasso", selection: poly, selected: sel }));
    expect(html.match(/data-selected="true"/g)).toHaveLength(2);
    expect(html).toContain('data-selection="4"');
    expect(html).toContain('data-tool="lasso"');
  });
});

describe("the canvas layer", () => {
  it("points into the raster: in place, the axes cannot show — skipped", () => {
    const sx = makeScale("log", { min: 1, max: 100 }, 0, 100);
    const sy = makeScale("linear", { min: 0, max: 10 }, 50, 0);
    const mx = axisMap(sx, 0, 2);
    const my = axisMap(sy, 0, 2);
    expect(mapValue(mx, 10)).toBeCloseTo(100, 9);
    expect(mapValue(my, 10)).toBeCloseTo(0, 9);
    expect(mapValue(mx, 0)).toBeNaN();
    const buf = new Uint32Array(200 * 100);
    const red = rgba32("#ff0000");
    expect(rgba32("#ff0000") & 0xff).toBe(255);
    expect(rasterPoints(buf, 200, 100, [10, 0, null, 1000], [5, 5, 5, 5], mx, my, red, 4)).toBe(1);
    expect(buf[50 * 200 + 100]).toBe(red);
    expect(rasterSize(plotBox(640, 520, { left: 66, right: 22, top: 18, bottom: 50 }))).toEqual({ w: 1104, h: 904 });
  });

  it("200 000 points with colours by a field: the redraw takes ≤ 100 ms", () => {
    const N = 200_000;
    const xs = new Float64Array(N);
    const ys = new Float64Array(N);
    const c: number[] = new Array(N);
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < N; i++) {
      xs[i] = 0.01 + rnd() * 100;
      ys[i] = xs[i] * (0.5 + rnd());
      c[i] = rnd();
    }
    const box = plotBox(640, 520, { left: 66, right: 22, top: 18, bottom: 50 });
    const { w, h } = rasterSize(box);
    const sx = makeScale("log", { min: 0.01, max: 200 }, box.x0, box.x1);
    const sy = makeScale("log", { min: 0.001, max: 200 }, box.y1, box.y0);
    const res = { c, color: { kind: "number" as const, min: 0, max: 1 } };
    const runs: number[] = [];
    for (let k = 0; k < 3; k++) {
      const t0 = performance.now();
      const px = pointPixels(res, N, "#1f6bff");
      const buf = new Uint32Array(w * h);
      const drawn = rasterPoints(buf, w, h, xs, ys, axisMap(sx, box.x0), axisMap(sy, box.y0), px, 4);
      runs.push(performance.now() - t0);
      expect(drawn).toBe(N);
    }
    expect(Math.min(...runs)).toBeLessThan(100);
    // the lasso over the same points
    const t1 = performance.now();
    const sel = selectInside(xs, ys, { x: [1, 50, 50, 1], y: [0.5, 0.5, 60, 60] }, "log", "log");
    expect(performance.now() - t1).toBeLessThan(100);
    expect(sel.length).toBeGreaterThan(1000);
  });

  it("colours by a field: numbers — the ramp, text — the series; a missing value — Slate", () => {
    const num = pointColors({ c: [0, 1, null], color: { kind: "number", min: 0, max: 1 } }, 3, "#000000");
    expect(num[0]).toBe("#1f6bff");
    expect(num[1]).toBe("#f46a1a");
    expect(num[2]).toBe("#8a99a8");
    const txt = pointColors({ c: [1, 0, null], color: { kind: "text", labels: ["OX", "SU"] } }, 3, "#000000");
    expect(txt).toEqual(["#f46a1a", "#1f6bff", "#8a99a8"]);
    // the pixels of the canvas layer — the same colours
    expect(Array.from(pointPixels({ c: [1, 0, null], color: { kind: "text", labels: ["OX", "SU"] } }, 3, "#000000"))).toEqual(txt.map((h) => rgba32(h, 0.8)));
    expect(pointPixels({ c: [0, 1], color: { kind: "number", min: 0, max: 1 } }, 2, "#000000")[1]).toBe(rgba32("#f46a1a", 0.8));
    expect(colorLegend({ color: { kind: "text", labels: ["OX", "SU"] }, color_field: "ROCK" })?.items.map((i) => i.label)).toEqual(["OX", "SU"]);
    const csv = scatterCsv({ ...POINTS, color_field: "ROCK", c: [0, 1, null, 0, 1], color: { kind: "text", labels: ["OX", "SU"] } }, { x: "AU", y: "AG" }).split("\r\n");
    expect(csv[0]).toBe('"Строка";"AU";"AG";"ROCK"');
    expect(csv[1]).toBe('101;0;1;"OX"');
    expect(csv[3]).toBe("103;2;5;");
  });
});

describe("the window and the command", () => {
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    windowStore.set({ windows: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method: init?.method ?? "GET", path: `${path}${u.search}`, body });
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 6, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }, { name: "AG", unit: "г/т", role: "component" }, { name: "length", unit: "м", role: "length" }], texts: [] }] }));
        if (path === "/stats/scatter") return Promise.resolve(json(POINTS));
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

  it("the window renders its form before any answer", () => {
    const html = renderToStaticMarkup(createElement(ScatterWindow, { winId: "w", setId: 3, setName: "Набор", settings: base() }));
    for (const label of ["Таблица", "Поле X", "Таблица Y", "Поле Y", "Цвет по полю", "Линия регрессии", "Вес", "Выделение", "Цвет точек"]) expect(html, label).toContain(`aria-label="${label}"`);
  });

  it("chart.scatter: X — the first component, Y — the next; the assistant gets pairs, r and the line", async () => {
    const r = await app.registry.execute("chart.scatter", {});
    expect(r.ok).toBe(true);
    const win = windowStore.get().windows[0];
    expect(win).toMatchObject({ kind: "chart-scatter", title: "Рассеяние · AU / AG · Набор" });
    expect(win.props?.settings).toMatchObject({ set_id: 3, table: "пробы", x: "AU", y: "AG" });
    const a = await app.registry.execute("chart.scatter", { x: "AG", y: "AU", log_x: true, regression: "rma" }, "claude");
    expect(sent.find((x) => x.path === "/stats/scatter")?.body).toMatchObject({ x: "AG", y: "AU", log_x: true, regression: "rma", table: "пробы" });
    expect(a.result).toMatch(/^Рассеяние · AG \/ AU.*: пар 5, r Пирсона 1, r Спирмена 1; RMA: b 2, a 1$/);
  });
});

describe("the pictures of a raster layer", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("a new picture revokes the one before it at once; dropped — the last one too", () => {
    let n = 0;
    const revoked: string[] = [];
    vi.stubGlobal("URL", { createObjectURL: () => `blob:${++n}`, revokeObjectURL: (u: string) => void revoked.push(u) });
    const urls = rasterUrls();
    expect(urls.show(new Blob(["a"]))).toBe("blob:1");
    expect(revoked).toEqual([]);
    expect(urls.show(new Blob(["b"]))).toBe("blob:2");
    expect(revoked).toEqual(["blob:1"]); // not a second later: when the new one is set
    urls.drop();
    expect(revoked).toEqual(["blob:1", "blob:2"]);
    urls.drop();
    expect(revoked).toHaveLength(2);
  });
});
