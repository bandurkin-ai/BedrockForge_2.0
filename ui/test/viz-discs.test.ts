// The discs of a structural trend in 3D (ТЗ 7.35 Д-15, 8.9.2; ADR 0026 решение 27; Э19 19.26): `viz/discs/build.ts` (the instance matrices
// over the unit circle: z = the upper unit normal, float32 relative to an integer origin — display only, `viz/origin.ts`) and
// `viz/discs/layer.ts` (an InstancedMesh, clipped by the section window, counted in «Вписать»); `modules/trends/discScene.ts` — the layers of the
// structural trends of the view key `ellipsoids`, read once per version, a size changed rebuilds, a colour changed repaints, a source that is
// gone says so. Three.js without a GL context (the layer makes objects only); a fake scene.
import * as THREE from "three";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readTrend, type Discs } from "../src/modules/trends/api";
import { DiscScene, discLayerKey, discRadius } from "../src/modules/trends/discScene";
import { ELLIPSOIDS_KEY, trendsStore } from "../src/modules/trends/state";
import { viewStore } from "../src/modules/scene/bind";
import { sceneUi } from "../src/modules/scene/sceneHost";
import { CoreError } from "../src/shell/api";
import { buildDiscs, MIN_RADIUS, planeFrame } from "../src/viz/discs/build";
import { DiscLayer, LOOK } from "../src/viz/discs/layer";
import { maxRoundTripError } from "../src/viz/origin";
import type { Scene } from "../src/viz/Scene";
import type { SceneLayer } from "../src/viz/sceneTypes";

const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (v: number[]) => v.map((x) => x / Math.hypot(...v));

describe("planeFrame", () => {
  it("u, v, n are a right-handed orthonormal frame (u × v = n), u horizontal unless the plane is", () => {
    for (const n of [[0, 0, 1], [1, 0, 0], [0, 1, 0], unit([1, 1, 1]), unit([0.6, 0, 0.8]), unit([-0.3, 0.2, 0.93]), unit([0.01, 0.01, 1])]) {
      const { u, v } = planeFrame(n[0], n[1], n[2]);
      expect(Math.hypot(...u)).toBeCloseTo(1, 12);
      expect(Math.hypot(...v)).toBeCloseTo(1, 12);
      expect(dot(u, v)).toBeCloseTo(0, 12);
      expect(dot(u, n)).toBeCloseTo(0, 12);
      expect(dot(v, n)).toBeCloseTo(0, 12);
      for (const [i, c] of cross(u, v).entries()) expect(c).toBeCloseTo(n[i], 12);
    }
    const { u } = planeFrame(...(unit([0.6, 0, 0.8]) as [number, number, number]));
    expect(u[2]).toBeCloseTo(0, 12); // the strike of a dipping plane is horizontal
  });
});

/** The matrix of disc `k` applied to a point of the unit circle (x, y, 0). */
function apply(b: ReturnType<typeof buildDiscs>, k: number, p: [number, number, number]): number[] {
  const m = b.matrices.subarray(k * 16, k * 16 + 16);
  return [0, 1, 2].map((i) => m[i] * p[0] + m[4 + i] * p[1] + m[8 + i] * p[2] + m[12 + i]);
}

const WORLD = { x: [5000123.25, 5000130.5, 5000140], y: [7200456.5, 7200460, 7200470], z: [312.125, 300, 290], nx: [0, 0.6, 0], ny: [0, 0, 0.8], nz: [1, 0.8, 0.6] };

