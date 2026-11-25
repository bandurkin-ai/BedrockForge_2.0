// The 3D of the inspector of a block (Э18, 18.Д4; ТЗ 7.35 Д-30) on the REAL answers of the core (`inspect_answers.json`): the numbers of the segments
// (float64 centre → float32 from the integer origin), the colours of the weights (a negative weight apart, a probe cut off in Slate), the layer
// of the segments (what the toggles and the probe lit change), the ellipsoid of the pass through `viz/ellipsoid` as it is, and the scene: a layer
// per window open, built again for another row, taken away with the window. A fake scene.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeWindow, openWindow, windowStore } from "../../src/shell/windows";
import { readInspect, type InspectAnswer } from "../../src/modules/estimation/inspect/api";
import { buildSegments, drawable, ellipsoidView } from "../../src/modules/estimation/inspect/build";
import { SegmentsLayer } from "../../src/modules/estimation/inspect/layer";
import { ELLIPSOID_KEY, InspectScene, SEGMENTS_KEY } from "../../src/modules/estimation/inspect/scene";
import { inspectStore, INSPECT_WINDOW, resetInspect, setActive, setShow } from "../../src/modules/estimation/inspect/state";
import { weightColor } from "../../src/modules/estimation/inspect/view";
import { buildEllipsoid } from "../../src/viz/ellipsoid/build";
import type { Scene } from "../../src/viz/Scene";
import type { SceneLayer } from "../../src/viz/sceneTypes";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const F: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/estimate/inspect_answers.json"), "utf-8"));
const ANS = (k: string): InspectAnswer => readInspect(F.answers[k]);
const ctx = () => ({ clip: [] as THREE.Plane[], resolution: new THREE.Vector2(800, 600) });

describe("the numbers of the segments", () => {
  const a = ANS("pass_1");
  const r = a.results[0];
  const b = buildSegments(r, a.block.centre);
  it("the origin is the centre rounded, the offset float64; a segment goes from the centre to the probe", () => {
    expect(b.origin).toEqual({ x: 1040, y: 2066, z: -296 });
    expect(b.offset[0]).toBeCloseTo(0.20671, 9);
    expect(b.offset[2]).toBeCloseTo(-0.25, 9);
    expect(b.taken.ids).toHaveLength(9);
    expect(b.taken.positions).toHaveLength(9 * 6);
    const s = r.segments[0];
    expect(b.taken.positions[0]).toBeCloseTo(a.block.centre[0] - 1040, 4);
    expect(b.taken.positions[3]).toBeCloseTo(s.to[0] - 1040, 4);
    expect(b.taken.positions[5]).toBeCloseTo(s.to[2] + 296, 4);
  });
  it("every candidate has its point: the probes taken first (the colour of the weight), then the cut off ones in Slate", () => {
    expect(b.points.ids).toHaveLength(121);
    expect(b.points.taken).toBe(9);
    const slate = weightColor(null, 1);
    expect([...b.points.colors.slice(27, 30)].map((x) => +x.toFixed(4))).toEqual(slate.map((x) => +x.toFixed(4)));
    expect(new Set(b.points.ids).size).toBe(121);
  });
  it("a line only to the probes cut off by a limit (hole, n_max, soft border), not to those outside the ellipsoid", () => {
    const limits = r.candidates.filter((c) => c.reason !== "selected" && c.reason !== "outside");
    expect(limits).toHaveLength(9);
    expect(b.cut.ids.sort()).toEqual(limits.map((c) => c.i).sort());
    expect(b.cut.positions).toHaveLength(9 * 6);
  });
  it("the reach counts the centre, the probes taken and cut off, not those outside the ellipsoid", () => {
    const [x0, , , x1] = b.reach;
    expect(x0).toBeLessThanOrEqual(a.block.centre[0]);
    expect(x1).toBeGreaterThanOrEqual(a.block.centre[0]);
    const inside = r.candidates.filter((c) => c.reason !== "outside" && c.xyz);
    expect(x0).toBeCloseTo(Math.min(a.block.centre[0], ...inside.map((c) => c.xyz![0])), 9);
  });
  it("negative weights are Copper Orange, the largest positive one is Cobalt", () => {
    const p3 = ANS("pass_3");
    const q = buildSegments(p3.results[0], p3.block.centre);
    const neg = p3.results[0].segments.findIndex((s) => (s.lambda ?? 0) < 0);
    expect(neg).toBeGreaterThanOrEqual(0);
    const want = weightColor(p3.results[0].segments[neg].lambda, q.maxWeight);
    expect([...q.taken.colors.slice(6 * neg, 6 * neg + 3)]).toEqual(want.map((x) => Math.fround(x)));
    // the colour of a negative weight is the orange ramp: red above blue
    expect(q.taken.colors[6 * neg]).toBeGreaterThan(q.taken.colors[6 * neg + 2]);
    expect(weightColor(q.maxWeight, q.maxWeight).map((x) => Math.round(x * 255))).toEqual([0x1f, 0x6b, 0xff]);
  });
  it("a block not estimated: nothing to draw but the centre", () => {
    const o = ANS("out_of_domain");
    const e = buildSegments(o.results[0], o.block.centre);
    expect(e.taken.ids).toEqual([]);
    expect(e.points.ids).toEqual([]);
    expect(e.reach.slice(0, 3)).toEqual(e.reach.slice(3));
  });
});

