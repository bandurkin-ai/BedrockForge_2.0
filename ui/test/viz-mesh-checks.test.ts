/**
 * The red layer of a checked wireframe (ТЗ 7.21, ADR 0013 §3, §5–§6): BFA1 `checks` of the core — the corners
 * of the crossing triangles as float32 from the whole-metre origins of their groups, within 0.25 mm of the
 * float64 of the core on UTM-sized coordinates — and the layer of the scene: a mesh per group, plain red over
 * the surface, clipped by the section window, not counted in the extents.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { LayerSet } from "../src/viz/core/layers";
import { SceneOrigin } from "../src/viz/core/sceneOrigin";
import { VizError } from "../src/viz/error";
import { CHECKS_COLOR, ChecksView, readChecks } from "../src/viz/mesh/checks";

const X = 512_345.678_901;
const Y = 6_234_567.123_456;
const Z = 412.345_678;

/** Three triangles in float64: two near the first origin (one of them 7.9 km away from it), one 40 km east. */
const CORNERS: number[][] = [
  [X, Y, Z, X + 10.001, Y, Z, X, Y + 10.002, Z + 0.003],
  [X + 7900.123_4, Y - 7900.567_8, Z, X + 7901, Y - 7900, Z + 1, X + 7900, Y - 7899, Z + 2],
  [X + 40_000, Y, Z, X + 40_010, Y, Z, X + 40_000, Y + 10, Z + 5],
];
const ORIGINS = [
  [Math.round(X), Math.round(Y), Math.round(Z)],
  [Math.round(X) + 40_000, Math.round(Y), Math.round(Z)],
];
const GROUPS = [
  { origin: ORIGINS[0], first: 0, n: 2 },
  { origin: ORIGINS[1], first: 2, n: 1 },
];

/** What the core sends: float64 minus the whole-metre origin of the group, then float32. */
function positions(): Float32Array {
  const out = new Float32Array(27);
  CORNERS.forEach((t, k) => t.forEach((v, i) => (out[k * 9 + i] = v - ORIGINS[k < 2 ? 0 : 1][i % 3])));
  return out;
}

function answer(patch: Record<string, unknown> = {}, xyz = positions(), tri = Uint32Array.from([5, 9, 77])): ArrayBuffer {
  const header = { kind: "checks", format: 1, state: "done", triangles: 3, crossing: 5, groups: GROUPS, bbox: [X, Y - 7900.567_8, Z, X + 40_010, Y + 10.002, Z + 5], ...patch };
  return build(header, [["xyz", "<f4", xyz], ["tri", "<u4", tri]]);
}

const empty = (state: string, crossing = 0): ArrayBuffer => answer({ state, triangles: 0, crossing, groups: [], bbox: null }, new Float32Array(0), new Uint32Array(0));

describe("BFA1 `checks`: the crossing triangles of a wireframe", () => {
  it("corners as float32 from the origin of their group, the numbers of the triangles, how many cross in all", () => {
    const c = readChecks(answer());
    expect([c.state, c.triangles, c.crossing]).toEqual(["done", 3, 5]); // 5 cross, 3 are listed here
    expect(c.positions).toBeInstanceOf(Float32Array);
    expect(c.positions.length).toBe(27);
    expect(Array.from(c.tri)).toEqual([5, 9, 77]);
    expect(c.groups).toEqual([
      { origin: { x: ORIGINS[0][0], y: ORIGINS[0][1], z: ORIGINS[0][2] }, first: 0, n: 2 },
      { origin: { x: ORIGINS[1][0], y: ORIGINS[1][1], z: ORIGINS[1][2] }, first: 2, n: 1 },
    ]);
    expect(c.bbox).toEqual([X, Y - 7900.567_8, Z, X + 40_010, Y + 10.002, Z + 5]);
    // origin + float32 is the corner of the core within a quarter of a millimetre, 7.9 km from the origin too
    let worst = 0;
    for (const g of c.groups) {
      const o = [g.origin.x, g.origin.y, g.origin.z];
      for (let k = g.first; k < g.first + g.n; k++) for (let i = 0; i < 9; i++) worst = Math.max(worst, Math.abs(c.positions[k * 9 + i] + o[i % 3] - CORNERS[k][i]));
    }
    expect(worst).toBeLessThan(2.5e-4);
    expect(Math.max(...Array.from(c.positions, Math.abs))).toBeLessThan(8000.5);
    // corners a millimetre apart stay apart
    expect(c.positions[3] - c.positions[0]).toBeCloseTo(10.001, 4);
  });

  it("a wireframe that was not checked, could not be, crosses nowhere, or lost its list", () => {
    for (const state of ["none", "failed", "done", "missing"] as const) {
      const c = readChecks(empty(state, state === "missing" ? 12 : 0));
      expect([c.state, c.triangles, c.groups.length, c.positions.length, c.bbox]).toEqual([state, 0, 0, 0, null]);
      expect(c.crossing).toBe(state === "missing" ? 12 : 0); // the numbers of «Свойства» stay when the list is gone
    }
  });

  it("a damaged answer is refused with a Russian text and a hint", () => {
    const bad = [
      answer({ kind: "mesh" }),
      answer({ state: "ready" }),
      answer({ triangles: 4 }), // more than the arrays hold
      answer({ triangles: -1 }),
      answer({ groups: [GROUPS[0]] }), // the third triangle is in no group
      answer({ groups: [GROUPS[1], GROUPS[0]] }),
      answer({ groups: [{ ...GROUPS[0], n: 0 }, { ...GROUPS[1], first: 0, n: 3 }] }),
      answer({ groups: [{ origin: [X, Y, Z], first: 0, n: 3 }] }), // not whole metres
      answer({ groups: "нет" }),
      answer({}, positions().subarray(0, 18)),
      answer({}, positions(), Uint32Array.from([5, 9])),
    ];
    for (const buf of bad) {
      let caught: unknown = null;
      try {
        readChecks(buf);
      } catch (e) {
        caught = e;
      }
      expect(caught).toBeInstanceOf(VizError);
      expect((caught as VizError).message).toMatch(/[А-Яа-яЁё]{4,}/);
      expect((caught as VizError).hint.length).toBeGreaterThan(10);
    }
    const refused = new TextEncoder().encode(JSON.stringify({ code: "wireframe_not_found", message: "Каркас не найден.", hint: "Обновите дерево проекта." })).buffer as ArrayBuffer;
    expect(() => readChecks(refused)).toThrow("Каркас не найден.");
  });
});

