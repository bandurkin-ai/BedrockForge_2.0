// The variogram charts (ТЗ 8.6.4; ADR 0022 §5, §15, decisions 4, 8, 19): experimental points (a lag without pairs —
// null — is never drawn as 0; «few» hollow; sized and labelled by N), the model curve of `model.ts`, the variance
// and sill lines; the rose of ranges (a ray without a range breaks the outline); the variogram map (an empty cell is
// not painted — never the colour of zero). The answers are of the form of e16-api.md, read by `readCalc`.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { rgba32, rampColor } from "../src/charts/core/palette";
import { RoseChart } from "../src/charts/variogram/RoseChart";
import { VariogramChart } from "../src/charts/variogram/VariogramChart";
import { VarMapChart } from "../src/charts/variogram/VarMapChart";
import {
  cellAt,
  chartHMax,
  curveDirection,
  curvesCsv,
  expPoints,
  mapCsv,
  mapDomain,
  measureValues,
  modelLine,
  nearestRay,
  pairsText,
  pointRadius,
  polar,
  rasterMap,
  readRose,
  readVarMap,
  roseRuns,
  segments,
  thetaOf,
  varianceLevel,
} from "../src/charts/variogram/scale";
import { readCalc } from "../src/modules/variography/api";
import { axisOf, direction, gammaAlong, type VModel } from "../src/modules/variography/model";

// the form of `job.result` of `variography.experimental` (e16-api.md): lag 2 has no pairs, lag 3 has few
const CALC = readCalc({
  key: "3f",
  set_id: 3,
  table: "12",
  field: "AU",
  unit: "г/т",
  transform: "none",
  sigma2: 1.84,
  sigma2_z: 1.84,
  origin: [0, 0, 0],
  counts: { rows: 10, missing: 0, no_position: 0, outside_domain: 0, used: 10, no_weight: 0 },
  sampled: null,
  params: {},
  directions: [
    {
      axis: 1, az: 30, plunge: 10, az_tol: 22.5, dip_tol: 22.5, band_h: 30, band_v: 20,
      lags: [10, 20, 30, 40], h: [9.7, null, 29.1, 40.2], n: [412, 0, 12, 900],
      gamma: [0.61, null, 1.2, 1.7], rho: [0.33, null, 0.1, 0.02], pairwise: [0.42, null, 0.5, 0.6], n_pw: [410, 0, 12, 898],
      few: [false, true, true, false],
    },
    { axis: 2, az: 120, plunge: 0, az_tol: 22.5, dip_tol: 22.5, band_h: "inf", band_v: 20, lags: [10, 20], h: [null, null], n: [0, 0], gamma: [null, null], rho: [null, null], pairwise: [null, null], n_pw: [0, 0], few: [true, true] },
  ],
  downhole: { lag: 2, lag_tol: 1, lags: [2, 4], h: [2, 4], n: [1180, 1150], gamma: [0.3, 0.5], rho: [0.8, 0.7], pairwise: [0.2, 0.3], n_pw: [1180, 1150], few: [false, false] },
  warnings: [],
});

const MODEL: VModel = { type: "semivariogram", c0: 0.2, common_axes: true, structures: [{ type: "spherical", c: 1.5, a1: 60, a2: 40, a3: 10, ang1: 30, ang2: 10, ang3: 0 }] };

