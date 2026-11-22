// The Э16 analyses of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11, решение 21; подшаг 16.26) on the answers of the
// real core (`core/tests/data/stats/tab_vario_answers.json`, made by `core/tests/test_stats_tab_vario_answers.py`
// through `POST /stats/tab/{variogram|variogram-model|map|local}`): the bodies of the forms, the points (a lag
// without pairs is not a point — never 0), the model curve with and without data, the map in plan (north up, east
// right; an empty cell — hatched, not 0), the uncertainty maps, the assistant's texts, 20 001 intervals with the
// ways out (composites, the window «Вариография»). The core is a fetch stub answering the stored answers.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TabLocalAnswer, TabMapAnswer, TabModelAnswer, TabVariogramAnswer } from "../src/modules/stats/api";
import { bindStats } from "../src/modules/stats/bind";
import { pickContext, type TabContext } from "../src/modules/stats/forms";
import { DEFAULT_VARIO_FORMS, localBody, localCsv, localGrid, localText, mapBody, mapCsv, mapFill, mapText, modelBody, NEW_STRUCTURE, variogramBody, variogramCsv, variogramFacts, variogramText, varioPoints, type VariogramForm } from "../src/modules/stats/formsVario";
import { LocalPanel } from "../src/modules/stats/LocalPanel";
import { MapPanel } from "../src/modules/stats/MapPanel";
import { varioArgs } from "../src/modules/stats/Parts";
import { initialStats, runModelCurve, setForm, statsStore, type StatsRuns } from "../src/modules/stats/state";
import { curveFor, VariogramPanel, variogramSeries } from "../src/modules/stats/VariogramPanel";
import { planBox } from "../src/modules/stats/VarioPlots";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { AppContext } from "../src/shell/AppContext";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/stats/tab_vario_answers.json"), "utf-8"));
const V = A.variogram as TabVariogramAnswer;
const OMNI = A.variogram_omni as TabVariogramAnswer;
const VM = A.variogram_model as TabModelAnswer;
const MAP = A.map as TabMapAnswer;
const SPARSE_MAP = A.map_sparse as TabMapAnswer;
const LOCAL = A.local as TabLocalAnswer;
const TIGHT = A.local_tight as TabLocalAnswer;
const CTX: TabContext = { setId: V.set_id, table: "пробы", field: "AU" };
const MODEL_FORM: VariogramForm = { ...DEFAULT_VARIO_FORMS.variogram, withModel: true, nugget: "0,1", structures: [{ it: "spherical", cc: "0,9", hmaj: "150", hmin: "80", azi: "30" }] };

/** The model as the core reads it back: the GSLIB code `it: 1` of the body is «spherical» in the answer. */
const MODEL = { nugget: V.model!.nugget, structures: V.model!.structures };
const sp = (t: unknown): string => String(t).replace(/\u00a0/g, " ");

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const idle = { busy: false, error: null, result: null };
const withRuns = (runs: Partial<StatsRuns>) => statsStore.set({ ...initialStats(), runs: { ...initialStats().runs, ...runs } });

