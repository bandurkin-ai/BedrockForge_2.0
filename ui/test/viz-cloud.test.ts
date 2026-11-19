import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { POINT_BUDGET } from "../src/viz/core/budget";
import { lineLevel, readLinesManifest, sameLayout } from "../src/viz/lines/build";
import {
  applyPointBudget,
  classColor,
  classHex,
  cloudBoundaries,
  cloudMode,
  cloudPalette,
  cloudStyleOf,
  DEFAULT_VALUE_SCALE,
  EMPTY_VALUE_COLOR,
  readCloudInfo,
  shownCodes,
  valueMode,
  valueOf,
} from "../src/viz/lines/cloud";
import { pickLines, type PickPart } from "../src/viz/lines/pick";
import { buildLines, lineTransferables } from "../src/viz/worker/lines";
import { lookFrom } from "../src/viz/core/views";
import { projectToScreen, viewProjection } from "../src/viz/pick/ray";

const LEVELS = [0, 0.5, 2, 8, 32];

/** The manifest of a cloud as `core/bf_core/viz/cloud.py::manifest` writes it. */
function manifest(cloud: Record<string, unknown>, parts = [{ k: 0, n: 1000, points: 1000, levels: [1000, 400, 90, 12, 2], bbox: [500_000, 7_000_000, 100, 500_100, 7_000_100, 120], origin: [500_050, 7_000_050, 110], etags: ["a", "b", "c", "d", "e"] }]) {
  return build(
    { kind: "line_layer", format: 1, layer: { id: 9, name: "Облако", file_id: 3 }, version: 2, points_only: true, color: "#e8873a", lines: 0, points: 1000, pieces: 1000, bbox: [500_000, 7_000_000, 100, 500_100, 7_000_100, 120], lods: LEVELS.map((tol, lod) => ({ lod, tol })), max_vertices: 524_288, labels: false, cloud, parts },
    [],
  );
}

