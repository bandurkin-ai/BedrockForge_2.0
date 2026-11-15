import { describe, expect, it } from "vitest";
import type { HistogramResult } from "../api";
import { barValues, logTicks } from "./HistogramChart";
import { DEFAULT_HISTOGRAM, toParams } from "./HistogramWindow";

describe("histogram chart", () => {
  it("puts log ticks on decades, with 2 and 5 for short ranges", () => {
    const short = logTicks(0.01, 10); // three decades
    [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10].forEach((v, i) => expect(short[i]).toBeCloseTo(v, 12));
    expect(short).toHaveLength(10);
    expect(logTicks(0.001, 10000)).toEqual([0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000]);
  });

  it("chooses bar values by the Y axis", () => {
    const r = { bins: { percent: [50, 50], weights: [3, 3], counts: [1, 2] } } as unknown as HistogramResult;
    expect(barValues(r, "percent")).toEqual([50, 50]);
    expect(barValues(r, "weight")).toEqual([3, 3]);
    expect(barValues(r, "count")).toEqual([1, 2]);
  });
});

describe("histogram settings", () => {
  it("waits for a field and a weight field", () => {
    expect(toParams(DEFAULT_HISTOGRAM)).toBeNull();
    expect(toParams({ ...DEFAULT_HISTOGRAM, field: "comp:AU", weight: "length_field" })).toBeNull();
  });

  it("sends only the chosen bin setting and parses numbers with commas", () => {
    const p = toParams({ ...DEFAULT_HISTOGRAM, field: "comp:AU", binMode: "width", binWidth: "0,25", bins: "40", topCut: "12,5", start: "" })!;
    expect(p).toMatchObject({ field: "comp:AU", weight: "length", bin_width: 0.25, bins: null, top_cut: 12.5, start: null, weight_field: null });
    const q = toParams({ ...DEFAULT_HISTOGRAM, field: "comp:AU", weight: "field", weightField: "attr:DENSITY", binMode: "count" })!;
    expect(q).toMatchObject({ bins: 30, bin_width: null, weight_field: "attr:DENSITY" });
  });
});
