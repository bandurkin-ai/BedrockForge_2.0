import { describe, expect, it } from "vitest";
import { EditError, fromStored, isChanged, isUntouched, moveBody, readLineGeometry, saveBody, summarize, toStored, type LoadedLayer } from "../../src/edit/io";
import type { EditLayer, Vec3 } from "../../src/edit/model";
import { decodeBfa1 } from "../../src/viz/format/bfa1";
import { build } from "../bfa1-build";

const bits = (a: Float64Array): Uint8Array => new Uint8Array(a.buffer, a.byteOffset, a.byteLength);

// Line 0: closed, the file repeats the first vertex. Line 1: open, NaN Z and −0.0. Line 2: closed in a file that does
// NOT repeat the first vertex. Line 3: a tie line of two points.
const XYZ = new Float64Array([
  512345.123456789, 6789012.987654321, 250.1, 512346.1, 6789013.3, 0.1 + 0.2, 512347.7, 6789014.000000001, -17.25, 512345.123456789, 6789012.987654321, 250.1,
  498765.4321, 6700000.000000001, NaN, 498766.5, -0.0, 123456.789,
  1, 2, 3, 4, 5, 6, 7, 8, 9,
  10, 10, 10, 20, 20, 20,
]);
const OFFSETS = new Uint32Array([0, 4, 6, 9, 11]);
const CLOSED = new Uint8Array([1, 0, 1, 0]);
const LID = new Uint32Array([0, 5, 2, 9]);

function linegeom(version = 3): ArrayBuffer {
  return build(
    { kind: "linegeom", format: 1, layer_id: 42, version, lines: 4, points: 11, points_only: false, ties: [3] },
    [
      ["xyz", "<f8", XYZ],
      ["offsets", "<u4", OFFSETS],
      ["closed", "<u1", CLOSED],
      ["lid", "<u4", LID],
    ],
  );
}

interface Body {
  header: Record<string, unknown>;
  src: Int32Array;
  touched: Uint8Array;
  count: Uint32Array;
  closed: Uint8Array;
  xyz: Float64Array;
}

function body(buf: ArrayBuffer, prefix = ""): Body {
  const { header, arrays } = decodeBfa1(buf);
  return {
    header,
    src: arrays[prefix + "src"] as Int32Array,
    touched: arrays[prefix + "touched"] as Uint8Array,
    count: arrays[prefix + "count"] as Uint32Array,
    closed: arrays[prefix + "closed"] as Uint8Array,
    xyz: arrays[prefix + "xyz"] as Float64Array,
  };
}

/** The new stored layer as `lines/assemble.py` builds it: untouched lines are slices of the base, bit for bit. */
function assemble(b: Body): { xyz: Float64Array; offsets: number[]; closed: number[] } {
  const pos = new Map<number, number>();
  LID.forEach((v, i) => pos.set(v, i));
  const out: number[] = [];
  const offsets = [0];
  let k = 0;
  for (let i = 0; i < b.src.length; i++) {
    if (b.touched[i] || b.src[i] < 0) {
      for (let j = 0; j < b.count[i] * 3; j++) out.push(b.xyz[k++]);
    } else {
      const l = pos.get(b.src[i]) as number;
      for (let j = OFFSETS[l] * 3; j < OFFSETS[l + 1] * 3; j++) out.push(XYZ[j]);
    }
    offsets.push(out.length / 3);
  }
  return { xyz: new Float64Array(out), offsets, closed: Array.from(b.closed) };
}

const withLine = (layer: EditLayer, i: number, change: (p: Vec3[]) => Vec3[]): EditLayer => ({
  ...layer,
  lines: layer.lines.map((l, k) => (k === i ? { ...l, points: change(l.points) } : l)),
});

describe("stored form ↔ working copy", () => {
  it("drops and restores the closing vertex, bit for bit", () => {
    const layer = fromStored(XYZ, OFFSETS, CLOSED, [], LID);
    expect(layer.lines.map((l) => l.points.length)).toEqual([3, 2, 3, 2]);
    expect(layer.lines.map((l) => l.src)).toEqual([0, 5, 2, 9]);
    expect(Object.is(layer.lines[1].points[1][1], -0)).toBe(true);
    expect(Number.isNaN(layer.lines[1].points[0][2])).toBe(true);
    const back = toStored(layer);
    // line 2 is closed without a repeat in the file: the stored form gets the repeat, the rest is identical
    expect(Array.from(back.offsets)).toEqual([0, 4, 6, 10, 12]);
    expect(bits(back.vertices.subarray(0, 18))).toEqual(bits(XYZ.subarray(0, 18)));
    expect(Array.from(back.src)).toEqual([0, 5, 2, 9]);
  });

  it("keeps a closing vertex that differs in its bits (−0.0 against 0.0)", () => {
    const xyz = new Float64Array([0, 0, 0, 1, 0, 0, 1, 1, 0, -0, 0, 0]);
    const layer = fromStored(xyz, [0, 4], [1], []);
    expect(layer.lines[0].points.length).toBe(4);
  });
});