describe("the red layer in the scene", () => {
  const clip = [new THREE.Plane(new THREE.Vector3(0, 1, 0), 5)];

  it("a mesh per group at a whole-metre offset; plain red in front of the surface, clipped by the section window", () => {
    const checks = readChecks(answer());
    const view = new ChecksView(12, checks, { clip });
    expect(view.wireframeId).toBe(12);
    expect(view.origin).toEqual({ x: ORIGINS[0][0], y: ORIGINS[0][1], z: ORIGINS[0][2] });
    expect(view.bbox).toBeNull(); // the wireframe counts in «Вписать», not its defects
    expect(view.object.children).toHaveLength(2);
    const [near, far] = view.object.children as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>[];
    expect(near.position.toArray()).toEqual([0, 0, 0]);
    expect(far.position.toArray()).toEqual([40_000, 0, 0]);
    // the buffer of the answer as it is: three corners a triangle, no index, nothing copied
    const a = near.geometry.getAttribute("position") as THREE.BufferAttribute;
    const b = far.geometry.getAttribute("position") as THREE.BufferAttribute;
    expect([a.count, b.count, a.itemSize]).toEqual([6, 3, 3]);
    expect(near.geometry.index).toBeNull();
    expect((a.array as Float32Array).buffer).toBe(checks.positions.buffer);
    expect(Array.from(b.array)).toEqual(Array.from(checks.positions.subarray(18)));
    expect(near.geometry.boundingSphere!.radius).toBeGreaterThan(5000); // both triangles of the group
    const m = near.material;
    expect(far.material).toBe(m);
    expect(m).toBeInstanceOf(THREE.MeshBasicMaterial); // unlit: red in the shade too
    expect(`#${m.color.getHexString()}`).toBe(CHECKS_COLOR);
    expect(m.side).toBe(THREE.DoubleSide);
    expect(m.clippingPlanes).toBe(clip); // the shared planes of the scene
    expect(m.polygonOffset).toBe(true);
    expect(m.polygonOffsetFactor).toBeLessThan(0); // the surface is pushed back by +1: the red triangles win
    expect(m.polygonOffsetUnits).toBeLessThan(0);
    expect(view.stats()).toEqual({ wireframe: 12, triangles: 3, groups: 2, visible: true });
  });

  it("follows the visibility of its wireframe, stands by its own origin and does not move the extents", () => {
    const view = new ChecksView(12, readChecks(answer()), { clip });
    expect(view.setVisible(true)).toBe(false); // nothing changed
    expect(view.setVisible(false)).toBe(true);
    expect([view.visible, view.stats().visible]).toEqual([false, false]);
    expect(view.setVisible(true)).toBe(true);
    const origin = new SceneOrigin();
    origin.ensure([X - 500, Y - 500, 0, X + 500, Y + 500, 100]);
    const set = new LayerSet(new THREE.Group(), origin);
    set.add("chk:12", view);
    const out: { box: unknown; visible: boolean }[] = [];
    set.extents(origin.value!, out as never);
    expect(out).toHaveLength(0);
    expect(view.object.parent!.position.toArray()).toEqual(origin.offset(view.origin));
    expect(set.remove("chk:12")).toBe(true); // disposes the layer
    expect(view.object.children).toHaveLength(0);
    expect(view.stats().groups).toBe(0);
  });

  it("an answer without triangles makes an empty layer at the zero origin", () => {
    const view = new ChecksView(7, readChecks(empty("done")), { clip });
    expect(view.object.children).toHaveLength(0);
    expect([view.triangles, view.origin]).toEqual([0, { x: 0, y: 0, z: 0 }]);
  });
});
