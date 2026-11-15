import { describe, expect, it } from "vitest";
import type { DownholeData, TrajectoryHole } from "../api";
import { DEFAULT_STRIPLOG, withDefaults, type StripLogSettings, type Track } from "../charts/stripLog";
import { autoPanelWidth, buildDownhole, pathBetween, pointAt, tubeTracks } from "./downholeGeometry";

// A vertical hole going down from z = 100, and one inclined 45 degrees.
const holes: TrajectoryHole[] = [
  { hole_id: "H1", td: 10, source: "survey", path: [[1000, 2000, 100, 0], [1000, 2000, 90, 10]] },
  { hole_id: "H2", td: 10, source: "survey", path: [[1100, 2000, 100, 0], [1100, 2000, 95, 5], [1105, 2000, 90, 10]] },
];
const data: DownholeData = {
  tables: [{ key: "5", name: "Литология", columns: [{ name: "LITH", kind: "text", unit: "" }, { name: "RQD", kind: "number", unit: "%" }] }],
  holes: [
    { hole_id: "H1", found: true, td: 10, max_depth: 10, tables: { "5": [
      { from: 0, to: 4, values: { LITH: "Туф", RQD: 40 } }, { from: 4, to: 10, values: { LITH: "Андезит", RQD: 90 } }] } },
    { hole_id: "H2", found: true, td: 10, max_depth: 10, tables: { "5": [{ from: 0, to: 6, values: { LITH: "Андезит", RQD: 20 } }] } },
    { hole_id: "H3", found: false, td: null, max_depth: 3, tables: { "5": [{ from: 0, to: 3, values: { LITH: "Туф", RQD: 1 } }] } },
  ],
};
const track = (patch: Partial<Track>): Track => ({
  id: "t", table: "5", field: "LITH", type: "color_text", width: 90, color: "#ff0000", min: "", max: "", log: false, boundaries: false, ...patch,
});
const settings = (tracks: Track[], extra: Partial<StripLogSettings> = {}): StripLogSettings => ({ ...DEFAULT_STRIPLOG, allHoles: true, tracks, ...extra });
const origin: [number, number, number] = [1000, 2000, 100];

describe("downhole geometry", () => {
  it("finds a point and the stations between two depths on a bent hole", () => {
    expect(pointAt(holes[0].path, 4)).toEqual([1000, 2000, 96]);
    expect(pointAt(holes[1].path, 7.5)).toEqual([1102.5, 2000, 92.5]);
    expect(pointAt(holes[0].path, 99)).toEqual([1000, 2000, 90]); // clamped to the bottom
    expect(pathBetween(holes[1].path, 2, 8)).toEqual([[1100, 2000, 98], [1100, 2000, 95], [1103, 2000, 92]]);
  });

  it("makes the first colour column a tube and the others panels unless told", () => {
    const a = track({ id: "a" });
    const b = track({ id: "b", field: "RQD", type: "color" });
    const c = track({ id: "c", form: "tube", type: "color" });
    expect([...tubeTracks([a, b])]).toEqual(["a"]);
    expect([...tubeTracks([a, b, c])].sort()).toEqual(["a", "c"]);
    expect([...tubeTracks([track({ id: "p", form: "panel" })])]).toEqual([]);
  });

  it("draws a colour column as a tube along each hole, relative to the origin, with a colour per category", () => {
    const b = buildDownhole(holes, data, settings([track({})]), origin);
    expect(b.holes).toBe(2); // H3 has no trajectory
    expect(b.tubes).toHaveLength(1);
    const t = b.tubes[0];
    // H1: two intervals, one segment each; H2: the first interval follows the survey station (two segments)
    expect(t.positions.length / 6).toBe(2 + 2);
    expect(t.positions.slice(0, 6)).toEqual([0, 0, 0, 0, 0, -4]);
    expect(t.colors.slice(0, 3)).not.toEqual(t.colors.slice(6, 9)); // Туф and Андезит differ
    expect(b.legend[0].items.map((i) => i[0])).toEqual(["Туф", "Андезит"]);
    expect(b.panels.amps).toHaveLength(0);
  });

  it("puts bars and lines beside the hole in stacked columns, scaled to the range of the column", () => {
    const bars = track({ id: "b", field: "RQD", type: "bars", width: 90, min: "0", max: "100" });
    const line = track({ id: "l", field: "RQD", type: "line", width: 180, min: "0", max: "100" });
    const b = buildDownhole(holes, data, settings([bars, line], { panel: 10 }), origin);
    expect(b.panelWidth).toBe(10);
    // 3 intervals -> 3 quads of 4 vertices; the first bar is 0.4 of a 10 m column
    expect(b.panels.amps).toHaveLength(12);
    expect(b.panels.index).toHaveLength(18);
    const [a0, , a2] = [b.panels.amps[0], b.panels.amps[1], b.panels.amps[2]];
    expect(a0).toBeCloseTo(1.5); // the first column starts 0.15 of a column from the hole
    expect(a2).toBeCloseTo(1.5 + 4); // RQD 40 of 0..100 on a 10 m column
    // the line's column is twice as wide and starts after the bars' column
    const lineAmps = [...b.lines.amps];
    expect(Math.max(...lineAmps)).toBeGreaterThan(1.5 + 10);
    // tangents point down the hole
    expect(b.panels.tangents.slice(0, 3)).toEqual([0, 0, -1]);
  });

  it("limits the depth window and the labels to what is long enough", () => {
    const b = buildDownhole(holes, data, settings([track({})], { from: "2", to: "5" }), origin);
    const t = b.tubes[0];
    expect(t.positions.length / 6).toBe(2 + 1); // H1: 2–4 and 4–5; H2: 2–5 across a station
    expect(b.labels.map((l) => l.text)).toContain("Туф");
    expect(b.labels.find((l) => l.text === "Туф")?.length).toBeCloseTo(2);
    expect(buildDownhole(holes, data, settings([track({})], { labels: false }), origin).labels).toHaveLength(0);
  });

  it("sizes the default panel from the holes and fills settings of older versions", () => {
    expect(autoPanelWidth(holes)).toBe(2); // 10 m holes → clamped to 2
    const long: TrajectoryHole[] = [{ hole_id: "L", td: 600, source: "survey", path: [[0, 0, 0, 0], [0, 0, -600, 600]] }];
    expect(autoPanelWidth(long)).toBe(30);
    const old = withDefaults({ holes: ["H1"], tracks: undefined as never });
    expect(old.tracks).toEqual([]);
    expect(old.tube).toBe(8);
    expect(old.labels).toBe(true);
  });
});
