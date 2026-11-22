// The ellipsoids of the trends in the main 3D (ТЗ 7.35 Д-15; ADR 0022 §9–10; Э16 16.20): a stored trend shown (the
// view key `ellipsoids`) — a layer in its colour, a1 = size, a2 = size·r2, a3 = size·r3 at its centre (none — the
// centre of the view when first shown, kept); an open window «Тренд» — the layer of its edit as typed, with the
// handles of `viz/ellipsoid` (the chain of the scene, the key `trend:handles`), its stored layer stepping aside; a drag
// of a knob changes the size and the ratios, of the ring — ang1, into the form of the window in the same frame. The
// trends are the real answers of the core of 16.16. A fake scene.
import * as THREE from "three";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readTrend } from "../src/modules/trends/api";
import { editLayerKey, TrendScene, trendLayerKey } from "../src/modules/trends/scene";
import { editOf, ELLIPSOIDS_KEY, newEdit, setEdit, TREND_WINDOW, trendsStore, valuesOf } from "../src/modules/trends/state";
import { viewStore } from "../src/modules/scene/bind";
import { closeWindow, openWindow, windowStore } from "../src/shell/windows";
import { projectMatrix, projectRay, type ToolPointer } from "../src/viz/core/toolInput";
import type { Vec3 } from "../src/viz/core/vec3";
import { lookFrom } from "../src/viz/core/views";
import { handlesOf, pointAt, type ArcHandle, type KnobHandle } from "../src/viz/ellipsoid/drag";
import { EllipsoidHandles } from "../src/viz/ellipsoid/handle";
import { EllipsoidLayer } from "../src/viz/ellipsoid/layer";
import { projectToScreen, viewProjection, type Mat4 } from "../src/viz/pick/ray";
import type { Scene } from "../src/viz/Scene";
import type { SceneLayer } from "../src/viz/sceneTypes";

const W = 800;
const H = 600;
const CENTRE: Vec3 = [5000123.25, 7200456.5, 312.125];
const ORIGIN = { x: 5000123, y: 7200456, z: 312 };
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
const cam = lookFrom([0, -1e-4, 1], { min: [-150, -150, -150], max: [150, 150, 150] }, opts);
const matrix: Mat4 = projectMatrix(viewProjection(cam, opts), ORIGIN, 1);
const screen = (p: Vec3) => {
  const s = projectToScreen(matrix, p[0], p[1], p[2], W, H)!;
  return { x: s[0], y: s[1] };
};
// the real answers of the core (16.16)
const MANUAL = { id: 5, name: "Вручную", kind: "global", ang1: 17.0, ang2: -3.0, ang3: 0.0, ratios: [1.0, 0.3, 0.07], size: 100.0, centre: CENTRE, color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: [[0.29197101946266824, 0.9549941735309524, -0.05233595624294437], [-3.187682519876785, 0.9745723490757893, 0.0], [0.2185936106434886, 0.7149874837571574, 14.266136210779624]], version: 1, created_at: "2026-11-22T00:26:28.180727Z", updated_at: "2026-11-22T00:26:28.180727Z" }; // prettier-ignore
const NO_CENTRE = { id: 6, name: "Тренд", kind: "global", ang1: 12.5, ang2: 0.0, ang3: 0.0, ratios: [1.0, 1.0, 1.0], size: 100.0, centre: null, color: "#1f6bff", origin: { type: "ellipsoid" }, used: false, matrix: [[0.21643961393810288, 0.9762960071199334, 0.0], [-0.9762960071199334, 0.21643961393810288, 0.0], [0.0, 0.0, 1.0]], version: 1, created_at: "2026-11-22T00:26:28.186942Z", updated_at: "2026-11-22T00:26:28.186942Z" }; // prettier-ignore

let layers: Map<string, SceneLayer>;
let slot: unknown;
let target: Vec3;
let scene: Scene;
const canvas = { style: { cursor: "" }, getBoundingClientRect: () => ({ left: 0, top: 0 }) };
const at = (type: ToolPointer["type"], p: { x: number; y: number }, o: { shift?: boolean; ctrl?: boolean } = {}): ToolPointer => ({ type, x: p.x, y: p.y, clientX: p.x, clientY: p.y, button: 0, shift: !!o.shift, ctrl: !!o.ctrl });

beforeEach(() => {
  layers = new Map();
  canvas.style.cursor = "";
  target = [100, 200, 300];
  windowStore.set({ windows: [] });
  viewStore.set({ [ELLIPSOIDS_KEY]: undefined });
  trendsStore.set({ trends: [readTrend(MANUAL), readTrend(NO_CENTRE)], loaded: true, selected: null, edits: {}, dragging: null });
  scene = {
    clipPlanes: [],
    resolution: new THREE.Vector2(W, H),
    canvas,
    addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
    removeLayer: (k: string) => void layers.delete(k),
    invalidate: () => undefined,
    layerChanged: vi.fn(),
    onRestored: () => () => true,
    setHandles: (h: unknown) => void (slot = h),
    cursorRay: (px: number, py: number) => projectRay(cam, opts, { w: W, h: H }, ORIGIN, 1, px, py),
    projectView: () => ({ matrix, width: W, height: H }),
    worldCamera: () => ({ position: [0, 0, 1000], target, up: [0, 1, 0], zoom: 100, ortho: true, fov: 45 }),
  } as unknown as Scene;
});

function openEdit(trendId: number | null): string {
  const t = trendsStore.get().trends.find((x) => x.id === trendId);
  const win = openWindow(TREND_WINDOW, "Тренд", "form", { trendId, n: Math.random() });
  setEdit(win, t ? newEdit({ trendId, values: valuesOf(t), origin: t.origin }) : newEdit());
  return win;
}

