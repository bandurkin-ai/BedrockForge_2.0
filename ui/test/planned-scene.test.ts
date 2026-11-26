// The planned holes in the main 3D and in the sections (Э19б, 19.39; ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29, № 103): the geometry from an integer origin
// (ТЗ 5.4: float32 only for the picture), a gap in a path breaks the line (never drawn to 0), the layer (one `LineSegments2` of the holes, a thicker one of
// the chosen hole, the targets, the collars) with the clipping planes of the scene — so the section window cuts it as the rest and the holes are seen in
// the sections —, the layer that follows the stores (the holes switched off, the hole chosen, a lost context), the measure of a redraw of 100 holes.
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Scene } from "../src/viz/Scene";
import { buildPlanned, type PlannedDraw } from "../src/viz/planned/build";
import { PlannedLayer } from "../src/viz/planned/layer";
import { maxRoundTripError } from "../src/viz/origin";
import { setCurrentScene } from "../src/modules/scene/sceneHost";
import { readRows, type PlannedRow } from "../src/modules/drillholes/plannedApi";
import { drawsOf, PLANNED_KEY, PlannedScene } from "../src/modules/drillholes/plannedScene";
import { choose, plannedStore, plannedViewStore, setShown } from "../src/modules/drillholes/plannedState";
import { AIM, FIRST, installCore, newApp, teardown, waitFor } from "./plannedHarness";

const rows = (): PlannedRow[] => readRows({ planned_holes: [FIRST, AIM] });

/** A straight path of `n` stations every metre from `from` along `dir`, absolute float64. */
function path(from: [number, number, number], dir: [number, number, number], n: number): PlannedDraw["x"][] {
  const out = [new Float64Array(n), new Float64Array(n), new Float64Array(n)];
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) out[k][i] = from[k] + dir[k] * i;
  return out;
}

const draw = (id: number, from: [number, number, number], n = 101, target: PlannedDraw["target"] = null): PlannedDraw => {
  const [x, y, z] = path(from, [0.6, 0.2, -0.77], n);
  return { id, x, y, z, target };
};

describe("the geometry (from an integer origin)", () => {
  it("the real holes: the origin is whole metres at the centre of the box, the numbers are small, the segments are of the paths", () => {
    const b = buildPlanned(drawsOf(rows(), []), null)!;
    expect(Number.isInteger(b.origin.x) && Number.isInteger(b.origin.y) && Number.isInteger(b.origin.z)).toBe(true);
    const [lo, hi] = [b.bbox!.slice(0, 3), b.bbox!.slice(3)];
    expect(b.origin.x).toBe(Math.round((lo[0] + hi[0]) / 2));
    expect(b.origin.z).toBe(Math.round((lo[2] + hi[2]) / 2));
    const n = FIRST.trajectory.depth.length - 1 + (AIM.trajectory.depth.length - 1);
    expect(b.segments).toBe(n);
    expect(b.lines.length).toBe(n * 6);
    expect(b.chosen.length).toBe(0);
    expect(b.holes).toBe(2);
    expect(b.collars.length / 3).toBe(2);
    expect(b.targets.length / 3).toBe(1); // only the second hole has a target
    expect(Math.max(...b.lines.map(Math.abs))).toBeLessThan(150); // metres from the centre, not from the datum
  });

  it("at the coordinates of a real project (millions of metres) the picture is exact to a fraction of a millimetre", () => {
    const base = 6_231_456.375;
    const d = draw(1, [base, 5_512_345.625, 1_230.5], 251);
    const b = buildPlanned([d], null)!;
    const flat = new Float64Array(251 * 3);
    for (let i = 0; i < 251; i++) flat.set([d.x[i], d.y[i], d.z[i]], i * 3);
    // the segments hold every station twice but the ends: compare the starts of the segments with the stations
    const starts = new Float32Array(250 * 3);
    for (let i = 0; i < 250; i++) starts.set(b.lines.subarray(i * 6, i * 6 + 3), i * 3);
    expect(maxRoundTripError(flat.subarray(0, 250 * 3), starts, b.origin)).toBeLessThan(1e-3);
    expect(b.origin.x).toBe(Math.round(base + 75));
  });

  it("the hole chosen goes to its own buffer; the targets and the collars are points", () => {
    const b = buildPlanned([draw(1, [0, 0, 0], 11, [5, 5, -5]), draw(2, [100, 0, 0], 21), draw(3, [0, 100, 0], 6, [1, 2, 3])], 2)!;
    expect(b.chosen.length / 6).toBe(20);
    expect(b.lines.length / 6).toBe(10 + 5);
    expect(b.targets.length / 3).toBe(2);
    expect(b.collars.length / 3).toBe(3);
    expect(b.segments).toBe(35);
  });

  it("a gap in a path breaks the line: the segments with it are left out, never drawn to 0", () => {
    const d = draw(1, [10, 10, 10], 6);
    (d.y as Float64Array)[3] = Number.NaN;
    const b = buildPlanned([d], null)!;
    expect(b.segments).toBe(3); // 5 segments, the two that touch the station 3 are gone
    expect(b.skipped).toBe(2);
    expect(Array.from(b.lines).every(Number.isFinite)).toBe(true);
    expect(b.bbox).toEqual([10, 10, 10 - 0.77 * 5, 10 + 0.6 * 5, 10 + 0.2 * 5, 10]); // the box is of what is drawn
    expect(buildPlanned([], null)).toBeNull();
    const nothing = draw(2, [0, 0, 0], 3);
    nothing.x = Float64Array.of(NaN, NaN, NaN);
    nothing.y = Float64Array.of(NaN, NaN, NaN);
    expect(buildPlanned([nothing], null)).toBeNull();
  });

  it("the holes the view switched off are not drawn; the others keep their place", () => {
    const all = drawsOf(rows(), []);
    const off = drawsOf(rows(), [FIRST.id]);
    expect(all.map((d) => d.id)).toEqual([FIRST.id, AIM.id]);
    expect(off.map((d) => d.id)).toEqual([AIM.id]);
    expect(drawsOf(readRows({ planned_holes: [{ ...FIRST, trajectory: undefined }] }), [])).toEqual([]); // no path yet: nothing to draw
  });
});

