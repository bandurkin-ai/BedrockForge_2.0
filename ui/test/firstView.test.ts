// The first view of the 3D window (viz/core/firstView.ts): the layers of an opened project arrive one by one —
// the view is fitted to the first content and again while there is more of it, until the camera is taken.
import { describe, expect, it } from "vitest";
import type { Box3 } from "../src/viz/core/bounds";
import { FirstView } from "../src/viz/core/firstView";

const box = (x0: number, y0: number, z0: number, x1: number, y1: number, z1: number): Box3 => ({ min: [x0, y0, z0], max: [x1, y1, z1] });

describe("FirstView", () => {
  it("fits the first content and every time it grows, not when it stays or shrinks", () => {
    const v = new FirstView();
    expect(v.grown(null)).toBe(false); // nothing yet
    const holes = box(5000, 8000, 230, 5350, 8250, 426);
    expect(v.grown(holes)).toBe(true);
    expect(v.grown(holes)).toBe(false); // the same content: a style or a colour changed
    expect(v.grown(box(5010, 8010, 240, 5300, 8200, 420))).toBe(false); // a layer was hidden
    expect(v.grown(box(4900, 7900, 230, 5500, 8350, 432))).toBe(true); // the relief came: wider and higher
    // a metre deeper (the plan dropped to the floor when its DTM was hidden) is under 2 % of 777 m: not «more»
    expect(v.grown(box(4900, 7900, 229, 5500, 8350, 432))).toBe(false);
    expect(v.grown(box(4900, 7900, 200, 5500, 8350, 432))).toBe(true); // 30 m deeper is
  });

  it("keeps what it was given: the caller may reuse its box", () => {
    const v = new FirstView();
    const b = box(0, 0, 0, 10, 10, 10);
    expect(v.grown(b)).toBe(true);
    b.max[0] = 20; // the same object, changed in place
    expect(v.grown(b)).toBe(true);
  });

  it("never moves the view once the camera is taken", () => {
    const v = new FirstView();
    expect(v.grown(box(0, 0, 0, 10, 10, 10))).toBe(true);
    v.stop(); // the user turned the view, pressed «Вписать» or applied a saved view
    expect(v.grown(box(-100, -100, -100, 100, 100, 100))).toBe(false);
    const early = new FirstView();
    early.stop(); // before any content (the projection of the view is set at the start): the first is still fitted
    expect(early.grown(box(0, 0, 0, 1, 1, 1))).toBe(true);
    expect(early.grown(box(0, 0, 0, 2, 2, 2))).toBe(true);
  });
});
