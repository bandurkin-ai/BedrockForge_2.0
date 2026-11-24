// The layer of the shell of a block model (ТЗ 8.7.6; ADR 0023 §11, decisions 16–19; подшаг 17.30), read against the
// BFA1 `blocks` the kernels of the core write (`core/tests/test_blocks_layer_fixture.py` → `layer_fixture.json`: the
// bytes, the float64 faces, the corners in the project and the rays with the nearest face found by a plain loop):
// the decoder, the legend that never travels with the geometry, the edges in the shader, float32 from the whole
// origin at UTM coordinates, the index of the faces and the click; and the CPU of 2.5 million faces without WebGL.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { ColorScale } from "../src/viz/color/colorScale";
import { decodeBlocks, localExtent, NO_CLASS, type BlocksData } from "../src/viz/blocks/decode";
import { BlocksLayer, insideSlack, type BlocksPick } from "../src/viz/blocks/layer";
import { CATEGORY_COLORS, classKey, legendBytes, legendItems, NO_VALUE_COLOR, parseColor } from "../src/viz/blocks/legend";
import { FRAGMENT, VERTEX, faceCorners } from "../src/viz/blocks/material";
import { PickIndex, type Vec3 } from "../src/viz/blocks/pickIndex";
import { VizError } from "../src/viz/error";
import type { PickQuery } from "../src/viz/sceneTypes";

interface Ray {
  name: string;
  local: { o: Vec3; d: Vec3 };
  project: { o: Vec3; d: Vec3 };
  hit: { face: number; t: number; point: Vec3 } | null;
}
interface Case {
  name: string;
  bfa1: string;
  grid: { corner: Vec3; rotation: number; counts: Vec3 };
  origin: Vec3;
  faces: { c: number[][]; h: number[][]; a: number[]; k: number[] };
  corners_project: number[][][];
  rays: Ray[];
  faces_raw: number;
  simplified: boolean;
  lod: number;
}

const FIX: { cases: Case[] } = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/layer_fixture.json"), "utf-8"));
const byName = (n: string): Case => FIX.cases.find((c) => c.name === n)!;
/** The bytes of a case in an ArrayBuffer of their own (a Buffer shares a pool). */
function bytesOf(c: Case): ArrayBuffer {
  const b = Buffer.from(c.bfa1, "base64");
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
}
const decoded = (n: string): BlocksData => decodeBlocks(bytesOf(byName(n)));
const ctx = () => ({ clip: [] as THREE.Plane[] });
const layerOf = (n: string, opts = {}) => new BlocksLayer(decoded(n), ctx(), opts);

