// The layer of an anisotropy ellipsoid (ТЗ 7.35 Д-14; ADR 0022 §9, решения 17–19; ТД14-1, ТД14-2): the unit sphere
// under the matrix of `ellipsoidBasis` (against `frames.json` → `ellipsoids`), the three principal sections, the disk
// a1–a2 and the samples in its tolerance (float64 from an integer origin, no value — never the colour of 0), the
// bounds for «Вписать»; a drag changes only the matrix; the six handles in Cobalt, the lit one Copper Orange.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import type { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { describe, expect, it } from "vitest";
import { NO_VALUE_COLOR } from "../src/viz/color/colorScale";
import type { Vec3 } from "../src/viz/core/vec3";
import { BRAND, buildEllipsoid, defaultSelected, halfExtents, poseMatrix, poseOf, samplesInTolerance, unitCircle, unitSections, type Samples } from "../src/viz/ellipsoid/build";
import { handlesOf, type Handle } from "../src/viz/ellipsoid/drag";
import { frameOf, type Angles, type Ranges } from "../src/viz/ellipsoid/frame";
import { EllipsoidHandles, handleSegments, knobOf } from "../src/viz/ellipsoid/handle";
import { EllipsoidLayer } from "../src/viz/ellipsoid/layer";

const C: Vec3 = [512_120.4, 7_100_010.6, 255.2];
const A: Angles = { ang1: 30, ang2: -20, ang3: 40 };
const R: Ranges = { a1: 250, a2: 120, a3: 35.5 };
const mul = (m: readonly number[], s: Vec3): Vec3 => [0, 1, 2].map((i) => m[3 * i] * s[0] + m[3 * i + 1] * s[1] + m[3 * i + 2] * s[2]) as Vec3;
const ctx = () => ({ clip: [] as THREE.Plane[], resolution: new THREE.Vector2(800, 600) });
const hex = (c: THREE.Color) => `#${c.getHexString()}`;

describe("the build: the unit sphere under a matrix", () => {
  it("the origin is the centre rounded, the offset float64; the selected — the largest a1", () => {
    const b = buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: { a1: 40, a2: 30, a3: 10 } }, { angles: A, ranges: R }, { angles: A, ranges: { ...R } }] });
    expect(b.origin).toEqual({ x: 512_120, y: 7_100_011, z: 255 });
    b.offset.forEach((x, i) => expect(x).toBeCloseTo(C[i] - [512_120, 7_100_011, 255][i], 9));
    expect(b.selected).toBe(1);
    expect(defaultSelected([])).toBe(-1);
    expect(b.structures[0].color).toBe(BRAND.cobalt);
    expect(buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: R }], selected: 0 }).selected).toBe(0);
  });

  it("refuses what cannot be drawn", () => {
    expect(() => buildEllipsoid({ centre: C, structures: [] })).toThrow(RangeError);
    expect(() => buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: { ...R, a2: 0 } }] })).toThrow(/Дальности/);
    expect(() => buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: R }], selected: 3 })).toThrow(RangeError);
    expect(() => buildEllipsoid({ centre: [NaN, 0, 0], structures: [{ angles: A, ranges: R }] })).toThrow(RangeError);
  });

  it("a point of the sphere goes to offset + M·s: the poles are the ends of the axes", () => {
    const b = buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: R }] });
    const p = b.structures[0];
    const m = poseMatrix(b.offset, p.basis);
    const apply = (s: Vec3): Vec3 => [0, 1, 2].map((i) => m[4 * i] * s[0] + m[4 * i + 1] * s[1] + m[4 * i + 2] * s[2] + m[4 * i + 3]) as Vec3;
    const f = frameOf(A);
    [f.u1, f.u2, f.u3].forEach((u, j) => {
      const e: Vec3 = [0, 0, 0];
      e[j] = 1;
      const r = [R.a1, R.a2, R.a3][j];
      apply(e).forEach((x, i) => expect(x).toBeCloseTo(b.offset[i] + r * u[i], 9));
    });
    expect(m.slice(12)).toEqual([0, 0, 0, 1]);
  });

  it("the bounds are exact: no point of the sphere beyond them, the extremes reached", () => {
    const p = poseOf({ angles: A, ranges: R });
    const h = halfExtents(p.basis);
    const hi: Vec3 = [0, 0, 0];
    for (let i = 0; i <= 90; i++)
      for (let j = 0; j < 180; j++) {
        const t = (Math.PI * i) / 90;
        const q = (2 * Math.PI * j) / 180;
        const w = mul(p.basis, [Math.sin(t) * Math.cos(q), Math.sin(t) * Math.sin(q), Math.cos(t)]);
        for (let k = 0; k < 3; k++) hi[k] = Math.max(hi[k], Math.abs(w[k]));
      }
    for (let k = 0; k < 3; k++) {
      expect(hi[k]).toBeLessThanOrEqual(h[k] * (1 + 1e-12));
      expect(hi[k]).toBeGreaterThan(h[k] * 0.998);
    }
    const b = buildEllipsoid({ centre: C, structures: [{ angles: A, ranges: R }] });
    for (let k = 0; k < 3; k++) {
      expect(b.reach[k]).toBeCloseTo(C[k] - h[k], 6);
      expect(b.reach[k + 3]).toBeCloseTo(C[k] + h[k], 6);
    }
  });

  it("three unit sections in their planes; the section a1–a2 lies in the plane ⟂ u3 under the matrix", () => {
    const s = unitSections(64);
    expect(s.length).toBe(3 * 64 * 6);
    for (let i = 0; i < s.length; i += 3) expect(Math.hypot(s[i], s[i + 1], s[i + 2])).toBeCloseTo(1, 6);
    const c = unitCircle("a1a2", 64);
    const p = poseOf({ angles: A, ranges: R });
    for (let i = 0; i < c.length; i += 3) {
      expect(c[i + 2]).toBe(0);
      const w = mul(p.basis, [c[i], c[i + 1], c[i + 2]]);
      expect(Math.abs(w[0] * p.frame.u3[0] + w[1] * p.frame.u3[1] + w[2] * p.frame.u3[2])).toBeLessThan(1e-4);
    }
    expect(unitCircle("a2a3", 8).filter((_, i) => i % 3 === 0).every((x) => x === 0)).toBe(true);
  });
});

