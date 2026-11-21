// ТД01-2 (ТЗ 7.35 Д-01; ADR 0019 §5): the frame of the box of a swath plot in the window is the core's — read
// against the shared answers `core/tests/data/swath/frames.json` (made by `stats/swath_frame.py`): angles → axes,
// axes → angles, the number of the swath of a point by the projection of its centre, the corners and the centre of
// the cuboid. Cases `exact: true` — bit for bit (−0 equals 0); others — the tolerances of the file (axes 1e−12,
// angles 1e−9°, t 1e−9 relative). ТД01-5: an azimuth of 90° gives the swaths of X exactly.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Vec3 } from "../src/viz/core/vec3";
import { anglesOf, boxCentre, corners, directionFrame, frameOf, isVertical, normalizeAngles, swathIndex, toFrame, toWorld, type Direction, type Frame } from "../src/viz/swath/frame";

interface Angles {
  azimuth: number;
  dip: number;
  roll: number;
}
interface FrameCase {
  id: string;
  exact: boolean;
  direction: Direction;
  given: Angles;
  angles: Angles;
  axes: { u: Vec3; v: Vec3; w: Vec3 };
  vertical: boolean;
  back: Angles;
}
interface SwathCase {
  id: string;
  frame: string;
  exact: boolean;
  start: number;
  width: number;
  count: number;
  points: { p: Vec3; local: Vec3; k: number }[];
}
interface BoxCase {
  frame: string;
  exact: boolean;
  limits: { u: [number, number]; v: [number, number]; w: [number, number] };
  centre: Vec3;
  corners: Vec3[];
}

const cases = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/swath/frames.json"), "utf-8")) as {
  tolerance: { axes: number; angles: number; t: number };
  frames: FrameCase[];
  swaths: SwathCase[];
  boxes: BoxCase[];
};
const tol = cases.tolerance;

/** Bit for bit (−0 equals 0), or within `rel`·max(1, |want|). */
function same(got: readonly number[], want: readonly number[], exact: boolean, rel: number, what: string): void {
  expect(got.length, what).toBe(want.length);
  got.forEach((g, i) => {
    if (exact) expect(g === want[i], `${what}[${i}]: ${g} ≠ ${want[i]}`).toBe(true);
    else expect(Math.abs(g - want[i]), `${what}[${i}]: ${g} vs ${want[i]}`).toBeLessThanOrEqual(rel * Math.max(1, Math.abs(want[i])));
  });
}

/** Angles on the circle (azimuth and roll), within `tolerance.angles` degrees. */
function sameAngle(got: number, want: number, exact: boolean, what: string): void {
  if (exact) return void expect(got === want, `${what}: ${got} ≠ ${want}`).toBe(true);
  const d = Math.abs(((got - want + 540) % 360) - 180);
  expect(d, `${what}: ${got} vs ${want}`).toBeLessThanOrEqual(tol.angles);
}

const frameById = (id: string): Frame => {
  const c = cases.frames.find((f) => f.id === id)!;
  return directionFrame(c.direction, c.given.azimuth, c.given.dip, c.given.roll);
};

describe("ТД01-2: the frame of the box as the core's", () => {
  it("has the cases of the core", () => {
    expect(cases.frames.length).toBeGreaterThanOrEqual(20);
    expect(cases.swaths.length).toBeGreaterThanOrEqual(5);
    expect(cases.boxes.length).toBeGreaterThanOrEqual(3);
  });

  it.each(cases.frames.map((c) => [c.id, c] as const))("angles → axes → angles: %s", (_id, c) => {
    const f = directionFrame(c.direction, c.given.azimuth, c.given.dip, c.given.roll);
    expect([f.azimuth, f.dip, f.roll], `${c.id}: angles`).toEqual([c.angles.azimuth, c.angles.dip, c.angles.roll]);
    same(f.u, c.axes.u, c.exact, tol.axes, `${c.id}.u`);
    same(f.v, c.axes.v, c.exact, tol.axes, `${c.id}.v`);
    same(f.w, c.axes.w, c.exact, tol.axes, `${c.id}.w`);
    expect(isVertical(f), `${c.id}: vertical`).toBe(c.vertical);
    const [a, d, r] = anglesOf(f.u, f.v);
    sameAngle(a, c.back.azimuth, c.exact, `${c.id}: back azimuth`);
    expect(Math.abs(d - c.back.dip), `${c.id}: back dip`).toBeLessThanOrEqual(c.exact ? 0 : tol.angles);
    sameAngle(r, c.back.roll, c.exact, `${c.id}: back roll`);
    // the angles of the frame give its axes back (ТД01-2)
    const again = frameOf(a, d, r);
    same(again.u, f.u, false, 1e-9, `${c.id}: u again`);
    same(again.v, f.v, false, 1e-9, `${c.id}: v again`);
    // u = w × v, unit and orthogonal
    const cross: Vec3 = [f.w[1] * f.v[2] - f.w[2] * f.v[1], f.w[2] * f.v[0] - f.w[0] * f.v[2], f.w[0] * f.v[1] - f.w[1] * f.v[0]];
    same(cross, f.u, false, 1e-12, `${c.id}: w × v`);
  });

  it.each(cases.swaths.map((c) => [c.id, c] as const))("a point → its swath by the projection of its centre: %s", (_id, c) => {
    const f = frameById(c.frame);
    for (const [i, pt] of c.points.entries()) {
      const local = toFrame(f, pt.p);
      same(local, pt.local, c.exact, tol.t, `${c.id}#${i}: local`);
      expect(swathIndex(pt.local[0], c.start, c.width, c.count), `${c.id}#${i}: k`).toBe(pt.k);
      expect(swathIndex(local[0], c.start, c.width, c.count), `${c.id}#${i}: k of our t`).toBe(pt.k);
    }
  });

  it.each(cases.boxes.map((c) => [c.frame, c] as const))("the corners and the centre of the cuboid: %s", (_id, c) => {
    const f = frameById(c.frame);
    same(boxCentre(f, c.limits), c.centre, c.exact, tol.axes, `${c.frame}: centre`);
    corners(f, c.limits).forEach((p, i) => same(p, c.corners[i], c.exact, tol.axes, `${c.frame}: corner ${i}`));
  });

  it("ТД01-5: azimuth 90° is X bit for bit; the ranges of the angles", () => {
    const x = directionFrame("x");
    const a = frameOf(90, 0, 0);
    expect([a.u, a.v, a.w]).toEqual([x.u, x.v, x.w]);
    expect(x.u).toEqual([1, 0, 0]);
    const p: Vec3 = [512_012.5, 7_100_991, 244];
    expect(toFrame(a, p)[0]).toBe(512_012.5);
    expect(toWorld(x, toFrame(x, p))).toEqual(p);
    expect(normalizeAngles(-90, 0, -180)).toEqual([270, 0, 180]);
    expect(normalizeAngles(720, -0, 540)).toEqual([0, 0, 180]);
    expect(() => normalizeAngles(0, 91, 0)).toThrow(/Наклон/);
    expect(swathIndex(Number.NaN, 0, 1, 3)).toBe(-1);
  });
});