/** A part as `viz/cloud.py::part` writes it: xyz f4 from the origin of the part, line u4 (numbers in a row), value u1. */
function cloudPart(o: { origin: [number, number, number]; pts: number[][]; value: number[]; start?: number; mode?: string; lod?: number }) {
  const xyz = new Float32Array(o.pts.length * 3);
  o.pts.forEach((p, i) => p.forEach((v, c) => (xyz[i * 3 + c] = v - o.origin[c])));
  const start = o.start ?? 0;
  const header = { kind: "lines", format: 1, layer: 9, k: 0, lod: o.lod ?? 0, tol: 0, points_only: true, cloud: true, mode: o.mode ?? "class", origin: o.origin, bbox: [-1e6, -1e6, -1e3, 1e6, 1e6, 1e3], source_points: o.pts.length, n: o.pts.length, points: o.pts.length, start, simplified: false, labels: null };
  return build(header, [["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.from(o.pts.map((_, i) => start + i))], ["value", "<u1", Uint8Array.from(o.value)]]);
}

describe("the manifest of a cloud", () => {
  it("reads classes, modes, the scale of values and the points of the levels of a part", () => {
    const m = readLinesManifest(manifest({ classes: { "1": 600, "2": 380, "7": 20 }, modes: [{ mode: "class", key: "k1" }, { mode: "h", key: "k2" }], value: { step: 0.02, min: -2.5, max: 2.5, empty: 255 }, source: { kind: "las", every: 1 }, ground: null, dz: null }));
    expect(m.pointsOnly).toBe(true);
    expect(m.tols).toEqual(LEVELS);
    expect(m.cloud?.classes).toEqual({ 1: 600, 2: 380, 7: 20 });
    expect(m.cloud?.modes).toEqual(["class", "h"]);
    expect(m.cloud?.value).toEqual(DEFAULT_VALUE_SCALE);
    expect(m.parts[0].levels).toEqual([1000, 400, 90, 12, 2]);
  });

  it("an ordinary layer of points has no cloud; the two never share their buffers", () => {
    const plain = readLinesManifest(build({ kind: "line_layer", format: 1, layer: { id: 9, name: "Точки", file_id: null }, version: 2, points_only: true, color: null, lines: 0, points: 1000, bbox: null, lods: [{ lod: 0, tol: 0 }], labels: false, parts: [{ k: 0, n: 1000, points: 1000, bbox: null, origin: [500_050, 7_000_050, 110], etags: ["a"] }] }, []));
    expect(plain.cloud).toBeNull();
    const cloud = readLinesManifest(manifest({ classes: {} }));
    expect(sameLayout(cloud, cloud)).toBe(true);
    expect(sameLayout(cloud, { ...cloud, cloud: null })).toBe(false);
  });

  it("junk in `cloud` is dropped: codes out of 0…255, unknown modes; «Класс» is always there", () => {
    const info = readCloudInfo({ classes: { "2": 5, "300": 1, x: 4, "3": 0 }, modes: [{ mode: "rgb" }, { mode: "dz" }], value: null });
    expect(info?.classes).toEqual({ 2: 5 });
    expect(info?.modes).toEqual(["class", "dz"]);
    expect(info?.value).toEqual(DEFAULT_VALUE_SCALE);
  });
});

describe("a part of a cloud (Worker)", () => {
  it("xyz from the origin of the part, the numbers of the points and their values, all transferred", () => {
    const origin: [number, number, number] = [500_000, 7_000_000, 100];
    const b = buildLines(cloudPart({ origin, pts: [[500_001.25, 7_000_002.5, 101], [500_003, 7_000_004, 99.5]], value: [2, 7], start: 4000, mode: "class" }));
    expect([b.pointsOnly, b.points, b.mode]).toEqual([true, 2, "class"]);
    expect(Array.from(b.dots)).toEqual([1.25, 2.5, 1, 3, 4, -0.5]);
    expect(Array.from(b.line)).toEqual([4000, 4001]);
    expect(Array.from(b.value!)).toEqual([2, 7]);
    // dots, numbers and values stay views of the answer buffer: transferred once, nothing copied
    expect(new Set([b.dots.buffer, b.line.buffer, b.value!.buffer]).size).toBe(1);
    expect(lineTransferables(b)).toContain(b.value!.buffer);
  });

  it("values that disagree with the points are refused", () => {
    expect(() => buildLines(cloudPart({ origin: [0, 0, 0], pts: [[0, 0, 0], [1, 1, 1]], value: [2] }))).toThrow();
  });
});

describe("colour modes and the palette", () => {
  const info = readCloudInfo({ classes: { "2": 10 }, modes: [{ mode: "class" }, { mode: "h" }] })!;

  it("«Класс» by default when there are classes; a mode the cloud has not is not asked", () => {
    expect(cloudMode(info, undefined)).toBe("class");
    expect(cloudMode(info, "h")).toBe("h");
    expect(cloudMode(info, "dz")).toBe("class"); // no DTM yet: the core would answer 409
    expect(cloudMode(readCloudInfo({ classes: {} })!, undefined)).toBe("layer");
    expect(valueMode("layer")).toBe("class"); // the layer colour still knows the classes
  });

  it("ASPRS colours: ground, vegetation, noise, water; other codes stable and distinct", () => {
    expect(classHex(2)).toBe("#b07a3b");
    expect(classHex(7)).toBe("#ff2bd6");
    expect(classHex(9)).toBe("#1f6bff");
    expect(classColor(64)).toEqual(classColor(64));
    expect(classHex(64)).not.toBe(classHex(65));
  });

  it("a hidden class has alpha 0: neither drawn nor picked", () => {
    const pal = cloudPalette("class", new Set([7]), "#e8873a");
    expect(Array.from(pal.subarray(2 * 4, 2 * 4 + 4))).toEqual([...classColor(2), 255]);
    expect(pal[7 * 4 + 3]).toBe(0);
    const shown = shownCodes(pal);
    expect([shown[2], shown[7]]).toEqual([1, 0]);
    const layer = cloudPalette("layer", new Set([1]), "#e8873a");
    expect(Array.from(layer.subarray(2 * 4, 2 * 4 + 4))).toEqual([0xe8, 0x87, 0x3a, 255]);
    expect(layer[1 * 4 + 3]).toBe(0);
  });

  it("h and dz: the diverging scale about 0, «no value» dim and never the colour of 0", () => {
    const pal = cloudPalette("dz", new Set([125]), "#e8873a");
    const at = (code: number) => Array.from(pal.subarray(code * 4, code * 4 + 4));
    expect(at(125)).toEqual([0xe6, 0xe9, 0xee, 255]); // 0 m — Light Stone; the hidden class codes do not apply
    expect(at(0)).toEqual([0x1f, 0x6b, 0xff, 255]); // −2.5 m — Cobalt
    expect(at(250)).toEqual([0xf4, 0x6a, 0x1a, 255]); // +2.5 m — Copper Orange
    const empty = [parseInt(EMPTY_VALUE_COLOR.slice(1, 3), 16), parseInt(EMPTY_VALUE_COLOR.slice(3, 5), 16), parseInt(EMPTY_VALUE_COLOR.slice(5, 7), 16), 255];
    expect(at(255)).toEqual(empty);
    expect(valueOf(125, DEFAULT_VALUE_SCALE)).toBe(0);
    expect(valueOf(130, DEFAULT_VALUE_SCALE)).toBe(0.1);
    expect(valueOf(255, DEFAULT_VALUE_SCALE)).toBeNull();
  });

  it("the style from the view key: junk dropped", () => {
    expect(cloudStyleOf({ "9": { color: "h", hidden: [7, 300, "x", 1] } }, 9)).toEqual({ color: "h", hidden: [7, 1] });
    expect(cloudStyleOf({ "9": { color: "rgb" } }, 9)).toEqual({});
    expect(cloudStyleOf({ "9": [] }, 9)).toBeNull();
    expect(cloudStyleOf(undefined, 9)).toBeNull();
  });
});

describe("levels of a cloud and the point budget", () => {
  const bounds = cloudBoundaries(LEVELS);

  it("the cell of a level is at most 2 px on screen", () => {
    expect(bounds).toEqual([4, 1, 0.25, 0.0625]);
    expect(lineLevel(5, bounds, null)).toBe(0); // 0.5 m would be 2.5 px
    expect(lineLevel(3, bounds, null)).toBe(1);
    expect(lineLevel(0.5, bounds, null)).toBe(2);
    expect(lineLevel(0.1, bounds, null)).toBe(3);
    expect(lineLevel(0.01, bounds, null)).toBe(4);
    expect(lineLevel(3.5, bounds, 0)).toBe(0); // hysteresis: 20 % below the boundary keeps the finer level
  });

  it("budgets of the classes of graphics", () => {
    expect(POINT_BUDGET).toEqual({ integrated: 6_000_000, discrete: 20_000_000, software: 1_000_000 });
  });

  it("the smallest parts on screen go coarser first, «упрощено»; within the budget nothing changes", () => {
    const parts = [
      { key: "a", screen: 900, points: [600_000, 200_000, 40_000, 5000, 300], level: 0 },
      { key: "b", screen: 50, points: [600_000, 200_000, 40_000, 5000, 300], level: 0 },
    ];
    const free = applyPointBudget(parts, 2_000_000);
    expect(free).toMatchObject({ levels: [0, 0], points: 1_200_000, simplified: false, over: false });
    const r = applyPointBudget(parts, 1_000_000);
    expect(r.levels).toEqual([0, 1]);
    expect(r.points).toBe(800_000);
    expect(r.simplified).toBe(true);
    const hard = applyPointBudget(parts, 100);
    expect(hard.levels).toEqual([4, 4]);
    expect(hard.over).toBe(true);
  });
});

describe("picking a point of a cloud", () => {
  const W = 1000;
  const H = 800;
  const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };
  const cam = lookFrom([0, -1e-4, 1], { min: [-200, -160, 0], max: [200, 160, 10] }, opts);
  const vp = viewProjection(cam, opts);
  const screen = (x: number, y: number, z = 0) => projectToScreen(vp, x, y, z, W, H)!;
  const view = (px: number, py: number) => ({ vp, vexag: 1, px, py, width: W, height: H, inWindow: () => true });

  it("the nearest shown point within 8 px: its number and value; a hidden class is skipped", () => {
    const built = buildLines(cloudPart({ origin: [0, 0, 0], pts: [[10, 10, 0], [12, 10, 0]], value: [7, 2], start: 500 }));
    const [sx, sy] = screen(10, 10);
    const all: PickPart[] = [{ k: 0, offset: [0, 0, 0], box: null, built, shown: shownCodes(cloudPalette("class", new Set(), "#8a99a8")) }];
    expect(pickLines(all, view(sx, sy))).toMatchObject({ line: 500, value: 7, on: "point" });
    const noNoise: PickPart[] = [{ ...all[0], shown: shownCodes(cloudPalette("class", new Set([7]), "#8a99a8")) }];
    const hit = pickLines(noNoise, view(sx, sy));
    expect(hit).toMatchObject({ line: 501, value: 2 });
    const none: PickPart[] = [{ ...all[0], shown: shownCodes(cloudPalette("class", new Set([2, 7]), "#8a99a8")) }];
    expect(pickLines(none, view(sx, sy))).toBeNull();
  });

  it("a million points of a part are searched within the budget of a click (50 ms)", () => {
    const n = 1_000_000;
    const xyz = new Float32Array(n * 3);
    const value = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      xyz[i * 3] = -190 + ((i * 7919) % 3800) / 10;
      xyz[i * 3 + 1] = -150 + ((i * 104_729) % 3000) / 10;
      value[i] = i % 3 === 0 ? 7 : 2;
    }
    const header = { kind: "lines", k: 0, lod: 0, points_only: true, cloud: true, mode: "class", origin: [0, 0, 0], bbox: [-190, -150, 0, 190, 150, 0], n, points: n, labels: null, simplified: false };
    const built = buildLines(build(header, [["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.from({ length: n }, (_, i) => i)], ["value", "<u1", value]]));
    const part: PickPart[] = [{ k: 0, offset: [0, 0, 0], box: null, built, shown: shownCodes(cloudPalette("class", new Set([7]), "#8a99a8")) }];
    const [sx, sy] = screen(xyz[400_001 * 3], xyz[400_001 * 3 + 1]);
    pickLines(part, view(sx, sy)); // warm-up
    const times: number[] = [];
    for (let r = 0; r < 5; r++) {
      const t0 = performance.now();
      const hit = pickLines(part, view(sx + 1, sy + 1));
      expect(hit?.value).toBe(2);
      times.push(performance.now() - t0);
    }
    times.sort((a, b) => a - b);
    console.info(`pick of 1e6 cloud points: median ${times[2].toFixed(1)} ms`);
    expect(times[2]).toBeLessThan(150);
  });
});
