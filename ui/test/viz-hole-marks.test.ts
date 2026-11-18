/**
 * Marks where holes cross wireframes (ТЗ 7.15, ADR 0007 §5): BFA1 `intersections` of the core, the label
 * «глубина · объект» to the millimetre, the colour of the wireframe or of the style, and the layer of the
 * scene — diamonds in float32 from a whole-metre origin, labels with the priority of crossings.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { VizError } from "../src/viz/error";
import { hexToLinear } from "../src/viz/holes/colors";
import { EVENT_CROSS, EVENT_ENTER, EVENT_LEAVE, HoleMarksView, MARK_DEFAULT_COLOR, markColors, marksQuery, markText, MAX_MARK_LABELS, readHoleMarks, type HoleMarksContext } from "../src/viz/holes/marks";
import { DEFAULT_TRACE_STYLE } from "../src/viz/holes/traceStyle";
import { LABEL_PRIORITY } from "../src/viz/labels/declutter";
import type { LabelCache } from "../src/viz/labels/sprites";

const X = 512_345.678_901;
const Y = 6_234_567.123_456;
const Z = 412.345_678;
const OBJECTS = [{ kind: "wireframe", id: 3, name: "Куб", color: "#ff0000" }, { kind: "wireframe", id: 5, name: "Разлом", color: null }];

function answer(patch: Record<string, unknown> = {}, obj = [0, 1, 0]): ArrayBuffer {
  const xyz = [X + 50.3, Y + 49.9, Z + 100, X + 50.3, Y + 49.9, Z + 65.15, X + 50.3, Y + 49.9, Z];
  return build({ kind: "intersections", format: 1, set: 9, version: 4, method: "minimum_curvature", objects: OBJECTS, n: 3, ...patch }, [
    ["hole", "<u4", Uint32Array.from([21, 21, 21])], ["depth", "<f8", Float64Array.from([30, 64.850_000_000_02, 130.000_4])], ["xyz", "<f8", Float64Array.from(xyz)],
    ["obj", "<u4", Uint32Array.from(obj)], ["event", "<u1", Uint8Array.from([EVENT_ENTER, EVENT_CROSS, EVENT_LEAVE])],
  ]);
}

/** The label cache and the marker textures without a canvas. */
function context(): HoleMarksContext & { texts: string[]; shapes: string[] } {
  const texts: string[] = [];
  const shapes: string[] = [];
  const labels = { material: (text: string) => (texts.push(text), { material: new THREE.SpriteMaterial(), wpx: 80, hpx: 14 }) } as unknown as LabelCache;
  const diamond = new THREE.Texture();
  return { clip: [new THREE.Plane(new THREE.Vector3(1, 0, 0), 5)], textures: { get: (shape) => (shapes.push(shape), diamond) }, labels, pixelRatio: 2, texts, shapes };
}

describe("the crossings of holes with wireframes", () => {
  it("the request lists the wireframes once, by id; an empty list asks for none", () => {
    expect(marksQuery([5, 3, 5])).toBe("wireframes=3,5");
    expect(marksQuery([])).toBe("wireframes=");
  });

  it("BFA1 `intersections`: objects in the header, a mark per crossing with float64 depth and point", () => {
    const m = readHoleMarks(answer());
    expect(m).toMatchObject({ setId: 9, version: 4, n: 3 });
    expect(m.objects).toEqual(OBJECTS);
    expect(m.depth).toBeInstanceOf(Float64Array);
    expect(m.xyz[0]).toBe(X + 50.3); // bit for bit
    expect(Array.from(m.event)).toEqual([1, 0, 2]);
    expect(Array.from(m.hole)).toEqual([21, 21, 21]);
    for (const bad of [answer({ kind: "paths" }), answer({ n: 4 }), answer({ objects: null }), answer({}, [0, 2, 0]), answer({ objects: [{ name: "без номера" }] })]) {
      expect(() => readHoleMarks(bad)).toThrow(VizError);
    }
    // a colour that is not #rrggbb is no colour
    expect(readHoleMarks(answer({ objects: [{ kind: "wireframe", id: 3, name: "Куб", color: "red" }, OBJECTS[1]] })).objects[0].color).toBeNull();
  });

  it("the label shows the depth to the millimetre and the object", () => {
    expect(markText(64.850_000_000_02, "Разлом")).toBe("64,850 м · Разлом");
    expect(markText(130.000_4, "Куб")).toBe("130,000 м · Куб");
    expect(markText(30.0015, "Куб")).toBe("30,002 м · Куб"); // 1 mm is told from 2 mm
    expect(markText(30.0006, "")).toBe("30,001 м");
  });

  it("the colour of the wireframe as it is now, the default for one without a colour, or the colour of the style", () => {
    const m = readHoleMarks(answer());
    expect(markColors(m, { wireframeColor: "object" })).toEqual(["#ff0000", MARK_DEFAULT_COLOR, "#ff0000"]);
    // the list of the project knows the colour the wireframe got after the answer; null — it lost its colour
    expect(markColors(m, { wireframeColor: "object" }, (id) => (id === 3 ? "#00ff00" : undefined))).toEqual(["#00ff00", MARK_DEFAULT_COLOR, "#00ff00"]);
    expect(markColors(m, { wireframeColor: "object" }, () => null)).toEqual([MARK_DEFAULT_COLOR, MARK_DEFAULT_COLOR, MARK_DEFAULT_COLOR]);
    expect(markColors(m, { wireframeColor: "#ff4d6d" })).toEqual(["#ff4d6d", "#ff4d6d", "#ff4d6d"]);
  });
});

