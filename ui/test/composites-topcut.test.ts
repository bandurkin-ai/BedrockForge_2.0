// The windows «Урезка» and «Декластеризация» (ТЗ 8.2.1–8.2.3; ADR 0018 §7, decision 8; Э12 12.29): the answer of the
// analysis made safe (a gap stays a gap), the report «до/после» read off the curves, a pick on a curve changes the
// threshold of the window only — the job starts with «Применить» alone; the marks 40 % / 10 % follow the limits of
// the window; the log-probability axis; the SVG curve breaks at a gap (never zero); «размер → среднее» of the last
// declustering with the best size. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startTool, type ResultRow } from "../src/modules/composites/api";
import { CurvePlot, extent, logTicks, nearestIndex, scale, segments, tickText } from "../src/modules/composites/CurvePlot";
import { DeclusCurve, DeclusReportView } from "../src/modules/composites/DeclusWindow";
import { defaultValues, NO_SOURCE, toolBody, type FormValues } from "../src/modules/composites/params";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { AppliedReport, DomainView, fetchAnalysis, ForecastTable, limitsOf, SharesTable, ThresholdRows } from "../src/modules/composites/TopcutWindow";
import { bestPoint, declusFacts, toDeclusReport } from "../src/modules/composites/tools/declus";
import {
  analysisBody,
  domainThreshold,
  forecast,
  latestResult,
  locate,
  markedP,
  marks,
  nearestP,
  normalZ,
  parseThresholds,
  pickValues,
  PROB_TICKS,
  reportRows,
  thresholdsText,
  toAnalysis,
  topcut,
} from "../src/modules/composites/tools/topcut";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// P 90,0 … 99,9; T = P − 80 (10 … 19,9); mean falls 5 → 4,01; n_cut = 100 − P (rounded)
const P = Array.from({ length: 100 }, (_, k) => Number((90 + 0.1 * k).toFixed(1)));
const slice = (from: number, to: number, share: number | null) => ({ from, to, min: from / 10, max: to / 10, mean: (from + to) / 20, metal: share, share_pct: share });
const RAW = {
  field: "AU",
  limits: { decile: 40, percentile: 10 },
  domains: [
    {
      domain: "ОК",
      n: 200,
      length: 400,
      mean: 5,
      cv: 2.5,
      min: 0.01,
      max: 95,
      metal: 2000,
      curves: { p: P, threshold: P.map((p) => Number((p - 80).toFixed(1))), mean: P.map((p) => 5 - (p - 90) * 0.1), cv: P.map((p) => 2.5 - (p - 90) * 0.05), loss_pct: P.map((p) => 100 - p), n_cut: P.map((p) => Math.round(2 * (100 - p))) },
      deciles: Array.from({ length: 10 }, (_, k) => slice(10 * k, 10 * k + 10, k === 9 ? 55 : 5)),
      top: Array.from({ length: 10 }, (_, k) => slice(90 + k, 91 + k, k === 9 ? 12 : 4.8)),
      top_decile_pct: 55,
      top_percentile_pct: 12,
      flag_decile: true,
      flag_percentile: true,
      quantiles: { p: [0.07, 50, 99.93], z: [-3.2, 0, 3.2], value: [0, 1.2, 80] },
    },
    { domain: "", n: 0, length: 0 },
  ],
};
const A = toAnalysis(RAW);
const OK = A.domains[0];

const sent: { path: string; body: Record<string, unknown> }[] = [];
beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
      sent.push({ path, body: init?.body ? JSON.parse(String(init.body)) : {} });
      if (path === "/composites/topcut/analysis") return json(RAW);
      if (path === "/composites/topcut") return json({ source: "jobs", id: 7 });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const form = (over: Record<string, string | boolean>): FormValues => ({ ...defaultValues(topcut.params), ...over });

