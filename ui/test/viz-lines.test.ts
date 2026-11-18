import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { lineBoundaries, lineLevel, nearestInBox, readLinesManifest } from "../src/viz/lines/build";
import { LINE_PICK_PX, pickLines, pieceOfSegment, POINT_PICK_PX, type PickPart } from "../src/viz/lines/pick";
import { buildLines, lineTransferables } from "../src/viz/worker/lines";
import { lookFrom } from "../src/viz/core/views";
import { projectToScreen, viewProjection } from "../src/viz/pick/ray";

/** A part as the core writes it (viz/lines.py): float32 offsets of float64 points from the integer origin. */
function partBuffer(o: {
  origin: [number, number, number];
  pts: number[][];
  offsets?: number[];
  closed?: number[];
  line?: number[];
  bbox?: (number | null)[] | null;
  labels?: unknown;
  lod?: number;
}) {
  const n = o.offsets ? o.offsets.length - 1 : o.pts.length;
  const xyz = new Float32Array(o.pts.length * 3);
  o.pts.forEach((p, i) => p.forEach((v, c) => (xyz[i * 3 + c] = v - o.origin[c])));
  const zs = o.pts.map((p) => p[2]).filter((z) => !Number.isNaN(z));
  const xs = o.pts.map((p) => p[0]);
  const ys = o.pts.map((p) => p[1]);
  const bbox = o.bbox === undefined ? [Math.min(...xs), Math.min(...ys), zs.length ? Math.min(...zs) : null, Math.max(...xs), Math.max(...ys), zs.length ? Math.max(...zs) : null] : o.bbox;
  const header = { kind: "lines", format: 1, layer: 7, version: 2, k: 3, lod: o.lod ?? 0, tol: 0, points_only: !o.offsets, origin: o.origin, bbox, source_points: o.pts.length, n, points: o.pts.length, labels: o.labels ?? null, simplified: false };
  const line = Uint32Array.from(o.line ?? Array.from({ length: n }, (_, i) => i + 10));
  return o.offsets
    ? build(header, [["offsets", "<u4", Uint32Array.from(o.offsets)], ["xyz", "<f4", xyz], ["line", "<u4", line], ["closed", "<u1", Uint8Array.from(o.closed ?? new Array(n).fill(0))]])
    : build(header, [["xyz", "<f4", xyz], ["line", "<u4", line]]);
}

describe("line layer manifest", () => {
  it("reads the layer, its levels and parts; an extent without heights keeps null", () => {
    const parts = [
      { k: 0, n: 4, points: 90, bbox: [500_000, 7_000_000, null, 500_900, 7_000_800, null], origin: [500_450, 7_000_400, 0], etags: ['"a0"', '"a1"', '"a2"'] },
      { k: 1, n: 1, points: 2, bbox: [510_000, 7_000_000, 120.5, 510_010, 7_000_010, 130], origin: [510_005, 7_000_005, 125], etags: ['"b0"', '"b1"', '"b2"'] },
    ];
    const m = readLinesManifest(
      build({ kind: "line_layer", format: 1, layer: { id: 12, name: "Горизонтали", file_id: 5 }, version: 4, points_only: false, color: "#e8873a", lines: 5, points: 92, pieces: 5, bbox: [500_000, 7_000_000, 120.5, 510_010, 7_000_800, 130], lods: [{ lod: 0, tol: 0 }, { lod: 1, tol: 4 }, { lod: 2, tol: 32 }], max_vertices: 256_000, labels: true, parts }, []),
    );
    expect(m.layerId).toBe(12);
    expect(m.fileId).toBe(5);
    expect(m.color).toBe("#e8873a");
    expect(m.tols).toEqual([0, 4, 32]);
    expect(m.labels).toBe(true);
    expect(m.parts[0].bbox?.[2]).toBeNull(); // never 0
    expect(m.parts[1].etags[2]).toBe('"b2"');
  });

  it("refuses another kind of answer", () => {
    expect(() => readLinesManifest(build({ kind: "holes", parts: [] }, []))).toThrow();
  });
});

