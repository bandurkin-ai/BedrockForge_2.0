/**
 * Stand-ins of parts of wireframes in the window (ADR 0015 §8, 9.16): a level the core has not made yet comes as
 * the whole part — `standin` in its header, no ETag, `no-store`. The window shows it, says «готовятся копии для
 * показа», does not take it for «the part in full» and does not ask other simplified levels; the event
 * `wireframes.lod` of the open project asks levels 1–2 again (without `If-None-Match`), and the real level
 * replaces the stand-in.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { build } from "./bfa1-build";
import { setCoreTarget } from "../src/shell/api";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { lodReady } from "../src/modules/scene/objectSources";
import type { WireframeInfo } from "../src/modules/scene/objects";
import { sceneUi } from "../src/modules/scene/sceneHost";
import { WireframesSource } from "../src/modules/scene/wireframesSource";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import { lookFrom } from "../src/viz/core/views";
import type { FrameContext, Scene } from "../src/viz/Scene";

const ID = 41;
const ORIGIN = [500_050, 7_000_050, 300];
const BBOX = [500_000, 7_000_000, 290, 500_100, 7_000_100, 310];
/** The part in full: a 4 × 4 grid of squares, 32 triangles. */
const FULL = 32;
const opts = { aspect: 1.25, perspective: false, fov: 45, vexag: 1 };

function grid(n: number): { xyz: Float32Array; idx: Uint16Array } {
  const side = Math.round(Math.sqrt(n / 2));
  const xyz = new Float32Array((side + 1) ** 2 * 3);
  for (let j = 0; j <= side; j++) for (let i = 0; i <= side; i++) xyz.set([(i / side) * 100 - 50, (j / side) * 100 - 50, 0], (j * (side + 1) + i) * 3);
  const idx: number[] = [];
  for (let j = 0; j < side; j++)
    for (let i = 0; i < side; i++) {
      const a = j * (side + 1) + i;
      idx.push(a, a + 1, a + side + 2, a, a + side + 2, a + side + 1);
    }
  return { xyz, idx: Uint16Array.from(idx) };
}

/** A core with one wireframe of one part; its levels 1–2 are made when `made` is set. */
function core() {
  const state = { made: false };
  const asked: { lod: number | null; tag: string | null }[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = new URL(String(url));
    const tag = new Headers(init?.headers).get("If-None-Match");
    if (u.pathname.endsWith(`/viz/wireframes/${ID}`)) {
      asked.push({ lod: null, tag });
      const header = {
        kind: "wireframe", wireframe: { id: ID, name: "Рельеф" }, version: 1, color: "#c9a46a", dtm: null, points: 25, triangles: FULL, bbox: BBOX,
        lods: [{ lod: 0, ratio: 1 }, { lod: 1, ratio: 8 }, { lod: 2, ratio: 64 }], parts: [{ k: 0, n: FULL, bbox: BBOX, origin: ORIGIN, etags: ['"p0"', '"p1"', '"p2"'] }],
      };
      return new Response(build(header, []), { headers: { ETag: '"m1"' } });
    }
    const lod = Number(u.searchParams.get("lod"));
    asked.push({ lod, tag });
    const standin = lod > 0 && !state.made;
    const n = standin || lod === 0 ? FULL : lod === 1 ? 8 : 2;
    const g = grid(n);
    const header: Record<string, unknown> = { kind: "mesh", k: 0, lod, origin: ORIGIN, bbox: BBOX, triangles: n, vertices: g.xyz.length / 3, source_triangles: FULL, simplified: n < FULL, index: "<u2" };
    if (standin) header.standin = true;
    const body = build(header, [["xyz", "<f4", g.xyz], ["idx", "<u2", g.idx]]);
    return new Response(body, { headers: standin ? { "Cache-Control": "no-store" } : { ETag: `"p${lod}"` } });
  });
  return { state, asked, fetch };
}

