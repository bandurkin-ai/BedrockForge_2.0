/**
 * «Зафиксировать вид» (Э10 10.15, ADR 0016 §4, Т8.1-6): the orthographic camera along v̂ with ŵ up; `]` moves the
 * camera and its target by (s_k′ − s_k)·v̂ — a point with the same (u, w) stays within 0.5 px on the screen through
 * 100 steps of a series on UTM (with the scene origin following the camera, as `Scene.draw`), the direction does not
 * change bit for bit; a turn does not change it either.
 */
import { describe, expect, it } from "vitest";
import { followSection, freeCamera, holdLock, lockedView, lockOn, type ViewLock } from "../src/viz/core/lockView";
import { rebaseCamera, SceneOrigin } from "../src/viz/core/sceneOrigin";
import { angle, dot, type Vec3 } from "../src/viz/core/vec3";
import { cameraBasis, project, viewDirection, type CameraState, type ViewOptions } from "../src/viz/core/views";
import { orbited } from "../src/viz/sceneHooks";
import { planePoint, type SectionPlane } from "../src/viz/section/plane";
import { PolylineGeometry, SeriesGeometry, seriesGeometry, type SeriesParams } from "../src/viz/section/series";

const W = 1600;
const H = 1000;
const opts = (vexag: number): ViewOptions => ({ aspect: W / H, perspective: false, fov: 45, vexag });

/** Screen px of a project point in a view whose scene origin is `o`. */
function screen(cam: CameraState, o: { x: number; y: number; z: number }, vexag: number, p: Vec3): [number, number] {
  const r = project(cam, opts(vexag), [p[0] - o.x, p[1] - o.y, (p[2] - o.z) * vexag])!;
  return [((r.x + 1) / 2) * W, ((1 - r.y) / 2) * H];
}

/** 100 steps `]` of a locked view: the largest shift of the points (u, w) on the screen, px, and the rebases. */
function walk(params: SeriesParams, vexag: number, uw: [number, number][]): { shift: number; rebases: number; lock: ViewLock; first: ViewLock } {
  const g = seriesGeometry(params) as SeriesGeometry;
  const origin = new SceneOrigin();
  const p0 = g.plane(0);
  const o = origin.ensure([p0.origin[0] - 50, p0.origin[1] - 50, p0.origin[2] - 50, p0.origin[0] + 50, p0.origin[1] + 50, p0.origin[2] + 50]);
  // any free camera first: an iso view at the start of the series, 1 px ≈ 0.4 m
  const start: CameraState = { position: [300, -400, 250], target: [3, 2, 1], up: [0, 0, 1], half: 200, near: 0.1, far: 1e5 };
  let { camera, lock } = lockOn(start, p0, o, vexag);
  const first = lock;
  const at = (k: number) => uw.map(([u, w]) => screen(camera, origin.value!, vexag, planePoint(g.plane(k), u, w)));
  const ref = at(0);
  let shift = 0;
  let rebases = 0;
  for (let k = 1; k <= 100; k++) {
    ({ camera, lock } = followSection(camera, lock, g.plane(k), origin.value!, vexag));
    const delta = origin.follow(camera.target, vexag);
    if (delta) {
      camera = rebaseCamera(camera, delta, vexag);
      rebases++;
    }
    for (const [i, s] of at(k).entries()) shift = Math.max(shift, Math.hypot(s[0] - ref[i][0], s[1] - ref[i][1]));
    for (let j = 0; j < 3; j++) {
      expect(Object.is(lock.dir[j], first.dir[j]), `dir after step ${k}`).toBe(true);
      expect(Object.is(lock.up[j], first.up[j]), `up after step ${k}`).toBe(true);
    }
    expect(angle(viewDirection(camera), first.dir)).toBeLessThan(1e-9);
  }
  return { shift, rebases, lock, first };
}

const UW: [number, number][] = [[0, 0], [137.25, -88.5], [-412.75, 230.125], [1500.5, -0.25]];

