import { describe, expect, it } from "vitest";
import type { DownholeData } from "../api";
import { along, categoryColors, defaultTracks, depthTicks, fieldRange, stripLogName, tableDisplay, classColor, classLabel, defaultClasses, displayName, sortClasses, DEFAULT_STRIPLOG } from "./stripLog";

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

  it("makes the display of a table on every hole: one tube with text, further text as ribbons, two numbers as bars", () => {
    const cols = [
      { name: "LITH", kind: "text" as const, unit: "" }, { name: "WEATH", kind: "text" as const, unit: "" },
      { name: "RQD", kind: "number" as const, unit: "%" }, { name: "A", kind: "number" as const, unit: "" }, { name: "B", kind: "number" as const, unit: "" },
    ];
    const d = tableDisplay({ key: "5", id: 5, name: "Литология", builtin: false, row_count: 3, columns: cols });
    expect(d.allHoles).toBe(true);
    expect(d.tracks.map((t) => [t.field, t.type])).toEqual([["LITH", "color_text"], ["WEATH", "color"], ["RQD", "bars"], ["A", "bars"]]);
  });

  it("ends the linear scale at the 98th percentile when there is enough data, so one outlier does not flatten the bars", () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({ from: i, to: i + 1, values: { AU: i === 99 ? 5000 : i / 10 } }));
    const many: DownholeData = { tables: [], holes: [{ hole_id: "H", found: true, td: 100, max_depth: 100, tables: { assay: rows } }] };
    const r = fieldRange(many, "assay", "AU", false)!;
    expect(r[0]).toBe(0);
    expect(r[1]).toBeLessThan(10);
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

  it("colours by grade classes: below the first bound, between bounds, from the last", () => {
    const classes = [{ from: null, color: "#a" }, { from: 0.3, color: "#b" }, { from: 1, color: "#c" }];
    expect([0, 0.29, 0.3, 0.99, 1, 50].map((v) => classColor(classes, v))).toEqual(["#a", "#a", "#b", "#b", "#c", "#c"]);
    expect(classColor([{ from: 0.5, color: "#b" }], 0.1)).toBeNull(); // below every class: no colour
    expect(classColor(classes, "x")).toBeNull();
    expect(classes.map((_, i) => classLabel(classes, i))).toEqual(["< 0.3", "0.3 – 1", "≥ 1"]);
    expect(sortClasses([{ from: 2, color: "#c" }, { from: null, color: "#a" }, { from: 1, color: "#b" }, { from: 2, color: "#d" }]).map((c) => c.from)).toEqual([null, 1, 2]);
    const auto = defaultClasses([0, 10], false);
    expect(auto.map((c) => c.from)).toEqual([null, 2, 4, 6, 8]);
    expect(defaultClasses([0.01, 10], true).length).toBeGreaterThan(2);
  });

  it("names a display by its field and kind", () => {
    const t = { id: "t", table: "assay", field: "AU", type: "bars" as const, width: 90, color: "#000000", min: "", max: "", log: true, boundaries: false };
    expect(displayName({ ...DEFAULT_STRIPLOG, form: "grade", tracks: [t] }, "Пробы")).toBe("AU · столбцы");
    expect(displayName({ ...DEFAULT_STRIPLOG, form: "lith", tracks: [{ ...t, field: "LITH", type: "color_text" }] }, "Литология")).toBe("LITH · трубка (Литология)");
    const cu = { ...t, id: "c", field: "CU", type: "color" as const, form: "panel" as const };
    expect(displayName({ ...DEFAULT_STRIPLOG, form: "grade", tracks: [t, cu, { ...t, id: "h", visible: false }] })).toBe("AU · столбцы + CU · лента");
  });
});
