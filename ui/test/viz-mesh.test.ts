/**
 * Surfaces for the 3D window (ADR 0011 §3–§4) without three.js: the manifest of a wireframe, the buffers of a
 * part as the Worker makes them from BFA1 `mesh`, the level of detail of every part and the click on a
 * surface. References are written by hand; hard data: UTM offsets, a vertex without coordinates, a damaged
 * index, a part of one triangle, a part hidden by the section window, a vertical exaggeration.
 */
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { VizError } from "../src/viz/error";
import { levelTriangles, pickMesh, readMeshManifest, reachesClip, sameMeshGeometry, sampleAt, wantedLevels, type LevelPart, type MeshPickPart } from "../src/viz/mesh/build";
import { buildMesh, meshTransferables } from "../src/viz/worker/mesh";

const ETAGS = ['"a0"', '"a1"', '"a2"'];

function manifestBuffer(patch: Record<string, unknown> = {}): ArrayBuffer {
  return build(
    {
      kind: "wireframe", format: 1, wireframe: { id: 41, name: "Рельеф" }, version: 3, color: "#c9a46a",
      dtm: { raster_id: 7, clip: true, tint: "#ffffff", opacity: 1, shade: true },
      points: 5, triangles: 4, bbox: [500_000, 7_000_000, 290, 502_100, 7_000_100, 310], origin: [501_050, 7_000_050, 300], part_triangles: 64_000,
      lods: [{ lod: 0, ratio: 1 }, { lod: 1, ratio: 8 }, { lod: 2, ratio: 64 }],
      parts: [
        { k: 0, first: 0, n: 2, bbox: [500_000, 7_000_000, 290, 500_100, 7_000_100, 310], origin: [500_050, 7_000_050, 300], etags: ETAGS },
        { k: 1, first: 2, n: 2, bbox: [502_000, 7_000_000, 295, 502_100, 7_000_100, 305], origin: [502_050, 7_000_050, 300], etags: ['"b0"', '"b1"', '"b2"'] },
      ],
      ...patch,
    },
    [],
  );
}

/** A part as the core sends it: float32 offsets from the origin, u2 or u4 indices. */
function partBuffer(pts: number[][], tri: number[][], o: { k?: number; lod?: number; origin?: number[]; wide?: boolean; source?: number; header?: Record<string, unknown> } = {}): ArrayBuffer {
  const origin = o.origin ?? [0, 0, 0];
  const xyz = new Float32Array(pts.length * 3);
  pts.forEach((p, i) => p.forEach((v, c) => (xyz[i * 3 + c] = v - origin[c])));
  const flat = tri.flat();
  const source = o.source ?? tri.length;
  return build(
    { kind: "mesh", format: 1, k: o.k ?? 0, lod: o.lod ?? 0, ratio: 8 ** (o.lod ?? 0), origin, bbox: [0, 0, 0, 0, 0, 0], triangles: tri.length, vertices: pts.length, source_triangles: source, simplified: tri.length < source, index: o.wide ? "<u4" : "<u2", ...o.header },
    [["xyz", "<f4", xyz], ["idx", o.wide ? "<u4" : "<u2", o.wide ? Uint32Array.from(flat) : Uint16Array.from(flat)]],
  );
}

describe("the manifest of a wireframe", () => {
  it("is read from BFA1: colour, settings of a DTM, levels, parts with their origins and ETags", () => {
    const m = readMeshManifest(manifestBuffer());
    expect(m).toMatchObject({ wireframeId: 41, name: "Рельеф", version: 3, color: "#c9a46a", points: 5, triangles: 4, ratios: [1, 8, 64] });
    expect(m.dtm).toEqual({ raster_id: 7, clip: true, tint: "#ffffff", opacity: 1, shade: true });
    expect(m.bbox).toEqual([500_000, 7_000_000, 290, 502_100, 7_000_100, 310]);
    expect(m.parts.map((p) => p.origin)).toEqual([[500_050, 7_000_050, 300], [502_050, 7_000_050, 300]]);
    expect(m.parts[1].etags[2]).toBe('"b2"');
    // a wireframe that is not a DTM, without a colour of its own
    const plain = readMeshManifest(manifestBuffer({ dtm: null, color: null }));
    expect(plain.dtm).toBeNull();
    expect(plain.color).toBeNull();
  });

  it("another answer is not a manifest", () => {
    expect(() => readMeshManifest(manifestBuffer({ kind: "line_layer" }))).toThrow(VizError);
    expect(() => readMeshManifest(manifestBuffer({ parts: null }))).toThrow(/не описание каркаса/);
  });

  it("a new name, colour or DTM setting keeps the geometry; other parts or ETags do not", () => {
    const a = readMeshManifest(manifestBuffer());
    expect(sameMeshGeometry(a, readMeshManifest(manifestBuffer({ version: 4, color: "#ff0000", dtm: null })))).toBe(true);
    const moved = readMeshManifest(manifestBuffer());
    moved.parts[1].etags = ['"c0"', '"c1"', '"c2"'];
    expect(sameMeshGeometry(a, moved)).toBe(false);
    const fewer = readMeshManifest(manifestBuffer());
    fewer.parts.pop();
    expect(sameMeshGeometry(a, fewer)).toBe(false);
  });
});