describe("the answer of the analysis", () => {
  it("is made safe: a domain without samples has empty parts, a gap stays null (never 0)", () => {
    expect(A.field).toBe("AU");
    expect(OK.curves.p).toHaveLength(100);
    expect(OK.deciles).toHaveLength(10);
    const empty = A.domains[1];
    expect(empty.domain).toBe("");
    expect(empty.mean).toBeNull();
    expect(empty.curves.threshold).toEqual([]);
    expect(empty.deciles).toEqual([]);
    expect(toAnalysis({ domains: [{ domain: null, n: 3, mean: Number.NaN, curves: { mean: [1, null, "x"] } }] }).domains[0]).toMatchObject({ domain: "", mean: null, curves: { mean: [1, null, null] } });
  });

  it("the request: the source of the tool, posted to /composites/topcut/analysis only", async () => {
    expect(analysisBody(NO_SOURCE)).toBe("Выберите набор скважин");
    const src = { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"], domain: { kind: "field" as const, table: "5", field: "ZONE" } };
    const body = analysisBody(src);
    expect(body).toEqual({ set_id: 3, table: "5", field: "AU", domain: { table: "5", field: "ZONE" } });
    const a = await fetchAnalysis(body as Record<string, unknown>);
    expect(a.domains[0].curves.threshold[90]).toBe(19);
    expect(sent.map((s) => s.path)).toEqual(["/composites/topcut/analysis"]);
  });
});

describe("the report «до/после» by the curves", () => {
  it("percentile P: a point of the curve exactly, between two — linearly, outside 90–99,9 % — none", () => {
    const f = forecast(OK, { p: 99 })!;
    expect(f).toMatchObject({ threshold: 19, p: 99, n_cut: 2, exact: true });
    expect(f.cut_pct).toBeCloseTo(1, 9);
    expect(f.mean).toBeCloseTo(4.1, 9);
    expect(f.loss_pct).toBeCloseTo(1, 9);
    const g = forecast(OK, { p: 99.95 });
    expect(g).toBeNull();
    const h = forecast(OK, { p: 95.05 })!;
    expect(h.exact).toBe(false);
    expect(h.threshold).toBeCloseTo(15.05, 9);
  });

  it("value T: by the threshold curve; none — not cut; a domain without samples — none", () => {
    const f = forecast(OK, { value: 15.55 })!;
    expect(f.exact).toBe(false);
    expect(f.p).toBeCloseTo(95.55, 9);
    expect(forecast(OK, { value: 5 })).toBeNull(); // below T at P 90
    expect(forecast(OK, null)).toBeNull();
    expect(forecast(A.domains[1], { p: 99 })).toBeNull();
    expect(locate([1, null, 3], 2)).toBeNull();
    expect(locate([1, 2, 3], 2.5)).toEqual({ i: 1, f: 0.5 });
  });

  it("the threshold of a domain from the form: P, T, by domain (an unlisted domain is not cut)", () => {
    expect(domainThreshold(form({ by: "percentile", percentile: "99,5" }), "ОК")).toEqual({ p: 99.5 });
    expect(domainThreshold(form({ by: "value", value: "30" }), "ОК")).toEqual({ value: 30 });
    expect(domainThreshold(form({ by: "value", value: "" }), "ОК")).toBeNull();
    const by = form({ by: "thresholds", thresholds: "ОК=30,5; =5" });
    expect(domainThreshold(by, "ОК")).toEqual({ value: 30.5 });
    expect(domainThreshold(by, "")).toEqual({ value: 5 });
    expect(domainThreshold(by, "ПР")).toBeNull();
  });

  it("thresholds by domain as text: «КОД=значение; …», the rows without a domain — «=T»", () => {
    expect(parseThresholds("ОК=30; ПР=12,5\n=5")).toEqual({ ОК: "30", ПР: "12,5", "": "5" });
    expect(parseThresholds("ОК 30")).toMatch(/КОД=значение/);
    expect(thresholdsText({ ОК: "30", ПР: " ", "": "5" })).toBe("ОК=30; =5");
  });
});

describe("the threshold is never applied by itself", () => {
  it("a pick on a curve changes the form only; the job starts with «Применить» with the picked threshold", async () => {
    await fetchAnalysis({ set_id: 3, table: "5", field: "AU" });
    let v = form({ by: "percentile" });
    v = pickValues(v, OK, 97.33);
    expect(v.percentile).toBe("97,3");
    expect(markedP(OK, domainThreshold(v, "ОК"))).toBe(97.3);
    const asValue = pickValues(form({ by: "value" }), OK, 98);
    expect(asValue.value).toBe("18");
    expect(markedP(OK, domainThreshold(asValue, "ОК"))).toBe(98);
    const byDomain = pickValues(form({ by: "thresholds", thresholds: "ПР=3" }), OK, 99.9);
    expect(byDomain.thresholds).toBe("ПР=3; ОК=19,9");
    expect(nearestP(OK.curves, 120)).toBe(99.9);
    // nothing but the analysis reached the core
    expect(sent.map((s) => s.path)).toEqual(["/composites/topcut/analysis"]);
    const src = { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] };
    const b = toolBody(topcut, src, v);
    expect(b.body).toEqual({ set_id: 3, table: "5", field: "AU", percentile: 97.3 });
    expect(await startTool(topcut.endpoint, b.body!, async () => "cancel")).toEqual({ source: "jobs", id: 7 });
    expect(sent.map((s) => s.path)).toEqual(["/composites/topcut/analysis", "/composites/topcut"]);
    expect(sent[1].body).toEqual({ set_id: 3, table: "5", field: "AU", percentile: 97.3 });
  });

  it("the window renders the source, the form and a disabled «Применить» — no request is made by rendering", () => {
    const node = renderCompositesWindow({ id: "w1", kind: "composites-topcut", title: "Урезка", x: 0, y: 0, w: 900, h: 700, z: 1, maximized: false, props: { args: { set_id: 3, percentile: 99.5 } } });
    const html = renderToStaticMarkup(node as never);
    expect(html).toContain('data-testid="composites-topcut"');
    expect(html).toContain("Источник");
    expect(html).toContain('value="99,5"');
    expect(html).toContain("Порог сам не применяется");
    expect(html).toMatch(/data-testid="composites-apply"[^>]*disabled/);
    expect(sent).toEqual([]);
  });
});