describe("the ellipsoid of the pass", () => {
  it("the angles and the ranges of the pass go to the layer of Д-14 as they are", () => {
    const e = ANS("pass_3").results[0].ellipsoid!;
    expect(drawable(e)).toBe(true);
    expect(ellipsoidView(e)).toEqual({ angles: { ang1: 35, ang2: -10, ang3: 20 }, ranges: { a1: 43.2, a2: 31.2, a3: 16.8 } });
    const b = buildEllipsoid({ centre: e.centre_xyz, structures: [ellipsoidView(e)], selected: 0 });
    expect(b.centre).toEqual(e.centre_xyz);
    expect(drawable(null)).toBe(false);
    expect(drawable({ ...e, r2: 0 })).toBe(false);
  });
});

describe("the layer of the segments", () => {
  const a = ANS("pass_1");
  const mk = () => new SegmentsLayer(buildSegments(a.results[0], a.block.centre), ctx());
  it("what is drawn: the segments and points taken, the probes cut off; the bounds for «Вписать»", () => {
    const l = mk();
    expect(l.shown()).toEqual({ taken: 9, cut: 9, points: 121, excluded: true, segments: true, picked: null });
    expect(l.origin).toEqual({ x: 1040, y: 2066, z: -296 });
    expect(l.bbox).toHaveLength(6);
    expect(l.object.children.length).toBeGreaterThanOrEqual(5);
    l.dispose();
  });
  it("the toggles hide the segments or the probes cut off; the probe lit is a ring, a probe without a point is none", () => {
    const l = mk();
    l.setShow({ segments: false, excluded: true });
    const vis = () => l.object.children.map((c) => c.visible);
    expect(vis().filter(Boolean).length).toBeLessThan(l.object.children.length);
    l.setShow({ segments: true, excluded: false });
    expect(l.shown()).toMatchObject({ segments: true, excluded: false });
    const i = a.results[0].candidates[3].i;
    l.setPicked(i);
    expect(l.shown().picked).toBe(i);
    const ring = l.object.children[l.object.children.length - 1] as THREE.Points;
    expect(ring.visible).toBe(true);
    expect(ring.geometry.getAttribute("position").count).toBe(1);
    l.setPicked(-5);
    expect(l.shown().picked).toBeNull();
    expect(ring.visible).toBe(false);
    l.dispose();
    expect(l.object.children).toEqual([]);
  });
  it("a block with no probes is a layer without lines", () => {
    const o = ANS("out_of_domain");
    const l = new SegmentsLayer(buildSegments(o.results[0], o.block.centre), ctx());
    expect(l.shown()).toMatchObject({ taken: 0, cut: 0, points: 0 });
    l.dispose();
  });
  it("the cost of the build and of the layer for a block of 121 candidates, ms (printed for the checkpoint)", () => {
    const t0 = performance.now();
    for (let k = 0; k < 20; k++) mk().dispose();
    const ms = (performance.now() - t0) / 20;
    console.log(`inspect layer build: ${ms.toFixed(2)} ms`);
    expect(ms).toBeLessThan(50);
  });
});

