/**
 * A raster draped on a surface (ТЗ 7.20, ADR 0011 §4): image coordinates from the plan position of a vertex.
 * The first four tests are BF `drape.test.ts` with the image rows counted from the top (BF2 textures are not
 * flipped). The reference of the accuracy tests is the affine of the raster in float64 on absolute
 * coordinates; the tolerance is 0.02 px of a 4096 px image and 0.5 mm on the ground for the float32
 * arithmetic of the shader (hard data: UTM coordinates, a part 16 km wide, a small rotated image far from
 * the origin of the part).
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { DRAPE_PROGRAM_KEY, drapeUv, drapeUvFloat32, imageAxes, imageWindow, patchDrapeShader } from "../src/viz/mesh/drape";

// An image 200 × 100 m: top-left, top-right, bottom-right, bottom-left.
const CORNERS = [[1000, 5100], [1200, 5100], [1200, 5000], [1000, 5000]];

describe("a raster on a terrain model", () => {
  it("turns plan coordinates relative to the surface's origin into image coordinates", () => {
    const axes = imageAxes(CORNERS, [1100, 5050])!;
    expect(drapeUv(axes, -100, 50)).toEqual([0, 0]); // top-left: row 0 of the image
    expect(drapeUv(axes, 100, -50)).toEqual([1, 1]); // bottom-right
    expect(drapeUv(axes, -100, -50)).toEqual([0, 1]); // bottom-left
    expect(drapeUv(axes, 0, 0)).toEqual([0.5, 0.5]);
  });

  it("follows a rotated image", () => {
    // The same image turned 90°: its left-to-right runs to the north.
    const axes = imageAxes([[0, 0], [0, 200], [100, 200], [100, 0]], [0, 0])!;
    const [u, v] = drapeUv(axes, 50, 100);
    expect(u).toBeCloseTo(0.5);
    expect(v).toBeCloseTo(0.5);
    expect(drapeUv(axes, 0, 0).map((n) => Math.round(n * 1e9) / 1e9)).toEqual([0, 0]);
    expect(drapeUv(axes, 100, 0).map((n) => Math.round(n * 1e9) / 1e9)).toEqual([0, 1]);
  });

  it("has no answer for an image without area, with an empty corner or without corners", () => {
    expect(imageAxes([[0, 0], [10, 0], [10, 0], [0, 0]], [0, 0])).toBeNull();
    expect(imageAxes([[0, 0], [10, 0], [10, 10], [0, null]], [0, 0])).toBeNull();
    expect(imageAxes([[0, 0], [NaN, 0], [10, 10], [0, 10]], [0, 0])).toBeNull();
    expect(imageAxes(null, [0, 0])).toBeNull();
    expect(imageAxes([[0, 0]], [0, 0])).toBeNull();
  });

  it("finds the part of the image under the surface", () => {
    expect(imageWindow(CORNERS, [1050, 5025], [1150, 5075])).toEqual([0.25, 0.25, 0.75, 0.75]);
    expect(imageWindow(CORNERS, [900, 4900], [1300, 5200])).toEqual([-0.5, -1, 1.5, 2]); // the surface is larger
    expect(imageWindow(CORNERS, [2000, 5000], [2100, 5100])).toBeNull(); // apart
    // rows from the top: the northern half of the image is its upper half
    expect(imageWindow(CORNERS, [1000, 5050], [1200, 5100])).toEqual([0, 0, 1, 0.5]);
  });
});

/** A seeded generator: the tests are repeatable. */
function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}

interface Image {
  /** absolute corners: top-left, top-right, bottom-right, bottom-left */
  corners: number[][];
  w: number;
  h: number;
  /** metres in a pixel */
  pixel: number;
}

/** An image w × h px turned by `deg` about its top-left corner (x0, y0). */
function image(x0: number, y0: number, w: number, h: number, pixel: number, deg = 0): Image {
  const [s, c] = [Math.sin((deg * Math.PI) / 180), Math.cos((deg * Math.PI) / 180)];
  const right = [c * w * pixel, s * w * pixel];
  const down = [s * h * pixel, -c * h * pixel];
  return { corners: [[x0, y0], [x0 + right[0], y0 + right[1]], [x0 + right[0] + down[0], y0 + right[1] + down[1]], [x0 + down[0], y0 + down[1]]], w, h, pixel };
}

