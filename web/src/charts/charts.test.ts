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
    const p = toParams({ ...DEFAULT_HISTOGRAM, field: "comp:AU", binMode: "width", binWidth: "0,25", bins: "40", topCut: "12,5", bottomCut: "0,1", start: "" })!;
    expect(p).toMatchObject({ field: "comp:AU", weight: "length", bin_width: 0.25, bins: null, top_cut: 12.5, top_mode: "cap", bottom_cut: 0.1, bottom_mode: "exclude", start: null, weight_field: null });
    const q = toParams({ ...DEFAULT_HISTOGRAM, field: "comp:AU", weight: "field", weightField: "attr:DENSITY", binMode: "count" })!;
    expect(q).toMatchObject({ bins: 30, bin_width: null, weight_field: "attr:DENSITY" });
  });
});

describe("Q-Q plot", async () => {
  const { labelledPoints, qqDomains } = await import("./QQChart");
  const { defaultQQ, toQQRequest, toSample } = await import("./QQWindow");
  const pts: [number, number, number][] = [[1, 2, 10], [2, 3, 50], [4, 9, 90]];

  it("uses one common range for equal axes, so the 1:1 line is the diagonal", () => {
    const d = qqDomains(pts, false, true)!;
    expect(d.x).toEqual(d.y);
    expect(d.x.min).toBeLessThan(1);
    expect(d.x.max).toBeGreaterThan(9);
    const own = qqDomains(pts, false, false)!;
    expect(own.x.max).toBeLessThan(own.y.max);
    expect(qqDomains([[0, 1, 50]], true, true)).toBeNull(); // no positive x on log axes
  });

  it("labels the points nearest to P10, P50, P90", () => {
    expect(labelledPoints(pts).map((l) => l.index)).toEqual([0, 1, 2]);
  });

  it("builds requests for a distribution and for two samples", () => {
    const q = defaultQQ(3);
    expect(toQQRequest(q)).toBeNull(); // no field yet
    q.x.field = "comp:AU";
    expect(toQQRequest(q)).toMatchObject({ x: { set_id: 3, field: "comp:AU", weight: "length", holes: null }, y: { kind: "lognormal" } });
    q.mode = "sample";
    q.y = { ...q.y, setId: 5, field: "comp:AU", holes: "H1, H2  H3" };
    q.quantiles = "5000";
    expect(toQQRequest(q)).toMatchObject({ y: { kind: "sample", set_id: 5, holes: ["H1", "H2", "H3"] }, quantiles: 999 });
    expect(toSample({ ...q.x, weight: "field", weightField: null })).toBeNull();
  });
});

describe("dendrogram and saved charts", async () => {
  const { clustersAt, corrColor } = await import("./DendrogramChart");
  const { dendrogramName, DEFAULT_DENDROGRAM } = await import("./DendrogramWindow");
  const { histogramName } = await import("./HistogramWindow");
  const { qqName } = await import("./QQWindow");
  // AU+AG at 0.1, CU+PB at 0.3, the two pairs at 0.9 (scipy linkage rows).
  const linkage = [[0, 1, 0.1, 2], [2, 3, 0.3, 2], [4, 5, 0.9, 4]];

  it("cuts the tree into groups", () => {
    expect(new Set(clustersAt(linkage, 4, [0, 1, 2, 3], 0.5).leaf).size).toBe(2);
    expect(clustersAt(linkage, 4, [0, 1, 2, 3], 0.5).leaf).toEqual([0, 0, 1, 1]);
    expect(new Set(clustersAt(linkage, 4, [0, 1, 2, 3], 0.05).leaf).size).toBe(4);
    expect(new Set(clustersAt(linkage, 4, [0, 1, 2, 3], 1).leaf).size).toBe(1);
  });

  it("colours correlations from blue through white to red", () => {
    expect(corrColor(0)).toBe("rgb(245,245,245)");
    expect(corrColor(1)).toBe("rgb(200,60,50)");
    expect(corrColor(-1)).toBe("rgb(50,110,200)");
    expect(corrColor(null)).toBe("#808080");
  });

  it("names saved charts after their settings", async () => {
    const { DEFAULT_HISTOGRAM } = await import("./HistogramWindow");
    const { defaultQQ } = await import("./QQWindow");
    expect(histogramName({ ...DEFAULT_HISTOGRAM, field: "comp:AU", log: true })).toBe("Гистограмма AU (log)");
    const q = { ...defaultQQ(1), x: { ...defaultQQ(1).x, field: "comp:AU" } };
    expect(qqName(q)).toBe("Q-Q AU — логнормальное");
    expect(qqName({ ...q, mode: "sample", y: { ...q.y, setId: 2, field: "comp:AU", holes: "H1" } })).toBe("Q-Q AU / AU (часть скважин) (два набора)");
    expect(dendrogramName({ ...DEFAULT_DENDROGRAM, method: "spearman" }, 5)).toBe("Дендрограмма (5 полей), Спирмен");
    expect(dendrogramName(DEFAULT_DENDROGRAM, 2)).toBe("Дендрограмма (2 поля)");
    expect(dendrogramName(DEFAULT_DENDROGRAM, 21)).toBe("Дендрограмма (21 поле)");
    expect(dendrogramName(DEFAULT_DENDROGRAM, 12)).toBe("Дендрограмма (12 полей)");
  });
});
