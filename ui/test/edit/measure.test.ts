import { describe, expect, it } from "vitest";
import { planeDistance } from "../../src/edit/coords";
import { bestFitPlane, centroid, layerMeasures, leastSquaresPlane, lineLength, lineMeasures, missingZ, planArea, planLength, polygonArea } from "../../src/edit/measure";
import type { EditLayer, Vec3 } from "../../src/edit/model";

const E = 512345.67;
const N = 6789012.34;

/** A w × h rectangle in the XY plane, rotated by `tilt` about the X axis and by `spin` about Z, moved to UTM. */
function tiltedRect(w: number, h: number, tiltDeg: number, spinDeg: number, z = 250): Vec3[] {
  const t = (tiltDeg * Math.PI) / 180;
  const s = (spinDeg * Math.PI) / 180;
  return (
    [
      [0, 0],
      [w, 0],
      [w, h],
      [0, h],
    ] as [number, number][]
  ).map(([x, y]): Vec3 => {
    const y1 = y * Math.cos(t);
    const z1 = y * Math.sin(t);
    return [E + x * Math.cos(s) - y1 * Math.sin(s), N + x * Math.sin(s) + y1 * Math.cos(s), z + z1];
  });
}

describe("lengths", () => {
  const pts: Vec3[] = [
    [E, N, 100],
    [E + 3, N + 4, 100],
    [E + 3, N + 4, 112],
  ];

  it("measures 3D and plan length of open and closed lines", () => {
    expect(lineLength(pts, false)).toBeCloseTo(17, 8);
    expect(planLength(pts, false)).toBeCloseTo(5, 8);
    expect(lineLength(pts, true)).toBeCloseTo(17 + 13, 8);
    expect(planLength(pts, true)).toBeCloseTo(10, 8);
    expect(lineLength([], false)).toBe(0);
    expect(lineLength([pts[0]], true)).toBe(0);
    // two points do not make a ring
    expect(lineLength(pts.slice(0, 2), true)).toBeCloseTo(5, 8);
  });
});

describe("areas", () => {
  it("measures a horizontal rectangle", () => {
    const r = tiltedRect(40, 25, 0, 0);
    expect(polygonArea(r)).toBeCloseTo(1000, 9);
    expect(planArea(r)).toBeCloseTo(1000, 9);
    expect(polygonArea(r.slice().reverse())).toBeCloseTo(1000, 9);
    expect(polygonArea(r.slice(0, 2))).toBe(0);
    expect(planArea(r.slice(0, 2))).toBe(0);
  });

  it("measures a rectangle rotated in 3D to 1e-9 relative", () => {
    for (const [tilt, spin] of [
      [30, 0],
      [60, 37],
      [75, 211],
      [90, 123],
      [12.5, 333],
    ]) {
      const r = tiltedRect(123.4, 56.7, tilt, spin);
      const exact = 123.4 * 56.7;
      expect(Math.abs(polygonArea(r) - exact) / exact).toBeLessThan(1e-9);
      const plan = exact * Math.cos((tilt * Math.PI) / 180);
      expect(Math.abs(planArea(r) - plan)).toBeLessThan(1e-6);
    }
  });

  it("measures a tilted triangle and an L-shape", () => {
    // a 3-4-5 triangle standing on a vertical plane
    const tri: Vec3[] = [
      [E, N, 0],
      [E + 3, N, 0],
      [E, N, 4],
    ];
    expect(polygonArea(tri)).toBeCloseTo(6, 9);
    expect(planArea(tri)).toBeCloseTo(0, 9);
    const L: Vec3[] = [
      [0, 0, 5],
      [4, 0, 5],
      [4, 1, 5],
      [1, 1, 5],
      [1, 3, 5],
      [0, 3, 5],
    ].map(([x, y, z]): Vec3 => [E + x, N + y, z]);
    expect(polygonArea(L)).toBeCloseTo(6, 9);
    expect(planArea(L)).toBeCloseTo(6, 9);
  });
});