describe("buffers of a part (the Worker)", () => {
  const PTS = [[0, 0, 5], [10, 0, 6], [10, 10, 7], [0, 10, 8]];
  const TRI = [[0, 1, 2], [0, 2, 3]];

  it("the answer of the core is used as it is: no copies, u2 indices, the extent of the vertices", () => {
    const utm = PTS.map((p) => [p[0] + 500_000, p[1] + 7_000_000, p[2]]);
    const buf = partBuffer(utm, TRI, { k: 3, lod: 1, origin: [500_005, 7_000_005, 6], source: 16 });
    const b = buildMesh(buf);
    expect(b).toMatchObject({ k: 3, lod: 1, triangles: 2, vertices: 4, sourceTriangles: 16, simplified: true, dropped: 0, origin: [500_005, 7_000_005, 6] });
    expect(b.positions.buffer).toBe(buf);
    expect(b.index.buffer).toBe(buf);
    expect(b.index).toBeInstanceOf(Uint16Array);
    expect(Array.from(b.positions.subarray(0, 6))).toEqual([-5, -5, -1, 5, -5, 0]);
    expect(b.box).toEqual([-5, -5, -1, 5, 5, 2]);
    expect(meshTransferables(b)).toEqual([buf]);
  });

  it("a part with more than 65 536 vertices has u4 indices", () => {
    const b = buildMesh(partBuffer(PTS, TRI, { wide: true }));
    expect(b.index).toBeInstanceOf(Uint32Array);
    expect(b.triangles).toBe(2);
    expect(b.simplified).toBe(false);
  });

  it("a vertex without coordinates is not drawn at zero: its triangles are left out and counted", () => {
    const b = buildMesh(partBuffer([...PTS, [NaN, 5, 5], [20, 20, Infinity]], [[0, 1, 4], ...TRI, [1, 2, 5]]));
    expect(b.triangles).toBe(2);
    expect(b.dropped).toBe(2);
    expect(Array.from(b.index)).toEqual([0, 1, 2, 0, 2, 3]);
    expect(b.box).toEqual([0, 0, 5, 10, 10, 8]); // the empty vertices are not in the extent
    // nothing but empty vertices: an empty part, not a part at the origin
    const none = buildMesh(partBuffer([[NaN, NaN, NaN], [1, 1, 1], [2, 2, 2]], [[0, 1, 2]]));
    expect(none.triangles).toBe(0);
    expect(none.index.length).toBe(0);
  });

  it("a damaged answer is an error with a hint, not a broken picture", () => {
    const outOfRange = () => buildMesh(partBuffer(PTS, [[0, 1, 9]]));
    expect(outOfRange).toThrow(VizError);
    expect(outOfRange).toThrow(/Данные поверхности повреждены/);
    try {
      outOfRange();
    } catch (e) {
      expect((e as VizError).code).toBe("mesh_damaged");
      expect((e as VizError).hint).not.toBe("");
    }
    // indices that are not triples, a vertex array that is not xyz, another kind of answer
    const flat = Uint16Array.of(0, 1, 2, 3);
    const header = { kind: "mesh", k: 0, lod: 0, origin: [0, 0, 0], triangles: 1, vertices: 4, source_triangles: 1 };
    expect(() => buildMesh(build(header, [["xyz", "<f4", new Float32Array(12)], ["idx", "<u2", flat]]))).toThrow(/mesh_damaged|повреждены/);
    expect(() => buildMesh(build(header, [["xyz", "<f4", new Float32Array(11)], ["idx", "<u2", Uint16Array.of(0, 1, 2)]]))).toThrow(/повреждены/);
    expect(() => buildMesh(build({ ...header, kind: "lines" }, [["xyz", "<f4", new Float32Array(12)], ["idx", "<u2", Uint16Array.of(0, 1, 2)]]))).toThrow(/не часть каркаса/);
    expect(() => buildMesh(build(header, [["xyz", "<f4", new Float32Array(12)], ["idx", "<i4", Int32Array.of(0, 1, 2)]]))).toThrow(VizError);
  });

  it("64 000 triangles are checked in a few milliseconds", () => {
    const n = 180; // a grid of 181 × 181 vertices: 64 800 triangles
    const pts: number[][] = [];
    const tri: number[][] = [];
    for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) pts.push([i * 2, j * 2, Math.sin(i / 9) * 5 + Math.cos(j / 7) * 3]);
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) tri.push([j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1], [j * (n + 1) + i, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i]);
    const buf = partBuffer(pts, tri);
    const t0 = performance.now();
    const b = buildMesh(buf);
    const ms = performance.now() - t0;
    expect(b.triangles).toBe(64_800);
    expect(ms).toBeLessThan(100);
  });
});

