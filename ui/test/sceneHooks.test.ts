/** The pure pieces taken out of `Scene.ts` (Э7 review: the scene stays within 600 lines): the orbit of the
 * frame benchmark and the E2E hook «where is this project point on the screen». */
import fs from "node:fs";
import { describe, expect, it } from "vitest";
import { lookFrom } from "../src/viz/core/views";
import { viewProjection } from "../src/viz/pick/ray";
import { orbited, screenOf } from "../src/viz/sceneHooks";

const W = 800;
const H = 600;
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
const canvas = (left: number, top: number) => ({ getBoundingClientRect: () => ({ left, top }) }) as unknown as HTMLCanvasElement;

describe("pieces of the scene (viz/sceneHooks.ts)", () => {
  it("orbits about the vertical through the centre: the distance, the height and the target stay", () => {
    const cam = lookFrom([0.9, -1.2, 0.8], { min: [-300, -200, -50], max: [400, 250, 80] }, opts);
    const turned = orbited(cam, Math.PI / 2);
    const [dx, dy] = [cam.position[0] - cam.target[0], cam.position[1] - cam.target[1]];
    expect(turned.position[0] - turned.target[0]).toBeCloseTo(-dy, 9);
    expect(turned.position[1] - turned.target[1]).toBeCloseTo(dx, 9);
    expect(turned.position[2]).toBe(cam.position[2]);
    expect([turned.target, turned.half, turned.up]).toEqual([cam.target, cam.half, cam.up]);
    const back = orbited(orbited(cam, 1.25), -1.25);
    back.position.forEach((v, i) => expect(v).toBeCloseTo(cam.position[i], 9));
    expect(orbited(cam, 0).position).toEqual(cam.position);
  });

  it("puts a project point on the screen in client px, from the scene origin and the exaggeration", () => {
    const cam = lookFrom([0, -1e-4, 1], { min: [-100, -100, 0], max: [100, 100, 10] }, opts);
    const vp = viewProjection(cam, opts);
    const origin = { x: 512_000, y: 5_912_000, z: 400 }; // UTM: the scene is float64 minus this integer point
    const size = { w: W, h: H };
    const at = (dx: number, dy: number, dz = 0): [number, number, number] => [origin.x + cam.target[0] + dx, origin.y + cam.target[1] + dy, origin.z + cam.target[2] + dz];
    const centre = screenOf(vp, origin, 1, size, canvas(30, 70), at(0, 0))!;
    expect(centre.x).toBeCloseTo(W / 2 + 30, 6);
    expect(centre.y).toBeCloseTo(H / 2 + 70, 6);
    const right = screenOf(vp, origin, 1, size, canvas(30, 70), at(10, 0))!;
    expect(right.x - centre.x).toBeCloseTo((10 / cam.half) * (H / 2), 4);
    expect(right.y).toBeCloseTo(centre.y, 4);
    // a side view: the height on the screen is stretched by the vertical exaggeration
    const side = lookFrom([0, -1, 0], { min: [-100, -100, 0], max: [100, 100, 10] }, opts);
    const m = viewProjection(side, opts);
    const base = screenOf(m, origin, 3, size, canvas(0, 0), [origin.x, origin.y, origin.z])!;
    const above = screenOf(m, origin, 3, size, canvas(0, 0), [origin.x, origin.y, origin.z + 5])!;
    expect(base.y - above.y).toBeCloseTo((15 / side.half) * (H / 2), 4);
    // nothing drawn yet
    expect(screenOf(null, origin, 1, size, canvas(0, 0), at(0, 0))).toBeNull();
    expect(screenOf(vp, null, 1, size, canvas(0, 0), at(0, 0))).toBeNull();
  });

  it("keeps Scene.ts within 600 lines (ТЗ 17)", () => {
    const lines = fs.readFileSync(new URL("../src/viz/Scene.ts", import.meta.url), "utf8").split("\n").length;
    expect(lines).toBeLessThanOrEqual(600);
  });
});
