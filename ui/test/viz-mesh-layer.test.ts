/**
 * The three.js layer of surfaces without a GPU (ADR 0011 §4): parts, levels, materials, the drape of a raster
 * with the image coordinates of every part from its own origin, the click, and the flat raster that is hidden
 * or cut out while a DTM carries it.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { LayerSet } from "../src/viz/core/layers";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import { lookFrom, screenRay } from "../src/viz/core/views";
import type { MeshManifest } from "../src/viz/mesh/build";
import { DRAPE_PROGRAM_KEY, imageWindow } from "../src/viz/mesh/drape";
import { DEFAULT_MESH_COLOR, MeshLayerView, SURFACE_SCORE, type MeshPick } from "../src/viz/mesh/layer";
import { projectToScreen, toNdc, viewProjection } from "../src/viz/pick/ray";
import { RasterLayerView, type RasterPick } from "../src/viz/rasters/layer";
import { patchHolesShader, rasterPlacement } from "../src/viz/rasters/placement";
import type { PickQuery } from "../src/viz/sceneTypes";
import { buildMesh } from "../src/viz/worker/mesh";

const W = 1000;
const H = 800;
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };

/** Two parts 2 km apart in UTM: a square 100 × 100 m each, heights about 300 m. */
function manifest(): MeshManifest {
  return {
    wireframeId: 41, name: "Рельеф", version: 1, color: "#c9a46a", dtm: null, points: 8, triangles: 4,
    bbox: [500_000, 7_000_000, 290, 502_100, 7_000_100, 310], ratios: [1, 8, 64],
    parts: [
      { k: 0, n: 2, bbox: [500_000, 7_000_000, 290, 500_100, 7_000_100, 310], origin: [500_050, 7_000_050, 300], etags: ['"a0"', '"a1"', '"a2"'] },
      { k: 1, n: 2, bbox: [502_000, 7_000_000, 295, 502_100, 7_000_100, 305], origin: [502_050, 7_000_050, 300], etags: ['"b0"', '"b1"', '"b2"'] },
    ],
  };
}

function level(k: number, lod: number, pts: number[][], tri: number[][], source = tri.length) {
  const origin = manifest().parts[k].origin;
  const xyz = new Float32Array(pts.length * 3);
  pts.forEach((p, i) => p.forEach((v, c) => (xyz[i * 3 + c] = v - origin[c])));
  return buildMesh(
    build({ kind: "mesh", k, lod, origin, triangles: tri.length, vertices: pts.length, source_triangles: source, simplified: tri.length < source }, [
      ["xyz", "<f4", xyz], ["idx", "<u2", Uint16Array.from(tri.flat())],
    ]),
  );
}

/** Part 0 in full (a ridge along x = 500 050 at 310 m) and at the coarsest level (a flat square at 300 m); part 1 flat. */
function view(style = { color: "#c9a46a" as string | null, opacity: 1, visible: true }) {
  const clip: THREE.Plane[] = [];
  const v = new MeshLayerView(manifest(), { clip }, style);
  const x0 = 500_000;
  const y0 = 7_000_000;
  v.setLevel(0, level(0, 0, [[x0, y0, 290], [x0 + 50, y0, 310], [x0 + 100, y0, 290], [x0, y0 + 100, 290], [x0 + 50, y0 + 100, 310], [x0 + 100, y0 + 100, 290]], [[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]]));
  v.setLevel(0, level(0, 2, [[x0, y0, 300], [x0 + 100, y0, 300], [x0 + 100, y0 + 100, 300], [x0, y0 + 100, 300]], [[0, 1, 2], [0, 2, 3]], 4));
  v.setLevel(1, level(1, 2, [[x0 + 2000, y0, 300], [x0 + 2100, y0, 300], [x0 + 2100, y0 + 100, 300], [x0 + 2000, y0 + 100, 300]], [[0, 1, 2], [0, 2, 3]]));
  v.show(0, 0);
  v.show(1, 2);
  return { v, clip };
}

/** An image 400 × 200 m over part 0: 0.25 m pixels, the top-left corner at (499 900.5, 7 000 150.25). */
const CORNERS = [[499_900.5, 7_000_150.25, null], [500_300.5, 7_000_150.25, null], [500_300.5, 6_999_950.25, null], [499_900.5, 6_999_950.25, null]];

function query(px: number, py: number, vp: Float64Array, cam: ReturnType<typeof lookFrom>, offset: [number, number, number] = [0, 0, 0]): PickQuery {
  return { vp, vexag: 1, px, py, width: W, height: H, inWindow: () => true, ray: screenRay(cam, opts, ...toNdc(px, py, W, H)), offset };
}

