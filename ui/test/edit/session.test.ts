import { describe, expect, it } from "vitest";
import { planeDistance, sectionPlane } from "../../src/edit/coords";
import { asLoaded, fromStored, toStored } from "../../src/edit/io";
import { emptyLayer, type Vec3 } from "../../src/edit/model";
import { EditSession } from "../../src/edit/session";
import * as cmd from "../../src/edit/tools";
import { closestOnSegmentToRay, type EditPointer } from "../../src/edit/tools";
import type { SectionLine } from "../../src/viz/section/math";
import { ortho } from "./testUtil";

const E0 = 512345.678901234;
const N0 = 6789012.345678901;

/** A session looking straight down on a 200 x 200 m patch; the screen is 400 x 400 px (2 px per metre). */
function planSession(): EditSession {
  const s = new EditSession(asLoaded(1, 3, emptyLayer()), "РУДА", "#ff8800");
  const transform = { matrix: ortho(E0, E0 + 200, N0, N0 + 200, -2000, 2000), width: 400, height: 400 };
  s.view = {
    transform: () => transform,
    ray: (cx, cy) => ({ origin: [E0 + cx / 2, N0 + 200 - cy / 2, 1000], dir: [0, 0, -1] }),
  };
  s.zLevel = 250;
  s.viewChanged();
  return s;
}
const at = (x: number, y: number, type: EditPointer["type"] = "click", extra: Partial<EditPointer> = {}): EditPointer =>
  ({ type, x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false, ...extra });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("stored form", () => {
  it("drops and restores the repeated vertex of closed lines, bit for bit", () => {
    const v = Float64Array.from([E0, N0, 1.5, E0 + 10, N0, 2.5, E0 + 10, N0 + 10, 3.5, E0, N0, 1.5, E0 + 50, N0 + 50, 0, E0 + 60, N0 + 50.1, 0, E0 - 1, N0, 9]);
    const layer = fromStored(v, [0, 4, 6, 7], [1, 0, 0], [{ NAME: "контур" }, {}, {}]);
    expect(layer.lines.map((l) => [l.points.length, l.closed])).toEqual([[3, true], [2, false], [1, false]]);
    const back = toStored(layer);
    expect(Array.from(back.vertices)).toEqual(Array.from(v));
    expect(Array.from(back.offsets)).toEqual([0, 4, 6, 7]);
    expect(Array.from(back.closed)).toEqual([1, 0, 0]);
    expect(back.attributes[0]).toEqual({ NAME: "контур" });
  });
});

