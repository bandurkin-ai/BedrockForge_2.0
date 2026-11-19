import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { windowTest } from "../src/viz/core/bounds";
import { dot, planeDistance, type Vec3 } from "../src/viz/core/vec3";
import { cameraBasis, view, viewDirection } from "../src/viz/core/views";
import { SharedClip } from "../src/viz/section/clip";
import { planeFromLine, planePoint } from "../src/viz/section/plane";
import { SectionRig } from "../src/viz/section/rig";
import { seriesGeometry, type SeriesGeometry } from "../src/viz/section/series";
import {
  azimuthDeg,
  clipPlanes,
  collarsXY,
  defaultSection,
  extentAlong,
  inCorridor,
  lineFromPoints,
  lineThroughHole,
  nextProfile,
  niceStep,
  niceTicks,
  sectionDirection,
  sectionNormal,
  shiftSection,
  toSection,
  viewPlanePoint,
  windowCorners,
  type SectionLine,
} from "../src/viz/section/math";

describe("section geometry (ported from BF viz.test.ts)", () => {
  it("makes round ticks", () => {
    expect(niceStep(100, 5)).toBe(20);
    expect(niceTicks(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(5, 5)).toEqual([]);
  });

  it("projects points on a section line", () => {
    const north = { x0: 100, y0: 200, az: 0, length: null, towards: 10, away: 10 };
    expect(toSection(100, 250, north)).toEqual({ u: 50, d: 0 });
    const p = toSection(105, 250, north); // 5 m east of a north-pointing line is to its right
    expect(p.u).toBeCloseTo(50);
    expect(p.d).toBeCloseTo(5);
    const east = { ...north, az: 90 };
    expect(toSection(160, 200, east).u).toBeCloseTo(60);
    expect(toSection(100, 190, east).d).toBeCloseTo(10); // south is to the right of an east-pointing line
  });

  it("selects holes by corridor and length", () => {
    const line = { x0: 0, y0: 0, az: 90, length: 100, towards: 10, away: 30 };
    // East-west line: the viewer is on the south side (to the right of the line).
    expect(inCorridor(50, -5, line)).toBe(true);
    expect(inCorridor(50, -15, line)).toBe(false); // beyond the window towards the viewer
    expect(inCorridor(50, 25, line)).toBe(true); // the window is deeper behind the plane
    expect(inCorridor(50, 35, line)).toBe(false);
    expect(inCorridor(150, 0, line)).toBe(false);
    expect(inCorridor(150, 0, { ...line, length: null })).toBe(true);
  });

  it("computes azimuths clockwise from north", () => {
    expect(azimuthDeg(0, 1)).toBe(0);
    expect(azimuthDeg(1, 0)).toBe(90);
    expect(azimuthDeg(0, -1)).toBe(180);
    expect(azimuthDeg(-1, 0)).toBe(270);
  });

  it("puts the default section across the main direction of the collars", () => {
    const eastWest: [number, number][] = [[0, 0], [100, 2], [200, -1], [300, 1]];
    const section = defaultSection(eastWest);
    expect(Math.abs(Math.sin((section.az * Math.PI) / 180))).toBeLessThan(0.05); // runs north-south
    expect(section.x0).toBeCloseTo(150);
    expect(defaultSection([]).az).toBe(90);
    expect(defaultSection([[5, 5]]).x0).toBe(5);
    expect(defaultSection([], 10, 40)).toMatchObject({ towards: 10, away: 40 });
  });

  it("steps a section across itself", () => {
    const east = { x0: 0, y0: 0, az: 90, length: null, towards: 10, away: 10 };
    const moved = shiftSection(east, 20);
    expect(moved.x0).toBeCloseTo(0);
    expect(moved.y0).toBeCloseTo(-20); // towards the viewer, who is south of an eastward line
  });
});

describe("section plan map (ported from BF sectionMap.test.ts)", () => {
  it("draws the window of a north-going line: away to the west, towards (viewer) to the east", () => {
    const c = windowCorners({ x0: 100, y0: 200, az: 0, length: 50, towards: 10, away: 5 }, 0);
    expect(c.map(([x, y]) => [Math.round(x), Math.round(y)])).toEqual([[95, 200], [95, 250], [110, 250], [110, 200]]);
  });
});

describe("sections in BF2 (ТЗ 7.14, ADR 0007 §4)", () => {
  const base: SectionLine = { x0: 512_345.678, y0: 7_012_345.125, az: 37.5, length: null, towards: 25, away: 15 };

  it("two points: from the first along the azimuth to the second, unlimited, window kept", () => {
    const a = { x: 500_000, y: 7_000_000 };
    expect(lineFromPoints(a, { x: 500_100, y: 7_000_100 }, base)).toEqual({ x0: 500_000, y0: 7_000_000, az: 45, length: null, towards: 25, away: 15 });
    expect(lineFromPoints(a, { x: 500_000, y: 6_999_000 })).toMatchObject({ az: 180, towards: 25, away: 25 });
    expect(lineFromPoints({ x: 10, y: 10 }, { x: 0, y: 10 })?.az).toBe(270);
    expect(lineFromPoints(a, { ...a })).toBeNull();
  });

  it("`]` moves the line exactly towards + away away from the viewer, `[` back towards", () => {
    const step = base.towards + base.away;
    const next = nextProfile(base, 1);
    const d = toSection(next.x0, next.y0, base);
    expect(Math.abs(d.d + step)).toBeLessThan(1e-9); // the coordinates are ~1e7 m: ulp 1e-9
    expect(Math.abs(d.u)).toBeLessThan(1e-9);
    expect(next).toMatchObject({ az: base.az, length: null, towards: 25, away: 15 });
    const back = nextProfile(base, -1);
    expect(Math.abs(toSection(back.x0, back.y0, base).d - step)).toBeLessThan(1e-9);
    // On the axes the step is exact in float64.
    const north: SectionLine = { ...base, az: 0, x0: 500_000.25, y0: 7_000_000.5 };
    expect(nextProfile(north, 1)).toEqual({ ...north, x0: 500_000.25 - 40, y0: 7_000_000.5 });
    expect(nextProfile(nextProfile(north, 1), -1)).toEqual(north);
    const east: SectionLine = { ...north, az: 90 };
    expect(nextProfile(east, 1)).toEqual({ ...east, y0: 7_000_000.5 + 40 });
    // Ten profiles on: ten windows, without drift.
    let p = base;
    for (let i = 0; i < 10; i++) p = nextProfile(p, 1);
    expect(Math.abs(toSection(p.x0, p.y0, base).d + 10 * step)).toBeLessThan(1e-8);
  });

  it("through a hole: collar and plan azimuth; a vertical hole (NaN) keeps the azimuth", () => {
    const vertical = lineThroughHole({ x: 500_010, y: 7_000_020 }, NaN, base);
    expect(vertical).toEqual({ ...base, x0: 500_010, y0: 7_000_020, length: null });
    expect(lineThroughHole({ x: 1, y: 2 }, Number.NaN, null).az).toBe(90);
    // An inclined hole dipping to azimuth 123.4: the whole trace lies in the plane.
    const az = Math.fround(123.4); // plan_az comes as float32
    const collar = { x: 512_000.5, y: 7_011_000.25 };
    const line = lineThroughHole(collar, az, base);
    expect(line).toMatchObject({ x0: collar.x, y0: collar.y, az, length: null, towards: 25, away: 15 });
    const dir = sectionDirection(az);
    for (const md of [0, 50, 137.5, 600]) {
      const h = md * Math.cos((60 * Math.PI) / 180); // dip 60°
      const d = toSection(collar.x + dir[0] * h, collar.y + dir[1] * h, line);
      expect(Math.abs(d.d)).toBeLessThan(1e-9);
      expect(d.u).toBeCloseTo(h, 9);
    }
    expect(lineThroughHole(collar, 370, base).az).toBe(10);
  });

  it("the automatic section runs across the strike of the collars (west–east → az ≈ 0/180)", () => {
    const collar = new Float64Array(3 * 40);
    for (let i = 0; i < 40; i++) {
      collar[3 * i] = 500_000 + i * 50;
      collar[3 * i + 1] = 7_000_000 + ((i * 7919) % 13) - 6;
      collar[3 * i + 2] = 300;
    }
    collar[3] = NaN; // a hole without a collar does not spoil the line
    const line = defaultSection(collarsXY(collar));
    expect(Math.min(Math.abs(line.az), Math.abs(line.az - 180), Math.abs(line.az - 360))).toBeLessThan(2);
    expect(line.x0).toBeGreaterThan(500_900);
    expect(line.x0).toBeLessThan(501_100);
    expect(Number.isFinite(line.y0)).toBe(true);
    // North–south collars give an east–west line.
    const ns = defaultSection([[0, 0], [1, 100], [-1, 200], [0, 300]]);
    expect(Math.abs(Math.cos((ns.az * Math.PI) / 180))).toBeLessThan(0.05);
  });

  it("clipping planes: 2, or 4 with a length, relative to the scene origin in float64", () => {
    const origin = { x: 512_000, y: 7_012_000 };
    const at = (u: number, d: number, z = 0): Vec3 => {
      const a = sectionDirection(base.az);
      const n = sectionNormal(base.az);
      return [base.x0 - origin.x + a[0] * u + n[0] * d, base.y0 - origin.y + a[1] * u + n[1] * d, z];
    };
    const inside = (p: Vec3, planes: ReturnType<typeof clipPlanes>) => planes.every((pl) => planeDistance(pl, p) >= 0);
    const two = clipPlanes(base, origin);
    expect(two.length).toBe(2);
    expect(inside(at(1e5, 24.999, -3000), two)).toBe(true);
    expect(inside(at(-50, -14.999, 900), two)).toBe(true);
    expect(inside(at(0, 25.001), two)).toBe(false);
    expect(inside(at(0, -15.001), two)).toBe(false);
    // The signed distances are the window distances in metres (float64, no float32 rounding).
    expect(planeDistance(two[0], at(10, 0))).toBeCloseTo(15, 9);
    expect(planeDistance(two[1], at(10, 0))).toBeCloseTo(25, 9);
    for (const pl of two) expect(pl.normal[2]).toBe(0);
    const four = clipPlanes({ ...base, length: 200 }, origin);
    expect(four.length).toBe(4);
    expect(inside(at(0.001, 0), four)).toBe(true);
    expect(inside(at(199.999, 0), four)).toBe(true);
    expect(inside(at(-0.001, 0), four)).toBe(false);
    expect(inside(at(200.001, 0), four)).toBe(false);
  });

  it("the line from edge to edge of the content", () => {
    const north: SectionLine = { x0: 0, y0: 0, az: 0, length: null, towards: 5, away: 5 };
    expect(extentAlong(north, -10, -100, 10, 300)).toEqual([-100, 300]);
    expect(extentAlong({ ...north, length: 50 }, -10, -100, 10, 300)).toEqual([0, 50]);
  });

  it("a click falls on the plane through the view centre: horizontal in plan, vertical from the side", () => {
    const target: Vec3 = [10, 20, -150];
    const fromAbove = viewPlanePoint({ origin: [40, 70, 500], dir: [0, 0, -1] }, target, [0, -1e-4, 1]);
    expect(fromAbove).toEqual([40, 70, -150]);
    const side = viewPlanePoint({ origin: [33, -500, -120], dir: [0, 1, 0] }, target, [0, -1, 0]);
    expect(side).toEqual([33, 20, -120]);
    expect(viewPlanePoint({ origin: [0, 0, 0], dir: [1, 0, 0] }, target, [0, 0, 1])).toBeNull();
    expect(viewPlanePoint({ origin: [0, 0, -1000], dir: [0, 0, -1] }, target, [0, 0, 1])).toBeNull(); // behind
  });
});

describe("the shared plane of the section in the window (Э10 10.14, ADR 0016 §4)", () => {
  const origin = { x: 512_000, y: 7_012_000, z: 100 };
  const line: SectionLine = { x0: 512_100.25, y0: 7_011_950.5, az: 63, length: null, towards: 15, away: 25 };
  const inclined = (seriesGeometry({ type: "inclined", p1: [512_010, 7_012_020, 300], az: 30, phi: 60, s0: 0, s1: 100, step: 20, towards: 10, away: 10 }) as SeriesGeometry).plane(2);
  const plan = (seriesGeometry({ type: "plan", p1: [512_000, 7_012_000, 400], s0: 0, s1: 100, step: 10, towards: 5, away: 5 }) as SeriesGeometry).plane(3);
  const gpuInside = (clip: SharedClip, p: Vec3) => clip.planes.every((pl) => pl.distanceToPoint(new THREE.Vector3(...p)) >= 0);

  it("a line of 7.14: the planes on the video card are those of `clipPlanes` at any exaggeration, «Вписать» as windowTest", () => {
    for (const length of [null, 200]) {
      const l = { ...line, length };
      const clip = new SharedClip();
      for (const e of [1, 5]) {
        clip.update(planeFromLine(l), true, origin, e);
        const old = clipPlanes(l, origin);
        expect(clip.planes.length).toBe(old.length);
        clip.planes.forEach((pl, i) => {
          expect([pl.normal.x, pl.normal.y, pl.normal.z]).toEqual(old[i].normal);
          expect(pl.constant).toBe(old[i].constant);
        });
      }
      const test = clip.boxTest()!;
      const legacy = windowTest(l, true, origin)!;
      for (let i = 0; i < 200; i++) {
        const x = ((i * 37) % 120) - 60;
        const y = ((i * 53) % 140) - 70;
        const box = { min: [x - 3, y - 2, -50] as Vec3, max: [x + 3, y + 2, 50] as Vec3 };
        expect(test(box)).toBe(legacy(box));
      }
    }
    const off = new SharedClip();
    off.update(planeFromLine(line), false, origin);
    expect(off.active).toBe(false);
    expect(off.boxTest()).toBeNull();
    expect(off.inWindow(1e9, 0, 0)).toBe(true);
  });

  it("an inclined section at ×1 and ×5 and a plan at ×5: the video card and inWindow(x, y, z) keep the same points", () => {
    for (const pl of [inclined, plan]) {
      for (const e of [1, 5]) {
        const clip = new SharedClip();
        clip.update(pl, true, origin, e);
        expect(clip.planes.length).toBe(2);
        let inside = 0;
        for (let i = 0; i < 300; i++) {
          const t = ((i * 29) % 41) - 20.5;
          const p = planePoint(pl, ((i * 37) % 101) - 50, ((i * 53) % 89) - 44);
          const q: Vec3 = [p[0] + t * pl.v[0] - origin.x, p[1] + t * pl.v[1] - origin.y, p[2] + t * pl.v[2] - origin.z];
          const want = t >= -pl.towards && t <= pl.away;
          expect(clip.inWindow(q[0], q[1], q[2]), `t = ${t}`).toBe(want);
          expect(gpuInside(clip, [q[0], q[1], q[2] * e]), `×${e}, t = ${t}`).toBe(want);
          if (want) inside++;
        }
        expect(inside).toBeGreaterThan(50);
      }
    }
  });

  it("the rig: ×1 while an inclined section is active, the frame of the view, the trace while clipping is off", () => {
    const rig = new SectionRig();
    rig.setLine(line, true);
    expect(rig.line).toEqual(line);
    expect(rig.vexag(5)).toBe(5);
    rig.set(inclined, true);
    expect(rig.line).toBeNull();
    expect(rig.vexag(5)).toBe(1);
    rig.set(plan, false);
    expect(rig.vexag(5)).toBe(5);
    rig.refresh(origin, { min: [-100, -100, -500], max: [100, 100, 400] }, 5);
    expect(rig.clip.active).toBe(false);
    expect(rig.overlay.group.children.length).toBe(1);
    expect(rig.frame(null)).toBeNull();
    rig.set(null, true);
    rig.refresh(origin, null, 1);
    expect(rig.overlay.group.children.length).toBe(0);
    expect(rig.clipOn).toBe(false);
    rig.dispose();
  });

  it("the «section» view looks along v̂ with ŵ up: a line as before, an inclined section, a plan with its azimuth", () => {
    const box = { min: [-200, -200, -300] as Vec3, max: [200, 200, 300] as Vec3 };
    const opts = { aspect: 1.5, perspective: false, fov: 45, vexag: 1 };
    const before = view("section", box, opts, { line, origin });
    const now = view("section", box, opts, { plane: planeFromLine(line), origin });
    for (const k of ["position", "target", "up"] as const) for (let i = 0; i < 3; i++) expect(now[k][i]).toBeCloseTo(before[k][i], 9);
    expect(now.half).toBeCloseTo(before.half, 9);
    const rotated = (seriesGeometry({ type: "plan", p1: [512_000, 7_012_000, 400], az: 120, s0: 0, s1: 50, step: 10, towards: 5, away: 5 }) as SeriesGeometry).plane(1);
    for (const pl of [inclined, rotated]) {
      const cam = view("section", box, opts, { plane: pl, origin });
      const b = cameraBasis(cam);
      expect(dot(viewDirection(cam), pl.v)).toBeCloseTo(-1, 6);
      expect(dot(b.y, pl.w)).toBeCloseTo(1, 6);
      expect(dot(b.x, pl.s)).toBeCloseTo(1, 6);
      // centred on the plane t = 0
      const t = dot(pl.v, [cam.target[0] + origin.x - pl.origin[0], cam.target[1] + origin.y - pl.origin[1], cam.target[2] + origin.z - pl.origin[2]]);
      expect(Math.abs(t)).toBeLessThan(1e-6);
    }
  });
});
