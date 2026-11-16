import { describe, expect, it } from "vitest";
import { integerOrigin, maxRoundTripError, toRelativeFloat32 } from "../src/viz/origin";

/** Deterministic PRNG so the test is reproducible. */
function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

describe("float32 relative to integer origin (ТЗ 10.2, 3D accuracy)", () => {
  it("200 000 points over 16 km in UTM stay within 0.5 mm", () => {
    const rnd = lcg(42);
    const n = 200_000;
    const xyz = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) {
      xyz[i * 3] = 500_000 + rnd() * 16_000;
      xyz[i * 3 + 1] = 7_000_000 + rnd() * 16_000;
      xyz[i * 3 + 2] = -500 + rnd() * 2_000;
    }
    const o = integerOrigin(xyz);
    expect(Number.isInteger(o.x) && Number.isInteger(o.y) && Number.isInteger(o.z)).toBe(true);
    const rel = toRelativeFloat32(xyz, o);
    expect(maxRoundTripError(xyz, rel, o)).toBeLessThanOrEqual(0.0005);
  });

  it("without the origin float32 loses metres-level precision (guard)", () => {
    const xyz = new Float64Array([500_000.123, 7_000_000.456, 100.789]);
    const rel = toRelativeFloat32(xyz, { x: 0, y: 0, z: 0 });
    expect(maxRoundTripError(xyz, rel, { x: 0, y: 0, z: 0 })).toBeGreaterThan(0.01);
  });
});
