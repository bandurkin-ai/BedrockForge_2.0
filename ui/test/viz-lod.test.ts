import { describe, expect, it } from "vitest";
import { boundingSphere, boxFromBbox6, scaleZ, type Box3 } from "../src/viz/core/bounds";
import { applyTriangleBudget, GpuMemory, gpuClass, lossFactor, MEMORY_BUDGET, TRIANGLE_BUDGET, type BudgetPart } from "../src/viz/core/budget";
import { boxInFrustum, cullBoxes, frustumPlanes, screenPriority } from "../src/viz/core/cull";
import { chooseLevel, DOWNHOLE_STEPS, isSimplified, screenRadius, shownLevel, stepBoundaries, triangleBoundaries } from "../src/viz/core/lod";
import { lookFrom, view, type ViewOptions } from "../src/viz/core/views";

const ortho: ViewOptions = { aspect: 1.6, perspective: false, fov: 45, vexag: 1 };
const persp: ViewOptions = { ...ortho, perspective: true };

describe("level of detail (ADR 0004 §3)", () => {
  const b = [100, 20]; // three levels: ≥ 100 px — 0, ≥ 20 px — 1, below — 2

  it("picks by the measure with a 20 % hysteresis about the current level", () => {
    expect(chooseLevel(150, b, null)).toBe(0);
    expect(chooseLevel(50, b, null)).toBe(1);
    expect(chooseLevel(5, b, null)).toBe(2);
    // From level 1 the finer level needs 120 px, the coarser one below 16 px.
    expect(chooseLevel(110, b, 1)).toBe(1);
    expect(chooseLevel(121, b, 1)).toBe(0);
    expect(chooseLevel(17, b, 1)).toBe(1);
    expect(chooseLevel(15, b, 1)).toBe(2);
    // From level 0 it stays down to 80 px.
    expect(chooseLevel(81, b, 0)).toBe(0);
    expect(chooseLevel(79, b, 0)).toBe(1);
    expect(chooseLevel(3, b, 0)).toBe(2);
    expect(chooseLevel(Infinity, b, 2)).toBe(0);
    expect(chooseLevel(NaN, b, 1)).toBe(1);
    // No flicker: a measure going round a boundary keeps its level.
    let level = chooseLevel(100, b, null);
    for (const m of [99, 101, 95, 105, 90, 110, 85]) level = chooseLevel(m, b, level);
    expect(level).toBe(0);
  });

  it("wireframes: at most 2 triangles per pixel of the disc; displays: merge steps 4 and 32 m at ≤ 2 px", () => {
    const tb = triangleBoundaries([64_000, 8_000, 1_000]);
    expect(tb.length).toBe(2);
    expect(tb[0]).toBeCloseTo(Math.sqrt(64_000 / (2 * Math.PI)), 9);
    expect(chooseLevel(tb[0] + 1, tb, null)).toBe(0);
    expect(chooseLevel(tb[1] - 1, tb, null)).toBe(2);
    const sb = stepBoundaries(DOWNHOLE_STEPS);
    expect(sb).toEqual([0.5, 2 / 32]);
    expect(chooseLevel(1, sb, null)).toBe(0); // 1 px per metre: full detail
    expect(chooseLevel(0.2, sb, null)).toBe(1); // 4 m is 0.8 px
    expect(chooseLevel(0.01, sb, null)).toBe(2); // 32 m is 0.3 px
    expect(isSimplified(1)).toBe(true);
    expect(isSimplified(0)).toBe(false);
  });

  it("measures the sphere on screen; inside a perspective camera — the finest", () => {
    const box: Box3 = { min: [-500, -500, -100], max: [500, 500, 100] };
    const cam = view("top", box, ortho);
    const s = boundingSphere(box);
    expect(screenRadius(s, cam, ortho, 800)).toBeCloseTo((s.radius * 800) / (2 * cam.half), 9);
    const p = view("iso", box, persp);
    expect(screenRadius({ center: p.position, radius: 10 }, p, persp, 800)).toBe(Infinity);
    const far = screenRadius({ center: p.target, radius: 10 }, p, persp, 800);
    const near = screenRadius({ center: [...p.target.map((v, i) => (v + p.position[i]) / 2)] as typeof p.target, radius: 10 }, p, persp, 800);
    expect(near).toBeGreaterThan(far);
  });

  it("shows the wanted level, else the nearest coarser loaded, else a finer one", () => {
    expect(shownLevel(0, [0, 2])).toBe(0);
    expect(shownLevel(0, new Set([1, 2]))).toBe(1);
    expect(shownLevel(1, [0, 2])).toBe(2);
    expect(shownLevel(2, [0, 1])).toBe(1);
    expect(shownLevel(1, [])).toBeNull();
  });
});

