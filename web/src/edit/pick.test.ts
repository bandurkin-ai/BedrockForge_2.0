import { describe, expect, it } from "vitest";
import { emptyLayer } from "./model";
import type { EditLayer, Vec3 } from "./model";
import { addLine, insertVertex } from "./ops";
import { linesInBox, nearestSegment, nearestVertex, verticesInBox } from "./pick";
import type { ViewTransform } from "./snap";
import { ortho, perspective } from "./testUtil";

const E = 512000;
const N = 6789000;
/** Plan view: the pixel of (E + x, N + y) is (x, 500 − y). */
const plan: ViewTransform = { matrix: ortho(E, E + 1000, N, N + 500, -1000, 1000), width: 1000, height: 500 };
const P = (x: number, y: number, z = 0): Vec3 => [E + x, N + y, z];

/** Line 1: open; line 2: closed square; line 3: far away, partly outside the viewport. */
function sample(): EditLayer {
  let layer = emptyLayer();
  layer = addLine(layer, [P(100, 100), P(200, 100, 50), P(300, 200, 100)]).layer;
  layer = addLine(layer, [P(500, 100), P(600, 100), P(600, 200), P(500, 200)], true).layer;
  layer = addLine(layer, [P(900, 400), P(1200, 400), P(900, 5000)]).layer;
  return layer;
}

describe("nearestVertex", () => {
  it("finds the nearest vertex within the radius", () => {
    const layer = sample();
    const hit = nearestVertex(layer, plan, 203, 396, 8)!;
    expect(hit.ref).toEqual({ line: 1, index: 1 });
    expect(hit.distancePx).toBeCloseTo(5, 9);
    expect(nearestVertex(layer, plan, 203, 396, 4)).toBeNull();
    expect(nearestVertex(layer, plan, 549, 340, 80)!.ref).toEqual({ line: 2, index: 3 });
    expect(nearestVertex(layer, plan, 551, 340, 80)!.ref).toEqual({ line: 2, index: 2 });
    expect(nearestVertex(emptyLayer(), plan, 0, 0, 100)).toBeNull();
  });

  it("skips hidden vertices and vertices behind the camera", () => {
    const layer = sample();
    const clipped: ViewTransform = { ...plan, visible: (_x, _y, z) => z < 25 };
    expect(nearestVertex(layer, clipped, 203, 396, 8)).toBeNull();
    expect(nearestVertex(layer, clipped, 103, 396, 8)!.ref).toEqual({ line: 1, index: 0 });
    const cam: ViewTransform = { matrix: perspective(60, 800 / 600, 1, 1000), width: 800, height: 600 };
    const l = addLine(emptyLayer(), [[0, 0, 10], [0, 0, -10]]).layer;
    expect(nearestVertex(l, cam, 400, 300, 5)!.ref).toEqual({ line: 1, index: 1 });
  });
});

describe("nearestSegment", () => {
  it("finds the segment and the parameter for inserting a vertex", () => {
    const layer = sample();
    const hit = nearestSegment(layer, plan, 140, 397, 6)!;
    expect(hit).toMatchObject({ line: 1, segment: 0 });
    expect(hit.t).toBeCloseTo(0.4, 12);
    expect(hit.distancePx).toBeCloseTo(3, 9);
    expect(nearestSegment(layer, plan, 140, 390, 6)).toBeNull();
    const second = nearestSegment(layer, plan, 250, 350, 6)!;
    expect(second).toMatchObject({ line: 1, segment: 1 });
    expect(second.t).toBeCloseTo(0.5, 12);
    // the parameter is ready for insertVertex
    const a = layer.lines[0].points[1];
    const b = layer.lines[0].points[2];
    const p: Vec3 = [a[0] + (b[0] - a[0]) * second.t, a[1] + (b[1] - a[1]) * second.t, a[2] + (b[2] - a[2]) * second.t];
    const next = insertVertex(layer, second.line, second.segment, p);
    expect(next.lines[0].points.length).toBe(4);
    expect(next.lines[0].points[2][0]).toBeCloseTo(E + 250, 8);
    expect(next.lines[0].points[2][1]).toBeCloseTo(N + 150, 8);
    expect(next.lines[0].points[2][2]).toBeCloseTo(75, 8);
  });

  it("knows the closing segment of a polygon and clamps at the ends", () => {
    const layer = sample();
    const closing = nearestSegment(layer, plan, 498, 325, 6)!;
    expect(closing).toMatchObject({ line: 2, segment: 3 });
    expect(closing.t).toBeCloseTo(0.25, 12);
    const end = nearestSegment(layer, plan, 96, 400, 6)!;
    expect(end).toMatchObject({ line: 1, segment: 0, t: 0 });
    expect(end.distancePx).toBeCloseTo(4, 9);
    // an open line has no closing segment
    expect(nearestSegment(layer, plan, 200, 350, 6)).toBeNull();
    // a segment that leaves the viewport
    const out = nearestSegment(layer, plan, 990, 102, 6)!;
    expect(out).toMatchObject({ line: 3, segment: 0 });
    expect(out.t).toBeCloseTo(0.3, 12);
    const clipped: ViewTransform = { ...plan, visible: (_x, _y, z) => z < 10 };
    expect(nearestSegment(layer, clipped, 140, 397, 6)).toBeNull();
    expect(nearestSegment(layer, clipped, 110, 397, 6)!.t).toBeCloseTo(0.1, 12);
  });
});

describe("box selection", () => {
  it("returns the lines with a vertex in the rectangle", () => {
    const layer = sample();
    expect(linesInBox(layer, plan, 0, 0, 1000, 500)).toEqual([1, 2, 3]);
    expect(linesInBox(layer, plan, 90, 410, 210, 390)).toEqual([1]);
    // any two opposite corners
    expect(linesInBox(layer, plan, 610, 290, 590, 410)).toEqual([2]);
    expect(linesInBox(layer, plan, 400, 0, 450, 500)).toEqual([]);
    // a segment crossing the box without a vertex in it does not count
    expect(linesInBox(layer, plan, 140, 390, 160, 410)).toEqual([]);
  });

  it("returns the vertices in the rectangle", () => {
    const layer = sample();
    expect(verticesInBox(layer, plan, 90, 290, 310, 410)).toEqual([
      { line: 1, index: 0 },
      { line: 1, index: 1 },
      { line: 1, index: 2 },
    ]);
    expect(verticesInBox(layer, plan, 590, 290, 610, 410)).toEqual([
      { line: 2, index: 1 },
      { line: 2, index: 2 },
    ]);
    expect(verticesInBox(layer, plan, 0, 0, 2000, 500).filter((r) => r.line === 3)).toEqual([
      { line: 3, index: 0 },
      { line: 3, index: 1 },
    ]);
    const clipped: ViewTransform = { ...plan, visible: (_x, _y, z) => z < 75 };
    expect(verticesInBox(layer, clipped, 90, 290, 310, 410).length).toBe(2);
    expect(linesInBox(layer, { ...plan, visible: () => false }, 0, 0, 1000, 500)).toEqual([]);
  });
});