describe("the bodies of the forms", () => {
  it("defaults of BF: empty — the core's; the body of the core's fixture is what the form sends", () => {
    expect(variogramBody(CTX, DEFAULT_VARIO_FORMS.variogram).body).toEqual({ set_id: CTX.setId, table: "пробы", field: "AU", lag: null, nlag: 10, lag_tol: null, directions: "omni", min_pairs: 30, standardize: false });
    const f = { ...MODEL_FORM, lag: "25", nlag: "8", directions: "four" as const, minPairs: "10" };
    const { set_id: _s, table: _t, field: _f, model: _m, ...want } = A.bodies.variogram;
    const { set_id: _s2, table: _t2, field: _f2, model, ...got } = variogramBody(CTX, f).body!;
    expect(got).toEqual({ ...want, lag_tol: null, standardize: false });
    expect(model).toEqual(MODEL); // the model — numbers only: «0,1» → 0.1
    expect(mapBody(CTX, { ...DEFAULT_VARIO_FORMS.map, cell: "20", n: "6", minPairs: "3" }).body).toMatchObject({ cell: 20, n: 6, min_pairs: 3, standardize: false });
    expect(localBody(CTX, { ...DEFAULT_VARIO_FORMS.local, nx: "12", ny: "10", k: "6", realizations: "20", seed: "11", threshold: "0,5" }).body).toMatchObject({ grid: { nx: 12, ny: 10 }, k: 6, power: 2, radius: null, realizations: 20, seed: 11, threshold: 0.5 });
  });

  it("a wrong field is told and the core is not asked", () => {
    const v = DEFAULT_VARIO_FORMS.variogram;
    expect(variogramBody(CTX, { ...v, lag: "10", lagTol: "12" }).reason).toBe("«Допуск лага» больше лага");
    expect(variogramBody(CTX, { ...v, nlag: "201" }).reason).toBe("«Лагов» — не больше 200");
    expect(variogramBody(CTX, { ...v, lag: "0" }).reason).toBe("«Лаг» — больше 0");
    expect(variogramBody(CTX, { ...MODEL_FORM, structures: [{ ...NEW_STRUCTURE, hmaj: "" }] }).reason).toBe("Структура 1: задайте вклад и дальность");
    expect(variogramBody(CTX, { ...MODEL_FORM, structures: Array(5).fill(NEW_STRUCTURE) }).reason).toBe("Структур больше 4");
    expect(mapBody(CTX, { ...DEFAULT_VARIO_FORMS.map, n: "101" }).reason).toBe("«Ячеек в каждую сторону» — не больше 100");
    expect(localBody(CTX, { ...DEFAULT_VARIO_FORMS.local, k: "65" }).reason).toBe("«Соседей» — не больше 64");
    expect(localBody(CTX, { ...DEFAULT_VARIO_FORMS.local, power: "0" }).reason).toBe("«Степень» — больше 0");
    expect(localBody(CTX, { ...DEFAULT_VARIO_FORMS.local, nx: "1" }).reason).toBe("«Узлов по X» — не меньше 2");
  });

  it("the curve of the model: up to the data, else «Кривая до», else 1,5 × the range of the core; no structures and no length — told", () => {
    expect(modelBody(MODEL_FORM, V).body).toMatchObject({ nugget: 0.1, h_max: V.lag * V.nlag + V.lag_tol, n: 101, directions: "omni" });
    expect(modelBody(MODEL_FORM, null).body?.h_max).toBeNull();
    expect(modelBody({ ...MODEL_FORM, hMax: "300" }, V).body?.h_max).toBe(300);
    expect(modelBody({ ...MODEL_FORM, structures: [] }, null).reason).toBe("Задайте «Кривая до» или хотя бы одну структуру");
    expect(A.model_no_h_max.body).toMatchObject({ code: "bad_param", param: "h_max" }); // the core refuses the same
  });
});

