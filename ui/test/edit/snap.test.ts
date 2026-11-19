import { describe, expect, it } from "vitest";
import { horizontalPlane, planeDistance, sectionPlane } from "../../src/edit/coords";
import type { Vec3 } from "../../src/edit/model";
import { SNAP_KINDS, SNAP_TITLES, gridSnap, projectPoint, projectSegment, segmentParamAt } from "../../src/edit/snap";
import { SnapIndex } from "../../src/edit/snapIndex";
import type { SnapKind, ViewTransform } from "../../src/edit/snap";
import { lcg, ortho, perspective } from "./testUtil";

const E = 512000;
const N = 6789000;
const W = 1000;
const H = 500;
/** Plan view: 1 px = 1 m, so the pixel of (E + x, N + y) is (x, 500 − y). */
const plan: ViewTransform = { matrix: ortho(E, E + W, N, N + H, -1000, 1000), width: W, height: H };
const ALL: ReadonlySet<SnapKind> = new Set(SNAP_KINDS);
const only = (...kinds: SnapKind[]): ReadonlySet<SnapKind> => new Set(kinds);
const xyz = (...p: number[][]): Float64Array => Float64Array.from(p.flat());

describe("projectPoint / projectSegment", () => {
  it("projects through an orthographic matrix", () => {
    const p = projectPoint(plan.matrix, W, H, E + 250, N + 100, 0)!;
    expect(p[0]).toBeCloseTo(250, 9);
    expect(p[1]).toBeCloseTo(400, 9);
    expect(p[2]).toBeCloseTo(0, 12);
    // outside the viewport is still a projection; outside clip z is not
    expect(projectPoint(plan.matrix, W, H, E - 50, N, 0)![0]).toBeCloseTo(-50, 9);
    expect(projectPoint(plan.matrix, W, H, E, N, 1500)).toBeNull();
    expect(projectPoint(plan.matrix, W, H, E, N, -1500)).toBeNull();
  });

  it("returns null behind a perspective camera", () => {
    const m = perspective(60, 800 / 600, 1, 1000);
    const p = projectPoint(m, 800, 600, 0, 0, -10)!;
    expect(p[0]).toBeCloseTo(400, 9);
    expect(p[1]).toBeCloseTo(300, 9);
    expect(projectPoint(m, 800, 600, 0, 0, 10)).toBeNull();
    expect(projectPoint(m, 800, 600, 0, 0, -0.5)).toBeNull();
    expect(projectPoint(m, 800, 600, 0, 0, -2000)).toBeNull();
    // up is up, right is right
    expect(projectPoint(m, 800, 600, 1, 0, -10)![0]).toBeGreaterThan(400);
    expect(projectPoint(m, 800, 600, 0, 1, -10)![1]).toBeLessThan(300);
  });

  it("clips a segment to the viewport and keeps the parameters", () => {
    const seg = new Float64Array(8);
    expect(projectSegment(plan.matrix, W, H, E - 1000, N + 100, 0, E + 3000, N + 100, 0, seg)).toBe(true);
    expect(seg[0]).toBeCloseTo(0, 9);
    expect(seg[2]).toBeCloseTo(1000, 9);
    expect(seg[4]).toBeCloseTo(0.25, 12);
    expect(seg[5]).toBeCloseTo(0.5, 12);
    expect(segmentParamAt(seg, 600, 390)).toBeCloseTo(0.4, 12);
    expect(projectSegment(plan.matrix, W, H, E - 10, N - 10, 0, E - 5, N + 600, 0, seg)).toBe(false);
    expect(projectSegment(plan.matrix, W, H, E + 10, N + 10, 2000, E + 20, N + 20, 3000, seg)).toBe(false);
  });

  it("clips a segment at the near plane of a perspective camera", () => {
    const m = perspective(60, 800 / 600, 1, 1000);
    const seg = new Float64Array(8);
    // from behind the camera to the front, through the view axis at z = -5
    expect(projectSegment(m, 800, 600, -5, 0, 5, 5, 0, -15, seg)).toBe(true);
    expect(seg[4]).toBeGreaterThan(0.25);
    expect(seg[5]).toBe(1);
    expect(segmentParamAt(seg, 400, 300)).toBeCloseTo(0.5, 9);
    // perspective-correct: the pixel of the point at t = 0.8
    const p = projectPoint(m, 800, 600, 3, 0, -11)!;
    expect(segmentParamAt(seg, p[0], p[1])).toBeCloseTo(0.8, 9);
  });
});