describe("budgets (ADR 0004 §3)", () => {
  it("knows the class of graphics by the renderer string", () => {
    const cases: [string, string][] = [
      ["ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) Direct3D11 vs_5_0 ps_5_0, D3D11)", "integrated"],
      ["ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)", "integrated"],
      ["ANGLE (AMD, AMD Radeon(TM) Vega 8 Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)", "integrated"],
      ["ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 vs_5_0 ps_5_0, D3D11)", "integrated"],
      ["ANGLE (Intel, Intel(R) Arc(TM) Graphics (0x00007D55) Direct3D11 vs_5_0 ps_5_0, D3D11)", "integrated"],
      ["ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Laptop GPU (0x00002560) Direct3D11 vs_5_0 ps_5_0, D3D11)", "discrete"],
      ["ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)", "discrete"],
      ["ANGLE (AMD, Radeon RX Vega Direct3D11 vs_5_0 ps_5_0, D3D11)", "discrete"],
      ["ANGLE (Intel, Intel(R) Arc(TM) A770 Graphics (0x000056A0) Direct3D11 vs_5_0 ps_5_0, D3D11)", "discrete"],
      ["ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)", "software"],
      ["ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)", "software"],
      ["llvmpipe (LLVM 15.0.7, 256 bits)", "software"],
      ["", "integrated"],
    ];
    for (const [s, cls] of cases) expect(gpuClass(s), s).toBe(cls);
    expect(gpuClass(null)).toBe("integrated");
    expect(TRIANGLE_BUDGET).toEqual({ integrated: 8e6, discrete: 30e6, software: 1e6 });
    expect(MEMORY_BUDGET.integrated).toBe(1.5 * 1024 ** 3);
    expect(MEMORY_BUDGET.discrete).toBe(4 * 1024 ** 3);
  });

  it("keeps triangles within the budget by coarsening the smallest parts on screen, «упрощено»", () => {
    const tri = [4_000_000, 500_000, 62_500];
    const parts: BudgetPart[] = [
      { key: "big", screen: 900, triangles: tri, level: 0 },
      { key: "mid", screen: 300, triangles: tri, level: 0 },
      { key: "small", screen: 20, triangles: tri, level: 0 },
    ];
    const ok = applyTriangleBudget(parts, 30e6);
    expect(ok).toEqual({ levels: [0, 0, 0], triangles: 12e6, simplified: false, over: false });
    const r = applyTriangleBudget(parts, 8e6);
    expect(r.levels).toEqual([0, 1, 2]); // the small part to its coarsest first, then the middle one a step
    expect(r.triangles).toBe(4_000_000 + 500_000 + 62_500);
    expect(r.simplified).toBe(true);
    expect(r.over).toBe(false);
    const sw = applyTriangleBudget(parts, 1e6);
    expect(sw.levels).toEqual([1, 2, 2]);
    expect(sw.triangles).toBe(500_000 + 2 * 62_500);
    expect(sw.over).toBe(false);
    expect(applyTriangleBudget(parts, 100_000)).toMatchObject({ levels: [2, 2, 2], over: true, simplified: true });
    expect(applyTriangleBudget([{ key: "x", screen: 1, triangles: [10], level: 5 }], 5)).toMatchObject({ levels: [0], over: true, simplified: false });
  });

  it("halves the budgets after three lost contexts in a minute", () => {
    expect(lossFactor([0, 10_000], 20_000)).toBe(1);
    expect(lossFactor([0, 10_000, 50_000], 55_000)).toBe(0.5);
    expect(lossFactor([0, 10_000, 50_000], 70_001)).toBe(1);
  });

  it("evicts by age: first parts out of view, then finer levels; never the coarsest", () => {
    const mb = 1 << 20;
    const m = new GpuMemory(100 * mb);
    m.add("a", 2, 5 * mb, true, 1);
    m.add("a", 0, 30 * mb, false, 2);
    m.add("b", 2, 5 * mb, true, 3);
    m.add("b", 1, 20 * mb, false, 4);
    m.add("c", 1, 20 * mb, false, 5);
    m.add("c", 0, 15 * mb, false, 6);
    expect(m.used).toBe(95 * mb);
    m.setVisible(["a", "b"]);
    expect(m.makeRoom(5 * mb)).toEqual({ ok: true, evicted: [] });
    // 25 MB more: the hidden part "c" goes first, oldest level first.
    const r1 = m.makeRoom(25 * mb);
    expect(r1.ok).toBe(true);
    expect(r1.evicted.map((e) => `${e.part}${e.level}`)).toEqual(["c1"]);
    expect(m.used).toBe(75 * mb);
    // Then the finer levels of parts in view: level 0 before level 1.
    const r2 = m.makeRoom(55 * mb);
    expect(r2.evicted.map((e) => `${e.part}${e.level}`)).toEqual(["c0", "a0"]);
    // What cannot be freed is not evicted at all; the coarsest levels stay.
    const r3 = m.makeRoom(95 * mb);
    expect(r3).toEqual({ ok: false, evicted: [] });
    expect(m.has("a", 2) && m.has("b", 2) && m.has("b", 1)).toBe(true);
    // A lowered limit (lost contexts): trimming evicts what it can.
    m.limit = 8 * mb;
    const t = m.makeRoom(0);
    expect(t.ok).toBe(false);
    expect(t.evicted.map((e) => `${e.part}${e.level}`)).toEqual(["b1"]);
    expect(m.used).toBe(10 * mb);
    // Drawn this frame: kept.
    m.add("d", 0, 4 * mb, false, 100);
    expect(m.makeRoom(0, 100).evicted).toEqual([]);
    expect(m.removePart("a").length).toBe(1);
    expect(m.used).toBe(9 * mb);
  });
});

