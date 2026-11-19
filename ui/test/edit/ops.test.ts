import { describe, expect, it } from "vitest";
import { horizontalPlane, planeDistance, sectionPlane } from "../../src/edit/coords";
import { lineLength, planArea, polygonArea } from "../../src/edit/measure";
import { emptyLayer, lineById } from "../../src/edit/model";
import type { EditLayer, Vec3 } from "../../src/edit/model";
import {
  addLine,
  appendPoint,
  breakAt,
  copyLines,
  deleteLines,
  deleteVertices,
  densifyLine,
  insertVertex,
  joinLines,
  moveVertex,
  moveVertices,
  offsetLine,
  offsetPoints,
  putLines,
  reverseLine,
  setAttrs,
  setClosed,
  simplifyLine,
  smoothLine,
  takeLines,
  translateLines,
} from "../../src/edit/ops";

const E = 512345.67;
const N = 6789012.34;
const P = (x: number, y: number, z = 100): Vec3 => [E + x, N + y, z];
const pts = (layer: EditLayer, id: number): Vec3[] => lineById(layer, id)!.points;

/** Line 1: open, 4 points; line 2: closed square 10 × 10; line 3: open, 2 points. */
function sample(): EditLayer {
  let layer = emptyLayer();
  layer = addLine(layer, [P(0, 0), P(10, 0), P(20, 5), P(30, 5)], false, { code: "A" }).layer;
  layer = addLine(layer, [P(0, 20), P(10, 20), P(10, 30), P(0, 30)], true, { code: "B", grade: 1.5 }).layer;
  layer = addLine(layer, [P(50, 0), P(60, 0)]).layer;
  return layer;
}

describe("creating lines", () => {
  it("adds lines with new ids and copies the input", () => {
    const input: Vec3[] = [P(0, 0), P(1, 1)];
    const { layer, id } = addLine(emptyLayer(), input, false, { name: "контур" });
    expect(id).toBe(1);
    expect(layer.nextId).toBe(2);
    input[0][0] = 0;
    expect(pts(layer, 1)[0]).toEqual(P(0, 0));
    expect(lineById(layer, 1)!.attrs).toEqual({ name: "контур" });
    const second = addLine(layer, [P(5, 5)]);
    expect(second.id).toBe(2);
    expect(second.layer.lines[0]).toBe(layer.lines[0]);
    // a polygon needs three points; nothing is made of no points
    expect(lineById(addLine(layer, [P(0, 0), P(1, 0)], true).layer, 2)!.closed).toBe(false);
    expect(lineById(addLine(layer, [P(0, 0), P(1, 0), P(1, 1)], true).layer, 2)!.closed).toBe(true);
    expect(addLine(layer, []).layer).toBe(layer);
    expect(addLine(layer, []).id).toBe(-1);
  });

  it("continues a line from either end", () => {
    const layer = sample();
    const end = appendPoint(layer, 1, P(40, 0));
    expect(pts(end, 1).length).toBe(5);
    expect(pts(end, 1)[4]).toEqual(P(40, 0));
    const start = appendPoint(layer, 1, P(-10, 0), true);
    expect(pts(start, 1)[0]).toEqual(P(-10, 0));
    expect(pts(start, 1)[1]).toEqual(P(0, 0));
    // the other lines are shared, the old layer is untouched
    expect(end.lines[1]).toBe(layer.lines[1]);
    expect(pts(layer, 1).length).toBe(4);
    expect(appendPoint(layer, 99, P(0, 0))).toBe(layer);
  });

  it("closes and opens", () => {
    const layer = sample();
    expect(lineById(setClosed(layer, 1, true), 1)!.closed).toBe(true);
    expect(lineById(setClosed(layer, 2, false), 2)!.closed).toBe(false);
    expect(setClosed(layer, 3, true)).toBe(layer);
    expect(setClosed(layer, 2, true)).toBe(layer);
    expect(setClosed(layer, 99, true)).toBe(layer);
  });
});