/** The reference: pixel coordinates of an absolute point by the affine of the image, float64. */
function referencePx(im: Image, x: number, y: number): [number, number] {
  const [tl, tr, , bl] = im.corners;
  const ex = [(tr[0] - tl[0]) / im.w, (tr[1] - tl[1]) / im.w]; // one pixel to the right
  const ey = [(bl[0] - tl[0]) / im.h, (bl[1] - tl[1]) / im.h]; // one pixel down
  const det = ex[0] * ey[1] - ex[1] * ey[0];
  const [dx, dy] = [x - tl[0], y - tl[1]];
  return [(dx * ey[1] - dy * ey[0]) / det, (ex[0] * dy - ex[1] * dx) / det];
}

/**
 * The largest error, px of the image, over `n` vertices inside the image: float32 offsets from `origin`, as
 * the core sends them; the reference is taken at the place the vertex is drawn (origin + float32 offset).
 */
function worstPx(im: Image, origin: [number, number], uv: (x: number, y: number) => [number, number], n = 20_000, seed = 7): number {
  const rnd = lcg(seed);
  const [tl, tr, , bl] = im.corners;
  let worst = 0;
  for (let i = 0; i < n; i++) {
    const [a, b] = [rnd(), rnd()];
    const lx = Math.fround(tl[0] + (tr[0] - tl[0]) * a + (bl[0] - tl[0]) * b - origin[0]);
    const ly = Math.fround(tl[1] + (tr[1] - tl[1]) * a + (bl[1] - tl[1]) * b - origin[1]);
    const ref = referencePx(im, origin[0] + lx, origin[1] + ly);
    const [u, v] = uv(lx, ly);
    worst = Math.max(worst, Math.abs(u * im.w - ref[0]), Math.abs(v * im.h - ref[1]));
  }
  return worst;
}

