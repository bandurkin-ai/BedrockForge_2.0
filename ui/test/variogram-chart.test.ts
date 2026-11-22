// The variogram charts (ТЗ 8.6.4; ADR 0022 §5, §15, decisions 4, 8, 19): experimental points (a lag without pairs —
// null — is never drawn as 0; «few» hollow; sized and labelled by N), the model curve of `model.ts`, the variance
// and sill lines; the rose of ranges (a ray without a range breaks the outline); the variogram map (an empty cell is
// not painted — never the colour of zero). The curves are the real answer of the core (`window_answers.json`), read
// by `readCalc`; the rose and the map — the real `job.result` of `variography.map` (16.25). The plan is seen from
// above: a1 up, a2 to the LEFT (west at zero angles), θ counter-clockwise from a1 (the mirror of the plane, 16.4–16.5).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  mapColor,
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
import { readMapCalc, roseWithWorld } from "../src/modules/variography/mapCalc";
import { axisOf, direction, gammaAlong, type VModel } from "../src/modules/variography/model";

// the real `job.result` of `variography.experimental` (core/tests/test_vario_window_answers.py): axis 1 — lags 1–2
// without pairs (null), lag 4 with few pairs (N < min_pairs = 800); along the holes — every lag few
const ANSWERS = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/variography/window_answers.json"), "utf-8"));
const CALC = readCalc(ANSWERS.job_axes);
const D1 = CALC.directions[0];
// a direction of the same answer with no pairs in any lag
const EMPTY = { ...D1, h: D1.h.map(() => null), n: D1.n.map(() => 0), gamma: D1.gamma.map(() => null), rho: D1.rho.map(() => null), pairwise: D1.pairwise.map(() => null), n_pw: D1.n_pw.map(() => 0), few: D1.few.map(() => true) };

const MODEL: VModel = { type: "semivariogram", c0: 0.2, common_axes: true, structures: [{ type: "spherical", c: 1.5, a1: 60, a2: 40, a3: 10, ang1: 30, ang2: 10, ang3: 0 }] };

