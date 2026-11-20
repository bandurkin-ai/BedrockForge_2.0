// Fat or thin lines of the line layers in the scene without a rebuild (ADR 0017, Э11 11.18; review 11.21): the
// manifest of a heavy layer makes the lines thin before its first build; a coarse level built first must not turn
// them fat (every LineObject made again) and the finer one thin again. The layer keeps the thin lines until it is
// hidden or removed. The scene is a stub, the core a fetch stub; the parts are built here (no Worker in Node).
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { build } from "./bfa1-build";
import { LinesSource } from "../src/modules/scene/linesSource";
import type { LineLayerInfo } from "../src/modules/scene/objects";
import { setCoreTarget } from "../src/shell/api";
import type { GpuClass } from "../src/viz/core/budget";
import { LineLayerView } from "../src/viz/lines/layer";
import type { Scene } from "../src/viz/Scene";

const ORIGIN = [500_000, 7_000_000, 0];
const BBOX = [500_000, 7_000_000, 0, 500_999, 7_000_999, 0];

/** The manifest of the editor's measure: 1000 lines × 1000 vertices in one part — 999 000 segments at full detail. */
const manifest = (version = 2) =>
  build(
    { kind: "line_layer", format: 1, layer: { id: 3, name: "Миллион", file_id: 1 }, version, points_only: false, color: null, lines: 1000, points: 1_000_000, bbox: BBOX, lods: [{ lod: 0, tol: 0 }, { lod: 1, tol: 4 }, { lod: 2, tol: 32 }], labels: false, parts: [{ k: 0, n: 1000, points: 1_000_000, bbox: BBOX, origin: ORIGIN, etags: ['"a"', '"b"', '"c"'] }] },
    [],
  );

/** A coarse level of the part: two polylines, 3 segments. */
function coarse(): ArrayBuffer {
  const pts = [[500_000, 7_000_000, 0], [500_010, 7_000_000, 0], [500_020, 7_000_005, 0], [500_000, 7_000_100, 0], [500_005, 7_000_120, 0]];
  const xyz = new Float32Array(pts.length * 3);
  pts.forEach((p, i) => p.forEach((c, j) => (xyz[i * 3 + j] = c - ORIGIN[j])));
  return build({ kind: "lines", format: 1, layer: 3, version: 2, k: 0, lod: 2, tol: 32, points_only: false, origin: ORIGIN, bbox: BBOX, source_points: 5, n: 2, points: 5, labels: null, simplified: true }, [
    ["offsets", "<u4", Uint32Array.from([0, 3, 5])], ["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.from([1, 2])], ["closed", "<u1", new Uint8Array(2)],
  ]);
}

const info = (version = 2): LineLayerInfo => ({ id: 3, file_id: 1, name: "Миллион", version, color: null, points_only: false, line_count: 1000, point_count: 1_000_000, length: 1, closed_count: 0, closed: "none" }) as LineLayerInfo;

function fakeScene(gpuClass: GpuClass): Scene {
  return {
    clipPlanes: [],
    resolution: new THREE.Vector2(800, 600),
    renderer: { pixelRatio: 1, budgetFactor: () => 1 },
    gpu: { gpuClass },
    onFrame: () => () => undefined,
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    invalidate: vi.fn(),
    layerChanged: vi.fn(),
    debug: () => ({ segments: 0 }),
    origin: { value: null },
  } as unknown as Scene;
}

interface Inside {
  layers: Map<number, { view: LineLayerView; parts: unknown[] }>;
  loadPart(st: unknown, k: number, lod: number, p: unknown): Promise<void>;
  updateFat(extra?: number): void;
}

let fats: boolean[] = []; // setFat calls that change a view
let layers: LineLayerInfo[] = [];
let hidden: string[] = [];

beforeEach(() => {
  fats = [];
  layers = [info()];
  hidden = [];
  const real = LineLayerView.prototype.setFat;
  vi.spyOn(LineLayerView.prototype, "setFat").mockImplementation(function (this: LineLayerView, fat: boolean) {
    if ((this as unknown as { fat: boolean }).fat !== fat) fats.push(fat);
    real.call(this, fat);
  });
  vi.stubGlobal("fetch", async (url: string) => {
    const p = new URL(url).pathname.replace(/^\/api\/v1/, "");
    if (p === "/viz/lines/3") return new Response(manifest(layers[0]?.version ?? 2), { status: 200, headers: { ETag: '"m"' } });
    if (p === "/viz/lines/3/parts/0") return new Response(coarse(), { status: 200 });
    return new Response("{}", { status: 404 });
  });
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

async function source(gpu: GpuClass) {
  const src = new LinesSource(fakeScene(gpu), { layers: () => layers, hidden: () => hidden, floor: () => 0 });
  src.sync();
  await vi.waitFor(() => expect(src.idle).toBe(true));
  const inside = src as unknown as Inside;
  const st = inside.layers.get(3)!;
  expect(st).toBeDefined();
  const fat = () => (st.view as unknown as { fat: boolean }).fat;
  // the load of the level, then the frame that shows it (`frame` → `show` → `updateFat`)
  const buildCoarse = async () => {
    await inside.loadPart(st, 0, 2, st.parts[0]);
    st.view.show(0, 2);
    inside.updateFat();
  };
  return { src, st, fat, buildCoarse };
}

describe("a heavy layer under software rendering", () => {
  it("thin before its first build, and a coarse level built first keeps it thin: no rebuild to fat and back", async () => {
    const { src, st, fat, buildCoarse } = await source("software");
    expect(fat()).toBe(false); // the manifest decided: 999 000 > 200 000
    await buildCoarse();
    expect(st.view.stats().segments).toBe(3); // what is shown is light…
    expect(fat()).toBe(false); // …but the layer stays thin
    expect(fats).toEqual([false]); // one switch, before anything was built
    src.dispose();
  });

  it("hidden, it no longer counts by its manifest; shown again it is weighed before its parts come; removed — gone", async () => {
    const { src, fat } = await source("software");
    hidden = ["ln:3"];
    src.sync();
    hidden = [];
    src.sync();
    expect(fat()).toBe(false);
    expect(fats).toEqual([false]);
    layers = [];
    src.sync();
    expect((src as unknown as Inside).layers.size).toBe(0);
    src.dispose();
  });
});

describe("the same layer on real graphics", () => {
  it("999 000 segments are under a million: fat from the start, no switch at all", async () => {
    const { src, fat, buildCoarse } = await source("integrated");
    await buildCoarse();
    expect(fat()).toBe(true);
    expect(fats).toEqual([]);
    src.dispose();
  });
});