describe("planes", () => {
  it("finds the Newell plane through the centroid", () => {
    const r = tiltedRect(40, 20, 30, 0);
    const plane = bestFitPlane(r);
    expect(plane).not.toBeNull();
    const n = plane!.normal;
    expect(Math.hypot(...n)).toBeCloseTo(1, 12);
    // tilted 30° about X: the normal is (0, -sin 30°, cos 30°); counter-clockwise seen from above
    expect(n[0]).toBeCloseTo(0, 9);
    expect(n[1]).toBeCloseTo(-0.5, 9);
    expect(n[2]).toBeCloseTo(Math.cos(Math.PI / 6), 9);
    for (const p of r) expect(Math.abs(planeDistance(p, plane!))).toBeLessThan(1e-8);
    const c = centroid(r);
    expect(plane!.point).toEqual(c);
    expect(c[0]).toBeCloseTo(E + 20, 8);
    // the reversed polygon looks the other way
    expect(bestFitPlane(r.slice().reverse())!.normal[2]).toBeCloseTo(-Math.cos(Math.PI / 6), 9);
  });

  it("returns null for degenerate input", () => {
    expect(bestFitPlane([])).toBeNull();
    expect(bestFitPlane([[E, N, 0], [E + 1, N, 0]])).toBeNull();
    expect(bestFitPlane([[E, N, 0], [E + 1, N + 1, 1], [E + 2, N + 2, 2], [E + 5, N + 5, 5]])).toBeNull();
  });

  it("fits a least-squares plane to an open line", () => {
    // an S-shaped line in a plane tilted 40°, where the Newell vector nearly cancels
    const t = (40 * Math.PI) / 180;
    const pts: Vec3[] = [];
    for (let i = 0; i <= 20; i++) {
      const x = i * 5;
      const y = Math.sin((i / 20) * 2 * Math.PI) * 10;
      pts.push([E + x, N + y * Math.cos(t), 100 + y * Math.sin(t)]);
    }
    const plane = leastSquaresPlane(pts)!;
    expect(plane.normal[0]).toBeCloseTo(0, 9);
    expect(plane.normal[1]).toBeCloseTo(-Math.sin(t), 9);
    expect(plane.normal[2]).toBeCloseTo(Math.cos(t), 9);
    for (const p of pts) expect(Math.abs(planeDistance(p, plane))).toBeLessThan(1e-8);
  });

  it("gives a straight line the plane closest to horizontal", () => {
    const flat = leastSquaresPlane([[E, N, 10], [E + 10, N + 10, 10], [E + 20, N + 20, 10]])!;
    expect(flat.normal[0]).toBeCloseTo(0, 9);
    expect(flat.normal[1]).toBeCloseTo(0, 9);
    expect(flat.normal[2]).toBeCloseTo(1, 9);
    const sloped = leastSquaresPlane([[E, N, 0], [E + 10, N, 10]])!;
    expect(sloped.normal[0]).toBeCloseTo(-Math.SQRT1_2, 9);
    expect(sloped.normal[1]).toBeCloseTo(0, 9);
    expect(sloped.normal[2]).toBeCloseTo(Math.SQRT1_2, 9);
    expect(leastSquaresPlane([[E, N, 0], [E, N, 50]])).toBeNull();
    expect(leastSquaresPlane([[E, N, 0]])).toBeNull();
    expect(leastSquaresPlane([[E, N, 0], [E, N, 0]])).toBeNull();
    // a vertical plane: the normal is horizontal and repeatable
    const wall = leastSquaresPlane([[E, N, 0], [E + 10, N, 0], [E + 10, N, 10], [E + 20, N, 3]])!;
    expect(wall.normal[0]).toBeCloseTo(0, 9);
    expect(wall.normal[1]).toBeCloseTo(1, 9);
    expect(wall.normal[2]).toBeCloseTo(0, 9);
  });
});

