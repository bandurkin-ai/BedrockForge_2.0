import { describe, expect, it } from "vitest";
import { imageAxes, imageWindow } from "./scene3d";

// An image 200 × 100 m: top-left, top-right, bottom-right, bottom-left.
const CORNERS = [[1000, 5100], [1200, 5100], [1200, 5000], [1000, 5000]];

describe("a raster on a terrain model", () => {
  const at = (axes: NonNullable<ReturnType<typeof imageAxes>>, x: number, y: number) =>
    [axes.u[0] * x + axes.u[1] * y + axes.u[2], axes.v[0] * x + axes.v[1] * y + axes.v[2]];

  it("turns plan coordinates relative to the surface's origin into image coordinates", () => {
    const axes = imageAxes(CORNERS, [1100, 5050])!;
    expect(at(axes, -100, -50)).toEqual([0, 0]); // bottom-left
    expect(at(axes, 100, 50)).toEqual([1, 1]); // top-right
    expect(at(axes, 0, 0)).toEqual([0.5, 0.5]);
  });

  it("follows a rotated image", () => {
    // The same image turned 90°: its left-to-right runs to the north.
    const axes = imageAxes([[0, 0], [0, 200], [100, 200], [100, 0]], [0, 0])!;
    const [u, v] = at(axes, 50, 100);
    expect(u).toBeCloseTo(0.5);
    expect(v).toBeCloseTo(0.5);
    expect(at(axes, 100, 0).map((n) => Math.round(n * 1e9) / 1e9)).toEqual([0, 0]);
  });

  it("has no answer for an image without area", () => {
    expect(imageAxes([[0, 0], [10, 0], [10, 0], [0, 0]], [0, 0])).toBeNull();
  });

  it("finds the part of the image under the surface", () => {
    expect(imageWindow(CORNERS, [1050, 5025], [1150, 5075])).toEqual([0.25, 0.25, 0.75, 0.75]);
    expect(imageWindow(CORNERS, [900, 4900], [1300, 5200])).toEqual([-0.5, -1, 1.5, 2]); // the surface is larger
    expect(imageWindow(CORNERS, [2000, 5000], [2100, 5100])).toBeNull(); // apart
  });
});
