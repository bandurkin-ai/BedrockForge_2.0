import { describe, expect, it } from "vitest";
import type { DownholeData } from "../api";
import { along, categoryColors, defaultTracks, depthTicks, fieldRange, stripLogName } from "./stripLog";

const data: DownholeData = {
  tables: [{ key: "5", name: "Литология", columns: [{ name: "LITH", kind: "text", unit: "" }, { name: "RQD", kind: "number", unit: "%" }] }],
  holes: [
    { hole_id: "H1", found: true, td: 10, max_depth: 10, tables: { "5": [
      { from: 0, to: 4, values: { LITH: "Туф", RQD: 40 } }, { from: 4, to: 10, values: { LITH: "Андезит", RQD: 90 } }] } },
    { hole_id: "H2", found: true, td: 6, max_depth: 6, tables: { "5": [{ from: 0, to: 6, values: { LITH: "Андезит", RQD: 0.5 } }] } },
  ],
};

describe("downhole log", () => {
  it("builds default columns: text first, then numbers", () => {
    const tracks = defaultTracks({ key: "5", id: 5, name: "Литология", builtin: false, row_count: 3, columns: data.tables[0].columns });
    expect(tracks.map((t) => [t.field, t.type])).toEqual([["LITH", "color_text"], ["RQD", "bars"]]);
  });

  it("colours categories over every hole, the same colour for the same rock", () => {
    const colors = categoryColors(data, "5", "LITH");
    expect([...colors.keys()]).toEqual(["Туф", "Андезит"]);
    expect(colors.get("Туф")).not.toBe(colors.get("Андезит"));
  });

  it("finds ranges from zero (linear) or positive values (log) and places values", () => {
    expect(fieldRange(data, "5", "RQD", false)).toEqual([0, 90]);
    expect(fieldRange(data, "5", "RQD", true)).toEqual([0.5, 90]);
    expect(along(45, 0, 90, false)).toBe(0.5);
    expect(along(10, 1, 100, true)).toBeCloseTo(0.5);
  });

  it("ticks depth at round steps and names the log", () => {
    expect(depthTicks(0, 100, 3)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(depthTicks(12, 30, 30)).toEqual([12, 14, 16, 18, 20, 22, 24, 26, 28, 30]);
    expect(stripLogName({ holes: ["H1", "H2", "H3", "H4"], tracks: [], from: "", to: "", scale: 0, grid: true })).toBe("Колонка H1, H2 и ещё 2");
  });
});