describe("the scene", () => {
  let layers: Map<string, SceneLayer>;
  let scene: Scene;
  let invalid: number;
  const fake = () => {
    layers = new Map();
    invalid = 0;
    return {
      clipPlanes: [],
      resolution: new THREE.Vector2(800, 600),
      addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
      removeLayer: (k: string) => void layers.delete(k),
      layerChanged: () => undefined,
      invalidate: () => void invalid++,
      onRestored: () => () => undefined,
    } as unknown as Scene;
  };
  let sc: InspectScene;
  beforeEach(() => {
    windowStore.set({ windows: [] });
    resetInspect();
    inspectStore.set({ show: { ellipsoid: true, segments: true, excluded: true } });
    scene = fake();
    sc = new InspectScene(scene);
  });
  afterEach(() => {
    sc.dispose();
    resetInspect();
    windowStore.set({ windows: [] });
  });
  const open = () => openWindow(INSPECT_WINDOW, "Блок", "normal", {});
  const ask = (k: string) => inspectStore.set({ answer: ANS(k), request: { model_id: 7, row: 1 }, active: null });

  it("no layers without the window or without the answer; the window with the answer has the two", () => {
    ask("pass_1");
    expect([...layers.keys()]).toEqual([]);
    const id = open();
    expect([...layers.keys()].sort()).toEqual([ELLIPSOID_KEY, SEGMENTS_KEY]);
    expect(sc.stats().segments).toMatchObject({ taken: 9, points: 121 });
    expect(sc.stats().ellipsoid!.bases).toHaveLength(1);
    expect(sc.stats().centre).toEqual([1040.20671, 2066.47595, -296.25]);
    closeWindow(id);
    expect([...layers.keys()]).toEqual([]);
    expect(sc.stats()).toEqual({ segments: null, ellipsoid: null, centre: null });
  });
  it("the toggles and the probe lit change the drawing, not the layers", () => {
    ask("pass_1");
    open();
    const seg = layers.get(SEGMENTS_KEY);
    setShow({ excluded: false });
    expect(sc.stats().segments).toMatchObject({ excluded: false });
    inspectStore.set({ picked: ANS("pass_1").results[0].candidates[0].i });
    expect(sc.stats().segments!.picked).not.toBeNull();
    expect(layers.get(SEGMENTS_KEY)).toBe(seg);
    const n = invalid;
    setShow({ segments: false });
    expect(invalid).toBeGreaterThan(n);
    setShow({ ellipsoid: false });
    expect([...layers.keys()]).toEqual([SEGMENTS_KEY]);
    setShow({ ellipsoid: true });
    expect([...layers.keys()].sort()).toEqual([ELLIPSOID_KEY, SEGMENTS_KEY]);
  });
  it("a new block or another row builds the layers again; a block not estimated has no ellipsoid", () => {
    ask("pass_1");
    open();
    const first = layers.get(SEGMENTS_KEY);
    ask("pass_3");
    expect(layers.get(SEGMENTS_KEY)).not.toBe(first);
    expect(sc.stats().ellipsoid!.bases).toHaveLength(1);
    expect(sc.stats().centre).toEqual(ANS("pass_3").results[0].ellipsoid!.centre_xyz);
    ask("out_of_domain");
    expect([...layers.keys()]).toEqual([SEGMENTS_KEY]);
    // two rows of the plan: the tab shows another one
    const one = F.answers.pass_1.results[0];
    const two = { ...F.answers.pass_3.results[0], estimation_id: 10 };
    inspectStore.set({ answer: readInspect({ ...F.answers.pass_1, results: [one, two] }), active: null });
    expect(sc.stats().segments).toMatchObject({ taken: 9 });
    setActive(10);
    expect(sc.stats().segments).toMatchObject({ taken: 24 });
  });
});
