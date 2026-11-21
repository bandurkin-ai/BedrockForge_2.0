// The window «Q-Q график» (ТЗ 7.26; ADR 0019 §11; перенос `QQWindow` / `QQChart` BF): the request from the settings
// (side Y «as X» by default), the points of the chart — on log axes only those > 0 — and the CSV of exactly them,
// one scale, P10 / P50 / P90, the table X / Y / отношение (missing stays «—»), the notes, the command and the
// assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QQChart, QQ_H, QQ_W } from "../src/charts/qq/QQChart";
import { QQWindow } from "../src/charts/qq/QQWindow";
import { axisTitles, labelledPoints, qqMarks, qqCsv, qqDomains, qqNotes, qqPoints, qqRequest, qqSummary, qqTable, ratio } from "../src/charts/qq/model";
import type { Describe, QQResult, QQSide } from "../src/modules/charts/api";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings, normalizeSettings, type QQSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const STATS: Describe = {
  count: 5,
  weight_sum: 5,
  min: 0.2,
  max: 8,
  mean: 2,
  median: 1,
  std: 1.5,
  variance: 2.25,
  cv: 0.75,
  skewness: 1.2,
  percentiles: { "5": 0.2, "10": 0.3, "25": 0.5, "50": 1, "75": 2, "90": 5, "95": 7, "97.5": 7.5, "99": 8 },
  log: null,
};
const COUNTS = { rows: 6, missing: 1, bad_weight: 0, negative: 0, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 0, top_excluded: 0, used: 5 };
const SIDE: QQSide = { weighted: true, counts: COUNTS, stats: STATS };

// the answers of the core 13.15 (docs/progress/e13-api.md): points [x, y, P %]
const THEORY: QQResult = {
  field: "AU",
  set_id: 3,
  y_field: null,
  y_set_id: null,
  mode: "theory",
  x: SIDE,
  y: null,
  points: [
    [0.25, 0.2, 10],
    [0.6, 0.5, 30],
    [1, 1, 50],
    [1.8, 2, 70],
    [4.5, 8, 90],
  ],
  count: 5,
  distribution: { name: "lognormal", mu: 0, sigma: 1.1, dropped_nonpositive: 0 },
  ppcc: 0.9934,
  marks: [
    [0.25, 0.2, 10],
    [1, 1, 50],
    [4.5, 8, 90],
  ],
};

const TWO: QQResult = {
  field: "AU",
  set_id: 3,
  y_field: "AU_CHK",
  y_set_id: 4,
  mode: "sample",
  x: SIDE,
  y: { ...SIDE, stats: { ...STATS, mean: 2.2, median: 0 } },
  points: [
    [0, 0.1, 25],
    [1, null, 50],
    [2, 2.4, 75],
  ],
  ratio: [null, null, 1.2],
  extremes: { x: [0, 3], y: [0.1, 3.3] },
  ks: 0.12,
  marks: [],
};

const base = (): QQSettings => ({ ...defaultSettings("qq", 3), field: "AU" });

describe("settings → request (перенос BF)", () => {
  it("lognormal with log axes by default; waits for a field and weight fields; the view is not sent", () => {
    const d = defaultSettings("qq", 3);
    expect(d).toMatchObject({ distribution: "lognormal", log: true, quantiles: 99, y_set_id: null, y_field: null, view: { equal: true, diagonal: true, labels: true } });
    expect(qqRequest(d)).toBeNull();
    expect(qqRequest({ ...base(), weight: "field" })).toBeNull();
    const r = qqRequest(base())!;
    expect(r).toMatchObject({ v: 1, set_id: 3, field: "AU", distribution: "lognormal", weight: "length" });
    expect("view" in r).toBe(false);
  });

  it("two samples: the quantiles 3…999; side Y without a field compares the field of X", () => {
    expect(qqRequest({ ...base(), distribution: "sample", quantiles: 5000 })?.quantiles).toBe(999);
    expect(qqRequest({ ...base(), distribution: "sample", quantiles: 1 })?.quantiles).toBe(3);
    expect(qqRequest({ ...base(), distribution: "sample" })).toMatchObject({ y_field: "AU", y_set_id: null, y_table: null });
    expect(qqRequest({ ...base(), distribution: "sample", y_field: "AU_CHK", y_set_id: 4 })).toMatchObject({ y_field: "AU_CHK", y_set_id: 4 });
    expect(qqRequest(base())?.y_field).toBeNull();
  });

  it("stored settings: the view key by key", () => {
    const s = normalizeSettings("qq", { v: 1, field: "AU", y_holes: "H1 H2", view: { equal: false, color: "#ABCDEF", junk: 1 } }, 3);
    expect(s.y_holes).toEqual(["H1", "H2"]);
    expect(s.view).toEqual({ equal: false, diagonal: true, labels: true, color: "#abcdef" });
    expect(chartName("qq", { ...base(), distribution: "sample", y_holes: ["H1"] })).toBe("Q-Q · AU / AU · часть скважин");
  });
});