describe("buildDiscs", () => {
  it("a disc per point: the normal is the axis, the centre is the point (float64 world → float32 from the integer origin, ≤ 1e-3 m)", () => {
    const b = buildDiscs(WORLD, 40);
    expect(b).toMatchObject({ count: 3, skipped: 0, radius: 40 });
    expect(b.origin).toEqual({ x: 5000132, y: 7200463, z: 301 }); // the integer centre of the extent
    const world = new Float64Array(9);
    const back = new Float32Array(9);
    for (let k = 0; k < 3; k++) {
      const m = b.matrices.subarray(k * 16, k * 16 + 16);
      const c = [WORLD.x[k], WORLD.y[k], WORLD.z[k]];
      world.set(c, k * 3);
      back.set([m[12], m[13], m[14]], k * 3);
      // the centre is the origin of the unit circle; the normal — its z axis; the rim — at the radius, in the plane
      for (const [i, v] of apply(b, k, [0, 0, 0]).entries()) expect(v + [b.origin.x, b.origin.y, b.origin.z][i]).toBeCloseTo(c[i], 3);
      const n = [WORLD.nx[k], WORLD.ny[k], WORLD.nz[k]];
      const tip = apply(b, k, [0, 0, 1]).map((v, i) => v - apply(b, k, [0, 0, 0])[i]);
      for (const [i, v] of tip.entries()) expect(v).toBeCloseTo(n[i], 6);
      for (const p of [[1, 0, 0], [0, 1, 0], [-0.6, 0.8, 0]] as [number, number, number][]) {
        const d = apply(b, k, p).map((v, i) => v - apply(b, k, [0, 0, 0])[i]);
        expect(Math.hypot(...d)).toBeCloseTo(40, 4);
        expect(dot(d, n)).toBeCloseTo(0, 4);
      }
    }
    expect(maxRoundTripError(world, back, b.origin)).toBeLessThan(1e-3);
    expect(b.bbox).toEqual([5000123.25 - 40, 7200456.5 - 40, 290 - 40, 5000140 + 40, 7200470 + 40, 312.125 + 40]);
  });

  it("a normal that is not of unit length is normalised; a point or a normal that is not a number or zero is left out and counted — never a disc at 0", () => {
    const b = buildDiscs({ x: [1, NaN, 3, 4, 5], y: [1, 2, 3, 4, 5], z: [1, 2, 3, 4, Infinity], nx: [0, 0, 0, 0, 0], ny: [0, 0, 0, NaN, 0], nz: [5, 1, 0, 1, 1] }, 10);
    expect(b).toMatchObject({ count: 1, skipped: 4 });
    const tip = apply(b, 0, [0, 0, 1]).map((v, i) => v - apply(b, 0, [0, 0, 0])[i]);
    expect(tip[2]).toBeCloseTo(1, 6);
    expect(buildDiscs({ x: [NaN], y: [1], z: [1], nx: [0], ny: [0], nz: [1] }, 10)).toMatchObject({ count: 0, skipped: 1, bbox: null });
    expect(buildDiscs({ x: [], y: [], z: [], nx: [], ny: [], nz: [] }, 10)).toMatchObject({ count: 0, skipped: 0, bbox: null, origin: { x: 0, y: 0, z: 0 } });
  });

  it("the radius is never zero or not a number; columns of different length are cut to the shortest", () => {
    expect(buildDiscs(WORLD, 0).radius).toBe(MIN_RADIUS);
    expect(buildDiscs(WORLD, NaN).radius).toBe(MIN_RADIUS);
    expect(buildDiscs({ ...WORLD, nz: [1, 0.8] }, 5).count).toBe(2);
  });

  it("50 000 discs (the limit of the core) are built at once", () => {
    const n = 50000;
    const col = (f: (i: number) => number) => Float64Array.from({ length: n }, (_, i) => f(i));
    const t0 = performance.now();
    const b = buildDiscs({ x: col((i) => 5000000 + (i % 500) * 3), y: col((i) => 7200000 + Math.floor(i / 500) * 3), z: col(() => 300), nx: col(() => 0.3), ny: col(() => 0.4), nz: col(() => 0.8660254037844386) }, 4);
    expect(b.count).toBe(n);
    expect(b.matrices.length).toBe(n * 16);
    expect(performance.now() - t0).toBeLessThan(500);
  });
});

describe("DiscLayer", () => {
  const clip = [new THREE.Plane(new THREE.Vector3(1, 0, 0), 0)];

  it("an instanced mesh: the instances of the build, the colour, both sides, clipped by the section window; counted in «Вписать»", () => {
    const b = buildDiscs(WORLD, 40);
    const layer = new DiscLayer(b, "#1f6bff", { clip });
    const mesh = layer.object.children[0] as THREE.InstancedMesh;
    expect(mesh.count).toBe(3);
    expect(Array.from((mesh.instanceMatrix.array as Float32Array).subarray(0, 48))).toEqual(Array.from(b.matrices));
    const mat = mesh.material as THREE.MeshBasicMaterial;
    expect(mat.side).toBe(THREE.DoubleSide);
    expect(mat.transparent).toBe(true);
    expect(mat.opacity).toBe(LOOK.opacity);
    expect(mat.clippingPlanes).toBe(clip);
    expect(layer.origin).toEqual(b.origin);
    expect(layer.bbox).toEqual(b.bbox);
    expect(layer.stats()).toMatchObject({ count: 3, skipped: 0, radius: 40, color: "#1f6bff" });
    layer.setColor("#f46a1a");
    expect(layer.stats().color).toBe("#f46a1a");
    const dispose = vi.spyOn(mat, "dispose");
    layer.dispose();
    expect(dispose).toHaveBeenCalled();
  });
});

