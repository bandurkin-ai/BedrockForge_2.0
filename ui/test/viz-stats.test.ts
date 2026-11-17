import { describe, expect, it } from "vitest";
import { bytesText, FpsTracker } from "../src/viz/core/stats";

describe("frame counter", () => {
  it("frames per second of consecutive frames: current, minimum, mean; ms per frame", () => {
    const f = new FpsTracker();
    let t = 1000;
    for (let i = 0; i < 120; i++, t += 1000 / 60) f.record(t, 4);
    for (let i = 0; i < 60; i++, t += 1000 / 30) f.record(t, 8);
    const s = f.summary(t - 1000 / 30);
    expect(s.current).toBeCloseTo(30, 0);
    expect(s.min).toBeCloseTo(30, 0);
    expect(s.mean).toBeGreaterThan(30);
    expect(s.mean).toBeLessThan(60);
    expect(s.msPerFrame).toBeCloseTo((120 * 4 + 60 * 8) / 180, 6);
    expect(s.frames).toBe(180);
  });

  it("an idle view is not «0 frames per second»: a pause starts a new run", () => {
    const f = new FpsTracker();
    for (let i = 0; i < 10; i++) f.record(i * 20, 1);
    f.record(5000, 1); // a frame on demand after 5 s of nothing
    const s = f.summary(9000);
    expect(s.current).toBe(0);
    expect(s.mean).toBeCloseTo(50, 6);
    f.reset();
    expect(f.summary(0)).toEqual({ current: 0, min: 0, mean: 0, msPerFrame: 0, frames: 0 });
    expect(bytesText(340 * 1024 * 1024)).toBe("340 МБ");
    expect(bytesText(1.5 * 1024 ** 3)).toBe("1,5 ГБ");
    expect(bytesText(0.3 * 1024 * 1024)).toBe("0,3 МБ");
  });
});
