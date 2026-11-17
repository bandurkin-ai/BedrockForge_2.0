import { describe, expect, it } from "vitest";
import { integerOrigin, maxRoundTripError, toRelativeFloat32 } from "../src/viz/origin";
import { rebaseCamera, RECENTER_DISTANCE, SceneOrigin } from "../src/viz/core/sceneOrigin";
import { clipPlanes } from "../src/viz/section/math";
import { planeDistance, type Vec3 } from "../src/viz/core/vec3";
import type { CameraState } from "../src/viz/core/views";

describe("scene origin (ADR 0004 §2)", () => {
  it("points 0.001 m apart in a 10 km object stay apart in float32 after the origin", () => {
    // An object of 10 km in UTM; the two points sit at its far corner, where float32 is coarsest.
    const p: Vec3 = [509_999.1234, 7_009_999.5678, 1_249.9012];
    const xyz = new Float64Array([500_000, 7_000_000, -750, 510_000, 7_010_000, 1_250, ...p, p[0] + 0.001, p[1] + 0.001, p[2] + 0.001]);
    const o = integerOrigin(xyz);
    expect(o).toEqual({ x: 505_000, y: 7_005_000, z: 250 });
    const rel = toRelativeFloat32(xyz, o);
    for (let k = 0; k < 3; k++) {
      expect(rel[6 + k]).not.toBe(rel[9 + k]);
      expect(Math.abs(rel[9 + k] - rel[6 + k] - 0.001)).toBeLessThan(0.0005);
    }
    expect(maxRoundTripError(xyz, rel, o)).toBeLessThanOrEqual(0.0005);
    // Without the origin they would merge into one float32.
    const raw = toRelativeFloat32(xyz, { x: 0, y: 0, z: 0 });
    expect(raw[6]).toBe(raw[9]);
    expect(raw[7]).toBe(raw[10]);
  });

  it("places a part by a float64 offset from an integer scene origin, within 0.5 mm on screen", () => {
    const scene = new SceneOrigin();
    expect(scene.value).toBeNull();
    const o = scene.ensure([500_000.4, 7_000_000.6, -300.2, 516_000.4, 7_016_000.6, 900.8]);
    expect(o).toEqual({ x: 508_000, y: 7_008_001, z: 300 });
    expect(scene.ensure([0, 0, 0, 1, 1, 1])).toBe(o); // the first object fixes it
    const part = { x: 514_321, y: 7_013_210, z: 120 };
    const offset = scene.offset(part);
    expect(offset).toEqual([6_321, 5_209, -180]);
    // A point of the part: float32 from the part origin, plus the offset as float32 in the matrix.
    const abs: Vec3 = [514_321 + 1234.5678, 7_013_210 - 2345.6781, 120 + 456.7891];
    const local = Float32Array.from([abs[0] - part.x, abs[1] - part.y, abs[2] - part.z]);
    const shown = [0, 1, 2].map((k) => Math.fround(offset[k]) + local[k]);
    const exact = scene.toScene(abs);
    for (let k = 0; k < 3; k++) expect(Math.abs(shown[k] - exact[k])).toBeLessThanOrEqual(0.0005);
    expect(scene.toAbsolute(exact)).toEqual(abs);
  });

  it("moves to the camera target farther than 4 km, the camera and the planes follow", () => {
    const scene = new SceneOrigin();
    scene.ensure([500_000, 7_000_000, 0, 500_000, 7_000_000, 0]);
    const vexag = 2;
    const cam: CameraState = { position: [100, 200, 900], target: [100, 200, 300], up: [0, 0, 1], half: 50, near: 0.1, far: 5000 };
    expect(scene.follow(cam.target, vexag)).toBeNull();
    const far: CameraState = { ...cam, target: [RECENTER_DISTANCE + 10.4, -20.6, 100], position: [RECENTER_DISTANCE + 10.4, -20.6, 700] };
    const before = scene.renderedToAbsolute(far.target, vexag);
    const delta = scene.follow(far.target, vexag)!;
    expect(delta).toEqual([4010, -21, 50]);
    expect(scene.value).toEqual({ x: 504_010, y: 6_999_979, z: 50 });
    const moved = rebaseCamera(far, delta, vexag);
    expect(scene.renderedToAbsolute(moved.target, vexag)).toEqual(before);
    expect(moved.position[2] - moved.target[2]).toBe(far.position[2] - far.target[2]);
    expect(Math.hypot(moved.target[0], moved.target[1])).toBeLessThan(1);
    // The clipping planes are recomputed from the new origin and still cut at the same place.
    const line = { x0: 504_000.5, y0: 6_999_990.25, az: 30, length: null, towards: 25, away: 25 };
    const p: Vec3 = [504_012.75, 6_999_975.5, 0];
    const oldPlanes = clipPlanes(line, { x: 500_000, y: 7_000_000 });
    const newPlanes = clipPlanes(line, scene.value!);
    for (let i = 0; i < 2; i++) {
      const dOld = planeDistance(oldPlanes[i], [p[0] - 500_000, p[1] - 7_000_000, 0]);
      const dNew = planeDistance(newPlanes[i], scene.toScene(p));
      expect(dNew).toBeCloseTo(dOld, 9);
    }
    scene.reset();
    expect(scene.value).toBeNull();
    expect(() => scene.offset({ x: 0, y: 0, z: 0 })).toThrow();
  });
});
