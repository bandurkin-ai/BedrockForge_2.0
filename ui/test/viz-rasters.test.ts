import { describe, expect, it } from "vitest";
import { floorLevel, isBackdrop, pickQuad, rasterPlacement, screenExtent, textureLevel, UNDER_M } from "../src/viz/rasters/placement";

/** JPG + JGW: 0.25 m pixels, the centre of the top-left pixel at (512345.125, 7123456.875) → corners of the image. */
const JGW = { a: 0.25, e: -0.25, c: 512_345.125, f: 7_123_456.875, w: 4000, h: 3000 };
function planCorners(z: number | null): (number | null)[][] {
  const x0 = JGW.c - JGW.a / 2;
  const y0 = JGW.f - JGW.e / 2;
  const x1 = x0 + JGW.w * JGW.a;
  const y1 = y0 + JGW.h * JGW.e;
  return [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]];
}

describe("raster corners: float64 → float32 from an integer origin", () => {
  it("a plan raster at its elevation: corners within 0.5 mm, the origin is whole metres", () => {
    const p = rasterPlacement({ corners3d: planCorners(250.5), elevation: 250.5, vertical: false }, 100)!;
    expect([p.origin.x, p.origin.y, p.origin.z].every(Number.isInteger)).toBe(true);
    expect(Array.from(p.corners.subarray(0, 3))).toEqual([512_345, 7_123_457, 250.5]); // the outer corner of the top-left pixel
    for (let i = 0; i < 12; i++) expect(Math.abs(p.local[i] + [p.origin.x, p.origin.y, p.origin.z][i % 3] - p.corners[i])).toBeLessThan(5e-4);
    // one pixel (0.25 m) is exact from the origin; absolute float32 at 7 million metres steps by 0.5 m
    expect(p.local[1] - p.local[7]).toBe(750);
    expect(Math.fround(7_123_456.875)).not.toBe(7_123_456.875);
    expect(Array.from(p.bbox)).toEqual([512_345, 7_122_707, 250.5, 513_345, 7_123_457, 250.5]);
    expect(p.floating).toBe(false);
  });

  it("without an elevation the raster lies under the lowest object, never at 0", () => {
    const p = rasterPlacement({ corners3d: planCorners(null), elevation: null, vertical: false }, 187.25)!;
    expect(p.floating).toBe(true);
    for (let i = 2; i < 12; i += 3) expect(p.corners[i]).toBe(187.25);
    expect(p.bbox[2]).toBe(187.25);
    // the elevation of the raster wins over the floor; corners with their own heights win over both
    expect(rasterPlacement({ corners3d: planCorners(null), elevation: 300, vertical: false }, 187.25)!.corners[2]).toBe(300);
    expect(rasterPlacement({ corners3d: planCorners(310), elevation: 300, vertical: false }, 187.25)!.corners[2]).toBe(310);
  });

  it("a vertical raster keeps the heights of its corners", () => {
    const c = [[500_000.5, 7_000_000.25, 420], [500_300.5, 7_000_400.25, 420], [500_300.5, 7_000_400.25, 120], [500_000.5, 7_000_000.25, 120]];
    const p = rasterPlacement({ corners3d: c, elevation: null, vertical: true }, 0)!;
    expect(p.floating).toBe(false);
    expect([p.origin.x, p.origin.y, p.origin.z]).toEqual([500_151, 7_000_200, 270]);
    expect(Array.from(p.local)).toEqual([-150.5, -199.75, 150, 149.5, 200.25, 150, 149.5, 200.25, -150, -150.5, -199.75, -150]);
    expect(Array.from(p.bbox)).toEqual([500_000.5, 7_000_000.25, 120, 500_300.5, 7_000_400.25, 420]);
  });

  it("no georeference — nothing to place", () => {
    expect(rasterPlacement({ corners3d: null, elevation: 5, vertical: false }, 0)).toBeNull();
    expect(rasterPlacement({ corners3d: [[1, 2, 3]], elevation: 5, vertical: false }, 0)).toBeNull();
    expect(rasterPlacement({ corners3d: [[1, null, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]], elevation: 5, vertical: false }, 0)).toBeNull();
  });
});

describe("the level of objects without a height", () => {
  it("a metre under the lowest visible object; hidden ones count only when nothing is visible; 0 in an empty project", () => {
    expect(floorLevel([{ zmin: 320, visible: true }, { zmin: 150, visible: false }, { zmin: 280.5, visible: true }])).toBe(280.5 - UNDER_M);
    expect(floorLevel([{ zmin: 150, visible: false }])).toBe(150 - UNDER_M);
    expect(floorLevel([{ zmin: NaN, visible: true }])).toBe(0);
    expect(floorLevel([])).toBe(0);
  });
});

describe("backdrop and texture levels", () => {
  it("only a vertical raster under clipping is a backdrop", () => {
    expect(isBackdrop(true, true)).toBe(true);
    expect(isBackdrop(true, false)).toBe(false);
    expect(isBackdrop(false, true)).toBe(false);
  });

  it("the small copy until it is stretched over more pixels than it has, with 20 % hysteresis", () => {
    const sizes = [4096, 1024];
    expect(textureLevel(600, sizes, null)).toBe(1);
    expect(textureLevel(1500, sizes, null)).toBe(0);
    expect(textureLevel(1100, sizes, 1)).toBe(1); // within 20 % above 1024
    expect(textureLevel(1300, sizes, 1)).toBe(0);
    expect(textureLevel(900, sizes, 0)).toBe(0); // within 20 % below
    expect(textureLevel(700, sizes, 0)).toBe(1);
    expect(textureLevel(5000, [800, 800], null)).toBe(1); // a small image: both copies are the same
  });

  it("the size of a raster on the screen: its longer edge, Z stretched", () => {
    const local = Float32Array.of(-100, 0, 50, 100, 0, 50, 100, 0, -50, -100, 0, -50);
    expect(screenExtent(local, [0, 0, 0], 1, () => 2)).toBe(400);
    expect(screenExtent(local, [0, 0, 0], 5, () => 2)).toBe(1000);
  });
});

describe("a click on a raster", () => {
  const local = Float32Array.of(-100, 50, 0, 100, 50, 0, 100, -50, 0, -100, -50, 0);

  it("a ray through the quad gives the distance along it and the place; beside it — nothing", () => {
    const hit = pickQuad(local, [10, 20, 5], 2, { origin: [40, 30, 110], dir: [0, 0, -1] });
    expect(hit?.t).toBeCloseTo(100, 9); // the quad is at z = (0 + 5) · 2
    expect(hit?.point).toEqual([40, 30, 10]);
    expect(pickQuad(local, [10, 20, 5], 2, { origin: [200, 30, 110], dir: [0, 0, -1] })).toBeNull();
    expect(pickQuad(local, [10, 20, 5], 2, { origin: [40, 30, 110], dir: [0, 0, 1] })).toBeNull(); // behind the camera
    expect(pickQuad(local, [10, 20, 5], 2, { origin: [40, 30, 110], dir: [1, 0, 0] })).toBeNull(); // along the plane
  });
});
