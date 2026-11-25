// The layer of the errors of the cross-validation in the 3D window (Э18, 18.23): the places of the real samples (the answer of the core)
// as float32 offsets from an integer origin (ТЗ 5.4), the colour of every point, the clipping of the scene, the release of the buffers.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { readRow } from "../../src/modules/estimation/xvalApi";
import { errorCloud } from "../../src/modules/estimation/xvalModel";
import { maxRoundTripError } from "../../src/viz/origin";
import { ERROR_POINT_PX, ErrorPoints, extentOf } from "../../src/viz/xval/errorPoints";
import { table } from "./xvalKit";

const cloud = errorCloud(table().map(readRow))!;

describe("the layer of the errors", () => {
  const clip = [new THREE.Plane(new THREE.Vector3(0, 0, 1), 0)];
  const layer = new ErrorPoints(cloud, clip, 2);
  const points = layer.object.children[0] as THREE.Points;
  const pos = points.geometry.getAttribute("position");
  const material = points.material as THREE.PointsMaterial;

  it("one disc a sample with an error; the extent and the origin are of the project coordinates (float64)", () => {
    expect(layer.count).toBe(146);
    expect(pos.count).toBe(146);
    expect(layer.bbox).toEqual([500000, 6700000, 289, 500000, 6700025, 299]);
    expect(layer.origin).toEqual({ x: 500000, y: 6700013, z: 294 });
  });
  it("the offsets from the origin are small, so float32 loses nothing visible", () => {
    const rel = pos.array as Float32Array;
    expect(maxRoundTripError(cloud.xyz, rel, layer.origin)).toBeLessThan(1e-3);
    expect(Math.max(...Array.from(rel).map(Math.abs))).toBeLessThan(20);
  });
  it("the colour of a point is the colour of its error; the sprite is the size of the screen, clipped as the rest of the scene", () => {
    expect(Array.from(points.geometry.getAttribute("color").array)).toEqual(Array.from(cloud.rgb));
    expect(material.vertexColors).toBe(true);
    expect(material.sizeAttenuation).toBe(false);
    expect(material.size).toBe(ERROR_POINT_PX * 2);
    expect(material.clippingPlanes).toBe(clip);
  });
  it("the buffers are released", () => {
    let disposed = 0;
    points.geometry.addEventListener("dispose", () => disposed++);
    material.addEventListener("dispose", () => disposed++);
    layer.dispose();
    expect(disposed).toBe(2);
  });
  it("no points — no extent", () => {
    expect(extentOf(new Float64Array(0))).toBeNull();
    expect(new ErrorPoints({ xyz: new Float64Array(0), rgb: new Float32Array(0) }, []).bbox).toBeNull();
  });
});
