import { describe, expect, it } from "vitest";
import { horizontalPlane, sectionPlane } from "../../src/edit/coords";
import { planArea } from "../../src/edit/measure";
import { layerHistory, lineById, type EditLayer, type Vec3 } from "../../src/edit/model";
import {
  appendPoint,
  breakAt,
  copyLines,
  deleteLines,
  deleteVertices,
  densifyLine,
  insertVertex,
  joinLines,
  moveVertex,
  moveVertices,
  offsetLine,
  reverseLine,
  setAttrs,
  setClosed,
  simplifyLine,
  smoothLine,
  takeLines,
  translateLines,
} from "../../src/edit/ops";

const E = 512345.67;
const N = 6789012.34;
const P = (x: number, y: number, z = 100): Vec3 => [E + x, N + y, z];

/** A loaded layer: lines with stored sources 10, 11, 12. */
function loaded(): EditLayer {
  return {
    lines: [
      { id: 1, points: [P(0, 0), P(10, 0), P(20, 5), P(30, 5)], closed: false, attrs: { code: "A" }, src: 10 },
      { id: 2, points: [P(0, 20), P(10, 20), P(10, 30), P(0, 30)], closed: true, attrs: { code: "B" }, src: 11 },
      { id: 3, points: [P(50, 0), P(50, 0.0001, 100)], closed: false, attrs: {}, src: 12 },
    ],
    nextId: 4,
  };
}

describe("acceptance (9.23): an operation that changes nothing returns the same layer, no history step", () => {
  const layer = loaded();
  const noops: [string, (l: EditLayer) => EditLayer][] = [
    ["замкнуть замкнутую", (l) => setClosed(l, 2, true)],
    ["разомкнуть открытую", (l) => setClosed(l, 1, false)],
    ["замкнуть линию из двух точек", (l) => setClosed(l, 3, true)],
    ["разорвать в конце", (l) => breakAt(l, { line: 1, index: 0 }).layer],
    ["разорвать в последней", (l) => breakAt(l, { line: 1, index: 3 }).layer],
    ["разорвать чужую", (l) => breakAt(l, { line: 99, index: 1 }).layer],
    ["соединить с собой", (l) => joinLines(l, 1, 1)?.layer ?? l],
    ["соединить с замкнутой", (l) => joinLines(l, 1, 2)?.layer ?? l],
    ["развернуть чужую", (l) => reverseLine(l, 99)],
    ["сдвиг на ноль", (l) => translateLines(l, [1, 2], [0, 0, 0])],
    ["сдвиг на NaN", (l) => translateLines(l, [1], [NaN, 0, 0])],
    ["сдвиг вершин на ноль", (l) => moveVertices(l, [{ line: 1, index: 0 }], [0, 0, 0])],
    ["вершину на её место", (l) => moveVertex(l, { line: 1, index: 1 }, P(10, 0))],
    ["копия ничего", (l) => copyLines(l, []).layer],
    ["копия на NaN", (l) => copyLines(l, [1], [0, NaN, 0]).layer],
    ["смещение на NaN", (l) => offsetLine(l, 1, NaN)?.layer ?? l],
    ["проредить с малым допуском", (l) => simplifyLine(l, 1, 1e-6)],
    ["проредить с отрицательным", (l) => simplifyLine(l, 1, -1)],
    ["сгустить с большим шагом", (l) => densifyLine(l, 1, 1000)],
    ["сгустить с нулевым", (l) => densifyLine(l, 1, 0)],
    ["сгладить ноль раз", (l) => smoothLine(l, 1, 0)],
    ["сгладить два отрезка из одного", (l) => smoothLine(l, 3)],
    ["вставить вне отрезка", (l) => insertVertex(l, 1, 7, P(0, 0))],
    ["продолжить чужую", (l) => appendPoint(l, 99, P(0, 0))],
    ["удалить чужие вершины", (l) => deleteVertices(l, [{ line: 1, index: 9 }])],
    ["удалить чужие линии", (l) => deleteLines(l, [99])],
    ["атрибут как был", (l) => setAttrs(l, [1], { code: "A" })],
    ["забрать ничего", (l) => takeLines(l, []).layer],
  ];

  for (const [name, op] of noops) {
    it(name, () => {
      const h = layerHistory(layer);
      const next = op(layer);
      expect(next).toBe(layer);
      h.push(next, name);
      expect(h.canUndo).toBe(false);
      expect(h.dirty).toBe(false);
    });
  }

  it("reversing a single point changes nothing", () => {
    const dot: EditLayer = { lines: [{ id: 1, points: [P(0, 0)], closed: false, attrs: {}, src: -1 }], nextId: 2 };
    expect(reverseLine(dot, 1)).toBe(dot);
  });

  it("a real change makes one step and keeps the other lines shared", () => {
    const h = layerHistory(layer);
    const next = reverseLine(layer, 1);
    h.push(next, "Развернуть");
    expect(h.depth).toBe(1);
    expect(next.lines[1]).toBe(layer.lines[1]);
    expect(h.bytes).toBeGreaterThan(0);
  });
});