describe("EditSession", () => {
  it("assembles contours in the order of the clicks; a second click takes one out; Esc leaves", async () => {
    const s = planSession();
    s.setTool("polygon");
    const square = (x0: number) => [[x0, 300], [x0 + 60, 300], [x0 + 60, 240], [x0, 240]];
    for (const x0 of [20, 150, 280]) {
      for (const [x, y] of square(x0)) {
        s.pointer(at(x, y));
        await tick();
      }
      s.finishDrawing();
    }
    s.setTool("line");
    s.pointer(at(20, 50));
    await tick();
    s.pointer(at(200, 50));
    await tick();
    s.finishDrawing();
    const [a, b, c, open] = s.layer.lines.map((l) => l.id);
    s.startAssemble();
    expect(s.tool).toBe("select");
    for (const x of [340, 20, 150]) s.pointer(at(x, 270)); // the left edges of the third, first and second squares
    expect(s.assembled()).toEqual([c, a, b]);
    expect(s.shownSelection.lines).toEqual([c, a, b]);
    s.pointer(at(20, 270)); // again: out
    expect(s.assembled()).toEqual([c, b]);
    s.pointer(at(100, 50)); // an open line is not a contour
    expect(s.assembled()).toEqual([c, b]);
    expect(s.message).toContain("замкнутым");
    void open;
    s.dropLastContour();
    expect(s.assembled()).toEqual([c]);
    s.setAssembleClosed(true);
    expect(s.assemble?.closed).toBe(true);
    s.escape();
    expect(s.assemble).toBeNull();
  });

  it("a right click ends the line being drawn; without a line it is left to the view", async () => {
    const s = planSession();
    s.setTool("line");
    for (const [x, y] of [[100, 300], [300, 300], [300, 100]]) {
      s.pointer(at(x, y));
      await tick();
    }
    expect(s.drawing).not.toBeNull();
    expect(s.pointer(at(200, 200, "click", { button: 2 }))).toBe(true);
    expect(s.drawing).toBeNull();
    expect(s.layer.lines[0].points).toHaveLength(3);
    expect(s.layer.lines[0].closed).toBe(false);
    expect(s.pointer(at(200, 200, "click", { button: 2 }))).toBe(false);
  });

  it("draws a polygon on the plan level, closes it and undoes point by point", async () => {
    const s = planSession();
    s.setTool("polygon");
    for (const [x, y] of [[100, 300], [300, 300], [300, 100], [100, 100]]) {
      s.pointer(at(x, y));
      await tick();
    }
    s.finishDrawing();
    const line = s.layer.lines[0];
    expect(line.closed).toBe(true);
    expect(line.points).toEqual([[E0 + 50, N0 + 50, 250], [E0 + 150, N0 + 50, 250], [E0 + 150, N0 + 150, 250], [E0 + 50, N0 + 150, 250]]);
    expect(s.dirty).toBe(true);
    s.undo(); // the closing
    s.undo(); // the fourth point
    expect(s.layer.lines[0].points).toHaveLength(3);
    s.redo();
    s.redo();
    expect(s.layer.lines[0].closed).toBe(true);
    s.saved(asLoaded(1, 4, s.layer));
    expect(s.dirty).toBe(false);
    expect(s.baseVersion).toBe(4);
  });

  it("snaps a new point to a vertex of another layer and keeps its exact coordinates", async () => {
    const s = planSession();
    const target: Vec3 = [E0 + 80.123456789, N0 + 120.987654321, 301.5];
    s.snapIndex.setSource({ key: "lines:7", points: { kind: "vertex", xyz: Float64Array.from(target) } });
    s.setTool("line");
    // 5 px away from the vertex on screen: inside the 12 px radius
    s.pointer(at((target[0] - E0) * 2 + 4, (N0 + 200 - target[1]) * 2 - 3));
    await tick();
    expect(s.layer.lines[0].points[0]).toEqual(target); // Z comes from the object, not from the plan level
    s.snap = { ...s.snap, enabled: false };
    s.pointer(at(200, 200));
    await tick();
    expect(s.layer.lines[0].points[1]).toEqual([E0 + 100, N0 + 100, 250]);
  });

  it("puts points into the section plane, and projects a hole target into it", async () => {
    const s = planSession();
    const section: SectionLine = { x0: E0 + 100, y0: N0, az: 0, length: null, towards: 30, away: 10 };
    s.section = section;
    s.clip = true;
    const plane = sectionPlane(section);
    s.setTool("line");

    // A boundary of an interval 14.5 m off the plane: with projection on, the vertex lands in the plane.
    const boundary: Vec3 = [E0 + 95.5, N0 + 75.25, 333.125];
    s.snapIndex.setSource({ key: "set:1:intervals", points: { kind: "interval", xyz: Float64Array.from(boundary), labels: () => "DH-1 · 12 м" } });
    const px = (boundary[0] - E0) * 2;
    const py = (N0 + 200 - boundary[1]) * 2;
    s.pointer(at(px + 2, py + 1));
    await tick();
    const p = s.layer.lines[0].points[0];
    expect(Math.abs(planeDistance(p, plane))).toBeLessThan(1e-9);
    expect(p[0]).toBeCloseTo(E0 + 110, 6); // the middle of the window: (30 − 10) / 2 to the viewer's side
    expect(p[1]).toBeCloseTo(boundary[1], 6); // moved across the plane only
    expect(p[2]).toBe(boundary[2]);

    // With projection off the true position of the boundary is taken, bit for bit.
    s.snap = { ...s.snap, project: false };
    s.finishDrawing();
    s.pointer(at(px + 2, py + 1));
    await tick();
    expect(s.layer.lines[s.layer.lines.length - 1].points[0]).toEqual(boundary);

    // Without a snap the cursor ray meets the drawing plane (here: seen from the east).
    s.snap = { ...s.snap, enabled: false };
    s.finishDrawing();
    s.view!.ray = (cx, cy) => ({ origin: [E0 + 500, N0 + cx / 2, 400 - cy / 2], dir: [-1, 0, 0] });
    s.pointer(at(100, 100));
    await tick();
    const q = s.layer.lines[s.layer.lines.length - 1].points[0];
    expect(Math.abs(planeDistance(q, plane))).toBeLessThan(1e-9);
    expect(q[1]).toBeCloseTo(N0 + 50, 9);
    expect(q[2]).toBeCloseTo(350, 9);
  });

  it("joins, breaks and deletes through the session commands", () => {
    const s = planSession();
    const a = cmd.addLine(s, [[E0, N0, 0], [E0 + 10, N0, 0]], false);
    const b = cmd.addLine(s, [[E0 + 10, N0, 0], [E0 + 10, N0 + 10, 0]], false);
    cmd.selectLines(s, [a, b]);
    cmd.join(s);
    expect(s.layer.lines).toHaveLength(1);
    expect(s.layer.lines[0].points).toHaveLength(3);
    cmd.setClosed(s, true);
    expect(s.layer.lines[0].closed).toBe(true);
    cmd.translate(s, [0, 0, 5], true);
    expect(s.layer.lines).toHaveLength(2);
    expect(s.layer.lines[1].points[0][2]).toBe(5);
    cmd.deleteSelection(s);
    expect(s.layer.lines).toHaveLength(1);
    s.undo();
    expect(s.layer.lines).toHaveLength(2);
  });

  it("drags a vertex in plan without changing its Z", () => {
    const s = planSession();
    s.snap = { ...s.snap, enabled: false };
    const id = cmd.addLine(s, [[E0 + 50, N0 + 50, 311.5], [E0 + 100, N0 + 50, 320]], false);
    s.pointer(at(100, 300, "down"));
    s.pointer(at(140, 260, "move"));
    s.pointer(at(140, 260, "up"));
    const moved = s.layer.lines.find((l) => l.id === id)!.points[0];
    expect(moved).toEqual([E0 + 70, N0 + 70, 311.5]); // not the plan level 250
    expect(s.history.undoLabel).toBe("Перемещение вершины");
    s.undo();
    expect(s.layer.lines[0].points[0]).toEqual([E0 + 50, N0 + 50, 311.5]);
  });

  it("finds the point of an edge closest to the cursor ray", () => {
    const p = closestOnSegmentToRay([0, 0, 0], [10, 0, 0], { origin: [4, 3, 50], dir: [0, 0, -1] });
    expect(p).toEqual([4, 0, 0]);
    expect(closestOnSegmentToRay([0, 0, 0], [10, 0, 0], { origin: [40, 3, 50], dir: [0, 0, -1] })).toEqual([10, 0, 0]);
  });
});