describe("SnapIndex", () => {
  it("returns stored coordinates exactly and honours the radius", () => {
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({ key: "lines:1", points: { kind: "vertex", xyz: xyz([E + 100.123456789, N + 200.987654321, 33.3], [E + 700.5, N + 50.25, -12]) } });
    expect(index.size).toBe(2);
    const hit = index.query(103, 303, 10, ALL)!;
    expect(hit.kind).toBe("vertex");
    expect(hit.point).toEqual([E + 100.123456789, N + 200.987654321, 33.3]);
    expect(hit.label).toBe("вершина");
    expect(hit.source).toBe("lines:1");
    expect(hit.distancePx).toBeCloseTo(Math.hypot(103 - 100.123456789, 303 - (500 - 200.987654321)), 3);
    // 6 px away
    expect(index.query(106.123456789, 299.012345679, 5, ALL)).toBeNull();
    expect(index.query(106.123456789, 299.012345679, 7, ALL)!.point[2]).toBe(33.3);
    // a large radius reaches over many cells and picks the nearest
    expect(index.query(500, 400, 300, ALL)!.point[0]).toBe(E + 700.5);
    expect(index.query(300, 350, 300, ALL)!.point[0]).toBe(E + 100.123456789);
    expect(index.query(103, 303, 10, only("midpoint", "nearest"))).toBeNull();
    expect(index.query(103, 303, 10, new Set())).toBeNull();
  });

  it("prefers point-like kinds to segment kinds in range", () => {
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({
      key: "lines:1",
      points: { kind: "vertex", xyz: xyz([E + 100, N + 100, 5], [E + 300, N + 100, 5]) },
      segments: { kind: "nearest", xyz: xyz([E + 100, N + 100, 5, E + 300, N + 100, 5]), midpoints: true },
    });
    expect(index.size).toBe(4);
    // 1 px from the segment, 8 px from the vertex: the vertex wins
    const v = index.query(108, 401, 10, ALL)!;
    expect(v.kind).toBe("vertex");
    expect(v.point).toEqual([E + 100, N + 100, 5]);
    expect(v.distancePx).toBeCloseTo(Math.hypot(8, 1), 4);
    // with a smaller radius the vertex is out of range
    const s = index.query(108, 401, 5, ALL)!;
    expect(s.kind).toBe("nearest");
    expect(s.label).toBe("на линии");
    expect(s.point[0]).toBeCloseTo(E + 108, 9);
    expect(s.point[1]).toBeCloseTo(N + 100, 9);
    expect(s.point[2]).toBe(5);
    expect(s.distancePx).toBeCloseTo(1, 4);
    // vertices switched off
    expect(index.query(108, 401, 10, only("nearest"))!.kind).toBe("nearest");
    // the midpoint competes with vertices by distance
    const m = index.query(203, 398, 10, ALL)!;
    expect(m.kind).toBe("midpoint");
    expect(m.label).toBe("середина");
    expect(m.point).toEqual([E + 200, N + 100, 5]);
    expect(index.query(203, 398, 10, only("vertex", "nearest"))!.kind).toBe("nearest");
    // the ends of the segment clamp
    const end = index.query(304, 400, 6, only("nearest"))!;
    expect(end.point).toEqual([E + 300, N + 100, 5]);
    expect(end.distancePx).toBeCloseTo(4, 4);
  });

  it("classifies by kind: collar and interval win over trace and wireframe edges", () => {
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({ key: "set:3:traces", segments: { kind: "trace", xyz: xyz([E + 400, N + 300, 0, E + 400, N + 100, -200]) } });
    index.setSource({
      key: "set:3:intervals",
      points: { kind: "interval", xyz: xyz([E + 400, N + 250, -50], [E + 400, N + 200, -100]), labels: (i) => `DH-7 · ${[12.5, 14][i]} м` },
    });
    index.setSource({ key: "set:3:collars", points: { kind: "collar", xyz: xyz([E + 400, N + 300, 0]) } });
    index.setSource({ key: "wf:9", points: { kind: "wf-vertex", xyz: xyz([E + 600, N + 300, 0]) }, segments: { kind: "wf-edge", xyz: xyz([E + 600, N + 300, 0, E + 700, N + 300, 0]) } });
    index.setSource({ key: "wf:9:section", segments: { kind: "wf-section", xyz: xyz([E + 600, N + 200, 0, E + 700, N + 250, 0]) } });
    const i = index.query(402, 252, 8, ALL)!;
    expect(i).toMatchObject({ kind: "interval", label: "DH-7 · 12.5 м", source: "set:3:intervals", point: [E + 400, N + 250, -50] });
    expect(index.query(402, 301, 8, ALL)!.label).toBe("DH-7 · 14 м");
    expect(index.query(401, 203, 8, ALL)).toMatchObject({ kind: "collar", label: "устье" });
    const t = index.query(402, 230, 8, ALL)!;
    expect(t.kind).toBe("trace");
    expect(t.label).toBe("траектория");
    expect(t.point[0]).toBeCloseTo(E + 400, 9);
    expect(t.point[1]).toBeCloseTo(N + 270, 9);
    expect(t.point[2]).toBeCloseTo(-30, 9);
    expect(index.query(603, 202, 8, ALL)!.kind).toBe("wf-vertex");
    expect(index.query(650, 202, 8, ALL)!.kind).toBe("wf-edge");
    const sec = index.query(650, 277, 8, ALL)!;
    expect(sec).toMatchObject({ kind: "wf-section", label: "сечение каркаса", source: "wf:9:section" });
    // the foot of the perpendicular from (650, 223) to the line through (600, 200) and (700, 250)
    expect(sec.point[0] - E).toBeCloseTo(649.2, 6);
    expect(sec.point[1] - N).toBeCloseTo(224.6, 6);
    expect(Object.keys(SNAP_TITLES).sort()).toEqual([...SNAP_KINDS].sort());
    expect(SNAP_TITLES.grid).toBe("узел сетки");
  });

  it("snaps to the point of a 3D segment closest to the cursor ray", () => {
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({ key: "lines:1", segments: { kind: "nearest", xyz: xyz([E + 100, N + 100, 0, E + 300, N + 100, 100]) } });
    // without a ray: by the screen parameter
    const flat = index.query(180, 397, 10, ALL)!;
    expect(flat.point[0]).toBeCloseTo(E + 180, 9);
    expect(flat.point[1]).toBeCloseTo(N + 100, 9);
    expect(flat.point[2]).toBeCloseTo(40, 9);
    // a vertical ray of the plan view gives the same point
    const down = index.query(180, 397, 10, ALL, { origin: [E + 180, N + 103, 1000], dir: [0, 0, -1] })!;
    expect(down.point[0]).toBeCloseTo(E + 180, 9);
    expect(down.point[2]).toBeCloseTo(40, 9);
    // an oblique ray in the plane of the segment meets it elsewhere: the ray decides
    const oblique = index.query(180, 397, 10, ALL, { origin: [E + 250, N + 100, 550], dir: [-0.1, 0, -1] })!;
    expect(oblique.point[0]).toBeCloseTo(E + 200, 8);
    expect(oblique.point[1]).toBeCloseTo(N + 100, 9);
    expect(oblique.point[2]).toBeCloseTo(50, 8);
    // a ray parallel to the segment falls back to the screen parameter
    const par = index.query(180, 397, 10, ALL, { origin: [E, N, 0], dir: [2, 0, 1] })!;
    expect(par.point[0]).toBeCloseTo(E + 180, 9);
  });

  it("skips what the view hides", () => {
    const index = new SnapIndex();
    index.setSource({ key: "p", points: { kind: "vertex", xyz: xyz([E + 100, N + 100, 80], [E + 120, N + 100, 20]) } });
    index.setSource({ key: "s", segments: { kind: "nearest", xyz: xyz([E + 100, N + 300, 0, E + 300, N + 300, 100], [E + 100, N + 400, 60, E + 300, N + 400, 90]), midpoints: true } });
    index.setView(plan);
    expect(index.query(101, 400, 5, ALL)!.point[2]).toBe(80);
    // a clipping window: only z < 50 is shown
    index.setView({ ...plan, visible: (_x, _y, z) => z < 50 });
    expect(index.query(101, 400, 5, ALL)).toBeNull();
    expect(index.query(101, 400, 30, ALL)!.point[2]).toBe(20);
    // the first segment is half inside: its visible part snaps, the hidden part and the midpoint (z = 50) do not
    expect(index.query(150, 201, 5, ALL)!.point[2]).toBeCloseTo(25, 9);
    expect(index.query(250, 201, 5, ALL)).toBeNull();
    expect(index.query(200, 201, 5, ALL)).toBeNull();
    expect(index.query(198, 201, 5, ALL)!.kind).toBe("nearest");
    // the second segment is hidden completely
    expect(index.query(150, 101, 5, ALL)).toBeNull();
    // a segment with both ends outside the window snaps where it crosses it
    index.setView({ ...plan, visible: (_x, _y, z) => z > 40 && z < 60 });
    expect(index.query(200, 201, 5, only("nearest"))!.point[2]).toBeCloseTo(50, 9);
    expect(index.query(150, 201, 5, only("nearest"))).toBeNull();
    // outside the viewport and outside clip z nothing is indexed
    index.setView(plan);
    index.setSource({ key: "far", points: { kind: "vertex", xyz: xyz([E - 3, N + 250, 0], [E + 500, N + 250, 5000]) } });
    expect(index.query(0, 250, 10, ALL)).toBeNull();
    expect(index.query(500, 250, 10, ALL)).toBeNull();
  });

  it("replaces and removes sources and follows the view", () => {
    const index = new SnapIndex();
    expect(index.query(10, 10, 10, ALL)).toBeNull();
    index.setSource({ key: "lines:1", points: { kind: "vertex", xyz: xyz([E + 100, N + 100, 0]) } });
    expect(index.query(100, 400, 10, ALL)).toBeNull(); // no view yet
    index.setView(plan);
    expect(index.query(100, 400, 10, ALL)).not.toBeNull();
    index.setSource({ key: "lines:1", points: { kind: "vertex", xyz: xyz([E + 500, N + 100, 0], [E + 510, N + 100, 0]) } });
    expect(index.size).toBe(2);
    expect(index.query(100, 400, 10, ALL)).toBeNull();
    expect(index.query(501, 400, 10, ALL)!.point[0]).toBe(E + 500);
    index.setSource({ key: "lines:2", points: { kind: "vertex", xyz: xyz([E + 100, N + 100, 0]) } });
    expect(index.size).toBe(3);
    expect(index.query(100, 400, 10, ALL)!.source).toBe("lines:2");
    index.removeSource("lines:2");
    index.removeSource("nothing");
    expect(index.query(100, 400, 10, ALL)).toBeNull();
    // the camera moved 400 m east and zoomed in twice
    index.setView({ matrix: ortho(E + 400, E + 900, N, N + 250, -1000, 1000), width: W, height: H });
    const hit = index.query(202, 300, 10, ALL)!;
    expect(hit.point).toEqual([E + 500, N + 100, 0]);
    expect(hit.distancePx).toBeCloseTo(2, 4);
    index.clear();
    expect(index.size).toBe(0);
    expect(index.query(202, 300, 10, ALL)).toBeNull();
    // an explicit rebuild is allowed too
    index.setSource({ key: "a", points: { kind: "collar", xyz: xyz([E + 500, N + 100, 0]) } });
    index.rebuild();
    expect(index.query(200, 300, 10, ALL)!.kind).toBe("collar");
  });

  it("indexes long segments that leave the viewport", () => {
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({
      key: "long",
      segments: {
        kind: "wf-edge",
        xyz: xyz([E - 5000, N - 2500, 0, E + 5000, N + 2500, 0], [E + 10, N - 100000, 0, E + 10, N + 100000, 0], [E - 100, N + 600, 0, E + 2000, N + 700, 0]),
        midpoints: true,
      },
    });
    // the diagonal is y = x / 2
    const rnd = lcg(3);
    for (let i = 0; i < 200; i++) {
      const x = 20 + rnd() * 960;
      const hit = index.query(x, 500 - x / 2 + 2, 6, only("wf-edge"))!;
      expect(hit).not.toBeNull();
      expect(hit.point[0] - E).toBeCloseTo(x - 0.8, 5);
      expect(hit.point[1] - N).toBeCloseTo((x - 0.8) / 2, 5);
    }
    expect(index.query(12, 77, 6, only("wf-edge"))!.point[1] - N).toBeCloseTo(423, 5);
    expect(index.query(500, 100, 6, ALL)).toBeNull();
    // the midpoint of the diagonal is in the corner of the view, the midpoints of the others are outside
    expect(index.query(2, 498, 6, only("midpoint"))!.point).toEqual([E, N, 0]);
    expect(index.query(12, 250, 6, only("midpoint"))).toBeNull();
  });

  it("works with a perspective camera", () => {
    const index = new SnapIndex();
    index.setView({ matrix: perspective(60, 800 / 600, 1, 1000), width: 800, height: 600 });
    index.setSource({ key: "p", points: { kind: "vertex", xyz: xyz([0, 0, 10], [2, 0, -10]) } });
    index.setSource({ key: "s", segments: { kind: "trace", xyz: xyz([-5, 1, 5, 5, 1, -15]) } });
    // the point behind the camera would land in the centre if the sign of w were ignored
    expect(index.query(400, 300, 5, only("vertex"))).toBeNull();
    const p = projectPoint(perspective(60, 800 / 600, 1, 1000), 800, 600, 2, 0, -10)!;
    expect(index.query(p[0] + 1, p[1], 5, ALL)!.point).toEqual([2, 0, -10]);
    const on = projectPoint(perspective(60, 800 / 600, 1, 1000), 800, 600, 3, 1, -11)!;
    const hit = index.query(on[0], on[1], 5, ALL)!;
    expect(hit.kind).toBe("trace");
    expect(hit.point[0]).toBeCloseTo(3, 6);
    expect(hit.point[1]).toBe(1);
    expect(hit.point[2]).toBeCloseTo(-11, 6);
    expect(hit.distancePx).toBeLessThan(1e-3);
  });

  it("matches a brute-force search on random data", () => {
    const rnd = lcg(11);
    const n = 3000;
    const pts = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) {
      pts[3 * i] = E - 100 + rnd() * 1200;
      pts[3 * i + 1] = N - 100 + rnd() * 700;
      pts[3 * i + 2] = rnd() * 100;
    }
    const m = 800;
    const segs = new Float64Array(m * 6);
    for (let i = 0; i < m; i++) {
      const x = E - 100 + rnd() * 1200;
      const y = N - 100 + rnd() * 700;
      const len = i % 10 === 0 ? 600 : 30;
      segs.set([x, y, 0, x + (rnd() - 0.5) * len, y + (rnd() - 0.5) * len, 0], i * 6);
    }
    const index = new SnapIndex();
    index.setView(plan);
    index.setSource({ key: "p", points: { kind: "vertex", xyz: pts } });
    index.setSource({ key: "s", segments: { kind: "nearest", xyz: segs } });
    const seg = new Float64Array(8);
    for (let q = 0; q < 400; q++) {
      const px = rnd() * W;
      const py = rnd() * H;
      const r = 3 + rnd() * 25;
      let bestP = Infinity;
      for (let i = 0; i < n; i++) {
        const s = projectPoint(plan.matrix, W, H, pts[3 * i], pts[3 * i + 1], pts[3 * i + 2]);
        if (!s || s[0] < 0 || s[0] > W || s[1] < 0 || s[1] > H) continue;
        bestP = Math.min(bestP, Math.hypot(s[0] - px, s[1] - py));
      }
      let bestS = Infinity;
      for (let i = 0; i < m; i++) {
        if (!projectSegment(plan.matrix, W, H, segs[6 * i], segs[6 * i + 1], 0, segs[6 * i + 3], segs[6 * i + 4], 0, seg)) continue;
        const dx = seg[2] - seg[0];
        const dy = seg[3] - seg[1];
        const len2 = dx * dx + dy * dy;
        const t = len2 > 0 ? Math.min(1, Math.max(0, ((px - seg[0]) * dx + (py - seg[1]) * dy) / len2)) : 0;
        bestS = Math.min(bestS, Math.hypot(seg[0] + dx * t - px, seg[1] + dy * t - py));
      }
      const hit = index.query(px, py, r, ALL);
      if (bestP <= r - 1e-3) {
        expect(hit!.kind).toBe("vertex");
        expect(hit!.distancePx).toBeCloseTo(bestP, 2);
      } else if (bestP > r + 1e-3 && bestS <= r - 1e-3) {
        expect(hit!.kind).toBe("nearest");
        expect(hit!.distancePx).toBeCloseTo(bestS, 2);
      } else if (bestP > r + 1e-3 && bestS > r + 1e-3) {
        expect(hit).toBeNull();
      }
    }
  });

  it("stays fast with 1 000 000 points and 200 000 segments", () => {
    const VW = 1920;
    const VH = 1080;
    const rnd = lcg(2024);
    const nPts = 1_000_000;
    const pts = new Float64Array(nPts * 3);
    for (let i = 0; i < nPts; i++) {
      pts[3 * i] = E + rnd() * VW;
      pts[3 * i + 1] = N + rnd() * VH;
      pts[3 * i + 2] = rnd() * 500 - 250;
    }
    const nSeg = 200_000;
    const segs = new Float64Array(nSeg * 6);
    for (let i = 0; i < nSeg; i++) {
      const x = E + rnd() * VW;
      const y = N + rnd() * VH;
      // most segments are short; every hundredth crosses the whole scene and leaves it
      const len = i % 100 === 0 ? 6000 : 40;
      segs.set([x, y, rnd() * 100, x + (rnd() - 0.5) * len, y + (rnd() - 0.5) * len, rnd() * 100], i * 6);
    }
    const index = new SnapIndex();
    index.setSource({ key: "points", points: { kind: "interval", xyz: pts } });
    index.setSource({ key: "segments", segments: { kind: "trace", xyz: segs, midpoints: true } });
    expect(index.size).toBe(nPts + 2 * nSeg);
    const view = (dx: number): ViewTransform => ({ matrix: ortho(E + dx, E + dx + VW, N, N + VH, -1000, 1000), width: VW, height: VH });

    index.setView(view(0));
    index.query(100, 100, 12, ALL); // first build: allocates the arrays
    const rebuilds: number[] = [];
    for (let k = 1; k <= 5; k++) {
      index.setView(view(k * 7.5));
      const t0 = performance.now();
      index.query(100, 100, 12, ALL);
      rebuilds.push(performance.now() - t0);
    }
    const rebuild = rebuilds.sort((a, b) => a - b)[2];

    const queries = 20000;
    const qx = new Float64Array(queries);
    const qy = new Float64Array(queries);
    for (let i = 0; i < queries; i++) {
      qx[i] = rnd() * VW;
      qy[i] = rnd() * VH;
    }
    let hits = 0;
    const t0 = performance.now();
    for (let i = 0; i < queries; i++) if (index.query(qx[i], qy[i], 12, ALL)) hits++;
    const perQuery = (performance.now() - t0) / queries;
    const t1 = performance.now();
    for (let i = 0; i < queries; i++) index.query(qx[i], qy[i], 12, only("trace"), { origin: [E + qx[i], N + VH - qy[i], 1000], dir: [0, 0, -1] });
    const perSegmentQuery = (performance.now() - t1) / queries;

    console.log(
      `SnapIndex: ${nPts} points + ${nSeg} segments (+ midpoints), viewport ${VW}x${VH}: ` +
        `rebuild ${rebuild.toFixed(1)} ms (median of 5, all: ${rebuilds.map((r) => r.toFixed(0)).join(" ")}), ` +
        `query ${(perQuery * 1000).toFixed(2)} µs avg, segment-only query with ray ${(perSegmentQuery * 1000).toFixed(2)} µs avg, ` +
        `${hits} hits of ${queries}`,
    );
    expect(hits).toBeGreaterThan(queries * 0.9);
    expect(rebuild).toBeLessThan(1500);
    expect(perQuery).toBeLessThan(2);
    expect(perSegmentQuery).toBeLessThan(2);
  }, 60000);
});

