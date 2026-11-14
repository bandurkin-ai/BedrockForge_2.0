import { describe, expect, it } from "vitest";
import { ColorScale, autoRange, formatValue, makeScale } from "./colorScale";
import { azimuthDeg, defaultSection, inCorridor, niceStep, niceTicks, toSection } from "./geometry";

describe("ColorScale", () => {
  it("maps values to positions on a log scale and clamps", () => {
    const s = new ColorScale(0.01, 10, true);
    expect(s.position(0.01)).toBeCloseTo(0);
    expect(s.position(10)).toBeCloseTo(1);
    expect(s.position(0.316)).toBeCloseTo(0.5, 2);
    expect(s.position(0.0001)).toBe(0);
    expect(s.position(1e6)).toBe(1);
    expect(s.position(-5)).toBe(0);
  });

  it("maps a linear scale and gives end colors from the ramp", () => {
    const s = new ColorScale(0, 100, false);
    expect(s.position(25)).toBeCloseTo(0.25);
    expect(s.rgb(0)).toEqual([43, 95, 168]);
    expect(s.rgb(100)).toEqual([178, 24, 43]);
    expect(s.css(null)).toBe("#9aa3b5");
  });

  it("repairs a log scale with a non-positive minimum", () => {
    const s = new ColorScale(0, 1000, true);
    expect(s.min).toBeGreaterThan(0);
    expect(s.position(1000)).toBe(1);
  });

  it("gives decade ticks for a log scale and round ticks for a linear one", () => {
    expect(new ColorScale(0.01, 100, true).ticks().map((t) => t.value)).toEqual([0.01, 0.1, 1, 10, 100]);
    expect(new ColorScale(0, 100, false).ticks().map((t) => t.value)).toEqual([0, 20, 40, 60, 80, 100]);
  });
});

describe("scale settings", () => {
  const stats = { count: 10, min: 0.001, max: 1302, p05: 0.0025, p50: 0.013, p95: 0.25 };

  it("uses the percentile range unless the user overrides it", () => {
    expect(autoRange(stats)).toEqual([0.0025, 0.25]);
    expect(makeScale({ min: null, max: null, log: true }, stats)?.max).toBe(0.25);
    expect(makeScale({ min: 0.1, max: 5, log: true }, stats)?.min).toBe(0.1);
  });

  it("copes with empty or degenerate statistics", () => {
    expect(autoRange({ count: 0, min: null, max: null, p05: null, p50: null, p95: null })).toBeNull();
    expect(autoRange({ count: 3, min: 2, max: 2, p05: 2, p50: 2, p95: 2 })).toEqual([2, 20]);
    expect(makeScale({ min: 5, max: 5, log: false }, null)).toBeNull();
  });

  it("formats values of very different magnitude", () => {
    expect(formatValue(1302.2)).toBe("1302");
    expect(formatValue(2.345)).toBe("2.35");
    expect(formatValue(0.0123)).toBe("0.012");
    expect(formatValue(0.00005)).toBe("0.00005");
    expect(formatValue(0)).toBe("0");
    expect(formatValue(null)).toBe("—");
  });
});

describe("geometry", () => {
  it("makes round ticks", () => {
    expect(niceStep(100, 5)).toBe(20);
    expect(niceTicks(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(5, 5)).toEqual([]);
  });

  it("projects points on a section line", () => {
    const north = { x0: 100, y0: 200, az: 0, length: null, halfWidth: 10 };
    expect(toSection(100, 250, north)).toEqual({ u: 50, d: 0 });
    const p = toSection(105, 250, north); // 5 m east of a north-pointing line is to its right
    expect(p.u).toBeCloseTo(50);
    expect(p.d).toBeCloseTo(5);
    const east = { ...north, az: 90 };
    expect(toSection(160, 200, east).u).toBeCloseTo(60);
    expect(toSection(100, 190, east).d).toBeCloseTo(10); // south is to the right of an east-pointing line
  });

  it("selects holes by corridor and length", () => {
    const line = { x0: 0, y0: 0, az: 90, length: 100, halfWidth: 10 };
    expect(inCorridor(50, 5, line)).toBe(true);
    expect(inCorridor(50, 15, line)).toBe(false);
    expect(inCorridor(150, 0, line)).toBe(false);
    expect(inCorridor(150, 0, { ...line, length: null })).toBe(true);
  });

  it("computes azimuths clockwise from north", () => {
    expect(azimuthDeg(0, 1)).toBe(0);
    expect(azimuthDeg(1, 0)).toBe(90);
    expect(azimuthDeg(0, -1)).toBe(180);
    expect(azimuthDeg(-1, 0)).toBe(270);
  });

  it("puts the default section across the main direction of the collars", () => {
    const eastWest: [number, number][] = [[0, 0], [100, 2], [200, -1], [300, 1]];
    const section = defaultSection(eastWest);
    expect(Math.abs(Math.sin((section.az * Math.PI) / 180))).toBeLessThan(0.05); // runs north-south
    expect(section.x0).toBeCloseTo(150);
    expect(defaultSection([]).az).toBe(90);
    expect(defaultSection([[5, 5]]).x0).toBe(5);
  });
});