describe("vertices", () => {
  it("moves one vertex", () => {
    const layer = sample();
    const next = moveVertex(layer, { line: 1, index: 3 }, [512399.123456789, 6789099.987654321, -12.5]);
    expect(pts(next, 1)[3]).toEqual([512399.123456789, 6789099.987654321, -12.5]);
    expect(pts(next, 1)[2]).toBe(pts(layer, 1)[2]);
    expect(moveVertex(layer, { line: 1, index: 4 }, P(0, 0))).toBe(layer);
    expect(moveVertex(layer, { line: 1, index: 0 }, P(0, 0))).toBe(layer);
  });

  it("moves several vertices by a delta", () => {
    const layer = sample();
    const next = moveVertices(
      layer,
      [
        { line: 1, index: 0 },
        { line: 1, index: 3 },
        { line: 2, index: 2 },
        { line: 2, index: 2 },
        { line: 7, index: 0 },
      ],
      [1, -2, 3],
    );
    expect(pts(next, 1)[0]).toEqual([E + 0 + 1, N + 0 - 2, 103]);
    expect(pts(next, 1)[3]).toEqual([E + 30 + 1, N + 5 - 2, 103]);
    expect(pts(next, 1)[1]).toBe(pts(layer, 1)[1]);
    expect(pts(next, 2)[2]).toEqual([E + 10 + 1, N + 30 - 2, 103]);
    expect(next.lines[2]).toBe(layer.lines[2]);
    expect(moveVertices(layer, [{ line: 1, index: 0 }], [0, 0, 0])).toBe(layer);
    expect(moveVertices(layer, [{ line: 1, index: 9 }], [1, 1, 1])).toBe(layer);
  });

  it("inserts a vertex on a segment, also on the closing one", () => {
    const layer = sample();
    const mid = insertVertex(layer, 1, 1, P(15, 2));
    expect(pts(mid, 1).map((p) => p[0] - E).map(Math.round)).toEqual([0, 10, 15, 20, 30]);
    const last = insertVertex(layer, 1, 2, P(25, 5));
    expect(pts(last, 1)[3]).toEqual(P(25, 5));
    expect(insertVertex(layer, 1, 3, P(0, 0))).toBe(layer);
    expect(insertVertex(layer, 1, -1, P(0, 0))).toBe(layer);
    // closed: segment 3 joins the last point and the first
    const closing = insertVertex(layer, 2, 3, P(0, 25));
    expect(pts(closing, 2).length).toBe(5);
    expect(pts(closing, 2)[4]).toEqual(P(0, 25));
    expect(insertVertex(layer, 2, 4, P(0, 0))).toBe(layer);
  });

  it("deletes vertices: lines vanish, polygons open", () => {
    const layer = sample();
    const first = deleteVertices(layer, [{ line: 1, index: 0 }]);
    expect(pts(first, 1)[0]).toEqual(P(10, 0));
    const lastOne = deleteVertices(layer, [{ line: 1, index: 3 }]);
    expect(pts(lastOne, 1).length).toBe(3);
    // a square without one corner is still a polygon, without two it is a line
    const tri = deleteVertices(layer, [{ line: 2, index: 1 }]);
    expect(lineById(tri, 2)).toMatchObject({ closed: true });
    expect(pts(tri, 2).length).toBe(3);
    const two = deleteVertices(layer, [
      { line: 2, index: 1 },
      { line: 2, index: 3 },
    ]);
    expect(lineById(two, 2)).toMatchObject({ closed: false });
    expect(pts(two, 2)).toEqual([P(0, 20), P(10, 30)]);
    // down to nothing
    const gone = deleteVertices(layer, [
      { line: 3, index: 0 },
      { line: 3, index: 1 },
      { line: 1, index: 2 },
    ]);
    expect(gone.lines.map((l) => l.id)).toEqual([1, 2]);
    expect(pts(gone, 1).length).toBe(3);
    const single = deleteVertices(layer, [{ line: 3, index: 1 }]);
    expect(pts(single, 3)).toEqual([P(50, 0)]);
    expect(deleteVertices(single, [{ line: 3, index: 0 }]).lines.length).toBe(2);
    expect(deleteVertices(layer, [{ line: 1, index: 17 }])).toBe(layer);
    expect(deleteVertices(layer, [])).toBe(layer);
  });
});

