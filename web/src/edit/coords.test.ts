import { describe, expect, it } from "vitest";
import { toSection } from "../viz/geometry";
import type { SectionLine } from "../viz/geometry";
import { formatCoord, horizontalPlane, parseCoordInput, planeDistance, projectToPlane, rayPlane, sectionPlane } from "./coords";
import type { Vec3 } from "./model";
import { lcg } from "./testUtil";

const point = (r: ReturnType<typeof parseCoordInput>): Vec3 => {
  if ("error" in r) throw new Error(r.error);
  return r.point;
};
const error = (r: ReturnType<typeof parseCoordInput>): string => ("error" in r ? r.error : "");

describe("planes and rays", () => {
  it("builds the section plane in the middle of the window", () => {
    const section: SectionLine = { x0: 512345.67, y0: 6789012.34, az: 37, length: null, towards: 30, away: 10 };
    const plane = sectionPlane(section);
    const a = (37 * Math.PI) / 180;
    expect(plane.normal[0]).toBeCloseTo(Math.cos(a), 15);
    expect(plane.normal[1]).toBeCloseTo(-Math.sin(a), 15);
    expect(plane.normal[2]).toBe(0);
    expect(Math.hypot(...plane.normal)).toBeCloseTo(1, 15);
    // 10 m to the viewer's side of the line: the middle of [-10, 30]
    const { u, d } = toSection(plane.point[0], plane.point[1], section);
    expect(d).toBeCloseTo(10, 8);
    expect(u).toBeCloseTo(0, 8);
    // a symmetric window: the plane goes through the line itself
    const sym = sectionPlane({ ...section, towards: 25, away: 25 });
    expect(sym.point).toEqual([section.x0, section.y0, 0]);
    // any point along the section at any height is in the plane
    const along: Vec3 = [plane.point[0] + 300 * Math.sin(a), plane.point[1] + 300 * Math.cos(a), -420];
    expect(Math.abs(planeDistance(along, plane))).toBeLessThan(1e-9);
  });

  it("puts a point drawn on a section exactly into the plane (UTM numbers)", () => {
    const rnd = lcg(7);
    let worst = 0;
    for (let i = 0; i < 2000; i++) {
      const section: SectionLine = {
        x0: 400000 + rnd() * 300000,
        y0: 5000000 + rnd() * 4000000,
        az: rnd() * 360,
        length: null,
        towards: rnd() * 50,
        away: rnd() * 50,
      };
      const plane = sectionPlane(section);
      const n = plane.normal;
      // the camera is on the viewer's side, somewhere along the section, looking roughly at the plane
      const a = (section.az * Math.PI) / 180;
      const s = (rnd() - 0.5) * 2000;
      const origin: Vec3 = [
        plane.point[0] + n[0] * 800 + Math.sin(a) * s,
        plane.point[1] + n[1] * 800 + Math.cos(a) * s,
        (rnd() - 0.5) * 1000,
      ];
      const dir: Vec3 = [-n[0] + (rnd() - 0.5) * 0.8, -n[1] + (rnd() - 0.5) * 0.8, (rnd() - 0.5) * 0.8];
      const hit = rayPlane({ origin, dir }, plane);
      expect(hit).not.toBeNull();
      const dist = Math.abs(planeDistance(hit as Vec3, plane));
      worst = Math.max(worst, dist);
      // the same check through the section's own formula
      expect(Math.abs(toSection((hit as Vec3)[0], (hit as Vec3)[1], section).d - (section.towards - section.away) / 2)).toBeLessThan(1e-6);
    }
    expect(worst).toBeLessThan(1e-9);
  });

  it("intersects a ray with a horizontal plane", () => {
    const plane = horizontalPlane(250);
    expect(plane).toEqual({ point: [0, 0, 250], normal: [0, 0, 1] });
    const hit = rayPlane({ origin: [512345.5, 6789012.25, 1250], dir: [0.3, -0.4, -1] }, plane) as Vec3;
    expect(hit[0]).toBeCloseTo(512345.5 + 300, 8);
    expect(hit[1]).toBeCloseTo(6789012.25 - 400, 8);
    expect(hit[2]).toBe(250);
  });

  it("returns null for a parallel ray and for a plane behind the origin", () => {
    const plane = horizontalPlane(100);
    expect(rayPlane({ origin: [0, 0, 200], dir: [1, 1, 0] }, plane)).toBeNull();
    expect(rayPlane({ origin: [0, 0, 200], dir: [0, 0, 1] }, plane)).toBeNull();
    expect(rayPlane({ origin: [0, 0, 200], dir: [0, 0, 0] }, plane)).toBeNull();
    // the origin in the plane (a tiny negative t) is still a hit
    const hit = rayPlane({ origin: [5, 6, 100 + 1e-10], dir: [0, 0, 1] }, plane);
    expect(hit).not.toBeNull();
    expect((hit as Vec3)[2]).toBeCloseTo(100, 9);
  });

  it("projects a point onto a plane", () => {
    const plane = sectionPlane({ x0: 500000, y0: 6700000, az: 90, length: null, towards: 0, away: 0 });
    // az 90: the line runs east, the normal looks south
    expect(plane.normal[0]).toBeCloseTo(0, 15);
    expect(plane.normal[1]).toBeCloseTo(-1, 15);
    const p = projectToPlane([500123.25, 6700042.5, 77], plane);
    expect(p[0]).toBeCloseTo(500123.25, 8);
    expect(p[1]).toBeCloseTo(6700000, 8);
    expect(p[2]).toBe(77);
    expect(Math.abs(planeDistance(p, plane))).toBeLessThan(1e-9);
  });
});