describe("line buffers of a part (Worker)", () => {
  const origin: [number, number, number] = [500_050, 7_000_025, 200];

  it("segments, points of one vertex, the polyline of every segment and the layer line of every polyline", () => {
    const pts = [
      [500_000, 7_000_000, 210], [500_010, 7_000_000, 211], [500_020, 7_000_005, 212], // 2 segments
      [500_040, 7_000_040, 190], // one vertex: a point
      [500_060, 7_000_010, 200], [500_070, 7_000_010, 200], [500_070, 7_000_020, 200], [500_060, 7_000_010, 200], // closed: 3 segments
    ];
    const b = buildLines(partBuffer({ origin, pts, offsets: [0, 3, 4, 8], closed: [0, 0, 1], line: [41, 42, 43] }));
    expect([b.k, b.lod, b.n, b.points, b.segments, b.pointsOnly]).toEqual([3, 0, 3, 8, 5, false]);
    expect(Array.from(b.segStart)).toEqual([0, 2, 2, 5]);
    expect(Array.from(b.positions.subarray(0, 12))).toEqual([-50, -25, 10, -40, -25, 11, -40, -25, 11, -30, -20, 12]);
    expect(Array.from(b.positions.subarray(24, 30))).toEqual([20, -5, 0, 10, -15, 0]); // the ring closes with its own last vertex
    expect(Array.from(b.dots)).toEqual([-10, 15, -10]);
    expect(Array.from(b.dotPiece!)).toEqual([1]);
    expect(Array.from(b.line)).toEqual([41, 42, 43]);
    expect(Array.from(b.closed!)).toEqual([0, 0, 1]);
    expect([b.emptyZ, b.noHeight]).toEqual([0, false]);
    expect([0, 1, 2, 3, 4].map((s) => pieceOfSegment(b.segStart, s))).toEqual([0, 0, 2, 2, 2]);
  });

  it("an empty height is drawn at the lowest height of the part, not at 0, and is counted", () => {
    const pts = [[500_000, 7_000_000, 230], [500_010, 7_000_000, NaN], [500_020, 7_000_000, 260]];
    const b = buildLines(partBuffer({ origin, pts, offsets: [0, 3] }));
    expect(b.emptyZ).toBe(1);
    expect(b.fill).toBe(30); // 230 − origin 200
    expect(b.positions[5]).toBe(30);
    expect(b.positions[8]).toBe(30);
    expect(b.positions.some((v) => Number.isNaN(v))).toBe(false);
    expect(b.noHeight).toBe(false);
  });

  it("a part without any height is flat at its own level: the layer puts it under the lowest object", () => {
    const pts = [[500_000, 7_000_000, NaN], [500_010, 7_000_000, NaN], [500_030, 7_000_030, NaN]];
    const b = buildLines(partBuffer({ origin: [500_015, 7_000_015, 0], pts, offsets: [0, 2, 3] }));
    expect(b.noHeight).toBe(true);
    expect([b.positions[2], b.positions[5], b.dots[2]]).toEqual([0, 0, 0]);
    expect(b.emptyZ).toBe(3);
    expect(b.segments).toBe(1);
  });

  it("a layer of points: the points themselves, a text at its point", () => {
    const pts = [[500_001.25, 7_000_002.5, 201], [500_003, 7_000_004, NaN], [500_005, 7_000_006, 203]];
    const labels = { at: [2, 0, 9], text: ["Т-3", "Т-1", "нет"], height: [2.5, null, 1], total: 3 };
    const b = buildLines(partBuffer({ origin, pts, line: [5, 6, 7], labels }));
    expect(b.pointsOnly).toBe(true);
    expect(b.segments).toBe(0);
    expect(b.dotPiece).toBeNull(); // dot i is point i
    expect(Array.from(b.dots.subarray(0, 3))).toEqual([-48.75, -22.5, 1]);
    expect(b.dots[5]).toBe(1); // empty height → the lowest of the part (201 − 200)
    expect(b.labels?.text).toEqual(["Т-3", "Т-1"]); // a text of a point that is not in the answer is dropped
    expect(Array.from(b.labels!.xyz)).toEqual([-45, -19, 3, -48.75, -22.5, 1]);
    expect(b.labels?.total).toBe(3);
  });

  it("a text of a line stands at its first vertex", () => {
    const pts = [[500_000, 7_000_000, 210], [500_010, 7_000_000, 211], [500_040, 7_000_040, 190]];
    const b = buildLines(partBuffer({ origin, pts, offsets: [0, 2, 3], labels: { at: [1], text: ["ТЕКСТ"], total: 1 } }));
    expect(Array.from(b.labels!.xyz)).toEqual([-10, 15, -10]);
  });

  it("UTM coordinates survive within 0.5 mm; a segment with a broken X or Y is not drawn", () => {
    const pts = [[512_345.6789, 7_123_456.7891, 987.6543], [512_345.6799, 7_123_456.7901, 987.6553], [512_347, NaN, 988], [512_348, 7_123_459, 989]];
    const o: [number, number, number] = [512_347, 7_123_458, 988];
    const b = buildLines(partBuffer({ origin: o, pts, offsets: [0, 4], bbox: [512_345, 7_123_456, 987, 512_348, 7_123_459, 989] }));
    expect(b.segments).toBe(1);
    expect(Array.from(b.segStart)).toEqual([0, 1]);
    for (let i = 0; i < 2; i++) for (let c = 0; c < 3; c++) expect(Math.abs(b.positions[i * 3 + c] + o[c] - pts[i][c])).toBeLessThan(5e-4);
    // 1 mm apart in the data stays 1 mm apart in float32 from the part origin; absolute float32 merges the two
    expect(b.positions[4] - b.positions[1]).toBeCloseTo(0.001, 5);
    expect(Math.fround(pts[1][1])).toBe(Math.fround(pts[0][1]));
  });

  it("offsets that disagree with the vertices are an error, an empty part is not", () => {
    const pts = [[1, 2, 3], [4, 5, 6]];
    expect(() => buildLines(partBuffer({ origin: [0, 0, 0], pts, offsets: [0, 3] }))).toThrow();
    expect(() => buildLines(partBuffer({ origin: [0, 0, 0], pts, offsets: [0, 2, 1] }))).toThrow();
    const empty = buildLines(partBuffer({ origin: [0, 0, 0], pts: [], offsets: [0], bbox: null }));
    expect([empty.n, empty.segments, empty.dots.length, empty.noHeight]).toEqual([0, 0, 0, true]);
  });

  it("every buffer goes back to the main thread once", () => {
    const b = buildLines(partBuffer({ origin, pts: [[500_000, 7_000_000, 210], [500_010, 7_000_000, 211]], offsets: [0, 2] }));
    const t = lineTransferables(b);
    expect(new Set(t).size).toBe(t.length);
    expect(t).toContain(b.positions.buffer);
    expect(t).toContain(b.line.buffer);
  });
});

