// The frame of an anisotropy ellipsoid (ТЗ 8.6.3, 7.35 Д-14; ADR 0022 §6; ТД14-1, Т8.6-3): GSLIB angles ↔ the axes
// a1, a2, a3, the round trip without loss, the vertical major axis, the ellipsoid matrix over the unit sphere, the
// second notation of the plane a1–a2; against `core/tests/data/variography/frames.json` of `geostat/rotation.py` when
// it is there (written by the core lane, 16.6; skipped only in a checkout without it).
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Vec3 } from "../src/viz/core/vec3";
import {
  anglesOf,
  anglesOfFrame,
  anglesOfPlane,
  ellipsoidBasis,
  frameOf,
  normalizeAngles,
  planeAnglesOf,
  reducedDistance,
  rotationRows,
  type Angles,
} from "../src/viz/ellipsoid/frame";

const close = (a: readonly number[], b: readonly number[], tol: number) => {
  expect(a.length).toBe(b.length);
  a.forEach((x, i) => expect(Math.abs(x - b[i])).toBeLessThanOrEqual(tol));
};
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const angleClose = (a: Angles, b: Angles, tol: number) => {
  const d1 = Math.abs(((a.ang1 - b.ang1 + 540) % 360) - 180);
  const d3 = Math.abs(((a.ang3 - b.ang3 + 540) % 360) - 180);
  expect(d1).toBeLessThanOrEqual(tol);
  expect(Math.abs(a.ang2 - b.ang2)).toBeLessThanOrEqual(tol);
  expect(d3).toBeLessThanOrEqual(tol);
};

/** 8.6.3 literally, with Math.sin / Math.cos of radians — the reference of the exact form. */
function literal({ ang1, ang2, ang3 }: Angles): [Vec3, Vec3, Vec3] {
  const r = Math.PI / 180;
  const a = (ang1 < 270 ? 90 - ang1 : 450 - ang1) * r;
  const b = -ang2 * r;
  const t = ang3 * r;
  const [sa, ca, sb, cb, st, ct] = [Math.sin(a), Math.cos(a), Math.sin(b), Math.cos(b), Math.sin(t), Math.cos(t)];
  return [
    [cb * ca, cb * sa, -sb],
    [-ct * sa + st * sb * ca, ct * ca + st * sb * sa, st * cb],
    [st * sa + ct * sb * ca, -st * ca + ct * sb * sa, ct * cb],
  ];
}

const grid: Angles[] = [];
for (let ang1 = 0; ang1 < 360; ang1 += 23) for (let ang2 = -89; ang2 <= 89; ang2 += 17) for (let ang3 = -179; ang3 <= 180; ang3 += 29) grid.push({ ang1, ang2, ang3 });
for (const ang1 of [0, 90, 180, 270]) for (const ang2 of [-45, 0, 45]) for (const ang3 of [-90, 0, 90, 180]) grid.push({ ang1, ang2, ang3 });

describe("GSLIB angles → axes (Т8.6-3)", () => {
  it("zero angles give the axes (0; 1; 0), (−1; 0; 0), (0; 0; 1) exactly", () => {
    const f = frameOf({ ang1: 0, ang2: 0, ang3: 0 });
    expect(f.u1).toEqual([0, 1, 0]);
    expect(f.u2).toEqual([-1, 0, 0]);
    expect(f.u3).toEqual([0, 0, 1]);
  });

  it("ang1 = 90°, plunge 30° (ang2 = −30°): a1 = (0.8660254; 0; −0.5)", () => {
    close(frameOf({ ang1: 90, ang2: -30, ang3: 0 }).u1, [Math.sqrt(3) / 2, 0, -0.5], 1e-12);
  });

  it("multiples of 90° are exact and without −0", () => {
    const f = frameOf({ ang1: 270, ang2: 0, ang3: 90 });
    for (const u of [f.u1, f.u2, f.u3]) for (const x of u) expect([0, 1, -1]).toContain(x), expect(Object.is(x, -0)).toBe(false);
    expect(f.u1).toEqual([-1, 0, 0]);
  });

  it("the rows equal 8.6.3 literally, orthonormal and right-handed (1e−12)", () => {
    for (const a of grid) {
      const rows = rotationRows(a);
      const ref = literal(a);
      rows.forEach((u, i) => close(u, ref[i], 1e-12));
      const [u1, u2, u3] = rows;
      expect(Math.abs(dot(u1, u1) - 1)).toBeLessThan(1e-12);
      expect(Math.abs(dot(u1, u2))).toBeLessThan(1e-12);
      close(cross(u1, u2), u3, 1e-12);
    }
  });

  it("the angles are normalized; ang2 beyond ±90° is refused", () => {
    expect(normalizeAngles({ ang1: -90, ang2: 10, ang3: 270 })).toEqual({ ang1: 270, ang2: 10, ang3: -90 });
    expect(normalizeAngles({ ang1: 360, ang2: 0, ang3: -180 })).toEqual({ ang1: 0, ang2: 0, ang3: 180 });
    expect(() => frameOf({ ang1: 0, ang2: 91, ang3: 0 })).toThrow(RangeError);
    expect(() => frameOf({ ang1: Number.NaN, ang2: 0, ang3: 0 })).toThrow(/числа/);
  });
});

