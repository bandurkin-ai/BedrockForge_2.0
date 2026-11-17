import { describe, expect, it } from "vitest";
import { boxFromBbox6, contentBox, fitBox, scaleZ, windowTest, type Box3 } from "../src/viz/core/bounds";
import { TURN_MS, TurnController, easeOutCubic } from "../src/viz/core/turn";
import { angle, distance, dot, sub, type Vec3 } from "../src/viz/core/vec3";
import {
  cameraBasis,
  changeVexag,
  fit,
  fitDepth,
  lookFrom,
  orthoFrustum,
  pixelsPerUnit,
  project,
  screenRay,
  toPlan,
  view,
  viewDirection,
  type CameraState,
  type ViewOptions,
} from "../src/viz/core/views";
import { sectionDirection, sectionNormal, type SectionLine } from "../src/viz/section/math";
import { VizError } from "../src/viz/error";

const ortho: ViewOptions = { aspect: 16 / 9, perspective: false, fov: 45, vexag: 1 };
const persp: ViewOptions = { ...ortho, perspective: true };
const box: Box3 = { min: [-800, -300, -450], max: [1200, 500, 120] };
const close = (a: Vec3, b: Vec3, eps = 1e-9) => expect(distance(a, b)).toBeLessThan(eps);

describe("standard views (ТЗ 7.13)", () => {
  it("top: Y up, X right; front: Z up, X right; side: Z up, Y right; iso", () => {
    const top = cameraBasis(view("top", box, ortho));
    expect(dot(top.y, [0, 1, 0])).toBeGreaterThan(0.9999);
    expect(dot(top.x, [1, 0, 0])).toBeGreaterThan(0.9999);
    const front = cameraBasis(view("front", box, ortho));
    expect(dot(front.x, [1, 0, 0])).toBeCloseTo(1, 12);
    expect(dot(front.y, [0, 0, 1])).toBeCloseTo(1, 12);
    expect(dot(front.z, [0, -1, 0])).toBeCloseTo(1, 12); // looking north: the camera is south
    const side = cameraBasis(view("side", box, ortho));
    expect(dot(side.x, [0, 1, 0])).toBeCloseTo(1, 12);
    expect(dot(side.y, [0, 0, 1])).toBeCloseTo(1, 12);
    const iso = view("iso", box, ortho);
    expect(angle(viewDirection(iso), [0.9, -1.2, 0.8])).toBeLessThan(1e-12);
  });

  it("section: Z up, the line from left to right, the camera on the viewer's side", () => {
    const line: SectionLine = { x0: 512_100, y0: 7_011_950, az: 63, length: null, towards: 25, away: 25 };
    const origin = { x: 512_000, y: 7_012_000 };
    const cam = view("section", box, ortho, { line, origin });
    const b = cameraBasis(cam);
    expect(dot(b.x, sectionDirection(63))).toBeCloseTo(1, 12);
    expect(dot(b.y, [0, 0, 1])).toBeCloseTo(1, 12);
    expect(dot(viewDirection(cam), sectionNormal(63))).toBeCloseTo(1, 12);
    // The view is centred on the plane of the section.
    const n = sectionNormal(63);
    expect(n[0] * (cam.target[0] - (line.x0 - origin.x)) + n[1] * (cam.target[1] - (line.y0 - origin.y))).toBeCloseTo(0, 9);
    // A drawn line is held from end to end with its window.
    const limited = view("section", box, ortho, { line: { ...line, length: 3000 }, origin });
    expect(limited.half).toBeCloseTo(Math.max(3000 / 2 / ortho.aspect, (box.max[2] - box.min[2]) / 2) * 1.08, 6);
  });

  it("section without a line is an error in Russian", () => {
    expect(() => view("section", box, ortho)).toThrow(VizError);
    expect(() => view("section", box, ortho)).toThrow("Линия разреза не задана: сначала section.set");
  });

  it("sizes the orthographic view by the extent and the window proportions, with vexag", () => {
    const flat: Box3 = { min: [0, 0, -50], max: [1000, 200, 50] };
    const t = view("top", flat, { ...ortho, aspect: 2 });
    expect(t.half).toBeCloseTo(Math.max(100, 500 / 2) * 1.08, 3);
    const tall = view("top", flat, { ...ortho, aspect: 10 });
    expect(tall.half).toBeCloseTo(100 * 1.08, 1); // the top view is a hair off the vertical
    const f1 = view("front", flat, { ...ortho, aspect: 100 });
    const f3 = view("front", flat, { ...ortho, aspect: 100, vexag: 3 });
    expect(f1.half).toBeCloseTo(50 * 1.08, 9);
    expect(f3.half).toBeCloseTo(150 * 1.08, 9);
    const fr = orthoFrustum(t, 2);
    expect(fr.right - fr.left).toBeCloseTo(2 * t.half * 2, 9);
    expect(pixelsPerUnit(t, t.target, 800, ortho)).toBeCloseTo(800 / (2 * t.half), 12);
  });

  it("«В план» (T) keeps the centre and the scale, from any view; again changes nothing", () => {
    for (const opts of [ortho, persp]) {
      let cam = view("iso", box, opts);
      // The user panned and zoomed: the target moved off the content centre.
      cam = { ...cam, target: [cam.target[0] + 333.3, cam.target[1] - 12.5, cam.target[2] + 7], position: [cam.position[0] + 333.3, cam.position[1] - 12.5, cam.position[2] + 7], half: cam.half * 0.37 };
      const plan = toPlan(cam, box, opts);
      expect(plan.target).toEqual(cam.target);
      expect(plan.half).toBe(cam.half);
      expect(distance(plan.position, plan.target)).toBeCloseTo(distance(cam.position, cam.target), 9);
      const b = cameraBasis(plan);
      expect(dot(b.y, [0, 1, 0])).toBeGreaterThan(0.9999);
      expect(dot(b.x, [1, 0, 0])).toBeGreaterThan(0.9999);
      expect(toPlan(plan, box, opts)).toEqual(plan);
    }
  });

  it("«Вписать» does not turn the view", () => {
    for (const opts of [ortho, persp]) {
      const dirs: Vec3[] = [[0.3, -0.2, 0.93], [-1, 0.2, -0.4], [0, -1e-4, 1], [0.6, 0.8, 0]];
      for (const d of dirs) {
        const cam = lookFrom(d, box, opts);
        const moved: CameraState = { ...cam, position: [cam.position[0] + 5000, cam.position[1], cam.position[2]] };
        const before = viewDirection(moved);
        const after = fit(moved, box, opts);
        expect(angle(viewDirection(after), before)).toBeLessThan(1e-12);
      }
    }
  });

  it("projects and casts rays consistently, float64", () => {
    for (const opts of [ortho, persp]) {
      const cam = view("iso", box, opts);
      const p: Vec3 = [123.456, -78.9, -12.0005];
      const ndc = project(cam, opts, p)!;
      const ray = screenRay(cam, opts, ndc.x, ndc.y);
      const t = dot(sub(p, ray.origin), ray.dir);
      close([ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t, ray.origin[2] + ray.dir[2] * t], p, 1e-8);
    }
    const cam = view("iso", box, persp);
    expect(project(cam, persp, cam.position.map((v, i) => v + (cam.position[i] - cam.target[i])) as Vec3)).toBeNull();
  });

  it("near and far hold the content from wherever the camera looks", () => {
    const corners = (b: Box3) => Array.from({ length: 8 }, (_, i): Vec3 => [i & 1 ? b.max[0] : b.min[0], i & 2 ? b.max[1] : b.min[1], i & 4 ? b.max[2] : b.min[2]]);
    const holds = (cam: CameraState) => {
      const fwd = sub(cam.target, cam.position).map((v) => v / distance(cam.target, cam.position)) as Vec3;
      for (const c of corners(box)) {
        const depth = dot(sub(c, cam.position), fwd);
        expect(depth).toBeGreaterThanOrEqual(cam.near);
        expect(depth).toBeLessThanOrEqual(cam.far);
      }
    };
    // Orthographic: even from inside the content — the camera steps back, the picture is the same.
    const inside = view("iso", box, ortho);
    const near = { ...inside, position: inside.position.map((v, i) => inside.target[i] + (v - inside.target[i]) * 0.01) as Vec3 };
    const stepped = fitDepth(near, box, ortho);
    holds(stepped);
    expect(angle(viewDirection(stepped), viewDirection(near))).toBeLessThan(1e-12);
    expect(stepped.half).toBe(near.half);
    // Perspective: from outside, after a turn.
    const p = view("iso", box, persp);
    const turned = { ...p, position: [p.target[0] - (p.position[0] - p.target[0]), p.position[1], p.position[2]] as Vec3 };
    holds(fitDepth(turned, box, persp));
    expect(fitDepth(turned, null, persp)).toBe(turned);
  });

  it("a new vertical exaggeration keeps the data point in the centre and the direction", () => {
    const cam = view("front", box, ortho);
    const v3 = changeVexag(cam, 1, 3);
    expect(v3.target[2]).toBeCloseTo(cam.target[2] * 3, 9);
    expect(angle(viewDirection(v3), viewDirection(cam))).toBeLessThan(1e-12);
    expect(changeVexag(v3, 3, 1).target[2]).toBeCloseTo(cam.target[2], 9);
  });
});

