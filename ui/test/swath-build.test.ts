// Swath plot in the main 3D (ТЗ 7.30 «3D»; ADR 0019 §6; перенос `buildSwath` BF): the cuboid cut into swaths (the
// outline and boundaries of the swaths shown, the others dimmed), the plot over it (lines broken at an empty
// swath, bars, names), the slab of the swath under the cursor (Т7.30-3), the plot turned to the viewer — in plan
// beside the cuboid, on a section over it, for Z to the side — by a matrix only (no new geometry), the extent
// «Вписать» takes (the plot turned any way), the float32 buffers from the centre at a small offset from the
// integer origin; the source `swath:<id>` of the scene follows the view key and the window.
import * as THREE from "three";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { niceTicks } from "../src/charts/column/scale";
import { formatNumber } from "../src/charts/core/axisScale";
import type { SwathResult } from "../src/modules/swath/api";
import { defaultSwath, sampleSeries, blockSeries } from "../src/modules/swath/model";
import { SwathScene, swathLayerKey } from "../src/modules/swath/scene";
import { liveStore, swathsStore } from "../src/modules/swath/state";
import { viewStore } from "../src/modules/scene/bind";
import type { Scene } from "../src/viz/Scene";
import type { FrameContext, SceneLayer } from "../src/viz/sceneTypes";
import type { Vec3 } from "../src/viz/core/vec3";
import type { LabelCache } from "../src/viz/labels/sprites";
import { buildSwath, chartRadius, placeChart, slabOf, type BuildLook, type BuildResult } from "../src/viz/swath/build";
import { directionFrame, toFrame } from "../src/viz/swath/frame";
import { SwathLayer } from "../src/viz/swath/layer";

const x = directionFrame("x");
/** 4 swaths of 10 m along X from 512 100; a model and samples, the third swath has no samples. */
const result: BuildResult = {
  direction: "x",
  frame: x,
  box: { u: [512_100, 512_140], v: [-7_100_020, -7_100_000], w: [250, 260] },
  start: 512_100,
  width: 10,
  count: 4,
  centres: [512_105, 512_115, 512_125, 512_135],
  series: [
    { kind: "blocks", name: "Модель · AU_CUT", mean: [1, 2, 3, 2], count: [4, 4, 4, 4], weights: [400, 800, 800, 200] },
    { kind: "samples", name: "Скважины · AU", mean: [1.5, 2.5, null, 1], count: [3, 5, 0, 2], weights: [3, 5, null, 2] },
  ],
};
const look = (patch: Partial<BuildLook> = {}): BuildLook => ({
  series: [
    { color: "#1f6bff", line: true, bars: true },
    { color: "#f46a1a", line: true, bars: false },
  ],
  range: [0, 3],
  chart3d: true,
  height3d: 0.35,
  ticks: (lo, hi) => niceTicks(lo, hi, 5),
  format: (v, d) => formatNumber(v, d),
  ...patch,
});
const xs = (a: Float32Array) => Array.from(a).filter((_, i) => i % 3 === 0);

