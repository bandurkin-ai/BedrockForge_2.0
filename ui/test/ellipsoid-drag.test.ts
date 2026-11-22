// The handles of an anisotropy ellipsoid (ТЗ 7.35 Д-14; ADR 0022 §9; ТД14-1, ТД14-2): the ring of ang1, the arcs of
// ang2 and ang3, the knobs of a1–a3; a handle ↔ its GSLIB angle, a drag changes only its own parameter, steps of
// 1° and 5° (`Shift`), ranges in three significant digits not below L / 2, `Ctrl` — all three in one ratio; a hit
// within 8 px, else the pointer goes on; the vertical major axis; the response well inside 50 ms.
import { describe, expect, it } from "vitest";
import { projectMatrix } from "../src/viz/core/toolInput";
import { add, scale, type Vec3 } from "../src/viz/core/vec3";
import { lookFrom } from "../src/viz/core/views";
import {
  alongAxis,
  angleOn,
  dragAngle,
  dragModel,
  dragRange,
  handlesOf,
  hitHandle,
  isArc,
  pointAt,
  roundRange,
  sameModel,
  type ArcHandle,
  type Handle,
  type HandleKind,
  type HitView,
  type KnobHandle,
  type Model,
} from "../src/viz/ellipsoid/drag";
import { ellipsoidBasis, frameOf, type Angles, type Ranges } from "../src/viz/ellipsoid/frame";
import { projectToScreen, viewProjection } from "../src/viz/pick/ray";
import type { Ray } from "../src/viz/section/math";

const close = (a: readonly number[], b: readonly number[], tol: number) => a.forEach((x, i) => expect(Math.abs(x - b[i])).toBeLessThanOrEqual(tol));

// UTM-sized absolute coordinates: float64 throughout
const CENTRE: Vec3 = [512_100, 7_100_010, 250];
const ANGLES: Angles = { ang1: 30, ang2: -20, ang3: 15 };
const RANGES: Ranges = { a1: 120, a2: 60, a3: 20 };
const MODEL: Model = { angles: ANGLES, ranges: RANGES };

const byKind = <K extends HandleKind>(hs: Handle[], k: K) => hs.find((h) => h.kind === k)! as K extends "a1" | "a2" | "a3" ? KnobHandle : ArcHandle;

/** A ray along the normal of an arc onto its point at an angle. */
const rayOnArc = (h: ArcHandle, deg: number): Ray => ({ origin: add(pointAt(h, deg), scale(h.axis, 500)), dir: scale(h.axis, -1) });
/** A ray across an axis onto its point at the distance s from the centre. */
const rayOnAxis = (h: KnobHandle, s: number, across: Vec3): Ray => ({ origin: add(add(h.centre, scale(h.axis, s)), scale(across, 500)), dir: scale(across, -1) });

describe("six handles ↔ the GSLIB angles (ТД14-1)", () => {
  const hs = handlesOf(ANGLES, RANGES, CENTRE);
  const f = frameOf(ANGLES);

  it("ring, two arcs, three knobs", () => {
    expect(hs.map((h) => h.kind)).toEqual(["ang1", "ang2", "ang3", "a1", "a2", "a3"]);
    expect(hs.filter(isArc).length).toBe(3);
  });

  it("each arc's knob reads back its angle, and lies on the axis it turns", () => {
    for (const h of hs.filter(isArc)) expect(angleOn(h, rayOnArc(h, h.at))).toBeCloseTo(h.at, 9);
    const ring = byKind(hs, "ang1");
    const dip = byKind(hs, "ang2");
    const roll = byKind(hs, "ang3");
    const hor = Math.hypot(f.u1[0], f.u1[1]);
    close(pointAt(ring, ring.at), add(CENTRE, scale([f.u1[0] / hor, f.u1[1] / hor, 0], ring.radius)), 1e-9);
    close(pointAt(dip, dip.at), add(CENTRE, scale(f.u1, dip.radius)), 1e-9);
    close(pointAt(roll, roll.at), add(add(CENTRE, scale(f.u1, RANGES.a1)), scale(f.u2, roll.radius)), 1e-9);
  });

  it("the knobs are the ends of the axes: the columns of the ellipsoid matrix", () => {
    const m = ellipsoidBasis(f, RANGES);
    (["a1", "a2", "a3"] as const).forEach((k, j) => close(byKind(hs, k).at, add(CENTRE, [m[j], m[3 + j], m[6 + j]]), 1e-9));
  });

  it("an arc after a drag shows the new angle where the frame of the new angles puts it", () => {
    for (const kind of ["ang1", "ang2", "ang3"] as const) {
      const h = byKind(hs, kind);
      const next = dragAngle(ANGLES, h, rayOnArc(h, h.at), rayOnArc(h, h.at + 12))!;
      const h2 = byKind(handlesOf(next, RANGES, CENTRE), kind);
      expect(h2.at).toBe(next[kind]);
      expect(angleOn(h2, rayOnArc(h2, h2.at))).toBeCloseTo(next[kind], 9);
    }
  });
});