describe("what the chart shows", () => {
  it("points: both values finite; on log axes only those > 0", () => {
    expect(qqPoints(TWO, false)).toEqual([
      { p: 0.25, x: 0, y: 0.1 },
      { p: 0.75, x: 2, y: 2.4 },
    ]);
    expect(qqPoints(TWO, true)).toEqual([{ p: 0.75, x: 2, y: 2.4 }]);
  });

  it("one scale: both axes take the common range; P10 / P50 / P90 — the nearest points", () => {
    const pts = qqPoints(THEORY, true);
    const eq = qqDomains(pts, true, true)!;
    expect(eq.x).toEqual(eq.y);
    expect(eq.x.min).toBeLessThan(0.2);
    expect(eq.x.max).toBeGreaterThan(8);
    const own = qqDomains(pts, true, false)!;
    expect(own.x.max).toBeLessThan(own.y.max);
    expect(qqDomains([], false, true)).toBeNull();
    expect(labelledPoints(pts)).toEqual([
      { p: 0.1, index: 0 },
      { p: 0.5, index: 2 },
      { p: 0.9, index: 4 },
    ]);
    // the marks of the core where given (exact P), else the nearest points
    expect(qqMarks(THEORY, pts, true).map((m) => m.p)).toEqual([0.1, 0.5, 0.9]);
    expect(qqMarks(TWO, qqPoints(TWO, false), false)).toEqual([
      { p: 0.1, x: 0, y: 0.1 },
      { p: 0.5, x: 0, y: 0.1 },
      { p: 0.9, x: 2, y: 2.4 },
    ]);
  });

  it("CSV of the points = the points of the chart", () => {
    for (const [r, log] of [[THEORY, true], [TWO, false], [TWO, true]] as const) {
      const pts = qqPoints(r, log);
      const titles = axisTitles(r, "AU, г/т", "AU_CHK, г/т");
      const lines = qqCsv(r, log, titles).split("\r\n");
      expect(lines[0]).toBe(`"Вероятность, %";"${titles.x}";"${titles.y}"`);
      expect(lines.slice(1)).toEqual(pts.map((q) => `${Number((q.p * 100).toPrecision(12))};${q.x};${q.y}`));
      const html = renderToStaticMarkup(createElement(QQChart, { points: pts, log, equal: true, diagonal: true, labels: false, color: "#1f6bff", xLabel: titles.x, yLabel: titles.y }));
      expect(html).toContain(`data-points="${pts.length}"`);
      expect(html.match(/<circle[^>]*fill="#1f6bff"/g)).toHaveLength(pts.length);
    }
  });

  it("the chart in SVG: 1:1 line, the marks, a fixed size for the export", () => {
    const pts = qqPoints(THEORY, true);
    const html = renderToStaticMarkup(createElement(QQChart, { points: pts, marks: qqMarks(THEORY, pts, true), log: true, equal: true, diagonal: true, labels: true, color: "#1f6bff", xLabel: "X", yLabel: "Y" }));
    expect(html).toContain(`viewBox="0 0 ${QQ_W} ${QQ_H}"`);
    expect(html).toContain('data-line="1:1"');
    for (const m of ["P10", "P50", "P90"]) expect(html).toContain(`data-mark="${m}"`);
    expect(html).not.toContain("NaN");
    expect(renderToStaticMarkup(createElement(QQChart, { points: [], log: true, equal: true, diagonal: true, labels: true, color: "#1f6bff", xLabel: "X", yLabel: "Y" }))).toBe("");
  });

  it("axis titles: against a distribution X — its quantiles, Y — the sample", () => {
    expect(axisTitles(THEORY, "AU, г/т", "—")).toEqual({ x: "Квантили: логнормальное распределение", y: "AU, г/т" });
    expect(axisTitles(TWO, "AU", "AU_CHK")).toEqual({ x: "AU", y: "AU_CHK" });
  });

  it("the table X / Y / отношение: no ratio of the count, of a missing value or over 0", () => {
    expect(qqTable(THEORY.x, null)[1]).toEqual(["Среднее", "2", null, null]);
    const rows = qqTable(TWO.x, TWO.y);
    expect(rows.find((r) => r[0] === "Число проб")).toEqual(["Число проб", "5", "5", ""]);
    expect(rows.find((r) => r[0] === "Среднее")).toEqual(["Среднее", "2", "2,2", "1,1"]);
    expect(rows.find((r) => r[0] === "Медиана")).toEqual(["Медиана", "1", "0", "0"]);
    expect(ratio(0, 1)).toBeNull();
    expect(ratio(null, 1)).toBeNull();
  });

  it("notes: the fit and PPCC; two samples — D; thinning", () => {
    expect(qqNotes(THEORY)).toEqual(["среднее ln 0, σ ln 1,1", "PPCC 0,9934 (1 — точное совпадение)", "точек 5"]);
    expect(qqNotes({ ...THEORY, count: 5000 })[2]).toBe(`точек 5 из ${(5000).toLocaleString("ru-RU")} (прорежено, крайние оставлены)`);
    expect(qqNotes(TWO)).toEqual(["квантилей 3", "D Колмогорова–Смирнова 0,12 (0 — одинаковые распределения)", "X 0…3, Y 0,1…3,3"]);
  });

  it("the window renders its form before any answer; two samples — side Y", () => {
    const html = renderToStaticMarkup(createElement(QQWindow, { winId: "w", setId: 3, setName: "Набор", settings: base() }));
    for (const label of ["Режим", "Распределение", "Таблица", "Поле", "Скважины X", "Вес", "Цвет точек"]) expect(html).toContain(`aria-label="${label}"`);
    expect(html).not.toContain('aria-label="Набор Y"');
    const two = renderToStaticMarkup(createElement(QQWindow, { winId: "w", setId: 3, setName: "Набор", settings: { ...base(), distribution: "sample" } }));
    for (const label of ["Число квантилей", "Набор Y", "Таблица Y", "Поле Y", "Скважины Y"]) expect(two).toContain(`aria-label="${label}"`);
  });
});

