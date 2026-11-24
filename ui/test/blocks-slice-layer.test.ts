// The slice of a block model in the 3D (ТЗ 8.7.6, 8.7.7; ADR 0023 §11, decision 19; Э17 17.32): the decoder of BFA1
// `blocks_slice` and the layer-texture of the raster, on the REAL answers of the core (`core/tests/data/blocks/
// viz_answers.json`, `slice_z`, `slice_oblique`, `slice_categories`, made through `POST /viz/blocks/{id}/slice` by
// `core/tests/test_blocks_viz.py`): the header and the arrays, the mask `m` (no block / value / block without a value),
// an empty slice, a damaged answer in Russian; the classes of the scale, the breaks and the categories as the core
// counts them; the colours that never turn a missing value into 0; the corners in float64 and float32 from the whole
// origin at UTM; a new palette baked without a new geometry; the plane of a section as a backdrop; the CPU of a raster
// of 1528 × 1528.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { NO_CLASS } from "../src/viz/blocks/decode";
import { legendBytes, NONE_COLOR, NO_VALUE_COLOR, parseColor } from "../src/viz/blocks/legend";
import { decodeSlice, M_EMPTY, M_NONE, M_VALUE, pixelCentre, sliceCorners, type SliceData } from "../src/viz/blocks/sliceDecode";
import { resolveSliceColour, sliceClasses, SliceLayer, sliceRgba, type SliceColour } from "../src/viz/blocks/sliceLayer";
import { VizError } from "../src/viz/error";

