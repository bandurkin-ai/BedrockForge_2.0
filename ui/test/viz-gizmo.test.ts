import { describe, expect, it } from "vitest";
import { TURN_MS, TurnController } from "../src/viz/core/turn";
import { dot, type Vec3 } from "../src/viz/core/vec3";
import { cameraBasis, view, viewDirection, type ViewOptions } from "../src/viz/core/views";
import { GIZMO, gizmoAxes, gizmoDirection, gizmoHit, gizmoTitles, toViewBox } from "../src/viz/gizmo/math";
import { declutter, LABEL_PRIORITY, type LabelBox } from "../src/viz/labels/declutter";

const opts: ViewOptions = { aspect: 1.5, perspective: false, fov: 45, vexag: 1 };
const box = { min: [-100, -100, -100] as Vec3, max: [100, 100, 100] as Vec3 };

describe("axes gizmo (ТЗ 7.13)", () => {
  const iso = view("iso", box, opts);

  it("projects the axes with radius 33, the nearest on top, the far ones dimmed", () => {
    const axes = gizmoAxes(iso);
    expect(axes.map((a) => a.key)).toEqual(["y", "z", "x"]); // drawing order: far → near (X is 0.9, Z 0.8 towards the viewer)
    const byKey = Object.fromEntries(axes.map((a) => [a.key, a]));
    for (const a of axes) expect(Math.hypot(a.x, a.y)).toBeLessThanOrEqual(GIZMO.radius + 1e-9);
    expect(byKey.y.away).toBe(true); // iso looks from the south: +Y goes away from the viewer
    expect(byKey.y.opacity).toBe(0.45);
    expect(byKey.x.away).toBe(false);
    expect(byKey.x.opacity).toBe(1);
    expect(byKey.z.depth).toBeGreaterThan(0);
    expect(byKey.z.y).toBeLessThan(0); // Z points up the screen: SVG Y down
    expect(byKey.x.backX).toBeCloseTo(-byKey.x.x * 0.8, 12);
    // In plan Z points straight at the viewer, X right, Y up.
    const top = Object.fromEntries(gizmoAxes(view("top", box, opts)).map((a) => [a.key, a]));
    expect(top.x.x).toBeCloseTo(33, 6);
    expect(top.y.y).toBeCloseTo(-33, 2);
    expect(Math.hypot(top.z.x, top.z.y)).toBeLessThan(0.01);
    expect(top.z.depth).toBeCloseTo(1, 6);
    // A flipped Z points down.
    const flipped = Object.fromEntries(gizmoAxes(iso, true).map((a) => [a.key, a]));
    expect(flipped.z.y).toBeCloseTo(-byKey.z.y, 12);
  });

  it("a click on the head (r = 8) looks along the axis, on the circle opposite (r = 4) from its negative side", () => {
    const axes = gizmoAxes(iso);
    const z = axes.find((a) => a.key === "z")!;
    expect(gizmoHit(axes, z.x + 7.9, z.y)).toEqual({ key: "z", sign: 1 });
    expect(gizmoHit(axes, z.x + 8.1, z.y)).not.toEqual({ key: "z", sign: 1 });
    expect(gizmoHit(axes, z.backX, z.backY + 3.9)).toEqual({ key: "z", sign: -1 });
    expect(gizmoHit(axes, z.backX, z.backY + 4.1)).not.toEqual({ key: "z", sign: -1 });
    expect(gizmoHit(axes, 40, 40)).toBeNull();
    expect(gizmoDirection({ key: "z", sign: 1 })).toEqual([0, 0, 1]);
    expect(gizmoDirection({ key: "z", sign: -1 })).toEqual([-0, -0, -1]);
    expect(gizmoDirection({ key: "x", sign: -1 })).toEqual([-1, -0, -0]);
    expect(gizmoDirection({ key: "z", sign: 1 }, true)).toEqual([-0, -0, -1]);
    // CSS px of the 92 × 92 box → viewBox.
    expect(toViewBox(46, 46)).toEqual([0, 0]);
    expect(toViewBox(0, 92)).toEqual([-46, 46]);
    expect(gizmoTitles("x").head).toBe("Вид вдоль оси X (с её положительной стороны)");
  });

  it("the Z head gives the view from above, the Z circle from below; both keep Y up", () => {
    const turns = new TurnController();
    for (const sign of [1, -1] as const) {
      const axes = gizmoAxes(iso);
      const z = axes.find((a) => a.key === "z")!;
      const hit = sign === 1 ? gizmoHit(axes, z.x, z.y)! : gizmoHit(axes, z.backX, z.backY)!;
      expect(hit).toEqual({ key: "z", sign });
      const token = turns.begin(iso, gizmoDirection(hit), 0, { ortho: true, box })!;
      const end = turns.frame(token, TURN_MS)!.camera;
      expect(viewDirection(end)[2] * sign).toBeGreaterThan(0.9999);
      expect(dot(cameraBasis(end).y, [0, 1, 0])).toBeGreaterThan(0.9999);
      expect(end.target).toEqual(iso.target);
      expect(end.half).toBe(iso.half);
    }
  });
});

describe("label decluttering (ADR 0007 §5)", () => {
  const screen = { width: 800, height: 600 };
  const at = (x: number, y: number, priority: number, w = 40, h = 12): LabelBox => ({ x, y, w, h, priority });

  it("keeps the higher priority: crossings, then end depths, then names, then ticks", () => {
    const labels = [
      at(100, 100, LABEL_PRIORITY.tick),
      at(105, 102, LABEL_PRIORITY.name),
      at(110, 98, LABEL_PRIORITY.depth),
      at(112, 100, LABEL_PRIORITY.crossing),
      at(400, 300, LABEL_PRIORITY.tick),
    ];
    expect([...declutter(labels, screen)]).toEqual([0, 0, 0, 1, 1]);
    expect([...declutter(labels.slice(0, 3), screen)]).toEqual([0, 0, 1]);
    expect([...declutter(labels.slice(0, 2), screen)]).toEqual([0, 1]);
  });

  it("equal priority goes by input order; separate cells all show", () => {
    const row = Array.from({ length: 10 }, (_, i) => at(i * 72, 200, LABEL_PRIORITY.tick)); // 3 cells apart
    expect([...declutter(row, screen)].every((v) => v === 1)).toBe(true);
    const pile = [at(300, 300, 1), at(301, 300, 1), at(302, 301, 1)];
    expect([...declutter(pile, screen)]).toEqual([1, 0, 0]);
  });

  it("hides labels off the screen or not projected without taking room", () => {
    const labels = [at(-100, 50, 3), at(900, 50, 3), at(NaN, 50, 3), at(10, 700, 3), at(-20, 50, 1), at(5, 50, 0)];
    expect([...declutter(labels, screen)]).toEqual([0, 0, 0, 0, 1, 0]);
  });

  it("copes with thousands of labels", () => {
    const many = Array.from({ length: 20_000 }, (_, i) => at((i * 37) % 800, (i * 53) % 600, i % 4));
    const t0 = performance.now();
    const shown = declutter(many, screen);
    expect(performance.now() - t0).toBeLessThan(200);
    const n = shown.reduce((s, v) => s + v, 0);
    expect(n).toBeGreaterThan(100);
    expect(n).toBeLessThan(2000); // 34 × 50 cells, a label takes 2–3 × 1–2
  });
});
