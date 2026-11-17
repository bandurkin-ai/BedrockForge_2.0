import { describe, expect, it } from "vitest";
import type { DownholeData, TrajectoryHole } from "../src/viz/downhole/types";
import { DEFAULT_STRIPLOG, withDefaults, type StripLogSettings, type Track } from "../src/charts/striplog/model";
import { autoPanelWidth, cleanValue, displayFields, hexRgb, labelText, passesFilter, pathBetween, pointAt, stackOf, tubeTracks } from "../src/viz/downhole/geometry";
import { buildDownhole, mergeSpans } from "../src/viz/downhole/build";
import { normalizeCollarStyle } from "../src/viz/holes/collarStyle";

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
    expect(pointAt(holes[0].path, 15)).toEqual([1000, 2000, 85]); // below the last point: straight on
    expect(pointAt(holes[0].path, -3)).toEqual([1000, 2000, 100]); // above the collar: the collar
    expect(pointAt([[5, 5, 50, 0]], 12)).toEqual([5, 5, 38]); // a hole without a depth: straight down
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

  it("draws intervals of a hole that has no depth in the collar table (a path of one point)", () => {
    const noTd: TrajectoryHole[] = [{ hole_id: "H1", td: 0, source: "vertical", path: [[1000, 2000, 100, 0]] }];
    const b = buildDownhole(noTd, data, settings([track({})]), origin);
    expect(b.holes).toBe(1);
    expect(b.tubes[0].positions.slice(0, 6)).toEqual([0, 0, 0, 0, 0, -4]);
  });

  it("matches hole names without regard to case and outer spaces", () => {
    const other: TrajectoryHole[] = [{ ...holes[0], hole_id: " h1 " }];
    expect(buildDownhole(other, data, settings([track({})]), origin).holes).toBe(1);
  });

  it("fills tracks saved by an older version, so building does not fail", () => {
    const old = withDefaults({ allHoles: true, tracks: [{ table: "5", field: "RQD" } as never] });
    expect(old.tracks[0]).toMatchObject({ type: "bars", width: 90, min: "", max: "", log: false });
    expect(buildDownhole(holes, data, old, origin).panels.amps.length).toBeGreaterThan(0);
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

  it("colours bars by grade classes and lists the classes in the legend", () => {
    const classes = [{ from: null, color: "#111111" }, { from: 50, color: "#ff0000" }];
    const bars = track({ id: "b", field: "RQD", type: "bars", min: "0", max: "100", colorBy: "classes", classes });
    const b = buildDownhole(holes, data, settings([bars], { panel: 10 }), origin);
    // H1: RQD 40 (below 50) then 90 (from 50): the first quad dark, the second red
    expect(b.panels.colors.slice(0, 3)).toEqual(hexRgb("#111111"));
    expect(b.panels.colors.slice(16, 19)).toEqual(hexRgb("#ff0000"));
    expect(b.legend[0].counts).toEqual([2, 1]);
    expect(b.legend[0].items).toEqual([["< 50", "#111111"], ["≥ 50", "#ff0000"]]);
  });

  it("uses the colours of rocks set by hand", () => {
    const b = buildDownhole(holes, data, settings([track({ catColors: { Туф: "#00ff00" } })]), origin);
    expect(b.tubes[0].colors.slice(0, 3)).toEqual(hexRgb("#00ff00"));
    expect(b.legend[0].items[0]).toEqual(["Туф", "#00ff00"]);
  });

  it("puts events as markers with labels at their depth", () => {
    const events: DownholeData = {
      tables: [{ key: "9", name: "События", kind: "event", columns: [{ name: "TYPE", kind: "text", unit: "" }] }],
      holes: [{ hole_id: "H1", found: true, td: 10, max_depth: 10, tables: { "9": [
        { from: 4, to: 4, values: { TYPE: "Вода" } }, { from: 7, to: 7, values: { TYPE: "Разлом" } }] } }],
    };
    const ev = track({ id: "e", table: "9", field: "TYPE", type: "event", marker: "star", size: 14 });
    const b = buildDownhole(holes, events, settings([ev]), origin);
    expect(b.markers).toHaveLength(1);
    expect(b.markers[0]).toMatchObject({ shape: "star", size: 14, positions: [0, 0, -4, 0, 0, -7] });
    expect(b.markers[0].colors.slice(0, 3)).not.toEqual(b.markers[0].colors.slice(3, 6)); // a colour per kind of event
    expect(b.labels.map((l) => [l.text, l.px])).toEqual([["Вода", 11], ["Разлом", 11]]);
    expect(b.panels.amps).toHaveLength(0);
    // the depth window applies to events too
    expect(buildDownhole(holes, events, settings([ev], { from: "5" }), origin).markers[0].positions).toEqual([0, 0, -7]);
  });

  it("starts a display after the columns and tubes of the displays before it", () => {
    const bars = track({ id: "b", field: "RQD", type: "bars", width: 90, min: "0", max: "100" });
    expect(stackOf(settings([bars]))).toMatchObject({ units: 1.08, tubes: 0, leftUnits: 0 });
    expect(stackOf(settings([track({})]))).toMatchObject({ units: 0, tubes: 1 });
    const second = buildDownhole(holes, data, settings([bars], { panel: 10 }), origin, { units: 1.08, tubes: 1 });
    expect(second.panels.amps[0]).toBeCloseTo(10 * (0.15 + 1.08));
    const tube = buildDownhole(holes, data, settings([track({})], { tube: 8 }), origin, { units: 0, tubes: 1 });
    expect(tube.tubes[0].width).toBe(5.5); // thinner than the tube under it
  });

  it("keeps a valid collar style from stored state", () => {
    const st = normalizeCollarStyle({ shape: "star", color: "#12ab34", size: 400, label: true, fontFamily: "comic", fontSize: 2 });
    expect(st).toMatchObject({ shape: "star", color: "#12ab34", size: 40, label: true, fontFamily: "sans-serif", fontSize: 8 });
    expect(normalizeCollarStyle(null)).toMatchObject({ shape: "circle", color: "audit", label: false });
  });

  describe("layers", () => {
    const bars = (patch: Partial<Track> = {}) => track({ id: "b", field: "RQD", type: "bars", min: "0", max: "100", ...patch });
    const amps = (t: Track, extra: Partial<StripLogSettings> = {}) => buildDownhole(holes, data, settings([t], { panel: 10, ...extra }), origin).panels.amps;

    it("draws on the left, on both sides, with a width and an offset in metres", () => {
      expect(amps(bars()).slice(0, 4)).toEqual([1.5, 1.5, 5.5, 5.5]); // RQD 40 of 100 on a 10 m column
      expect(amps(bars({ side: "left" })).slice(0, 4)).toEqual([-1.5, -1.5, -5.5, -5.5]);
      const both = amps(bars({ side: "both" }));
      expect(both.slice(0, 8)).toEqual([1.5, 1.5, 5.5, 5.5, -1.5, -1.5, -5.5, -5.5]);
      expect(amps(bars({ widthM: 20 })).slice(0, 4)).toEqual([1.5, 1.5, 9.5, 9.5]);
      expect(amps(bars({ offsetM: 7 })).slice(0, 4)).toEqual([7, 7, 11, 11]);
    });

    it("stacks each side on its own and leaves hidden layers out", () => {
      const left = bars({ id: "l", side: "left" });
      const right = bars({ id: "r" });
      const b = buildDownhole(holes, data, settings([left, right, bars({ id: "h", visible: false })], { panel: 10 }), origin);
      expect(b.panels.amps[0]).toBe(-1.5);
      expect(b.panels.amps[12]).toBe(1.5); // H1 has two intervals of the left layer, then the right one starts at the hole too
      expect(b.legend.map((l) => l.trackId)).toEqual(["l", "r"]);
      expect(stackOf(settings([left, right]))).toMatchObject({ units: 1.08, leftUnits: 1.08 });
      expect(stackOf(settings([bars({ widthM: 12 })]))).toMatchObject({ units: 0.08, m: 12 });
    });

    it("draws the base line of a column along the hole, not its outer edge", () => {
      const lines = buildDownhole(holes, data, settings([bars()], { panel: 10 }), origin).lines.amps;
      expect(lines.filter((a) => a === 1.5).length).toBeGreaterThan(0); // the base line at the hole side
      expect(lines.filter((a) => a === 11.5)).toHaveLength(0); // no line over the graph
    });

    it("runs the base line along a bent hole and only where the column has values", () => {
      // H2 bends at 5 m; RQD only from 0 to 6 m there, and a row without a value from 6 to 10 m.
      const bent: DownholeData = { ...data, holes: [{ hole_id: "H2", found: true, td: 10, max_depth: 10, tables: { "5": [
        { from: 0, to: 6, values: { RQD: 20 } }, { from: 6, to: 10, values: { RQD: null } }] } }] };
      const b = buildDownhole([holes[1]], bent, settings([bars()], { panel: 10 }), origin);
      const base: number[][] = [];
      for (let i = 0; i < b.lines.amps.length; i++) if (b.lines.amps[i] === 1.5) base.push(b.lines.positions.slice(i * 3, i * 3 + 3));
      // Two pieces through the survey station at 5 m (not one straight line from the collar), ending at 6 m.
      expect(base).toEqual([[100, 0, 0], [100, 0, -5], [100, 0, -5], [101, 0, -6]]);
    });

    it("joins touching spans of values", () => {
      expect(mergeSpans([[4, 6], [0, 2], [2, 3], [5, 8], [10, 12]])).toEqual([[0, 3], [4, 8], [10, 12]]);
    });

    it("writes the opacity into the colours of panels", () => {
      const b = buildDownhole(holes, data, settings([bars({ opacity: 0.4 })], { panel: 10 }), origin);
      expect(b.panels.colors[3]).toBeCloseTo(0.4);
      expect(buildDownhole(holes, data, settings([bars()], { panel: 10 }), origin).panels.colors[3]).toBe(1);
    });

    it("clips values above the maximum, marks them or draws them as they are", () => {
      const over = bars({ max: "50" }); // RQD 90 is above
      expect(amps(over).slice(4, 8)).toEqual([1.5, 1.5, 11.5, 11.5]);
      expect(amps(bars({ max: "50", over: "asis" })).slice(4, 8)).toEqual([1.5, 1.5, 19.5, 19.5]);
      const lines = (t: Track) => buildDownhole(holes, data, settings([t], { panel: 10 }), origin).lines.amps;
      expect(lines(bars({ max: "50", over: "mark" })).filter((a) => Math.abs(a - 12.1) < 1e-9)).toHaveLength(2);
      expect(lines(over).filter((a) => Math.abs(a - 12.1) < 1e-9)).toHaveLength(0);
    });

    it("hides intervals below the cut-off, of a hidden class and of a hidden rock", () => {
      const cut = buildDownhole(holes, data, settings([bars({ filter: { mode: "min", min: 30, empty: "skip", nonPositive: "asis" } })], { panel: 10 }), origin);
      expect(cut.intervals).toBe(2); // 40 and 90 stay, 20 goes
      const classes = [{ from: null, color: "#111111", hidden: true }, { from: 50, color: "#ff0000" }];
      const hidden = buildDownhole(holes, data, settings([bars({ colorBy: "classes", classes })], { panel: 10 }), origin);
      expect(hidden.intervals).toBe(1);
      expect(hidden.panels.colors.slice(0, 3)).toEqual(hexRgb("#ff0000"));
      const rocks = buildDownhole(holes, data, settings([track({ hidden: ["Туф"] })]), origin);
      expect(rocks.tubes[0].positions.length / 6).toBe(3); // only Андезит
    });

    it("handles empty codes and non-positive values before drawing", () => {
      const f = (patch: Partial<NonNullable<Track["filter"]>>) => bars({ filter: { mode: "all", empty: "skip", nonPositive: "asis", ...patch } });
      expect(cleanValue(f({ emptyBelow: -900 }), -999)).toBeNull();
      expect(cleanValue(f({ nonPositive: "empty" }), 0)).toBeNull();
      expect(cleanValue(f({ nonPositive: { replace: 0.005 } }), -1)).toBe(0.005);
      expect(cleanValue(f({}), -1)).toBe(-1);
      expect(cleanValue(bars(), "x")).toBeNull();
      expect(passesFilter(f({ mode: "range", min: 1, max: 5 }), 6)).toBe(false);
      expect(passesFilter(f({ mode: "range", min: 1, max: 5 }), 5)).toBe(true);
      expect(passesFilter(f({ mode: "min", min: 1 }), null)).toBe(false);
    });

    it("splits values below and above a threshold to the two sides", () => {
      const a = amps(bars({ split: 50 })).map((x) => Number(x.toFixed(6)));
      expect(a.slice(0, 4)).toEqual([-1.5, -1.5, -3.5, -3.5]); // 40: a fifth of the way down from 50
      expect(a.slice(4, 8)).toEqual([1.5, 1.5, 9.5, 9.5]); // 90: four fifths of the way up
    });

    it("draws steps, a line through the middles of intervals and points", () => {
      const steps = buildDownhole(holes, data, settings([bars({ type: "steps" })], { panel: 10 }), origin);
      expect(steps.panels.index.length / 6).toBe(3);
      expect(steps.panels.colors[3]).toBeCloseTo(0.55); // the fill is lighter than the outline
      expect(buildDownhole(holes, data, settings([bars({ type: "steps", fill: false })], { panel: 10 }), origin).panels.amps).toHaveLength(0);
      const line = buildDownhole(holes, data, settings([bars({ type: "line", dots: true })], { panel: 10 }), origin);
      expect(line.points[0].amps).toEqual([5.5, 10.5, 3.5]);
      expect(line.points[0].positions.slice(0, 6)).toEqual([0, 0, -2, 0, 0, -7]); // middles of 0–4 and 4–10
      // one segment between the two intervals of H1, from one middle to the next
      const i = line.lines.amps.findIndex((a, k) => a === 5.5 && line.lines.amps[k + 1] === 10.5);
      expect(i).toBeGreaterThanOrEqual(0);
      const pts = buildDownhole(holes, data, settings([bars({ type: "points", pointSize: 9 })], { panel: 10 }), origin);
      expect(pts.points[0]).toMatchObject({ size: 9, amps: [5.5, 10.5, 3.5] });
      expect(pts.panels.amps).toHaveLength(0);
    });

    it("sizes a tube by the value in a few thicknesses", () => {
      const t = track({ id: "r", field: "RQD", type: "color", form: "tube", min: "0", max: "100", radius: { by: "value", min: 2, max: 12, log: false } });
      const b = buildDownhole(holes, data, settings([t]), origin);
      expect(b.tubes.map((x) => x.width)).toEqual([4, 6, 12]); // 20, 40 and 90 of 100
      const fixed = buildDownhole(holes, data, settings([{ ...t, radius: { by: "fixed", min: 2, max: 9, log: false } }]), origin);
      expect(fixed.tubes.map((x) => x.width)).toEqual([9]);
    });

    it("writes labels by a template, only for high values, the highest first when over the limit", () => {
      const label = { on: true, template: "{rqd} % · {from}–{to}", decimals: 1, size: 11, color: "class", place: "outside" as const };
      const b = buildDownhole(holes, data, settings([bars({ label })], { panel: 10 }), origin);
      expect(b.labels.map((l) => l.text)).toEqual(["40,0 % · 0–4", "90,0 % · 4–10", "20,0 % · 0–6"]);
      expect(b.labels[0]).toMatchObject({ size: 11, color: "#ff0000" });
      expect(b.labels[0].amp).toBeCloseTo(5.9);
      const high = buildDownhole(holes, data, settings([bars({ label: { ...label, min: 30 } })], { panel: 10 }), origin);
      expect(high.labels).toHaveLength(2);
      const top = buildDownhole(holes, data, settings([bars({ label: { ...label, limit: 1, color: "#00ff00" } })], { panel: 10 }), origin);
      expect(top.labels.map((l) => [l.text.slice(0, 4), l.color])).toEqual([["90,0", "#00ff00"]]);
      const left = buildDownhole(holes, data, settings([bars({ side: "left", label })], { panel: 10 }), origin);
      expect(left.labels[0]).toMatchObject({ right: true });
      expect(labelText("{len} м", { from: 1, to: 3.5, values: {} }, null)).toBe("2,5 м");
    });

    it("colours by another field and asks the server for every field it needs", () => {
      const by = bars({ colorBy: "field", colorField: "LITH", label: { on: true, template: "{RQD} {lith}", decimals: null, size: 11, color: "class", place: "outside" } });
      const b = buildDownhole(holes, data, settings([by], { panel: 10 }), origin);
      expect(b.panels.colors.slice(0, 3)).not.toEqual(b.panels.colors.slice(16, 19)); // Туф and Андезит
      expect(b.panels.colors.slice(16, 19)).toEqual(b.panels.colors.slice(32, 35)); // both Андезит
      expect(displayFields(settings([by]))).toEqual({ "5": ["RQD", "LITH"] });
    });

    it("keeps the scale colours of older displays and takes other gradients", () => {
      const t = track({ id: "r", field: "RQD", type: "color", form: "panel", min: "0", max: "100" });
      const old = buildDownhole(holes, data, settings([t], { panel: 10 }), origin).panels.colors.slice(0, 3);
      const viridis = buildDownhole(holes, data, settings([{ ...t, gradient: { name: "viridis", reverse: false, stretch: "linear", min: "", max: "" } }], { panel: 10 }), origin).panels.colors.slice(0, 3);
      expect(old).not.toEqual(viridis);
      expect(old[2]).toBeGreaterThan(0.3); // 40 of 100 on blue → red: still on the green-blue side
    });

    it("builds fifty thousand intervals fast enough to follow an edit", () => {
      const rows = Array.from({ length: 500 }, (_, i) => ({ from: i, to: i + 1, values: { LITH: "A", RQD: (i * 37) % 100 } }));
      const many: TrajectoryHole[] = Array.from({ length: 100 }, (_, i) => ({ hole_id: `M${i}`, td: 500, source: "survey", path: [[i * 10, 0, 0, 0], [i * 10, 0, -500, 500]] }));
      const big: DownholeData = { tables: data.tables, holes: many.map((h) => ({ hole_id: h.hole_id, found: true, td: 500, max_depth: 500, tables: { "5": rows } })) };
      const classes = [{ from: null, color: "#111111" }, { from: 30, color: "#00ff00" }, { from: 60, color: "#ff0000" }];
      buildDownhole(many, big, settings([bars({ colorBy: "classes", classes })]), [0, 0, 0]); // warm-up
      const t0 = performance.now();
      const b = buildDownhole(many, big, settings([bars({ colorBy: "classes", classes })]), [0, 0, 0]);
      const ms = performance.now() - t0;
      expect(b.intervals).toBe(50000);
      expect(ms).toBeLessThan(400); // the target is 100 ms; the margin is for slow test machines
    });
  });
});