interface Case {
  name: string;
  request: Record<string, unknown>;
  header: Record<string, unknown>;
  bfa1: string;
}
const FIX: { cases: Case[] } = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/viz_answers.json"), "utf-8"));
const bytesOf = (name: string): ArrayBuffer => {
  const b = Buffer.from(FIX.cases.find((c) => c.name === name)!.bfa1, "base64");
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
const real = (name: string): SliceData => decodeSlice(bytesOf(name));
const hist = (m: Uint8Array): number[] => [0, 1, 2].map((v) => m.reduce((n, x) => n + (x === v ? 1 : 0), 0));
const ctx = () => ({ clip: [] as THREE.Plane[] });

/** A raster made by hand (an answer of the core in the same bytes): the frame at UTM, a pixel of 5 m. */
function make(over: Record<string, unknown>, value: number[], m: number[]): ArrayBuffer {
  const width = (over.width as number | undefined) ?? value.length;
  const header = {
    kind: "blocks_slice", format: 1, model: 1, version: 1,
    plane: { origin: [512100, 7100000, 260], normal: [0, 0, 1] },
    raster: "cells", width, height: value.length / width,
    frame: { origin: [512000.5, 7100000.25, 260], u: [1, 0, 0], v: [0, 1, 0], du: 5, dv: 5 },
    passed: m.filter((x) => x === 1).length, field: "AU", field_kind: "number", labels: null, warnings: [], ...over,
  }; // prettier-ignore
  return encodeBfa1(header, [["value", "<f4", Float32Array.from(value)], ["m", "<u1", Uint8Array.from(m)]]);
}

describe("the decoder of BFA1 blocks_slice (real answers)", () => {
  it("reads the horizontal slice of the turned model: sub-cells, the frame turned by 30°, the mask, no value is not 0", () => {
    const d = real("slice_z");
    const h = d.header;
    expect(h).toMatchObject({ kind: "blocks_slice", format: 1, model: 1, version: 1, raster: "subcells", width: 12, height: 10, passed: 33, field: "AU", field_kind: "number", labels: null, warnings: [] });
    expect([d.width, d.height, d.empty]).toEqual([12, 10, false]);
    expect(h.plane.normal).toEqual([0, 0, 1]);
    expect(h.frame).toMatchObject({ origin: [512120.4, 7100010.6, 262.7], du: 5, dv: 5 });
    expect(h.frame.u[0]).toBeCloseTo(Math.cos(Math.PI / 6), 12);
    expect(h.frame.v[0]).toBeCloseTo(-Math.sin(Math.PI / 6), 12);
    expect(d.value).toBeInstanceOf(Float32Array);
    expect(d.m).toBeInstanceOf(Uint8Array);
    expect(hist(d.m)).toEqual([32, 77, 11]);
    expect([d.withValue, d.withoutValue]).toEqual([77, 11]);
    // a pixel with a value has a finite value; none is 0 only because the grade is 0 (the smallest one in the answer is 0.087)
    for (let i = 0; i < d.m.length; i++) if (d.m[i] === M_VALUE) expect(Number.isFinite(d.value[i])).toBe(true);
    expect(Math.min(...Array.from(d.value).filter((_, i) => d.m[i] === M_VALUE))).toBeCloseTo(0.0870, 3);
  });

  it("reads the oblique slice (samples) and the categories (cells, the hidden code is a transparent pixel)", () => {
    const o = real("slice_oblique");
    expect(o.header).toMatchObject({ raster: "sampled", width: 23, height: 10, passed: 48 });
    expect(Math.hypot(...o.header.plane.normal)).toBeCloseTo(1, 12);
    expect(hist(o.m)).toEqual([50, 166, 14]);
    const c = real("slice_categories");
    expect(c.header).toMatchObject({ raster: "cells", width: 8, height: 6, field: "ROCK", field_kind: "category", labels: ["руда", "порода", "окисленная", "дайка", "вода"] });
    expect(hist(c.m)).toEqual([17, 31, 0]);
    expect(c.maxCode).toBe(3);
    const codes = new Set(Array.from(c.value).filter((_, i) => c.m[i] === M_VALUE));
    expect([...codes].sort()).toEqual([0, 2, 3]); // «порода» (1) was hidden in the request
  });

  it("the arrays are views of the answer: nothing is copied", () => {
    const buf = bytesOf("slice_z");
    const d = decodeSlice(buf);
    expect(d.value.buffer).toBe(buf);
    expect(d.m.buffer).toBe(buf);
  });

  it("a plane that misses the model is an empty slice: 0 × 0, nothing to draw", () => {
    const d = decodeSlice(make({ width: 0, height: 0, raster: "sampled", passed: 0 }, [], []));
    expect(d).toMatchObject({ width: 0, height: 0, empty: true, withValue: 0, withoutValue: 0, maxCode: -1 });
    expect(d.value.length).toBe(0);
    expect(sliceCorners(d.header)).toHaveLength(4);
    const layer = new SliceLayer(d, ctx(), { colour: resolveSliceColour(d, { kind: "scale", from: null, to: null, log: false, breaks: [] }) });
    expect(layer.bbox).toBeNull();
    expect(layer.mesh).toBeNull();
    layer.dispose();
  });

  it("a damaged answer is a Russian error with a hint: kind, format, mask, sizes, frame, arrays", () => {
    const bad = (b: ArrayBuffer, code: string) => {
      try {
        decodeSlice(b);
        throw new Error("не отказал");
      } catch (e) {
        expect(e).toBeInstanceOf(VizError);
        expect((e as VizError).code).toBe(code);
        expect((e as VizError).message).toMatch(/[А-Яа-я]/);
        expect((e as VizError).hint).toMatch(/[А-Яа-я]/);
      }
    };
    bad(make({ kind: "blocks" }, [1, 2], [1, 1]), "slice_kind");
    bad(make({ format: 2 }, [1, 2], [1, 1]), "slice_format");
    bad(make({}, [1, 2], [1, 3]), "slice_mask");
    bad(make({ height: 0 }, [1, 2], [1, 1]), "slice_header");
    bad(make({ raster: "tiles" }, [1, 2], [1, 1]), "slice_header");
    bad(make({ field_kind: "text" }, [1, 2], [1, 1]), "slice_header");
    bad(make({ frame: { origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], du: 0, dv: 5 } }, [1, 2], [1, 1]), "slice_header");
    bad(make({ frame: { origin: [0, 0, 0], u: [2, 0, 0], v: [0, 1, 0], du: 5, dv: 5 } }, [1, 2], [1, 1]), "slice_header");
    bad(make({ plane: { origin: [0, 0], normal: [0, 0, 1] } }, [1, 2], [1, 1]), "slice_header");
    bad(encodeBfa1({ kind: "blocks_slice", format: 1 }, []), "slice_header");
    bad(make({ width: 2, height: 1 }, [1, 2, 3, 4], [1, 1, 1, 1]), "bfa1_count");
  });

  it("the corners are float64 of the frame; the centre of a pixel is where the core says", () => {
    const d = real("slice_z");
    const [c0, c1, c2, c3] = sliceCorners(d.header);
    expect(c0).toEqual(d.header.frame.origin);
    const { u, v } = d.header.frame;
    expect(c1[0] - c0[0]).toBeCloseTo(12 * 5 * u[0], 9);
    expect(c3[1] - c0[1]).toBeCloseTo(10 * 5 * v[1], 9);
    expect(c2[2]).toBeCloseTo(c0[2], 12);
    const p = pixelCentre(d.header, 0, 0);
    expect(p[0]).toBeCloseTo(c0[0] + 2.5 * u[0] + 2.5 * v[0], 9);
  });
});

