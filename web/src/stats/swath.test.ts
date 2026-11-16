import { describe, expect, it } from "vitest";
import type { Dataset, SwathResult } from "../api";
import {
  axisValue, barScale, buildSwath, defaultSwath, matchComponent, normalizeSwath, shownRange, swathProblem, swathRequest, valueAxis,
  type SwathBlockSeries, type SwathSampleSeries, type SwathSettings,
} from "./swath";

const model = {
  id: 7, kind: "blocks", name: "Модель", folder: "", created_at: "", counts: null, components: null, audit: null, wireframes: null, lines: null, rasters: null,
  meta: {
    attributes: [
      { key: "a0", name: "SG", kind: "number", unit: null, source: "", stats: { count: 1, empty: 0 } },
      { key: "a1", name: "AU_CUT", kind: "number", unit: "g/t", source: "", stats: { count: 1, empty: 0 } },
      { key: "a2", name: "DOMAIN", kind: "category", labels: ["Руда", "Ореол"], source: "", stats: { count: 1, empty: 0 } },
    ],
    report: { density: "a0", grades: ["a1"] },
  },
} as unknown as Dataset;
const holes = {
  id: 3, kind: "drillholes", name: "Скважины", folder: "", created_at: "", meta: {}, counts: { collar: 1, survey: 1, assay: 9 },
  components: [{ name: "CU", unit: "%" }, { name: "AU", unit: "г/т" }], audit: null, wireframes: null, lines: null, rasters: null,
} as unknown as Dataset;

/** 4 swaths of 10 m along X from 100; a model and samples, the third swath has no samples. */
const result: SwathResult = {
  direction: "x", azimuth: 90, frame: { u: [1, 0, 0], v: [0, 1, 0], w: [0, 0, 1] },
  box: { u: [100, 140], v: [0, 20], w: [50, 60] }, start: 100, width: 10, count: 4, centres: [105, 115, 125, 135], block: 10, notes: [],
  series: [
    { index: 0, kind: "blocks", name: "Модель · AU_CUT", field: "AU_CUT", unit: "g/t", weight: "volume", weight_name: "объём", weight_unit: "м³", dataset_id: 7,
      counts: {}, mean: [1, 2, 3, 2], count: [4, 4, 4, 4], weights: [400, 800, 800, 200], total: { mean: 2.1, count: 16, weight: 2200 } },
    { index: 1, kind: "samples", name: "Скважины · AU", field: "AU", unit: "г/т", weight: "length", weight_name: "длина", weight_unit: "м", dataset_id: 3,
      counts: {}, mean: [1.5, 2.5, null, 1], count: [3, 5, 0, 2], weights: [3, 5, 0, 2], total: { mean: 1.9, count: 10, weight: 10 } },
  ],
};