// frames.json → ellipsoids: matrix[i][j] = a_j · u_j[i]; ends — a1·u1, a2·u2, a3·u3 (1e−12 of the largest range)
const FRAMES = resolve(__dirname, "../../core/tests/data/variography/frames.json");
describe("against the core (frames.json → ellipsoids)", () => {
  it.skipIf(!existsSync(FRAMES))("the matrix over the unit sphere and the ends of the axes", () => {
    const data = JSON.parse(readFileSync(FRAMES, "utf-8")) as { ellipsoids: { ang1: number; ang2: number; ang3: number; ranges: number[]; matrix: number[][]; ends: Vec3[] }[] };
    expect(data.ellipsoids.length).toBeGreaterThan(0);
    for (const e of data.ellipsoids) {
      const ranges = { a1: e.ranges[0], a2: e.ranges[1], a3: e.ranges[2] };
      const p = poseOf({ angles: e, ranges });
      const tol = 1e-12 * Math.max(...e.ranges);
      p.basis.forEach((x, k) => expect(Math.abs(x - e.matrix[Math.floor(k / 3)][k % 3])).toBeLessThanOrEqual(tol));
      e.ends.forEach((end, j) => {
        const s: Vec3 = [0, 0, 0];
        s[j] = 1;
        mul(p.basis, s).forEach((x, i) => expect(Math.abs(x - end[i])).toBeLessThanOrEqual(tol));
      });
    }
  });
});

