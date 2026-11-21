// The handles of a swath box (ТЗ 7.35 Д-01; ADR 0019 §6; ТД01-1; лучи и плоскости — перенос `swathBox.test.ts`
// BF): the ring of the azimuth, the half circle of the dip, the circle of the roll; the ray of the cursor on the
// plane of a handle → its angle, steps of 1° (`Shift` — 5°), the sign of the dip (negative — down, question № 29);
// a handle is hit within 8 px, else the pointer is the scene's; the pointer shared with the editor's tool; a drag
// turns the cuboid without a request, the release runs `stats.swath {swath_id, azimuth, dip, roll}` once.
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SwathResult } from "../src/modules/swath/api";
import { runSwath } from "../src/modules/swath/bind";
import { blockSeries, defaultSwath, sampleSeries } from "../src/modules/swath/model";
import { HANDLES_KEY } from "../src/modules/swath/handles";
import { SwathScene, swathLayerKey } from "../src/modules/swath/scene";
import { dragStore, liveStore, swathsStore } from "../src/modules/swath/state";
import { viewStore } from "../src/modules/scene/bind";
import { projectMatrix, projectRay, type ToolPointer } from "../src/viz/core/toolInput";
import { lookFrom } from "../src/viz/core/views";
import type { Vec3 } from "../src/viz/core/vec3";
import type { LabelCache } from "../src/viz/labels/sprites";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { projectToScreen, viewProjection } from "../src/viz/pick/ray";
import type { Ray } from "../src/viz/section/math";
import type { Scene } from "../src/viz/Scene";
import { ToolChain } from "../src/viz/sceneHooks";
import type { SceneLayer } from "../src/viz/sceneTypes";
import { angleOn, clampAngle, dragAngles, handlesOf, hitHandle, onPlane, pointAt, rotationBetween, turn, type Angles, type Handle, type HitView } from "../src/viz/swath/drag";
import { directionFrame, frameOf } from "../src/viz/swath/frame";
import { SwathHandles } from "../src/viz/swath/handle";
import type { SwathLayer } from "../src/viz/swath/layer";

const close = (a: readonly number[], b: readonly number[], d = 9) => a.forEach((x, i) => expect(x).toBeCloseTo(b[i], d));
/** A ray straight down onto (x, y); one looking north through (x, z); one looking east through (y, z). */
const down = (x: number, y: number): Ray => ({ origin: [x, y, 1000], dir: [0, 0, -1] });
const north = (x: number, z: number): Ray => ({ origin: [x, -1e4, z], dir: [0, 1, 0] });
const east = (y: number, z: number): Ray => ({ origin: [-1e4, y, z], dir: [1, 0, 0] });
const C: Vec3 = [512_120, 7_100_010, 255];
const HALF: Vec3 = [20, 10, 5];
const X: Angles = { azimuth: 90, dip: 0, roll: 0 };
const of = (hs: Handle[], kind: Handle["kind"]) => hs.find((h) => h.kind === kind)!;