function fakeScene() {
  let frame: ((ctx: FrameContext) => void) | null = null;
  const origin = new SceneOrigin();
  const scene = {
    origin,
    clipPlanes: [],
    gpu: { gpuClass: "discrete" },
    renderer: { budgetFactor: () => 1 },
    onFrame: (fn: (ctx: FrameContext) => void) => ((frame = fn), () => (frame = null)),
    invalidate: vi.fn(),
    layerChanged: vi.fn(),
    addLayer: vi.fn((_key: string, layer: { bbox: Float64Array }) => void origin.ensure(layer.bbox)),
    removeLayer: vi.fn(),
  };
  const o = { x: ORIGIN[0], y: ORIGIN[1], z: ORIGIN[2] };
  // far above the part: its coarsest level is wanted
  const camera = lookFrom([0, -1e-4, 1], { min: [BBOX[0] - o.x - 5e4, BBOX[1] - o.y - 5e4, -10], max: [BBOX[3] - o.x + 5e4, BBOX[4] - o.y + 5e4, 10] }, opts);
  const draw = () => frame?.({ camera, options: opts, width: 1000, height: 800, vexag: 1, pixelsPerUnit: () => 1 });
  return { scene: scene as unknown as Scene, draw };
}

const wf: WireframeInfo = { id: ID, name: "Рельеф", version: 1, color: null, point_count: 25, triangle_count: FULL, extent: null, dtm: null, stats: null, source: null } as unknown as WireframeInfo;

async function start() {
  const c = core();
  vi.stubGlobal("fetch", c.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  const { scene, draw } = fakeScene();
  const source = new WireframesSource(scene, { wireframes: () => [wf], rasters: () => [], hidden: () => [], opacity: () => 1, texture: () => null });
  const settle = async () => {
    for (let i = 0; i < 4; i++) {
      draw();
      await vi.waitFor(() => expect(source.idle).toBe(true));
    }
    await Promise.resolve(); // the notes of the scene are set in a microtask
  };
  source.sync();
  await settle();
  return { c, source, settle };
}

afterEach(() => {
  vi.unstubAllGlobals();
  projectStore.set({ current: null });
});

describe("a part of a wireframe whose levels the core has not made yet", () => {
  it("the stand-in is shown and marked, not kept, and finer simplified levels are not asked for it", async () => {
    const t = await start();
    expect(t.c.asked).toEqual([{ lod: null, tag: null }, { lod: 2, tag: null }]);
    expect(t.source.stats()).toMatchObject({ parts: 1, triangles: FULL, simplified: 0, standins: 1, shown: [0, 0, 1] });
    expect(sceneUi.get().wireframesStandins).toBe(1);
    // nearer: level 1 is wanted, and it would come whole as well — nothing is asked until `wireframes.lod`
    t.source.forceLevel(1);
    await t.settle();
    expect(t.c.asked).toHaveLength(2);
    // the full part is still worth asking: the stand-in did not make the part «whole»
    t.source.forceLevel(0);
    await t.settle();
    expect(t.c.asked[2]).toEqual({ lod: 0, tag: null });
    t.source.dispose();
  });

  it("`wireframes.lod` asks levels 1–2 again without If-None-Match; the real levels replace the stand-in", async () => {
    const t = await start();
    t.source.forceLevel(1);
    await t.settle();
    t.c.state.made = true;
    t.source.levelsReady([ID + 1]); // another wireframe: nothing is asked
    await t.settle();
    expect(t.c.asked).toHaveLength(2);
    t.source.levelsReady([ID]);
    await t.settle();
    expect(t.c.asked.slice(2).sort((a, b) => a.lod! - b.lod!)).toEqual([{ lod: 1, tag: null }, { lod: 2, tag: null }]);
    expect(t.source.stats()).toMatchObject({ triangles: 8, simplified: 1, standins: 0, shown: [0, 1] });
    expect(sceneUi.get().wireframesStandins).toBe(0);
    // a real level is not asked again by the next event
    t.source.levelsReady([ID]);
    await t.settle();
    expect(t.c.asked).toHaveLength(4);
    t.source.dispose();
  });

  it("the event counts for the open project only", () => {
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    expect(lodReady(JSON.stringify({ source: "p1", job: "wireframe.lod", wireframe_ids: [41, "x", 7] }))).toEqual([41, 7]);
    expect(lodReady(JSON.stringify({ source: "p2", job: "wireframe.lod", wireframe_ids: [41] }))).toBeNull();
    expect(lodReady("не JSON")).toBeNull();
    expect(lodReady(JSON.stringify({ source: "p1" }))).toBeNull();
  });
});