describe("the layer of marks", () => {
  it("diamonds in float32 from a whole-metre origin, clipped by the window, labelled first of all", () => {
    const ctx = context();
    const m = readHoleMarks(answer());
    const view = new HoleMarksView(m, ctx, DEFAULT_TRACE_STYLE);
    expect(view.setId).toBe(9);
    expect(view.bbox).toBeNull();
    expect([view.origin.x, view.origin.y, view.origin.z].every(Number.isInteger)).toBe(true);
    const points = view.object.children[0] as THREE.Points;
    const material = points.material as THREE.PointsMaterial;
    expect(ctx.shapes).toEqual(["diamond"]);
    expect(material.size).toBe(DEFAULT_TRACE_STYLE.markerSize * 2); // device pixels
    expect(material.clippingPlanes).toBe(ctx.clip);
    expect(material.vertexColors).toBe(true);
    const pos = points.geometry.getAttribute("position").array as Float32Array;
    expect(pos).toBeInstanceOf(Float32Array);
    const o = [view.origin.x, view.origin.y, view.origin.z];
    let worst = 0;
    for (let i = 0; i < pos.length; i++) worst = Math.max(worst, Math.abs(pos[i] + o[i % 3] - m.xyz[i]));
    expect(worst).toBeLessThan(1e-5); // offsets below 64 m: a float32 step of 4e-6 m
    const colors = Array.from(points.geometry.getAttribute("color").array as Float32Array);
    expect(colors.slice(0, 3)).toEqual(Array.from(Float32Array.from(hexToLinear("#ff0000"))));
    expect(colors.slice(3, 6)).toEqual(Array.from(Float32Array.from(hexToLinear(MARK_DEFAULT_COLOR))));
    // «глубина · объект» for every mark, with the priority of crossings
    expect(ctx.texts).toEqual(["30,000 м · Куб", "64,850 м · Разлом", "130,000 м · Куб"]);
    const labels = view.labels();
    expect(labels).toHaveLength(3);
    expect(labels.every((l) => l.priority === LABEL_PRIORITY.crossing && l.clipped)).toBe(true);
    expect(labels[1].sprite.position.toArray()).toEqual([pos[3], pos[4], pos[5]]);
    expect(view.stats()).toEqual({ marks: 3, labels: 3 });

    // the style changes at once: no labels, a larger marker, one colour for every wireframe
    view.apply({ ...DEFAULT_TRACE_STYLE, markerLabels: false, markerSize: 14, wireframeColor: "#ff4d6d" });
    expect(view.labels()).toHaveLength(0);
    expect(material.size).toBe(28);
    expect(Array.from(points.geometry.getAttribute("color").array as Float32Array).slice(3, 6)).toEqual(Array.from(Float32Array.from(hexToLinear("#ff4d6d"))));
    view.dispose();
    expect(view.object.children).toHaveLength(0);
  });

  it("many marks: every one is drawn, the labels stop at the limit", () => {
    const n = MAX_MARK_LABELS + 50;
    const xyz = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) xyz.set([X + i, Y, Z - i * 0.5], i * 3);
    const buf = build({ kind: "intersections", set: 9, version: 1, objects: [OBJECTS[0]], n }, [
      ["hole", "<u4", new Uint32Array(n)], ["depth", "<f8", Float64Array.from({ length: n }, (_, i) => i * 0.5)], ["xyz", "<f8", xyz], ["obj", "<u4", new Uint32Array(n)], ["event", "<u1", new Uint8Array(n)],
    ]);
    const view = new HoleMarksView(readHoleMarks(buf), context(), DEFAULT_TRACE_STYLE);
    expect(view.stats()).toEqual({ marks: n, labels: MAX_MARK_LABELS });
    expect((view.object.children[0] as THREE.Points).geometry.getAttribute("position").count).toBe(n);
  });
});