describe("the decoder of BFA1 blocks", () => {
  it("reads every case: the header, the arrays as the float32 of the float64 faces from the origin", () => {
    for (const c of FIX.cases) {
      const d = decodeBlocks(bytesOf(c));
      expect(d.faces, c.name).toBe(c.faces.a.length);
      expect(d.header.kind).toBe("blocks");
      expect(d.header.corner).toEqual(c.grid.corner);
      expect(d.header.origin).toEqual(c.origin);
      expect(d.header.faces_raw).toBe(c.faces_raw);
      expect(d.header.simplified).toBe(c.simplified);
      expect(d.header.lod).toBe(c.lod);
      expect(Array.from(d.a)).toEqual(c.faces.a);
      expect(Array.from(d.k)).toEqual(c.faces.k);
      for (let i = 0; i < d.faces; i++) {
        for (let j = 0; j < 3; j++) expect(d.c[3 * i + j]).toBe(Math.fround(c.faces.c[i][j] - c.origin[j]));
        for (let j = 0; j < 2; j++) expect(d.h[2 * i + j]).toBe(Math.fround(c.faces.h[i][j]));
      }
    }
  });

  it("takes the arrays as views of the answer: nothing is copied", () => {
    const buf = bytesOf(byName("scale_turned"));
    const d = decodeBlocks(buf);
    for (const arr of [d.c, d.h, d.a, d.k]) expect(arr.buffer).toBe(buf);
  });

  it("the cases: a lot of one class merge to 6 rectangles (Т8.7-9), the LOD is marked, «Все блоки» is a mode", () => {
    expect(decoded("cube_100").faces).toBe(6);
    expect(byName("cube_100").faces_raw).toBe(60_000);
    const lod = decoded("lod").header;
    expect([lod.simplified, lod.lod > 0, lod.faces_raw > lod.faces]).toEqual([true, true, true]);
    expect(decoded("all_blocks").header.mode).toBe("all");
    expect(decoded("scale_turned").header.mode).toBe("shell");
  });

  it("refuses a damaged answer in Russian, with a code", () => {
    const c = byName("classes_plain");
    const good = decodeBlocks(bytesOf(c));
    const rebuild = (head: Record<string, unknown>, arrays: [string, "<f4" | "<u1" | "<u2", Float32Array | Uint8Array | Uint16Array][]) => encodeBfa1(head, arrays);
    const head = { ...good.header };
    const parts = (): [string, "<f4" | "<u1" | "<u2", Float32Array | Uint8Array | Uint16Array][] => [["c", "<f4", good.c], ["h", "<f4", good.h], ["a", "<u1", good.a], ["k", "<u2", good.k]];
    const fails = (b: ArrayBuffer): VizError => {
      try {
        decodeBlocks(b);
      } catch (e) {
        expect(e).toBeInstanceOf(VizError);
        expect((e as VizError).message).toMatch(/[А-Яа-я]/);
        return e as VizError;
      }
      throw new Error("не отказал");
    };
    expect(fails(rebuild({ ...head, kind: "blocks_slice" }, parts())).code).toBe("blocks_kind");
    expect(fails(rebuild({ ...head, format: 2 }, parts())).code).toBe("blocks_format");
    expect(fails(rebuild({ ...head, mode: "x" }, parts())).code).toBe("blocks_header");
    expect(fails(rebuild({ ...head, origin: [1, 2] }, parts())).code).toBe("blocks_header");
    expect(fails(rebuild({ ...head, faces: head.faces + 1 }, parts())).code).toBe("bfa1_count");
    const a = good.a.slice();
    a[3] = 6;
    expect(fails(rebuild(head, [["c", "<f4", good.c], ["h", "<f4", good.h], ["a", "<u1", a], ["k", "<u2", good.k]])).code).toBe("blocks_side");
    const c2 = good.c.slice();
    c2[4] = NaN;
    expect(fails(rebuild(head, [["c", "<f4", c2], ["h", "<f4", good.h], ["a", "<u1", good.a], ["k", "<u2", good.k]])).code).toBe("blocks_finite");
    const h2 = good.h.slice();
    h2[0] = 0;
    expect(fails(rebuild(head, [["c", "<f4", good.c], ["h", "<f4", h2], ["a", "<u1", good.a], ["k", "<u2", good.k]])).code).toBe("blocks_finite");
    expect(fails(new TextEncoder().encode('{"code":"stale","message":"Модель изменилась.","hint":"Повторите."}').buffer as ArrayBuffer).code).toBe("stale");
  });
});