const render = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("experimental points", () => {
  it("a lag without pairs is not a point; the values of a type; few marked", () => {
    const c = CALC.directions[0];
    expect(measureValues(c, "semivariogram")).toEqual([0.61, null, 1.2, 1.7]);
    expect(measureValues(c, "correlogram").map((v) => (v === null ? null : Number(v.toFixed(12))))).toEqual([0.67, null, 0.9, 0.98]);
    expect(measureValues(c, "pairwise")).toEqual([0.42, null, 0.5, 0.6]);
    const pts = expPoints(c, "semivariogram");
    expect(pts.map((p) => p.k)).toEqual([0, 2, 3]);
    expect(pts.find((p) => p.k === 2)).toMatchObject({ few: true, n: 12, h: 29.1 });
    expect(expPoints(c, "pairwise").map((p) => p.n)).toEqual([410, 12, 898]);
    // NaN, undefined — not a point either
    expect(expPoints({ ...c, gamma: [NaN, 1, 2, 3], h: [1, 2, undefined as unknown as null, 4] }, "semivariogram").map((p) => p.k)).toEqual([1, 3]);
    expect(expPoints(CALC.directions[1], "semivariogram")).toEqual([]);
  });

  it("marks by N; labels; the variance level by type", () => {
    expect(pointRadius(0, 100)).toBe(2.5);
    expect(pointRadius(100, 100)).toBe(7);
    expect(pointRadius(25, 100)).toBeCloseTo(4.75, 12);
    expect([pairsText(412), pairsText(12345), pairsText(2_500_000)]).toEqual(["412", "12,3 тыс.", "2,5 млн"]);
    expect(varianceLevel("semivariogram", 1.84)).toEqual({ value: 1.84, label: "σ²" });
    expect(varianceLevel("nscore", 1)).toEqual({ value: 1, label: "σ²" });
    expect(varianceLevel("correlogram", 1.84)).toEqual({ value: 1, label: "1" });
    expect(varianceLevel("pairwise", 1.84)).toBeNull();
    expect(varianceLevel("semivariogram", null)).toBeNull();
    expect(chartHMax(CALC.directions)).toBeCloseTo(40.2 * 1.05, 12);
    expect(chartHMax([], MODEL)).toBe(90);
    expect(chartHMax([])).toBeNull();
  });

  it("the model curve is the mirror of model.ts; an axis follows the model, a direction its az, plunge", () => {
    const u = curveDirection(CALC.directions[0], MODEL);
    expect(u).toEqual(axisOf(MODEL, 1));
    expect(curveDirection({ axis: null, az: 45, plunge: 0 }, MODEL)).toEqual(direction(45, 0));
    expect(curveDirection({ axis: 2, az: 45, plunge: 0 }, null)).toEqual(direction(45, 0));
    const l = modelLine(MODEL, u, 120, 12);
    expect(l.h[0]).toBe(0);
    expect(l.value[0]).toBe(0);
    expect(l.value).toEqual(gammaAlong(MODEL, l.h, u));
    expect(l.value[12]).toBeCloseTo(1.7, 12);
    // a missing value breaks the line
    const id = (v: number | null) => v;
    expect(segments([0, 1, 2, 3, 4], [0, 1, null, 3, 4], id, id)).toEqual(["0.0,0.0 1.0,1.0", "3.0,3.0 4.0,4.0"]);
    expect(segments([0, 1, 2], [0, NaN, 1], id, id)).toEqual([]);
  });
});

