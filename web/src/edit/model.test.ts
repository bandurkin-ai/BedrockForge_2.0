import { describe, expect, it } from "vitest";
import { EMPTY_SELECTION, History, emptyLayer, fromArrays, lineById, toArrays, vertexAt } from "./model";

describe("fromArrays / toArrays", () => {
  const vertices = new Float64Array([
    512345.123456789, 6789012.987654321, 250.1, 512346.1, 6789013.3, 0.1 + 0.2, 512347.7, 6789014.000000001, -17.25,
    // second line: two points
    498765.4321, 6700000.000000001, 1e-9, 498766.5, 6700001.25, 123456.789,
    // third line: a single point
    1 / 3, 2 / 3, Math.PI,
  ]);
  const offsets = [0, 3, 5, 6];
  const closed = [1, 0, 0];
  const attributes = [{ code: "ORE", grade: 1.25, note: null }, { code: 7, flag: true, nested: { a: 1 }, nan: NaN, skip: undefined }, {}];

  it("builds lines with ids, flags and attributes", () => {
    const layer = fromArrays(vertices, offsets, closed, attributes);
    expect(layer.lines.map((l) => l.id)).toEqual([1, 2, 3]);
    expect(layer.nextId).toBe(4);
    expect(layer.lines.map((l) => l.points.length)).toEqual([3, 2, 1]);
    expect(layer.lines.map((l) => l.closed)).toEqual([true, false, false]);
    expect(layer.lines[0].attrs).toEqual({ code: "ORE", grade: 1.25, note: null });
    expect(layer.lines[1].attrs).toEqual({ code: 7, flag: "true", nested: '{"a":1}', nan: null });
    expect(lineById(layer, 2)?.points[1]).toEqual([498766.5, 6700001.25, 123456.789]);
    expect(lineById(layer, 9)).toBeUndefined();
    expect(vertexAt(layer, { line: 1, index: 2 })).toEqual([512347.7, 6789014.000000001, -17.25]);
  });

  it("round-trips every float64 bit", () => {
    const out = toArrays(fromArrays(vertices, offsets, closed, attributes));
    expect(out.vertices).toBeInstanceOf(Float64Array);
    expect(new Uint8Array(out.vertices.buffer)).toEqual(new Uint8Array(vertices.buffer));
    expect(Array.from(out.offsets)).toEqual(offsets);
    expect(Array.from(out.closed)).toEqual(closed);
    const again = toArrays(fromArrays(out.vertices, out.offsets, out.closed, out.attributes));
    expect(new Uint8Array(again.vertices.buffer)).toEqual(new Uint8Array(vertices.buffer));
    expect(again.attributes).toEqual(out.attributes);
  });

  it("handles an empty layer and missing attributes", () => {
    const out = toArrays(emptyLayer());
    expect(out.vertices.length).toBe(0);
    expect(Array.from(out.offsets)).toEqual([0]);
    expect(fromArrays(new Float64Array(0), [0], [], []).lines).toEqual([]);
    expect(fromArrays(new Float64Array([1, 2, 3]), [0, 1], [0], []).lines[0].attrs).toEqual({});
    expect(EMPTY_SELECTION).toEqual({ lines: [], vertices: [] });
  });
});

describe("History", () => {
  it("pushes, undoes and redoes with labels", () => {
    const a = { v: 1 };
    const b = { v: 2 };
    const c = { v: 3 };
    const h = new History(a);
    expect(h.present).toBe(a);
    expect(h.canUndo).toBe(false);
    expect(h.canRedo).toBe(false);
    expect(h.undo()).toBeNull();
    expect(h.redo()).toBeNull();
    expect(h.undoLabel).toBeNull();

    h.push(b, "Новая линия");
    h.push(c, "Переместить вершину");
    expect(h.present).toBe(c);
    expect(h.undoLabel).toBe("Переместить вершину");
    expect(h.undo()).toBe("Переместить вершину");
    expect(h.present).toBe(b);
    expect(h.redoLabel).toBe("Переместить вершину");
    expect(h.undoLabel).toBe("Новая линия");
    expect(h.undo()).toBe("Новая линия");
    expect(h.present).toBe(a);
    expect(h.canUndo).toBe(false);
    expect(h.redo()).toBe("Новая линия");
    expect(h.redo()).toBe("Переместить вершину");
    expect(h.present).toBe(c);
    expect(h.canRedo).toBe(false);
  });

  it("drops the redo tail on push and ignores an identical state", () => {
    const a = { v: 1 };
    const b = { v: 2 };
    const c = { v: 3 };
    const h = new History(a);
    h.push(b, "b");
    h.undo();
    expect(h.canRedo).toBe(true);
    h.push(a, "ничего");
    expect(h.canRedo).toBe(true);
    expect(h.canUndo).toBe(false);
    h.push(c, "c");
    expect(h.canRedo).toBe(false);
    expect(h.redo()).toBeNull();
    expect(h.undo()).toBe("c");
    expect(h.present).toBe(a);
  });

  it("tracks dirty against the saved state", () => {
    const a = { v: 1 };
    const b = { v: 2 };
    const c = { v: 3 };
    const h = new History(a);
    expect(h.dirty).toBe(false);
    h.push(b, "b");
    expect(h.dirty).toBe(true);
    h.undo();
    expect(h.dirty).toBe(false);
    h.redo();
    h.markSaved();
    expect(h.dirty).toBe(false);
    h.push(c, "c");
    expect(h.dirty).toBe(true);
    h.undo();
    expect(h.dirty).toBe(false);
    h.undo();
    expect(h.dirty).toBe(true);
  });

  it("makes one step out of a live drag", () => {
    const a = { v: 1 };
    const h = new History(a);
    const frames = [{ v: 2 }, { v: 3 }, { v: 4 }];
    for (const f of frames) {
      h.replace(f);
      expect(h.present).toBe(f);
      expect(h.canUndo).toBe(false);
      expect(h.dirty).toBe(true);
      expect(h.pending).toBe(true);
      expect(h.committed).toBe(a);
    }
    h.push(frames[2], "Переместить вершину");
    expect(h.pending).toBe(false);
    expect(h.present).toBe(frames[2]);
    expect(h.undo()).toBe("Переместить вершину");
    expect(h.present).toBe(a);
    expect(h.canUndo).toBe(false);
    expect(h.redo()).toBe("Переместить вершину");
    expect(h.present).toBe(frames[2]);
  });

  it("commits or cancels a live state", () => {
    const a = { v: 1 };
    const b = { v: 2 };
    const h = new History(a);
    h.replace(b);
    h.cancel();
    expect(h.present).toBe(a);
    expect(h.dirty).toBe(false);
    h.commit("пусто");
    expect(h.canUndo).toBe(false);

    h.replace(b);
    h.commit("Сдвиг");
    expect(h.present).toBe(b);
    expect(h.undoLabel).toBe("Сдвиг");

    // undo during a drag drops the drag and undoes the last step
    h.replace({ v: 9 });
    expect(h.undo()).toBe("Сдвиг");
    expect(h.present).toBe(a);

    // a drag that ends where it started makes no step
    h.replace({ v: 5 });
    h.push(a, "ничего");
    expect(h.canUndo).toBe(false);
    expect(h.present).toBe(a);
  });

  it("keeps a bounded number of steps", () => {
    const h = new History(0, 3);
    for (let i = 1; i <= 10; i++) h.push(i, `шаг ${i}`);
    let undone = 0;
    while (h.undo()) undone++;
    expect(undone).toBe(3);
    expect(h.present).toBe(7);
    expect(h.dirty).toBe(true);
  });
});