describe("parseCoordInput", () => {
  const prev: Vec3 = [500000, 6700000, 250];

  it("reads absolute coordinates with different separators", () => {
    expect(point(parseCoordInput("512345.5 6789012.25 250", null))).toEqual([512345.5, 6789012.25, 250]);
    expect(point(parseCoordInput("  512345.5   6789012.25\t-250.75 ", null))).toEqual([512345.5, 6789012.25, -250.75]);
    expect(point(parseCoordInput("512345.5, 6789012.25, 250", null))).toEqual([512345.5, 6789012.25, 250]);
    expect(point(parseCoordInput("512345.5,6789012.25,250", null))).toEqual([512345.5, 6789012.25, 250]);
    expect(point(parseCoordInput("512345.5;6789012.25;250", null))).toEqual([512345.5, 6789012.25, 250]);
    expect(point(parseCoordInput("512345; 6789012; 250", null))).toEqual([512345, 6789012, 250]);
    expect(point(parseCoordInput("1,2,3", null))).toEqual([1, 2, 3]);
    expect(point(parseCoordInput("1, 2, 3", null))).toEqual([1, 2, 3]);
    expect(point(parseCoordInput("1e3 2.5E2 -1e-1", null))).toEqual([1000, 250, -0.1]);
  });

  it("accepts a decimal comma with spaces and semicolons", () => {
    expect(point(parseCoordInput("512345,5 6789012,25 250", null))).toEqual([512345.5, 6789012.25, 250]);
    expect(point(parseCoordInput("512345,5;6789012,25;250,125", null))).toEqual([512345.5, 6789012.25, 250.125]);
    expect(point(parseCoordInput("512345,5 6789012,25", prev))).toEqual([512345.5, 6789012.25, 250]);
  });

  it("keeps the previous Z for X Y", () => {
    expect(point(parseCoordInput("512345.5 6789012.25", prev))).toEqual([512345.5, 6789012.25, 250]);
    expect(error(parseCoordInput("512345.5 6789012.25", null))).toMatch(/Z/);
  });

  it("reads relative polar input", () => {
    const east = point(parseCoordInput("@90 100", prev));
    expect(east[0]).toBeCloseTo(500100, 9);
    expect(east[1]).toBeCloseTo(6700000, 8);
    expect(east[2]).toBe(250);
    const north = point(parseCoordInput("@0 50", prev));
    expect(north).toEqual([500000, 6700050, 250]);
    const up = point(parseCoordInput("@ 0 10 30", prev));
    expect(up[0]).toBeCloseTo(500000, 9);
    expect(up[1]).toBeCloseTo(6700000 + 10 * Math.cos(Math.PI / 6), 8);
    expect(up[2]).toBeCloseTo(255, 10);
    const down = point(parseCoordInput("@225 100 -45", prev));
    const h = 100 * Math.SQRT1_2;
    expect(down[0]).toBeCloseTo(500000 - h * Math.SQRT1_2, 8);
    expect(down[1]).toBeCloseTo(6700000 - h * Math.SQRT1_2, 8);
    expect(down[2]).toBeCloseTo(250 - h, 9);
    // decimal comma in a relative input
    expect(point(parseCoordInput("@90 12,5 0", prev))[0]).toBeCloseTo(500012.5, 9);
    // straight down
    const shaft = point(parseCoordInput("@0 30 -90", prev));
    expect(shaft[0]).toBeCloseTo(500000, 9);
    expect(shaft[1]).toBeCloseTo(6700000, 8);
    expect(shaft[2]).toBeCloseTo(220, 10);
  });

  it("reads relative cartesian input", () => {
    expect(point(parseCoordInput("@@10 -20 5", prev))).toEqual([500010, 6699980, 255]);
    expect(point(parseCoordInput("@@10 -20", prev))).toEqual([500010, 6699980, 250]);
    expect(point(parseCoordInput("@@ 1,5; 2,5; -0,5", prev))).toEqual([500001.5, 6700002.5, 249.5]);
  });

  it("reports errors in Russian", () => {
    expect(error(parseCoordInput("", null))).toMatch(/[а-я]/i);
    expect(error(parseCoordInput("   ", prev))).not.toBe("");
    expect(error(parseCoordInput("abc 1 2", prev))).toMatch(/abc/);
    expect(error(parseCoordInput("1 2 3 4", prev))).toMatch(/X Y Z/);
    expect(error(parseCoordInput("5", prev))).not.toBe("");
    expect(error(parseCoordInput("@90 100", null))).toMatch(/предыдущ/);
    expect(error(parseCoordInput("@@1 2 3", null))).toMatch(/предыдущ/);
    expect(error(parseCoordInput("@90", prev))).not.toBe("");
    expect(error(parseCoordInput("@90 -5", prev))).toMatch(/Расстояние/);
    expect(error(parseCoordInput("@90 5 120", prev))).toMatch(/90/);
    expect(error(parseCoordInput("@@5", prev))).not.toBe("");
    expect(error(parseCoordInput("1..2 3 4", prev))).not.toBe("");
    expect(error(parseCoordInput("1e999 3 4", prev))).not.toBe("");
  });

  it("formats coordinates", () => {
    expect(formatCoord([512345.678, 6789012.3449, -250])).toBe("512345.68 6789012.34 -250.00");
    expect(formatCoord([1, 2, 3], 0)).toBe("1 2 3");
    expect(point(parseCoordInput(formatCoord([512345.125, 6789012.5, 250.25], 3), null))).toEqual([512345.125, 6789012.5, 250.25]);
  });
});