describe("smooth turn (gizmo)", () => {
  it("turns in 260 ms with ease-out, keeps the centre and the scale; from above and below Y is up", () => {
    const turns = new TurnController();
    const start = view("iso", box, ortho);
    for (const [from, ySign] of [[[0, 0, 1], 1], [[0, 0, -1], 1]] as [Vec3, number][]) {
      const token = turns.begin(start, from, 1000, { ortho: true, box, vexag: 1 })!;
      expect(token).not.toBeNull();
      const total = angle(viewDirection(start), from);
      const mid = turns.frame(token, 1000 + TURN_MS / 2)!;
      expect(mid.done).toBe(false);
      expect(angle(viewDirection(start), viewDirection(mid.camera)) / total).toBeCloseTo(easeOutCubic(0.5), 2);
      const end = turns.frame(token, 1000 + TURN_MS)!;
      expect(end.done).toBe(true);
      expect(end.camera.target).toEqual(start.target);
      expect(end.camera.half).toBe(start.half);
      expect(distance(end.camera.position, end.camera.target)).toBeCloseTo(distance(start.position, start.target), 6);
      expect(end.camera.far).toBeGreaterThanOrEqual(start.far);
      const b = cameraBasis(end.camera);
      expect(dot(b.y, [0, ySign, 0])).toBeGreaterThan(0.9999);
      expect(Math.abs(dot(viewDirection(end.camera), from))).toBeGreaterThan(0.99999);
      expect(turns.frame(token, 2000)).toBeNull();
    }
  });

  it("a new turn interrupts the one under way; the same direction is no turn", () => {
    const turns = new TurnController();
    const cam = view("front", box, ortho);
    const t1 = turns.begin(cam, [1, 0, 0], 0)!;
    const half = turns.frame(t1, 100)!.camera;
    const t2 = turns.begin(half, [0, 0, 1], 100)!;
    expect(turns.frame(t1, 150)).toBeNull();
    expect(turns.frame(t2, 100 + TURN_MS)!.done).toBe(true);
    expect(turns.begin(cam, viewDirection(cam), 0)).toBeNull();
    // Half a turn (opposite direction) has an axis too.
    const t3 = turns.begin(cam, [0, 1, 0], 0)!;
    expect(angle(viewDirection(turns.frame(t3, TURN_MS)!.camera), [0, 1, 0])).toBeLessThan(1e-9);
    turns.cancel();
    expect(turns.active).toBe(false);
  });
});