describe("the legend: colours are the viewer's, the classes the core's", () => {
  it("a scale: 256 buckets of the ramp in a texture of one row; a missing value is not in it", () => {
    const d = decoded("scale_turned");
    expect(d.header.classes).toBe(256);
    expect(d.header.bins).toHaveLength(256);
    expect(d.header.bins![0][0]).toBeCloseTo(0.1, 12);
    expect(d.header.bins![255][1]).toBeCloseTo(10, 12);
    expect(d.k.some((k) => k === NO_CLASS)).toBe(true); // a block without a value is in the shell
    const b = legendBytes("scale", d.header.classes, d.maxClass);
    expect([b.width, b.height, b.data.length]).toEqual([256, 1, 1024]);
    const ramp = new ColorScale(0, 1, false);
    for (const j of [0, 1, 127, 255]) expect(Array.from(b.data.subarray(4 * j, 4 * j + 3))).toEqual(ramp.rgb((j + 0.5) / 256));
    expect(b.data[3]).toBe(255);
  });

  it("a missing value is never the colour of class 0: the layer paints it with «нет значения»", () => {
    const l = layerOf("scale_turned");
    expect(l.mesh.material).toBeInstanceOf(THREE.ShaderMaterial);
    const u = (l.mesh.material as THREE.ShaderMaterial).uniforms;
    expect(`#${(u.uNoValue.value as THREE.Color).getHexString()}`).toBe(new THREE.Color(NO_VALUE_COLOR).getHexString().replace(/^/, "#"));
    expect(FRAGMENT).toMatch(/k < 65534\.5/); // the class 0xFFFF skips the texture
    const first = Array.from(legendBytes("scale", 256, 255).data.subarray(0, 3));
    expect(first).not.toEqual(parseColor(NO_VALUE_COLOR));
    l.dispose();
  });

  it("classes by breaks, categories with a code far over 255 (more rows), codes cycle the brand colours", () => {
    const k = decoded("classes_plain");
    expect(k.header.classes).toBe(4);
    expect(k.header.bins).toEqual([[null, 0.5], [0.5, 1], [1, 2], [2, null]]);
    const cl = legendBytes("classes", 4, k.maxClass);
    expect(Array.from(cl.data.subarray(0, 3))).toEqual(new ColorScale(0, 1, false).rgb(0));
    expect(Array.from(cl.data.subarray(12, 15))).toEqual(new ColorScale(0, 1, false).rgb(1));
    const c = decoded("categories");
    expect(c.maxClass).toBe(300);
    expect(c.k.includes(NO_CLASS)).toBe(true);
    const b = legendBytes("categories", c.header.classes, c.maxClass);
    expect([b.width, b.height]).toEqual([256, 2]);
    expect(Array.from(b.data.subarray(4 * 300, 4 * 300 + 3))).toEqual(parseColor(CATEGORY_COLORS[300 % CATEGORY_COLORS.length]));
    expect(Array.from(b.data.subarray(4 * 2, 4 * 2 + 3))).toEqual(parseColor(CATEGORY_COLORS[2]));
    const items = legendItems(c.header, c.maxClass);
    expect(items).toHaveLength(301);
    expect(items[0].label).toBe("руда");
    expect(items[7].label).toBeNull();
    expect(legendItems(decoded("cube_100").header, 0)).toEqual([]);
  });

  it("new colours refill the texels only: the buffers of the faces, their versions and the request key do not change", () => {
    const l = layerOf("scale_turned");
    const g = l.mesh.geometry as THREE.InstancedBufferGeometry;
    const attrs = ["iC", "iH", "iA", "iK"].map((n) => g.getAttribute(n) as THREE.InstancedBufferAttribute);
    const arrays = attrs.map((a) => a.array);
    const versions = attrs.map((a) => a.version);
    const tex = (l.mesh.material as THREE.ShaderMaterial).uniforms.uLegendMap.value as THREE.DataTexture;
    const before = tex.version;
    const key = classKey(l.header.field, l.header.colour);
    l.setStyle({ colors: ["#ff0000", null, "#00ff00"], noValue: "#222222" });
    expect(attrs.map((a) => a.array)).toEqual(arrays);
    attrs.forEach((a, i) => {
      expect(a.array).toBe(arrays[i]);
      expect(a.version).toBe(versions[i]);
    });
    expect(g.instanceCount).toBe(l.data.faces);
    expect(tex.version).toBeGreaterThan(before);
    expect((l.mesh.material as THREE.ShaderMaterial).uniforms.uLegendMap.value).toBe(tex); // the same texture, refilled
    const data = (tex.image.data as Uint8Array).subarray(0, 12);
    expect(Array.from(data)).toEqual([255, 0, 0, 255, ...new ColorScale(0, 1, false).rgb(1.5 / 256), 255, 0, 255, 0, 255]);
    expect(classKey(l.header.field, l.header.colour)).toBe(key);
    l.dispose();
  });

  it("the field, the range, the breaks and the hidden categories change the classes — the colours do not", () => {
    const base = { kind: "scale" as const, from: 0.1, to: 10, log: true };
    const key = classKey("AU", base);
    expect(classKey("AU", { ...base })).toBe(key);
    expect(classKey("CU", base)).not.toBe(key);
    expect(classKey("AU", { ...base, to: 11 })).not.toBe(key);
    expect(classKey("AU", { ...base, log: false })).not.toBe(key);
    expect(classKey("AU", { kind: "classes", breaks: [1, 2] })).not.toBe(classKey("AU", { kind: "classes", breaks: [1, 3] }));
    expect(classKey("R", { kind: "categories", hidden: [2] })).not.toBe(classKey("R", { kind: "categories", hidden: [] }));
  });
});