describe("src follows the geometry", () => {
  it("copies, parts, joins and offsets keep the source; new and put lines have none", () => {
    const layer = loaded();
    expect(copyLines(layer, [2], [5, 0, 0]).layer.lines[3].src).toBe(11);
    const broken = breakAt(layer, { line: 1, index: 2 }).layer;
    expect(broken.lines.map((l) => l.src)).toEqual([10, 10, 11, 12]);
    const closedBreak = breakAt(layer, { line: 2, index: 1 }).layer;
    expect(lineById(closedBreak, 2)!.src).toBe(11);
    expect(joinLines(layer, 3, 1)!.layer.lines.map((l) => l.src)).toEqual([11, 12]);
    expect(offsetLine(layer, 2, 1)!.layer.lines[3].src).toBe(11);
    // moving keeps the source, a new line has none
    expect(translateLines(layer, [1], [1, 0, 0]).lines[0].src).toBe(10);
  });
});

describe("vertices without a height", () => {
  const noZ = (): EditLayer => ({
    lines: [{ id: 1, points: [P(0, 0, NaN), P(10, 0.05, NaN), P(20, 0, 100), P(30, 6, NaN), P(40, 0, NaN)], closed: false, attrs: {}, src: 0 }],
    nextId: 2,
  });

  it("stay without a height through moves, densify and smoothing", () => {
    const layer = noZ();
    const moved = translateLines(layer, [1], [1, 2, 3]);
    expect(Number.isNaN(moved.lines[0].points[0][2])).toBe(true);
    expect(moved.lines[0].points[2][2]).toBe(103);
    const dense = densifyLine(layer, 1, 2.5).lines[0].points;
    // every segment is a little over 10 m in plan → 5 pieces; the new points have no height either
    expect(dense.length).toBe(5 + 4 * 4);
    expect(Number.isNaN(dense[1][2])).toBe(true);
    expect(dense.every((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))).toBe(true);
    expect(moveVertex(layer, { line: 1, index: 0 }, P(0, 0, NaN))).toBe(layer);
  });

  it("are simplified by their distance in plan, never dropped as NaN", () => {
    const pts: Vec3[] = [P(0, 0, NaN), P(10, 0.05, NaN), P(20, 0, 100), P(30, -0.05, NaN), P(40, 0, NaN)];
    const layer: EditLayer = { lines: [{ id: 1, points: pts, closed: false, attrs: {}, src: 0 }], nextId: 2 };
    expect(simplifyLine(layer, 1, 0.1).lines[0].points.map((p) => Math.round(p[0] - E))).toEqual([0, 40]);
    const fine = simplifyLine(layer, 1, 0.01).lines[0].points.map((p) => Math.round(p[0] - E));
    expect(fine).toContain(10);
    expect(fine).toContain(30);
    // the vertex at 20 lies on the chord 10–30 in plan: only it goes, whatever its height
    expect(simplifyLine(layer, 1, 1e-6).lines[0].points.map((p) => Math.round(p[0] - E))).toEqual([0, 10, 30, 40]);
  });

  it("are offset in plan, keeping their heights", () => {
    const sq: EditLayer = {
      lines: [{ id: 1, points: [P(0, 0, NaN), P(10, 0, 5), P(10, 10, NaN), P(0, 10, 7)], closed: true, attrs: {}, src: 0 }],
      nextId: 2,
    };
    const out = offsetLine(sq, 1, 1)!.layer.lines[1].points;
    expect(planArea(out)).toBeCloseTo(144, 7);
    expect(Number.isNaN(out[0][2])).toBe(true);
    expect(out[1][2]).toBe(5);
    expect(out[3][2]).toBe(7);
    expect(offsetLine(sq, 1, 1, horizontalPlane(0))).not.toBeNull();
    // a vertical plane needs the heights
    expect(offsetLine(sq, 1, 1, sectionPlane({ x0: E, y0: N, az: 90, length: null, towards: 0, away: 0 }))).toBeNull();
  });
});