describe("linegeom and the body of a save", () => {
  it("reads the layer, marks tie lines and changes nothing on its own", () => {
    const loaded = readLineGeometry(linegeom());
    expect(loaded).toMatchObject({ layerId: 42, version: 3, pointsOnly: false });
    expect(loaded.layer.lines[3].attrs).toEqual({ ROLE: "TIE" });
    for (const line of loaded.layer.lines) {
      expect(isUntouched(line, loaded)).toBe(true);
      expect(isChanged(line, loaded)).toBe(false);
    }
    expect(summarize(loaded.layer, loaded)).toEqual({ lines: 4, touched: 0, created: 0, removed: 0, vertices: 10 });
  });

  it("an unchanged layer: references only, the core rebuilds the base bit for bit", () => {
    const loaded = readLineGeometry(linegeom());
    const b = body(saveBody(loaded.layer, loaded, "ничего"));
    expect(b.header).toMatchObject({ base_version: 3, note: "ничего", new_attrs: {} });
    expect(Array.from(b.src)).toEqual([0, 5, 2, 9]);
    expect(Array.from(b.touched)).toEqual([0, 0, 0, 0]);
    expect(Array.from(b.count)).toEqual([4, 2, 3, 2]);
    expect(Array.from(b.closed)).toEqual([1, 0, 1, 0]);
    expect(b.xyz.length).toBe(0);
    const out = assemble(b);
    expect(bits(out.xyz)).toEqual(bits(XYZ));
    expect(out.offsets).toEqual(Array.from(OFFSETS));
  });

  it("sends the vertices of a changed line in the storage form; the others stay as in the file", () => {
    const loaded = readLineGeometry(linegeom());
    const moved = withLine(loaded.layer, 0, (p) => [p[0], [p[1][0] + 0.5, p[1][1], p[1][2]], p[2]]);
    expect(isChanged(moved.lines[0], loaded)).toBe(true);
    expect(isChanged(moved.lines[1], loaded)).toBe(false);
    const b = body(saveBody(moved, loaded));
    expect(Array.from(b.touched)).toEqual([1, 0, 0, 0]);
    expect(Array.from(b.count)).toEqual([4, 2, 3, 2]);
    expect(b.xyz.length).toBe(12);
    // the repeated first vertex, bit for bit
    expect(bits(b.xyz.subarray(9, 12))).toEqual(bits(XYZ.subarray(0, 3)));
    const out = assemble(b);
    expect(out.xyz[3]).toBe(512346.6);
    expect(bits(out.xyz.subarray(12))).toEqual(bits(XYZ.subarray(12)));
  });

  it("new lines, removed lines and attributes", () => {
    const loaded = readLineGeometry(linegeom());
    const l = loaded.layer.lines;
    const edited: EditLayer = {
      lines: [
        l[1],
        { id: 10, points: [[1, 1, NaN], [2, 2, 2], [3, 1, 2]], closed: true, attrs: { ROLE: "TIE", grade: 1.5 }, src: -1 },
        { ...l[3], attrs: {} },
        { id: 11, points: [[5, 5, 5]], closed: true, attrs: {}, src: -1 },
        { id: 12, points: [], closed: false, attrs: {}, src: -1 },
      ],
      nextId: 13,
    };
    expect(summarize(edited, loaded)).toEqual({ lines: 4, touched: 2, created: 2, removed: 2, vertices: 8 });
    const b = body(saveBody(edited, loaded));
    expect(Array.from(b.src)).toEqual([5, -1, 9, -1]);
    expect(Array.from(b.touched)).toEqual([0, 1, 0, 1]);
    // a closed line of one point is stored open
    expect(Array.from(b.closed)).toEqual([0, 1, 0, 0]);
    expect(Array.from(b.count)).toEqual([2, 4, 2, 1]);
    expect(b.header.new_attrs).toEqual({ "1": { ROLE: "TIE", grade: 1.5 }, "2": { ROLE: null } });
    expect(Number.isNaN(b.xyz[2]) && Number.isNaN(b.xyz[11])).toBe(true);
  });

  it("moves stored lines only", () => {
    const loaded: LoadedLayer = readLineGeometry(linegeom());
    const layer = loaded.layer;
    const b = moveBody(layer, loaded, [layer.lines[1].id, layer.lines[2].id], 77, 4);
    const src = body(b);
    const moved = body(b, "moved_");
    expect(src.header).toMatchObject({ base_version: 3, target_id: 77, target_base: 4 });
    expect(Array.from(src.src)).toEqual([0, 9]);
    expect(Array.from(moved.src)).toEqual([5, 2]);
    expect(Array.from(moved.touched)).toEqual([0, 0]);
    const withNew: EditLayer = { lines: [...layer.lines, { id: 50, points: [[0, 0, 0]], closed: false, attrs: {}, src: -1 }], nextId: 51 };
    expect(() => moveBody(withNew, loaded, [50], 77, 4)).toThrow(EditError);
    expect(() => moveBody(layer, loaded, [], 77, 4)).toThrow(/Не выбраны/);
  });

  it("rejects what is not a line layer, in Russian", () => {
    const wrong = build({ kind: "snap" }, [["xyz", "<f8", new Float64Array(3)]]);
    expect(() => readLineGeometry(wrong)).toThrow(/Геометрия слоя повреждена/);
    const bad = build({ kind: "linegeom" }, [
      ["xyz", "<f8", new Float64Array(6)],
      ["offsets", "<u4", new Uint32Array([0, 3])],
      ["closed", "<u1", new Uint8Array(1)],
      ["lid", "<u4", new Uint32Array(1)],
    ]);
    expect(() => readLineGeometry(bad)).toThrow(EditError);
  });
});