describe("the edges and the corners are the shader's", () => {
  it("the shader takes the faces from the one table of axes: u = axis + 1, v = axis + 2", () => {
    expect(VERTEX).toContain("axis < 0.5 ? vec3(0.0, 1.0, 0.0) : (axis < 1.5 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0))"); // eu of X, Y, Z
    expect(VERTEX).toContain("axis < 0.5 ? vec3(0.0, 0.0, 1.0) : (axis < 1.5 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0))"); // ev
    expect(VERTEX).toContain("attribute vec3 iC");
    expect(VERTEX).toContain("#include <clipping_planes_vertex>");
    expect(FRAGMENT).toContain("#include <clipping_planes_fragment>");
    expect(FRAGMENT).toContain("fwidth(vLocal)");
  });

  it("the corners the shader builds, turned by the matrix of the layer, are the corners of the core (float64, 5·10⁻⁴ m)", () => {
    for (const c of FIX.cases) {
      const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
      const e = l.object.matrix.elements; // column-major, float64
      let worst = 0;
      for (let i = 0; i < l.data.faces; i++) {
        faceCorners(l.data, i).forEach((p, j) => {
          const x = e[0] * p[0] + e[4] * p[1] + e[8] * p[2] + e[12] + l.origin.x;
          const y = e[1] * p[0] + e[5] * p[1] + e[9] * p[2] + e[13] + l.origin.y;
          const z = e[2] * p[0] + e[6] * p[1] + e[10] * p[2] + e[14] + l.origin.z;
          const want = c.corners_project[i][j];
          worst = Math.max(worst, Math.abs(x - want[0]), Math.abs(y - want[1]), Math.abs(z - want[2]));
        });
      }
      expect(worst, c.name).toBeLessThan(5e-4);
      l.dispose();
    }
  });

  it("edges are off on a shell and on in «Все блоки»; the width, the opacity and the colour are uniforms", () => {
    const shell = layerOf("scale_turned");
    const all = layerOf("all_blocks");
    expect(shell.look.edges).toBe(false);
    expect(all.look.edges).toBe(true);
    const u = (l: BlocksLayer) => (l.mesh.material as THREE.ShaderMaterial).uniforms;
    expect([u(shell).uEdges.value, u(all).uEdges.value]).toEqual([0, 1]);
    shell.setLook({ edges: true, edgeWidth: 2.5, edgeOpacity: 0.9, edgeColor: "#F46A1A" });
    expect([u(shell).uEdges.value, u(shell).uEdgeWidth.value, u(shell).uEdgeOpacity.value]).toEqual([1, 2.5, 0.9]);
    expect(`#${(u(shell).uEdgeColor.value as THREE.Color).getHexString()}`).toBe("#f46a1a");
    shell.setLook({ edgeWidth: 0 });
    expect(u(shell).uEdgeWidth.value).toBe(0.5);
    // the geometry stays
    expect((shell.mesh.geometry as THREE.InstancedBufferGeometry).instanceCount).toBe(shell.data.faces);
    shell.dispose();
    all.dispose();
  });

  it("opacity below 1 blends without writing depth; 1 is opaque again", () => {
    const l = layerOf("all_blocks");
    const m = l.mesh.material as THREE.ShaderMaterial;
    expect([m.transparent, m.depthWrite, m.uniforms.uOpacity.value]).toEqual([false, true, 1]);
    l.setLook({ opacity: 0.4 });
    expect([m.transparent, m.depthWrite, m.uniforms.uOpacity.value]).toEqual([true, false, 0.4]);
    l.setLook({ opacity: 1 });
    expect([m.transparent, m.depthWrite]).toEqual([false, true]);
    expect(l.shown().look.opacity).toBe(1);
    l.dispose();
  });

  it("«все блоки / оболочка» is the mode of the answer; the layer shows what it was given", () => {
    const shell = layerOf("scale_turned").shown();
    const all = layerOf("all_blocks").shown();
    expect([shell.mode, all.mode, all.faces, all.facesRaw]).toEqual(["shell", "all", 28, 156]);
    const lod = layerOf("lod").shown();
    expect([lod.simplified, lod.lod, lod.facesRaw]).toEqual([true, 2, 1536]);
  });
});