describe("the level of detail of a part", () => {
  const part = (p: Partial<LevelPart>): LevelPart => ({ screen: 100, triangles: [64_000, 8_000, 1_000], current: null, floor: 0, ...p });

  it("levels hold 1, 1/8 and 1/64 of the triangles until the real numbers come", () => {
    expect(levelTriangles(64_000, [1, 8, 64])).toEqual([64_000, 8_000, 1_000]);
    expect(levelTriangles(10, [1, 8, 64])).toEqual([10, 2, 1]);
  });

  it("at most 2 triangles per pixel of the disc of the part: far — 1/64, nearer — finer; out of view — nothing", () => {
    // √(64 000 / 2π) ≈ 101 px, √(8 000 / 2π) ≈ 36 px
    const r = wantedLevels([part({ screen: 20 }), part({ screen: 50 }), part({ screen: 150 }), part({ screen: Infinity }), part({ screen: null })], 1e9);
    expect(r.levels).toEqual([2, 1, 0, 0, null]);
    expect(r).toMatchObject({ budgeted: false, over: false });
  });

  it("20 % hysteresis about the level shown", () => {
    expect(wantedLevels([part({ screen: 105, current: 1 })], 1e9).levels).toEqual([1]); // needs 121 px to go finer
    expect(wantedLevels([part({ screen: 125, current: 1 })], 1e9).levels).toEqual([0]);
    expect(wantedLevels([part({ screen: 85, current: 0 })], 1e9).levels).toEqual([0]); // stays down to 81 px
    expect(wantedLevels([part({ screen: 75, current: 0 })], 1e9).levels).toEqual([1]);
  });

  it("the triangle budget of a frame coarsens the smallest parts on the screen first («упрощено»)", () => {
    const parts = [part({ screen: 400 }), part({ screen: 300 }), part({ screen: 200 })];
    expect(wantedLevels(parts, 200_000).levels).toEqual([0, 0, 0]);
    const tight = wantedLevels(parts, 140_000);
    expect(tight.levels).toEqual([0, 0, 1]);
    expect(tight).toMatchObject({ budgeted: true, over: false });
    const none = wantedLevels(parts, 2_000);
    expect(none.levels).toEqual([2, 2, 2]);
    expect(none.over).toBe(true);
  });

  it("a part cut by the section window is asked in full; a level that came back whole is not asked finer", () => {
    expect(wantedLevels([part({ screen: 10, exact: true })], 1e9).levels).toEqual([0]);
    // the part has too few triangles to simplify: its 1/64 is the full mesh
    expect(wantedLevels([part({ screen: 500, floor: 2, triangles: [40, 40, 40] })], 1e9).levels).toEqual([2]);
    expect(wantedLevels([part({ screen: 500, floor: 1 })], 1e9).levels).toEqual([1]);
  });

  it("clipping: a box entirely behind a plane of the window is out", () => {
    // the window −25…25 m about the plane x = 100 (scene coordinates)
    const planes = [{ normal: { x: 1, y: 0, z: 0 }, constant: -75 }, { normal: { x: -1, y: 0, z: 0 }, constant: 125 }];
    expect(reachesClip({ min: [0, 0, 0], max: [80, 50, 10] }, planes)).toBe(true);
    expect(reachesClip({ min: [0, 0, 0], max: [70, 50, 10] }, planes)).toBe(false);
    expect(reachesClip({ min: [130, 0, 0], max: [200, 50, 10] }, planes)).toBe(false);
    expect(reachesClip({ min: [0, 0, 0], max: [70, 50, 10] }, [])).toBe(true);
  });
});