describe("the handles of the box (ТД01-1)", () => {
  it("rings the cuboid: the azimuth in plan, the dip in the vertical plane of the axis, the roll at the far end", () => {
    const hs = handlesOf(X, C, HALF);
    expect(hs.map((h) => h.kind)).toEqual(["azimuth", "dip", "roll"]);
    const [az, dip, roll] = hs;
    const reach = Math.hypot(...HALF);
    expect(az.radius).toBeGreaterThan(reach); // outside the cuboid
    expect(dip.radius).toBeGreaterThan(reach);
    close(az.axis, [0, 0, 1]);
    close(dip.axis, [0, -1, 0]); // h of the azimuth 90°
    close(roll.axis, [1, 0, 0]); // u
    close(roll.centre, [C[0] + HALF[0], C[1], C[2]]); // at the end face
    expect(roll.radius).toBeGreaterThan(Math.hypot(HALF[1], HALF[2]));
    // the knobs where the angles are: the ring at the azimuth (east), the dip on the axis, the roll on v
    close(pointAt(az, az.at), [C[0] + az.radius, C[1], C[2]], 6);
    close(pointAt(dip, dip.at), [C[0] + dip.radius, C[1], C[2]], 6);
    const v = frameOf(90).v;
    close(pointAt(roll, roll.at), [roll.centre[0] + roll.radius * v[0], roll.centre[1] + roll.radius * v[1], roll.centre[2] + roll.radius * v[2]], 6);
    expect([dip.from, dip.to, roll.from, roll.to]).toEqual([-90, 90, -180, 180]);
  });

  it("meets a plane with a ray (BF onPlane) and reads the angle of the cursor on a handle", () => {
    close(onPlane([0, 0, 50], [0, 0, 1], down(3, 4))!, [3, 4, 50]);
    expect(onPlane([0, 0, 50], [0, 0, 1], north(3, 4))).toBeNull(); // along the plane
    const az = of(handlesOf(X, C, HALF), "azimuth");
    expect(angleOn(az, down(C[0] + 10, C[1] + 10))).toBeCloseTo(45, 9);
    expect(angleOn(az, down(C[0], C[1] - 7))).toBe(180);
    expect(angleOn(az, down(C[0], C[1]))).toBeNull(); // the centre: no direction
  });

  it("turns the azimuth by the angle of the cursor about the centre, in steps of 1° (Shift — 5°)", () => {
    const az = of(handlesOf(X, C, HALF), "azimuth");
    const knob = pointAt(az, az.at);
    const r = az.radius;
    const toward = (deg: number) => down(C[0] + r * Math.sin((deg * Math.PI) / 180), C[1] + r * Math.cos((deg * Math.PI) / 180));
    expect(dragAngles(X, az, down(knob[0], knob[1]), toward(45))).toEqual({ azimuth: 45, dip: 0, roll: 0 });
    expect(dragAngles(X, az, down(knob[0], knob[1]), toward(47.3))!.azimuth).toBe(47);
    expect(dragAngles(X, az, down(knob[0], knob[1]), toward(47.3), true)!.azimuth).toBe(45);
    expect(dragAngles(X, az, down(knob[0], knob[1]), toward(48), true)!.azimuth).toBe(50);
    // past north: the azimuth wraps to [0; 360)
    expect(dragAngles(X, az, down(knob[0], knob[1]), toward(-20))!.azimuth).toBe(340);
    // less than half a step: as it was (a typed 33.4 stays 33.4)
    const typed = { azimuth: 33.4, dip: 0, roll: 0 };
    const h = of(handlesOf(typed, C, HALF), "azimuth");
    const k = pointAt(h, h.at);
    expect(dragAngles(typed, h, down(k[0], k[1]), down(k[0] + 0.001, k[1]))).toEqual(typed);
    // the ring seen edge-on gives no angle
    expect(dragAngles(X, az, north(C[0], C[2]), north(C[0] + 5, C[2]))).toBeNull();
  });

  it("dips the axis: negative — down, as the holes; within ±90°", () => {
    const dip = of(handlesOf(X, C, HALF), "dip");
    const r = dip.radius;
    const at = (deg: number) => north(C[0] + r * Math.cos((deg * Math.PI) / 180), C[2] + r * Math.sin((deg * Math.PI) / 180));
    const d = dragAngles(X, dip, at(0), at(-30))!;
    expect(d).toEqual({ azimuth: 90, dip: -30, roll: 0 });
    const u = frameOf(d.azimuth, d.dip, d.roll).u;
    expect(u[2]).toBeLessThan(0); // the axis goes down
    close(u, [Math.cos(Math.PI / 6), 0, -0.5]);
    expect(dragAngles(X, dip, at(0), at(60), true)!.dip).toBe(60);
    // over the top: no more than +90°
    expect(dragAngles({ azimuth: 90, dip: 80, roll: 0 }, dip, at(0), at(30))!.dip).toBe(90);
    expect(clampAngle("dip", -120)).toBe(-90);
  });

  it("rolls the box about the axis of the swaths: v from h toward n", () => {
    const roll = of(handlesOf(X, C, HALF), "roll");
    const r = roll.radius;
    // looking east at the end face: h = (0, −1, 0), n = (0, 0, 1)
    const at = (deg: number) => east(C[1] - r * Math.cos((deg * Math.PI) / 180), C[2] + r * Math.sin((deg * Math.PI) / 180));
    const d = dragAngles(X, roll, at(0), at(30))!;
    expect(d).toEqual({ azimuth: 90, dip: 0, roll: 30 });
    close(frameOf(90, 0, 30).v, [0, -Math.cos(Math.PI / 6), 0.5]);
    expect(dragAngles({ azimuth: 90, dip: 0, roll: 170 }, roll, at(0), at(20))!.roll).toBe(-170); // (−180; 180]
    expect(clampAngle("roll", -180)).toBe(180);
  });

  it("turns the cuboid rigidly: the axes of the old frame onto the new", () => {
    const from = directionFrame("x");
    const to = frameOf(35, -20, 15);
    const m = rotationBetween(from, to);
    close(turn(m, from.u), to.u, 12);
    close(turn(m, from.v), to.v, 12);
    close(turn(m, from.w), to.w, 12);
    const p: Vec3 = [3, -4, 5];
    expect(Math.hypot(...turn(m, p))).toBeCloseTo(Math.hypot(...p), 12);
    close(rotationBetween(to, to), [1, 0, 0, 0, 1, 0, 0, 0, 1], 12);
  });
});

