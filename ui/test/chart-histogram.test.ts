// The window «Гистограмма» (ТЗ 7.25; ADR 0019 §11; перенос `charts.test.ts` BF): the request from the settings, bin
// modes, Y values, marks, the CSV of the bins, the counters, the statistics with and without weights (missing
// stays «—»), the chart in SVG, PNG twice the chart (Т7.25-3), an edit computes 300 ms later and an overtaken
// answer is dropped, the command opens the window and answers the assistant. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HistogramChart, HIST_H, HIST_W } from "../src/charts/histogram/HistogramChart";
import { HistogramWindow } from "../src/charts/histogram/HistogramWindow";
import { barValues, binModeOf, countsNotes, cumulativeStart, histogramCalculator, histogramCsv, histogramMarks, histogramRequest, histogramSummary, statsTable, withBinMode } from "../src/charts/histogram/model";
import type { Describe, HistogramResult } from "../src/modules/charts/api";
import { bindCharts, renderChartsWindow } from "../src/modules/charts/bind";
import { defaultSettings, type HistogramSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

vi.mock("../src/charts/striplog/export", () => ({
  svgText: vi.fn(async () => "<svg/>"),
  pngBytes: vi.fn(async () => new Uint8Array([137, 80, 78, 71])),
}));

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const STATS: Describe = {
  count: 4,
  weight_sum: 6,
  min: 0.1,
  max: 5,
  mean: 1.5,
  median: 1.2,
  std: 0.8,
  variance: 0.64,
  cv: 0.5333,
  skewness: null,
  percentiles: { "5": 0.1, "10": 0.2, "25": 0.5, "50": 1.2, "75": 2, "90": 3, "95": 4, "97.5": 4.5, "99": 5 },
  log: null,
};

const RESULT: HistogramResult = {
  field: "AU",
  log: false,
  weighted: true,
  counts: { rows: 7, missing: 1, bad_weight: 1, negative: 1, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 1, top_excluded: 0, used: 4 },
  stats: STATS,
  stats_unweighted: { ...STATS, weight_sum: 4, mean: 1.4 },
  bins: { edges: [0, 1, 2, 3], counts: [1, 2, 1], weights: [1, 3, 2], percent: [10, 50, 30], cumulative_percent: [20, 70, 100], width: 1, below: { count: 1, weight: 1 }, above: { count: 0, weight: 0 } },
  conventions: "Процентили — по GSLIB; дисперсия генеральная.",
};

const base = (): HistogramSettings => ({ ...defaultSettings("histogram", 3), field: "AU" });

describe("settings → request (перенос BF)", () => {
  it("waits for a field and for a weight field; the view is not sent", () => {
    expect(histogramRequest(defaultSettings("histogram", 3))).toBeNull();
    expect(histogramRequest({ ...base(), weight: "length_field" })).toBeNull();
    const r = histogramRequest({ ...base(), top_cut: 12.5, bottom_cut: 0.1 })!;
    expect(r).toMatchObject({ v: 1, set_id: 3, field: "AU", weight: "length", top_cut: 12.5, top_mode: "cap", bottom_cut: 0.1, bottom_mode: "exclude", start: null, weight_field: null });
    expect("view" in r).toBe(false);
  });

  it("only the chosen bin setting is sent", () => {
    expect(binModeOf(base())).toBe("auto");
    const w = withBinMode({ ...base(), bins: 40 }, "width", 0.25);
    expect([w.bin_width, w.bins, binModeOf(w)]).toEqual([0.25, null, "width"]);
    const c = withBinMode(w, "count");
    expect([c.bin_width, c.bins, binModeOf(c)]).toEqual([null, 30, "count"]);
    expect(withBinMode(c, "auto")).toMatchObject({ bin_width: null, bins: null });
  });
});

describe("what the chart shows", () => {
  it("bar values by the Y axis", () => {
    expect(barValues(RESULT, "percent")).toEqual([10, 50, 30]);
    expect(barValues(RESULT, "weight")).toEqual([1, 3, 2]);
    expect(barValues(RESULT, "count")).toEqual([1, 2, 1]);
  });

  it("marks: mean, median, P90, P97,5 and the cuts; a missing value gives no mark", () => {
    const s = { ...base(), top_cut: 4, view: { ...base().view, marks: { mean: true, median: true, p90: true, p975: true, cuts: true } } };
    expect(histogramMarks(RESULT, s).map((m) => m.label)).toEqual(["Среднее 1.5", "Медиана 1.2", "P90 3", "P97,5 4.5", "Урезка 4"]);
    expect(histogramMarks({ ...RESULT, stats: { ...STATS, mean: null } }, base()).map((m) => m.label)).toEqual(["Медиана 1.2"]);
  });

  it("the cumulative curve starts at what lies below the range", () => {
    expect(cumulativeStart(RESULT)).toBe(10);
  });

  it("CSV of the bins with the out-of-range rows; empty stays empty", () => {
    expect(histogramCsv(RESULT).split("\r\n")).toEqual([
      '"От";"До";"Проб";"Сумма весов";"Доля, %";"Накоплено, %"',
      '"ниже диапазона";0;1;1;;',
      "0;1;1;1;10;20",
      "1;2;2;3;50;70",
      "2;3;1;2;30;100",
    ]);
  });

  it("counters of the selection and the statistics with and without weights side by side", () => {
    const notes = countsNotes(RESULT.counts, base(), RESULT);
    expect(notes[0]).toBe("строк 7, в расчёте 4");
    expect(notes).toContain("без значения 1");
    expect(notes).toContain("урезано 1");
    expect(notes).toContain("вне диапазона: ниже 1, выше 0");
    const rows = statsTable(RESULT.stats, RESULT.stats_unweighted);
    expect(rows.find((r) => r[0] === "Среднее")).toEqual(["Среднее", "1.5", "1.4"]);
    expect(rows.find((r) => r[0] === "Асимметрия")).toEqual(["Асимметрия", "—", "—"]); // missing — never 0
    expect(rows.find((r) => r[0] === "Геом. среднее")?.[1]).toBe("—");
    expect(rows.map((r) => r[0])).toEqual(expect.arrayContaining(["P5", "P97,5", "P99", "Дисперсия ln"]));
    expect(statsTable(RESULT.stats, null)[0][2]).toBeNull();
  });

  it("the chart in SVG: bars, the cumulative curve, marks; a fixed size for the export", () => {
    const html = renderToStaticMarkup(createElement(HistogramChart, { result: RESULT, y: "percent", cumulative: true, marks: histogramMarks(RESULT, base()), color: "#1f6bff", xLabel: "AU, г/т" }));
    expect(html).toContain(`viewBox="0 0 ${HIST_W} ${HIST_H}"`);
    expect(html.match(/<rect[^>]*fill="#1f6bff"/g)).toHaveLength(3);
    expect(html).toContain('data-curve="cumulative"');
    expect(html).toContain('data-mark="Среднее 1.5"');
    expect(html).not.toContain("NaN");
    const log = renderToStaticMarkup(createElement(HistogramChart, { result: { ...RESULT, log: true, bins: { ...RESULT.bins, edges: [0.01, 0.1, 1, 10] } }, y: "count", cumulative: false, marks: [], color: "#1f6bff", xLabel: "AU" }));
    expect(log).toContain("логарифмическая шкала");
    expect(log).not.toContain("NaN");
  });

  it("the window renders its form before any answer", () => {
    const html = renderToStaticMarkup(createElement(HistogramWindow, { winId: "w", setId: 3, setName: "Набор", settings: base() }));
    for (const label of ["Таблица", "Поле", "Вес", "Отсечка сверху", "Ось Y"]) expect(html).toContain(`aria-label="${label}"`);
    expect(html).toContain("Накопленная кривая");
    expect(html).toContain("Расчёт…");
  });
});

describe("PNG is twice the chart (Т7.25-3)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("the export asks for scale 2 of the chart's SVG and saves through the shell", async () => {
    const saveFile = vi.fn(async () => "C:/Графики/Гистограмма.png");
    vi.stubGlobal("window", { bf: { saveFile } });
    const { exportChart, PNG_SCALE } = await import("../src/charts/core/ChartToolbar");
    const { pngBytes } = await import("../src/charts/striplog/export");
    const svg = { viewBox: { baseVal: { width: HIST_W, height: HIST_H } } } as unknown as SVGSVGElement;
    expect(await exportChart("png", "Гистограмма Набор AU", svg, () => null)).toBe("C:/Графики/Гистограмма.png");
    expect(PNG_SCALE).toBe(2);
    expect(pngBytes).toHaveBeenCalledWith(svg, 2);
    expect(saveFile).toHaveBeenCalledWith("Гистограмма Набор AU.png", "png", expect.any(Uint8Array));
    await exportChart("csv", "Гистограмма", null, () => histogramCsv(RESULT));
    expect(saveFile).toHaveBeenLastCalledWith("Гистограмма.csv", "csv", histogramCsv(RESULT));
  });
});