describe("whole lines", () => {
  it("translates (also by Z only)", () => {
    const layer = sample();
    const next = translateLines(layer, [1, 3], [0, 0, -15]);
    expect(pts(next, 1).map((p) => p[2])).toEqual([85, 85, 85, 85]);
    expect(pts(next, 1)[0][0]).toBe(E);
    expect(pts(next, 1)[0][1]).toBe(N);
    expect(next.lines[1]).toBe(layer.lines[1]);
    expect(translateLines(layer, [2], [0, 0, 0])).toBe(layer);
    expect(translateLines(layer, [42], [1, 1, 1])).toBe(layer);
  });

  it("copies with an optional shift", () => {
    const layer = sample();
    const { layer: next, ids } = copyLines(layer, [2, 1], [100, 0, 5]);
    expect(ids).toEqual([4, 5]);
    expect(next.nextId).toBe(6);
    expect(pts(next, 4)[0]).toEqual(P(100, 0, 105));
    expect(lineById(next, 5)).toMatchObject({ closed: true, attrs: { code: "B", grade: 1.5 } });
    expect(lineById(next, 5)!.attrs).not.toBe(lineById(layer, 2)!.attrs);
    const same = copyLines(layer, [3]);
    expect(pts(same.layer, 4)).toEqual(pts(layer, 3));
    expect(pts(same.layer, 4)).not.toBe(pts(layer, 3));
    expect(copyLines(layer, [77])).toEqual({ layer, ids: [] });
  });

  it("deletes", () => {
    const layer = sample();
    expect(deleteLines(layer, [1, 3]).lines.map((l) => l.id)).toEqual([2]);
    expect(deleteLines(layer, [1, 2, 3]).lines).toEqual([]);
    expect(deleteLines(layer, [1, 2, 3]).nextId).toBe(4);
    expect(deleteLines(layer, [9])).toBe(layer);
  });

  it("breaks an open line into two sharing the vertex", () => {
    const layer = sample();
    const { layer: next, ids } = breakAt(layer, { line: 1, index: 2 });
    expect(ids).toEqual([1, 4]);
    expect(next.lines.map((l) => l.id)).toEqual([1, 4, 2, 3]);
    expect(pts(next, 1)).toEqual([P(0, 0), P(10, 0), P(20, 5)]);
    expect(pts(next, 4)).toEqual([P(20, 5), P(30, 5)]);
    expect(lineById(next, 4)!.attrs).toEqual({ code: "A" });
    // at an end nothing happens
    expect(breakAt(layer, { line: 1, index: 0 })).toEqual({ layer, ids: [1] });
    expect(breakAt(layer, { line: 1, index: 3 }).layer).toBe(layer);
    expect(breakAt(layer, { line: 1, index: 8 }).layer).toBe(layer);
    expect(breakAt(layer, { line: 8, index: 0 })).toEqual({ layer, ids: [] });
  });

  it("breaks a closed line into one open line through the vertex", () => {
    const layer = sample();
    const { layer: next, ids } = breakAt(layer, { line: 2, index: 2 });
    expect(ids).toEqual([2]);
    expect(lineById(next, 2)!.closed).toBe(false);
    expect(pts(next, 2)).toEqual([P(10, 30), P(0, 30), P(0, 20), P(10, 20), P(10, 30)]);
    expect(lineLength(pts(next, 2), false)).toBeCloseTo(lineLength(pts(layer, 2), true), 9);
    const first = breakAt(layer, { line: 2, index: 0 }).layer;
    expect(pts(first, 2)).toEqual([P(0, 20), P(10, 20), P(10, 30), P(0, 30), P(0, 20)]);
  });

  it("joins two open lines by their nearest ends", () => {
    let layer = emptyLayer();
    layer = addLine(layer, [P(0, 0), P(10, 0)], false, { code: "A", name: "a" }).layer;
    layer = addLine(layer, [P(12, 0), P(20, 0)], false, { code: "B", extra: 5 }).layer; // a.end – b.start
    layer = addLine(layer, [P(20, 0), P(12, 0)]).layer; // a.end – b.end
    layer = addLine(layer, [P(-9, 0), P(-2, 0)]).layer; // a.start – b.end
    layer = addLine(layer, [P(-2, 0), P(-9, 0)]).layer; // a.start – b.start
    const xs = (l: EditLayer) => pts(l, 1).map((p) => Math.round(p[0] - E));

    const j2 = joinLines(layer, 1, 2)!;
    expect(j2.id).toBe(1);
    expect(xs(j2.layer)).toEqual([0, 10, 12, 20]);
    expect(lineById(j2.layer, 1)!.attrs).toEqual({ code: "A", name: "a", extra: 5 });
    expect(j2.layer.lines.map((l) => l.id)).toEqual([1, 3, 4, 5]);
    expect(xs(joinLines(layer, 1, 3)!.layer)).toEqual([0, 10, 12, 20]);
    expect(xs(joinLines(layer, 1, 4)!.layer)).toEqual([-9, -2, 0, 10]);
    expect(xs(joinLines(layer, 1, 5)!.layer)).toEqual([-9, -2, 0, 10]);
    // the other way round the direction of the first argument is kept
    expect(pts(joinLines(layer, 3, 1)!.layer, 3).map((p) => Math.round(p[0] - E))).toEqual([20, 12, 10, 0]);
  });

  it("does not duplicate a coincident end and refuses closed lines", () => {
    let layer = emptyLayer();
    layer = addLine(layer, [P(0, 0), P(10, 0)]).layer;
    layer = addLine(layer, [P(10, 0), P(10, 10)]).layer;
    layer = addLine(layer, [P(0, 0), P(0, 10), P(5, 5)], true).layer;
    layer = addLine(layer, [P(0, -5), P(0, 1e-7)]).layer;
    const joined = joinLines(layer, 1, 2)!;
    expect(pts(joined.layer, 1)).toEqual([P(0, 0), P(10, 0), P(10, 10)]);
    expect(pts(joinLines(layer, 2, 1)!.layer, 2)).toEqual([P(0, 0), P(10, 0), P(10, 10)]);
    // nearly coincident: kept apart unless a tolerance is given
    expect(pts(joinLines(layer, 1, 4)!.layer, 1).length).toBe(4);
    expect(pts(joinLines(layer, 1, 4, 1e-3)!.layer, 1)).toEqual([P(0, -5), P(0, 0), P(10, 0)]);
    expect(joinLines(layer, 1, 3)).toBeNull();
    expect(joinLines(layer, 3, 1)).toBeNull();
    expect(joinLines(layer, 1, 1)).toBeNull();
    expect(joinLines(layer, 1, 99)).toBeNull();
  });

  it("reverses", () => {
    const layer = sample();
    expect(pts(reverseLine(layer, 1), 1)).toEqual([P(30, 5), P(20, 5), P(10, 0), P(0, 0)]);
    // a polygon keeps its first point
    expect(pts(reverseLine(layer, 2), 2)).toEqual([P(0, 20), P(0, 30), P(10, 30), P(10, 20)]);
    expect(reverseLine(reverseLine(layer, 2), 2).lines[1].points).toEqual(pts(layer, 2));
    expect(reverseLine(layer, 99)).toBe(layer);
  });
});

