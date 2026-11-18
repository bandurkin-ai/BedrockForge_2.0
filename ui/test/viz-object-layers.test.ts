/** The three.js layers of lines and rasters without a GPU: objects, materials, clipping, floor, picking. */
import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { LayerSet } from "../src/viz/core/layers";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import { lookFrom, screenRay } from "../src/viz/core/views";
import type { LabelCache } from "../src/viz/labels/sprites";
import { sameLayout, type LinesManifest } from "../src/viz/lines/build";
import { LineLayerView, type LinePick } from "../src/viz/lines/layer";
import { projectToScreen, toNdc, viewProjection } from "../src/viz/pick/ray";
import { RASTER_SCORE, RasterLayerView, type RasterPick } from "../src/viz/rasters/layer";
import { rasterPlacement } from "../src/viz/rasters/placement";
import type { PickQuery } from "../src/viz/sceneTypes";
import { buildLines } from "../src/viz/worker/lines";

const W = 1000;
const H = 800;
const opts = { aspect: W / H, perspective: false, fov: 45, vexag: 1 };

/** part 0 — lines with heights around (500 000, 7 000 000, 300); part 1 — 2 km east, without any height */
function manifest(): LinesManifest {
  return {
    layerId: 12, name: "Слой", fileId: 5, version: 1, pointsOnly: false, color: "#e8873a", lines: 3, points: 6,
    bbox: [499_900, 6_999_990, 290, 502_100, 7_000_010, 310], tols: [0, 4, 32], labels: false,
    parts: [
      { k: 0, n: 2, points: 4, bbox: [499_900, 6_999_990, 290, 500_100, 7_000_010, 310], origin: [500_000, 7_000_000, 300], etags: ['"a0"', '"a1"', '"a2"'] },
      { k: 1, n: 1, points: 2, bbox: [501_900, 7_000_000, null, 502_100, 7_000_000, null], origin: [502_000, 7_000_000, 0], etags: ['"b0"', '"b1"', '"b2"'] },
    ],
  };
}

