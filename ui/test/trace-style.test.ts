import { describe, expect, it } from "vitest";
import { crossedWireframes, DEFAULT_TRACE_STYLE, depthText, isLabelDepth, normalizeTraceStyle, tickDepths } from "../src/viz/holes/traceStyle";

describe("hole trace style", () => {
  it("keeps valid stored values and drops broken ones", () => {
    const s = normalizeTraceStyle({ width: 40, color: "#12ab34", ticks: true, tickStep: -1, wireframeColor: "object", blockColor: "red" });
    expect(s).toMatchObject({ width: 12, color: "#12ab34", ticks: true, tickStep: 0.5, wireframeColor: "object", blockColor: DEFAULT_TRACE_STYLE.blockColor });
    expect(normalizeTraceStyle(null)).toEqual(DEFAULT_TRACE_STYLE);
    expect(DEFAULT_TRACE_STYLE.endDepth).toBe(false);
    expect(normalizeTraceStyle({ endDepth: true }).endDepth).toBe(true);
    expect(normalizeTraceStyle({ endDepth: "yes" }).endDepth).toBe(false);
  });

  it("marks crossings with wireframes, and with DTMs only when asked: a DTM built through the collars is crossed by every hole at 0 m", () => {
    const relief = { id: 1, triangle_count: 5000, dtm: { raster_id: null } };
    const ore = { id: 2, triangle_count: 800, dtm: null };
    const fault = { id: 3, triangle_count: 200, dtm: null };
    const empty = { id: 4, triangle_count: 0, dtm: null };
    const pit = { id: 5, triangle_count: 900, dtm: { raster_id: 7 } };
    const all = [relief, ore, fault, empty, pit];
    const shown = (id: number) => id !== 3 && id !== 5; // the fault and the pit are hidden
    const on = { ...DEFAULT_TRACE_STYLE, intersections: true };
    const ids = (style: Parameters<typeof crossedWireframes>[1]) => crossedWireframes(all, style, shown).map((w) => w.id);

    expect(DEFAULT_TRACE_STYLE).toMatchObject({ intersections: false, withWireframes: true, withDtm: false, onlyVisible: true });
    expect(ids(DEFAULT_TRACE_STYLE)).toEqual([]); // crossings are off
    expect(ids(on)).toEqual([2]); // wireframes only: not the relief, not what is hidden, not what has no triangles
    expect(ids({ ...on, onlyVisible: false })).toEqual([2, 3]);
    expect(ids({ ...on, withDtm: true })).toEqual([1, 2]);
    expect(ids({ ...on, withDtm: true, onlyVisible: false })).toEqual([1, 2, 3, 5]);
    expect(ids({ ...on, withDtm: true, withWireframes: false, onlyVisible: false })).toEqual([1, 5]); // DTMs alone
    expect(ids({ ...on, withWireframes: false })).toEqual([]);
    // the demo: a relief through the collars and nothing else — no request at all by default
    expect(crossedWireframes([relief], on, () => true)).toEqual([]);
    expect(crossedWireframes([relief], { ...on, withDtm: true }, () => true)).toEqual([relief]);
    // a style stored before the setting was there has no DTMs; a broken value is no
    expect(normalizeTraceStyle({ intersections: true, withWireframes: true }).withDtm).toBe(false);
    expect(normalizeTraceStyle({ withDtm: true }).withDtm).toBe(true);
    expect(normalizeTraceStyle({ withDtm: "да" }).withDtm).toBe(false);
  });

  it("puts ticks at multiples of the step and writes every n-th", () => {
    expect(tickDepths(35, 10)).toEqual([10, 20, 30]);
    expect(tickDepths(1, 0.25)).toEqual([0.25, 0.5, 0.75, 1]);
    expect(tickDepths(0, 10)).toEqual([]);
    expect([10, 20, 50, 100].filter((d) => isLabelDepth(d, 50))).toEqual([50, 100]);
    expect(depthText(125)).toBe("125 м");
    expect(depthText(12.5)).toBe("12.5 м");
    expect(depthText(3.456)).toBe("3.46 м");
  });
});