describe("float32 from a whole origin at UTM coordinates", () => {
  it("the layer's origin is the centre of the model, whole; the matrix holds metres, not 7·10⁶", () => {
    for (const c of FIX.cases) {
      const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
      const b = l.bbox!;
      expect([l.origin.x, l.origin.y, l.origin.z]).toEqual([Math.round((b[0] + b[3]) / 2), Math.round((b[1] + b[4]) / 2), Math.round((b[2] + b[5]) / 2)]);
      const e = l.object.matrix.elements;
      expect(Math.hypot(e[12], e[13], e[14]), c.name).toBeLessThan(3);
      // the box counts every corner, and is tight
      const all = c.corners_project.flat();
      for (let k = 0; k < 3; k++) {
        expect(Math.min(...all.map((p) => p[k]))).toBeCloseTo(b[k], 9);
        expect(Math.max(...all.map((p) => p[k]))).toBeCloseTo(b[k + 3], 9);
      }
      l.dispose();
    }
  });

  it("millimetres where a float32 of the absolute coordinate is off by decimetres", () => {
    const c = byName("scale_turned");
    const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
    expect(c.grid.corner[1]).toBeGreaterThan(7e6);
    let naive = 0;
    for (const quad of c.corners_project) for (const p of quad) for (const x of p) naive = Math.max(naive, Math.abs(Math.fround(x) - x));
    expect(naive).toBeGreaterThan(0.03); // UTM Y in float32: the step is 0.5 m
    let kept = 0;
    for (let i = 0; i < l.data.faces; i++) {
      for (const p of faceCorners(l.data, i)) for (const x of p) kept = Math.max(kept, Math.abs(Math.fround(x) - x)); // the float32 the GPU holds
    }
    expect(kept).toBeLessThan(1e-4);
    l.dispose();
  });

  it("an empty answer is a layer with nothing to count", () => {
    const head = { ...decoded("all_blocks").header, faces: 0 };
    const empty = decodeBlocks(encodeBfa1(head, [["c", "<f4", new Float32Array(0)], ["h", "<f4", new Float32Array(0)], ["a", "<u1", new Uint8Array(0)], ["k", "<u2", new Uint16Array(0)]]));
    const l = new BlocksLayer(empty, ctx());
    expect(l.bbox).toBeNull();
    expect(l.pick(query([0, 0, 10], [0, 0, -1], [0, 0, 0]))).toBeNull();
    l.dispose();
  });
});

/** A click ray over a layer placed in a scene whose origin is `sceneOrigin`; `vexag` stretches Z of the rendered space. */
function query(o: Vec3, d: Vec3, off: Vec3, vexag = 1, inWindow: PickQuery["inWindow"] = () => true): PickQuery {
  return {
    vp: new Float64Array(16),
    vexag,
    px: 0,
    py: 0,
    width: 800,
    height: 600,
    inWindow,
    ray: { origin: o, dir: d },
    offset: off,
  };
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol: number) => [0, 1, 2].forEach((i) => expect(Math.abs(a[i] - b[i])).toBeLessThan(tol));