describe("lineMeasures / layerMeasures", () => {
  const rect = tiltedRect(40, 25, 60, 20);
  const open: Vec3[] = [
    [E, N, 0],
    [E + 3, N + 4, 12],
  ];
  const layer: EditLayer = {
    lines: [
      { id: 1, points: rect, closed: true, attrs: {}, src: -1 },
      { id: 2, points: open, closed: false, attrs: {}, src: -1 },
      { id: 3, points: tiltedRect(10, 10, 0, 0), closed: false, attrs: {}, src: -1 },
    ],
    nextId: 4,
  };

  it("measures a polygon and an open line", () => {
    const m = lineMeasures(layer.lines[0]);
    expect(m.vertices).toBe(4);
    expect(m.closed).toBe(true);
    expect(m.length).toBeCloseTo(130, 8);
    expect(m.perimeter).toBeCloseTo(130, 8);
    expect(m.area).toBeCloseTo(1000, 7);
    expect(m.planArea).toBeCloseTo(500, 7);
    expect(m.planLength).toBeCloseTo(2 * 40 + 2 * 12.5, 7);
    const o = lineMeasures(layer.lines[1]);
    expect(o).toMatchObject({ vertices: 2, closed: false, area: null, planArea: null, perimeter: null });
    expect(o.length).toBeCloseTo(13, 9);
    expect(o.planLength).toBeCloseTo(5, 9);
  });

  it("sums a layer", () => {
    const m = layerMeasures(layer);
    expect(m.lines).toBe(3);
    expect(m.vertices).toBe(10);
    expect(m.closedLines).toBe(1);
    expect(m.length).toBeCloseTo(130 + 13 + 30, 7);
    expect(m.planLength).toBeCloseTo(105 + 5 + 30, 7);
    expect(m.area).toBeCloseTo(1000, 7);
    expect(m.planArea).toBeCloseTo(500, 7);
    expect(layerMeasures({ lines: [], nextId: 1 })).toEqual({ lines: 0, vertices: 0, missingZ: 0, length: 0, planLength: 0, closedLines: 0, area: 0, planArea: 0, areaUnknown: 0 });
  });
});

describe("acceptance (ТЗ 7.22): a 10 × 10 square", () => {
  it("is 100 in plan and in its own plane, flat and tilted, at UTM", () => {
    const flat = tiltedRect(10, 10, 0, 0);
    expect(planArea(flat)).toBeCloseTo(100, 9);
    expect(polygonArea(flat)).toBeCloseTo(100, 9);
    const m = lineMeasures({ id: 1, points: flat, closed: true, attrs: {}, src: -1 });
    expect(m.area).toBeCloseTo(100, 9);
    expect(m.planArea).toBeCloseTo(100, 9);
    expect(m.perimeter).toBeCloseTo(40, 9);
    // tilted by 60°: 100 in its plane, 50 in plan
    const tilted = tiltedRect(10, 10, 60, 25);
    expect(Math.abs(polygonArea(tilted) - 100)).toBeLessThan(1e-9 * 100);
    expect(planArea(tilted)).toBeCloseTo(50, 7);
  });
});

describe("vertices without a height", () => {
  const pts: Vec3[] = [
    [E, N, 100],
    [E + 3, N + 4, 100],
    [E + 6, N + 8, NaN],
    [E + 6, N + 8 + 5, NaN],
  ];

  it("are left out of the 3D length, never taken as zero", () => {
    expect(missingZ(pts)).toBe(2);
    expect(lineLength(pts, false)).toBeCloseTo(5, 9);
    expect(planLength(pts, false)).toBeCloseTo(15, 9);
    expect(Number.isNaN(polygonArea(pts))).toBe(true);
  });

  it("leave the area of the polygon unknown, the plan area known", () => {
    const sq: Vec3[] = [
      [E, N, 5],
      [E + 10, N, NaN],
      [E + 10, N + 10, 5],
      [E, N + 10, 5],
    ];
    const m = lineMeasures({ id: 1, points: sq, closed: true, attrs: {}, src: -1 });
    expect(m).toMatchObject({ vertices: 4, missingZ: 1, area: null, perimeter: null });
    expect(m.planArea).toBeCloseTo(100, 9);
    expect(m.length).toBeCloseTo(20, 9);
    const sum = layerMeasures({ lines: [{ id: 1, points: sq, closed: true, attrs: {}, src: -1 }, { id: 2, points: tiltedRect(10, 10, 0, 0), closed: true, attrs: {}, src: 0 }], nextId: 3 });
    expect(sum).toMatchObject({ closedLines: 2, areaUnknown: 1, missingZ: 1 });
    expect(sum.area).toBeCloseTo(100, 9);
    expect(sum.planArea).toBeCloseTo(200, 9);
  });
});