describe("the samples in the tolerance of the disk a1–a2", () => {
  // the frame of zero angles: u1 north, u2 west, u3 up; a1 = 100, a2 = 50; the disk is horizontal through the centre
  const centre: Vec3 = [512_120.25, 7_100_010.5, 255];
  const b = buildEllipsoid({ centre, structures: [{ angles: { ang1: 0, ang2: 0, ang3: 0 }, ranges: { a1: 100, a2: 50, a3: 20 } }] });
  const so = { x: 512_000, y: 7_100_000, z: 200 };
  const at = (dx: number, dy: number, dz: number) => [centre[0] + dx - so.x, centre[1] + dy - so.y, centre[2] + dz - so.z];
  const xyz = new Float64Array([...at(0, 90, 1), ...at(0, 90, 6), ...at(0, 110, 0), ...at(45, 0, -4.9), ...at(55, 0, 0), ...at(NaN, 0, 0), ...at(10, 10, 0)]);
  const samples: Samples = { origin: so, xyz, values: [1, 2, 3, null, 5, 6, 0] };

  it("kept: inside the disk and not further than the thickness; float32 from the layer origin", () => {
    const s = samplesInTolerance(b, b.structures[0], samples, 5);
    expect([...s.indices]).toEqual([0, 3, 6]);
    s.indices.forEach((i, j) => {
      for (let k = 0; k < 3; k++) {
        const abs = [so.x, so.y, so.z][k] + xyz[3 * i + k];
        expect(Math.abs(s.position[3 * j + k] + [b.origin.x, b.origin.y, b.origin.z][k] - abs)).toBeLessThan(1e-4);
      }
    });
  });

  it("no value — the colour of «no value», not of 0; 0 is a value", () => {
    const s = samplesInTolerance(b, b.structures[0], samples, 5);
    const v = parseInt(NO_VALUE_COLOR.slice(1), 16);
    const empty = [(v >> 16) & 255, (v >> 8) & 255, v & 255].map((c) => c / 255);
    const rgb = (j: number) => [...s.color.slice(3 * j, 3 * j + 3)];
    rgb(1).forEach((c, k) => expect(c).toBeCloseTo(empty[k], 6));
    expect(rgb(2)).not.toEqual(rgb(1));
    expect(rgb(0)).not.toEqual(rgb(2));
    expect(() => samplesInTolerance(b, b.structures[0], samples, -1)).toThrow(RangeError);
  });

  it("a turned disk takes other samples: ang3 = 90° stands it up through north", () => {
    const pose = poseOf({ angles: { ang1: 0, ang2: 0, ang3: 90 }, ranges: { a1: 100, a2: 50, a3: 20 } });
    const s = samplesInTolerance(b, pose, samples, 5);
    expect([...s.indices]).toEqual([0, 1]);
  });
});

describe("the layer: one geometry under the matrix of each structure", () => {
  const structures = [
    { angles: A, ranges: R },
    { angles: { ang1: 120, ang2: 0, ang3: 0 }, ranges: { a1: 60, a2: 40, a3: 10 }, color: BRAND.copper },
  ];
  const groups = (l: EllipsoidLayer) => l.object.children.filter((o): o is THREE.Group => o instanceof THREE.Group);

  it("the selected — the surface, the sections, the disk; the other — thin sections", () => {
    const l = new EllipsoidLayer(buildEllipsoid({ centre: C, structures }), ctx());
    const [g0, g1] = groups(l);
    const vis = (g: THREE.Group) => g.children.map((o) => o.visible);
    expect(vis(g0)).toEqual([true, true, false, true, true]);
    expect(vis(g1)).toEqual([false, false, true, false, false]);
    expect(hex(((g1.children[2] as LineSegments2).material as unknown as { color: THREE.Color }).color)).toBe(BRAND.copper);
    expect(hex(((g0.children[3] as THREE.Mesh).material as THREE.MeshBasicMaterial).color)).toBe(BRAND.slate);
    l.select(1);
    expect(vis(g1)).toEqual([true, true, false, true, true]);
    expect(l.shown().selected).toBe(1);
    expect(() => l.select(2)).toThrow(RangeError);
    l.dispose();
  });

  it("a drag sets only the matrix: the same geometry, the new basis, the bounds in place", () => {
    const l = new EllipsoidLayer(buildEllipsoid({ centre: C, structures }), ctx());
    const g0 = groups(l)[0];
    const geoms = g0.children.map((o) => (o as THREE.Mesh).geometry);
    const bbox = l.bbox;
    const before = [...bbox];
    const pose = l.setPose(0, { angles: { ...A, ang1: 75 }, ranges: { ...R, a1: 400 } });
    expect(g0.children.map((o) => (o as THREE.Mesh).geometry)).toEqual(geoms);
    const m = poseMatrix(l.build.offset, pose.basis);
    const e = g0.matrix.elements; // column-major
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) expect(e[j * 4 + i]).toBeCloseTo(m[i * 4 + j], 6);
    expect(l.bbox).toBe(bbox);
    expect(l.bbox).not.toEqual(before);
    expect(l.shown().bases[0]).toEqual(pose.basis);
    expect(l.pose(0).color).toBe(BRAND.cobalt);
    expect(() => l.setPoses([structures[0]])).toThrow(/Число структур/);
    l.setPoses(structures);
    l.bbox.forEach((x, k) => expect(x).toBeCloseTo(before[k], 6));
    l.dispose();
  });

  it("the samples follow the pose of the selected structure", () => {
    const centre: Vec3 = [1000.5, 2000.5, 100];
    const l = new EllipsoidLayer(buildEllipsoid({ centre, structures: [{ angles: { ang1: 0, ang2: 0, ang3: 0 }, ranges: { a1: 100, a2: 50, a3: 20 } }] }), ctx());
    const xyz = new Float64Array([0, 50, 0, 0, 0, 50]).map((v, i) => v + centre[i % 3]);
    l.setSamples({ origin: { x: 0, y: 0, z: 0 }, xyz, values: [1, 2] }, 5);
    expect([...l.sampled!.indices]).toEqual([0]);
    expect(l.shown().samples).toBe(1);
    l.setPose(0, { angles: { ang1: 0, ang2: 0, ang3: 90 }, ranges: { a1: 100, a2: 50, a3: 20 } });
    expect([...l.sampled!.indices]).toEqual([0, 1]);
    const pts = l.object.children.find((o): o is THREE.Points => o instanceof THREE.Points)!;
    expect(pts.geometry.getAttribute("position").count).toBe(2);
    l.setSamples(null);
    expect(l.sampled).toBeNull();
    expect(pts.geometry.getAttribute("position").count).toBe(0);
    l.setVisible(false);
    expect(l.shown().visible).toBe(false);
    l.dispose();
  });
});