describe("the marks, the curves, the shares", () => {
  it("40 % / 10 % by default; the limits of the window move the marks; a wrong limit — its default", () => {
    expect(marks(OK, { decile: 40, percentile: 10 })).toEqual({ decile: true, percentile: true });
    expect(marks(OK, { decile: 60, percentile: 15 })).toEqual({ decile: false, percentile: false });
    expect(marks({ top_decile_pct: null, top_percentile_pct: null }, { decile: 0, percentile: 0 })).toEqual({ decile: false, percentile: false });
    expect(limitsOf({ decile: "50", percentile: "abc" })).toEqual({ decile: 50, percentile: 10 });
  });

  it("the view of a domain: five curves with the mark of P, the marked rows, the log-probability curve", () => {
    const html = renderToStaticMarkup(createElement(DomainView, { d: OK, field: "AU", values: form({ by: "percentile", percentile: "99" }), limits: { decile: 40, percentile: 10 }, onPick: () => undefined }));
    for (const id of ["threshold", "mean", "cv", "loss", "ncut"]) expect(html).toContain(`data-testid="topcut-curve-${id}"`);
    expect((html.match(/data-testid="curve-mark"/g) ?? []).length).toBe(5);
    expect(html).toContain("Пометка: верхний дециль — 55 % металла (больше 40 %)");
    expect(html).toContain("Пометка: верхний процентиль — 12 % металла (больше 10 %)");
    expect(html).toContain('data-testid="topcut-logprob"');
    expect(html).toContain("Значения ≤ 0 на логарифмической шкале не показаны.");
    expect(html).toContain("T = 19");
    expect(html).toContain("щелчок или ←/→ — выбрать");
    const calm = renderToStaticMarkup(createElement(DomainView, { d: OK, field: "AU", values: form({}), limits: { decile: 60, percentile: 20 } }));
    expect(calm).not.toContain("Пометка:");
    expect(renderToStaticMarkup(createElement(DomainView, { d: A.domains[1], field: "AU", values: form({}), limits: { decile: 40, percentile: 10 } }))).toContain("нет проб");
  });

  it("the shares table marks the last row only when flagged", () => {
    const on = renderToStaticMarkup(createElement(SharesTable, { rows: OK.deciles, caption: "Децили", head: "Дециль", flagged: true, testId: "t" }));
    expect((on.match(/data-flagged="true"/g) ?? []).length).toBe(1);
    expect(on).toContain("90–100 %");
    expect(on).toContain("55 %");
    const off = renderToStaticMarkup(createElement(SharesTable, { rows: OK.deciles, caption: "Децили", head: "Дециль", flagged: false, testId: "t" }));
    expect(off).not.toContain("data-flagged");
  });

  it("the forecast table and the rows of T by domain", () => {
    const html = renderToStaticMarkup(createElement(ForecastTable, { analysis: A, values: form({ by: "value", value: "15,55" }) }));
    expect(html).toContain("≈ ");
    expect(html).toContain("нет проб");
    const by = renderToStaticMarkup(createElement(ForecastTable, { analysis: A, values: form({ by: "thresholds", thresholds: "ОК=19" }) }));
    expect(by).toContain("не урезается");
    expect(by).toContain("2 из 200 (1 %)");
    const rows = renderToStaticMarkup(createElement(ThresholdRows, { analysis: A, values: form({ by: "thresholds", thresholds: "ОК=19" }), onChange: () => undefined }));
    expect(rows).toContain("без домена");
    expect(rows).toContain("99 %");
  });

  it("the axis of probabilities: the normal score of P", () => {
    expect(normalZ(0.5)).toBeCloseTo(0, 12);
    expect(normalZ(0.975)).toBeCloseTo(1.959964, 6);
    expect(normalZ(0.001)).toBeCloseTo(-3.090232, 6);
    expect(normalZ(0.999)).toBeCloseTo(3.090232, 6);
    expect(Number.isNaN(normalZ(0))).toBe(true);
    expect(PROB_TICKS.map((t) => t.label)).toContain("99,9");
  });
});