// ---- the discs in the scene ----

const MEAS = { id: 11, name: "Замеры", kind: "structural", source: { type: "measurements", set_id: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR" }, source_info: { set_name: "Скважины", table_name: "Замеры", wireframe_name: null, lost: false }, source_lost: false, radius: 200.0, ratios: [1.0, 0.36, 0.05], size: 80.0, centre: null, color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: null, version: 1, created_at: null, updated_at: null }; // prettier-ignore
const GLOBAL = { id: 5, name: "Вручную", kind: "global", ang1: 17.0, ang2: -3.0, ang3: 0.0, ratios: [1.0, 0.3, 0.07], size: 100.0, centre: [1, 2, 3], color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], version: 1, created_at: null, updated_at: null }; // prettier-ignore
const DISCS: Discs = { radius: 200, n: 3, total: 3, thinned: false, x: WORLD.x, y: WORLD.y, z: WORLD.z, nx: WORLD.nx, ny: WORLD.ny, nz: WORLD.nz };

let layers: Map<string, SceneLayer>;
let scene: Scene;
let load: ReturnType<typeof vi.fn>;
const removed: string[] = [];
const flush = () => new Promise((r) => setTimeout(r, 0));
const show = (...ids: number[]) => viewStore.set({ [ELLIPSOIDS_KEY]: ids.map((object) => ({ object, visible: true })) });

beforeEach(() => {
  layers = new Map();
  removed.length = 0;
  sceneUi.set({ error: null });
  viewStore.set({ [ELLIPSOIDS_KEY]: undefined });
  trendsStore.set({ trends: [readTrend(MEAS), readTrend(GLOBAL)], loaded: true, selected: null, edits: {}, dragging: null, discs: {} });
  load = vi.fn(async () => structuredClone(DISCS));
  scene = {
    clipPlanes: [],
    addLayer: (k: string, l: SceneLayer) => void layers.set(k, l),
    removeLayer: (k: string) => void (layers.delete(k), removed.push(k)),
    invalidate: () => undefined,
    onRestored: () => () => true,
  } as unknown as Scene;
});

describe("the discs of the structural trends in the scene", () => {
  it("shown: read once, a layer `trend:discs:<id>` in the colour of the trend, the radius is half the size; hidden — gone; a global trend has none", async () => {
    const s = new DiscScene(scene, load);
    show(5, 11, 99); // 5 — global (its ellipsoid), 99 — not a trend
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith(11);
    expect([...layers.keys()]).toEqual([discLayerKey(11)]);
    expect(s.stats()).toMatchObject([{ id: 11, count: 3, radius: 40, color: "#f46a1a" }]);
    expect(discRadius(readTrend(MEAS))).toBe(40);
    expect(trendsStore.get().discs[11]).toEqual({ n: 3, total: 3, thinned: false, skipped: 0 });
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 11, visible: false }] });
    expect([...layers.keys()]).toEqual([]);
    expect(removed).toEqual([discLayerKey(11)]);
    expect(trendsStore.get().discs).toEqual({});
    show(11);
    await flush();
    expect(layers.size).toBe(1);
    s.dispose();
    expect(layers.size).toBe(0);
  });

  it("the colour changed repaints, the size changed rebuilds from what was read, a new version reads again; nothing is read twice", async () => {
    const s = new DiscScene(scene, load);
    show(11);
    await flush();
    const first = layers.get(discLayerKey(11));
    const t = trendsStore.get().trends;
    trendsStore.set({ trends: [{ ...t[0], color: "#1f6bff" }, t[1]] });
    expect(layers.get(discLayerKey(11))).toBe(first);
    expect(s.stats()[0].color).toBe("#1f6bff");
    trendsStore.set({ trends: [{ ...t[0], color: "#1f6bff", size: 20 }, t[1]] });
    expect(layers.get(discLayerKey(11))).not.toBe(first);
    expect(s.stats()[0].radius).toBe(10);
    expect(load).toHaveBeenCalledTimes(1);
    trendsStore.set({ trends: [{ ...t[0], color: "#1f6bff", size: 20, version: 2 }, t[1]] });
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
    expect(s.stats()).toHaveLength(1);
    s.dispose();
  });

  it("a source that is gone (409) says so in the scene and is not asked again until the trend is saved; a source lost in the list — not asked at all", async () => {
    load.mockRejectedValue(new CoreError(409, "source_gone", "Замеры структурного тренда удалены или недоступны.", "Выберите другой источник в окне тренда или удалите тренд."));
    const s = new DiscScene(scene, load);
    show(11);
    await flush();
    expect(layers.size).toBe(0);
    expect(sceneUi.get().error).toMatch(/Диски тренда «Замеры» не показаны в 3D: .*удалены или недоступны/);
    trendsStore.set({ discs: {} }); // an event of the store: no new read
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    const t = trendsStore.get().trends;
    load.mockResolvedValue(structuredClone(DISCS));
    trendsStore.set({ trends: [{ ...t[0], version: 2 }, t[1]] });
    await flush();
    expect(layers.size).toBe(1);
    s.dispose();
    const lost = new DiscScene(scene, load);
    load.mockClear();
    trendsStore.set({ trends: [{ ...t[0], sourceLost: true }, t[1]] });
    expect(layers.size).toBe(0);
    expect(load).not.toHaveBeenCalled();
    lost.dispose();
  });

  it("measurements without a position or a normal are counted; none at all — said once, no layer, no loop", async () => {
    load.mockResolvedValue({ ...DISCS, x: [NaN, NaN, NaN], thinned: true, total: 90000 });
    const s = new DiscScene(scene, load);
    show(11);
    await flush();
    expect(layers.size).toBe(0);
    expect(sceneUi.get().error).toMatch(/нет дисков для показа/);
    expect(trendsStore.get().discs[11]).toEqual({ n: 0, total: 90000, thinned: true, skipped: 3 });
    expect(load).toHaveBeenCalledTimes(1);
    s.dispose();
  });

  it("a restored context rebuilds the layers the scene dropped", async () => {
    let restore: () => void = () => undefined;
    (scene as unknown as { onRestored: (f: () => void) => () => boolean }).onRestored = (f) => ((restore = f), () => true);
    const s = new DiscScene(scene, load);
    show(11);
    await flush();
    layers.clear(); // the scene lost its layers with the context
    restore();
    expect(layers.size).toBe(1);
    expect(load).toHaveBeenCalledTimes(1);
    s.dispose();
  });
});

