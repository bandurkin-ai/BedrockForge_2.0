// Swath plot — the settings and the answer on screen (ТЗ 7.30, 7.35 Д-01; ADR 0019 §2, §5, §9; перенос `swath.test.ts`
// BF): defaults, normalisation of stored settings, the 13th series not added (Т7.30-1), the name after the
// settings, directions and angles (X, Y turned become «по углам»), the request without the look, the range, the
// value and bar scales, Δ from the first series, CSV; the command `stats.swath` creates and saves a swath at once,
// opens one window per swath, gives an open window the new settings, `stats.swath_3d` writes the view key. The
// core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SwathChart } from "../src/charts/swath/SwathChart";
import { niceTicks } from "../src/charts/column/scale";
import type { ChartRow } from "../src/modules/charts/api";
import type { SwathResult } from "../src/modules/swath/api";
import { sourcesView, swathError } from "../src/modules/swath/api";
import { defaultSeries, openSwathWindow, renderSwathWindow, runSwath, SWATH_WINDOW } from "../src/modules/swath/bind";
import {
  addSeries,
  anglesOf,
  applyArgs,
  barScale,
  blockSeries,
  defaultSwath,
  deviation,
  deviations,
  matchComponent,
  MAX_SERIES,
  moveSeries,
  normalizeSwath,
  removeSeries,
  sampleSeries,
  seriesNote,
  shownRange,
  swathCsv,
  swathName,
  swathProblem,
  swathRequest,
  swathTitle,
  valueAxis,
  withAngles,
  withDirection,
  type SampleSeries,
  type SwathSeries,
  type SwathSettings,
} from "../src/modules/swath/model";
import { createSwathSaver, savedSwathName } from "../src/modules/swath/saver";
import { onPushed, swathChanged, swathsStore, upsertSwath } from "../src/modules/swath/state";
import { swathMenu } from "../src/modules/swath/SwathTree";
import { tablesStore } from "../src/modules/tables/state";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { AppContext } from "../src/shell/AppContext";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const flush = () => new Promise((r) => setTimeout(r, 0));

const model = (series: SwathSeries[] = []) => blockSeries(series, 7, "AU_CUT", "SG");
const twoSeries = (): SwathSeries[] => {
  const b = model();
  return [b, sampleSeries([b], 3, "пробы", "AU")];
};
const settings = (patch: Partial<SwathSettings> = {}): SwathSettings => ({ ...defaultSwath(twoSeries()), ...patch });

/** 4 swaths of 10 m along X from 100; a model and samples, the third swath has no samples. */
const result: SwathResult = {
  direction: "x",
  frame: { azimuth: 90, dip: 0, roll: 0, u: [1, 0, 0], v: [0, -1, 0], w: [0, 0, 1] },
  box: { u: [100, 140], v: [-20, 0], w: [50, 60] },
  limits: { u: [100, 140], v: [-20, 0], w: [50, 60] },
  custom_limits: false,
  start: 100,
  width: 10,
  count: 4,
  centres: [105, 115, 125, 135],
  block: 10,
  notes: [],
  series: [
    { index: 0, kind: "blocks", name: "Модель · AU_CUT", field: "AU_CUT", unit: "г/т", weight: "volume", weight_name: "объём", weight_unit: "м³", source_id: 7, counts: { blocks: 16, used: 16 }, mean: [1, 2, 3, 2], count: [4, 4, 4, 4], weights: [400, 800, 800, 200], total: { mean: 2.1, count: 16, weight: 2200 } },
    { index: 1, kind: "samples", name: "Скважины · AU", field: "AU", unit: "г/т", weight: "length", weight_name: "длина", weight_unit: "м", source_id: 3, counts: { rows: 12, used: 10, missing: 2 }, mean: [1.5, 2.5, null, 1], count: [3, 5, 0, 2], weights: [3, 5, null, 2], total: { mean: 1.9, count: 10, weight: 10 } },
  ],
};

