// Э10 10.21 (дефект 10.12): the own snap targets of the working copy in pieces — an edit rebuilds only the pieces of
// the lines it changed, and the index answers exactly as with one source of the whole layer.
import { describe, expect, it } from "vitest";
import { asLoaded, fromStored } from "../../src/edit/io";
import type { EditLayer } from "../../src/edit/model";
import * as ops from "../../src/edit/ops";
import { CHUNK_VERTICES, OwnSnapSource } from "../../src/edit/ownSnap";
import { EditSession } from "../../src/edit/session";
import { layerSnapSource, type SnapKind } from "../../src/edit/snap";
import { SnapIndex } from "../../src/edit/snapIndex";
import { lcg, ortho } from "./testUtil";

const X0 = 500000.25;
const Y0 = 7000000.5;
const LINES = 100;
const VERTS = 500;

function grid(): EditLayer {
  const xyz = new Float64Array(LINES * VERTS * 3);
  const off = [0];
  for (let l = 0; l < LINES; l++) {
    for (let v = 0; v < VERTS; v++) xyz.set([X0 + v * 0.4, Y0 + l * 2, 100 + ((l * 7 + v) % 13)], (l * VERTS + v) * 3);
    off.push((l + 1) * VERTS);
  }
  return fromStored(xyz, off, new Uint8Array(LINES), []);
}

const VIEW = { matrix: ortho(X0 - 5, X0 + 205, Y0 - 5, Y0 + 205, -1000, 1000), width: 800, height: 800 };
const KINDS = new Set<SnapKind>(["vertex", "midpoint", "nearest"]);

/** The index over the pieces and the index over one source of the whole layer answer the same at 300 pixels. */
function sameAnswers(own: SnapIndex, layer: EditLayer): void {
  const whole = new SnapIndex();
  whole.setSource(layerSnapSource(layer));
  whole.setView(VIEW);
  own.setView(VIEW);
  expect(own.size).toBe(whole.size);
  const rnd = lcg(5);
  for (let i = 0; i < 300; i++) {
    const x = rnd() * 800;
    const y = rnd() * 800;
    const a = own.query(x, y, 10, KINDS);
    const b = whole.query(x, y, 10, KINDS);
    expect(a && { kind: a.kind, point: a.point, d: a.distancePx }).toEqual(b && { kind: b.kind, point: b.point, d: b.distancePx });
  }
}

describe("OwnSnapSource", () => {
  it("splits the layer into pieces of about CHUNK_VERTICES; an edit of one line rebuilds one piece", () => {
    const own = new OwnSnapSource();
    let layer = grid();
    const first = own.update(layer);
    expect(first.set.length).toBe(Math.ceil((LINES * VERTS) / (Math.floor(CHUNK_VERTICES / VERTS) * VERTS)));
    expect(first.removed).toEqual([]);
    expect(own.update(layer)).toEqual({ set: [], removed: [] });
    layer = ops.reverseLine(layer, layer.lines[50].id);
    const next = own.update(layer);
    expect(next.set.length).toBe(1);
    expect(next.set[0].points!.xyz.length).toBeLessThanOrEqual((CHUNK_VERTICES + VERTS) * 3);
  });

  it("after adds, deletes, moves and undo the pieces answer as one source of the whole layer", () => {
    const s = new EditSession(asLoaded(1, 1, grid()));
    s.view = { transform: () => VIEW, ray: () => ({ origin: [X0, Y0, 1000], dir: [0, 0, -1] }) };
    s.viewChanged();
    sameAnswers(s.snapIndex, s.layer);
    const ids = s.layer.lines.map((l) => l.id);
    s.edit(ops.translateLines(s.layer, [ids[3], ids[77]], [0.3, 0.7, 0]), "сдвиг");
    s.edit(ops.deleteLines(s.layer, ids.slice(0, 40)), "удаление");
    s.edit(ops.addLine(s.layer, [[X0 + 10, Y0 + 10, 5], [X0 + 90, Y0 + 150, 6], [X0 + 3, Y0 + 199, 7]], true).layer, "новая");
    sameAnswers(s.snapIndex, s.layer);
    s.undo();
    s.undo();
    sameAnswers(s.snapIndex, s.layer);
    s.redo();
    s.redo();
    sameAnswers(s.snapIndex, s.layer);
  });

  it("a piece left without lines is removed from the index", () => {
    const own = new OwnSnapSource();
    const layer = grid();
    const keys = own.update(layer).set.map((x) => x.key);
    const gone = own.update(ops.deleteLines(layer, layer.lines.map((l) => l.id)));
    expect(gone.removed.sort()).toEqual([...keys].sort());
    expect(own.keys()).toEqual([]);
  });

  it("a dragged vertex is left out of its piece and comes back when the drag ends", () => {
    const own = new OwnSnapSource();
    const layer = grid();
    own.update(layer);
    const line = layer.lines[10];
    const drag = own.update(layer, { line: line.id, index: 0 });
    expect(drag.set.length).toBe(1);
    const xyz = drag.set[0].points!.xyz;
    const has = (p: number[]) => {
      for (let i = 0; i < xyz.length; i += 3) if (xyz[i] === p[0] && xyz[i + 1] === p[1] && xyz[i + 2] === p[2]) return true;
      return false;
    };
    expect(has(line.points[0])).toBe(false);
    expect(has(line.points[1])).toBe(true);
    const back = own.update(layer);
    expect(back.set.length).toBe(1);
    expect(back.set[0].key).toBe(drag.set[0].key);
    expect(back.set[0].points!.xyz.length).toBe(xyz.length + 3);
  });
});