function part(k: number, origin: [number, number, number], pts: number[][], offsets: number[], line: number[], bbox: (number | null)[], lod = 0) {
  const xyz = new Float32Array(pts.length * 3);
  pts.forEach((p, i) => p.forEach((v, c) => (xyz[i * 3 + c] = v - origin[c])));
  const n = offsets.length - 1;
  return buildLines(
    build({ kind: "lines", k, lod, points_only: false, origin, bbox, n, points: pts.length, labels: null, simplified: lod > 0 }, [
      ["offsets", "<u4", Uint32Array.from(offsets)], ["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.from(line)], ["closed", "<u1", new Uint8Array(n)],
    ]),
  );
}

function view(floor = 250) {
  const clip: THREE.Plane[] = [];
  const ctx = { clip, resolution: new THREE.Vector2(W, H), labels: null as unknown as LabelCache, pixelRatio: 1 };
  const v = new LineLayerView(manifest(), ctx, { color: "#e8873a", visible: true }, floor);
  const m = manifest();
  v.setLevel(0, part(0, m.parts[0].origin, [[499_900, 7_000_000, 290], [500_100, 7_000_000, 310], [500_050, 7_000_010, 300]], [0, 2, 3], [70, 71], m.parts[0].bbox!));
  v.setLevel(1, part(1, m.parts[1].origin, [[501_900, 7_000_000, NaN], [502_100, 7_000_000, NaN]], [0, 2], [72], m.parts[1].bbox!));
  v.show(0, 0);
  v.show(1, 0);
  return { v, clip };
}

/** A top view over the layer; the scene origin is the origin of part 0. */
function query(px: number, py: number, vp: Float64Array, cam: ReturnType<typeof lookFrom>, offset: [number, number, number] = [0, 0, 0]): PickQuery {
  return { vp, vexag: 1, px, py, width: W, height: H, inWindow: () => true, ray: screenRay(cam, opts, ...toNdc(px, py, W, H)), offset };
}

describe("a layer of lines in the scene", () => {
  it("parts stand at whole-metre offsets from the layer origin; a part without heights — at the floor", () => {
    const { v, clip } = view(250);
    expect(v.origin).toEqual({ x: 500_000, y: 7_000_000, z: 300 });
    const [p0, p1] = v.object.children as THREE.Group[];
    expect(p0.position.toArray()).toEqual([0, 0, 0]);
    expect(p1.position.toArray()).toEqual([2000, 0, -50]); // floor 250 − layer origin 300
    expect(Array.from(v.bbox)).toEqual([499_900, 6_999_990, 250, 502_100, 7_000_010, 310]);
    expect(v.partBbox(1)).toEqual([501_900, 7_000_000, 250, 502_100, 7_000_000, 250]);
    // the floor moved: only the flat part and the extent follow
    expect(v.setFloor(120)).toBe(true);
    expect(p1.position.z).toBe(-180);
    expect(p0.position.z).toBe(0);
    expect(v.bbox[2]).toBe(120);
    expect(v.setFloor(120)).toBe(false);
    // lines with a width in px take the shared clipping planes; a heavy scene gets one-pixel lines
    const line = (p0.children[0] as THREE.Group).children[0] as LineSegments2;
    expect(line).toBeInstanceOf(LineSegments2);
    expect(line.material.clippingPlanes).toBe(clip);
    v.setFat(false);
    const thin = (p0.children[0] as THREE.Group).children.find((o) => o instanceof THREE.LineSegments) as THREE.LineSegments;
    expect(thin).toBeDefined();
    expect((thin.material as THREE.LineBasicMaterial).clippingPlanes).toBe(clip);
    expect(v.stats()).toEqual({ segments: 2, points: 1, parts: 2, simplified: 0 });
    v.dispose();
    expect(v.object.children.length).toBe(0);
  });

  it("one loaded level of a part is shown; the colour and the visibility are the layer's", () => {
    const { v } = view();
    const m = manifest();
    v.setLevel(0, part(0, m.parts[0].origin, [[499_900, 7_000_000, 290], [500_100, 7_000_000, 310]], [0, 2], [70], m.parts[0].bbox!, 2));
    expect(v.levels(0).sort()).toEqual([0, 2]);
    expect(v.show(0, 2)).toBe(true);
    expect(v.show(0, 2)).toBe(false);
    expect(v.shown(0)).toBe(2);
    expect(v.stats().simplified).toBe(1);
    const groups = (v.object.children[0] as THREE.Group).children;
    expect(groups.map((g) => g.visible)).toEqual([false, true]);
    v.dropLevel(0, 2);
    expect(v.shown(0)).toBeNull();
    v.setColor(null);
    v.show(0, 0);
    const mat = ((v.object.children[0] as THREE.Group).children[0] as THREE.Group).children[0] as LineSegments2;
    expect(mat.material.color.getHexString()).toBe("8a99a8");
    v.setVisible(false);
    expect(v.object.visible).toBe(false);
  });

  it("a click gives the line of the layer and the place in project coordinates; the picked line is drawn over", () => {
    const { v } = view(250);
    const cam = lookFrom([0, -1e-4, 1], { min: [-200, -160, -60], max: [2200, 160, 20] }, opts);
    const vp = viewProjection(cam, opts);
    // the flat part: local (-100…100, 0, 0) at the offset (2000, 0, −50)
    const at = projectToScreen(vp, 2050, 0, -50, W, H)!;
    const hit = v.pick(query(at[0], at[1] + 2, vp, cam))!;
    const pick = hit.hit as LinePick;
    expect(pick).toMatchObject({ layerId: 12, k: 1, line: 72, on: "line", lod: 0 });
    expect(pick.xyz[0]).toBeCloseTo(502_050, 2);
    expect(pick.xyz[1]).toBeCloseTo(7_000_000, 6);
    expect(pick.xyz[2]).toBe(250);
    expect(hit.score).toBeLessThan(1);
    // the one-vertex line of part 0 is a point
    const dot = projectToScreen(vp, 50, 10, 0, W, H)!;
    expect((v.pick(query(dot[0] + 3, dot[1], vp, cam))!.hit as LinePick).on).toBe("point");
    expect(v.pick(query(5, 5, vp, cam))).toBeNull();
    v.select(72);
    v.beforeRender();
    const p1 = v.object.children[1] as THREE.Group;
    const over = p1.children.filter((o) => o.renderOrder === 7);
    expect(over.length).toBe(1);
    expect(((over[0] as LineSegments2).material.color as THREE.Color).getHexString()).toBe("f46a1a");
    v.select(null);
    v.beforeRender();
    expect(p1.children.filter((o) => o.renderOrder === 7).length).toBe(0);
  });

  it("a rename keeps the layout, another cut of the parts does not", () => {
    const a = manifest();
    const b = { ...manifest(), name: "Другое имя", version: 2, color: null };
    expect(sameLayout(a, b)).toBe(true);
    const c = manifest();
    c.parts[0] = { ...c.parts[0], points: 5 };
    expect(sameLayout(a, c)).toBe(false);
  });
});

describe("a raster in the scene", () => {
  const corners = [[500_000, 7_000_000, 420], [500_300, 7_000_000, 420], [500_300, 7_000_000, 120], [500_000, 7_000_000, 120]];
  const bg = (out: THREE.Color) => out.setRGB(0, 0, 0);
  const style = { opacity: 0.5, visible: true, vertical: true, alpha: false };

  function raster(vertical = true) {
    const clip: THREE.Plane[] = [];
    const p = rasterPlacement({ corners3d: corners, elevation: null, vertical }, 0)!;
    const v = new RasterLayerView(9, p, { clip, background: bg }, { ...style, vertical });
    const mesh = v.object.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    return { v, clip, mesh, p };
  }

  it("the quad is float32 from the integer origin; nothing is drawn until the image comes", () => {
    const { v, mesh } = raster();
    expect(v.origin).toEqual({ x: 500_150, y: 7_000_000, z: 270 });
    expect(Array.from(mesh.geometry.getAttribute("position").array)).toEqual([-150, 0, 150, 150, 0, 150, 150, 0, -150, -150, 0, -150]);
    expect(Array.from(mesh.geometry.getAttribute("uv").array)).toEqual([0, 0, 1, 0, 1, 1, 0, 1]); // row 0 of the image is its top
    expect(mesh.visible).toBe(false);
    const t = new THREE.Texture();
    v.setTexture(t);
    expect(mesh.visible).toBe(true);
    expect(mesh.material.map).toBe(t);
  });

  it("without clipping an ordinary object: clipped, depth-tested, its opacity blends", () => {
    const { v, clip, mesh } = raster();
    v.beforeRender();
    const m = mesh.material;
    expect([m.clippingPlanes, m.depthTest, m.depthWrite, m.transparent, m.opacity, mesh.renderOrder]).toEqual([clip, true, false, true, 0.5, -1]);
    expect(v.isBackdrop).toBe(false);
  });

  it("under clipping a vertical raster is a backdrop: out of the planes, first, no depth; holes stay on top", () => {
    const { v, clip, mesh } = raster();
    v.beforeRender();
    clip.push(new THREE.Plane(new THREE.Vector3(0, 1, 0), 25), new THREE.Plane(new THREE.Vector3(0, -1, 0), 25));
    v.beforeRender();
    const m = mesh.material;
    expect(v.isBackdrop).toBe(true);
    expect([m.clippingPlanes, m.depthTest, m.depthWrite, m.transparent, m.opacity, mesh.renderOrder]).toEqual([null, false, false, false, 1, -10]);
    expect(m.color.r).toBeCloseTo(0.5, 6); // dimmed toward the (black) background by the opacity
    // the clipping is off again: an ordinary object again
    clip.length = 0;
    v.beforeRender();
    expect([m.clippingPlanes, m.depthTest, mesh.renderOrder, m.color.r]).toEqual([clip, true, -1, 1]);
  });

  it("a raster in plan stays an ordinary clipped object under clipping", () => {
    const { v, clip, mesh } = raster(false);
    clip.push(new THREE.Plane(new THREE.Vector3(0, 1, 0), 25));
    v.beforeRender();
    expect(v.isBackdrop).toBe(false);
    expect(mesh.material.clippingPlanes).toBe(clip);
    expect(mesh.material.depthTest).toBe(true);
  });

  it("a click on the image gives the raster and the place; a line under the cursor wins over it", () => {
    const { v, p } = raster();
    v.setTexture(new THREE.Texture());
    const origin = new SceneOrigin();
    origin.ensure(p.bbox);
    const root = new THREE.Group();
    const set = new LayerSet(root, origin);
    set.add("rs:9", v);
    // looking north at the vertical raster
    const cam = lookFrom([0, -1, 0], { min: [-200, -10, -200], max: [200, 10, 200] }, opts);
    const vp = viewProjection(cam, opts);
    const at = projectToScreen(vp, 100, 0, 50, W, H)!;
    const q = query(at[0], at[1], vp, cam);
    const hit = set.pick(q)!;
    expect(hit.key).toBe("rs:9");
    expect(hit.score).toBeGreaterThanOrEqual(RASTER_SCORE);
    const xyz = (hit.hit as RasterPick).xyz;
    expect(xyz[0]).toBeCloseTo(500_250, 6);
    expect(xyz[1]).toBeCloseTo(7_000_000, 6);
    expect(xyz[2]).toBeCloseTo(320, 6);
    expect(set.pick(query(2, 2, vp, cam))).toBeNull();
    // a hidden raster is not asked
    v.setStyle({ ...style, visible: false });
    expect(set.pick(q)).toBeNull();
    v.setStyle(style);
    // a line layer through the same place: its hit (score ≤ 1) wins
    const lines: LinesManifest = { layerId: 3, name: "Л", fileId: 1, version: 1, pointsOnly: false, color: null, lines: 1, points: 2, bbox: [500_000, 7_000_000, 320, 500_300, 7_000_000, 320], tols: [0, 4, 32], labels: false, parts: [{ k: 0, n: 1, points: 2, bbox: [500_000, 7_000_000, 320, 500_300, 7_000_000, 320], origin: [500_150, 7_000_000, 320], etags: ["a", "b", "c"] }] };
    const lv = new LineLayerView(lines, { clip: [], resolution: new THREE.Vector2(W, H), labels: null as unknown as LabelCache, pixelRatio: 1 }, { color: null, visible: true }, 0);
    lv.setLevel(0, part(0, [500_150, 7_000_000, 320], [[500_000, 7_000_000, 320], [500_300, 7_000_000, 320]], [0, 2], [4], lines.parts[0].bbox!));
    lv.show(0, 0);
    set.add("ln:3", lv);
    const both = set.pick(q)!;
    expect(both.key).toBe("ln:3");
    expect((both.hit as LinePick).line).toBe(4);
    expect(set.keys()).toEqual(["rs:9", "ln:3"]);
    set.clear();
    expect(root.children.length).toBe(0);
  });
});
