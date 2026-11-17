import { describe, expect, it } from "vitest";
import { fromWorld, toWorld } from "../src/viz/core/worldCamera";
import { cameraOf } from "../src/modules/views/state";

const origin = { x: 512000, y: 7104000, z: 300 };

describe("saved views: the camera (ТЗ 7.35 Д-09)", () => {
  it("float64 through the core JSON without loss", () => {
    const cam = { position: [512345.123456789, 7104321.987654321, 1234.000000001], target: [512300.5, 7104300.25, 250.125], up: [0, 0, 1], zoom: 123.456789, ortho: true, fov: 45 };
    expect(JSON.parse(JSON.stringify(cam))).toEqual(cam);
    expect(cameraOf(JSON.parse(JSON.stringify(cam)))).toEqual(cam);
  });

  it("absolute ↔ rendered: the same view at any exaggeration and scene origin", () => {
    const rendered = { position: [345.123456789, 321.987654321, 900.5], target: [300.5, 300.25, -149.625], up: [0, 0, 1], half: 120.5, near: 0.1, far: 5000 } as const;
    for (const vexag of [1, 2, 3, 5]) {
      const r = { ...rendered, target: [rendered.target[0], rendered.target[1], rendered.target[2] * vexag] as [number, number, number] };
      const world = toWorld({ ...r, position: [...r.position], up: [...r.up] }, origin, vexag, true, 45);
      expect(world.target[2]).toBeCloseTo(300 - 149.625, 9); // not stretched
      const back = fromWorld(world, origin, vexag, { near: 0.1, far: 5000 });
      for (let i = 0; i < 3; i++) {
        expect(Math.abs(back.position[i] - r.position[i])).toBeLessThan(1e-9);
        expect(Math.abs(back.target[i] - r.target[i])).toBeLessThan(1e-9);
      }
      expect(back.half).toBe(120.5);
    }
    // another scene origin of another session: the absolute camera is the same
    const w = toWorld({ ...rendered, position: [...rendered.position], target: [...rendered.target], up: [0, 0, 1] }, origin, 1, true, 45);
    const other = { x: 513000, y: 7105000, z: 0 };
    const again = toWorld(fromWorld(w, other, 1, { near: 1, far: 2 }), other, 1, true, 45);
    for (let i = 0; i < 3; i++) expect(Math.abs(again.position[i] - w.position[i])).toBeLessThan(1e-9);
  });

  it("a damaged camera is skipped (the view keys still apply)", () => {
    expect(cameraOf(null)).toBeNull();
    expect(cameraOf({ position: [1, 2], target: [0, 0, 0], zoom: 1 })).toBeNull();
    expect(cameraOf({ position: [1, 2, 3], target: [0, 0, 0], zoom: 0 })).toBeNull();
    expect(cameraOf({ position: [1, 2, 3], target: [0, 0, 0], zoom: 2 })).toMatchObject({ up: [0, 0, 1], ortho: true, fov: 45 });
  });
});