describe("swath settings", () => {
  it("starts with the first model against the samples it was estimated from", () => {
    const s = defaultSwath([holes, model], 3);
    const [b, p] = s.series as [SwathBlockSeries, SwathSampleSeries];
    expect([b.kind, b.modelId, b.field, b.weight, b.bars]).toEqual(["blocks", 7, "a1", "volume", true]);
    expect([p.kind, p.setId, p.field, p.weight, p.within]).toEqual(["samples", 3, "comp:AU", "length", "blocks"]);
    expect(b.color).not.toBe(p.color);
    expect(matchComponent(holes, "CU_OK")).toBe("CU");
    expect(matchComponent(holes, "DENSITY")).toBe("CU"); // nothing alike: the first component
    expect(defaultSwath([], null).series).toEqual([]);
    // Samples alone are taken in the cuboid: there is no model to take them inside.
    expect((defaultSwath([holes]).series[0] as SwathSampleSeries).within).toBe("box");
  });

  it("reads stored settings and repairs what is broken", () => {
    const s = defaultSwath([holes, model], 3);
    const stored = JSON.parse(JSON.stringify({ ...s, direction: "azimuth", azimuth: "35", range: [2, 9], height3d: 5 }));
    const back = normalizeSwath(stored);
    expect(back.series).toEqual(s.series);
    expect([back.direction, back.azimuth, back.range, back.height3d]).toEqual(["azimuth", "35", [2, 9], 1]);
    const junk = normalizeSwath({ direction: "up", width: 10, range: [5, 2], series: [null, { kind: "blocks" }, { kind: "samples", setId: 3, field: "comp:AU", weight: "magic", color: "red" }] });
    expect([junk.direction, junk.width, junk.range, junk.series.length]).toEqual(["x", "10", null, 1]);
    expect([junk.series[0].kind, (junk.series[0] as SwathSampleSeries).weight, /^#[0-9a-f]{6}$/.test(junk.series[0].color)]).toEqual(["samples", "length", true]);
    expect(normalizeSwath(null).series).toEqual([]);
  });

  it("names what stands in the way of a calculation", () => {
    const s = defaultSwath([holes, model], 3);
    expect(swathProblem(s)).toBeNull();
    expect(swathProblem({ ...s, series: [] })).toMatch(/Добавьте ряд/);
    expect(swathProblem({ ...s, width: "-5" })).toMatch(/Ширина/);
    expect(swathProblem({ ...s, direction: "azimuth", azimuth: "400" })).toMatch(/Азимут/);
    expect(swathProblem({ ...s, series: [{ ...(s.series[0] as SwathBlockSeries), weight: "tonnage", density: null }] })).toMatch(/плотности/);
    expect(swathProblem({ ...s, series: [s.series[1]] })).toMatch(/в блоках модели/);
    expect(swathProblem({ ...s, series: [{ ...(s.series[1] as SwathSampleSeries), within: "box", weight: "field" }] })).toMatch(/поле веса/);
  });

  it("makes the request: only what changes the numbers", () => {
    const s = defaultSwath([holes, model], 3);
    const second = { ...(s.series[0] as SwathBlockSeries), id: "second", filterField: "a2", filterLabels: ["Руда"], weight: "tonnage" as const };
    const samples = { ...(s.series[1] as SwathSampleSeries), withinSeries: "second", topCut: "30,5" };
    const r = swathRequest({ ...s, width: "12,5", series: [samples, s.series[0], second] });
    expect([r.direction, r.azimuth, r.width, r.anchor]).toEqual(["x", 0, 12.5, null]);
    expect(r.series[0]).toMatchObject({ kind: "samples", set_id: 3, field: "comp:AU", weight: "length", within: "blocks", within_series: 2, top_cut: 30.5 });
    expect(r.series[1]).toEqual({ kind: "blocks", model_id: 7, field: "a1", weight: "volume", density: null, filters: [] });
    expect(r.series[2]).toMatchObject({ weight: "tonnage", density: "a0", filters: [{ key: "a2", labels: ["Руда"] }] });
    // Colours and what is shown do not send the plot to be counted again.
    const look: SwathSettings = { ...s, chart3d: false, range: [0, 1], series: s.series.map((x) => ({ ...x, color: "#000000", bars: !x.bars })) };
    expect(swathRequest(look)).toEqual(swathRequest(s));
    expect(swathRequest({ ...s, direction: "azimuth", azimuth: "35" }).azimuth).toBe(35);
  });

  it("keeps the range of swaths inside the plot", () => {
    const s = defaultSwath([holes, model], 3);
    expect(shownRange(s, 4)).toEqual([0, 3]);
    expect(shownRange({ ...s, range: [1, 2] }, 4)).toEqual([1, 2]);
    expect(shownRange({ ...s, range: [2, 90] }, 4)).toEqual([2, 3]);
    expect(shownRange({ ...s, range: [7, 9] }, 4)).toEqual([3, 3]);
  });
});

describe("swath plot", () => {
  const settings = defaultSwath([holes, model], 3);

  it("scales the values and the bars over the swaths shown", () => {
    const axis = valueAxis(result, [true, true], [0, 3]);
    expect([axis.min, axis.max]).toEqual([0, 3]);
    expect(axis.ticks[0]).toBe(0);
    expect(axis.ticks[axis.ticks.length - 1]).toBe(3);
    expect(valueAxis(result, [false, true], [3, 3]).max).toBe(1); // only what is shown counts
    expect(valueAxis(result, [false, false], [0, 3]).max).toBeGreaterThan(0);
    expect(barScale(result, "blocks", [true, true], [0, 3])).toBe(800);
    expect(barScale(result, "samples", [true, true], [0, 3])).toBe(5); // samples: their number
    expect(barScale(result, "blocks", [true, true], [3, 3])).toBe(200);
    expect(axisValue(result, 125)).toBe(125);
    expect(axisValue({ direction: "azimuth", start: 100 }, 125)).toBe(25);
  });

  it("cuts the cuboid into swaths and stands the plot over them", () => {
    const b = buildSwath(result, settings);
    expect(b.centre).toEqual([120, 10, 55]);
    expect(b.half).toEqual([20, 10, 5]);
    expect(b.vertical).toBe(false);
    // The outline: two end rectangles and four long edges over four swaths; three boundaries inside.
    expect([b.lines.length / 6, b.inner.length / 6, b.dim.length]).toEqual([2 * 4 + 4 * 4, 3 * 4, 0]);
    expect(Math.min(...b.lines.filter((_, i) => i % 3 === 0))).toBe(-20);
    const chart = b.chart!;
    expect(chart.lines.map((l) => l.segments.length / 4)).toEqual([3, 1]); // the gap of the empty swath breaks the line
    expect(chart.points.map((p) => p.at.length / 2)).toEqual([4, 3]);
    expect(chart.points[0].at[0]).toBe(-15); // the middle of the first swath
    expect(chart.points[0].at[1]).toBeCloseTo(1 / 3); // a mean of 1 on the axis 0..3
    expect(chart.bars.length).toBe(1); // bars are on for the model only
    expect(chart.bars[0].rects[2]).toBeCloseTo(0.15); // 400 of 800, in the lower 30 %
    expect(chart.labels.filter((l) => l.bold).map((l) => l.text)).toEqual(["Модель · AU_CUT", "Скважины · AU"]);
    expect(chart.labels.filter((l) => l.bold)[0].rows).toBeLessThan(chart.labels.filter((l) => l.bold)[1].rows); // the first series on top
    expect(buildSwath(result, { ...settings, chart3d: false }).chart).toBeNull();
  });

  it("dims the swaths outside the range and marks the one under the cursor", () => {
    const b = buildSwath(result, { ...settings, range: [1, 2] }, 2);
    expect([b.lines.length / 6, b.inner.length / 6, b.dim.length / 6]).toEqual([2 * 4 + 2 * 4, 4, 2 * 4 + 2 * 4]);
    expect(b.highlight).toEqual([0, 10]);
    expect(Array.from(b.chart!.grid.slice(0, 4))).toEqual([-10, 0, 10, 0]); // the plot spans the swaths shown
    expect(b.chart!.points.map((p) => p.at.length / 2)).toEqual([2, 1]);
    expect(buildSwath(result, settings, 9).highlight).toBeNull();
    // Swaths along Z: the plot lies on its side.
    const z = buildSwath({ ...result, direction: "z", frame: { u: [0, 0, 1], v: [1, 0, 0], w: [0, 1, 0] } }, settings);
    expect(z.vertical).toBe(true);
    expect(z.centre).toEqual([10, 55, 120]);
  });
});