describe("a click on a surface", () => {
  // two squares one above the other: z = 10 and z = 4 (the part's own coordinates)
  const positions = Float32Array.of(0, 0, 10, 100, 0, 10, 100, 100, 10, 0, 100, 10, 0, 0, 4, 100, 0, 4, 100, 100, 4, 0, 100, 4);
  const index = Uint16Array.of(0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7);
  const part = (o: Partial<MeshPickPart> = {}): MeshPickPart => ({ k: 0, offset: [0, 0, 0], box: [0, 0, 4, 100, 100, 10], positions, index, triangles: 4, ...o });
  const down = (x: number, y: number, z = 1000) => ({ origin: [x, y, z] as [number, number, number], dir: [0, 0, -1] as [number, number, number] });
  const all = () => true;

  it("the nearest triangle along the ray, the place in rendered space", () => {
    const hit = pickMesh([part()], down(30, 60), 1, all)!;
    expect(hit).toMatchObject({ k: 0, triangle: 1 });
    expect(hit.t).toBeCloseTo(990, 9);
    hit.point.forEach((v, i) => expect(v).toBeCloseTo([30, 60, 10][i], 9));
    // from below the lower square is the first
    const below = pickMesh([part()], { origin: [30, 60, -50], dir: [0, 0, 1] }, 1, all)!;
    expect(below.triangle).toBe(3);
    expect(below.t).toBeCloseTo(54, 9);
    expect(pickMesh([part()], down(130, 60), 1, all)).toBeNull();
    expect(pickMesh([part({ triangles: 0 })], down(30, 60), 1, all)).toBeNull();
  });

  it("the offset of the part is float64: a hit at UTM-like offsets stays within a micrometre", () => {
    const offset: [number, number, number] = [123_456.25, -98_765.5, 250.125];
    const hit = pickMesh([part({ offset })], down(offset[0] + 70.5, offset[1] + 20.25), 1, all)!;
    expect(hit.triangle).toBe(0);
    expect(Math.abs(hit.point[0] - (offset[0] + 70.5))).toBeLessThan(1e-6);
    expect(Math.abs(hit.point[1] - (offset[1] + 20.25))).toBeLessThan(1e-6);
    expect(Math.abs(hit.point[2] - (250.125 + 10))).toBeLessThan(1e-6);
  });

  it("the vertical exaggeration stretches the surface, not the offsets of the hit", () => {
    // rendered Z = (local + offset) · 3
    const hit = pickMesh([part({ offset: [0, 0, 100] })], down(30, 60, 5000), 3, all)!;
    expect(hit.point[2]).toBeCloseTo(330, 9);
    expect(hit.t).toBeCloseTo(5000 - 330, 9);
    // a slanted ray
    const dir: [number, number, number] = [0.6, 0, -0.8];
    const slanted = pickMesh([part({ offset: [0, 0, 100] })], { origin: [0, 50, 330 + 40], dir }, 3, all)!;
    expect(slanted.point[0]).toBeCloseTo(30, 9);
    expect(slanted.point[2]).toBeCloseTo(330, 9);
  });

  it("what the section window cuts off is not picked: the next surface along the ray answers", () => {
    const upper = part({ k: 0, positions: positions.subarray(0, 12), index: index.subarray(0, 6), triangles: 2, box: [0, 0, 10, 100, 100, 10] });
    const lower = part({ k: 1, offset: [200, 0, 0] });
    // the window keeps x ≥ 150 only
    const inWindow = (x: number) => x >= 150;
    expect(pickMesh([upper, lower], down(30, 60), 1, inWindow)).toBeNull();
    expect(pickMesh([upper, lower], down(230, 60), 1, inWindow)).toMatchObject({ k: 1, triangle: 1 });
  });

  it("64 000 triangles under the cursor are searched well within the 50 ms of a click", () => {
    const n = 180;
    const pos = new Float32Array((n + 1) * (n + 1) * 3);
    const idx = new Uint16Array(n * n * 6);
    for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) pos.set([i * 2, j * 2, Math.sin(i / 9) * 5], (j * (n + 1) + i) * 3);
    let at = 0;
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      const a = j * (n + 1) + i;
      idx.set([a, a + 1, a + n + 2, a, a + n + 2, a + n + 1], at);
      at += 6;
    }
    const big = part({ positions: pos, index: idx, triangles: n * n * 2, box: [0, 0, -5, 360, 360, 5] });
    // the first calls run before the engine has compiled the loop (15–20 ms measured alone); then about 1 ms
    let best = Infinity;
    let hit = null as ReturnType<typeof pickMesh>;
    for (let run = 0; run < 8; run++) {
      const t0 = performance.now();
      hit = pickMesh([big], down(181.3, 97.7), 1, all);
      best = Math.min(best, performance.now() - t0);
    }
    expect(hit!.point[2]).toBeCloseTo(Math.sin(90.65 / 9) * 5, 1);
    expect(best).toBeLessThan(10);
  });

  it("the surface over a plan point: the triangle that holds it, the highest of several", () => {
    const s = sampleAt(positions, index, 4, 30, 60)!;
    expect(s.triangle).toBe(1);
    [s.x, s.y, s.z].forEach((v, i) => expect(v).toBeCloseTo([30, 60, 10][i], 9));
    expect(sampleAt(positions, index, 4, 130, 60)).toBeNull();
    // a sloped triangle: the height is interpolated
    const slope = sampleAt(Float32Array.of(0, 0, 0, 10, 0, 10, 0, 10, 20), Uint16Array.of(0, 1, 2), 1, 2.5, 5)!;
    expect(slope.z).toBeCloseTo(12.5, 6);
  });
});