describe("extent and the section window (BF windowTest)", () => {
  const origin = { x: 500_000, y: 7_000_000, z: 0 };
  const north: SectionLine = { x0: 500_000, y0: 7_000_000, az: 0, length: null, towards: 25, away: 25 };
  const parts = [
    { box: boxFromBbox6([499_990, 6_999_000, -500, 500_010, 7_001_000, 300], origin), visible: true },
    { box: boxFromBbox6([501_000, 6_997_000, -900, 501_200, 7_003_000, 300], origin), visible: true },
    { box: boxFromBbox6([499_000, 6_999_500, -2000, 499_960, 7_000_500, 0], origin), visible: false },
  ];

  it("counts only boxes reaching into the window when clipping", () => {
    const inWindow = windowTest(north, true, origin)!;
    expect(inWindow(parts[0].box)).toBe(true);
    expect(inWindow(parts[1].box)).toBe(false); // 1 km east, the window is 25 m
    expect(inWindow(boxFromBbox6([499_976, 0, 0, 499_976, 0, 0], origin))).toBe(true); // 24 m behind
    expect(inWindow(boxFromBbox6([499_974, 0, 0, 499_974, 0, 0], origin))).toBe(false);
    expect(windowTest(north, false, origin)).toBeNull();
    expect(windowTest(null, true, origin)).toBeNull();
    expect(fitBox(parts, inWindow)).toEqual(parts[0].box);
    expect(fitBox(parts, null)).toEqual({ min: [-10, -3000, -900], max: [1200, 3000, 300] });
  });

  it("falls back to everything, then to a default box", () => {
    const hidden = parts.map((p) => ({ ...p, visible: false }));
    expect(contentBox(hidden)?.min[0]).toBe(-1000);
    const inWindow = windowTest({ ...north, x0: 600_000 }, true, origin);
    expect(fitBox(parts, inWindow)).toEqual(contentBox(parts));
    expect(fitBox([], null)).toEqual({ min: [-50, -50, -50], max: [50, 50, 50] });
    expect(scaleZ(parts[0].box, -2).min[2]).toBe(-600);
  });

  it("fit with clipping holds only the objects in the window", () => {
    const inWindow = windowTest(north, true, origin);
    const opts = { ...ortho, aspect: 1 };
    const section = view("section", fitBox(parts, inWindow), opts, { line: north, origin });
    const all = view("section", fitBox(parts, null), opts, { line: north, origin });
    expect(section.half).toBeLessThan(all.half);
    expect(section.half).toBeCloseTo(1000 * 1.08, 6);
  });
});
