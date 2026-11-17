import { describe, expect, it } from "vitest";
import { displayLevel, mergeData, mergeRows } from "../src/viz/downhole/lod";
import type { DownholeData } from "../src/viz/downhole/types";

const iv = (from: number, to: number, au: number | null, lith: string | null) => ({ from, to, values: { AU: au, LITH: lith } });
const numeric = (f: string) => f === "AU";

describe("levels of detail of the displays along holes", () => {
  it("merges into spans of the step: numbers by the mean weighted by length, categories by the most length", () => {
    const rows = [iv(0, 1, 1, "A"), iv(1, 4, 5, "B"), iv(4, 5, null, "A"), iv(5, 6, 2, null)];
    const m = mergeRows(rows, 4, numeric);
    expect(m).toHaveLength(2);
    expect(m[0].from).toBe(0);
    expect(m[0].to).toBe(4);
    expect(m[0].values.AU).toBeCloseTo((1 * 1 + 5 * 3) / 4, 12);
    expect(m[0].values.LITH).toBe("B");
    // the second span: AU only where there is a value; LITH — the one category present
    expect(m[1]).toEqual({ from: 4, to: 6, values: { AU: 2, LITH: "A" } });
  });

  it("an empty value stays empty, never 0; a row longer than the step stays as it is", () => {
    const m = mergeRows([iv(0, 2, null, null), iv(2, 3, null, null), iv(3, 50, 7, "C")], 4, numeric);
    expect(m[0].values).toEqual({ AU: null, LITH: null });
    expect(m[1]).toEqual(iv(3, 50, 7, "C"));
    expect(mergeRows([iv(0, 1, 1, "A")], 0, numeric)).toEqual([iv(0, 1, 1, "A")]);
  });

  it("events and rows without depths are not merged", () => {
    const data: DownholeData = {
      tables: [
        { key: "assay", name: "Пробы", columns: [{ name: "AU", kind: "number", unit: "г/т" }], kind: "interval" },
        { key: "ev", name: "События", columns: [{ name: "K", kind: "text", unit: "" }], kind: "event" },
      ],
      holes: [{ hole_id: "A", found: true, td: 10, max_depth: 10, tables: { assay: [iv(0, 1, 1, null), iv(1, 2, 3, null)], ev: [{ from: 1, to: 1, values: { K: "x" } }, { from: 1.5, to: 1.5, values: { K: "y" } }] } }],
    };
    const m = mergeData(data, 32);
    expect(m.holes[0].tables.assay).toHaveLength(1);
    expect(m.holes[0].tables.assay[0].values.AU).toBe(2);
    expect(m.holes[0].tables.ev).toHaveLength(2);
    expect(mergeRows([{ from: null, to: 2, values: {} }, iv(0, 1, 1, "A")], 4, numeric)).toHaveLength(2);
  });

  it("the level by pixels per metre: full when a 4 m span is longer than 2 px, with hysteresis", () => {
    expect(displayLevel(10, null)).toBe(0);
    expect(displayLevel(0.3, null)).toBe(1); // 4 m → 1.2 px: merge to 4 m; 32 m → 9.6 px
    expect(displayLevel(0.01, null)).toBe(2);
    expect(displayLevel(0.48, 1)).toBe(1); // just under 0.5 px/m: within the 20 % band, stays
    expect(displayLevel(0.65, 1)).toBe(0);
  });
});

describe("which displays the 3D view draws", async () => {
  const { shownDisplays, fieldsOfSets, panelWidthOf } = await import("../src/modules/downhole/displayList");
  const { withDefaults, newTrack } = await import("../src/charts/striplog/model");
  const track = (field: string) => ({ ...newTrack("assay", { name: field, kind: "number", unit: "" }, 0), id: `t-${field}` });
  const settings = (...fields: string[]) => ({ ...withDefaults({ allHoles: true }), tracks: fields.map(track) });
  const charts = [
    { id: 1, set_id: 3, name: "AU", settings: settings("AU") },
    { id: 2, set_id: 3, name: "CU", settings: settings("CU") },
    { id: 5, set_id: 4, name: "Другой", settings: settings("AG") },
  ];

  it("saved displays switched on, a draft instead of its saved display, a new draft in addition; side by side", () => {
    const shown = shownDisplays({
      displays: [{ chart: 1, visible: true }, { chart: 2, visible: false }, { chart: 5, visible: true }],
      charts,
      drafts: [{ key: "w1", setId: 3, chartId: 1, settings: settings("AU", "CU") }, { key: "w2", setId: 3, chartId: null, settings: settings("PB") }],
      styles: [],
      hidden: [],
    });
    expect(shown.map((d) => [d.key, d.draftKey, d.name])).toEqual([["chart:1", "w1", "AU"], ["chart:5", null, "Другой"], ["draft:w2", "w2", "Новое: PB"]]);
    expect(shown[0].settings.tracks.map((t) => t.field)).toEqual(["AU", "CU"]); // the draft is drawn
    expect(shown[0].start).toEqual({ units: 0, tubes: 0 });
    expect(shown[2].start.units).toBeGreaterThan(0); // after the columns of the first display of set 3
    expect(shown[1].start).toEqual({ units: 0, tubes: 0 }); // another set starts at the hole
    expect(fieldsOfSets(shown).get(3)).toEqual({ assay: ["AU", "CU", "PB"] });
  });

  it("nothing of a set whose intervals or the set itself are hidden; nothing without holes", () => {
    const base = { displays: [{ chart: 1, visible: true }, { chart: 5, visible: true }], charts, drafts: [], styles: [] };
    expect(shownDisplays({ ...base, hidden: ["set:3:intervals"] }).map((d) => d.key)).toEqual(["chart:5"]);
    expect(shownDisplays({ ...base, hidden: ["set:4"] }).map((d) => d.key)).toEqual(["chart:1"]);
    const none = shownDisplays({ ...base, charts: [{ ...charts[0], settings: { ...settings("AU"), allHoles: false, holes: [] } }], hidden: [] });
    expect(none).toEqual([]);
    expect(panelWidthOf(Float64Array.of(100, 200, 300))).toBe(10);
    expect(panelWidthOf([])).toBe(5);
  });
});