describe("the core: calculation and the command", () => {
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let pending: { body: Record<string, unknown>; resolve: (r: Response) => void }[] = [];
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    pending = [];
    windowStore.set({ windows: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        const method = init?.method ?? "GET";
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method, path: `${path}${u.search}`, body });
        if (path === "/stats/fields")
          return Promise.resolve(json({ tables: [{ key: "пробы", name: "Пробы", kind: "assay", rows: 7 }], table: "пробы", numeric: [{ name: "length", kind: "length", unit: "м", count: 7 }, { name: "AU", kind: "component", unit: "г/т", count: 6 }], text: [] }));
        if (path === "/stats/histogram") return new Promise<Response>((resolve) => pending.push({ body, resolve }));
        return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindCharts(app);
    app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("an edit computes 300 ms later; an overtaken answer is dropped", async () => {
    vi.useFakeTimers();
    const applied: HistogramResult[] = [];
    const calc = histogramCalculator((r) => applied.push(r), () => undefined);
    calc.request(histogramRequest(base())!);
    vi.advanceTimersByTime(100);
    calc.request(histogramRequest({ ...base(), top_cut: 10 })!);
    vi.advanceTimersByTime(299);
    expect(pending).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(pending.map((p) => p.body.top_cut)).toEqual([10]);
    calc.request(histogramRequest({ ...base(), top_cut: 8 })!);
    vi.advanceTimersByTime(300);
    expect(pending).toHaveLength(2);
    pending[1].resolve(json({ ...RESULT, stats: { ...STATS, max: 8 } }));
    pending[0].resolve(json(RESULT)); // overtaken
    await vi.runAllTimersAsync();
    expect(applied.map((r) => r.stats.max)).toEqual([8]);
    calc.stop();
  });

  it("chart.histogram: unavailable without a set; opens the window «‹имя› · ‹набор›» with the first component", async () => {
    expect(app.registry.reason("chart.histogram")).toBeNull();
    expect(app.registry.reason("chart.qq")).toBe("Функция появится на этапе Э13");
    app.store.set({ setName: null });
    expect(app.registry.reason("chart.histogram")).toBe("Нет активного набора скважин");
    app.store.set({ setName: "Набор" });
    const r = await app.registry.execute("chart.histogram", {});
    expect(r).toEqual({ ok: true, result: "Открыто окно «Гистограмма · AU · по длине · Набор»" });
    const win = windowStore.get().windows[0];
    expect(win).toMatchObject({ kind: "chart-histogram", title: "Гистограмма · AU · по длине · Набор", w: 860, h: 600 });
    expect(win.props?.settings).toMatchObject({ set_id: 3, table: "пробы", field: "AU" });
    expect(renderChartsWindow(win)).not.toBeNull();
    expect(renderChartsWindow({ ...win, kind: "settings" })).toBeNull();
  });

  it("the assistant gets the name, number of samples, mean, median and CV", async () => {
    const run = app.registry.execute("chart.histogram", { field: "AU", weight: "none", top_cut: 4 }, "claude");
    await vi.waitFor(() => expect(pending).toHaveLength(1));
    expect(pending[0].body).toMatchObject({ field: "AU", weight: "none", top_cut: 4, top_mode: "cap" });
    pending[0].resolve(json({ ...RESULT, weighted: false, stats_unweighted: null }));
    expect(await run).toEqual({ ok: true, result: "Гистограмма · AU · без веса: проб 4, среднее 1.5, медиана 1.2, CV 0.533" });
    expect(histogramSummary("Г", RESULT)).toBe("Г: проб 4, среднее 1.5, медиана 1.2, CV 0.533 (с весом)");
  });
});
