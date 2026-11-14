import { describe, expect, it } from "vitest";
import { buildRibbon, flattenRibbon } from "../shell/ribbonConfig";
import { BOTTOM_TITLES, DEFAULT_WORKSPACE, normalize, openBottom } from "../shell/workspace";
import { ANALYSES } from "./analyses";
import { domainOf, formatNumber, linePath, linearScale, parseNumber, ticksFor } from "./chartMath";
import { defaultLag, planSpan } from "./defaults";

describe("chart math", () => {
  it("pads the domain, ignores missing values and widens a flat series", () => {
    expect(domainOf([1, null, 3, NaN])).toEqual({ min: 0.9, max: 3.1 });
    expect(domainOf([null, NaN])).toBeNull();
    expect(domainOf([5, 5])).toEqual({ min: 4.5, max: 5.5 });
    expect(domainOf([0.1, 2], 0.5, 0)?.min).toBe(0);  // a floor keeps the axis from going negative
  });

  it("maps values to pixels, also upside down for the y axis", () => {
    const x = linearScale({ min: 0, max: 10 }, 0, 100);
    const y = linearScale({ min: 0, max: 10 }, 200, 0);
    expect([x(0), x(5), x(10)]).toEqual([0, 50, 100]);
    expect([y(0), y(10)]).toEqual([200, 0]);
    expect(ticksFor({ min: 0, max: 100 }, 5)).toEqual([0, 20, 40, 60, 80, 100]);
  });

  it("breaks the line where a value is missing", () => {
    const id = (v: number) => v;
    expect(linePath([1, 2, 3, 4], [1, null, 3, 4], id, id)).toBe("M1.0,1.0M3.0,3.0L4.0,4.0");
    expect(linePath([], [], id, id)).toBe("");
  });

  it("formats and parses numbers the way the form needs", () => {
    expect(formatNumber(0.123456)).toBe("0.123");
    expect(formatNumber(1234.5678, 4)).toBe("1235");
    expect(formatNumber(0.00004)).toBe("4.0e-5");
    expect(formatNumber(null)).toBe("—");
    expect(parseNumber("12,5")).toBe(12.5);
    expect(parseNumber("  ")).toBeNull();
    expect(parseNumber("abc")).toBeNull();
  });
});

describe("defaults", () => {
  it("measures the plan extent from holes with coordinates only", () => {
    expect(planSpan([{ x: 0, y: 0 }, { x: 300, y: 100 }, { x: null, y: 5 }])).toBe(300);
    expect(planSpan([{ x: 1, y: 1 }])).toBeNull();
    expect(planSpan([{ x: 1, y: 1 }, { x: 1, y: 1 }])).toBeNull();
  });

  it("proposes a round lag giving about 20 lags over the extent", () => {
    expect(defaultLag(1000)).toBe(50);
    expect(defaultLag(null)).toBe(50);
    expect(defaultLag(400)).toBe(20);
  });
});

describe("statistics in the shell", () => {
  it("has a bottom tab that opens the dock and survives a reload of stored state", () => {
    expect(BOTTOM_TITLES.stats).toBe("Статистика");
    expect(openBottom(DEFAULT_WORKSPACE, "stats")).toMatchObject({ bottom: true, bottomTab: "stats" });
    expect(normalize({ bottomTab: "stats" }).bottomTab).toBe("stats");
  });

  it("puts every analysis on the ribbon and disables them without a set", () => {
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, { hasHole: false, hasSet: false, hasSection: false })).filter((i) => i.command === "stats.open");
    expect(items.map((i) => i.args?.analysis)).toEqual(ANALYSES.map(([id]) => id));
    expect(items.every((i) => i.disabled)).toBe(true);
    const enabled = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, { hasHole: false, hasSet: true, hasSection: false })).filter((i) => i.command === "stats.open");
    expect(enabled.some((i) => i.disabled)).toBe(false);
  });
});