describe("the ellipsoids leave the structural trends alone", () => {
  it("a structural trend of the view key and the window of one have no ellipsoid layer (the discs are theirs); a global one still has", async () => {
    const { TrendScene } = await import("../src/modules/trends/scene");
    const { TREND_WINDOW, newEdit, setEdit, valuesOf } = await import("../src/modules/trends/state");
    const { editFieldsOf } = await import("../src/modules/trends/structural");
    const { openWindow, windowStore } = await import("../src/shell/windows");
    windowStore.set({ windows: [] });
    const ellipsoids = new Map<string, SceneLayer>();
    const fake = {
      clipPlanes: [],
      resolution: new THREE.Vector2(800, 600),
      canvas: { style: { cursor: "" }, getBoundingClientRect: () => ({ left: 0, top: 0 }) },
      addLayer: (k: string, l: SceneLayer) => void ellipsoids.set(k, l),
      removeLayer: (k: string) => void ellipsoids.delete(k),
      invalidate: () => undefined,
      layerChanged: () => undefined,
      onRestored: () => () => true,
      setHandles: () => undefined,
      worldCamera: () => ({ position: [0, 0, 1000], target: [100, 200, 300], up: [0, 1, 0], zoom: 100, ortho: true, fov: 45 }),
    } as unknown as Scene;
    const s = new TrendScene(fake);
    show(5, 11);
    expect([...ellipsoids.keys()]).toEqual(["trend:5"]);
    const t = trendsStore.get().trends[0];
    const win = openWindow(TREND_WINDOW, "Тренд", "form", { trendId: 11 });
    setEdit(win, newEdit({ ...editFieldsOf(t), show: true }));
    expect([...ellipsoids.keys()]).toEqual(["trend:5"]);
    const fresh = openWindow(TREND_WINDOW, "Тренд", "form", { trendId: null, n: 2 });
    setEdit(fresh, newEdit({ kind: "structural", values: valuesOf(t), show: true }));
    expect(s.items().map((i) => i.key)).toEqual(["trend:5"]);
    s.dispose();
  });
});