describe("offsetLine", () => {
  const square = (reverse = false): EditLayer => {
    const p = [P(0, 0), P(10, 0), P(10, 10), P(0, 10)];
    return addLine(emptyLayer(), reverse ? p.reverse() : p, true, { code: "SQ" }).layer;
  };

  it("offsets a square outward and inward", () => {
    for (const reverse of [false, true]) {
      const layer = square(reverse);
      const out = offsetLine(layer, 1, 1)!;
      expect(out.id).toBe(2);
      expect(lineById(out.layer, 2)).toMatchObject({ closed: true, attrs: { code: "SQ" } });
      expect(pts(out.layer, 2).length).toBe(4);
      expect(polygonArea(pts(out.layer, 2))).toBeCloseTo(144, 7);
      expect(planArea(pts(out.layer, 2))).toBeCloseTo(144, 7);
      const xs = pts(out.layer, 2).map((p) => p[0] - E);
      expect(Math.min(...xs)).toBeCloseTo(-1, 7);
      expect(Math.max(...xs)).toBeCloseTo(11, 7);
      expect(pts(out.layer, 2).every((p) => Math.abs(p[2] - 100) < 1e-8)).toBe(true);
      const inner = offsetLine(layer, 1, -1.5)!;
      expect(polygonArea(pts(inner.layer, 2))).toBeCloseTo(49, 7);
      // the original is untouched
      expect(out.layer.lines[0]).toBe(layer.lines[0]);
    }
  });

  it("returns null when the polygon collapses or the line degenerates", () => {
    const layer = square();
    expect(offsetLine(layer, 1, -5)).toBeNull();
    expect(offsetLine(layer, 1, -6)).toBeNull();
    expect(offsetLine(layer, 1, NaN)).toBeNull();
    expect(offsetLine(layer, 9, 1)).toBeNull();
    const narrow = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(10, 2), P(0, 2)], true).layer;
    expect(offsetLine(narrow, 1, -3)).toBeNull();
    expect(offsetLine(narrow, 1, -0.5)).not.toBeNull();
    const dot = addLine(emptyLayer(), [P(0, 0)]).layer;
    expect(offsetLine(dot, 1, 1)).toBeNull();
    const twice = addLine(emptyLayer(), [P(0, 0), P(0, 0)]).layer;
    expect(offsetLine(twice, 1, 1)).toBeNull();
    const shaft = addLine(emptyLayer(), [P(0, 0, 0), P(0, 0, 50)]).layer;
    expect(offsetLine(shaft, 1, 1)).toBeNull();
  });

  it("offsets a tilted polygon in its own plane", () => {
    // a 20 × 10 rectangle tilted 60° about the X axis
    const c = Math.cos(Math.PI / 3);
    const s = Math.sin(Math.PI / 3);
    const rect: Vec3[] = [
      [E, N, 100],
      [E + 20, N, 100],
      [E + 20, N + 10 * c, 100 + 10 * s],
      [E, N + 10 * c, 100 + 10 * s],
    ];
    const layer = addLine(emptyLayer(), rect, true).layer;
    const out = offsetLine(layer, 1, 2)!;
    const o = pts(out.layer, 2);
    expect(polygonArea(o)).toBeCloseTo(24 * 14, 6);
    // still in the same plane: normal (0, -sin 60°, cos 60°)
    for (const p of o) expect(Math.abs(planeDistance(p, { point: rect[0], normal: [0, -s, c] }))).toBeLessThan(1e-8);
    const inner = offsetLine(layer, 1, -2)!;
    expect(polygonArea(pts(inner.layer, 2))).toBeCloseTo(16 * 6, 6);
  });

  it("offsets an open line to the right of its direction", () => {
    const layer = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(10, 10)]).layer;
    const right = pts(offsetLine(layer, 1, 2)!.layer, 2);
    expect(right.length).toBe(3);
    const rel = (p: Vec3) => [p[0] - E, p[1] - N];
    expect(rel(right[0])[0]).toBeCloseTo(0, 7);
    expect(rel(right[0])[1]).toBeCloseTo(-2, 7);
    expect(rel(right[1])[0]).toBeCloseTo(12, 7);
    expect(rel(right[1])[1]).toBeCloseTo(-2, 7);
    expect(rel(right[2])[0]).toBeCloseTo(12, 7);
    expect(rel(right[2])[1]).toBeCloseTo(10, 7);
    const left = pts(offsetLine(layer, 1, -2)!.layer, 2);
    expect(rel(left[1])[0]).toBeCloseTo(8, 7);
    expect(rel(left[1])[1]).toBeCloseTo(2, 7);
    expect(lineById(offsetLine(layer, 1, 2)!.layer, 2)!.closed).toBe(false);
    // a straight line in plan
    const straight = addLine(emptyLayer(), [P(0, 0, 50), P(0, 10, 60)]).layer;
    const moved = pts(offsetLine(straight, 1, 3)!.layer, 2);
    expect(moved[0][0] - E).toBeCloseTo(3, 7);
    expect(moved[0][1] - N).toBeCloseTo(0, 7);
    expect(moved[0][2]).toBeCloseTo(50, 7);
    expect(moved[1][2]).toBeCloseTo(60, 7);
  });

  it("uses the given plane and keeps the distance of every vertex from it", () => {
    // a line drawn on a section, slightly off the plane
    const plane = sectionPlane({ x0: E, y0: N, az: 90, length: null, towards: 0, away: 0 });
    const layer = addLine(emptyLayer(), [P(0, 0.5, 0), P(10, -0.25, 0), P(20, 0, 10)]).layer;
    const o = pts(offsetLine(layer, 1, 1, plane)!.layer, 2);
    expect(o.length).toBe(3);
    // az 90: the viewer looks north, travel east → the right-hand side is down
    expect(o[0][0] - E).toBeCloseTo(0, 7);
    expect(o[0][2]).toBeCloseTo(-1, 7);
    expect(o[0][1] - N).toBeCloseTo(0.5, 7);
    expect(o[1][1] - N).toBeCloseTo(-0.25, 7);
    expect(o[2][1] - N).toBeCloseTo(0, 7);
    // in a horizontal plane the Z of every vertex survives
    const sloped = addLine(emptyLayer(), [P(0, 0, 10), P(10, 0, 20), P(10, 10, 30), P(0, 10, 40)], true).layer;
    const h = pts(offsetLine(sloped, 1, 1, horizontalPlane(0))!.layer, 2);
    expect(h.map((p) => Math.round(p[2]))).toEqual([10, 20, 30, 40]);
    expect(planArea(h)).toBeCloseTo(144, 7);
  });

  it("bevels a corner sharper than the mitre limit", () => {
    // a spike: the mitre of the outer side would be 1 / sin(half angle) ≈ 20 distances long
    const spike: Vec3[] = [P(0, 0), P(100, 5), P(0, 10)];
    // the line turns left at the tip, so its right-hand side is the outer one
    const outer = offsetPoints(spike, false, 1)!;
    expect(outer.length).toBe(4);
    expect(Math.max(...outer.map((p) => p[0] - E))).toBeLessThan(102);
    const mitred = offsetPoints(spike, false, 1, undefined, 100)!;
    expect(mitred.length).toBe(3);
    expect(Math.max(...mitred.map((p) => p[0] - E))).toBeGreaterThan(115);
    // the inner side keeps the true intersection
    expect(offsetPoints(spike, false, -1)!.length).toBe(3);
  });
});