describe("axes → GSLIB angles: the round trip (ТД14-1)", () => {
  it("angles → axes → angles within 1e−9 over the grid", () => {
    for (const a of grid) angleClose(anglesOfFrame(frameOf(a)), normalizeAngles(a), 1e-9);
  });

  it("whole degrees come back as they were after rounding to 1e−9", () => {
    for (const a of grid) {
      const back = anglesOfFrame(frameOf(a));
      const n = normalizeAngles(a);
      expect(Math.round(back.ang2 * 1e9) / 1e9).toBe(n.ang2);
    }
    expect(anglesOfFrame(frameOf({ ang1: 90, ang2: 0, ang3: 180 }))).toEqual({ ang1: 90, ang2: 0, ang3: 180 });
  });

  it("a vertical major axis keeps ang1 and reads ang3 from a2", () => {
    for (const ang2 of [90, -90]) for (const ang1 of [0, 37, 200, 300]) for (const ang3 of [-150, -30, 0, 45, 180]) {
      const f = frameOf({ ang1, ang2, ang3 });
      expect(Math.abs(f.u1[2])).toBe(1);
      angleClose(anglesOf(f.u1, f.u2, ang1), { ang1, ang2, ang3 }, 1e-9);
    }
  });

  it("near the vertical (ang2 = 89.9999°) the round trip holds", () => {
    const a = { ang1: 123, ang2: 89.9999, ang3: 17 };
    angleClose(anglesOfFrame(frameOf(a)), a, 1e-7);
  });

  it("axes of any length are read the same", () => {
    const f = frameOf({ ang1: 40, ang2: -20, ang3: 10 });
    angleClose(anglesOf([f.u1[0] * 5, f.u1[1] * 5, f.u1[2] * 5], [f.u2[0] * 0.1, f.u2[1] * 0.1, f.u2[2] * 0.1]), f, 1e-9);
    expect(() => anglesOf([0, 0, 0], f.u2)).toThrow(RangeError);
  });
});

describe("the ellipsoid over the unit sphere", () => {
  it("the columns of the matrix are a1·u1, a2·u2, a3·u3; h_r = 1 on them", () => {
    const f = frameOf({ ang1: 30, ang2: -15, ang3: 40 });
    const r = { a1: 300, a2: 120, a3: 25 };
    const m = ellipsoidBasis(f, r);
    const col = (j: number): Vec3 => [m[j], m[3 + j], m[6 + j]];
    close(col(0), f.u1.map((x) => x * 300), 1e-12);
    close(col(1), f.u2.map((x) => x * 120), 1e-12);
    close(col(2), f.u3.map((x) => x * 25), 1e-12);
    for (let j = 0; j < 3; j++) expect(reducedDistance(f, r, col(j))).toBeCloseTo(1, 12);
    expect(reducedDistance(f, r, [0, 0, 0])).toBe(0);
  });
});