describe("the answers of the core", () => {
  it("variogram: a lag without pairs is not a point; few pairs are marked; the facts and the CSV", () => {
    for (const d of [...V.directions, ...OMNI.directions]) {
      const pts = varioPoints(d);
      expect(pts).toHaveLength(d.gamma!.filter((g) => g !== null).length);
      expect(pts.every((p) => p.n > 0 && Number.isFinite(p.gamma))).toBe(true);
    }
    const gaps = A.gaps as TabVariogramAnswer;
    const d = gaps.directions[0];
    expect(d.gamma!.some((g) => g === null)).toBe(true);
    expect(varioPoints(d)).toHaveLength(d.n.filter((n) => n > 0).length);
    expect(variogramCsv(gaps).split("\r\n").some((l) => /^"всенаправленная";[^;]+;;;0;"мало пар";$/.test(l))).toBe(true); // empty, not 0
    expect(Object.fromEntries(variogramFacts(V))).toMatchObject({ Интервалов: V.count.toLocaleString("ru-RU"), Направления: "0°, 45°, 90°, 135°" });
    expect(sp(variogramText(V))).toMatch(/^Вариограмма AU \(Пробы\) в плане: интервалов 4 035/);
    expect(variogramText(V)).toContain("Модель: C0 0,1; 1: сферическая 0,9, 150 × 80 м, 30°; порог 1.");
    expect(variogramText(A.sparse)).toContain("без значения — 30");
  });

  it("the series: the model curve of the core along each direction of the data; without data — the curves alone", () => {
    const four = variogramSeries(V, { ...VM, directions: VM.directions });
    expect(four.map((s) => s.label)).toEqual(["азимут 0° ± 22,5°", "азимут 45° ± 22,5°", "азимут 90° ± 22,5°", "азимут 135° ± 22,5°"]);
    expect(four.every((s) => s.curve && s.curve.h.length === VM.n)).toBe(true);
    expect(curveFor(VM, { az: 0, omni: true })).toBe(VM.directions[0]);
    expect(curveFor(VM, { az: 10, omni: false })).toBeNull();
    const alone = variogramSeries(null, VM);
    expect(alone.map((s) => s.label)).toEqual(["модель, азимут 0°", "модель, азимут 45°", "модель, азимут 90°", "модель, азимут 135°"]);
    expect(alone.every((s) => s.points.length === 0)).toBe(true);
  });

  it("map: u = ΔX (column), v = ΔY (row from the south); an empty cell is null, the CSV leaves it empty", () => {
    expect(MAP.u).toHaveLength(2 * MAP.nu + 1);
    expect(MAP.gamma).toHaveLength(MAP.v.length);
    const { filled, empty } = mapFill(SPARSE_MAP);
    expect(empty).toBe(SPARSE_MAP.gamma.flat().filter((g) => g === null).length);
    expect(filled + empty).toBe(SPARSE_MAP.u.length * SPARSE_MAP.v.length);
    expect(empty).toBeGreaterThan(0);
    const lines = mapCsv(SPARSE_MAP).split("\r\n");
    expect(lines).toHaveLength(1 + filled + empty);
    expect(lines.some((l) => /;;\d+$/.test(l))).toBe(true); // γ empty, the pairs told
    expect(mapText(SPARSE_MAP)).toContain(`пустых (пар меньше 3) ${empty}`);
  });

  it("local: the grids by rows of y; no threshold — no probability; a node out of reach — null", () => {
    expect(localGrid(LOCAL, "prob")).toBe(LOCAL.prob);
    expect(localGrid(TIGHT, "prob")).toBeNull();
    expect(TIGHT.mean.flat().filter((v) => v === null)).toHaveLength(TIGHT.empty);
    expect(localCsv(TIGHT).split("\r\n")[1]).toMatch(/^[\d.]+;[\d.]+;;;;;$/);
    expect(sp(localText(LOCAL))).toMatch(/^Карта неопределённости AU \(Пробы\) \(IDW в плане\): интервалов 4 035, скважин 49, реализаций 20, зерно 11/);
    expect(localText(LOCAL)).toContain("вероятность ≥ 0,5");
  });

  it("the plan keeps one scale on both axes", () => {
    const b = planBox({ width: 500, height: 400, margin: { left: 0, right: 0, top: 0, bottom: 0 }, x0: 0, x1: 500, y0: 0, y1: 400 }, 100, 50);
    expect((b.x1 - b.x0) / 100).toBeCloseTo((b.y1 - b.y0) / 50, 12);
    expect([b.x0, b.x1, b.y0, b.y1]).toEqual([0, 500, 75, 325]);
  });
});