const render = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("experimental points", () => {
  it("a lag without pairs is not a point; the values of a type; few marked", () => {
    const c = D1;
    expect(c.n.slice(0, 2)).toEqual([0, 0]);
    expect(c.gamma.slice(0, 2)).toEqual([null, null]);
    expect(measureValues(c, "semivariogram")).toEqual(c.gamma);
    expect(measureValues(c, "correlogram")).toEqual(c.rho.map((r) => (r === null ? null : 1 - r)));
    expect(measureValues(c, "pairwise")).toEqual(c.pairwise);
    const pts = expPoints(c, "semivariogram");
    expect(pts.map((p) => p.k)).toEqual([2, 3, 4, 5]);
    expect(pts.find((p) => p.k === 3)).toMatchObject({ few: true, n: 772, h: c.h[3] });
    expect(expPoints(c, "pairwise").map((p) => p.n)).toEqual(c.n_pw.slice(2));
    // NaN, undefined — not a point either
    expect(expPoints({ ...c, gamma: [NaN, 1, 2, 3, 4, 5], h: [1, 2, undefined as unknown as null, 4, 5, 6] }, "semivariogram").map((p) => p.k)).toEqual([1, 3, 4, 5]);
    expect(expPoints(EMPTY, "semivariogram")).toEqual([]);
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
    expect(chartHMax(CALC.directions)).toBeCloseTo(60 * 1.05, 12); // the farthest lag centre 6·L
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
    expect(html).toContain('data-series="Ось 1" data-points="4" data-few="1"');
    expect(html).toContain('data-series="Вдоль скважин" data-points="15" data-few="15"');
    expect(count(html, /<circle /g)).toBe(19);
    expect(count(html, /fill="none"[^>]*data-few="true"/g)).toBe(16);
    expect(html).toContain(">2391<");
    expect(html).toContain(">772<");
    expect(html).toContain(`data-line="variance" data-value="${CALC.sigma2}"`);
    expect(html).toContain('data-line="sill" data-value="1.7"');
    expect(html).toContain('data-model="Ось 1"');
    expect(html).not.toContain('data-model="Вдоль скважин"');
    expect(html).toContain("γ(h)");
  });

  it("no point sits on the zero line: a lag without pairs is not at γ = 0", () => {
    const html = render(createElement(VariogramChart, { series: [{ label: "Ось 1", curve: CALC.directions[0] }], type: "semivariogram", sigma2: CALC.sigma2 }));
    // the y pixel of γ = 0 is the bottom of the plot box (340 − 44 = 296)
    const cys = [...html.matchAll(/<circle cx="[\d.]+" cy="([\d.]+)"/g)].map((m) => Number(m[1]));
    expect(cys).toHaveLength(4);
    expect(cys.every((y) => y < 296 - 1)).toBe(true);
  });

  it("all lags empty — a message, no points; the correlogram as 1 − ρ with the level 1; pairwise without a level", () => {
    const empty = render(createElement(VariogramChart, { series: [{ label: "Ось 2", curve: EMPTY }], type: "semivariogram", sigma2: null }));
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

describe("rose of ranges (the real answer of /map)", () => {
  const MAPCALC = readMapCalc(ANSWERS.job_map);
  const ROSE = roseWithWorld(MAPCALC)!;

  it("reads the answer; the outline is broken at a ray without a range, closed over all", () => {
    expect(readRose(null)).toBeNull();
    expect(ROSE.q).toBe(0.75);
    expect(ROSE.rays.map((r) => r.theta)).toEqual([0, 30, 60, 90, 120, 150]);
    expect(ROSE.rays.filter((r) => r.range === null).map((r) => [r.theta, r.beyond])).toEqual([[90, true], [120, true]]);
    // the labels are the world az of the ray of the fan: θ 30° → Аз 330° (a2 — west)
    expect(ROSE.rays.map((r) => r.az)).toEqual([0, 330, 300, 270, 240, 210]);
    const { runs, closed } = roseRuns(ROSE.rays);
    expect(closed).toBe(false);
    // 0, 30, 60 | 150 … 240 | 330 — the run through 360° joins the first
    expect(runs.map((r) => r.map((v) => v.theta))).toEqual([[330, 0, 30, 60], [150, 180, 210, 240]]);
    const all = roseRuns(ROSE.rays.map((r) => ({ ...r, range: 10, beyond: false })));
    expect(all.closed).toBe(true);
    expect(all.runs).toHaveLength(1);
    expect(all.runs[0]).toHaveLength(12);
  });

  it("theta: a1 up, a2 LEFT, counter-clockwise; the nearest ray mod 180°", () => {
    expect(polar(0, 1).map((v) => Number(v.toFixed(12)) + 0)).toEqual([0, -1]);
    expect(polar(90, 1).map((v) => Number(v.toFixed(12)) + 0)).toEqual([-1, 0]);
    expect(polar(30, 2).map((v) => Number(v.toFixed(12)) + 0)).toEqual([-1, -Number((2 * Math.cos(Math.PI / 6)).toFixed(12))]);
    expect(thetaOf(-1, 0)).toBeCloseTo(90, 12);
    expect(thetaOf(0, 1)).toBeCloseTo(0, 12);
    expect(thetaOf(-1, -1)).toBeCloseTo(45, 12);
    expect(thetaOf(1, -1)).toBeCloseTo(135, 12);
    // a ray drawn at θ points to the world direction of the fan: θ 90° — west, to the left on the screen
    const west = MAPCALC.fan!.find((d) => d.theta === 90)!;
    expect(direction(west.az, west.plunge).map((v) => Number(v.toFixed(12)) + 0)).toEqual([-1, 0, 0]);
    expect(nearestRay(ROSE.rays, 170)?.theta).toBe(0);
    expect(nearestRay(ROSE.rays, 140)?.theta).toBe(150);
  });

  it("draws the reached rays as points, the others as dashed spokes; world labels; nothing reached — a message", () => {
    const html = render(createElement(RoseChart, { rose: ROSE, hMax: 60, selected: 30, axes: { a1: "Аз 0° · Пог 0°", a2: "Аз 270° · Пог 0°" } }));
    expect(count(html, /<circle data-ray=/g)).toBe(8);
    expect(count(html, /data-beyond=/g)).toBe(4);
    expect(html).toContain('data-runs="2"');
    expect(html).toContain('data-selected="30"');
    expect(html).toContain("достигнут: 4 из 6");
    expect(html).toContain("a1 · Аз 0° · Пог 0°");
    expect(html).toContain("Аз 270° · Пог 0°");
    const none = render(createElement(RoseChart, { rose: { q: 1, level: 1.84, rays: [{ theta: 0, range: null, beyond: true }] } }));
    expect(none).toContain('data-empty="rose"');
    expect(none).not.toContain("<polygon");
  });
});

describe("variogram map", () => {
  const REAL = readMapCalc(ANSWERS.job_map).map!;
  // 3 × 3 cells (nu = nv = 1, cell 10): the centre is empty, one cell γ = 0 (a real zero); not symmetric in v alone
  const MAP = readVarMap({
    nu: 1, nv: 1, cell: 10, thickness: "inf", min_pairs: 5,
    u: [-10, 0, 10], v: [-10, 0, 10],
    gamma: [[1, 0, 2], [0.5, null, 0.5], [2, 0, 1]],
    n: [[7, 9, 6], [8, 2, 8], [6, 9, 7]],
  })!;

  it("reads the real answer: 9 × 9 cells, N < min_pairs — empty (null), never 0", () => {
    expect(readVarMap(null)).toBeNull();
    expect([REAL.nu, REAL.nv, REAL.cell, REAL.thickness, REAL.min_pairs]).toEqual([4, 4, 10, 20, 2]);
    expect(REAL.u).toEqual([-40, -30, -20, -10, 0, 10, 20, 30, 40]);
    const cells = REAL.gamma.flatMap((row, j) => row.map((g, i) => [g, REAL.n[j][i]] as const));
    expect(cells).toHaveLength(81);
    expect(cells.every(([g, n]) => (g === null) === (n < REAL.min_pairs))).toBe(true);
    expect(cells.some(([g]) => g === null)).toBe(true);
    expect(mapDomain(REAL, null)!.max).toBe(Math.max(...cells.flatMap(([g]) => (g === null ? [] : [g]))));
  });

  it("reads the answer: ∞ thickness — null, null cells stay null", () => {
    expect(MAP.thickness).toBeNull();
    expect(MAP.gamma[1][1]).toBeNull();
    expect(MAP.gamma[0][1]).toBe(0);
    expect(mapDomain(MAP, 1.84)).toEqual({ min: 0, max: 2 });
    expect(mapDomain(MAP, 3)).toEqual({ min: 0, max: 3 });
    expect(cellAt(MAP, 0, 0)).toEqual({ i: 1, j: 1 });
    expect(cellAt(MAP, 9, -9)).toEqual({ i: 2, j: 0 });
    expect(cellAt(MAP, 16, 0)).toBeNull();
  });

  it("the raster: u (a1) up, v (a2) to the LEFT; an empty cell is not painted; a zero has the colour of zero", () => {
    const w = 30;
    const buf = new Uint32Array(w * w);
    expect(rasterMap(buf, w, w, MAP, mapDomain(MAP, null))).toBe(8);
    const px = (x: number, y: number) => buf[y * w + x];
    expect(px(15, 15)).toBe(0); // the centre: empty
    // gamma[j = 0 (v = −10: right)][i = 2 (u = 10: top)] = 2 — top right; gamma[j = 2 (v = 10: left)][i = 2] = 1 — top left
    expect(px(25, 5)).toBe(rgba32(rampColor(1)));
    expect(px(5, 5)).toBe(rgba32(rampColor(0.5)));
    // gamma[j = 1 (v = 0)][i = 0 (u = −10)] = 0.5 — bottom middle; gamma[j = 0 (v = −10)][i = 1 (u = 0)] = 0 — right middle
    expect(px(15, 25)).toBe(rgba32(rampColor(0.25)));
    expect(px(25, 15)).toBe(rgba32(rampColor(0)));
    expect(px(25, 15)).not.toBe(0);
    // the real map: the cell of (u, v) lands at x = (v_max − v)·k, y = (u_max − u)·k
    const W = 90;
    const real = new Uint32Array(W * W);
    const dom = mapDomain(REAL, null);
    expect(rasterMap(real, W, W, REAL, dom)).toBe(REAL.gamma.flat().filter((g) => g !== null).length);
    // the extent −45…45 m on 90 px: 1 px a metre; the centre of a cell (u, v) is at x = 45 − v, y = 45 − u
    REAL.gamma.forEach((row, j) =>
      row.forEach((g, i) => {
        const at = real[(45 - REAL.u[i]) * W + (45 - REAL.v[j])];
        expect(at).toBe(g === null ? 0 : rgba32(mapColor(g, dom)!));
      }),
    );
    // all empty — nothing painted
    const none = { ...MAP, gamma: MAP.gamma.map((r) => r.map(() => null)) };
    buf.fill(0);
    expect(rasterMap(buf, w, w, none, mapDomain(none, null))).toBe(0);
    expect(buf.every((v) => v === 0)).toBe(true);
  });

  it("the chart: counts, hatching for the empty, the colour bar with σ², world titles; all empty — a message", () => {
    const html = render(createElement(VarMapChart, { map: REAL, sigma2: ANSWERS.job_map.sigma2, selected: 30, axes: { a1: "Аз 0°", a2: "Аз 270°" } }));
    expect(html).toContain(`data-cells="81" data-empty="${REAL.gamma.flat().filter((g) => g === null).length}"`);
    expect(html).toContain("data-empty-fill");
    expect(html).toContain("data-colorbar");
    expect(html).toContain(`data-sigma2="${ANSWERS.job_map.sigma2}"`);
    expect(html).toContain("пусто");
    expect(html).toContain('data-selected="30"');
    expect(html).toContain("a2 · Аз 270°");
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