// a view from above over the cuboid, 800 × 600, with the scene origin of UTM
const W = 800;
const H = 600;
const ORIGIN = { x: 512_000, y: 7_100_000, z: 0 };
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
const cam = lookFrom([0, -1e-4, 1], { min: [60, -40, 200], max: [180, 60, 300] }, opts);
const vp = viewProjection(cam, opts);
const view: HitView = { matrix: projectMatrix(vp, ORIGIN, 1), width: W, height: H };
const screen = (p: Vec3) => {
  const s = projectToScreen(view.matrix, p[0], p[1], p[2], W, H)!;
  return { x: s[0], y: s[1] };
};

describe("hit within 8 px", () => {
  it("finds the handle under the cursor, else none — the pointer is the scene's", () => {
    const hs = handlesOf(X, C, HALF);
    const az = of(hs, "azimuth");
    const ring = screen(pointAt(az, 200)); // south-west of the ring: away from the other two
    const centre = screen(C);
    const out = { x: ring.x - centre.x, y: ring.y - centre.y };
    const k = Math.hypot(out.x, out.y);
    const off = (px: number) => ({ x: ring.x + (out.x / k) * px, y: ring.y + (out.y / k) * px });
    expect(hitHandle(hs, view, ring.x, ring.y)).toBe("azimuth");
    expect(hitHandle(hs, view, off(7).x, off(7).y)).toBe("azimuth");
    expect(hitHandle(hs, view, off(9).x, off(9).y)).toBeNull();
    // from above the circle of the roll is a segment across the end; the dip — along the axis east of the centre
    const end = screen(pointAt(of(hs, "roll"), 90));
    expect(hitHandle(hs, view, end.x, end.y)).toBe("roll");
    const d = screen(pointAt(of(hs, "dip"), 0));
    expect(hitHandle(hs, view, d.x - 5, d.y + 3)).toBe("dip");
    expect(hitHandle(hs, view, centre.x - 150, centre.y - 150)).toBeNull();
  });
});

