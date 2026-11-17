import { describe, expect, it } from "vitest";
import { classBounds, classCounts, classesFromBounds, CLASS_METHODS, parseBounds, quantile } from "../src/viz/downhole/classify";
import { axisPosition, axisValue, buildHistogram, cumulative, fieldValues } from "../src/viz/downhole/histogram";
import { gradientHex, mixHex, rampColors } from "../src/viz/downhole/gradients";

const uniform = Array.from({ length: 1000 }, (_, i) => i + 1);
// a long-tailed set like gold grades
const lognormal = Array.from({ length: 2000 }, (_, i) => Math.exp(((i % 200) / 200) * 6 - 4 + (i % 7) * 0.05));
const methods = CLASS_METHODS.map((m) => m[0]);

describe("automatic classes", () => {
  it("gives ascending bounds without repeats inside the range, by every method", () => {
    for (const values of [uniform, lognormal]) {
      const lo = Math.min(...values);
      const hi = Math.max(...values);
      for (const m of methods) {
        const b = classBounds(values, m, 5);
        expect(b.length, m).toBeGreaterThan(0);
        expect(b.length, m).toBeLessThanOrEqual(4);
        for (let i = 0; i < b.length; i++) {
          expect(b[i], m).toBeGreaterThan(lo);
          expect(b[i], m).toBeLessThanOrEqual(hi);
          if (i) expect(b[i], m).toBeGreaterThan(b[i - 1]);
        }
      }
    }
  });

  it("gives no bounds for degenerate data", () => {
    for (const m of methods) {
      expect(classBounds([], m, 5)).toEqual([]);
      expect(classBounds([3], m, 5)).toEqual([]);
      expect(classBounds([2, 2, 2, 2], m, 5)).toEqual([]);
      expect(classBounds([NaN, Infinity], m, 5)).toEqual([]);
    }
  });

  it("puts about the same number of samples into each quantile class", () => {
    const sorted = [...lognormal].sort((a, b) => a - b);
    const classes = classesFromBounds(classBounds(lognormal, "quantile", 5));
    expect(classes).toHaveLength(5);
    for (const c of classCounts(sorted, classes)) expect(Math.abs(c - 400)).toBeLessThan(40);
  });

  it("makes the shares fall in the progressive breaks", () => {
    const sorted = [...uniform];
    const share = (m: "progressive" | "double") => classCounts(sorted, classesFromBounds(classBounds(uniform, m, 5))).map((c) => Math.round((c / 1000) * 100));
    expect(share("progressive")).toEqual([33, 27, 20, 13, 7]);
    const d = share("double");
    expect(d[0]).toBe(50);
    expect(d[1]).toBe(25);
    expect(d[0] + d[1] + d[2] + d[3] + d[4]).toBe(100);
  });

  it("splits equal intervals evenly and reads bounds typed by hand", () => {
    expect(classBounds([0, 100], "equal", 4)).toEqual([25, 50, 75]);
    expect(parseBounds("0,3 1; 3 10 x 1")).toEqual([0.3, 1, 3, 10]);
    expect(quantile([1, 2, 3, 4, 5], 0.5)).toBe(3);
  });

  it("counts a value equal to a bound in the upper class and skips values below every class", () => {
    expect(classCounts([0.1, 0.3, 0.3, 1, 5], [{ from: null, color: "#000000" }, { from: 0.3, color: "#000000" }, { from: 1, color: "#000000" }])).toEqual([1, 2, 2]);
    expect(classCounts([0.1, 0.5], [{ from: 0.3, color: "#000000" }])).toEqual([1]);
    expect(classCounts([1, 2], [])).toEqual([]);
  });

  it("keeps the labels of classes that stay in place and spreads a gradient over the classes", () => {
    const c = classesFromBounds([1, 0.3], "grey-red", false, [{ from: null, color: "#000000", label: "пусто" }]);
    expect(c.map((x) => x.from)).toEqual([null, 0.3, 1]);
    expect(c[0].label).toBe("пусто");
    expect(c[0].color).toBe(gradientHex("grey-red", 0));
    expect(c[2].color).toBe(gradientHex("grey-red", 1));
    expect(rampColors("viridis", 3, true)[0]).toBe(gradientHex("viridis", 1));
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
  });
});

describe("histogram of a field", () => {
  const data = {
    tables: [],
    holes: [{ hole_id: "A", found: true, td: 10, max_depth: 10, tables: { t: [
      { from: 0, to: 1, values: { AU: 0.1 } }, { from: 1, to: 4, values: { AU: 10 } }, { from: 4, to: 5, values: { AU: null } }, { from: 5, to: 6, values: { AU: 0 } }] } }],
  };

  it("collects numbers with interval lengths, sorted", () => {
    const v = fieldValues(data as never, "t", "AU");
    expect([...v.values]).toEqual([0, 0.1, 10]);
    expect([...v.lengths]).toEqual([1, 1, 3]);
    expect(v.rows).toBe(4);
  });

  it("bins by count or by length, on a linear or a log axis", () => {
    const v = fieldValues(data as never, "t", "AU");
    const lin = buildHistogram(v, 10)!;
    expect(lin.counts[0]).toBe(2);
    expect(lin.counts[9]).toBe(1);
    expect(cumulative(lin)[9]).toBe(1);
    const log = buildHistogram(v, 2, true, true)!;
    expect(log.min).toBe(0.1); // zero has no logarithm
    expect(log.counts).toEqual([1, 3]);
    expect(axisPosition(log, 1)).toBeCloseTo(0.5);
    expect(axisValue(log, 0.5)).toBeCloseTo(1);
    expect(buildHistogram(fieldValues(data as never, "t", "NONE"), 10)).toBeNull();
  });
});
