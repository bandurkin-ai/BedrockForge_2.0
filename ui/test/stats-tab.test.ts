// The bottom tab «Статистика» (ТЗ 7.29; ADR 0019 §7; перенос `stats.test.ts` BF): the tab in the shell and its
// buttons on the ribbon (variogram, map, local — unavailable with the stage Э16), the defaults of the forms and
// the bodies of `POST /stats/tab/*`, the facts (a missing number is «—», never 0), `stats.open` for the person
// and for the assistant, a refusal of 20 001 intervals shown with its hint (Т7.29-2), an overtaken answer and an
// edit of the data drop the result. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { tablesStore } from "../src/modules/tables/state";
import type { BootstrapAnswer, DeclusAnswer, NscoreAnswer } from "../src/modules/stats/api";
import { bindStats } from "../src/modules/stats/bind";
import { STATS_BUTTONS } from "../src/modules/stats/commands";
import { DeclusPanel } from "../src/modules/stats/DeclusPanel";
import { backtrBody, bestPoint, bootRows, bootstrapBody, bootstrapCsv, bootstrapText, declusBody, declusCsv, declusCurve, declusFacts, declusText, DEFAULT_FORMS, fmt, meanChange, nscoreBody, nscoreFacts, parseScores, pickContext, problemOf, scoreMoments, type TabContext } from "../src/modules/stats/forms";
import { BootstrapPanel } from "../src/modules/stats/BootstrapPanel";
import { NscorePanel } from "../src/modules/stats/NscorePanel";
import { StatsPanel } from "../src/modules/stats/StatsPanel";
import { initialStats, runAnalysis, setContext, statsDataChanged, statsStore } from "../src/modules/stats/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { AppContext } from "../src/shell/AppContext";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { EMPTY_CONTEXT } from "../src/shell/commands";
import { BOTTOM_TITLES, DEFAULT_VIEW, DEFAULT_WORKSPACE, normalizeWorkspace, setPanel } from "../src/shell/workspace";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const CTX: TabContext = { setId: 3, table: "пробы", field: "AU" };
const COUNTS = { rows: 12, missing: 1, no_position: 1, used: 10 };

const DECLUS: DeclusAnswer = {
  set_id: 3, table: "Пробы", field: "AU", method: "cell", count: 10, naive_mean: 2, declustered_mean: 1.5, weight_min: 0.4, weight_max: 2.5,
  zero_weights: 0, uniform: false, best_cell_size: 50, cell_sizes: [0, 25, 50, 75], means: [2, 1.8, 1.5, null], cmin: 25, cmax: 75, ncell: 3, noff: 8,
  minmax: "min", notes: ["Размер 75 м: нет ячеек"], counts: COUNTS,
};

const NSCORE: NscoreAnswer = { set_id: 3, table: "Пробы", field: "AU", count: 3, values: [0.5, 1, 4], scores: [-0.97, 0, 0.97], table_rows: 3, zmin: 0.5, zmax: 4, rows: [7, 8, 9], sample_scores: [-0.97, 0, 0.97], weighted: false, counts: COUNTS };

const entry = (observed: number | null) => ({ observed, observed_weighted: null, defined: observed === null ? 0 : 1000, mean: observed, std: observed === null ? null : 0.1, ci_low: observed === null ? null : observed - 0.2, ci_high: observed === null ? null : observed + 0.2, edges: observed === null ? [] : [observed - 0.3, observed, observed + 0.3], counts: observed === null ? [] : [400, 600] });
const BOOT: BootstrapAnswer = {
  set_id: 3, table: "Пробы", field: "AU", count: 10, n_boot: 1000, seed: 42, confidence: 0.95, weighted: false, counts: COUNTS,
  statistics: { mean: entry(2), median: entry(1.5), std: entry(0.8), cv: entry(null), p10: entry(0.3), p90: entry(4) },
};

describe("the tab in the shell (перенос `stats.test.ts` BF)", () => {
  it("is a bottom tab that opens the dock and survives a reload of the stored layout", () => {
    expect(BOTTOM_TITLES.stats).toBe("Статистика");
    expect(setPanel(DEFAULT_WORKSPACE, "bottom", true, "stats")).toMatchObject({ bottom: true, bottomTab: "stats" });
    expect(normalizeWorkspace({ bottomTab: "stats" }).bottomTab).toBe("stats");
  });

  it("puts every analysis on the ribbon; variogram, map, local — unavailable with the stage Э16", () => {
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW)).filter((i) => i.command === "stats.open");
    expect(items.map((i) => i.args?.analysis)).toEqual(STATS_BUTTONS.map((b) => b.analysis));
    const reason = (ctx: typeof EMPTY_CONTEXT) => Object.fromEntries(items.map((i) => [i.args?.analysis, i.unavailable?.(ctx) ?? null]));
    expect(reason({ ...EMPTY_CONTEXT, hasProject: true })).toEqual({ declus: "Нет активного набора скважин", nscore: "Нет активного набора скважин", bootstrap: "Нет активного набора скважин", variogram: "Появится на этапе Э16", map: "Появится на этапе Э16", local: "Появится на этапе Э16" });
    expect(reason({ ...EMPTY_CONTEXT, hasProject: true, hasSet: true })).toMatchObject({ declus: null, nscore: null, bootstrap: null, variogram: "Появится на этапе Э16" });
  });
});

