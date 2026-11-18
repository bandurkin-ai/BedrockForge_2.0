/**
 * Section lines of wireframes under clipping (ТЗ 7.14, ADR 0013 §5–§6): the request by the section line, BFA1
 * `section` of the core, the segments for the video card — float32 from a whole-metre origin within 0.25 mm of
 * the float64 of the core on UTM-sized coordinates — and the layer of the scene with a group per wireframe.
 */
import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { LayerSet } from "../src/viz/core/layers";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import { VizError } from "../src/viz/error";
import { planeReaches, readSection, SECTION_DEFAULT_COLOR, SECTION_WIDTH_PX, SectionLinesView, sectionQuery, sectionSegments } from "../src/viz/mesh/section";
import type { SectionLine } from "../src/viz/section/math";

const X = 512_345.678_901;
const Y = 6_234_567.123_456;
const Z = 412.345_678;

/** A closed square 100 × 100 m in the plane y = Y + 42.5 and an open line of three points 1 mm apart at its end. */
function answer(patch: Record<string, unknown> = {}, offsets = [0, 4, 7], closed = [1, 0]): ArrayBuffer {
  const y = Y + 42.5;
  const xyz = [X, y, Z, X + 100, y, Z, X + 100, y, Z + 100, X, y, Z + 100, X + 2000, y, Z, X + 2000.001, y, Z, X + 2000.002, y, Z + 0.001];
  return build({ kind: "section", format: 1, wireframe: 12, point: [X - 30, y, 0], normal: [0, -1, 0], lines: closed.length, points: 7, ...patch }, [
    ["xyz", "<f8", Float64Array.from(xyz)], ["offsets", "<u4", Uint32Array.from(offsets)], ["closed", "<u1", Uint8Array.from(closed)],
  ]);
}

describe("the section of a wireframe by the plane of a section line", () => {
  it("the request carries the line in full precision; the length only when the line has one", () => {
    const line: SectionLine = { x0: 512_315.678_901, y0: 6_234_609.623_456, az: 90, length: null, towards: 25, away: 25 };
    expect(sectionQuery(line)).toBe("x0=512315.678901&y0=6234609.623456&az=90");
    expect(sectionQuery({ ...line, length: 80.5 })).toBe("x0=512315.678901&y0=6234609.623456&az=90&length=80.5");
    // float64 survives the text: a number that needs 17 digits, a tiny one written with an exponent
    for (const x0 of [512_315 + 0.1 + 0.2, 0.1 + 0.2, 1e-7, -6_234_609.623_456_001]) {
      expect(Number(new URLSearchParams(sectionQuery({ ...line, x0 })).get("x0"))).toBe(x0);
    }
  });

  it("only wireframes the plane passes through are asked", () => {
    const east: SectionLine = { x0: X - 30, y0: Y + 42.5, az: 90, length: null, towards: 25, away: 25 };
    const box = [X, Y, Z, X + 100, Y + 100, Z + 100];
    expect(planeReaches(box, east)).toBe(true);
    expect(planeReaches(box, { ...east, y0: Y + 100 })).toBe(true); // through its face
    expect(planeReaches(box, { ...east, y0: Y + 100.01 })).toBe(false);
    expect(planeReaches(box, { ...east, y0: Y - 0.01 })).toBe(false);
    // a diagonal line: the corner of the box reaches the plane, the box beside it does not
    const diagonal: SectionLine = { ...east, x0: X - 10, y0: Y - 10, az: 45 };
    expect(planeReaches(box, diagonal)).toBe(true);
    expect(planeReaches([X + 200, Y, Z, X + 300, Y + 50, Z + 1], diagonal)).toBe(false);
    // no extent, or one with a missing value: asked (the core answers)
    expect(planeReaches(null, east)).toBe(true);
    expect(planeReaches([X, Y, null, X + 1, Y + 1, null], { ...east, y0: Y + 500 })).toBe(true);
  });

  it("BFA1 `section`: float64 points in project coordinates, lines by offsets, closed flags", () => {
    const s = readSection(answer());
    expect(s.wireframeId).toBe(12);
    expect(s.normal).toEqual([0, -1, 0]);
    expect(s.xyz).toBeInstanceOf(Float64Array);
    expect(s.xyz[0]).toBe(X); // bit for bit
    expect(Array.from(s.offsets)).toEqual([0, 4, 7]);
    expect(Array.from(s.closed)).toEqual([1, 0]);
    const empty = readSection(build({ kind: "section", wireframe: 12, lines: 0, points: 0 }, [["xyz", "<f8", new Float64Array(0)], ["offsets", "<u4", Uint32Array.from([0])], ["closed", "<u1", new Uint8Array(0)]]));
    expect(empty.closed.length).toBe(0);
    expect(sectionSegments(empty)).toMatchObject({ segments: 0, bbox: null });
    for (const bad of [answer({ kind: "mesh" }), answer({}, [0, 4, 9]), answer({}, [0, 5, 4, 7], [1, 0, 0]), answer({}, [1, 4, 7])]) {
      expect(() => readSection(bad)).toThrow(VizError);
    }
    expect(() => readSection(new TextEncoder().encode(JSON.stringify({ code: "wireframe_not_found", message: "Каркас не найден.", hint: "Обновите дерево проекта." })).buffer as ArrayBuffer)).toThrow("Каркас не найден.");
  });

  it("segments: a closed line is closed, points 1 mm apart stay apart, float32 within 0.25 mm of the core", () => {
    const s = readSection(answer());
    const g = sectionSegments(s);
    expect(g.segments).toBe(4 + 2); // the square with the segment that closes it, and two of the open line
    expect([g.origin.x, g.origin.y, g.origin.z].every(Number.isInteger)).toBe(true);
    expect(g.positions).toBeInstanceOf(Float32Array);
    expect(g.positions.length).toBe(g.segments * 6);
    const o = [g.origin.x, g.origin.y, g.origin.z];
    const at = (segment: number, end: 0 | 1) => [0, 1, 2].map((c) => g.positions[segment * 6 + end * 3 + c] + o[c]);
    const point = (i: number) => [s.xyz[i * 3], s.xyz[i * 3 + 1], s.xyz[i * 3 + 2]];
    const pairs: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6]];
    let worst = 0;
    pairs.forEach(([a, b], k) => {
      for (const [got, want] of [[at(k, 0), point(a)], [at(k, 1), point(b)]]) for (let c = 0; c < 3; c++) worst = Math.max(worst, Math.abs(got[c] - want[c]));
    });
    expect(worst).toBeLessThan(2.5e-4); // offsets up to 1050 m from the origin: a float32 step of 1.2e-4 m
    // the three points 1 mm apart are three different float32 points
    expect(g.positions[4 * 6]).not.toBe(g.positions[4 * 6 + 3]);
    expect(g.positions[5 * 6]).not.toBe(g.positions[5 * 6 + 3]);
    expect(Array.from(g.bbox!)).toEqual([X, Y + 42.5, Z, X + 2000.002, Y + 42.5, Z + 100]);
    // a closed line of two points is one segment, a line of one point none
    const odd = sectionSegments({ xyz: Float64Array.from([0, 0, 0, 1, 0, 0, 5, 5, 5]), offsets: Uint32Array.from([0, 2, 3]), closed: Uint8Array.from([1, 0]) });
    expect(odd.segments).toBe(1);
  });
});