describe("the core: the command", () => {
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
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 6, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }, { name: "length", unit: "м", role: "length" }], texts: [] }] }));
        if (path === "/stats/qq") return Promise.resolve(json(THEORY));
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

  it("chart.qq opens the window with the first component; the assistant gets n, mean, median, CV and PPCC", async () => {
    const r = await app.registry.execute("chart.qq", {});
    expect(r.ok).toBe(true);
    const win = windowStore.get().windows[0];
    expect(win).toMatchObject({ kind: "chart-qq", title: "Q-Q · AU · логнормальное · Набор" });
    expect(win.props?.settings).toMatchObject({ set_id: 3, table: "пробы", field: "AU", distribution: "lognormal" });
    const a = await app.registry.execute("chart.qq", { field: "AU", distribution: "normal" }, "claude");
    expect(sent.find((x) => x.path === "/stats/qq")?.body).toMatchObject({ field: "AU", distribution: "normal" });
    expect(a.result).toMatch(/^Q-Q · AU · нормальное.*: проб 5, среднее 2, медиана 1, CV 0.75; PPCC 0.9934$/);
    expect(qqSummary("Q", TWO)).toBe("Q: проб 5, среднее 2, медиана 1, CV 0,75; Y: проб 5, среднее 2,2; D 0,12");
  });
});