describe("the defaults and the bodies", () => {
  it("as BF: cells, 20 sizes, 8 offsets, the least mean; the bootstrap — 1000 realizations, 95 %, a new seed", () => {
    expect(DEFAULT_FORMS.declus).toMatchObject({ method: "cell", cmin: "", cmax: "", ncell: "20", noff: "8", minmax: "min" });
    expect(DEFAULT_FORMS.nscore).toMatchObject({ weighted: false, zmin: "", zmax: "" });
    expect(DEFAULT_FORMS.bootstrap).toMatchObject({ nBoot: "1000", confidence: "0.95", seed: "", weighted: false, statistic: "mean" });
    expect(declusBody(CTX, DEFAULT_FORMS.declus).body).toEqual({ set_id: 3, table: "пробы", field: "AU", declus: { method: "cell", cmin: null, cmax: null, ncell: 20, noff: 8, minmax: "min" } });
    expect(nscoreBody(CTX, DEFAULT_FORMS.nscore).body).toEqual({ set_id: 3, table: "пробы", field: "AU", declus: null, zmin: null, zmax: null });
    expect(bootstrapBody(CTX, DEFAULT_FORMS.bootstrap).body).toEqual({ set_id: 3, table: "пробы", field: "AU", declus: null, n_boot: 1000, seed: null, confidence: 0.95 });
  });

  it("a decimal comma is a number; a wrong field is told, the core is not asked", () => {
    const cells = { ...DEFAULT_FORMS.declus, cmin: "12,5", cmax: "100" };
    expect(declusBody(CTX, cells).body?.declus).toMatchObject({ cmin: 12.5, cmax: 100 });
    expect(declusBody(CTX, { ...cells, cmin: "200" }).reason).toBe("«Ячейка от» больше «до»");
    expect(declusBody(CTX, { ...cells, ncell: "2,5" }).reason).toBe("«Размеров» — целое число");
    expect(declusBody(CTX, { ...cells, noff: "abc" }).reason).toBe("«Сдвигов» — не число");
    const poly = { ...DEFAULT_FORMS.declus, method: "polygon" as const };
    expect(declusBody(CTX, poly).body?.declus).toEqual({ method: "polygon", nx: null, ny: null, bounds: null });
    expect(declusBody(CTX, { ...poly, bounds: ["0", "100", "", ""] }).reason).toMatch(/все четыре/);
    expect(declusBody(CTX, { ...poly, nx: "40", bounds: ["0", "100", "-5", "5"] }).body?.declus).toEqual({ method: "polygon", nx: 40, ny: null, bounds: [0, 100, -5, 5] });
    expect(bootstrapBody(CTX, { ...DEFAULT_FORMS.bootstrap, nBoot: "5" }).reason).toBe("«Реализаций» — не меньше 10");
    expect(nscoreBody(CTX, { ...DEFAULT_FORMS.nscore, weighted: true }).body?.declus).toMatchObject({ method: "cell" });
  });

  it("the back-transform: scores by «;» or spaces, the tails and their parameters only where they are", () => {
    expect(parseScores("-1,5; 0  1")).toEqual([-1.5, 0, 1]);
    const b = backtrBody(CTX, DEFAULT_FORMS.nscore, { scores: "-2; 2", ltail: "none", utail: "hyperbolic", ltpar: "2", utpar: "1,5" }).body!;
    expect(b).toMatchObject({ scores: [-2, 2], ltail: null, utail: "hyperbolic", ltpar: null, utpar: 1.5, declus: null, zmin: null, zmax: null });
    expect(backtrBody(CTX, DEFAULT_FORMS.nscore, { ...DEFAULT_FORMS.backtr, scores: " " }).reason).toMatch(/хотя бы одну/);
    expect(backtrBody(CTX, DEFAULT_FORMS.nscore, { ...DEFAULT_FORMS.backtr, scores: "1; x" }).reason).toBe("«x» — не число");
  });

  it("the context: the chosen table and component, else the first table and its first component", () => {
    const fields = { tables: [], table: "пробы", numeric: [{ name: "length", kind: "length" as const, unit: "м", role: "length" }, { name: "AU", kind: "component" as const, unit: "г/т", role: "component" }], text: [] };
    expect(pickContext(3, fields, null)).toEqual(CTX);
    expect(pickContext(3, fields, "CU")).toEqual({ ...CTX, field: "CU" });
  });
});