describe("the SVG curve", () => {
  it("a gap breaks the line and is never drawn as zero; a log axis leaves out ≤ 0", () => {
    const sx = scale(0, 4, 0, 40);
    const sy = scale(0, 10, 100, 0);
    const lin = segments([0, 1, 2, 3, 4], [1, 2, null, 4, 5], sx, sy);
    expect(lin.lines).toEqual(["M0.00 90.00L10.00 80.00", "M30.00 60.00L40.00 50.00"]);
    const log = segments([0, 1, 2], [0, 10, 100], sx, scale(1, 100, 100, 0, true), true);
    expect(log.lines).toEqual(["M10.00 50.00L20.00 0.00"]);
    expect(segments([0, 1, 2], [1, null, 3], sx, sy).dots).toHaveLength(2);
    expect(extent([null, 3, Number.NaN, 1])).toEqual([1, 3]);
    expect(extent([0, 5], true)).toEqual([2.5, 10]);
    expect(extent([null])).toBeNull();
    expect(logTicks(0.5, 30)).toEqual([0.5, 1, 2, 5, 10, 20]);
    expect(logTicks(0.01, 1000)).toEqual([0.01, 0.1, 1, 10, 100, 1000]);
    expect(nearestIndex([1, 2, 3], [5, null, 7], 2.2)).toBe(2);
    expect(tickText(2500000)).toBe("2,5·10⁶");
    expect(tickText(0.5)).toBe("0,5");
  });

  it("renders the line, the mark, the reference, the point; no data — «Нет данных»", () => {
    const html = renderToStaticMarkup(createElement(CurvePlot, { title: "Т", xs: [1, 2, 3], ys: [1, 2, 3], xLabel: "x", markX: 2, refY: { value: 1.5, label: "до" }, point: { x: 3, y: 3, label: "лучший" } }));
    expect(html).toContain("<path");
    expect(html).toContain('data-testid="curve-mark"');
    expect(html).toContain('data-testid="curve-ref"');
    expect(html).toContain('data-testid="curve-point"');
    expect(html).not.toContain("tabindex"); // not pickable without onPick
    expect(renderToStaticMarkup(createElement(CurvePlot, { title: "Т", xs: [], ys: [], xLabel: "x" }))).toContain("Нет данных");
  });
});

const result = (over: Partial<ResultRow>): ResultRow => ({ id: 1, set_id: 3, table_id: 5, table: "Пробы", op: "topcut", op_title: "Урезка", makes_table: false, name: "", params: { table: "5", field: "AU", percentile: 99 }, inputs: {}, sha256: "", algo: "", report: {}, written: [], job_id: 1, created_at: "2026-11-20T10:00:00Z", stale: false, reason: null, ...over });