type Shader = Parameters<THREE.Material["onBeforeCompile"]>[0];
/** What three.js gives a material before compiling its program. */
function compile(m: THREE.Material, lib: { vertexShader: string; fragmentShader: string }): Shader {
  const shader = { uniforms: {}, vertexShader: lib.vertexShader, fragmentShader: lib.fragmentShader } as unknown as Shader;
  m.onBeforeCompile(shader, null as unknown as THREE.WebGLRenderer);
  return shader;
}

describe("a surface in the scene", () => {
  it("parts stand at whole-metre offsets from the layer origin; a material per part, one program for all", () => {
    const { v, clip } = view();
    expect(v.origin).toEqual({ x: 500_050, y: 7_000_050, z: 300 });
    const [p0, p1] = v.object.children as THREE.Group[];
    expect(p0.position.toArray()).toEqual([0, 0, 0]);
    expect(p1.position.toArray()).toEqual([2000, 0, 0]);
    expect(Array.from(v.bbox)).toEqual([500_000, 7_000_000, 290, 502_100, 7_000_100, 310]);
    const m0 = (p0.children[0] as THREE.Mesh).material as THREE.MeshLambertMaterial;
    const m1 = (p1.children[0] as THREE.Mesh).material as THREE.MeshLambertMaterial;
    expect(m0).not.toBe(m1);
    expect(m0.customProgramCacheKey()).toBe(DRAPE_PROGRAM_KEY);
    expect(m1.customProgramCacheKey()).toBe(DRAPE_PROGRAM_KEY);
    for (const m of [m0, m1]) {
      expect(m.clippingPlanes).toBe(clip);
      expect(m.flatShading).toBe(true);
      expect(m.side).toBe(THREE.DoubleSide);
      expect(m.color.getHexString()).toBe("c9a46a");
      expect(m.transparent).toBe(false);
      expect(m.polygonOffset).toBe(true);
    }
    // the buffers of the Worker are used as they are; the sphere comes from the extent of the part
    const g = (p0.children[0] as THREE.Mesh).geometry;
    expect(g.getAttribute("position").count).toBe(6);
    expect(g.getIndex()!.array).toBeInstanceOf(Uint16Array);
    expect(g.getAttribute("normal")).toBeUndefined();
    expect(g.boundingSphere!.radius).toBeCloseTo(Math.hypot(100, 100, 20) / 2, 6);
    v.dispose();
    expect(v.object.children.length).toBe(0);
  });

  it("one loaded level of a part is shown; the triangles of what is seen are counted", () => {
    const { v } = view();
    expect(v.levels(0).sort()).toEqual([0, 2]);
    expect(v.shown(0)).toBe(0);
    expect(v.stats()).toEqual({ parts: 2, triangles: 6, simplified: 0, standins: 0 });
    expect(v.show(0, 2)).toBe(true);
    expect(v.show(0, 2)).toBe(false);
    expect(v.stats()).toEqual({ parts: 2, triangles: 4, simplified: 1, standins: 0 });
    const meshes = (v.object.children[0] as THREE.Group).children;
    expect(meshes.map((m) => m.visible)).toEqual([false, true]);
    expect(v.levelTriangles(0, 0)).toBe(4);
    expect(v.levelTriangles(0, 1)).toBeNull();
    v.dropLevel(0, 2);
    expect(v.shown(0)).toBeNull();
    expect(v.show(0, 1)).toBe(false); // not loaded
    expect(v.show(0, 0)).toBe(true);
    // a level without a triangle has nothing to draw but is a level
    v.setLevel(1, level(1, 0, [[502_000, 7_000_000, NaN], [502_100, 7_000_000, 300], [502_100, 7_000_100, 300]], [[0, 1, 2]]));
    expect(v.levels(1).sort()).toEqual([0, 2]);
    expect(v.show(1, 0)).toBe(true);
    expect(v.stats().triangles).toBe(4);
  });

  it("the colour is the wireframe's or the default; the opacity of the view blends", () => {
    const { v } = view({ color: null, opacity: 0.7, visible: true });
    const m = ((v.object.children[0] as THREE.Group).children[0] as THREE.Mesh).material as THREE.MeshLambertMaterial;
    expect(`#${m.color.getHexString()}`).toBe(DEFAULT_MESH_COLOR);
    expect(m.transparent).toBe(true);
    expect(m.depthWrite).toBe(false);
    expect(m.opacity).toBe(0.7);
    v.setStyle({ color: "#112233", opacity: 1, visible: false });
    expect(m.color.getHexString()).toBe("112233");
    expect(m.transparent).toBe(false);
    expect(m.depthWrite).toBe(true);
    expect(v.visible).toBe(false);
    v.setStyle({ color: "красный", opacity: 5, visible: true });
    expect(`#${m.color.getHexString()}`).toBe(DEFAULT_MESH_COLOR);
    expect(m.opacity).toBe(1);
    // the picked surface glows in the selection colour
    v.select(true);
    expect(m.emissive.getHex()).not.toBe(0);
    v.select(false);
    expect(m.emissive.getHex()).toBe(0);
  });

  it("a draped raster: the corner of the image from the origin of every part, one image for all levels", () => {
    const { v } = view();
    const [p0, p1] = v.object.children as THREE.Group[];
    const s0 = compile((p0.children[0] as THREE.Mesh).material as THREE.Material, THREE.ShaderLib.lambert);
    const s1 = compile((p1.children[0] as THREE.Mesh).material as THREE.Material, THREE.ShaderLib.lambert);
    const u0 = s0.uniforms as Record<string, { value: unknown }>;
    const u1 = s1.uniforms as Record<string, { value: unknown }>;
    expect(s0.vertexShader).toContain("uDrapeO");
    expect(u0.uDrapeMix.value).toBe(0);
    expect(v.draped).toBe(false);

    v.setDrape({ corners: CORNERS, tint: "#ff8000", opacity: 0.8, shade: false });
    // no image yet: the surface keeps its own colour
    expect(u0.uDrapeMix.value).toBe(0);
    const texture = new THREE.Texture();
    expect(v.setDrapeTexture(texture)).toBe(true);
    expect(v.setDrapeTexture(texture)).toBe(false);
    expect(v.draped).toBe(true);
    expect(u0.uDrapeMix.value).toBe(0.8);
    expect(u0.uDrapeMap.value).toBe(texture);
    expect(u1.uDrapeMap).toBe(u0.uDrapeMap); // shared by the parts
    expect(u0.uDrapeShade.value).toBe(0);
    expect((u0.uDrapeTint.value as THREE.Color).getHexString()).toBe("ff8000");
    // float64 on the CPU: the corner minus the origin of the part — exact, no UTM in a uniform
    expect((u0.uDrapeO.value as THREE.Vector2).toArray()).toEqual([-149.5, 100.25]);
    expect((u1.uDrapeO.value as THREE.Vector2).toArray()).toEqual([-2149.5, 100.25]);
    expect((u0.uDrapeU.value as THREE.Vector2).toArray()).toEqual([1 / 400, 0]);
    expect((u0.uDrapeV.value as THREE.Vector2).toArray()).toEqual([0, -1 / 200]);

    // the same plan point on two levels of detail: the same image coordinates, another height
    const [x, y] = [500_030.25, 7_000_060.5];
    const fine = v.probe(x, y)!;
    v.show(0, 2);
    const coarse = v.probe(x, y)!;
    expect(fine).toMatchObject({ k: 0, lod: 0, triangles: 4 });
    expect(coarse).toMatchObject({ k: 0, lod: 2, triangles: 2 });
    expect(fine.z).toBeCloseTo(290 + (30.25 / 50) * 20, 3);
    expect(coarse.z).toBe(300);
    const exact = [(x - 499_900.5) / 400, (7_000_150.25 - y) / 200];
    for (const k of [0, 1]) {
      expect(Math.abs(fine.uv![k] - coarse.uv![k])).toBeLessThan(1e-6);
      expect(Math.abs(fine.uv![k] - exact[k]) * 1600).toBeLessThan(0.02); // px of the image
      expect(fine.exact![k]).toBeCloseTo(exact[k], 12);
    }
    expect(v.probe(501_000, 7_000_050)).toBeNull(); // between the parts

    // the raster is taken off: the own colour again
    v.setDrape(null);
    expect(u0.uDrapeMix.value).toBe(0);
    expect(v.draped).toBe(false);
    expect(v.probe(x, y)!.uv).toBeNull();
    // an image without area is no drape
    v.setDrape({ corners: [[0, 0], [0, 0], [0, 0], [0, 0]], tint: "#ffffff", opacity: 1, shade: true });
    expect(v.draped).toBe(false);
  });

  it("a click gives the surface and the place in project coordinates; a line under the cursor wins", () => {
    const { v } = view();
    const origin = new SceneOrigin();
    const root = new THREE.Group();
    const set = new LayerSet(root, origin);
    set.add("wf:41", v);
    const cam = lookFrom([0, -1e-4, 1], { min: [-1100, -100, -20], max: [1100, 100, 20] }, opts);
    const vp = viewProjection(cam, opts);
    const off = origin.offset(v.origin);
    expect(off).toEqual([-1000, 0, 0]);
    // the scene origin is the centre of the extent of the layer: part 0 stands at `off`
    const at = projectToScreen(vp, off[0] - 20, off[1] + 10, off[2], W, H)!;
    const hit = set.pick(query(at[0], at[1], vp, cam))!;
    expect(hit.key).toBe("wf:41");
    expect(hit.score).toBeGreaterThanOrEqual(SURFACE_SCORE);
    expect(hit.score).toBeLessThan(SURFACE_SCORE + 1);
    const pick = hit.hit as MeshPick;
    expect(pick).toMatchObject({ wireframeId: 41, k: 0, lod: 0 });
    expect(pick.xyz[0]).toBeCloseTo(500_030, 3);
    expect(pick.xyz[1]).toBeCloseTo(7_000_060, 3);
    expect(pick.xyz[2]).toBeCloseTo(290 + (30 / 50) * 20, 3);
    // the ray of the click in project coordinates passes through the picked place: what the core is asked with
    const t = (pick.xyz[2] - pick.ray.origin[2]) / pick.ray.dir[2];
    expect(t).toBeGreaterThan(0);
    expect(pick.ray.origin[0] + t * pick.ray.dir[0]).toBeCloseTo(pick.xyz[0], 6);
    expect(pick.ray.origin[1] + t * pick.ray.dir[1]).toBeCloseTo(pick.xyz[1], 6);
    // the coarse level of the same part answers at its own height
    v.show(0, 2);
    expect((set.pick(query(at[0], at[1], vp, cam))!.hit as MeshPick).xyz[2]).toBeCloseTo(300, 6);
    // a hidden layer is not asked; beside the parts nothing answers
    v.setVisible(false);
    expect(set.pick(query(at[0], at[1], vp, cam))).toBeNull();
    v.setVisible(true);
    const beside = projectToScreen(vp, off[0] + 1000, off[1], off[2], W, H)!;
    expect(set.pick(query(beside[0], beside[1], vp, cam))).toBeNull();
  });
});

