// The traces of hole sets take the class of graphics of the scene (review 11.21; `paths.ts`: software rendering —
// 200 thousand segments, real graphics — a million): before, `SceneHoles` asked without the class, so under
// SwiftShader the traces stayed fat up to a million. A set is a stub here: only its segments and its fat switch.
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import type { GpuClass } from "../src/viz/core/budget";
import { SceneHoles, type HolesHost } from "../src/viz/holes/sceneHoles";
import { DEFAULT_TRACE_STYLE } from "../src/viz/holes/traceStyle";
import type { BuiltPaths } from "../src/viz/worker/paths";

function holes(gpu?: GpuClass) {
  const host = { root: new THREE.Group(), origin: {}, context: () => ({}), contentChanged: vi.fn(), gridChanged: vi.fn(), invalidate: vi.fn(), ...(gpu ? { gpu: () => gpu } : {}) } as unknown as HolesHost;
  const sh = new SceneHoles(host, [], new THREE.Vector2(800, 600));
  const fat: boolean[] = [];
  const built: boolean[] = [];
  let segments = 0;
  const layer = {
    style: { trace: { ...DEFAULT_TRACE_STYLE, width: 2 } },
    get segments() {
      return segments;
    },
    setFat: (f: boolean) => void fat.push(f),
    setPaths: (_k: number, b: BuiltPaths, f: boolean) => {
      segments += b.segments;
      built.push(f);
    },
  };
  (sh as unknown as { sets: Map<number, unknown> }).sets.set(1, layer);
  const part = (n: number) => sh.setPaths(1, 0, { segments: n } as BuiltPaths);
  return { part, fat, built };
}

describe("the threshold of thin traces by the class of graphics of the scene", () => {
  it("software rendering: 300 thousand segments are thin, and the part is built thin at once", () => {
    const { part, fat, built } = holes("software");
    part(150_000);
    expect(built).toEqual([true]);
    part(150_000);
    expect(built).toEqual([true, false]); // chosen before the build, with its segments counted
    expect(fat.at(-1)).toBe(false);
  });

  it("real graphics (or a host that does not say): a million as before", () => {
    for (const gpu of ["integrated", undefined] as const) {
      const { part, fat, built } = holes(gpu);
      part(150_000);
      part(150_000);
      expect(built).toEqual([true, true]);
      expect(fat.every(Boolean)).toBe(true);
      part(800_000);
      expect(built.at(-1)).toBe(false);
    }
  });
});