describe("the trends shown in 3D", () => {
  it("a stored trend of the view key: its layer, colour, axes and centre; hidden — gone", () => {
    const s = new TrendScene(scene);
    expect(slot).toBe(s.handles);
    expect(layers.size).toBe(0);
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 5, visible: true }, { object: 99, visible: true }] }); // 99 — no such trend (a model)
    expect([...layers.keys()]).toEqual([trendLayerKey(5)]);
    const [st] = s.stats();
    expect(st.key).toBe("trend:5");
    expect(st.centre).toEqual(CENTRE);
    expect(st.color).toBe("#f46a1a");
    expect(st.ranges[0]).toBe(100);
    expect(st.ranges[1]).toBeCloseTo(30, 12);
    expect(st.ranges[2]).toBeCloseTo(7, 12);
    expect(st.angles).toEqual([17, -3, 0]);
    expect(s.target()).toBeNull(); // no window: no handles
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 5, visible: false }] });
    expect(layers.size).toBe(0);
    s.dispose();
  });

  it("without a centre: the centre of the view when first shown, kept while the view moves", () => {
    const s = new TrendScene(scene);
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 6, visible: true }] });
    expect(s.stats()[0]).toMatchObject({ key: "trend:6", centre: [100, 200, 300], color: "#1f6bff" });
    target = [900, 900, 900];
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 6, visible: true }, { object: 5, visible: true }] });
    expect(s.stats().find((x) => x.key === "trend:6")!.centre).toEqual([100, 200, 300]);
    s.dispose();
  });

  it("an open window: the layer of its edit as typed (the stored one steps aside), built once, the form follows", () => {
    const s = new TrendScene(scene);
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 5, visible: true }] });
    const win = openEdit(5);
    expect([...layers.keys()].sort()).toEqual(["trend:handles", editLayerKey(win)]);
    expect(layers.get("trend:handles")).toBeInstanceOf(EllipsoidHandles);
    const layer = layers.get(editLayerKey(win)) as EllipsoidLayer;
    setEdit(win, (e) => ({ values: { ...e.values, ang1: "45", r2: "0,5" } }));
    expect(layers.get(editLayerKey(win))).toBe(layer); // new matrices of the same layer
    expect(layer.pose(0).frame.ang1).toBeCloseTo(45, 12);
    expect(layer.pose(0).ranges.a2).toBeCloseTo(50, 12);
    // a wrong value: not drawn until fixed
    setEdit(win, (e) => ({ values: { ...e.values, r2: "0" } }));
    expect(layers.has(editLayerKey(win))).toBe(false);
    setEdit(win, (e) => ({ values: { ...e.values, r2: "0,5", color: "#1f6bff" } }));
    expect(s.stats().find((x) => x.key === editLayerKey(win))!.color).toBe("#1f6bff");
    // «Эллипсоид в 3D» off: nothing of the trend (its stored layer stays aside while the window is open)
    setEdit(win, { show: false });
    expect(layers.size).toBe(0);
    closeWindow(win);
    expect([...layers.keys()]).toEqual([trendLayerKey(5)]);
    s.dispose();
    expect(slot).toBeNull();
  });

  it("the handles of the window: a knob changes the size and the ratios, the ring ang1 — into the form", () => {
    const s = new TrendScene(scene);
    const win = openEdit(5);
    const t = s.target()!;
    expect(t).toMatchObject({ id: win, index: 0, min: 0 });
    expect(s.focused()).toBe(true);
    const knob = handlesOf(t.model.angles, t.model.ranges, CENTRE).find((h) => h.kind === "a2") as KnobHandle;
    const on = (r: number) => screen([CENTRE[0] + knob.axis[0] * r, CENTRE[1] + knob.axis[1] * r, CENTRE[2] + knob.axis[2] * r]);
    expect(s.handles.pointer(at("down", on(30)))).toBe(true);
    expect(trendsStore.get().dragging).toBe(win);
    s.handles.pointer(at("move", on(60)));
    expect(editOf(win)!.values).toMatchObject({ size: "100", r2: "0,6", r3: "0,07", ang1: "17" });
    expect(editOf(win)!.dirty).toBe(true);
    s.handles.pointer(at("up", on(60)));
    expect(trendsStore.get().dragging).toBeNull();
    // the knob of a1: the size, a2 and a3 keep their metres — the ratios follow
    const t2 = s.target()!;
    const k1 = handlesOf(t2.model.angles, t2.model.ranges, CENTRE).find((h) => h.kind === "a1") as KnobHandle;
    const on1 = (r: number) => screen([CENTRE[0] + k1.axis[0] * r, CENTRE[1] + k1.axis[1] * r, CENTRE[2] + k1.axis[2] * r]);
    s.handles.pointer(at("down", on1(100)));
    s.handles.pointer(at("move", on1(120)));
    s.handles.pointer(at("up", on1(120)));
    expect(editOf(win)!.values).toMatchObject({ size: "120", r2: "0,5", r3: "0,0583333333333" });
    // the ring: ang1, step 1°
    const t3 = s.target()!;
    const ring = handlesOf(t3.model.angles, t3.model.ranges, CENTRE).find((h) => h.kind === "ang1") as ArcHandle;
    s.handles.pointer(at("down", screen(pointAt(ring, 200))));
    s.handles.pointer(at("move", screen(pointAt(ring, 230.3))));
    s.handles.pointer(at("up", screen(pointAt(ring, 230.3))));
    expect(editOf(win)!.values.ang1).toBe("47");
    s.dispose();
  });
});