describe("shape", () => {
  it("simplifies with Douglas–Peucker in 3D", () => {
    const line: Vec3[] = [P(0, 0), P(10, 0.05), P(20, -0.05), P(30, 0, 100.08), P(40, 6), P(50, 0), P(60, 0.01), P(70, 0)];
    const layer = addLine(emptyLayer(), line).layer;
    expect(pts(simplifyLine(layer, 1, 0.1), 1)).toEqual([P(0, 0), P(30, 0, 100.08), P(40, 6), P(50, 0), P(70, 0)]);
    expect(pts(simplifyLine(layer, 1, 0.5), 1)).toEqual([P(0, 0), P(30, 0, 100.08), P(40, 6), P(50, 0), P(70, 0)]);
    expect(pts(simplifyLine(layer, 1, 10), 1)).toEqual([P(0, 0), P(70, 0)]);
    // the kept vertices are the same objects, nothing is recomputed
    expect(pts(simplifyLine(layer, 1, 0.1), 1)[1]).toBe(pts(layer, 1)[3]);
    expect(simplifyLine(layer, 1, 0.001)).toBe(layer);
    expect(simplifyLine(layer, 1, -1)).toBe(layer);
    // a deviation in Z only counts as well
    const z = addLine(emptyLayer(), [P(0, 0, 0), P(10, 0, 5), P(20, 0, 0)]).layer;
    expect(pts(simplifyLine(z, 1, 1), 1).length).toBe(3);
    expect(pts(simplifyLine(z, 1, 6), 1).length).toBe(2);
    // exactly collinear points go at zero tolerance
    const col = addLine(emptyLayer(), [[0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]]).layer;
    expect(pts(simplifyLine(col, 1, 0), 1)).toEqual([[0, 0, 0], [3, 3, 3]]);
  });

  it("simplifies a closed line keeping its first point", () => {
    const ring: Vec3[] = [P(0, 0), P(5, 0.01), P(10, 0), P(10, 5), P(10, 10), P(5, 10.02), P(0, 10), P(-0.01, 5)];
    const layer = addLine(emptyLayer(), ring, true).layer;
    const next = simplifyLine(layer, 1, 0.1);
    expect(pts(next, 1)).toEqual([P(0, 0), P(10, 0), P(10, 10), P(0, 10)]);
    expect(lineById(next, 1)!.closed).toBe(true);
    // a huge tolerance never leaves fewer than 3 points
    expect(simplifyLine(layer, 1, 1000)).toBe(layer);
    const tri = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(0, 10)], true).layer;
    expect(simplifyLine(tri, 1, 100)).toBe(tri);
  });

  it("densifies keeping the original vertices bit-identical", () => {
    const line: Vec3[] = [
      [512345.123456789, 6789012.987654321, 250.1],
      [512355.123456789, 6789012.987654321, 250.1],
      [512355.123456789, 6789016.487654321, 250.1],
    ];
    const layer = addLine(emptyLayer(), line).layer;
    const next = densifyLine(layer, 1, 2.5);
    const p = pts(next, 1);
    // 10 m → 4 pieces, 3.5 m → 2 pieces
    expect(p.length).toBe(1 + 4 + 2);
    expect(p[0]).toEqual(line[0]);
    expect(p[4]).toEqual(line[1]);
    expect(p[6]).toEqual(line[2]);
    expect(p[0]).toBe(pts(layer, 1)[0]);
    expect(p[2][0]).toBeCloseTo(512350.123456789, 8);
    for (let i = 1; i < p.length; i++) expect(lineLength([p[i - 1], p[i]], false)).toBeLessThanOrEqual(2.5 * (1 + 1e-9));
    expect(lineLength(p, false)).toBeCloseTo(13.5, 8);
    expect(densifyLine(layer, 1, 10)).toBe(layer);
    expect(densifyLine(layer, 1, 0)).toBe(layer);
    expect(densifyLine(layer, 1, -1)).toBe(layer);
  });

  it("densifies the closing segment of a polygon", () => {
    const layer = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(10, 10), P(0, 10)], true).layer;
    const p = pts(densifyLine(layer, 1, 5), 1);
    expect(p.length).toBe(8);
    expect(p[7]).toEqual(P(0, 5));
    expect(polygonArea(p)).toBeCloseTo(100, 8);
  });

  it("smooths by corner cutting", () => {
    const layer = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(10, 10)]).layer;
    const once = pts(smoothLine(layer, 1), 1);
    expect(once.length).toBe(4);
    expect(once[0]).toEqual(P(0, 0));
    expect(once[3]).toEqual(P(10, 10));
    expect(once[1][0] - E).toBeCloseTo(7.5, 8);
    expect(once[1][1] - N).toBeCloseTo(0, 8);
    expect(once[2][0] - E).toBeCloseTo(10, 8);
    expect(once[2][1] - N).toBeCloseTo(2.5, 8);
    const twice = pts(smoothLine(layer, 1, 2), 1);
    expect(twice.length).toBe(6);
    expect(twice[0]).toEqual(P(0, 0));
    expect(twice[5]).toEqual(P(10, 10));
    // a polygon stays closed and loses its corners
    const sq = addLine(emptyLayer(), [P(0, 0), P(10, 0), P(10, 10), P(0, 10)], true).layer;
    const round = smoothLine(sq, 1);
    expect(lineById(round, 1)!.closed).toBe(true);
    expect(pts(round, 1).length).toBe(8);
    expect(polygonArea(pts(round, 1))).toBeCloseTo(100 - 4 * (2.5 * 2.5) / 2, 8);
    // nothing to smooth
    const two = addLine(emptyLayer(), [P(0, 0), P(10, 0)]).layer;
    expect(smoothLine(two, 1)).toBe(two);
    expect(smoothLine(layer, 1, 0)).toBe(layer);
  });
});