describe("swath settings", () => {
  it("starts with the first model against the samples it was estimated from", () => {
    const [b, p] = twoSeries() as [SwathSeries & { kind: "blocks" }, SampleSeries];
    expect([b.kind, b.model_id, b.field, b.weight, b.bars, b.density]).toEqual(["blocks", 7, "AU_CUT", "volume", true, "SG"]);
    expect([p.kind, p.set_id, p.field, p.weight, p.within, p.within_series]).toEqual(["samples", 3, "AU", "length", "blocks", null]);
    expect(b.color).not.toBe(p.color);
    expect(matchComponent(["CU", "AU"], "AU_OK")).toBe("AU");
    expect(matchComponent(["CU", "AU"], "DENSITY")).toBe("CU"); // nothing alike: the first component
    expect(matchComponent([], "AU")).toBeNull();
    // samples alone are taken in the cuboid: there is no model to take them inside
    expect(sampleSeries([], 3, null, "AU").within).toBe("box");
    const s = defaultSwath();
    expect([s.v, s.direction, s.azimuth, s.dip, s.roll, s.width, s.anchor, s.limits, s.range]).toEqual([1, "x", 0, 0, 0, null, null, null, null]);
  });

  it("Т7.30-1: the 13th series is not added", () => {
    let s = defaultSwath();
    for (let i = 0; i < MAX_SERIES; i++) {
      const r = addSeries(s, sampleSeries(s.series, 3, null, "AU"));
      expect(r.added).toBe(true);
      s = r.settings;
    }
    expect(s.series).toHaveLength(12);
    const r = addSeries(s, model(s.series));
    expect(r.added).toBe(false);
    expect(r.settings).toBe(s);
    expect(r.settings.series).toHaveLength(12);
    // stored settings of more than 12 series keep the first 12
    expect(normalizeSwath({ series: Array.from({ length: 15 }, () => ({ kind: "samples", set_id: 3, field: "AU" })) }).series).toHaveLength(12);
    // colours stay apart for all 12
    expect(new Set(s.series.map((x) => x.color)).size).toBe(12);
  });

  it("reads stored settings and repairs what is broken", () => {
    const s = settings({ direction: "angles", azimuth: 35, dip: -20, roll: 10, width: 25, anchor: 512000, range: [2, 9], view: { chart3d: false, height3d: 0.5 } });
    expect(normalizeSwath(JSON.parse(JSON.stringify(s)))).toEqual(s);
    const junk = normalizeSwath({
      direction: "up",
      azimuth: 400,
      dip: -120,
      roll: 270,
      width: -5,
      anchor: "12",
      limits: { u: [1, 0], v: [0, 1], w: [0, 1] },
      range: [5, 2],
      view: { height3d: 5 },
      series: [null, { kind: "blocks" }, { kind: "samples", set_id: 3, field: "AU", weight: "magic", color: "red", within: "blocks", within_series: 0 }, { kind: "lines" }],
    });
    expect([junk.direction, junk.azimuth, junk.dip, junk.roll, junk.width, junk.anchor, junk.limits, junk.range, junk.view.height3d]).toEqual(["x", 40, -90, -90, null, null, null, null, 1]);
    // a block series without a model is kept: «источник удалён» (a `.bfggis` without it)
    expect(junk.series.map((x) => x.kind)).toEqual(["blocks", "samples"]);
    expect((junk.series[0] as { model_id: unknown }).model_id).toBeNull();
    const p = junk.series[1] as SampleSeries;
    expect([p.weight, p.within, p.within_series, /^#[0-9a-f]{6}$/.test(p.color)]).toEqual(["length", "blocks", 0, true]);
    expect(normalizeSwath(null)).toEqual(defaultSwath());
    // samples «inside the blocks» with no model left — in the cuboid
    expect((normalizeSwath({ series: [{ kind: "samples", set_id: 3, field: "AU", within: "blocks" }] }).series[0] as SampleSeries).within).toBe("box");
  });

  it("is named after its settings", () => {
    expect(swathName(settings())).toBe("Swath plot · X · ширина авто");
    expect(swathName(settings({ width: 25 }))).toBe("Swath plot · X · 25 м");
    expect(swathName(settings({ direction: "y", width: 12.5 }))).toBe("Swath plot · Y · 12,5 м");
    expect(swathName(settings({ direction: "z", azimuth: 30, width: 5 }))).toBe("Swath plot · Z, аз. 30° · 5 м");
    expect(swathName(settings({ direction: "angles", azimuth: 35, dip: -20, roll: 0, width: 10 }))).toBe("Swath plot · аз. 35°, накл. −20° · 10 м");
    expect(swathName(settings({ direction: "angles", azimuth: 120, dip: 0, roll: 15.5 }))).toBe("Swath plot · аз. 120°, пов. 15,5° · ширина авто");
    expect(swathTitle("Swath plot · X · 25 м")).toBe("Swath plot · X · 25 м");
    expect(swathTitle("Рудное тело")).toBe("Swath plot · Рудное тело");
    // the stored name while it follows the settings, the name of the settings not stored yet
    const row = { id: 5, set_id: null, kind: "swath", name: "Swath plot · X · ширина авто", auto_name: true, settings: settings() as unknown as Record<string, unknown>, version: 1 } as ChartRow;
    expect(savedSwathName(settings(), row)).toBe(row.name);
    expect(savedSwathName(settings({ width: 25 }), row)).toBe("Swath plot · X · 25 м");
    expect(savedSwathName(settings({ width: 25 }), { ...row, auto_name: false, name: "Моё" })).toBe("Моё");
  });

  it("turns X and Y into «по углам», keeps the azimuth of Z", () => {
    expect(anglesOf(settings())).toEqual({ azimuth: 90, dip: 0, roll: 0 });
    expect(anglesOf(settings({ direction: "y" }))).toEqual({ azimuth: 0, dip: 0, roll: 0 });
    expect(anglesOf(settings({ direction: "z", azimuth: 30 }))).toEqual({ azimuth: 30, dip: 90, roll: 0 });
    const a = withDirection(settings({ range: [1, 2] }), "angles");
    expect([a.direction, a.azimuth, a.dip, a.roll, a.range]).toEqual(["angles", 90, 0, 0, null]);
    expect(withDirection(settings({ direction: "angles", azimuth: 35 }), "z").azimuth).toBe(35);
    expect(withDirection(settings({ azimuth: 35 }), "z").azimuth).toBe(0);
    // Д-01: X turned by a handle or a number becomes «по углам» with the angles of X
    const t = withAngles(settings(), { dip: -15 });
    expect([t.direction, t.azimuth, t.dip, t.roll]).toEqual(["angles", 90, -15, 0]);
    const z = withAngles(settings({ direction: "z", azimuth: 10 }), { azimuth: 370 });
    expect([z.direction, z.azimuth]).toEqual(["z", 10]);
    expect(withAngles(settings({ direction: "z" }), { roll: 20 }).direction).toBe("angles");
    // ranges: azimuth [0; 360), dip clamped, roll (−180; 180]
    const n = withAngles(settings(), { azimuth: -90, dip: 100, roll: -180 });
    expect([n.azimuth, n.dip, n.roll]).toEqual([270, 90, 180]);
    // the arguments of the command (range — numbers from 1)
    const c = applyArgs(settings(), { azimuth: 45, dip: -10, roll: 5, width: 20, anchor: 0, range_from: 2, range_to: 5 });
    expect([c.direction, c.azimuth, c.dip, c.roll, c.width, c.anchor, c.range]).toEqual(["angles", 45, -10, 5, 20, 0, [1, 4]]);
    expect(applyArgs(settings(), { direction: "z", azimuth: 30 })).toMatchObject({ direction: "z", azimuth: 30 });
    expect(applyArgs(settings({ width: 5 }), {})).toEqual(settings({ width: 5 }));
  });

  it("keeps «inside the blocks of series k» pointing at its model when the list changes", () => {
    const b1 = model();
    const b2 = blockSeries([b1], 8, "CU", null);
    const p = { ...sampleSeries([b1, b2], 3, null, "AU"), within_series: 1 };
    const s = defaultSwath([b1, b2, p]);
    const moved = moveSeries(s, 1, -1); // b2 first
    expect((moved.series[2] as SampleSeries).within_series).toBe(0);
    const gone = removeSeries(s, 1); // b2 removed: the first model
    expect((gone.series[1] as SampleSeries).within_series).toBeNull();
    expect((gone.series[1] as SampleSeries).within).toBe("blocks");
    const none = removeSeries(removeSeries(s, 1), 0); // no model left: in the cuboid
    expect((none.series[0] as SampleSeries).within).toBe("box");
  });

  it("names what stands in the way of a calculation", () => {
    const s = settings();
    expect(swathProblem(s)).toBeNull();
    expect(swathProblem(defaultSwath())).toMatch(/Добавьте ряд/);
    expect(swathProblem({ ...s, series: [{ ...(s.series[0] as SwathSeries & { kind: "blocks" }), weight: "tonnage", density: null }] })).toMatch(/плотности/);
    expect(swathProblem({ ...s, series: [s.series[1]] })).toMatch(/внутри блоков/);
    expect(swathProblem({ ...s, series: [{ ...(s.series[1] as SampleSeries), within: "box", weight: "field" }] })).toMatch(/поле веса/);
    expect(swathProblem({ ...s, series: [{ ...(s.series[1] as SampleSeries), field: null }] })).toMatch(/выберите поле/);
    // a source that is gone is the core's to mark, not a refusal
    expect(swathProblem({ ...s, series: [{ ...(s.series[0] as SwathSeries & { kind: "blocks" }), model_id: null }] })).toBeNull();
  });

  it("sends only what changes the numbers", () => {
    const s = settings({ width: 12.5 });
    const r = swathRequest(s) as { direction: string; azimuth: number; dip: number; roll: number; width: number; series: Record<string, unknown>[] };
    expect([r.direction, r.azimuth, r.dip, r.roll, r.width]).toEqual(["x", 0, 0, 0, 12.5]);
    expect(r.series[0]).toEqual({ kind: "blocks", model_id: 7, field: "AU_CUT", weight: "volume", density: null, filter: null });
    expect(r.series[1]).toEqual({ kind: "samples", set_id: 3, table: "пробы", field: "AU", weight: "length", weight_field: null, negatives: "keep", top_cut: null, within: "blocks", within_series: null });
    // colours, what is shown, the 3D view and the angles X does not use do not count again
    const look: SwathSettings = { ...s, azimuth: 33, range: [0, 1], view: { chart3d: false, height3d: 0.9 }, series: s.series.map((x) => ({ ...x, color: "#000000", bars: !x.bars, line: !x.line })) };
    expect(swathRequest(look)).toEqual(swathRequest(s));
    expect(swathRequest(settings({ direction: "z", azimuth: 30, dip: 5 }))).toMatchObject({ direction: "z", azimuth: 30, dip: 0, roll: 0 });
    expect(swathRequest(settings({ direction: "angles", azimuth: 30, dip: -5, roll: 7 }))).toMatchObject({ azimuth: 30, dip: -5, roll: 7 });
    expect((swathRequest(settings({ series: [{ ...model(), weight: "tonnage", filter: { field: "DOMAIN", labels: ["Руда"] } }] })) as { series: unknown[] }).series[0]).toMatchObject({ density: "SG", filter: { field: "DOMAIN", labels: ["Руда"] } });
  });
});

describe("swath plot on screen", () => {
  it("keeps the range of swaths inside the plot", () => {
    expect(shownRange(null, 4)).toEqual([0, 3]);
    expect(shownRange([1, 2], 4)).toEqual([1, 2]);
    expect(shownRange([2, 90], 4)).toEqual([2, 3]);
    expect(shownRange([7, 9], 4)).toEqual([3, 3]);
    expect(shownRange([1, 2], 0)).toEqual([0, 0]);
  });

  it("scales the values and the bars over the swaths shown", () => {
    const ticks = (lo: number, hi: number) => niceTicks(lo, hi, 5);
    const axis = valueAxis(result.series, [true, true], [0, 3], ticks);
    expect([axis.min, axis.max, axis.ticks[0], axis.ticks[axis.ticks.length - 1]]).toEqual([0, 3, 0, 3]);
    expect(valueAxis(result.series, [false, true], [3, 3], ticks).max).toBe(1); // only what is shown counts
    expect(valueAxis(result.series, [false, false], [0, 3], ticks).max).toBeGreaterThan(0);
    expect(barScale(result.series, "blocks", [true, true], [0, 3])).toBe(800);
    expect(barScale(result.series, "samples", [true, true], [0, 3])).toBe(5); // samples: their number
    expect(barScale(result.series, "blocks", [true, true], [3, 3])).toBe(200);
  });

  it("draws lines broken at an empty swath, bars, the legend with the totals, the tooltip", () => {
    const look = [{ color: "#1f6bff", line: true, bars: true }, { color: "#f46a1a", line: true, bars: false }];
    const svg = renderToStaticMarkup(createElement(SwathChart, { result, look, range: [0, 3], hover: 1, onHover: () => undefined }));
    expect(svg).toContain("Модель · AU_CUT, г/т — среднее 2,1 (объём; блоков 16)");
    expect(svg.match(/data-testid="swath-hover"/g)).toHaveLength(1); // the rectangle of the hover (E2E)
    expect((svg.match(/<path /g) ?? []).length).toBe(2);
    expect(svg).toMatch(/M[^"]*L[^"]*L[^"]*"/); // the model: one line over 4 swaths
    expect(svg).toContain("Полоса 2: 110 – 120 м");
    expect(svg).toContain("X (восток), м · полоса 10 м");
  });

  it("tells Δ from the first series and leaves an empty swath empty", () => {
    expect(deviation(2.5, 2)).toBeCloseTo(25, 12);
    expect([deviation(null, 2), deviation(1, null), deviation(1, 0)]).toEqual([null, null, null]);
    expect(deviations(result, 1)).toEqual([50, 25, null, -50]);
    expect(deviations(result, 0)).toEqual([null, null, null, null]);
    const t = swathCsv(result, [1, 2]);
    expect(t.header).toEqual(["№", "От, м", "До, м", "Центр, м", "Модель · AU_CUT: среднее", "Модель · AU_CUT: число", "Модель · AU_CUT: объём", "Скважины · AU: среднее", "Скважины · AU: число", "Скважины · AU: длина", "Скважины · AU: Δ, %"]);
    expect(t.rows).toEqual([
      [2, 110, 120, 115, 2, 4, 800, 2.5, 5, 5, 25],
      [3, 120, 130, 125, 3, 4, 800, null, 0, null, null],
    ]);
    expect(seriesNote(result.series[1])).toBe("Скважины · AU: проб в расчёте 10 из 12, без значения 2.");
    expect(seriesNote({ ...result.series[0], error: { code: "model_not_found", message: "источник удалён" } })).toBe("Модель · AU_CUT: источник удалён");
  });

  it("shows a refusal of the core at its field: over 2000 swaths — at the width (Т7.30-1)", () => {
    const e = new CoreError(422, "too_many_swaths", "Полос получилось 4000 — больше 2000.", "Увеличьте ширину полосы.");
    expect(swathError(e)).toEqual({ text: "Полос получилось 4000 — больше 2000. Увеличьте ширину полосы.", param: "width" });
    e.body = { param: "anchor" };
    expect(swathError(e).param).toBe("anchor");
    expect(swathError(new CoreError(422, "too_many_series", "Не больше 12 рядов.", "")).param).toBe("series");
    // too many samples or blocks for the series (422 too_much_data {param: series}) — at the series, in Russian
    const big = new CoreError(422, "too_much_data", "Слишком много данных для рядов: 30 000 000 значений.", "Уберите часть рядов или сузьте диапазон.");
    big.body = { code: "too_much_data", param: "series" };
    expect(swathError(big)).toEqual({ text: "Слишком много данных для рядов: 30 000 000 значений. Уберите часть рядов или сузьте диапазон.", param: "series" });
    expect(swathError(new CoreError(422, "too_much_data", "Много.", "")).param).toBe("series");
    expect(swathError(new Error("сеть")).param).toBeNull();
  });

  it("reads the sources whatever the core adds or leaves out", () => {
    const v = sourcesView({ sets: [{ id: 3, name: "Скважины", tables: [{ key: "пробы", name: "Пробы", kind: "assay", rows: 10 }] }, { name: "без id" }], models: [{ id: 7, name: "Модель", numbers: [{ name: "AU_CUT", unit: "г/т" }], categories: [{ name: "DOMAIN", labels: ["Руда", 5] }], block: [10, 10, 5], density: "SG", grades: ["AU_CUT"] }] });
    expect(v.sets.map((s) => s.id)).toEqual([3]);
    expect(v.models[0]).toEqual({ id: 7, name: "Модель", numbers: [{ name: "AU_CUT", unit: "г/т" }], categories: [{ name: "DOMAIN", labels: ["Руда"], more: false }], block: [10, 10, 5], density: "SG", grades: ["AU_CUT"] });
    expect(sourcesView(null)).toEqual({ sets: [], models: [] });
  });
});

describe("swath commands", () => {
  const calls: { method: string; url: string; body: unknown }[] = [];
  let answers: Record<string, (body: unknown) => Response>;

  beforeEach(() => {
    calls.length = 0;
    windowStore.set({ windows: [] });
    swathsStore.set({ swaths: [], loaded: true, rev: 0, hover: null, selected: null });
    tablesStore.set({ setId: 3, sets: [{ id: 3, name: "Скважины" }] as never });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    answers = {
      "GET /stats/swath/sources": () => json({ sets: [{ id: 3, name: "Скважины", tables: [] }], models: [{ id: 7, name: "Модель", numbers: [{ name: "SG", unit: null }, { name: "AU_CUT", unit: "г/т" }], categories: [], block: [10, 10, 5], density: "SG", grades: [] }] }),
      "GET /stats/fields": () => json({ set_id: 3, tables: [{ id: 1, name: "Пробы", name_key: "пробы", kind: "assay", rows: 9, rev: 1, numbers: [{ name: "CU", role: "component", unit: "%" }, { name: "AU", role: "component", unit: "г/т" }], texts: [] }] }),
      "POST /charts": (b) => json({ id: 41, set_id: null, kind: "swath", name: (b as { name: string }).name, auto_name: true, settings: (b as { settings: unknown }).settings, version: 1 }),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const u = new URL(url);
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ method, url: u.pathname.replace(/^\/api\/v1/, "") + u.search, body });
        const key = `${method} ${u.pathname.replace(/^\/api\/v1/, "")}`;
        const hit = Object.entries(answers).find(([k]) => key === k || key.startsWith(`${k}/`) || key.startsWith(`${k}?`));
        return hit ? hit[1](body) : json({ code: "not_found", message: "нет", hint: "" }, 404);
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("makes a swath by default: the first model against the active set, saved at once and opened", async () => {
    const app = createApp();
    const text = await runSwath(app, {}, "user");
    const post = calls.find((c) => c.method === "POST" && c.url === "/charts")!;
    const body = post.body as { set_id: null; kind: string; name: string; auto_name: boolean; settings: SwathSettings };
    expect([body.set_id, body.kind, body.auto_name, body.name]).toEqual([null, "swath", true, "Swath plot · X · ширина авто"]);
    expect(body.settings.series.map((s) => [s.kind, s.kind === "blocks" ? s.model_id : s.set_id, s.field])).toEqual([["blocks", 7, "AU_CUT"], ["samples", 3, "AU"]]);
    expect(text).toBe("Открыто окно «Swath plot · X · ширина авто»");
    const w = windowStore.get().windows;
    expect(w.map((x) => [x.kind, x.title, x.props?.swathId])).toEqual([[SWATH_WINDOW, "Swath plot · X · ширина авто", 41]]);
    expect(swathsStore.get().swaths.map((r) => r.id)).toEqual([41]);
    // one window per swath; the window renders
    openSwathWindow(swathsStore.get().swaths[0], body.settings);
    expect(windowStore.get().windows).toHaveLength(1);
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, renderSwathWindow(w[0])));
    expect(html).toContain("Азимут, °");
    expect(html).toContain("Ряды · 2 из 12");
    expect(html).toContain('data-testid="swath-window"');
    expect(renderSwathWindow({ ...w[0], kind: "chart-histogram" })).toBeNull();
  });

  it("gives an open window the settings of the command; without a window saves them", async () => {
    const row: ChartRow = { id: 41, set_id: null, kind: "swath", name: "Swath plot · X · ширина авто", auto_name: true, settings: settings() as unknown as Record<string, unknown>, version: 3 };
    upsertSwath(row);
    const app = createApp();
    const got: SwathSettings[] = [];
    const stop = onPushed(41, (s) => got.push(s));
    await runSwath(app, { swath_id: 41, azimuth: 30, dip: -10 }, "user");
    expect(got.map((s) => [s.direction, s.azimuth, s.dip])).toEqual([["angles", 30, -10]]);
    expect(calls.some((c) => c.method === "PUT")).toBe(false); // the window saves it as its own edit
    stop();
    answers["PUT /charts"] = (b) => json({ ...row, ...(b as object), name: "Swath plot · аз. 30°, накл. −10° · 20 м", version: 4 });
    await runSwath(app, { swath_id: 41, width: 20 }, "user");
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/charts/41");
    expect(put.body).toMatchObject({ expected_version: 3, auto_name: "Swath plot · X · 20 м" });
    expect(swathsStore.get().swaths[0].version).toBe(4);
  });

  it("refuses an unknown swath and keeps the store by the events", async () => {
    const app = createApp();
    await expect(runSwath(app, { swath_id: 99 }, "user")).rejects.toThrow(/не найден/);
    upsertSwath({ id: 41, set_id: null, kind: "swath", name: "S", auto_name: true, settings: {}, version: 1 });
    upsertSwath({ id: 12, set_id: 3, kind: "histogram", name: "H", auto_name: true, settings: {}, version: 1 }); // a chart of a set is not a swath
    expect(swathsStore.get().swaths.map((r) => r.id)).toEqual([41]);
    await swathChanged({ id: 41, deleted: true, chart: "swath" });
    expect(swathsStore.get().swaths).toEqual([]);
    const items = swathMenu({ id: 41, set_id: null, kind: "swath", name: "S", auto_name: true, settings: {}, version: 1 }, () => null, () => undefined);
    expect(items.map((i) => i.label)).toEqual(["Открыть", "Переименовать…", "Удалить…"]);
  });

  it("without a model the default is the samples of the active set alone", async () => {
    answers["GET /stats/swath/sources"] = () => json({ code: "not_found", message: "нет", hint: "" }, 404); // the core of 13.18 is not there yet
    const series = await defaultSeries(null, 3);
    expect(series.map((s) => [s.kind, (s as SampleSeries).set_id, s.field, (s as SampleSeries).within])).toEqual([["samples", 3, "CU", "box"]]);
  });

  it("autosave: one PUT 400 ms after the last edit, the name of the settings while it follows them", async () => {
    const row: ChartRow = { id: 41, set_id: null, kind: "swath", name: "Swath plot · X · ширина авто", auto_name: true, settings: settings() as unknown as Record<string, unknown>, version: 1 };
    let now = 0;
    let timers: { at: number; fn: () => void; id: number }[] = [];
    const clock = {
      set: (fn: () => void, ms: number) => {
        timers.push({ at: now + ms, fn, id: timers.length + 1 });
        return timers.length;
      },
      clear: (t: unknown) => void (timers = timers.filter((x) => x.id !== t)),
    };
    const run = (ms: number) => {
      now += ms;
      const due = timers.filter((t) => t.at <= now);
      timers = timers.filter((t) => t.at > now);
      due.forEach((t) => t.fn());
    };
    const put = vi.fn(async (_id: number, body: { settings?: object; auto_name?: string; expected_version: number }) => ({ ...row, settings: body.settings as Record<string, unknown>, name: body.auto_name ?? row.name, version: body.expected_version + 1 }));
    const saver = createSwathSaver(row, {}, put, 400, clock);
    saver.change(settings()); // the stored ones: nothing
    run(500);
    expect(put).not.toHaveBeenCalled();
    saver.change(settings({ width: 10 }));
    run(200);
    saver.change(settings({ width: 25 }));
    run(399);
    expect(put).not.toHaveBeenCalled();
    run(1);
    await flush();
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][1]).toMatchObject({ auto_name: "Swath plot · X · 25 м", expected_version: 1 });
    // the colour of a series is saved too (it is in the settings), not computed
    saver.change(settings({ width: 25, series: twoSeries().map((x) => ({ ...x, color: "#000000" })) }));
    run(400);
    await flush();
    expect(put).toHaveBeenCalledTimes(2);
    saver.stop();
  });
});