describe("the facts", () => {
  it("declustering: the means, the change, the best size; size 0 is the reference, not the curve", () => {
    const f = Object.fromEntries(declusFacts(DECLUS));
    expect(f).toMatchObject({ Метод: "ячейки в плане", Интервалов: "10", "Среднее без весов": "2", "Среднее с весами": "1,5", Изменение: "−25 %", "Лучший размер ячейки": "50 м" });
    expect(declusCurve(DECLUS)).toEqual({ xs: [25, 50, 75], ys: [1.8, 1.5, null] });
    expect(bestPoint(DECLUS)).toEqual({ x: 50, y: 1.5 });
    expect(Object.fromEntries(declusFacts({ ...DECLUS, uniform: true }))["Лучший размер ячейки"]).toBe("нет: веса равны 1");
    expect(bestPoint({ ...DECLUS, uniform: true })).toBeNull();
    expect(meanChange({ naive_mean: 0, declustered_mean: 1 })).toBeNull();
    expect(Object.fromEntries(declusFacts({ ...DECLUS, declustered_mean: null })).Изменение).toBe("—");
    expect(declusCsv(DECLUS)).toBe('"Размер ячейки, м";"Среднее AU"\r\n25;1.8\r\n50;1.5\r\n75;');
    expect(declusText(DECLUS)).toContain("среднее без весов 2, с весами 1,5; лучший размер ячейки 50 м");
  });

  it("normal scores and the bootstrap: a statistic not defined is «—»", () => {
    expect(scoreMoments([-1, 0, 1])).toEqual({ mean: 0, std: Math.sqrt(2 / 3) });
    expect(Object.fromEntries(nscoreFacts(NSCORE))["Значения: от … до"]).toBe("0,5 … 4");
    const cv = bootRows(BOOT).find((r) => r.id === "cv")!;
    expect([cv.observed, cv.low, cv.high, cv.defined]).toEqual(["—", "—", "—", "0"]);
    expect(bootstrapCsv(BOOT).split("\r\n")[4]).toBe('"CV";;;;;;;0');
    expect(bootstrapText(BOOT)).toContain("реализаций 1");
    expect(bootstrapText(BOOT)).toContain("Среднее 2 [1,8; 2,2]");
    expect(fmt(null)).toBe("—");
    expect(fmt(0)).toBe("0");
    expect(fmt(0.00002)).toBe("2·10⁻⁵");
  });

  it("the panels draw their results; the declustering curve marks the best size and the plain mean", () => {
    const app = createApp({ persist: false });
    statsStore.set({ ...initialStats(), runs: { declus: { busy: false, error: null, result: DECLUS }, nscore: { busy: false, error: null, result: NSCORE }, backtr: { busy: false, error: null, result: { set_id: 3, table: "Пробы", field: "AU", values: [0.5, null], scores: [-3, 9] } }, bootstrap: { busy: false, error: null, result: BOOT } } });
    const wrap = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
    const d = wrap(createElement(DeclusPanel, { ctx: CTX }));
    expect(d).toContain('data-testid="plot-point"');
    expect(d).toContain("без весов 2");
    expect(d).toContain("Размер 75 м: нет ячеек");
    expect(d).toContain("Интервалов в таблице: 12; без значения — 1, без положения на скважине — 1; в расчёте — 10.");
    const n = wrap(createElement(NscorePanel, { ctx: CTX }));
    expect(n).toContain('aria-label="Таблица преобразования"');
    expect(n).toContain("Обратное преобразование");
    expect(n).toContain("<td>9</td><td>—</td>"); // the hyperbolic tail far out: no number, not 0
    const b = wrap(createElement(BootstrapPanel, { ctx: CTX }));
    expect(b).toContain("Интервал 95 %");
    expect(b).toContain("Повторить с этим зерном");
    expect(b.match(/data-testid="plot-mark"/g)).toHaveLength(3);
  });
});