describe("the six handles drawn", () => {
  const hs = handlesOf(A, R, C);
  const of = (kind: Handle["kind"]) => hs.find((h) => h.kind === kind)!;

  it("Cobalt; under the cursor Copper Orange; a drag dims the others", () => {
    const d = new EllipsoidHandles(C, new THREE.Vector2(800, 600));
    expect(d.object.children.every((o) => !o.visible)).toBe(true);
    d.update(hs, "ang1", null);
    expect(d.shown.map((s) => s.kind)).toEqual(["ang1", "ang2", "ang3", "a1", "a2", "a3"]);
    expect(d.shown.filter((s) => s.lit).map((s) => s.kind)).toEqual(["ang1"]);
    expect(d.shown.some((s) => s.dimmed)).toBe(false);
    const colors = d.object.children.filter((o) => o instanceof THREE.Points).map((o) => hex(((o as THREE.Points).material as THREE.PointsMaterial).color));
    expect(colors).toEqual([BRAND.copper, BRAND.cobalt, BRAND.cobalt, BRAND.cobalt, BRAND.cobalt, BRAND.cobalt]);
    d.update(hs, "ang1", "a2");
    expect(d.shown.filter((s) => s.lit).map((s) => s.kind)).toEqual(["a2"]);
    expect(d.shown.filter((s) => s.dimmed).length).toBe(5);
    d.update(hs.filter((h) => h.kind !== "a3"), null, null);
    expect(d.object.children.filter((o) => o.visible).length).toBe(10);
    d.dispose();
  });

  it("buffers from the integer origin: the ring through its angle, the knob of a1 at the end of the axis", () => {
    const d = new EllipsoidHandles(C, new THREE.Vector2(800, 600));
    const base: Vec3 = [d.origin.x, d.origin.y, d.origin.z];
    expect(base).toEqual([512_120, 7_100_011, 255]);
    const ring = handleSegments(of("ang1"), base);
    for (let i = 0; i < ring.length; i += 3) expect(Math.abs(ring[i])).toBeLessThan(2 * 1.3 * R.a1);
    const axis = handleSegments(of("a1"), base);
    expect(axis.length).toBe(6);
    const f = frameOf(A);
    knobOf(of("a1")).forEach((x, i) => expect(x).toBeCloseTo(C[i] + R.a1 * f.u1[i], 9));
    d.update(hs, null, null);
    const knob = d.object.children.filter((o): o is THREE.Points => o instanceof THREE.Points)[3];
    const p = knob.geometry.getAttribute("position");
    [p.getX(0), p.getY(0), p.getZ(0)].forEach((x, i) => expect(x).toBeCloseTo(C[i] + R.a1 * f.u1[i] - base[i], 3));
    d.setVisible(false);
    expect(d.object.visible).toBe(false);
    d.dispose();
  });
});