describe("the colours of the raster (the classes are the core's: shell_merge.scale_class / break_class)", () => {
  const style = (kind: SliceColour["kind"], extra: Partial<{ from: number | null; to: number | null; log: boolean; breaks: number[] }> = {}) => ({ kind, from: null, to: null, log: false, breaks: [] as number[], ...extra });

  it("the kind follows the field of the answer; the range is the style's, else the shell's, else of the raster", () => {
    const z = real("slice_z");
    const auto = resolveSliceColour(z, style("scale"));
    expect(auto).toMatchObject({ kind: "scale", log: false });
    expect(auto.from).toBeCloseTo(0.0870, 3);
    expect(auto.to).toBeCloseTo(4.0526, 3);
    expect(resolveSliceColour(z, style("scale"), { from: 0.1, to: 10 })).toMatchObject({ from: 0.1, to: 10 });
    expect(resolveSliceColour(z, style("scale", { from: 1 }), { from: 0.1, to: 10 })).toMatchObject({ from: 1, to: 10 }); // the style keeps what it says
    expect(resolveSliceColour(z, style("scale", { from: 0.1, to: 10, log: true }))).toMatchObject({ from: 0.1, to: 10, log: true });
    expect(resolveSliceColour(z, style("scale", { from: -1, to: 10, log: true })).from).toBeCloseTo(0.0870, 3); // log: the smallest positive
    expect(resolveSliceColour(z, style("none")).kind).toBe("scale"); // the field came with the answer: it is drawn by a scale
    expect(resolveSliceColour(z, style("classes", { breaks: [0.5, 1, 2] }))).toMatchObject({ kind: "classes", breaks: [0.5, 1, 2] });
    expect(resolveSliceColour(z, style("classes"))).toMatchObject({ kind: "scale" }); // no breaks: a scale
    expect(resolveSliceColour(real("slice_categories"), style("scale"))).toMatchObject({ kind: "categories" });
    const flat = decodeSlice(make({}, [3, 3, 3], [1, 1, 1]));
    expect(resolveSliceColour(flat, style("scale"))).toMatchObject({ from: 3, to: 4 }); // a constant field: one bucket
    const bare = decodeSlice(make({ field: null, field_kind: null }, [NaN, NaN], [1, 1]));
    expect(resolveSliceColour(bare, style("scale")).kind).toBe("none");
  });

  it("the buckets of the scale, the log, the breaks and the codes are those of the core", () => {
    const d = decodeSlice(make({}, [0.1, 0.5, 5, 10, 100, -3, NaN, 7], [1, 1, 1, 1, 1, 1, 1, 2]));
    const lin = sliceClasses(d, { kind: "scale", from: 0, to: 10, log: false, breaks: [] });
    expect(Array.from(lin)).toEqual([2, 12, 128, 255, 255, 0, NO_CLASS, NO_CLASS]); // floor(t·256), clamped; NaN and m = 2 — no class
    const log = sliceClasses(d, { kind: "scale", from: 0.1, to: 10, log: true, breaks: [] });
    expect(Array.from(log)).toEqual([0, 89, 217, 255, 255, 0, NO_CLASS, NO_CLASS]); // v ≤ 0 → 0
    const brk = sliceClasses(d, { kind: "classes", from: 0, to: 1, log: false, breaks: [0.5, 5] });
    expect(Array.from(brk)).toEqual([0, 1, 2, 2, 2, 0, NO_CLASS, NO_CLASS]); // the number of breaks ≤ the value
    const cat = decodeSlice(make({ field: "ROCK", field_kind: "category", labels: ["а", "б", "в"] }, [2, 0, 1, 1], [1, 1, 1, 0]));
    expect(Array.from(sliceClasses(cat, { kind: "categories", from: 0, to: 1, log: false, breaks: [] }))).toEqual([2, 0, 1, 0]); // m = 0 — not drawn, class 0 is nothing
  });

  it("RGBA: m = 0 is transparent, a block without a value is the grey «нет значения» — never the colour of 0", () => {
    const d = decodeSlice(make({}, [1, 0, NaN, 9], [1, 1, 2, 0]));
    const c: SliceColour = { kind: "scale", from: 0, to: 10, log: false, breaks: [] };
    const legend = legendBytes("scale", 256, 255);
    const out = sliceRgba(d, sliceClasses(d, c), "scale", legend);
    const px = (i: number) => Array.from(out.subarray(4 * i, 4 * i + 4));
    const l = (k: number) => [legend.data[4 * k], legend.data[4 * k + 1], legend.data[4 * k + 2], 255];
    expect(px(0)).toEqual(l(25)); // 1 of 10 → bucket 25
    expect(px(1)).toEqual(l(0)); // the grade 0 is a value: the first colour, drawn
    expect(px(2)).toEqual([...parseColor(NO_VALUE_COLOR), 255]); // m = 2
    expect(px(2)).not.toEqual(l(0));
    expect(px(3)).toEqual([0, 0, 0, 0]); // m = 0
    // no field at all: one colour, the Cobalt of the brand; a block without value is still grey
    const bare = decodeSlice(make({ field: null, field_kind: null }, [NaN, NaN], [1, 2]));
    const o = sliceRgba(bare, sliceClasses(bare, { kind: "none", from: 0, to: 1, log: false, breaks: [] }), "none", legendBytes("none", 0, -1));
    expect(Array.from(o.subarray(0, 4))).toEqual([...parseColor(NONE_COLOR), 255]);
    expect(Array.from(o.subarray(4, 8))).toEqual([...parseColor(NO_VALUE_COLOR), 255]);
  });

  it("the colours of the user: the palette of the classes, the categories by code, the hidden code gone", () => {
    const d = real("slice_categories");
    const c = resolveSliceColour(d, style("categories"));
    const classes = sliceClasses(d, c);
    const mine = sliceRgba(d, classes, "categories", legendBytes("categories", 5, 3, { colors: [null, null, null, "#e07020"] }));
    const stock = sliceRgba(d, classes, "categories", legendBytes("categories", 5, 3));
    let seen = 0;
    for (let i = 0; i < d.m.length; i++) {
      if (d.m[i] === M_NONE) expect(mine[4 * i + 3]).toBe(0);
      else if (d.value[i] === 3) {
        seen++;
        expect(Array.from(mine.subarray(4 * i, 4 * i + 3))).toEqual([0xe0, 0x70, 0x20]);
        expect(Array.from(stock.subarray(4 * i, 4 * i + 3))).not.toEqual([0xe0, 0x70, 0x20]);
      }
    }
    expect(seen).toBeGreaterThan(0);
  });
});