describe("gridSnap", () => {
  it("rounds X and Y in plan", () => {
    const plane = horizontalPlane(250);
    expect(gridSnap([512347.4, 6789012.6, 250], 5, plane)).toEqual([512345, 6789015, 250]);
    expect(gridSnap([512347.6, 6789017.4, 251.7], 5, plane)).toEqual([512350, 6789015, 250]);
    expect(gridSnap([512347.26, 6789012.74, 250], 0.5, plane)).toEqual([512347.5, 6789012.5, 250]);
    expect(gridSnap([512347.26, 6789012.74, 250], 0, plane)).toEqual([512347.26, 6789012.74, 250]);
    // a tilted plane: the node keeps to the plane
    const tilted = { point: [512000, 6789000, 100] as Vec3, normal: [0, -Math.SQRT1_2, Math.SQRT1_2] as Vec3 };
    const t = gridSnap([512012.3, 6789007.9, 0], 5, tilted);
    expect(t[0]).toBe(512010);
    expect(t[1]).toBe(6789010);
    expect(t[2]).toBeCloseTo(110, 9);
    expect(Math.abs(planeDistance(t, tilted))).toBeLessThan(1e-9);
  });

  it("rounds the distance along the section and Z on a vertical plane", () => {
    const plane = sectionPlane({ x0: 512345.67, y0: 6789012.34, az: 30, length: null, towards: 25, away: 25 });
    const a = (30 * Math.PI) / 180;
    const at = (s: number, z: number): Vec3 => [512345.67 + Math.sin(a) * s, 6789012.34 + Math.cos(a) * s, z];
    const g = gridSnap(at(123.4, -47.2), 10, plane);
    const want = at(120, -50);
    expect(g[0]).toBeCloseTo(want[0], 8);
    expect(g[1]).toBeCloseTo(want[1], 8);
    expect(g[2]).toBe(-50);
    expect(Math.abs(planeDistance(g, plane))).toBeLessThan(1e-9);
    // behind the origin of the section and off the plane: the node is still in the plane
    const off = at(-17, 4.9);
    const h = gridSnap([off[0] + 3 * plane.normal[0], off[1] + 3 * plane.normal[1], off[2]], 5, plane);
    const want2 = at(-15, 5);
    expect(h[0]).toBeCloseTo(want2[0], 8);
    expect(h[1]).toBeCloseTo(want2[1], 8);
    expect(h[2]).toBe(5);
  });
});