describe("the index of the faces: planes → buckets", () => {
  it("every ray of the core fixture meets the face the plain loop found (the same t, the same point); a miss is null", () => {
    for (const c of FIX.cases) {
      const d = decodeBlocks(bytesOf(c));
      const index = PickIndex.build(d);
      for (const r of c.rays) {
        const o = r.local.o.map((x, i) => x - c.origin[i]) as Vec3;
        const got = index.ray(o, r.local.d);
        if (!r.hit) {
          expect(got, `${c.name} ${r.name}`).toBeNull();
          continue;
        }
        expect(got, `${c.name} ${r.name}`).not.toBeNull();
        expect(got!.t).toBeCloseTo(r.hit.t, 3);
        expect(got!.face).toBe(r.hit.face);
        near(got!.point.map((x, i) => x + c.origin[i]), r.hit.point, 2e-3);
        const a = d.a[got!.face];
        expect(got!.normal[a >> 1]).toBe(a & 1 ? 1 : -1);
      }
    }
  });

  it("Т8.7-9: a click on any of the six big rectangles finds it (a rectangle of 100 × 100 m in one bucket query)", () => {
    const c = byName("cube_100");
    const d = decodeBlocks(bytesOf(c));
    const index = PickIndex.build(d);
    expect(index.stats().runs).toBe(6);
    expect(index.stats().bucketed).toBe(0);
    for (let a = 0; a < 6; a++) {
      const axis = a >> 1;
      const o: Vec3 = [10.3, 11.9, -3.2]; // the frame of `c`: the model is ±50 about the origin
      const dir: Vec3 = [0, 0, 0];
      dir[axis] = a & 1 ? -1 : 1; // from outside toward the side
      o[axis] = a & 1 ? 500 : -500;
      const hit = index.ray(o, dir)!;
      expect(d.a[hit.face]).toBe(a);
      expect(hit.t).toBeCloseTo(450, 6);
    }
  });

  /** random rectangles in a few planes: many small, some huge — the index against a loop over all of them */
  function scatter(n: number, seed: number): BlocksData {
    let s = seed;
    const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    const c = new Float32Array(3 * n);
    const h = new Float32Array(2 * n);
    const a = new Uint8Array(n);
    const k = new Uint16Array(n);
    const planes = [-20, -5, 0, 7.5, 31, 40];
    for (let i = 0; i < n; i++) {
      const side = Math.floor(rnd() * 6);
      a[i] = side;
      const axis = side >> 1;
      const [u, v] = [(axis + 1) % 3, (axis + 2) % 3];
      c[3 * i + axis] = planes[Math.floor(rnd() * planes.length)];
      c[3 * i + u] = rnd() * 100 - 50;
      c[3 * i + v] = rnd() * 100 - 50;
      const big = rnd() < 0.02;
      h[2 * i] = big ? 10 + rnd() * 30 : 0.2 + rnd() * 1.2;
      h[2 * i + 1] = big ? 10 + rnd() * 30 : 0.2 + rnd() * 1.2;
      k[i] = i % 7;
    }
    // the answer comes sorted by side and plane: runs of one plane
    const order = Array.from({ length: n }, (_, i) => i).sort((x, y) => a[x] - a[y] || c[3 * x + (a[x] >> 1)] - c[3 * y + (a[y] >> 1)]);
    const out = { c: new Float32Array(3 * n), h: new Float32Array(2 * n), a: new Uint8Array(n), k: new Uint16Array(n), faces: n };
    order.forEach((f, i) => {
      out.c.set(c.subarray(3 * f, 3 * f + 3), 3 * i);
      out.h.set(h.subarray(2 * f, 2 * f + 2), 2 * i);
      out.a[i] = a[f];
      out.k[i] = k[f];
    });
    return out as unknown as BlocksData;
  }
  const loop = (d: BlocksData, o: Vec3, dir: Vec3, accept?: (p: Vec3) => boolean): { t: number; face: number } | null => {
    let best: { t: number; face: number } | null = null;
    for (let f = 0; f < d.faces; f++) {
      const axis = d.a[f] >> 1;
      const [u, v] = [(axis + 1) % 3, (axis + 2) % 3];
      if (Math.abs(dir[axis]) < 1e-15) continue;
      const t = (d.c[3 * f + axis] - o[axis]) / dir[axis];
      if (t <= 0 || (best && t >= best.t)) continue;
      const p: Vec3 = [0, 0, 0];
      p[axis] = d.c[3 * f + axis];
      p[u] = o[u] + t * dir[u];
      p[v] = o[v] + t * dir[v];
      if (Math.abs(p[u] - d.c[3 * f + u]) <= d.h[2 * f] + 1e-6 && Math.abs(p[v] - d.c[3 * f + v]) <= d.h[2 * f + 1] + 1e-6 && (!accept || accept(p))) best = { t, face: f };
    }
    return best;
  };

  it("against a loop over 6000 random rectangles, small and huge, in buckets: the same face on 400 rays", () => {
    const d = scatter(6000, 7);
    const index = PickIndex.build(d);
    const st = index.stats();
    expect(st.bucketed).toBeGreaterThan(0);
    expect(st.bigFaces).toBeGreaterThan(0);
    let s = 99;
    const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    let hits = 0;
    for (let r = 0; r < 400; r++) {
      const dir: Vec3 = [rnd() - 0.5, rnd() - 0.5, rnd() - 0.5];
      const o: Vec3 = [rnd() * 160 - 80, rnd() * 160 - 80, rnd() * 160 - 80];
      const want = loop(d, o, dir);
      const got = index.ray(o, dir);
      if (!want) expect(got).toBeNull();
      else {
        hits++;
        expect(got, `ray ${r}`).not.toBeNull();
        expect(got!.t).toBeCloseTo(want.t, 9);
      }
    }
    expect(hits).toBeGreaterThan(150);
  });

  it("a refused hit is passed over: the next face on the ray, or none (the section window)", () => {
    const d = scatter(2000, 3);
    const index = PickIndex.build(d);
    const o: Vec3 = [0.3, 0.7, 90];
    const dir: Vec3 = [0.01, 0.02, -1];
    const first = index.ray(o, dir)!;
    expect(first).not.toBeNull();
    const second = index.ray(o, dir, (p) => p[2] !== first.point[2]);
    const want = loop(d, o, dir, (p) => p[2] !== first.point[2]);
    expect(second?.t ?? null).toBe(want?.t ?? null);
    if (second) expect(second.t).toBeGreaterThan(first.t);
    expect(index.ray(o, dir, () => false)).toBeNull();
  });
});