describe("the last applied operation of the table and field", () => {
  it("the newest result of the op, table and field", () => {
    const rows = [result({ id: 1 }), result({ id: 2, created_at: "2026-11-20T11:00:00Z" }), result({ id: 3, params: { table: "5", field: "CU" }, created_at: "2026-11-21T00:00:00Z" }), result({ id: 4, op: "declus", created_at: "2026-11-22T00:00:00Z" })];
    expect(latestResult(rows, "topcut", "5", "AU")?.id).toBe(2);
    expect(latestResult(rows, "declus", "5", "AU")?.id).toBe(4);
    expect(latestResult(rows, "topcut", "6", "AU")).toBeNull();
    expect(latestResult(rows, "topcut", "5", null)).toBeNull();
  });

  it("the report of the top cut: the domains, then «Всего»; a domain not cut", () => {
    const report = {
      columns: ["AU_CUT", "AU_CUT_F"],
      domains: [
        { domain: "ОК", threshold: 19, n: 200, n_cut: 2, cut_pct: 1, mean_before: 5, mean_after: 4.1, cv_before: 2.5, cv_after: 2, max_before: 95, max_after: 19, loss_pct: 18 },
        { domain: "", threshold: null, n: 10, n_cut: 0, cut_pct: 0, mean_before: 1, mean_after: 1, cv_before: 1, cv_after: 1, max_before: 3, max_after: 3, loss_pct: 0 },
      ],
      total: { n: 210, n_cut: 2, length: 420, mean_before: 4.8, mean_after: 3.95, cv_before: 2.6, cv_after: 2.1, max_before: 95, max_after: 19, loss_pct: 17.5 },
    };
    const rows = reportRows(report);
    expect(rows.map((r) => r.domain)).toEqual(["ОК", "", null]);
    expect(rows[2].cut_pct).toBeCloseTo((100 * 2) / 210, 9);
    expect(reportRows({ domains: [report.domains[0]], total: report.total })).toHaveLength(1);
    expect(reportRows(null)).toEqual([]);
    const html = renderToStaticMarkup(createElement(AppliedReport, { result: result({ report, stale: true, reason: "изменена таблица «Пробы»" }) }));
    expect(html).toContain("AU_CUT, AU_CUT_F");
    expect(html).toContain("не урезался");
    expect(html).toContain("Всего");
    expect(html).toContain("устарел: изменена таблица «Пробы»");
  });
});

describe("«Декластеризация»", () => {
  const REPORT = { method: "cell", n: 120, n_missing: 3, naive_mean: 2.4, mean: 1.9, uniform: false, best_size: 50, sizes: [10, 50, 100], means: [2.2, 1.9, 2.0], min_weight: 0.2, max_weight: 4.1, n_zero: 0, notes: [] };

  it("the report: the curve «размер → среднее», the best size, the facts; a gap stays a gap", () => {
    const r = toDeclusReport(REPORT)!;
    expect(bestPoint(r)).toEqual({ x: 50, y: 1.9 });
    expect(declusFacts(r).find((f) => f.label === "Лучший размер ячейки, м")?.value).toBe("50");
    expect(toDeclusReport({ ...REPORT, means: [2.2, null, Number.NaN] })!.means).toEqual([2.2, null, null]);
    expect(toDeclusReport({ summary: {} })).toBeNull();
    const uniform = toDeclusReport({ ...REPORT, uniform: true, best_size: null })!;
    expect(declusFacts(uniform).find((f) => f.label === "Лучший размер ячейки, м")?.value).toBe("нет (веса равны 1)");
    const poly = toDeclusReport({ ...REPORT, method: "polygon", sizes: [], means: [], best_size: null, nx: 40, ny: 40, n_zero: 2 })!;
    expect(declusFacts(poly).map((f) => f.label)).toEqual(expect.arrayContaining(["Сетка полигонов", "Проб с нулевым весом"]));
    expect(renderToStaticMarkup(createElement(DeclusCurve, { report: poly, field: "AU" }))).toBe("");
  });

  it("the curve with the best size and the naive mean; the window of the last declustering", () => {
    const html = renderToStaticMarkup(createElement(DeclusCurve, { report: toDeclusReport(REPORT)!, field: "AU" }));
    expect(html).toContain('data-testid="declus-curve"');
    expect(html).toContain('data-testid="curve-point"');
    expect(html).toContain("без весов 2,4");
    const view = renderToStaticMarkup(createElement(DeclusReportView, { result: result({ op: "declus", report: { ...REPORT, notes: ["Проб с нулевым весом: 2"] } }) }));
    expect(view).toContain("Последняя декластеризация поля AU");
    expect(view).toContain("Проб с нулевым весом: 2");
    const node = renderCompositesWindow({ id: "w2", kind: "composites-declus", title: "Декластеризация", x: 0, y: 0, w: 900, h: 600, z: 1, maximized: false, props: { args: { set_id: 3, method: "cell3d" } } });
    const win = renderToStaticMarkup(node as never);
    expect(win).toContain('data-testid="composites-declus"');
    expect(win).toContain("Анизотропия Z");
    expect(win).toContain("после «Применить»");
    expect(win).toMatch(/data-testid="composites-apply"[^>]*disabled/);
  });
});