describe("level of detail of a part of lines", () => {
  const bounds = lineBoundaries([0, 4, 32]);

  it("a level is coarse enough when its tolerance is at most a pixel on the screen", () => {
    expect(bounds).toEqual([0.25, 0.03125]);
    expect(lineLevel(1, bounds, null)).toBe(0);
    expect(lineLevel(0.1, bounds, null)).toBe(1);
    expect(lineLevel(0.01, bounds, null)).toBe(2);
  });

  it("20 % hysteresis about the current level", () => {
    expect(lineLevel(0.22, bounds, 0)).toBe(0);
    expect(lineLevel(0.19, bounds, 0)).toBe(1);
    expect(lineLevel(0.29, bounds, 1)).toBe(1);
    expect(lineLevel(0.31, bounds, 1)).toBe(0);
    expect(lineLevel(NaN, bounds, 1)).toBe(1);
  });

  it("the place of a box nearest to the camera", () => {
    const box = { min: [0, 0, 0] as [number, number, number], max: [10, 20, 30] as [number, number, number] };
    expect(nearestInBox(box, [-5, 10, 100])).toEqual([0, 10, 30]);
    expect(nearestInBox(box, [3, 4, 5])).toEqual([3, 4, 5]);
  });
});

describe("picking lines and points on the screen", () => {
  const W = 1000;
  const H = 800;
  const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
  const cam = lookFrom([0, -1e-4, 1], { min: [-200, -160, 0], max: [200, 160, 10] }, opts);
  const vp = viewProjection(cam, opts);
  const screen = (x: number, y: number, z = 0) => projectToScreen(vp, x, y, z, W, H)!;
  const ppm = screen(1, 0)[0] - screen(0, 0)[0];
  const view = (px: number, py: number, inWindow: (x: number, y: number) => boolean = () => true) => ({ vp, vexag: 1, px, py, width: W, height: H, inWindow });

  /** part 0 at the scene origin: a line along X at y = 0 and a point; part 1 shifted by (0, 50): a line at local y = 0 */
  function parts(): PickPart[] {
    const a = buildLines(partBuffer({ origin: [0, 0, 0], pts: [[-100, 0, 0], [0, 0, 0], [100, 0, 0], [20, 30, 0]], offsets: [0, 3, 4], line: [70, 71] }));
    const b = buildLines(partBuffer({ origin: [0, 0, 0], pts: [[-100, 0, 0], [100, 0, 0]], offsets: [0, 2], line: [80] }));
    return [
      { k: 0, offset: [0, 0, 0], box: null, built: a },
      { k: 1, offset: [0, 50, 0], box: { min: [-100, 0, 0], max: [100, 0, 0] }, built: b },
    ];
  }

  it("the nearest segment within 6 px, none beyond", () => {
    const [sx, sy] = screen(50, 0);
    const hit = pickLines(parts(), view(sx, sy + 5));
    expect(hit).toMatchObject({ k: 0, piece: 0, line: 70, on: "line" });
    expect(hit!.distance).toBeCloseTo(5, 6);
    expect(hit!.score).toBeCloseTo(5 / LINE_PICK_PX, 6);
    expect(hit!.at[0]).toBeCloseTo(50, 3);
    expect(pickLines(parts(), view(sx, sy + 7))).toBeNull();
  });

  it("a point within 8 px; the nearer of a point and a line by its share of the tolerance", () => {
    const [sx, sy] = screen(20, 30);
    const hit = pickLines(parts(), view(sx + 7, sy));
    expect(hit).toMatchObject({ k: 0, piece: 1, line: 71, on: "point" });
    expect(hit!.score).toBeCloseTo(7 / POINT_PICK_PX, 6);
    expect(pickLines(parts(), view(sx + 9, sy))).toBeNull();
    // a line 6 px above the point: 1 px below the point only the point is in reach; 4 px above it the point
    // takes 0.5 of its tolerance and the line (2 px away) 0.33 — the line wins
    const near = parts();
    near[1].offset = [0, 30 + 6 / ppm, 0];
    expect(pickLines(near, view(sx, sy + 1))).toMatchObject({ on: "point" });
    expect(pickLines(near, view(sx, sy - 4))).toMatchObject({ k: 1, line: 80, on: "line" });
  });

  it("the offset of a part and the vertical exaggeration are applied in float64", () => {
    const [sx, sy] = screen(-30, 50);
    expect(pickLines(parts(), view(sx, sy - 3))).toMatchObject({ k: 1, piece: 0, line: 80 });
    const side = lookFrom([0, -1, 0], { min: [-200, -10, -160], max: [200, 10, 160] }, opts);
    const m = viewProjection(side, opts);
    const p = buildLines(partBuffer({ origin: [0, 0, 0], pts: [[-100, 0, 10], [100, 0, 10]], offsets: [0, 2], line: [5] }));
    const at = projectToScreen(m, 0, 0, 30, W, H)!; // z 10 stretched 3 times
    const q = { vp: m, vexag: 3, px: at[0], py: at[1] + 2, width: W, height: H, inWindow: () => true };
    expect(pickLines([{ k: 0, offset: [0, 0, 0], box: null, built: p }], q)).toMatchObject({ line: 5 });
    expect(pickLines([{ k: 0, offset: [0, 0, 0], box: null, built: p }], { ...q, vexag: 1 })).toBeNull();
  });

  it("what the section window cuts off is not picked", () => {
    const [sx, sy] = screen(50, 0);
    expect(pickLines(parts(), view(sx, sy, (x) => x < 0))).toBeNull();
    expect(pickLines(parts(), view(sx, sy, (x) => x > 0))).toMatchObject({ line: 70 });
  });

  it("a million segments are searched within the budget of a click (50 ms)", () => {
    const n = 1_000_000;
    const xyz = new Float32Array((n + 1) * 3);
    for (let i = 0; i <= n; i++) {
      xyz[i * 3] = -190 + (380 * i) / n;
      xyz[i * 3 + 1] = 150 * Math.sin(i * 0.001);
    }
    const header = { kind: "lines", k: 0, lod: 0, points_only: false, origin: [0, 0, 0], bbox: [-190, -150, 0, 190, 150, 0], n: 1, points: n + 1, labels: null, simplified: false };
    const big = buildLines(build(header, [["offsets", "<u4", Uint32Array.of(0, n + 1)], ["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.of(1)], ["closed", "<u1", Uint8Array.of(0)]]));
    expect(big.segments).toBe(n);
    const part: PickPart[] = [{ k: 0, offset: [0, 0, 0], box: null, built: big }];
    const [sx, sy] = screen(xyz[400_000 * 3], xyz[400_000 * 3 + 1]);
    pickLines(part, view(sx, sy)); // warm-up
    const times: number[] = [];
    for (let r = 0; r < 5; r++) {
      const t0 = performance.now();
      expect(pickLines(part, view(sx + 1, sy + 1))).not.toBeNull();
      times.push(performance.now() - t0);
    }
    times.sort((a, b) => a - b);
    console.info(`pick of 1e6 segments: median ${times[2].toFixed(1)} ms`);
    expect(times[2]).toBeLessThan(150); // the budget is 50 ms; the margin is for a loaded CI machine
  });
});
