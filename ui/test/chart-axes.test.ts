// Axes of the charts (ADR 0019 §11): linear, log10 and probability scales; a value the axis cannot show maps to
// null (never 0); «round» ticks; numbers of axes and forms; CSV of the charts (rules of Э6).
import { describe, expect, it } from "vitest";
import { axisValue, domainOf, formatNumber, logTicks, makeScale, normCdf, normInv, numberText, parseNumber, roundUp } from "../src/charts/core/axisScale";
import { csvCell, csvText } from "../src/charts/core/csv";

describe("scales", () => {
  it("linear: domain onto pixels, Y bottom first; ticks 1-2-5", () => {
    const s = makeScale("linear", { min: 0, max: 10 }, 400, 0);
    expect(s.map(0)).toBe(400);
    expect(s.map(5)).toBe(200);
    expect(s.invert(100)).toBeCloseTo(7.5, 12);
    expect(s.map(null)).toBeNull();
    expect(s.map(Number.NaN)).toBeNull();
    expect(s.ticks(5).map((t) => t.value)).toEqual([0, 2, 4, 6, 8, 10]);
  });

  it("log10: decades with 2 and 5 on short ranges; values ≤ 0 are not drawn", () => {
    const short = logTicks(0.01, 10);
    [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10].forEach((v, i) => expect(short[i]).toBeCloseTo(v, 12));
    expect(short).toHaveLength(10);
    expect(logTicks(0.001, 10000)).toEqual([0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000]);
    expect(logTicks(0, 10)).toEqual([]);
    const s = makeScale("log", { min: 1, max: 100 }, 0, 200);
    expect(s.map(10)).toBeCloseTo(100, 9);
    expect(s.map(0)).toBeNull();
    expect(s.map(-1)).toBeNull();
    expect(s.invert(200)).toBeCloseTo(100, 9);
  });

  it("probability: Φ⁻¹ of P; the middle at 50 %, symmetric; P outside (0; 1) is not drawn", () => {
    expect(normInv(0.5)).toBe(0);
    expect(normInv(0.975)).toBeCloseTo(1.959963984540054, 8);
    expect(normInv(0.001)).toBeCloseTo(-3.090232306167813, 7);
    expect(normInv(0.9)).toBeCloseTo(-normInv(0.1), 9);
    expect(normInv(0)).toBe(-Infinity);
    expect(Number.isNaN(normInv(1.2))).toBe(true);
    for (const p of [0.001, 0.05, 0.3, 0.5, 0.8, 0.999]) expect(normCdf(normInv(p))).toBeCloseTo(p, 6);
    const s = makeScale("prob", { min: 0.01, max: 0.99 }, 300, 0);
    expect(s.map(0.5)).toBeCloseTo(150, 6);
    expect(s.map(0)).toBeNull();
    expect(s.map(1)).toBeNull();
    expect(s.ticks().map((t) => t.label)).toEqual(["1%", "5%", "10%", "20%", "30%", "50%", "70%", "80%", "90%", "95%", "99%"]);
    expect(axisValue("prob", 0.5)).toBe(0);
  });

  it("domains are padded in the axis space; a log domain takes only positive values", () => {
    expect(domainOf([1, 2, 3, null, Number.NaN])).toEqual({ min: 0.9, max: 3.1 });
    expect(domainOf([5, 5])).toEqual({ min: 4.5, max: 5.5 });
    expect(domainOf([0, 0])).toEqual({ min: -1, max: 1 });
    expect(domainOf([null, undefined])).toBeNull();
    const d = domainOf([-1, 0, 1, 100], "log")!;
    expect(d.min).toBeCloseTo(10 ** -0.1, 12);
    expect(d.max).toBeCloseTo(10 ** 2.1, 9);
    expect(domainOf([-1, 0], "log")).toBeNull();
    expect(domainOf([-3, 4], "linear", 0, 0)).toEqual({ min: 0, max: 4 });
  });

  it("round numbers 1, 2, 2.5, 5 × 10ⁿ", () => {
    expect([0.7, 1, 1.2, 2.2, 3, 7, 26, 0.031].map(roundUp)).toEqual([1, 1, 2, 2.5, 5, 10, 50, 0.05]);
  });
});

describe("numbers", () => {
  it("axes and tables: no trailing zeros, exponent for very small and large, «—» for missing", () => {
    expect(formatNumber(1.23456)).toBe("1,23");
    expect(formatNumber(2.5)).toBe("2,5");
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(1234567)).toBe("1,2e6");
    expect(formatNumber(0.0001234)).toBe("1,2e-4");
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(Number.NaN)).toBe("—");
  });

  it("form fields: a decimal comma is a point, empty is null (never 0)", () => {
    expect(parseNumber("0,25")).toBe(0.25);
    expect(parseNumber(" 12.5 ")).toBe(12.5);
    expect(parseNumber("1 000")).toBe(1000);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("abc")).toBeNull();
    expect(numberText(0.25)).toBe("0,25");
    expect(numberText(null)).toBe("");
  });
});

describe("CSV of the charts (rules of Э6)", () => {
  it("«;», decimal point, empty stays empty, formulas are disarmed", () => {
    expect(csvText(["От", "До", "Проб"], [[0, 0.5, 3], [0.5, 1, null], [1, Number.NaN, 0]])).toBe('"От";"До";"Проб"\r\n0;0.5;3\r\n0.5;1;\r\n1;;0');
    expect(csvCell("=SUM(A1)")).toBe(`"'=SUM(A1)"`);
    expect(csvCell("-1")).toBe(`"'-1"`);
    expect(csvCell('a"b')).toBe('"a""b"');
    expect(csvCell(undefined)).toBe("");
  });
});