describe("the panels", () => {
  let app: App;
  beforeEach(() => {
    app = createApp({ persist: false });
  });
  const wrap = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));

  it("variogram: the points of four directions with the model, the pairs of the lags, the window «Вариография»", () => {
    withRuns({ variogram: { busy: false, error: null, result: { data: V, curve: VM } } });
    setForm("variogram", { withModel: true });
    const html = wrap(createElement(VariogramPanel, { ctx: CTX }));
    expect(html.match(/data-testid="vario-model"/g)).toHaveLength(4);
    expect(html).toContain('aria-label="Число пар в лагах"');
    expect(html).toContain('aria-label="Структуры модели"');
    expect(html).toContain("Открыть в окне «Вариография»");
    expect(html).toContain("Кривая модели без данных");
    expect(html).toContain("σ² = 0,8066");
    expect(app.registry.list().some((c) => c.id === "variography.open")).toBe(true);
    expect(varioArgs(CTX)).toEqual({ set_id: CTX.setId, table: "пробы", field: "AU", view: "axes" });
    expect(varioArgs({ ...CTX, table: null, field: null }, "map")).toEqual({ set_id: CTX.setId, view: "map" });
    withRuns({ variogram: { busy: false, error: null, result: { data: A.gaps as TabVariogramAnswer, curve: null } } });
    const s = wrap(createElement(VariogramPanel, { ctx: CTX }));
    expect(s).not.toContain('data-testid="vario-model"');
    expect(s).toMatch(/<td( class="[^"]*")?>—<\/td><td>0<\/td>/); // a lag without pairs: «—» and 0 pairs, no γ of 0
  });

  it("map: north up, east right — row j = 0 (south) at the bottom; empty cells hatched", () => {
    withRuns({ map: { busy: false, error: null, result: SPARSE_MAP } });
    const html = wrap(createElement(MapPanel, { ctx: CTX }));
    const { empty } = mapFill(SPARSE_MAP);
    expect(html).toContain(`data-cells="${SPARSE_MAP.u.length * SPARSE_MAP.v.length}" data-empty="${empty}"`);
    expect(html.match(/data-empty="true"/g)).toHaveLength(empty);
    const group = html.slice(html.indexOf('data-testid="grid-cells"'));
    const rects = [...group.matchAll(/<rect x="([\d.]+)" y="([\d.]+)"/g)].slice(0, SPARSE_MAP.u.length * SPARSE_MAP.v.length).map((m) => [Number(m[1]), Number(m[2])]);
    const n = SPARSE_MAP.u.length;
    expect(rects[1][0]).toBeGreaterThan(rects[0][0]); // i + 1 — east
    expect(rects[n][1]).toBeLessThan(rects[0][1]); // j + 1 — north, up
    expect(html).toContain("ΔX (восток), м");
    expect(html).toContain("ΔY (север), м");
    expect(html).toContain("пусто");
  });

  it("local: the map of the mean; without a threshold the probability is not offered; out of reach — empty", () => {
    withRuns({ local: { busy: false, error: null, result: TIGHT } });
    const html = wrap(createElement(LocalPanel, { ctx: CTX }));
    expect(html).toContain(`data-cells="20" data-empty="20"`);
    expect(html).toContain('disabled="">Вероятность ≥ порога (задайте порог)');
    withRuns({ local: { busy: false, error: null, result: LOCAL } });
    setForm("local", { show: "prob" });
    const p = wrap(createElement(LocalPanel, { ctx: CTX }));
    expect(p).toContain("Вероятность ≥ 0,5");
    expect(p).toContain(`data-cells="${LOCAL.grid.nx * LOCAL.grid.ny}" data-empty="0"`);
    expect(p).toContain("Повторить с этим зерном");
  });
});