describe("the layer-texture", () => {
  it("one rectangle on the plane: the corners from a whole origin in float32 hold a millimetre at UTM", () => {
    const d = real("slice_z");
    const layer = new SliceLayer(d, ctx(), { colour: resolveSliceColour(d, { kind: "scale", from: null, to: null, log: false, breaks: [] }) });
    const pos = layer.mesh!.geometry.getAttribute("position").array as Float32Array;
    const corners = sliceCorners(d.header);
    expect(Number.isInteger(layer.origin.x) && Number.isInteger(layer.origin.y) && Number.isInteger(layer.origin.z)).toBe(true);
    corners.forEach((c, i) => {
      for (let k = 0; k < 3; k++) expect(Math.abs(pos[3 * i + k] + [layer.origin.x, layer.origin.y, layer.origin.z][k] - c[k])).toBeLessThan(1e-3);
      for (let k = 0; k < 3; k++) expect(Math.abs(pos[3 * i + k])).toBeLessThan(100); // metres, not 10⁶
    });
    expect(layer.bbox![0]).toBeCloseTo(Math.min(...corners.map((c) => c[0])), 9);
    expect(layer.bbox![5]).toBeCloseTo(Math.max(...corners.map((c) => c[2])), 9);
    expect(layer.mesh!.geometry.getAttribute("uv").array).toEqual(Float32Array.of(0, 0, 1, 0, 1, 1, 0, 1));
    const tex = layer.texture!;
    expect([tex.image.width, tex.image.height]).toEqual([12, 10]);
    expect(tex.magFilter).toBe(THREE.NearestFilter); // a pixel is a cell: not smoothed
    expect(tex.flipY).toBe(false); // row 0 is the bottom, as the raster
    const rgba = tex.image.data as Uint8Array;
    for (let i = 0; i < d.m.length; i++) expect(rgba[4 * i + 3]).toBe(d.m[i] === M_NONE ? 0 : 255);
    expect(layer.shown()).toMatchObject({ model: 1, raster: "subcells", width: 12, height: 10, withValue: 77, withoutValue: 11, kind: "scale", visible: true });
    layer.dispose();
  });

  it("a new palette or range bakes the same texture again: no new geometry, no new texture; the plane of a section is a backdrop", () => {
    const d = real("slice_z");
    const scale = (from: number, to: number): SliceColour => ({ kind: "scale", from, to, log: false, breaks: [] });
    const layer = new SliceLayer(d, ctx(), { colour: scale(0, 5) });
    const geometry = layer.mesh!.geometry;
    const texture = layer.texture!;
    const before = Uint8Array.from(texture.image.data as Uint8Array);
    layer.setColour(scale(0, 5), { colors: Array.from({ length: 256 }, () => "#ff0000") }); // the palette
    const red = Uint8Array.from(texture.image.data as Uint8Array);
    expect(red).not.toEqual(before);
    for (let i = 0; i < d.m.length; i++) if (d.m[i] === M_VALUE) expect(Array.from(red.subarray(4 * i, 4 * i + 3))).toEqual([255, 0, 0]);
    for (let i = 0; i < d.m.length; i++) if (d.m[i] === M_EMPTY) expect(Array.from(red.subarray(4 * i, 4 * i + 3))).toEqual([...parseColor(NO_VALUE_COLOR)]); // the grey stays
    layer.setColour(scale(1, 2), {}); // the range: the classes again
    expect(layer.mesh!.geometry).toBe(geometry);
    expect(layer.texture).toBe(texture);
    expect(Uint8Array.from(texture.image.data as Uint8Array)).not.toEqual(before);

    const mat = layer.mesh!.material as THREE.MeshBasicMaterial;
    expect([mat.depthTest, mat.depthWrite, mat.transparent, layer.mesh!.renderOrder]).toEqual([true, true, false, 0]); // a plane in the 3D: an ordinary surface
    layer.setLook({ backdrop: true });
    expect([mat.depthTest, mat.depthWrite, layer.mesh!.renderOrder]).toEqual([false, false, -10]); // the holes of the section stay on top
    layer.setLook({ opacity: 0.5, backdrop: false });
    expect([mat.depthTest, mat.depthWrite, mat.transparent, mat.opacity, layer.mesh!.renderOrder]).toEqual([true, false, true, 0.5, 4]);
    expect(mat.clippingPlanes).toEqual([]);
    layer.dispose();
  });
});