describe("a click on the layer: the face under the cursor, the point and the normal", () => {
  /** the scene origin S (whole), the layer placed at origin − S; the rendered ray of a project ray (Z stretched) */
  const SCENE: Vec3 = [512_000, 7_100_000, 200];
  function clickAt(l: BlocksLayer, r: Ray, vexag: number, inWindow?: PickQuery["inWindow"]) {
    const off: Vec3 = [l.origin.x - SCENE[0], l.origin.y - SCENE[1], l.origin.z - SCENE[2]];
    const o: Vec3 = [r.project.o[0] - SCENE[0], r.project.o[1] - SCENE[1], (r.project.o[2] - SCENE[2]) * vexag];
    const d: Vec3 = [r.project.d[0], r.project.d[1], r.project.d[2] * vexag];
    return l.pick(query(o, d, off, vexag, inWindow));
  }

  it("the point on the face in the project (float64), the same as the plain loop, at any exaggeration of Z", () => {
    for (const name of ["scale_turned", "categories", "lod"]) {
      const c = byName(name);
      const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
      for (const vexag of [1, 3]) {
        for (const r of c.rays) {
          const got = clickAt(l, r, vexag);
          if (!r.hit) {
            expect(got).toBeNull();
            continue;
          }
          expect(got, `${name} ${r.name}`).not.toBeNull();
          const pick = got!.hit as BlocksPick;
          const want = r.project.o.map((x, i) => x + r.hit!.t * r.project.d[i]);
          near(pick.xyz, want, 2e-3);
          expect(pick.face).toBe(r.hit.face);
          expect(pick.model).toBe(12);
          expect(got!.score).toBeGreaterThan(2);
          expect(got!.score).toBeLessThan(3);
        }
      }
      l.dispose();
    }
  });

  it("the normal leaves the block (the project axes); `inside` is a hair behind the face along it, within the float32 slack", () => {
    const c = byName("scale_turned");
    const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
    for (const r of c.rays.filter((x) => x.hit)) {
      const pick = clickAt(l, r, 1)!.hit as BlocksPick;
      const toRay = r.project.d;
      expect(pick.normal[0] * toRay[0] + pick.normal[1] * toRay[1] + pick.normal[2] * toRay[2]).toBeLessThan(0); // the ray comes from the side the normal looks to... or from inside
      const back = [0, 1, 2].map((i) => pick.xyz[i] - pick.inside[i]);
      const depth = back[0] * pick.normal[0] + back[1] * pick.normal[1] + back[2] * pick.normal[2];
      expect(depth).toBeGreaterThan(0);
      expect(depth).toBeLessThan(0.01);
      expect(Math.hypot(...back)).toBeCloseTo(depth, 9); // exactly along the normal
      expect(Math.hypot(...pick.normal)).toBeCloseTo(1, 12);
    }
    expect(insideSlack(0)).toBeCloseTo(1e-6, 12);
    expect(insideSlack(-2500)).toBeGreaterThan(5e-4); // 4 ulp of float32 at 2.5 km
    l.dispose();
  });

  it("a face outside the section window is not clicked; a hidden layer answers nothing", () => {
    const c = byName("scale_turned");
    const l = new BlocksLayer(decodeBlocks(bytesOf(c)), ctx());
    const r = c.rays.find((x) => x.hit)!;
    expect(clickAt(l, r, 1, () => false)).toBeNull();
    const first = clickAt(l, r, 1)!;
    const behind = clickAt(l, r, 1, (x, y, z) => {
      const p = r.project.o.map((v, i) => v + r.hit!.t * r.project.d[i]);
      return Math.hypot(x + SCENE[0] - p[0], y + SCENE[1] - p[1], z + SCENE[2] - p[2]) > 1e-3;
    });
    if (behind) expect(behind.score).toBeGreaterThan(first.score);
    l.setVisible(false);
    expect(clickAt(l, r, 1)).toBeNull();
    l.dispose();
  });

  it("an index from a Worker is taken as it is; without one the first click builds it once", () => {
    const c = byName("categories");
    const d = decodeBlocks(bytesOf(c));
    const built = PickIndex.build(d);
    const l = new BlocksLayer(d, ctx(), { index: built });
    expect(l.pickIndex()).toBe(built);
    const own = new BlocksLayer(d, ctx());
    expect(own.pickIndex()).toBe(own.pickIndex());
    l.dispose();
    own.dispose();
  });
});