describe("the cuboid and the plot in 3D", () => {
  it("cuts the cuboid into swaths from its centre, float32 from an integer origin", () => {
    const b = buildSwath(result, look());
    expect(b.centre).toEqual([512_120, 7_100_010, 255]);
    expect(b.origin).toEqual({ x: 512_120, y: 7_100_010, z: 255 });
    expect(b.offset).toEqual([0, 0, 0]);
    expect(b.half).toEqual([20, 10, 5]);
    expect(b.vertical).toBe(false);
    // the outline: two end rectangles and four long edges over four swaths; three boundaries inside
    expect([b.lines.length / 6, b.inner.length / 6, b.dim.length]).toEqual([2 * 4 + 4 * 4, 3 * 4, 0]);
    expect([Math.min(...xs(b.lines)), Math.max(...xs(b.lines))]).toEqual([-20, 20]);
    // a centre off the metre: the buffers stay small, the offset carries the rest in float64
    const off = buildSwath({ ...result, box: { ...result.box, u: [512_100.3, 512_140.3] }, start: 512_100.3 }, look());
    expect(off.origin.x).toBe(512_120);
    expect(off.offset[0]).toBeCloseTo(0.3, 9);
    expect(Math.max(...xs(off.lines))).toBe(20);
  });

  it("stands the plot over the swaths shown: lines broken at an empty swath, bars, names", () => {
    const b = buildSwath(result, look());
    const chart = b.chart!;
    expect(chart.lines.map((l) => l.segments.length / 4)).toEqual([3, 1]);
    expect(chart.points.map((p) => p.at.length / 2)).toEqual([4, 3]);
    expect(chart.points[0].at[0]).toBe(-15); // the middle of the first swath
    expect(chart.points[0].at[1]).toBeCloseTo(1 / 3, 6); // a mean of 1 on the axis 0…3
    expect(chart.bars).toHaveLength(1);
    expect(chart.bars[0].rects[2]).toBeCloseTo(0.15, 6); // 400 of 800 in the lower 30 %
    const names = chart.labels.filter((l) => l.bold);
    expect(names.map((l) => l.text)).toEqual(["Модель · AU_CUT", "Скважины · AU"]);
    expect(names[0].rows).toBeLessThan(names[1].rows); // the first series on top
    expect(buildSwath(result, look({ chart3d: false })).chart).toBeNull();
    // a series the core could not compute is not drawn
    const gone = buildSwath({ ...result, series: [result.series[0], { ...result.series[1], error: { code: "set_not_found" } }] }, look());
    expect(gone.chart!.lines).toHaveLength(1);
  });

  it("dims the swaths outside the range; along Z the plot lies on its side", () => {
    const b = buildSwath(result, look({ range: [1, 2] }));
    expect([b.lines.length / 6, b.inner.length / 6, b.dim.length / 6]).toEqual([2 * 4 + 2 * 4, 4, 2 * 4 + 2 * 4]);
    expect([b.chart!.t0, b.chart!.t1]).toEqual([-10, 10]);
    expect(Array.from(b.chart!.grid.slice(0, 4))).toEqual([-10, 0, 10, 0]);
    expect(b.chart!.points.map((p) => p.at.length / 2)).toEqual([2, 1]);
    const z = directionFrame("z");
    const bz = buildSwath({ ...result, direction: "z", frame: z, box: { u: [100, 140], v: [0, 20], w: [0, 10] }, start: 100, centres: [105, 115, 125, 135] }, look());
    expect(bz.vertical).toBe(true);
    expect(bz.centre).toEqual(toWorldOf(z, [120, 10, 5]));
  });

  it("Т7.30-3: the slab lit is exactly the swath under the cursor of the window, with its strip of the plot", () => {
    const b = buildSwath(result, look({ range: [1, 3] }));
    for (let k = 0; k < 4; k++) {
      const s = slabOf(b, k)!;
      const t = xs(s.faces);
      expect([Math.min(...t), Math.max(...t)], `swath ${k}`).toEqual([-20 + 10 * k, -10 + 10 * k]);
      expect(s.faces.length).toBe(6 * 2 * 3 * 3);
      expect(s.rim.length).toBe(12 * 6);
      // the strip: over the swaths the plot shows, none for a swath outside the range
      expect(s.strip, `strip ${k}`).toEqual(k === 0 ? null : [-20 + 10 * k, -10 + 10 * k]);
    }
    expect(slabOf(b, -1)).toBeNull();
    expect(slabOf(b, 4)).toBeNull();
    // the slab lies in the swath of the core: its middle is in swath k by the projection of its centre
    const g = directionFrame("angles", 120, -35, 20);
    const bg = buildSwath({ ...result, direction: "angles", frame: g, box: { u: [-100, -60], v: [0, 20], w: [0, 10] }, start: -100 }, look());
    const s2 = slabOf(bg, 2)!;
    const mid: Vec3 = [0, 0, 0];
    for (let i = 0; i < s2.rim.length; i += 3) for (let j = 0; j < 3; j++) mid[j] += s2.rim[i + j] / (s2.rim.length / 3);
    const t = toFrame(g, [mid[0] + bg.centre[0], mid[1] + bg.centre[1], mid[2] + bg.centre[2]])[0];
    expect(Math.floor((t - -100) / 10)).toBe(2);
  });

  it("turns the plot to the viewer: beside in plan, over the model on a section, to the side for Z", () => {
    const b = buildSwath(result, look());
    // plan: looking down, the screen up is north
    const plan = placeChart(b, [0, 0, -1], [1, 0, 0], [0, 1, 0], 1);
    expect(plan.base[0]).toBeCloseTo(0, 12);
    expect(plan.base[1]).toBeGreaterThan(b.half[1]); // beyond the outline, toward the top of the screen
    expect(plan.rise[2]).toBeCloseTo(0, 12);
    // a section looking north: over the model
    const side = placeChart(b, [0, 1, 0], [1, 0, 0], [0, 0, 1], 1);
    expect(side.base[2]).toBeGreaterThan(b.half[2]);
    expect(side.rise[1]).toBeCloseTo(0, 12);
    // exaggerated ×2: the rise is drawn as tall, so it is half in the space of the layer
    const tall = placeChart(b, [0, 1, 0], [1, 0, 0], [0, 0, 1], 2);
    expect(tall.rise[2] * 2).toBeCloseTo(side.rise[2] * (2 * Math.max(20, 10, 10) / (2 * 20)), 6);
    // Z: the plot to the right of the screen
    const z = directionFrame("z");
    const bz = buildSwath({ ...result, direction: "z", frame: z, box: { u: [100, 140], v: [0, 20], w: [0, 10] }, start: 100 }, look());
    const zs = placeChart(bz, [0, 1, 0], [1, 0, 0], [0, 0, 1], 1);
    expect(zs.base[0]).toBeGreaterThan(0);
  });

  it("«Вписать» and the standard views hold the plot: the extent holds the cuboid and the plot turned any way", () => {
    const b = buildSwath(result, look());
    const r = chartRadius(b.half, 0.35, 2);
    expect(b.reach[0]).toBe(512_100);
    expect(b.reach[3]).toBe(512_140);
    expect(b.reach[1]).toBeCloseTo(7_100_010 - r, 6);
    expect(b.reach[5]).toBeCloseTo(255 + r, 6);
    // every view: the plot's corners (with the room for names) inside the extent
    const room = 1 + 0.18 * 3;
    for (const [view, right, up] of [
      [[0, 0, -1], [1, 0, 0], [0, 1, 0]],
      [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
      [[-0.577, -0.577, -0.577], [0.707, -0.707, 0], [-0.408, -0.408, 0.816]],
    ] as [Vec3, Vec3, Vec3][]) {
      const { base, rise } = placeChart(b, view, right, up, 1);
      for (const t of [b.chart!.t0, b.chart!.t1])
        for (const h of [-0.1, room]) {
          const p = [0, 1, 2].map((i) => b.centre[i] + base[i] + b.u[i] * t + rise[i] * h);
          for (let i = 0; i < 3; i++) {
            expect(p[i]).toBeGreaterThanOrEqual(b.reach[i] - 1e-6);
            expect(p[i]).toBeLessThanOrEqual(b.reach[i + 3] + 1e-6);
          }
        }
    }
    // no plot: the cuboid alone
    const bare = buildSwath(result, look({ chart3d: false }));
    expect(bare.reach).toEqual([512_100, 7_100_000, 250, 512_140, 7_100_020, 260]);
  });
});

const labels = { material: () => ({ material: new THREE.SpriteMaterial(), wpx: 80, hpx: 14 }), dispose: () => undefined } as unknown as LabelCache;
const ctxOf = (position: Vec3, up: Vec3 = [0, 0, 1]): FrameContext => ({
  camera: { position, target: [0, 0, 0], up, half: 50, near: 1, far: 1e5 },
  options: { perspective: false, fov: 45 } as FrameContext["options"],
  width: 800,
  height: 600,
  vexag: 1,
  pixelsPerUnit: () => 6,
});

function geometries(o: THREE.Object3D): THREE.BufferGeometry[] {
  const out: THREE.BufferGeometry[] = [];
  o.traverse((x) => {
    const g = (x as THREE.Mesh).geometry;
    if (g) out.push(g);
  });
  return out;
}

describe("the scene layer", () => {
  it("turns the plot each frame by its matrix only; lights a swath and puts it out", () => {
    const layer = new SwathLayer(buildSwath(result, look()), { clip: [], resolution: new THREE.Vector2(800, 600), labels });
    expect(layer.origin).toEqual({ x: 512_120, y: 7_100_010, z: 255 });
    expect(layer.bbox).toHaveLength(6);
    const before = geometries(layer.object);
    layer.beforeRender(ctxOf([0, 0, 100], [0, 1, 0]));
    const m1 = layer.object.children.find((c) => !c.matrixAutoUpdate)!.matrix.clone();
    layer.beforeRender(ctxOf([0, -100, 0]));
    const m2 = layer.object.children.find((c) => !c.matrixAutoUpdate)!.matrix.clone();
    expect(m1.equals(m2)).toBe(false);
    const after = geometries(layer.object);
    expect(after).toHaveLength(before.length);
    after.forEach((g, i) => expect(g).toBe(before[i])); // the same geometry: no rebuild
    const n = layer.object.children.length;
    layer.setHighlight(1);
    expect(layer.highlighted).toBe(1);
    expect(layer.object.children.length).toBe(n + 2); // the slab and its edges (the strip is in the plot)
    layer.setHighlight(null);
    expect(layer.object.children.length).toBe(n);
    layer.dispose();
  });
});

function toWorldOf(f: ReturnType<typeof directionFrame>, c: Vec3): Vec3 {
  return [0, 1, 2].map((i) => f.u[i] * c[0] + f.v[i] * c[1] + f.w[i] * c[2]).map((v) => v + 0) as Vec3;
}

describe("the source of the scene", () => {
  const row = (id: number) => ({ id, set_id: null, kind: "swath" as const, name: `S${id}`, auto_name: true, settings: defaultSwath([blockSeries([], 7, "AU_CUT"), sampleSeries([], 3, null, "AU")]) as unknown as Record<string, unknown>, version: 1 });
  let layers: Map<string, SceneLayer>;
  let scene: Scene;

  beforeEach(() => {
    layers = new Map();
    scene = {
      clipPlanes: [],
      resolution: new THREE.Vector2(800, 600),
      addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
      removeLayer: (k: string) => void layers.delete(k),
      invalidate: () => undefined,
      onRestored: () => () => true,
    } as unknown as Scene;
    swathsStore.set({ swaths: [row(5), row(6)], loaded: true, rev: 0, hover: null, selected: null });
    liveStore.set({ live: {} });
    viewStore.set({ swaths: [] });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "x", message: "нет ядра", hint: "" }), { status: 503 })));
  });

  it("draws the swaths the view key shows, from the window's answer; lights the swath under its cursor", () => {
    const s = new SwathScene(scene, { labels: () => labels });
    expect(layers.size).toBe(0);
    const settings = defaultSwath([blockSeries([], 7, "AU_CUT"), sampleSeries([], 3, null, "AU")]);
    liveStore.set({ live: { 5: { settings, result: { ...(result as unknown as SwathResult), frame: { ...x }, box: result.box as SwathResult["box"] } } } });
    viewStore.set({ swaths: [{ swath: 5, visible: true }, { swath: 6, visible: false }] });
    expect([...layers.keys()]).toEqual([swathLayerKey(5)]);
    const first = layers.get(swathLayerKey(5));
    swathsStore.set({ hover: { id: 5, index: 2 } });
    expect(s.stats()).toEqual([{ id: 5, count: 4, lit: 2, chart: true }]);
    expect(layers.get(swathLayerKey(5))).toBe(first); // lit without a new build
    // a colour changed in the window: built again
    liveStore.set({ live: { 5: { ...liveStore.get().live[5], settings: { ...settings, series: settings.series.map((x) => ({ ...x, color: "#000000" })) } } } });
    expect(layers.get(swathLayerKey(5))).not.toBe(first);
    viewStore.set({ swaths: [{ swath: 5, visible: false }] });
    expect(layers.size).toBe(0);
    s.dispose();
  });
});