describe("the layer", () => {
  const ctx = () => ({ clip: [new THREE.Plane(new THREE.Vector3(1, 0, 0), 0)], resolution: new THREE.Vector2(800, 600), pixelRatio: 1 });

  it("the lines, the chosen hole, the collars and the targets; Copper for the holes, Cobalt for the targets (the brand)", () => {
    const c = ctx();
    const layer = new PlannedLayer(buildPlanned(drawsOf(rows(), []), AIM.id)!, c);
    expect(layer.object.children).toHaveLength(4);
    const s = layer.stats();
    expect(s).toMatchObject({ holes: 2, chosenSegments: AIM.trajectory.depth.length - 1, targets: 1, collars: 2, skipped: 0 });
    expect(layer.origin).toEqual(s.origin);
    expect(layer.bbox!.length).toBe(6);
    const widths = layer.object.children.filter((o) => (o as THREE.Object3D & { isLineSegments2?: boolean }).isLineSegments2).map((o) => ((o as THREE.Mesh).material as unknown as { linewidth: number }).linewidth);
    expect(widths).toEqual([2.5, 4.5]); // the chosen one is thicker
    const colours = layer.object.children.map((o) => `#${((o as THREE.Mesh).material as THREE.Material & { color: THREE.Color }).color.getHexString()}`);
    expect(colours).toContain("#f46a1a");
    expect(colours).toContain("#1f6bff");
    layer.dispose();
    expect(layer.object.children).toHaveLength(0);
  });

  it("every material takes the clipping planes of the scene: the section window cuts the holes and they are seen in the sections", () => {
    const c = ctx();
    const layer = new PlannedLayer(buildPlanned(drawsOf(rows(), []), FIRST.id)!, c);
    const planes = layer.object.children.map((o) => ((o as THREE.Mesh).material as THREE.Material).clippingPlanes);
    expect(planes).toHaveLength(4);
    for (const p of planes) expect(p).toBe(c.clip); // the same array as the scene's: a moved window is seen at once
    layer.dispose();
  });

  it("only what there is is made: no chosen hole, no targets — no object for them", () => {
    const layer = new PlannedLayer(buildPlanned([draw(1, [0, 0, 0])], null)!, ctx());
    expect(layer.object.children).toHaveLength(2); // the lines and the collar
    layer.dispose();
  });
});