describe("the second notation: dip / dip direction / pitch of the plane a1–a2", () => {
  it("zero angles: a horizontal plane, a1 at 90° from the strike line", () => {
    expect(planeAnglesOf({ ang1: 0, ang2: 0, ang3: 0 })).toEqual({ dip: 0, dipDirection: 0, pitch: 90 });
  });

  it("a plane dipping 30° east with a1 along the strike to the north", () => {
    const a = anglesOfPlane({ dip: 30, dipDirection: 90, pitch: 0 });
    const f = frameOf(a);
    close(f.u1, [0, 1, 0], 1e-12);
    close(f.u3, [0.5, 0, Math.sqrt(3) / 2], 1e-12);
  });

  it("there and back: the same plane and the same line of a1 (its sign may turn)", () => {
    for (const a of grid) {
      const p = planeAnglesOf(a);
      expect(p.dip).toBeGreaterThanOrEqual(0);
      expect(p.dip).toBeLessThanOrEqual(90);
      expect(p.pitch).toBeGreaterThanOrEqual(0);
      expect(p.pitch).toBeLessThan(180);
      const f0 = frameOf(a);
      const f1 = frameOf(anglesOfPlane(p, a.ang1));
      expect(Math.abs(Math.abs(dot(f0.u3, f1.u3)) - 1)).toBeLessThan(1e-9);
      expect(Math.abs(Math.abs(dot(f0.u1, f1.u1)) - 1)).toBeLessThan(1e-9);
    }
  });
});

// frames.json (the core lane, 16.6): {"rules": "...", "cases": [{"ang1", "ang2", "ang3", "axes": [[u1], [u2], [u3]],
// "back": {"ang1", "ang2", "ang3"}, "plane"?: {"dip", "dip_direction", "pitch"}}]}
const FRAMES = resolve(__dirname, "../../core/tests/data/variography/frames.json");
interface FrameCase {
  ang1: number;
  ang2: number;
  ang3: number;
  axes: [Vec3, Vec3, Vec3];
  back: Angles;
  plane?: { dip: number; dip_direction: number; pitch: number };
}

describe("against the core (frames.json, ТД14-1)", () => {
  it.skipIf(!existsSync(FRAMES))("the axes 1e−12, the angles back 1e−9, the plane 1e−9", () => {
    const data = JSON.parse(readFileSync(FRAMES, "utf-8")) as { cases: FrameCase[] };
    expect(data.cases.length).toBeGreaterThan(0);
    for (const c of data.cases) {
      const f = frameOf(c);
      [f.u1, f.u2, f.u3].forEach((u, i) => close(u, c.axes[i], 1e-12));
      angleClose(anglesOf(c.axes[0], c.axes[1], c.ang1), c.back, 1e-9);
      if (c.plane) {
        const p = planeAnglesOf(c);
        close([p.dip, p.dipDirection, p.pitch], [c.plane.dip, c.plane.dip_direction, c.plane.pitch], 1e-9);
      }
    }
  });
});

// The other parts of frames.json (16.18): an `exact` case bit for bit, `normalize`, `from_plane` (hint = the dip
// direction), `directions` (the row u1 of the angles (Az, −p, 0)).
interface FullFrames {
  cases: (FrameCase & { exact?: boolean; from_plane?: Angles })[];
  normalize: { given: [number, number, number]; angles: [number, number, number] }[];
  directions: { azimuth: number; plunge: number; u: Vec3 }[];
}

describe("against the core (frames.json: exact, normalize, from_plane, directions)", () => {
  it.skipIf(!existsSync(FRAMES))("bit for bit where exact; 1e−9 degrees; 1e−12 of the directions", () => {
    const data = JSON.parse(readFileSync(FRAMES, "utf-8")) as FullFrames;
    for (const c of data.cases) {
      if (c.exact) {
        const f = frameOf(c);
        expect([f.u1, f.u2, f.u3]).toEqual(c.axes);
      }
      if (c.from_plane && c.plane) {
        const back = anglesOfPlane({ dip: c.plane.dip, dipDirection: c.plane.dip_direction, pitch: c.plane.pitch }, c.plane.dip_direction);
        angleClose(back, c.from_plane, 1e-9);
      }
    }
    expect(data.normalize.length).toBeGreaterThan(0);
    for (const n of data.normalize) {
      const a = normalizeAngles({ ang1: n.given[0], ang2: n.given[1], ang3: n.given[2] });
      expect([a.ang1, a.ang2, a.ang3]).toEqual(n.angles);
    }
    expect(data.directions.length).toBeGreaterThan(0);
    for (const d of data.directions) close(frameOf({ ang1: d.azimuth, ang2: -d.plunge, ang3: 0 }).u1, d.u, 1e-12);
  });
});