describe("VariogramChart", () => {
  const series = [
    { label: "Ось 1", curve: CALC.directions[0], u: curveDirection(CALC.directions[0], MODEL) },
    { label: "Вдоль скважин", curve: CALC.downhole! },
  ];

  it("draws only the lags with pairs; few hollow; N as labels; model, sill and σ² lines", () => {
    const html = render(createElement(VariogramChart, { series, type: "semivariogram", model: MODEL, sigma2: CALC.sigma2 }));
    expect(html).toContain('data-series="Ось 1" data-points="3" data-few="1"');
    expect(html).toContain('data-series="Вдоль скважин" data-points="2" data-few="0"');
    expect(count(html, /<circle /g)).toBe(5);
    expect(count(html, /fill="none"[^>]*data-few="true"/g)).toBe(1);
    expect(html).toContain(">412<");
    expect(html).toContain(">900<");
    expect(html).toContain('data-line="variance" data-value="1.84"');
    expect(html).toContain('data-line="sill" data-value="1.7"');
    expect(html).toContain('data-model="Ось 1"');
    expect(html).not.toContain('data-model="Вдоль скважин"');
    expect(html).toContain("γ(h)");
  });

  it("no point sits on the zero line: a lag without pairs is not at γ = 0", () => {
    const html = render(createElement(VariogramChart, { series: [{ label: "Ось 1", curve: CALC.directions[0] }], type: "semivariogram", sigma2: 1.84 }));
    // the y pixel of γ = 0 is the bottom of the plot box (340 − 44 = 296)
    const cys = [...html.matchAll(/<circle cx="[\d.]+" cy="([\d.]+)"/g)].map((m) => Number(m[1]));
    expect(cys).toHaveLength(3);
    expect(cys.every((y) => y < 296 - 1)).toBe(true);
  });

  it("all lags empty — a message, no points; the correlogram as 1 − ρ with the level 1; pairwise without a level", () => {
    const empty = render(createElement(VariogramChart, { series: [{ label: "Ось 2", curve: CALC.directions[1] }], type: "semivariogram", sigma2: null }));
    expect(empty).toContain('data-empty="variogram"');
    expect(empty).toContain("Нет точек");
    expect(count(empty, /<circle /g)).toBe(0);
    const corr = render(createElement(VariogramChart, { series: [{ label: "Ось 1", curve: CALC.directions[0] }], type: "correlogram", sigma2: 1.84 }));
    expect(corr).toContain('data-line="variance" data-value="1"');
    expect(corr).toContain("1 − ρ(h)");
    const pw = render(createElement(VariogramChart, { series: [{ label: "Ось 1", curve: CALC.directions[0] }], type: "pairwise", sigma2: 1.84 }));
    expect(pw).not.toContain('data-line="variance"');
  });

  it("the overlay gets the scales", () => {
    let got: { hMax: number; y0: number | null } | null = null;
    render(createElement(VariogramChart, { series, type: "semivariogram", model: MODEL, sigma2: 1.84, hMax: 100, overlay: (s) => ((got = { hMax: s.hMax, y0: s.sy.map(0) }), null) }));
    expect(got).toEqual({ hMax: 100, y0: 296 });
  });

  it("CSV: an empty lag stays empty", () => {
    const text = curvesCsv([{ label: "Ось 1", curve: CALC.directions[0] }], "semivariogram").split("\r\n");
    expect(text[0]).toBe('"Направление";"Лаг";"Центр лага";"h среднее";"γ(h)";"Число пар";"Мало пар"');
    expect(text[2]).toBe('"Ось 1";2;20;;;0;"да"');
  });
});

describe("rose of ranges", () => {
  const ROSE = readRose({
    q: 1.0,
    level: 1.84,
    rays: [
      { theta: 0, range: 85, beyond: false },
      { theta: 45, range: 60, beyond: false },
      { theta: 90, range: null, beyond: true },
      { theta: 135, range: 50, beyond: false },
    ],
  })!;

  it("reads the answer; the outline is broken at a ray without a range, closed over all", () => {
    expect(readRose(null)).toBeNull();
    expect(ROSE.rays[2]).toEqual({ theta: 90, range: null, beyond: true });
    const { runs, closed } = roseRuns(ROSE.rays);
    expect(closed).toBe(false);
    // 0, 45 | 135, 180, 225 | 315 — the run through 360° joins the first
    expect(runs.map((r) => r.map((v) => v.theta))).toEqual([[315, 0, 45], [135, 180, 225]]);
    const all = roseRuns(ROSE.rays.map((r) => ({ ...r, range: 10, beyond: false })));
    expect(all.closed).toBe(true);
    expect(all.runs).toHaveLength(1);
    expect(all.runs[0]).toHaveLength(8);
  });

  it("theta: a1 up, a2 right; the nearest ray mod 180°", () => {
    expect(polar(0, 1).map((v) => Number(v.toFixed(12)) + 0)).toEqual([0, -1]);
    expect(polar(90, 1).map((v) => Number(v.toFixed(12)) + 0)).toEqual([1, 0]);
    expect(thetaOf(1, 0)).toBeCloseTo(90, 12);
    expect(thetaOf(0, 1)).toBeCloseTo(0, 12);
    expect(thetaOf(1, -1)).toBeCloseTo(45, 12);
    expect(nearestRay(ROSE.rays, 170)?.theta).toBe(0);
    expect(nearestRay(ROSE.rays, 120)?.theta).toBe(135);
  });

  it("draws the reached rays as points, the others as dashed spokes; nothing reached — a message", () => {
    const html = render(createElement(RoseChart, { rose: ROSE, hMax: 100, selected: 45 }));
    expect(count(html, /<circle data-ray=/g)).toBe(6);
    expect(count(html, /data-beyond=/g)).toBe(2);
    expect(html).toContain('data-runs="2"');
    expect(html).toContain('data-selected="45"');
    expect(html).toContain("достигнут: 3 из 4");
    const none = render(createElement(RoseChart, { rose: { q: 1, level: 1.84, rays: [{ theta: 0, range: null, beyond: true }] } }));
    expect(none).toContain('data-empty="rose"');
    expect(none).not.toContain("<polygon");
  });
});