describe("the CPU of 2.5 million faces (no WebGL)", () => {
  /** six sides, 50 planes each of 91 × 91 unit squares: 2 484 300 faces sorted as the core sorts them */
  function huge(): ArrayBuffer {
    const per = 91;
    const planes = 50;
    const n = 6 * planes * per * per;
    const c = new Float32Array(3 * n);
    const h = new Float32Array(2 * n).fill(5);
    const a = new Uint8Array(n);
    const k = new Uint16Array(n);
    let i = 0;
    for (let side = 0; side < 6; side++) {
      const axis = side >> 1;
      const [u, v] = [(axis + 1) % 3, (axis + 2) % 3];
      for (let pl = 0; pl < planes; pl++)
        for (let q = 0; q < per; q++)
          for (let p = 0; p < per; p++, i++) {
            c[3 * i + axis] = pl * 10 - 250;
            c[3 * i + u] = p * 10 - 450 + 5;
            c[3 * i + v] = q * 10 - 450 + 5;
            a[i] = side;
            k[i] = (p * 7 + q * 3 + pl) % 256;
          }
    }
    const header = { kind: "blocks", format: 1, model: 1, version: 1, mode: "shell", corner: [512120.4, 7100010.6, 255.2], rotation: 30, origin: [0, 0, 0], faces: n, faces_raw: n, simplified: false, lod: 0, rows: n, passed: n, empty: 0, field: "AU", colour: { kind: "scale", from: 0, to: 1, log: false }, classes: 256, bins: null, labels: null };
    return encodeBfa1(header, [["c", "<f4", c], ["h", "<f4", h], ["a", "<u1", a], ["k", "<u2", k]]);
  }

  it("decodes, takes the extent, builds the index and answers a click; the times are for the report", () => {
    const body = huge();
    const t0 = performance.now();
    const d = decodeBlocks(body);
    const t1 = performance.now();
    const ext = localExtent(d);
    const t2 = performance.now();
    const index = PickIndex.build(d);
    const t3 = performance.now();
    const layer = new BlocksLayer(d, ctx(), { index });
    const t4 = performance.now();
    let hits = 0;
    const t5 = performance.now();
    for (let r = 0; r < 200; r++) {
      const hit = index.ray([(r % 20) * 40 - 400 + 1.3, Math.floor(r / 20) * 80 - 400 + 2.1, 900], [0.01, 0.02, -1]);
      if (hit) hits++;
    }
    const t6 = performance.now();
    const ms = (a: number, b: number) => Math.round(b - a);
    console.log(`blocks 2.5M faces: decode ${ms(t0, t1)} ms, extent ${ms(t1, t2)} ms, index ${ms(t2, t3)} ms, layer ${ms(t3, t4)} ms, 200 clicks ${ms(t5, t6)} ms; ${JSON.stringify(index.stats())}`);
    expect(d.faces).toBe(2_484_300);
    expect(Array.from(ext.subarray(0, 3))).toEqual([-450, -450, -450]);
    expect(hits).toBeGreaterThan(100);
    expect(ms(t0, t1)).toBeLessThan(2000);
    expect(ms(t2, t3)).toBeLessThan(6000);
    expect(ms(t5, t6)).toBeLessThan(2000);
    layer.dispose();
  });
});
