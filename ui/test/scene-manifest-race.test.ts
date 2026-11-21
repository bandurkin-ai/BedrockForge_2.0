// A newer version listed while the manifest of a layer is on the way (Windows CI, series.spec, run 44769654382):
// `POST /lines/layers` lists an empty layer, the window asks its manifest; `PUT …/geometry` lists version 2 while
// that answer — still of version 1, no parts — is on the way. The second ask must not be dropped as a duplicate:
// once the old answer comes, the manifest is asked again and the layer with its lines comes into the scene. The
// same for a wireframe whose geometry changed meanwhile. The scene is a stub, the core a fetch stub.
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { build } from "./bfa1-build";
import { LinesSource } from "../src/modules/scene/linesSource";
import type { LineLayerInfo, WireframeInfo } from "../src/modules/scene/objects";
import { WireframesSource } from "../src/modules/scene/wireframesSource";
import { setCoreTarget } from "../src/shell/api";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import type { Scene } from "../src/viz/Scene";

const ORIGIN = [1000, 5000, 0];
const BBOX = [990, 4980, -10, 1010, 5120, 10];

const lineManifest = (version: number) =>
  build(
    { kind: "line_layer", format: 1, layer: { id: 3, name: "Опоры", file_id: 1 }, version, points_only: false, color: null, lines: version > 1 ? 4 : 0, points: version > 1 ? 8 : 0, bbox: version > 1 ? BBOX : null, lods: [{ lod: 0, tol: 0 }], labels: false,
      parts: version > 1 ? [{ k: 0, n: 4, points: 8, bbox: BBOX, origin: ORIGIN, etags: ['"a"'] }] : [] },
    [],
  );

const meshManifest = (version: number) =>
  build(
    { kind: "wireframe", wireframe: { id: 5, name: "Тело" }, version, color: null, dtm: null, points: version > 1 ? 8 : 0, triangles: version > 1 ? 12 : 0, bbox: version > 1 ? BBOX : null, lods: [{ lod: 0, ratio: 1 }],
      parts: version > 1 ? [{ k: 0, n: 12, bbox: BBOX, origin: ORIGIN, etags: ['"p0"'] }] : [] },
    [],
  );

const lineInfo = (version: number): LineLayerInfo => ({ id: 3, file_id: 1, name: "Опоры", version, color: null, points_only: false, line_count: version > 1 ? 4 : 0, point_count: 0, length: 0, closed_count: 0, closed: "none" }) as LineLayerInfo;
const wfInfo = (version: number): WireframeInfo => ({ id: 5, name: "Тело", version, color: null, point_count: 0, triangle_count: 0, extent: null, dtm: null }) as unknown as WireframeInfo;

function fakeScene(): Scene {
  const origin = new SceneOrigin();
  return {
    origin,
    clipPlanes: [],
    resolution: new THREE.Vector2(800, 600),
    renderer: { pixelRatio: 1, budgetFactor: () => 1 },
    gpu: { gpuClass: "discrete" },
    onFrame: () => () => undefined,
    addLayer: vi.fn((_key: string, layer: { bbox?: Float64Array }) => void (layer.bbox && origin.ensure(layer.bbox))),
    removeLayer: vi.fn(),
    invalidate: vi.fn(),
    layerChanged: vi.fn(),
    debug: () => ({ segments: 0 }),
  } as unknown as Scene;
}

/** The core: the manifest of the version current when the request came; the first answer is held until released. */
function core(path: string, manifest: (version: number) => ArrayBuffer) {
  const state = { version: 1, asked: 0 };
  let release: () => void = () => undefined;
  const held = new Promise<void>((r) => (release = r));
  vi.stubGlobal("fetch", async (url: string) => {
    const p = new URL(url).pathname.replace(/^\/api\/v1/, "");
    if (p !== path) return new Response("{}", { status: 404 });
    const version = state.version;
    if (++state.asked === 1) await held;
    return new Response(manifest(version), { status: 200, headers: { ETag: `"m${version}"` } });
  });
  return { state, release: () => release() };
}

beforeEach(() => setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("a manifest overtaken by a newer version of its object", () => {
  it("a new empty line layer saved at once: its lines come into the scene", async () => {
    const c = core("/viz/lines/3", lineManifest);
    let layers = [lineInfo(1)];
    const scene = fakeScene();
    const src = new LinesSource(scene, { layers: () => layers, hidden: () => [], floor: () => 0 });
    src.sync(); // objects.changed of the new layer: its manifest (version 1, empty) is on the way
    await vi.waitFor(() => expect(c.state.asked).toBe(1));
    c.state.version = 2; // PUT …/geometry
    layers = [lineInfo(2)];
    src.sync(); // objects.changed of the save, while the first answer is still on the way
    c.release();
    await vi.waitFor(() => expect(src.idle).toBe(true));
    expect(c.state.asked).toBe(2);
    expect(vi.mocked(scene.addLayer).mock.calls.map((a) => a[0])).toEqual(["ln:3"]);
    expect(src.stats().layers).toBe(1);
    src.dispose();
  });

  it("an empty wireframe given its geometry meanwhile: the surface comes into the scene", async () => {
    const c = core("/viz/wireframes/5", meshManifest);
    let list = [wfInfo(1)];
    const scene = fakeScene();
    const src = new WireframesSource(scene, { wireframes: () => list, rasters: () => [], hidden: () => [], opacity: () => 1, texture: () => null });
    src.sync();
    await vi.waitFor(() => expect(c.state.asked).toBe(1));
    c.state.version = 2;
    list = [wfInfo(2)];
    src.sync();
    c.release();
    await vi.waitFor(() => expect(src.idle).toBe(true));
    expect(c.state.asked).toBe(2);
    expect(vi.mocked(scene.addLayer).mock.calls.map((a) => a[0])).toEqual(["wf:5"]);
    src.dispose();
  });

  it("one answer of the version asked: no second request", async () => {
    const c = core("/viz/lines/3", lineManifest);
    c.state.version = 2;
    const layers = [lineInfo(2)];
    const scene = fakeScene();
    const src = new LinesSource(scene, { layers: () => layers, hidden: () => [], floor: () => 0 });
    src.sync();
    c.release();
    await vi.waitFor(() => expect(src.idle).toBe(true));
    expect(c.state.asked).toBe(1);
    expect(src.stats().layers).toBe(1);
    src.dispose();
  });
});
