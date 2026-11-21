// The column of intervals along a hole (ТЗ 8.2.3 preview; Э12 12.26; shared with Э13–Э15): depth → pixels, nice
// depth ticks, colours of the brand ramp shared by samples and composites, a missing value hatched — never the
// colour of zero, intervals with TO ≤ FROM not drawn, flagged composites marked.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DepthAxis, SampleColumn } from "../src/charts/column/SampleColumn";
import { colorOf, columnBars, columnsRange, depthScale, GRADE_RAMP, niceTicks, rampColor, valueRange, valueText, type Columns } from "../src/charts/column/scale";

// Т8.2-1: samples 0–1: 1,0; 1–3: 2,0; 3–4: none; 4–6: 4,0 → composites 1,5; 2,0; 4,0
const SAMPLES: Columns = { from: [0, 1, 3, 4], to: [1, 3, 4, 6], values: { AU: [1, 2, null, 4] } };
const COMPOSITES: Columns = { from: [0, 2, 4], to: [2, 4, 6], values: { AU: [1.5, 2, 4], FLAG: [null, "", "мало проб"] } };

describe("scale", () => {
  it("maps depth to pixels and back; a range of zero length is widened", () => {
    const s = depthScale(10, 20, 100, 5);
    expect(s.y(10)).toBe(5);
    expect(s.y(20)).toBe(105);
    expect(s.depth(55)).toBe(15);
    const flat = depthScale(7, 7, 50);
    expect([flat.top, flat.bottom]).toEqual([7, 8]);
  });

  it("nice ticks: steps 1, 2, 5 × 10ⁿ inside the range", () => {
    expect(niceTicks(0, 7, 6)).toEqual([0, 2, 4, 6]);
    expect(niceTicks(12.3, 47.9, 5)).toEqual([20, 30, 40]);
    expect(niceTicks(0, 0.5, 5)).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5]);
    expect(niceTicks(5, 5)).toEqual([]);
  });

  it("ranges skip missing ends, TO ≤ FROM, missing and text values", () => {
    const bad: Columns = { from: [2, null, 9], to: [1, 3, 12], values: { AU: [Number.NaN, "x", 3] } };
    expect(columnsRange(SAMPLES, COMPOSITES)).toEqual([0, 6]);
    expect(columnsRange(bad)).toEqual([9, 12]);
    expect(columnsRange({ from: [], to: [], values: {} })).toBeNull();
    expect(valueRange("AU", SAMPLES, COMPOSITES)).toEqual([1, 4]);
    expect(valueRange("AU", bad)).toEqual([3, 3]);
    expect(valueRange("CU", SAMPLES)).toBeNull();
  });

  it("colours: the ends of the brand ramp, a missing value has none (not the colour of zero)", () => {
    const c = colorOf([1, 4]);
    expect(c(1)).toBe(GRADE_RAMP[0]);
    expect(c(4)).toBe(GRADE_RAMP[GRADE_RAMP.length - 1]);
    expect(c(null)).toBeNull();
    expect(c(Number.NaN)).toBeNull();
    expect(c(0)).toBe(GRADE_RAMP[0]); // below the range — clamped, still a colour: zero is a value
    expect(colorOf([2, 2])(2)).toBe(rampColor(0.5));
    expect(colorOf(null)(2)).toBeNull();
    expect(rampColor(1 / 3)).toBe(GRADE_RAMP[1]);
    expect(valueText(null)).toBe("нет значения");
    expect(valueText(Number.NaN)).toBe("нет значения");
    expect(valueText(1.5454545)).toBe("1,545");
  });

  it("bars: one per drawable interval, at least 1 px high", () => {
    const s = depthScale(0, 6, 60);
    const bars = columnBars(SAMPLES, "AU", s, colorOf([1, 4]));
    expect(bars.map((b) => [b.from, b.to, b.y, b.h])).toEqual([
      [0, 1, 0, 10],
      [1, 3, 10, 20],
      [3, 4, 30, 10],
      [4, 6, 40, 20],
    ]);
    expect(bars[2].color).toBeNull();
    const thin = columnBars({ from: [0, 1, 2], to: [0.001, 1, 1.5], values: {} }, null, s, () => null);
    expect(thin).toHaveLength(1);
    expect(thin[0].h).toBe(1);
  });
});

describe("SampleColumn", () => {
  const scale = depthScale(0, 6, 120, 2);
  const color = colorOf(valueRange("AU", SAMPLES, COMPOSITES));

  it("draws every interval with its tooltip; the missing one hatched", () => {
    const html = renderToStaticMarkup(createElement(SampleColumn, { title: "Пробы", cols: SAMPLES, field: "AU", scale, color, testId: "s" }));
    expect(html.match(/<rect /g)).toHaveLength(4);
    expect(html).toContain("<title>0–1 м: 1</title>");
    expect(html).toContain("<title>3–4 м: нет значения</title>");
    expect(html.match(/data-missing="yes"/g)).toHaveLength(1);
    expect(html).toMatch(/<pattern id="hatch-[^"]+"/);
    expect(html).toMatch(/fill="url\(#hatch-[^"]+\)"/);
    expect(html).toContain('aria-label="Пробы: 4 интервалов"');
  });

  it("marks flagged composites and writes values on tall bars", () => {
    const html = renderToStaticMarkup(createElement(SampleColumn, { title: "Композиты", cols: COMPOSITES, field: "AU", scale, color, flagField: "FLAG" }));
    expect(html.match(/data-flag="yes"/g)).toHaveLength(1);
    expect(html).toContain("4–6 м: 4 · мало проб");
    expect(html).toContain(">1,5<");
    expect(html).not.toContain('data-missing="yes"');
  });

  it("the depth axis has nice ticks in metres", () => {
    const html = renderToStaticMarkup(createElement(DepthAxis, { scale, count: 3 }));
    for (const t of ["0", "2", "4", "6"]) expect(html).toContain(`>${t}</text>`);
  });
});
