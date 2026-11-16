import { describe, expect, it } from "vitest";
import { DEFAULT_TRACE_STYLE, depthText, isLabelDepth, normalizeTraceStyle, tickDepths } from "./traceStyle";

describe("hole trace style", () => {
  it("keeps valid stored values and drops broken ones", () => {
    const s = normalizeTraceStyle({ width: 40, color: "#12ab34", ticks: true, tickStep: -1, wireframeColor: "object", blockColor: "red" });
    expect(s).toMatchObject({ width: 12, color: "#12ab34", ticks: true, tickStep: 0.5, wireframeColor: "object", blockColor: DEFAULT_TRACE_STYLE.blockColor });
    expect(normalizeTraceStyle(null)).toEqual(DEFAULT_TRACE_STYLE);
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