describe("the pointer shared by the editor's tool, the handles and the scene", () => {
  const ev = (type: ToolPointer["type"], button = 0): ToolPointer => ({ type, x: 1, y: 2, clientX: 1, clientY: 2, button, shift: false, ctrl: false });

  it("the tool of the editor, else the handles; the taker of a press has the drag and its click", () => {
    const chain = new ToolChain();
    expect(chain.pointer(ev("down"))).toBe(false); // neither: the scene as before
    expect(chain.pointer(ev("click"))).toBe(false);
    const got: string[] = [];
    let toolTakes = true;
    chain.tool = { pointer: (e) => (got.push(`tool:${e.type}`), toolTakes && e.type !== "move") };
    chain.handles = { pointer: (e) => (got.push(`handles:${e.type}`), e.type === "down") };
    expect(chain.pointer(ev("down"))).toBe(true);
    expect(got).toEqual(["tool:down"]); // the editor took it: the handles never see it
    got.length = 0;
    chain.pointer(ev("move"));
    chain.pointer(ev("up"));
    chain.pointer(ev("click"));
    expect(got).toEqual(["tool:move", "tool:up", "tool:click"]);
    // a press the tool does not take is the scene's: the handles are not there while a tool is set
    toolTakes = false;
    got.length = 0;
    expect(chain.pointer(ev("down"))).toBe(false);
    chain.pointer(ev("move", -1)); // hover: not lit under the tool
    chain.pointer(ev("leave", -1));
    expect(got).toEqual(["tool:down", "tool:move", "tool:leave"]);
    // the tool gone: the handles have the pointer; the change lets go of their hover first
    chain.tool = null;
    got.length = 0;
    chain.reset();
    expect(got).toEqual(["handles:leave"]);
    got.length = 0;
    expect(chain.pointer(ev("down"))).toBe(true);
    chain.pointer(ev("move"));
    chain.pointer(ev("up"));
    chain.pointer(ev("move", -1));
    expect(got).toEqual(["handles:down", "handles:move", "handles:up", "handles:move"]);
    // the tool changed in the middle of a drag: the drag is called off
    const cancel = vi.fn();
    chain.handles = { pointer: (e: ToolPointer) => e.type === "down", cancel } as never;
    chain.pointer(ev("down"));
    chain.reset();
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});

describe("a drag in the main 3D (Д-01)", () => {
  const labels = { material: () => ({ material: new THREE.SpriteMaterial(), wpx: 80, hpx: 14 }), dispose: () => undefined } as unknown as LabelCache;
  const x = directionFrame("x");
  const result = {
    direction: "x",
    frame: { ...x },
    box: { u: [C[0] - 20, C[0] + 20], v: [-(C[1] + 10), -(C[1] - 10)], w: [250, 260] },
    start: C[0] - 20,
    width: 10,
    count: 4,
    centres: [C[0] - 15, C[0] - 5, C[0] + 5, C[0] + 15],
    block: null,
    notes: [],
    series: [
      { kind: "blocks", name: "Модель · AU", mean: [1, 2, 3, 2], count: [4, 4, 4, 4], weights: [400, 800, 800, 200] },
      { kind: "samples", name: "Скважины · AU", mean: [1.5, 2.5, null, 1], count: [3, 5, 0, 2], weights: [3, 5, null, 2] },
    ],
  } as unknown as SwathResult;
  const settings = defaultSwath([blockSeries([], 7, "AU"), sampleSeries([], 3, null, "AU")]);
  const row = { id: 5, set_id: null, kind: "swath" as const, name: "S5", auto_name: true, settings: settings as unknown as Record<string, unknown>, version: 1 };
  let layers: Map<string, SceneLayer>;
  let scene: Scene;
  let fetchMock: ReturnType<typeof vi.fn>;
  let handles: unknown;
  const canvas = { style: { cursor: "" } };

  beforeEach(() => {
    layers = new Map();
    canvas.style.cursor = "";
    scene = {
      clipPlanes: [],
      resolution: new THREE.Vector2(W, H),
      canvas,
      addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
      removeLayer: (k: string) => void layers.delete(k),
      invalidate: () => undefined,
      onRestored: () => () => true,
      setHandles: (h: unknown) => void (handles = h),
      cursorRay: (px: number, py: number) => projectRay(cam, opts, { w: W, h: H }, ORIGIN, 1, px, py),
      projectView: () => view,
    } as unknown as Scene;
    swathsStore.set({ swaths: [row], loaded: true, rev: 0, hover: null, selected: null });
    liveStore.set({ live: { 5: { settings, result } } }); // its window is open
    viewStore.set({ swaths: [{ swath: 5, visible: true }] });
    dragStore.set({ drag: null });
    fetchMock = vi.fn(async () => new Response("{}", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const at = (type: ToolPointer["type"], p: { x: number; y: number }, opt: { button?: number; shift?: boolean } = {}): ToolPointer => ({ type, x: p.x, y: p.y, clientX: p.x, clientY: p.y, button: opt.button ?? (type === "move" ? -1 : 0), shift: !!opt.shift, ctrl: false });

  it("shows the handles at the swath chosen and shown, lights the one under the cursor", () => {
    const run = vi.fn(async () => true);
    const s = new SwathScene(scene, { labels: () => labels, run });
    expect(handles).toBe(s.handles);
    expect(layers.has(HANDLES_KEY)).toBe(false); // nothing chosen
    swathsStore.set({ selected: 5 });
    expect(layers.get(HANDLES_KEY)).toBeInstanceOf(SwathHandles);
    expect(s.handles.stats().shown.map((h) => [h.kind, h.at, h.lit])).toEqual([["azimuth", 90, false], ["dip", 0, false], ["roll", 0, false]]);
    const ring = screen(pointAt(of(handlesOf(X, C, HALF), "azimuth"), 200));
    expect(s.handles.pointer(at("move", ring))).toBe(false); // hover is never taken
    expect(s.handles.stats().shown[0].lit).toBe(true);
    expect(canvas.style.cursor).toBe("grab");
    s.handles.pointer(at("move", { x: 5, y: 5 }));
    expect(canvas.style.cursor).toBe("");
    // away from the handles a press is the scene's (the view turns, a collar is picked)
    expect(s.handles.pointer(at("down", { x: 5, y: 5 }))).toBe(false);
    viewStore.set({ swaths: [{ swath: 5, visible: false }] });
    expect(layers.has(HANDLES_KEY)).toBe(false);
    s.dispose();
    expect(handles).toBeNull();
  });

  it("while a tool of the editor is set the handles are not lit and leave its cursor alone", () => {
    const s = new SwathScene(scene, { labels: () => labels, run: vi.fn(async () => true) });
    swathsStore.set({ selected: 5 });
    const chain = new ToolChain();
    chain.handles = s.handles;
    const ring = screen(pointAt(of(handlesOf(X, C, HALF), "azimuth"), 200));
    chain.pointer(at("move", ring));
    expect(canvas.style.cursor).toBe("grab");
    // Scene.setTool: the tool, the chain reset, the tool's cursor
    chain.tool = { pointer: () => false };
    chain.reset();
    canvas.style.cursor = "crosshair";
    expect(s.handles.stats().shown.every((h) => !h.lit)).toBe(true);
    chain.pointer(at("move", ring));
    chain.pointer(at("move", { x: 5, y: 5 }));
    chain.pointer(at("move", ring));
    expect(canvas.style.cursor).toBe("crosshair");
    expect(s.handles.stats().shown.every((h) => !h.lit)).toBe(true);
    expect(chain.pointer(at("down", ring))).toBe(false); // the press is the scene's (or the tool's)
    s.dispose();
  });

  it("turns the cuboid every move without a request; the release runs stats.swath once", async () => {
    const run = vi.fn(async () => true);
    const s = new SwathScene(scene, { labels: () => labels, run });
    swathsStore.set({ selected: 5 });
    const layer = layers.get(swathLayerKey(5)) as SwathLayer;
    const az = of(handlesOf(X, C, HALF), "azimuth");
    const toward = (deg: number) => screen(pointAt(az, deg));
    fetchMock.mockClear();
    expect(s.handles.pointer(at("down", toward(90)))).toBe(true);
    expect(s.handles.stats().dragging).toBe("azimuth");
    for (const deg of [80, 70, 60, 52, 45.2]) s.handles.pointer(at("move", toward(deg), { button: 0 }));
    expect(fetchMock).not.toHaveBeenCalled(); // ни одного запроса за перетаскивание
    expect(run).not.toHaveBeenCalled();
    expect(dragStore.get().drag).toEqual({ id: 5, azimuth: 45, dip: 0, roll: 0 }); // the window shows the angles
    const m = layer.posed!;
    close(turn(m, x.u), frameOf(45).u, 9);
    expect(layers.get(swathLayerKey(5))).toBe(layer); // turned, not built again
    expect(s.handles.stats().shown.map((h) => h.lit)).toEqual([true, false, false]);
    s.handles.pointer(at("up", toward(45.2)));
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith({ swath_id: 5, azimuth: 45, dip: 0, roll: 0 });
    expect(fetchMock).not.toHaveBeenCalled();
    // until the answer for the new angles: the pose and the angles held
    expect(s.target()!.angles).toEqual({ azimuth: 45, dip: 0, roll: 0 });
    await Promise.resolve();
    await Promise.resolve();
    expect(dragStore.get().drag).toBeNull();
    // the window took the settings (X → «по углам») and its answer came: built anew, nothing held
    const turned = { ...settings, direction: "angles" as const, azimuth: 45 };
    liveStore.set({ live: { 5: { settings: turned, result: { ...result, direction: "angles", frame: frameOf(45) } as SwathResult } } });
    const next = layers.get(swathLayerKey(5)) as SwathLayer;
    expect(next).not.toBe(layer);
    expect(next.posed).toBeNull();
    expect(s.target()!.angles).toEqual({ azimuth: 45, dip: 0, roll: 0 });
    s.dispose();
  });

  it("a press that does not turn runs nothing; a failed command puts the box back", async () => {
    const run = vi.fn(async () => false);
    const s = new SwathScene(scene, { labels: () => labels, run });
    swathsStore.set({ selected: 5 });
    const layer = layers.get(swathLayerKey(5)) as SwathLayer;
    const dip = of(handlesOf(X, C, HALF), "dip");
    const knob = screen(pointAt(dip, 0));
    expect(s.handles.pointer(at("down", knob))).toBe(true);
    s.handles.pointer(at("up", knob));
    expect(run).not.toHaveBeenCalled();
    expect(layer.posed).toBeNull();
    // the roll from above is seen edge-on: a move along it gives no angle, nothing runs
    const roll = of(handlesOf(X, C, HALF), "roll");
    const top = screen(pointAt(roll, 90));
    expect(s.handles.pointer(at("down", top))).toBe(true);
    s.handles.pointer(at("move", { x: top.x, y: top.y + 3 }, { button: 0 }));
    s.handles.pointer(at("up", { x: top.x, y: top.y + 3 }));
    expect(run).not.toHaveBeenCalled();
    // the azimuth turned, the command failed
    const az = of(handlesOf(X, C, HALF), "azimuth");
    expect(s.handles.pointer(at("down", screen(pointAt(az, 90))))).toBe(true);
    s.handles.pointer(at("move", screen(pointAt(az, 120)), { button: 0 }));
    expect(layer.posed).not.toBeNull();
    s.handles.pointer(at("up", screen(pointAt(az, 120))));
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith({ swath_id: 5, azimuth: 120, dip: 0, roll: 0 });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(layer.posed).toBeNull();
    expect(s.target()!.angles).toEqual({ azimuth: 90, dip: 0, roll: 0 });
    s.dispose();
  });

  it("a second drag begun before the answer of the first keeps its own angles; a failure of the window's calculation puts the box back", async () => {
    let answer: (ok: boolean) => void = () => undefined;
    const run = vi.fn(() => new Promise<boolean>((resolve) => (answer = resolve)));
    const s = new SwathScene(scene, { labels: () => labels, run });
    swathsStore.set({ selected: 5 });
    const layer = layers.get(swathLayerKey(5)) as SwathLayer;
    const az = of(handlesOf(X, C, HALF), "azimuth");
    const toward = (deg: number) => screen(pointAt(az, deg));
    s.handles.pointer(at("down", toward(90)));
    s.handles.pointer(at("move", toward(120), { button: 0 }));
    s.handles.pointer(at("up", toward(120)));
    expect(run).toHaveBeenCalledTimes(1);
    const first = answer;
    // the second drag from the held angles, before the first answer
    expect(s.handles.pointer(at("down", toward(120)))).toBe(true);
    s.handles.pointer(at("move", toward(150), { button: 0 }));
    expect(dragStore.get().drag).toEqual({ id: 5, azimuth: 150, dip: 0, roll: 0 });
    first(false); // the first failed: it lets nothing go — the second drag is on
    await Promise.resolve();
    await Promise.resolve();
    expect(dragStore.get().drag).toEqual({ id: 5, azimuth: 150, dip: 0, roll: 0 });
    expect(layer.posed).not.toBeNull();
    s.handles.pointer(at("up", toward(150)));
    expect(run).toHaveBeenCalledTimes(2);
    answer(true); // the command went: the window computes the new angles
    await Promise.resolve();
    await Promise.resolve();
    expect(dragStore.get().drag).toBeNull();
    expect(s.target()!.angles).toEqual({ azimuth: 150, dip: 0, roll: 0 });
    // the window took them and its calculation was refused (too_many_swaths): the box comes back, nothing held
    const turned = { ...settings, direction: "angles" as const, azimuth: 150 };
    liveStore.set({ live: { 5: { settings: turned, result, error: { text: "Слишком много полос", param: "width" } } } });
    expect(layer.posed).toBeNull();
    expect(s.target()!.angles).toEqual({ azimuth: 150, dip: 0, roll: 0 }); // the angles of the window's settings
    s.dispose();
  });

  it("an old failure of the window does not let go a pose held after it", () => {
    const s = new SwathScene(scene, { labels: () => labels, run: () => new Promise<boolean>(() => undefined) });
    const failed = { text: "Слишком много полос", param: "width" };
    liveStore.set({ live: { 5: { settings, result, error: failed } } });
    swathsStore.set({ selected: 5 });
    const layer = layers.get(swathLayerKey(5)) as SwathLayer;
    const az = of(handlesOf(X, C, HALF), "azimuth");
    s.handles.pointer(at("down", screen(pointAt(az, 90))));
    s.handles.pointer(at("move", screen(pointAt(az, 120)), { button: 0 }));
    s.handles.pointer(at("up", screen(pointAt(az, 120))));
    liveStore.set({ live: { 5: { settings: { ...settings, direction: "angles", azimuth: 120 }, result, error: failed } } });
    expect(layer.posed).not.toBeNull();
    expect(s.target()!.angles).toEqual({ azimuth: 120, dip: 0, roll: 0 });
    s.dispose();
  });

  it("without a window: the release saves, opens the window, and the 3D does not ask for the new angles itself", async () => {
    liveStore.set({ live: {} });
    windowStore.set({ windows: [] });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        calls.push(`${init?.method ?? "GET"} ${path}`);
        if (path === "/charts/5") return new Response(JSON.stringify({ ...row, ...(JSON.parse(String(init?.body)) as object), version: 2 }), { status: 200, headers: { "Content-Type": "application/json" } });
        if (path === "/stats/swath") return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
        return new Response(JSON.stringify({ code: "x", message: "нет", hint: "" }), { status: 404 });
      }),
    );
    const s = new SwathScene(scene, { labels: () => labels });
    await vi.waitFor(() => expect(layers.has(swathLayerKey(5))).toBe(true)); // drawn by its id: one request
    expect(calls).toEqual(["POST /stats/swath"]);
    calls.length = 0;
    await runSwath(createApp(), { swath_id: 5, azimuth: 45, dip: 0, roll: 0 }, "user");
    expect(calls).toEqual(["PUT /charts/5"]); // saved; the window opened computes it (one request)
    expect(windowStore.get().windows.map((w) => [w.kind, w.props?.swathId])).toEqual([["swath", 5]]);
    expect(swathsStore.get().swaths[0].version).toBe(2);
    await Promise.resolve();
    expect(calls).toEqual(["PUT /charts/5"]);
    s.dispose();
    setCoreTarget(null);
    windowStore.set({ windows: [] });
  });
});