describe("a drag changes only its own parameter (ТД14-2)", () => {
  const hs = handlesOf(ANGLES, RANGES, CENTRE);

  it("each arc: whole degrees, 5° with Shift, the rest as it was", () => {
    for (const kind of ["ang1", "ang2", "ang3"] as const) {
      const h = byKind(hs, kind);
      const m = dragModel(MODEL, h, rayOnArc(h, h.at), rayOnArc(h, h.at + 7.4))!;
      expect(m.angles[kind]).toBe(ANGLES[kind] + 7);
      expect(m.ranges).toEqual(RANGES);
      for (const other of ["ang1", "ang2", "ang3"] as const) if (other !== kind) expect(m.angles[other]).toBe(ANGLES[other]);
      const s = dragAngle(ANGLES, h, rayOnArc(h, h.at), rayOnArc(h, h.at + 7.4), true)!;
      expect(Math.abs(s[kind] % 5)).toBe(0);
      expect(s[kind]).toBe(Math.round((ANGLES[kind] + 7.4) / 5) * 5);
    }
  });

  it("less than half a step keeps the angle; a turn back goes back", () => {
    const h = byKind(hs, "ang1");
    expect(dragAngle(ANGLES, h, rayOnArc(h, h.at), rayOnArc(h, h.at + 0.4))).toEqual(ANGLES);
    expect(dragAngle(ANGLES, h, rayOnArc(h, h.at), rayOnArc(h, h.at - 45))!.ang1).toBe(345);
  });

  it("ranges of the angles: ang1 wraps, ang2 stops at ±90°, ang3 wraps to (−180; 180]", () => {
    const at = (a: Angles, kind: "ang1" | "ang2" | "ang3", by: number) => {
      const h = byKind(handlesOf(a, RANGES, CENTRE), kind);
      return dragAngle(a, h, rayOnArc(h, h.at), rayOnArc(h, h.at + by))!;
    };
    expect(at({ ang1: 350, ang2: 0, ang3: 0 }, "ang1", 15).ang1).toBe(5);
    expect(at({ ang1: 0, ang2: 80, ang3: 0 }, "ang2", 30).ang2).toBe(90);
    expect(at({ ang1: 0, ang2: -80, ang3: 0 }, "ang2", -30).ang2).toBe(-90);
    expect(at({ ang1: 0, ang2: 0, ang3: 170 }, "ang3", 20).ang3).toBe(-170);
  });

  it("a plane seen edge-on gives no value", () => {
    const h = byKind(hs, "ang1");
    const along: Ray = { origin: add(CENTRE, [0, -1000, 0]), dir: [0, 1, 0] };
    expect(dragAngle(ANGLES, h, rayOnArc(h, h.at), along)).toBeNull();
  });

  it("a knob: the range by the move along its axis, three significant digits, only it", () => {
    const f = frameOf(ANGLES);
    const k1 = byKind(hs, "a1");
    const m = dragModel(MODEL, k1, rayOnAxis(k1, 120, f.u2), rayOnAxis(k1, 157.38, f.u2))!;
    expect(m.ranges).toEqual({ a1: 157, a2: 60, a3: 20 });
    expect(m.angles).toEqual(ANGLES);
    const k3 = byKind(hs, "a3");
    expect(dragRange(RANGES, k3, rayOnAxis(k3, 20, f.u1), rayOnAxis(k3, 22.3456, f.u1))).toEqual({ a1: 120, a2: 60, a3: 22.3 });
    // grabbed off the knob: the move counts, not where the press was
    expect(dragRange(RANGES, k1, rayOnAxis(k1, 100, f.u3), rayOnAxis(k1, 110, f.u3))!.a1).toBe(130);
  });

  it("not below L / 2; Ctrl — all three in one ratio", () => {
    const f = frameOf(ANGLES);
    const k2 = byKind(hs, "a2");
    expect(dragRange(RANGES, k2, rayOnAxis(k2, 60, f.u1), rayOnAxis(k2, -500, f.u1), { min: 12.5 })!.a2).toBe(12.5);
    const k1 = byKind(hs, "a1");
    expect(dragRange(RANGES, k1, rayOnAxis(k1, 120, f.u2), rayOnAxis(k1, 240, f.u2), { ctrl: true })).toEqual({ a1: 240, a2: 120, a3: 40 });
    const low = dragRange(RANGES, k1, rayOnAxis(k1, 120, f.u2), rayOnAxis(k1, 10, f.u2), { ctrl: true, min: 10 })!;
    expect(low.a3).toBe(10);
    expect(low.a1 / low.a3).toBeCloseTo(6, 12);
  });

  it("an axis along the ray gives no value", () => {
    const k1 = byKind(hs, "a1");
    const f = frameOf(ANGLES);
    expect(alongAxis(k1, { origin: add(CENTRE, scale(f.u1, 1000)), dir: scale(f.u1, -1) })).toBeNull();
  });

  it("roundRange keeps three significant digits", () => {
    expect([roundRange(1234.5), roundRange(0.012345), roundRange(99.96)]).toEqual([1230, 0.0123, 100]);
  });
});