describe("stats.open and the core", () => {
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  let app: App;
  const byPath: Record<string, unknown> = { "/stats/tab/variogram": V, "/stats/tab/variogram-model": VM, "/stats/tab/map": MAP, "/stats/tab/local": LOCAL };
  let answer: (path: string) => Promise<Response>;

  beforeEach(() => {
    sent.length = 0;
    statsStore.set(initialStats());
    answer = (path) => Promise.resolve(json(byPath[path] ?? { code: "not_found", message: "Нет", hint: "" }, byPath[path] ? 200 : 404));
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        if (path === "/stats/fields")
          return Promise.resolve(json({ set_id: CTX.setId, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 4036, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }], texts: [] }] }));
        if (!path.startsWith("/stats/tab/")) return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
        sent.push({ path, body });
        return answer(path);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindStats(app);
    app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
    tablesStore.set({ sets: [{ id: CTX.setId, name: "Набор", holes: 49 }], setId: CTX.setId, tabs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("from the assistant: the variogram with the model asks the points, then the curve up to the last lag", async () => {
    setForm("variogram", { ...MODEL_FORM, lag: "25", nlag: "8", directions: "four", minPairs: "10" });
    const r = await app.registry.execute("stats.open", { analysis: "variogram" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/^Вариограмма AU \(Пробы\) в плане/);
    expect(sent.map((x) => x.path)).toEqual(["/stats/tab/variogram", "/stats/tab/variogram-model"]);
    expect(sent[0].body).toMatchObject({ set_id: CTX.setId, table: "пробы", field: "AU", lag: 25, nlag: 8, directions: "four", min_pairs: 10, model: MODEL });
    expect(sent[1].body).toMatchObject({ nugget: 0.1, h_max: V.lag * V.nlag + V.lag_tol, directions: "four", n: 101 });
    expect(statsStore.get().runs.variogram.result).toEqual({ data: V, curve: VM });
  });

  it("map and local from the assistant; the ribbon opens the tab and computes nothing", async () => {
    expect(sp((await app.registry.execute("stats.open", { analysis: "map" }, "claude")).result)).toMatch(/^Карта вариограммы AU \(Пробы\): интервалов 4 035/);
    expect((await app.registry.execute("stats.open", { analysis: "local" }, "claude")).result).toMatch(/^Карта неопределённости AU \(Пробы\)/);
    expect(sent.map((x) => x.path)).toEqual(["/stats/tab/map", "/stats/tab/local"]);
    sent.length = 0;
    expect(await app.registry.execute("stats.open", { analysis: "variogram" })).toEqual({ ok: true, result: "Открыта вкладка «Статистика» · Вариограмма · Набор" });
    expect(statsStore.get().analysis).toBe("variogram");
    expect(sent).toHaveLength(0);
  });

  it("the curve of the model without data needs no points", async () => {
    setForm("variogram", MODEL_FORM);
    const curve = await runModelCurve();
    expect(curve).toEqual(VM);
    expect(sent).toEqual([{ path: "/stats/tab/variogram-model", body: { ...MODEL, h_max: null, n: 101, directions: "omni" } }]);
    expect(statsStore.get().runs.variogram.result).toEqual({ data: null, curve: VM });
  });

  it("20 001 intervals: the refusal of the core with composites and the window «Вариография» (Т7.29-2)", async () => {
    answer = () => Promise.resolve(json(A.too_many.body, A.too_many.status));
    const r = await app.registry.execute("stats.open", { analysis: "map" }, "claude");
    expect(r.ok).toBe(false);
    expect(sp(r.result)).toContain("20 001");
    expect(statsStore.get().runs.map).toMatchObject({ busy: false, result: null, error: { code: "too_many_samples", composite: true } });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(MapPanel, { ctx: pickContext(CTX.setId, { table: "пробы", tables: [], numeric: [] } as never, "AU") })));
    expect(html).toContain('role="alert" data-code="too_many_samples"');
    expect(sp(html)).toContain("20 001");
    expect(html).toContain("Композиты…");
    expect(html.match(/Открыть в окне «Вариография»/g)).toHaveLength(2); // the form and the refusal
    answer = () => Promise.resolve(json(A.bad_param.body, A.bad_param.status));
    const bad = await app.registry.execute("stats.open", { analysis: "local" }, "claude");
    expect(bad).toEqual({ ok: false, result: `${A.bad_param.body.message} ${A.bad_param.body.hint}` });
    expect(statsStore.get().runs.local).toMatchObject({ ...idle, error: { code: "bad_param" } });
  });
});