describe("variogram map", () => {
  // 3 × 3 cells (nu = nv = 1, cell 10): the centre is empty, one cell γ = 0 (a real zero)
  const MAP = readVarMap({
    nu: 1, nv: 1, cell: 10, thickness: "inf", min_pairs: 5,
    u: [-10, 0, 10], v: [-10, 0, 10],
    gamma: [[1, 0, 2], [0.5, null, 0.5], [2, 0, 1]],
    n: [[7, 9, 6], [8, 2, 8], [6, 9, 7]],
  })!;

  it("reads the answer: ∞ thickness — null, null cells stay null", () => {
    expect(readVarMap(null)).toBeNull();
    expect(MAP.thickness).toBeNull();
    expect(MAP.gamma[1][1]).toBeNull();
    expect(MAP.gamma[0][1]).toBe(0);
    expect(mapDomain(MAP, 1.84)).toEqual({ min: 0, max: 2 });
    expect(mapDomain(MAP, 3)).toEqual({ min: 0, max: 3 });
    expect(cellAt(MAP, 0, 0)).toEqual({ i: 1, j: 1 });
    expect(cellAt(MAP, 9, -9)).toEqual({ i: 2, j: 0 });
    expect(cellAt(MAP, 16, 0)).toBeNull();
  });

  it("the raster: an empty cell is not painted; a zero has the colour of zero", () => {
    const w = 30;
    const buf = new Uint32Array(w * w);
    expect(rasterMap(buf, w, w, MAP, mapDomain(MAP, null))).toBe(8);
    const px = (x: number, y: number) => buf[y * w + x];
    // the centre (u = 0, v = 0)
    expect(px(15, 15)).toBe(0);
    // u along a1 up, v along a2 right: gamma[j = 1 (v = 0)][i = 0 (u = −10)] = 0.5 is at the bottom middle
    expect(px(15, 25)).toBe(rgba32(rampColor(0.25)));
    // gamma[j = 0 (v = −10)][i = 1 (u = 0)] = 0 — left middle: the colour of zero, not transparent
    expect(px(5, 15)).toBe(rgba32(rampColor(0)));
    expect(px(5, 15)).not.toBe(0);
    // all empty — nothing painted
    const none = { ...MAP, gamma: MAP.gamma.map((r) => r.map(() => null)) };
    buf.fill(0);
    expect(rasterMap(buf, w, w, none, mapDomain(none, null))).toBe(0);
    expect(buf.every((v) => v === 0)).toBe(true);
  });

  it("the chart: counts, hatching for the empty, the colour bar with σ²; all empty — a message", () => {
    const html = render(createElement(VarMapChart, { map: MAP, sigma2: 1.84, selected: 30 }));
    expect(html).toContain('data-cells="9" data-empty="1"');
    expect(html).toContain("data-empty-fill");
    expect(html).toContain("data-colorbar");
    expect(html).toContain('data-sigma2="1.84"');
    expect(html).toContain("пусто");
    expect(html).toContain('data-selected="30"');
    const none = render(createElement(VarMapChart, { map: { ...MAP, gamma: MAP.gamma.map((r) => r.map(() => null)) }, sigma2: null }));
    expect(none).toContain("Во всех ячейках меньше 5 пар");
    expect(none).not.toContain("data-colorbar");
  });

  it("CSV: an empty cell stays empty", () => {
    const rows = mapCsv(MAP).split("\r\n");
    expect(rows[0]).toBe('"u (a1)";"v (a2)";"γ";"Число пар"');
    expect(rows[5]).toBe("0;0;;2");
    expect(rows[2]).toBe("0;-10;0;9");
  });
});