describe("stats.open and the core", () => {
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  let answer: (path: string, body: Record<string, unknown>) => Promise<Response>;
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    statsStore.set(initialStats());
    answer = (path) => Promise.resolve(path === "/stats/tab/declus" ? json(DECLUS) : json({ code: "not_found", message: "Нет", hint: "" }, 404));
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        if (path === "/stats/fields")
          return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 12, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }, { name: "CU", unit: "%", role: "component" }], texts: [] }] }));
        if (!path.startsWith("/stats/tab/")) return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
        sent.push({ path, body });
        return answer(path, body);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindStats(app);
    app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("is unavailable without a set; variogram, map, local are refused with the stage Э16", async () => {
    expect(app.registry.reason("stats.open")).toBeNull();
    app.store.set({ setName: null });
    expect(app.registry.reason("stats.open")).toBe("Нет активного набора скважин");
    app.store.set({ setName: "Набор" });
    for (const analysis of ["variogram", "map", "local"]) {
      const r = await app.registry.execute("stats.open", { analysis }, "claude");
      expect(r.ok).toBe(false);
      expect(r.result).toMatch(/появится на этапе Э16/);
    }
    expect(sent).toHaveLength(0);
  });

  it("from the ribbon: opens the tab on the analysis, keeps the chosen component, computes nothing", async () => {
    setContext({ component: "CU" });
    const r = await app.registry.execute("stats.open", { analysis: "bootstrap" });
    expect(r).toEqual({ ok: true, result: "Открыта вкладка «Статистика» · Бутстрап · Набор" });
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: "stats" });
    expect(statsStore.get()).toMatchObject({ analysis: "bootstrap", component: "CU" });
    expect(sent).toHaveLength(0);
  });

  it("from the assistant: computes with the forms of the tab (the first table and component) and tells the result", async () => {
    const r = await app.registry.execute("stats.open", { analysis: "declus" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/^Декластеризация AU \(Пробы\): интервалов 10; среднее без весов 2, с весами 1,5/);
    expect(sent).toEqual([{ path: "/stats/tab/declus", body: { set_id: 3, table: "пробы", field: "AU", declus: { method: "cell", cmin: null, cmax: null, ncell: 20, noff: 8, minmax: "min" }, weights: false } }]);
    expect(statsStore.get().runs.declus.result).toEqual(DECLUS);
  });

  it("20 001 intervals: a refusal shown with the advice to composite (Т7.29-2)", async () => {
    const hint = "Скомпозитируйте пробы («Анализ → Обработка проб → Композиты») и выберите таблицу композитов. Веса декластеризации для всей таблицы считает «Анализ → Обработка проб → Декластеризация».";
    answer = () => Promise.resolve(json({ code: "too_many_samples", message: "Слишком много интервалов: 20 001, вкладка «Статистика» считает не больше 20 000.", hint, param: "table" }, 422));
    const r = await app.registry.execute("stats.open", { analysis: "nscore", component: "AU" }, "claude");
    expect(r).toEqual({ ok: false, result: `Слишком много интервалов: 20 001, вкладка «Статистика» считает не больше 20 000. ${hint}` });
    const run = statsStore.get().runs.nscore;
    expect(run).toMatchObject({ busy: false, result: null, error: { code: "too_many_samples", composite: true, hint } });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(NscorePanel, { ctx: CTX })));
    expect(html).toContain('role="alert" data-code="too_many_samples"');
    expect(html).toContain("20 001");
    expect(html).toContain("Скомпозитируйте пробы");
    expect(html).toContain("Композиты…");
    expect(problemOf(new Error("x")).composite).toBe(false);
  });

  it("an overtaken answer is dropped; an edit of the data of the set drops the result, of another set — not", async () => {
    const waiting: ((r: Response) => void)[] = [];
    answer = () => new Promise<Response>((resolve) => waiting.push(resolve));
    const first = runAnalysis("declus");
    const second = runAnalysis("declus");
    await vi.waitFor(() => expect(waiting).toHaveLength(2));
    waiting[1](json({ ...DECLUS, count: 2 }));
    await second;
    waiting[0](json(DECLUS)); // overtaken
    await first;
    expect(statsStore.get().runs.declus.result?.count).toBe(2);
    statsDataChanged(99);
    expect(statsStore.get().runs.declus.result?.count).toBe(2);
    const rev = statsStore.get().rev;
    statsDataChanged(3);
    expect(statsStore.get()).toMatchObject({ rev: rev + 1, runs: { declus: { result: null } } });
    setContext({ component: "CU" });
    setContext({ setId: 4 });
    expect(statsStore.get()).toMatchObject({ setId: 4, table: null, component: null });
  });

  it("the tab: the analyses with the Э16 ones unavailable, the set, the table, the component", () => {
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(StatsPanel)));
    for (const label of ["Набор скважин", "Таблица", "Компонент"]) expect(html).toContain(`aria-label="${label}"`);
    expect(html.match(/disabled="" title="Появится на этапе Э16"/g)).toHaveLength(3);
    expect(html).toContain("Декластеризация");
    expect(html).toContain("Не больше 20 000 интервалов.");
  });
});