describe("a flat raster while a DTM carries it", () => {
  const placement = rasterPlacement({ corners3d: CORNERS, elevation: 280, vertical: false }, 0)!;
  const make = () => new RasterLayerView(7, placement, { clip: [], background: (c) => c.set(0x11161d) }, { opacity: 1, visible: true, vertical: false, alpha: false });

  it("«Скрыть данные за ЦТМ» hides it whole; without it the image is cut out where the surface covers it", () => {
    const r = make();
    r.setTexture(new THREE.Texture());
    const mesh = r.object.children[0] as THREE.Mesh;
    const shader = compile(mesh.material as THREE.Material, THREE.ShaderLib.basic);
    const u = shader.uniforms as Record<string, { value: unknown }>;
    expect(shader.fragmentShader).toContain("uHoles[4]");
    expect(u.uHoleCount.value).toBe(0);
    r.setCover(true);
    expect(r.visible).toBe(false);
    r.setCover(false, [imageWindow(CORNERS, [500_000, 7_000_000], [500_100, 7_000_100])!]);
    expect(r.visible).toBe(true);
    expect(u.uHoleCount.value).toBe(1);
    (u.uHoles.value as THREE.Vector4[])[0].toArray().forEach((n, i) => expect(n).toBeCloseTo([99.5 / 400, 50.25 / 200, 199.5 / 400, 150.25 / 200][i], 12));
    // the visibility of the raster itself still counts
    r.setStyle({ opacity: 1, visible: false, vertical: false, alpha: false });
    expect(r.visible).toBe(false);
    r.setStyle({ opacity: 1, visible: true, vertical: false, alpha: false });
    expect(r.visible).toBe(true);

    // a click in the hole goes through the raster, a click beside it picks the raster
    const cam = lookFrom([0, -1e-4, 1], { min: [-200, -100, -20], max: [200, 100, 20] }, opts);
    const vp = viewProjection(cam, opts);
    const click = (x: number, y: number) => {
      const s = projectToScreen(vp, x - placement.origin.x, y - placement.origin.y, 0, W, H)!;
      return r.pick(query(s[0], s[1], vp, cam));
    };
    expect(click(500_050, 7_000_050)).toBeNull();
    const beside = click(500_250, 7_000_050)!;
    expect((beside.hit as RasterPick).rasterId).toBe(7);
    expect((beside.hit as RasterPick).xyz[0]).toBeCloseTo(500_250, 3);
    r.setCover(false, []);
    expect(click(500_050, 7_000_050)).not.toBeNull();
  });

  it("every anchor of the holes is in the basic shader of this three.js", () => {
    const shader = { vertexShader: THREE.ShaderLib.basic.vertexShader, fragmentShader: THREE.ShaderLib.basic.fragmentShader };
    expect(patchHolesShader(shader)).toBe(true);
    expect(shader.vertexShader).toContain("vHoleUv = uv;");
    expect(patchHolesShader({ vertexShader: "", fragmentShader: "" })).toBe(false);
  });
});