describe("attributes and layers", () => {
  it("sets and removes attributes", () => {
    const layer = sample();
    const next = setAttrs(layer, [1, 2], { code: "ORE", grade: undefined, name: "Тело 1", empty: null });
    expect(lineById(next, 1)!.attrs).toEqual({ code: "ORE", name: "Тело 1", empty: null });
    expect(lineById(next, 2)!.attrs).toEqual({ code: "ORE", name: "Тело 1", empty: null });
    expect(next.lines[2]).toBe(layer.lines[2]);
    expect(lineById(next, 1)!.points).toBe(lineById(layer, 1)!.points);
    // nothing to change: the same layer
    expect(setAttrs(layer, [1], { code: "A", grade: undefined })).toBe(layer);
    expect(setAttrs(layer, [], { code: "X" })).toBe(layer);
    expect(setAttrs(layer, [1], {})).toBe(layer);
  });

  it("moves lines to another layer", () => {
    const layer = sample();
    const { layer: rest, taken } = takeLines(layer, [3, 1]);
    expect(rest.lines.map((l) => l.id)).toEqual([2]);
    expect(taken.map((l) => l.id)).toEqual([1, 3]);
    let other = addLine(emptyLayer(), [P(0, 0), P(1, 1)]).layer;
    other = addLine(other, [P(2, 2), P(3, 3)]).layer;
    const put = putLines(other, taken);
    expect(put.ids).toEqual([3, 4]);
    expect(put.layer.nextId).toBe(5);
    expect(pts(put.layer, 3)).toEqual(pts(layer, 1));
    expect(lineById(put.layer, 3)!.attrs).toEqual({ code: "A" });
    expect(takeLines(layer, [])).toEqual({ layer, taken: [] });
    expect(putLines(other, [])).toEqual({ layer: other, ids: [] });
  });
});