describe("culling by the view frustum", () => {
  const scene: Box3 = { min: [-1000, -1000, -500], max: [1000, 1000, 100] };

  it("keeps the parts in view and drops the ones beside or behind", () => {
    for (const opts of [ortho, persp]) {
      const cam = view("top", scene, opts);
      const planes = frustumPlanes(cam, opts);
      expect(planes.length).toBe(6);
      const at = (x: number, y: number, z = 0, r = 10): Box3 => ({ min: [x - r, y - r, z - r], max: [x + r, y + r, z + r] });
      expect(boxInFrustum(at(0, 0), planes)).toBe(true);
      expect(boxInFrustum(at(900, 900, -400), planes)).toBe(true);
      expect(boxInFrustum(at(1e5, 0), planes)).toBe(false);
      expect(boxInFrustum(at(0, -1e5), planes)).toBe(false);
      expect(boxInFrustum(at(0, 0, cam.position[2] + 100), planes)).toBe(false); // behind the camera
      expect(boxInFrustum(at(0, 0, cam.position[2] - cam.far - 100), planes)).toBe(false); // beyond far
      expect(boxInFrustum({ min: [-1e6, -5, -5], max: [1e6, 5, 5] }, planes)).toBe(true); // across the view
      expect(cullBoxes([at(0, 0), at(1e5, 0), at(500, -500)], planes)).toEqual([0, 2]);
    }
  });

  it("parts of the core (absolute bbox) in rendered space, loading from the screen centre out", () => {
    const origin = { x: 500_000, y: 7_000_000, z: 0 };
    const opts = { ...ortho, vexag: 2 };
    const parts = [
      [500_600, 7_000_600, -400, 500_900, 7_000_900, 100],
      [499_950, 6_999_950, -400, 500_050, 7_000_050, 100],
      [530_000, 7_000_000, -400, 530_100, 7_000_100, 100],
      [500_200, 7_000_100, -400, 500_300, 7_000_200, 100],
    ].map((b) => scaleZ(boxFromBbox6(b, origin), opts.vexag));
    const cam = lookFrom([0, -1e-4, 1], { min: [-1000, -1000, -400], max: [1000, 1000, 100] }, opts);
    const order = parts.map((b, i) => [screenPriority(b, cam, opts), i]).sort((a, b) => a[0] - b[0]).map(([, i]) => i);
    expect(order).toEqual([1, 3, 0, 2]);
    expect(screenPriority(parts[2], cam, opts)).toBeGreaterThan(1000);
  });
});