describe("«Зафиксировать вид» (Т8.1-6)", () => {
  it("a vertical series on UTM: 100 steps of 50 m — the points stay ≤ 0.5 px, the direction bit for bit (×3)", () => {
    const r = walk({ type: "vertical", p1: [512_345.678, 7_012_345.123, 0], az: 63, s0: 0, s1: 5000, step: 50, towards: 25, away: 25 }, 3, UW);
    expect(r.rebases).toBeGreaterThan(0); // the scene origin moved on the way
    expect(r.shift).toBeLessThanOrEqual(0.5);
    expect(r.shift).toBeLessThan(1e-3); // in fact far below
  });

  it("an inclined series at ×1 and a plan at ×5 keep the points and the direction too", () => {
    const inclined = walk({ type: "inclined", p1: [512_010.5, 7_012_020.25, 300], az: 30, phi: 60, s0: -200, s1: 4800, step: 50, towards: 25, away: 25 }, 1, UW);
    expect(inclined.shift).toBeLessThanOrEqual(0.5);
    const plan = walk({ type: "plan", p1: [512_000, 7_012_000, 400], az: 120, s0: 0, s1: 1000, step: 10, towards: 5, away: 5 }, 5, UW);
    expect(plan.shift).toBeLessThanOrEqual(0.5);
  });

  it("locks along v̂ with ŵ up: the target on the plane, the scale kept, orthographic axes ŝ / ŵ", () => {
    const g = seriesGeometry({ type: "inclined", p1: [1000, 2000, 300], az: 30, phi: 60, s0: 0, s1: 100, step: 20, towards: 10, away: 10 }) as SeriesGeometry;
    const pl = g.plane(2);
    const o = { x: 1000, y: 2000, z: 300 };
    const cam: CameraState = { position: [500, -800, 600], target: [10, 20, -5], up: [0, 0, 1], half: 123.5, near: 0.1, far: 5000 };
    const { camera, lock } = lockOn(cam, pl, o, 1);
    expect(camera.half).toBe(123.5);
    const b = cameraBasis(camera);
    expect(dot(b.z, pl.v)).toBeCloseTo(-1, 12);
    expect(dot(b.y, pl.w)).toBeCloseTo(1, 12);
    expect(dot(b.x, pl.s)).toBeCloseTo(1, 12);
    expect(camera.up).toEqual(lock.up);
    const t = dot(pl.v, [camera.target[0] + o.x - pl.origin[0], camera.target[1] + o.y - pl.origin[1], camera.target[2] + o.z - pl.origin[2]]);
    expect(Math.abs(t)).toBeLessThan(1e-9);
    // «Вписать» under the lock does not turn the view
    const fitted = lockedView(lock, { min: [-100, -100, -100], max: [100, 100, 100] }, opts(1), { plane: pl, origin: o });
    expect(angle(viewDirection(fitted), lock.dir)).toBeLessThan(1e-12);
    expect(fitted.up).toEqual(lock.up);
  });

  it("a turn under the lock does not change the direction; freed — Z up again", () => {
    const g = seriesGeometry({ type: "plan", p1: [0, 0, 100], az: 120, s0: 0, s1: 100, step: 10, towards: 5, away: 5 }) as SeriesGeometry;
    const { camera, lock } = lockOn({ position: [0, -500, 500], target: [0, 0, 0], up: [0, 0, 1], half: 50, near: 0.1, far: 5000 }, g.plane(0), { x: 0, y: 0, z: 0 }, 1);
    for (const rad of [0.3, -1.2, Math.PI]) {
      const held = holdLock(orbited(camera, rad), lock);
      expect(angle(viewDirection(held), lock.dir)).toBeLessThan(1e-12);
      expect(held.up).toEqual(lock.up);
      expect(held.target).toEqual(camera.target);
    }
    const free = freeCamera(camera);
    expect(free.up).toEqual([0, 0, 1]);
    expect(angle(viewDirection(free), lock.dir)).toBeLessThan(1e-3);
    expect(Math.hypot(viewDirection(free)[0], viewDirection(free)[1])).toBeGreaterThan(0);
  });

  it("another direction (the next segment of a polyline) locks anew along its v̂", () => {
    const g = seriesGeometry({ type: "polyline", polyline: [[500_000, 7_000_000], [500_300, 7_000_400], [500_500, 7_000_880]], corridor: 25 }) as PolylineGeometry;
    const o = { x: 500_000, y: 7_000_000, z: 0 };
    const a: SectionPlane = g.plane(0);
    const b: SectionPlane = g.plane(1);
    const first = lockOn({ position: [100, -100, 100], target: [0, 0, 0], up: [0, 0, 1], half: 80, near: 0.1, far: 5000 }, a, o, 2);
    const next = followSection(first.camera, first.lock, b, o, 2);
    expect(next.lock.plane).toBe(b);
    expect(dot(viewDirection(next.camera), b.v)).toBeCloseTo(-1, 12);
    expect(next.camera.half).toBe(80);
  });
});