describe("the CPU of a big raster (no WebGL)", () => {
  it("1528 × 1528: decode, resolve the range, classes, RGBA", () => {
    const n = 1528 * 1528;
    const value = new Float32Array(n);
    const m = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      value[i] = ((i * 2654435761) % 1000) / 100;
      m[i] = i % 7 === 0 ? 0 : i % 11 === 0 ? 2 : 1;
    }
    const buf = encodeBfa1(
      { kind: "blocks_slice", format: 1, model: 1, version: 1, plane: { origin: [0, 0, 0], normal: [0, 0, 1] }, raster: "subcells", width: 1528, height: 1528, frame: { origin: [512000, 7100000, 260], u: [1, 0, 0], v: [0, 1, 0], du: 1, dv: 1 }, passed: 1, field: "AU", field_kind: "number", labels: null, warnings: [] },
      [["value", "<f4", value], ["m", "<u1", m]],
    ); // prettier-ignore
    const t0 = performance.now();
    const d = decodeSlice(buf);
    const t1 = performance.now();
    const c = resolveSliceColour(d, { kind: "scale", from: null, to: null, log: false, breaks: [] });
    const classes = sliceClasses(d, c);
    const t2 = performance.now();
    const rgba = sliceRgba(d, classes, "scale", legendBytes("scale", 256, 255));
    const t3 = performance.now();
    expect(d.withValue + d.withoutValue).toBeGreaterThan(n * 0.7);
    expect(rgba.length).toBe(4 * n);
    // eslint-disable-next-line no-console
    console.info(`[blocks-slice] 1528×1528: decode ${(t1 - t0).toFixed(1)} мс, range+classes ${(t2 - t1).toFixed(1)} мс, RGBA ${(t3 - t2).toFixed(1)} мс`);
    expect(t1 - t0).toBeLessThan(250);
    expect(t3 - t1).toBeLessThan(1500);
  });
});