describe("accuracy of the image coordinates (float64 affine → UV)", () => {
  // JPG + JGW in UTM: 0.25 m pixels, 4000 × 3000 px, the outer corner of the top-left pixel
  const ortho = image(512_345, 7_123_457, 4000, 3000, 0.25);

  it("float64: the affine of the raster to 1e-6 px at UTM coordinates", () => {
    const origin: [number, number] = [512_845, 7_123_082];
    const axes = imageAxes(ortho.corners, origin)!;
    expect(worstPx(ortho, origin, (x, y) => drapeUv(axes, x, y))).toBeLessThan(1e-6);
    // a corner of a pixel is exact: 0.25 m steps from a whole-metre origin
    expect(drapeUv(axes, 512_345.25 - origin[0], 7_123_456.75 - origin[1])).toEqual([1 / 4000, 1 / 3000]);
  });

  it("the shader (float32): within 0.02 px and 0.5 mm for a part around the image", () => {
    const origin: [number, number] = [512_845, 7_123_082];
    const axes = imageAxes(ortho.corners, origin)!;
    const px = worstPx(ortho, origin, (x, y) => drapeUvFloat32(axes, x, y));
    expect(px).toBeLessThan(0.02);
    expect(px * ortho.pixel).toBeLessThan(5e-4);
  });

  it("hard data: a small rotated image at the edge of a part 16 km wide", () => {
    // 100 m of image, 4096 px (2.4 cm pixels), turned 30°, 8 km from the origin of the part
    const small = image(520_200.3371, 7_128_950.7713, 4096, 4096, 100 / 4096, 30); // its corner is not a float32 there
    const origin: [number, number] = [512_345, 7_123_457];
    const axes = imageAxes(small.corners, origin)!;
    expect(axes.o[0]).toBeGreaterThan(7800); // float32 steps of 0.5 mm there
    expect(worstPx(small, origin, (x, y) => drapeUv(axes, x, y))).toBeLessThan(1e-6);
    const px = worstPx(small, origin, (x, y) => drapeUvFloat32(axes, x, y)); // measured: 0.011 px, 0.26 mm
    expect(px).toBeLessThan(0.02);
    expect(px * small.pixel).toBeLessThan(5e-4);
  });

  it("hard data: an image of 40 km over a part (5 m pixels)", () => {
    const wide = image(495_000, 7_140_000, 8192, 8192, 5, -12);
    const origin: [number, number] = [512_345, 7_123_457];
    const axes = imageAxes(wide.corners, origin)!;
    const px = worstPx(wide, origin, (x, y) => drapeUvFloat32(axes, x, y));
    expect(px).toBeLessThan(0.02);
  });

  it("absolute coordinates in float32 would miss by a pixel: the origin of the part is what keeps the accuracy", () => {
    // the corner of the pixel one step right and down from the corner of the image
    const [x, y] = [512_345.25, 7_123_456.75];
    const absolute = imageAxes(ortho.corners, [0, 0])!;
    const [, v] = drapeUvFloat32(absolute, x, y); // float32 steps by 0.5 m at 7 million metres
    expect(Math.abs(v * 3000 - 1)).toBeGreaterThanOrEqual(1);
    const origin = [512_845, 7_123_082];
    const [u2, v2] = drapeUvFloat32(imageAxes(ortho.corners, origin)!, x - origin[0], y - origin[1]);
    expect(Math.abs(u2 * 4000 - 1)).toBeLessThan(1e-3);
    expect(Math.abs(v2 * 3000 - 1)).toBeLessThan(1e-3);
  });

  it("the same point of the plan has the same image coordinates whatever triangle carries it", () => {
    // a fine and a coarse triangle over the same place (two levels of detail), heights apart
    const origin: [number, number] = [512_845, 7_123_082];
    const axes = imageAxes(ortho.corners, origin)!;
    const p = [137.375, -212.625];
    const through = (tri: number[][]) => {
      // barycentric weights of p in the plan triangle, then the interpolation the rasteriser does
      const [a, b, c] = tri;
      const d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
      const wa = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d;
      const wb = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d;
      const uvs = tri.map(([x, y]) => drapeUvFloat32(axes, x, y));
      return [0, 1].map((k) => wa * uvs[0][k] + wb * uvs[1][k] + (1 - wa - wb) * uvs[2][k]);
    };
    const fine = through([[137, -213], [138, -213], [137.25, -212]]);
    const coarse = through([[-350.5, -480.25], [480, -470.5], [20.75, 495.5]]);
    const exact = drapeUv(axes, p[0], p[1]);
    for (const k of [0, 1]) {
      expect(Math.abs(fine[k] - coarse[k]) * 4000).toBeLessThan(0.02);
      expect(Math.abs(fine[k] - exact[k]) * 4000).toBeLessThan(0.02);
    }
  });
});

describe("the shader of a draped surface", () => {
  it("every anchor is in the Lambert shader of this three.js; the image coordinates come from position.xy", () => {
    const shader = { vertexShader: THREE.ShaderLib.lambert.vertexShader, fragmentShader: THREE.ShaderLib.lambert.fragmentShader };
    expect(patchDrapeShader(shader)).toBe(true);
    expect(shader.vertexShader).toContain("uniform vec2 uDrapeO;");
    expect(shader.vertexShader).toContain("position.xy - uDrapeO");
    expect(shader.fragmentShader).toContain("uniform sampler2D uDrapeMap;");
    expect(shader.fragmentShader).toContain("uDrapeTint");
    expect(shader.fragmentShader).toContain("uDrapeShade");
    // a shader without the anchors is left as it is and told about
    const other = { vertexShader: "void main() {}", fragmentShader: "void main() {}" };
    expect(patchDrapeShader(other)).toBe(false);
    expect(other.vertexShader).toBe("void main() {}");
    expect(DRAPE_PROGRAM_KEY).toMatch(/drape/);
  });
});