describe("the layer of section lines", () => {
  const resolution = new THREE.Vector2(1000, 800);

  it("a group per wireframe at a whole-metre offset; thick lines of its colour, not clipped by the window", () => {
    const view = new SectionLinesView({ x: 512_316, y: 6_234_610, z: 0 }, { resolution });
    expect(view.bbox).toBeNull(); // the wireframes count in «Вписать», not their lines
    const s = readSection(answer());
    view.set(12, s, "#c0392b");
    view.set(13, { xyz: Float64Array.from([X, Y, Z, X + 10, Y, Z]), offsets: Uint32Array.from([0, 2]), closed: Uint8Array.from([0]) }, null);
    expect(view.has(12) && view.has(13)).toBe(true);
    expect(view.object.children).toHaveLength(2);
    const group = view.object.children[0];
    const g = sectionSegments(s);
    expect(group.position.toArray()).toEqual([g.origin.x - 512_316, g.origin.y - 6_234_610, g.origin.z]);
    const line = group.children[0] as LineSegments2;
    expect(line).toBeInstanceOf(LineSegments2);
    expect(line.material.linewidth).toBe(SECTION_WIDTH_PX);
    expect(`#${line.material.color.getHexString()}`).toBe("#c0392b");
    expect(line.material.uniforms.resolution.value).toBe(resolution);
    expect(line.material.clippingPlanes ?? []).toHaveLength(0);
    expect(view.stats()).toEqual([
      { wireframe: 12, lines: 2, closed: 1, segments: 6, color: "#c0392b", bbox: Array.from(g.bbox!) },
      { wireframe: 13, lines: 1, closed: 0, segments: 1, color: SECTION_DEFAULT_COLOR, bbox: [X, Y, Z, X + 10, Y, Z] },
    ]);
    // a new colour of the wireframe; a newer section replaces the old one; an empty one draws nothing
    view.setColor(12, "#00ff00");
    expect(`#${line.material.color.getHexString()}`).toBe("#00ff00");
    view.set(12, { xyz: new Float64Array(0), offsets: Uint32Array.from([0]), closed: new Uint8Array(0) }, "#00ff00");
    expect(view.object.children).toHaveLength(2);
    expect(view.stats().find((x) => x.wireframe === 12)).toMatchObject({ lines: 0, segments: 0, bbox: null });
    expect(view.remove(13)).toBe(true);
    expect(view.remove(13)).toBe(false);
    view.dispose();
    expect(view.object.children).toHaveLength(0);
  });

  it("stands in the scene by its own origin and does not move the extents", () => {
    const origin = new SceneOrigin();
    origin.ensure([X - 500, Y - 500, 0, X + 500, Y + 500, 100]);
    const set = new LayerSet(new THREE.Group(), origin);
    const view = new SectionLinesView({ x: 512_316, y: 6_234_610, z: 0 }, { resolution });
    set.add("sec", view);
    const out: { box: unknown; visible: boolean }[] = [];
    set.extents(origin.value!, out as never);
    expect(out).toHaveLength(0);
    expect(view.object.parent!.position.toArray()).toEqual(origin.offset(view.origin));
  });
});
