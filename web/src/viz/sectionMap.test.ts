import { describe, expect, it } from "vitest";
import { overviewBounds, windowCorners } from "./SectionMap";

describe("section plan map", () => {
  it("draws the window of a north-going line: away to the west, towards (viewer) to the east", () => {
    const c = windowCorners({ x0: 100, y0: 200, az: 0, length: 50, towards: 10, away: 5 }, 0);
    expect(c.map(([x, y]) => [Math.round(x), Math.round(y)])).toEqual([[95, 200], [95, 250], [110, 250], [110, 200]]);
  });

  it("bounds the data and the extra points", () => {
    const o = { holes: [[[0, 0], [10, 5]] as [number, number][]], collars: [], segments: [[20, -5, 30, 0]] as [number, number, number, number][], boxes: [] };
    expect(overviewBounds(o, [[-4, 40]])).toEqual([-4, -5, 30, 40]);
    expect(overviewBounds({ holes: [], collars: [], segments: [], boxes: [] }, [])).toBeNull();
  });
});
