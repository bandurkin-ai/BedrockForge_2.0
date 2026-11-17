import { describe, expect, it } from "vitest";
import { nearestPoint, nearestSegment, segmentDistance } from "../src/viz/pick/holes";
import { multiply, partModel, projectToScreen, viewProjection } from "../src/viz/pick/ray";
import { lookFrom } from "../src/viz/core/views";

const W = 800;
const H = 600;
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };

describe("projection for picking (float64)", () => {
  it("a top view maps X to the right and Y up the screen, the target to the centre", () => {
    const cam = lookFrom([0, -1e-4, 1], { min: [-100, -100, 0], max: [100, 100, 10] }, opts);
    const m = viewProjection(cam, opts);
    const c = projectToScreen(m, cam.target[0], cam.target[1], cam.target[2], W, H)!;
    expect(c[0]).toBeCloseTo(W / 2, 6);
    expect(c[1]).toBeCloseTo(H / 2, 6);
    const right = projectToScreen(m, cam.target[0] + 10, cam.target[1], cam.target[2], W, H)!;
    const up = projectToScreen(m, cam.target[0], cam.target[1] + 10, cam.target[2], W, H)!;
    expect(right[0]).toBeGreaterThan(c[0]);
    expect(up[1]).toBeLessThan(c[1]);
    // the scale: half the height of the view is cam.half units
    expect(c[1] - up[1]).toBeCloseTo((10 / cam.half) * (H / 2), 4);
  });

  it("a part model places float32 offsets at origin − scene origin and stretches Z", () => {
    const model = partModel([1000, -2000, 50], 3);
    const p = multiply(model, Float64Array.of(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1));
    expect([p[12], p[13], p[14]]).toEqual([1000, -2000, 150]);
    expect(p[10]).toBe(3);
  });

  it("matches the matrices of three.js cameras (ortho and perspective)", async () => {
    const THREE = await import("three");
    const cam = lookFrom([0.9, -1.2, 0.8], { min: [-300, -200, -50], max: [400, 250, 80] }, opts);
    const ortho = new THREE.OrthographicCamera(-cam.half * opts.aspect, cam.half * opts.aspect, cam.half, -cam.half, cam.near, cam.far);
    ortho.up.set(0, 0, 1);
    ortho.position.set(...cam.position);
    ortho.lookAt(...cam.target);
    ortho.updateMatrixWorld();
    ortho.updateProjectionMatrix();
    const ref = new THREE.Matrix4().multiplyMatrices(ortho.projectionMatrix, ortho.matrixWorldInverse).elements;
    viewProjection(cam, opts).forEach((v, i) => expect(v).toBeCloseTo(ref[i], 9));
    const popts = { ...opts, perspective: true };
    const pc = lookFrom([0.9, -1.2, 0.8], { min: [-300, -200, -50], max: [400, 250, 80] }, popts);
    const persp = new THREE.PerspectiveCamera(45, popts.aspect, pc.near, pc.far);
    persp.up.set(0, 0, 1);
    persp.position.set(...pc.position);
    persp.lookAt(...pc.target);
    persp.updateMatrixWorld();
    persp.updateProjectionMatrix();
    const pref = new THREE.Matrix4().multiplyMatrices(persp.projectionMatrix, persp.matrixWorldInverse).elements;
    viewProjection(pc, popts).forEach((v, i) => expect(v).toBeCloseTo(pref[i], 9));
  });

  it("points behind the near or far plane are not projected", () => {
    const cam = { ...lookFrom([0, -1, 0], { min: [-10, -10, -10], max: [10, 10, 10] }, opts) };
    const m = viewProjection(cam, opts);
    expect(projectToScreen(m, 0, cam.position[1] - 1, 0, W, H)).toBeNull();
  });
});

describe("hole picking on the screen", () => {
  const cam = lookFrom([0, -1e-4, 1], { min: [-100, -100, 0], max: [100, 100, 10] }, opts);
  const m = viewProjection(cam, opts);

  it("the nearest collar within 8 px, none beyond", () => {
    const xyz = Float64Array.of(0, 0, 0, 30, 0, 0, 0.5, 0, 0);
    const at = projectToScreen(m, 0.5, 0, 0, W, H)!;
    const hit = nearestPoint(m, xyz, 3, at[0] + 1, at[1], W, H, 8);
    expect(hit?.index).toBe(2);
    const far = projectToScreen(m, 30, 0, 0, W, H)!;
    expect(nearestPoint(m, xyz, 3, far[0] + 20, far[1], W, H, 8)).toBeNull();
    expect(nearestPoint(m, xyz, 3, far[0], far[1], W, H, 8, (i) => i !== 1)).toBeNull();
  });

  it("the nearest segment within 6 px, with the parameter along it", () => {
    // two segments: (0,0)-(50,0) and (0,20)-(50,20)
    const pos = Float32Array.of(0, 0, 0, 50, 0, 0, 0, 20, 0, 50, 20, 0);
    const a = projectToScreen(m, 25, 0, 0, W, H)!;
    const hit = nearestSegment(m, pos, 2, a[0], a[1] - 3, W, H, 6);
    expect(hit?.segment).toBe(0);
    expect(hit?.t).toBeCloseTo(0.5, 6);
    expect(hit?.distance).toBeCloseTo(3, 4);
    expect(nearestSegment(m, pos, 2, a[0], a[1] - 30, W, H, 6)).toBeNull();
    expect(nearestSegment(m, pos, 2, a[0], a[1] - 3, W, H, 6, (s) => s !== 0)).toBeNull();
  });

  it("distance to a segment: inside, past the ends, a zero-length one", () => {
    expect(segmentDistance(5, 3, 0, 0, 10, 0)).toEqual({ d: 3, t: 0.5 });
    expect(segmentDistance(-3, 4, 0, 0, 10, 0)).toEqual({ d: 5, t: 0 });
    expect(segmentDistance(13, 4, 0, 0, 10, 0)).toEqual({ d: 5, t: 1 });
    expect(segmentDistance(3, 4, 0, 0, 0, 0)).toEqual({ d: 5, t: 0 });
  });
});