describe("the layer follows the stores", () => {
  let layers: Map<string, { stats(): ReturnType<PlannedLayer["stats"]> }>;
  let restored: (() => void)[];
  let removed: string[];
  let fake: Scene;
  beforeEach(() => {
    installCore();
    layers = new Map();
    restored = [];
    removed = [];
    fake = {
      clipPlanes: [],
      resolution: new THREE.Vector2(1, 1),
      addLayer: (k: string, l: unknown) => void layers.set(k, l as never),
      removeLayer: (k: string) => void (layers.delete(k), removed.push(k)),
      invalidate: () => undefined,
      onRestored: (fn: () => void) => (restored.push(fn), () => void restored.splice(restored.indexOf(fn), 1)),
      setHandles: () => undefined,
    } as unknown as Scene;
    plannedStore.set({ rows: [], loaded: true, error: null });
    plannedViewStore.set({ hidden: [], chosen: null });
  });
  afterEach(() => {
    setCurrentScene(null);
    plannedStore.set({ rows: [], loaded: false, error: null });
    teardown();
  });

  it("no holes — no layer; the holes of the list come; a hole switched off goes; the hole chosen is thicker; a lost context builds it again", () => {
    const s = new PlannedScene(fake);
    expect(layers.size).toBe(0);
    plannedStore.set({ rows: rows() });
    expect(layers.has(PLANNED_KEY)).toBe(true);
    expect(layers.get(PLANNED_KEY)!.stats()).toMatchObject({ holes: 2, chosenSegments: 0 });
    choose(FIRST.id);
    expect(layers.get(PLANNED_KEY)!.stats().chosenSegments).toBe(FIRST.trajectory.depth.length - 1);
    setShown(FIRST.id, false);
    expect(layers.get(PLANNED_KEY)!.stats()).toMatchObject({ holes: 1, chosenSegments: 0 }); // the chosen hole is off: nothing is thick
    setShown(FIRST.id, true);
    expect(layers.get(PLANNED_KEY)!.stats().holes).toBe(2);
    layers.clear(); // the scene dropped every layer with its context
    restored.forEach((fn) => fn());
    expect(layers.has(PLANNED_KEY)).toBe(true);
    plannedStore.set({ rows: [] });
    expect(layers.size).toBe(0);
    expect(s.stats()).toMatchObject({ holes: 0, segments: 0 });
    s.dispose();
    expect(restored).toHaveLength(0); // the subscriptions are gone
  });

  it("a redraw with nothing changed does nothing", () => {
    const s = new PlannedScene(fake);
    plannedStore.set({ rows: rows() });
    const before = removed.length;
    choose(null); // the same view: not a redraw
    plannedViewStore.set({ hidden: plannedViewStore.get().hidden });
    expect(removed.length).toBe(before);
    s.dispose();
  });

  it("the layer is attached to the scene of the 3D window by the bind and goes with it", async () => {
    const app = newApp();
    await waitFor(() => plannedStore.get().loaded && plannedStore.get().rows.length === 2);
    setCurrentScene(fake);
    await waitFor(() => layers.has(PLANNED_KEY));
    expect(layers.get(PLANNED_KEY)!.stats().holes).toBe(2);
    setCurrentScene(null);
    await waitFor(() => !layers.has(PLANNED_KEY));
    void app;
  });

  it("measure: 100 planned holes of 250 m are drawn again in milliseconds", () => {
    const many: PlannedRow[] = [];
    const base = readRows({ planned_holes: [FIRST] })[0];
    for (let i = 0; i < 100; i++) {
      const n = 251;
      const [x, y, z] = path([6_231_000 + (i % 10) * 25, 5_512_000 + Math.floor(i / 10) * 25, 1_200], [Math.cos(i), Math.sin(i), -0.9], n);
      many.push({ ...base, id: 1000 + i, name: `ПС-${i}`, meta: { ...base.meta, target: i % 2 ? [x[n - 1], y[n - 1], z[n - 1]] : null }, trajectory: { depth: Float64Array.from({ length: n }, (_, k) => k), x: x as Float64Array, y: y as Float64Array, z: z as Float64Array } });
    }
    plannedStore.set({ rows: many });
    const s = new PlannedScene(fake);
    expect(layers.get(PLANNED_KEY)!.stats()).toMatchObject({ holes: 100, segments: 100 * 250, targets: 50, collars: 100 });
    const times: number[] = [];
    for (let k = 0; k < 7; k++) {
      choose(1000 + k); // the chosen hole changes: the whole layer is built again
      times.push(s.redrawMs!);
    }
    times.sort((a, b) => a - b);
    const median = times[Math.floor(times.length / 2)];
    // eslint-disable-next-line no-console
    console.log(`planned layer: 100 holes × 250 segments, redraw median ${median.toFixed(1)} ms (min ${times[0].toFixed(1)}, max ${times[times.length - 1].toFixed(1)})`);
    expect(median).toBeLessThan(250); // a generous bound for a loaded runner; the real number is in the log
    s.dispose();
  });
});