describe("the vertical major axis", () => {
  const V: Angles = { ang1: 40, ang2: 90, ang3: 25 };
  const hs = handlesOf(V, RANGES, CENTRE);

  it("the ring still turns ang1 and the circle of ang3 still turns a2 about a1", () => {
    const ring = byKind(hs, "ang1");
    expect(dragAngle(V, ring, rayOnArc(ring, ring.at), rayOnArc(ring, ring.at + 10))).toEqual({ ang1: 50, ang2: 90, ang3: 25 });
    const roll = byKind(hs, "ang3");
    close(roll.axis, [0, 0, 1], 1e-12);
    const r = dragAngle(V, roll, rayOnArc(roll, roll.at), rayOnArc(roll, roll.at - 30))!;
    expect(r).toEqual({ ang1: 40, ang2: 90, ang3: -5 });
    close(pointAt(byKind(handlesOf(r, RANGES, CENTRE), "ang3"), -5), add(add(CENTRE, [0, 0, RANGES.a1]), scale(frameOf(r).u2, roll.radius)), 1e-9);
  });
});

// a view from above, a little to the south, 800 × 600, the scene origin of UTM
const W = 800;
const H = 600;
const ORIGIN = { x: 512_000, y: 7_100_000, z: 0 };
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
const cam = lookFrom([0.2, -0.5, 1], { min: [-100, -150, 100], max: [300, 170, 400] }, opts);
const view: HitView = { matrix: projectMatrix(viewProjection(cam, opts), ORIGIN, 1), width: W, height: H };
const screen = (p: Vec3) => projectToScreen(view.matrix, p[0], p[1], p[2], W, H)!;

describe("a hit within 8 px", () => {
  const hs = handlesOf(ANGLES, RANGES, CENTRE);

  it("each knob and each arc's knob is hit as itself", () => {
    for (const h of hs) {
      const p = isArc(h) ? pointAt(h, h.at + (h.kind === "ang1" ? 90 : 0)) : h.at;
      const [x, y] = screen(p);
      expect(hitHandle(hs, view, x + 3, y - 2)).toBe(h.kind);
    }
  });

  it("far from every handle — none: the pointer goes on", () => {
    expect(hitHandle(hs, view, 2, 2)).toBeNull();
    const [x, y] = screen(CENTRE);
    expect(hitHandle(hs, view, x, y)).toBeNull();
  });
});

describe("the response (≤ 50 ms per edit, ADR 0022 §9)", () => {
  it("handles, a drag and the matrix of the ellipsoid: a thousand edits well inside 50 ms each", () => {
    const f = frameOf(ANGLES);
    let m: Model = MODEL;
    const t0 = performance.now();
    for (let i = 0; i < 1000; i++) {
      const hs = handlesOf(m.angles, m.ranges, CENTRE);
      const h = hs[i % 6];
      const next = isArc(h) ? dragModel(m, h, rayOnArc(h, h.at), rayOnArc(h, h.at + 1)) : dragModel(m, h, rayOnAxis(h, h.range, f.u1[2] > 0.9 ? [1, 0, 0] : [0, 0, 1]), rayOnAxis(h, h.range + 0.5, f.u1[2] > 0.9 ? [1, 0, 0] : [0, 0, 1]));
      if (next && !sameModel(next, m)) m = next;
      ellipsoidBasis(frameOf(m.angles), m.ranges);
    }
    expect((performance.now() - t0) / 1000).toBeLessThan(1);
  });
});
