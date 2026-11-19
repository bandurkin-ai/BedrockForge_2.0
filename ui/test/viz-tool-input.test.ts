import { describe, expect, it } from "vitest";
import { layerOf } from "../src/edit/io";
import type { RawPointer } from "../src/viz/core/pointer";
import { projectMatrix, projectRay, projectView, ToolInput, type ToolPointer } from "../src/viz/core/toolInput";
import { view, type ViewOptions } from "../src/viz/core/views";
import { viewProjection } from "../src/viz/pick/ray";
import { projectPoint } from "../src/edit/snap";
import { draftPoints, layerBbox, markedLines, outOfReach, SegmentBuffer, segmentCount, vertexMarks } from "../src/viz/edit/overlayBuild";

const ev = (type: RawPointer["type"], x: number, y: number, t: number, button = 0, mods: Partial<RawPointer> = {}): RawPointer => ({ type, x, y, clientX: x + 100, clientY: y + 50, button, shift: false, ctrl: false, t, ...mods });

function rig(takeDown = false) {
  const log: string[] = [];
  const tool = {
    events: [] as ToolPointer[],
    pointer(e: ToolPointer) {
      this.events.push(e);
      if (e.type === "down") return takeDown;
      if (e.type === "click") return e.button === 0;
      return e.type !== "move";
    },
  };
  const input = new ToolInput({ click: (x, y) => log.push(`click ${x} ${y}`), move: (x, y) => log.push(`move ${x} ${y}`), leave: () => log.push("leave"), camera: (on) => log.push(`camera ${on}`) });
  return { input, log, tool };
}

describe("input automaton of the 3D view", () => {
  it("without a tool: the old behaviour — a still quick press is a click, a drag turns the view", () => {
    const { input, log } = rig();
    expect(input.feed(ev("down", 10, 10, 0))).toBe(false);
    expect(input.feed(ev("move", 12, 11, 10))).toBe(false); // within 4 px: still a hover
    expect(input.feed(ev("up", 12, 11, 100))).toBe(false);
    expect(log).toEqual(["move 12 11", "click 12 11"]);
    log.length = 0;
    input.feed(ev("down", 10, 10, 0));
    input.feed(ev("move", 30, 10, 10)); // a drag of the view: no hover
    input.feed(ev("up", 30, 10, 50));
    expect(log).toEqual([]);
    input.feed(ev("down", 10, 10, 0));
    input.feed(ev("up", 10, 10, 700)); // too long
    input.feed(ev("down", 10, 10, 0, 2));
    input.feed(ev("up", 10, 10, 50, 2)); // right button: no pick
    expect(log).toEqual([]);
    expect(input.feed(ev("dblclick", 10, 10, 0))).toBe(false);
  });

  it("a click with a tool goes to the tool; a click it does not take picks as before", () => {
    const { input, log, tool } = rig();
    input.setTool(tool);
    input.feed(ev("down", 5, 5, 0));
    input.feed(ev("up", 5, 5, 30));
    expect(tool.events.map((e) => e.type)).toEqual(["down", "click"]);
    expect(tool.events[1]).toMatchObject({ x: 5, y: 5, clientX: 105, clientY: 55, button: 0 });
    expect(log).toEqual([]); // taken: no pick
    const refuse = { pointer: () => false };
    input.setTool(refuse);
    input.feed(ev("down", 5, 5, 0));
    input.feed(ev("up", 5, 5, 30));
    expect(log).toEqual(["click 5 5"]);
  });

  it("a press the tool takes is a drag of its own: the camera is off until the release, the moves are its", () => {
    const { input, log, tool } = rig(true);
    input.setTool(tool);
    expect(input.feed(ev("down", 10, 10, 0, 0, { ctrl: true }))).toBe(true);
    expect(input.feed(ev("move", 40, 30, 20))).toBe(true);
    expect(input.feed(ev("move", 60, 30, 40))).toBe(true);
    expect(input.feed(ev("up", 60, 30, 60))).toBe(true);
    expect(tool.events.map((e) => e.type)).toEqual(["down", "move", "move", "up"]); // moved: no click
    expect(tool.events[0].ctrl).toBe(true);
    expect(log).toEqual(["camera false", "camera true"]);
    // pressed and released in place: the release is also a click (selection)
    tool.events.length = 0;
    input.feed(ev("down", 10, 10, 0));
    input.feed(ev("up", 11, 10, 40));
    expect(tool.events.map((e) => e.type)).toEqual(["down", "up", "click"]);
  });

  it("right button without a drag finishes (goes to the tool); with a drag it pans the view", () => {
    const { input, log, tool } = rig();
    input.setTool(tool);
    expect(input.feed(ev("down", 10, 10, 0, 2))).toBe(false); // the camera may pan
    input.feed(ev("up", 11, 11, 80, 2));
    expect(tool.events.map((e) => `${e.type}:${e.button}`)).toEqual(["click:2"]);
    tool.events.length = 0;
    input.feed(ev("down", 10, 10, 0, 2));
    input.feed(ev("move", 50, 10, 20));
    input.feed(ev("up", 50, 10, 80, 2));
    expect(tool.events).toEqual([]);
    expect(log).toEqual([]);
  });

  it("hover moves reach the tool and the scene; a double click goes to the tool; a tool out frees the camera", () => {
    const { input, log, tool } = rig(true);
    input.setTool(tool);
    input.feed(ev("move", 3, 4, 0));
    expect(log).toEqual(["move 3 4"]);
    expect(tool.events[0].type).toBe("move");
    expect(input.feed(ev("dblclick", 3, 4, 0))).toBe(true);
    input.feed(ev("down", 3, 4, 0));
    input.setTool(null);
    expect(log.slice(-2)).toEqual(["camera false", "camera true"]);
    expect(input.active).toBe(false);
  });
});

describe("cursor ray in project coordinates (float64)", () => {
  const origin = { x: 512_000, y: 7_100_000, z: 300 };
  const box = { min: [-50, -40, -30] as [number, number, number], max: [50, 40, 30] as [number, number, number] };
  const size = { w: 800, h: 600 };

  for (const perspective of [false, true]) {
    it(`through the point it was cast at, ×5 exaggeration (${perspective ? "perspective" : "orthographic"})`, () => {
      const vexag = 5;
      const opts: ViewOptions = { aspect: 800 / 600, perspective, fov: 45, vexag };
      const cam = view("iso", { min: [box.min[0], box.min[1], box.min[2] * vexag], max: [box.max[0], box.max[1], box.max[2] * vexag] }, opts);
      const m = projectMatrix(viewProjection(cam, opts), origin, vexag);
      const p: [number, number, number] = [512_013.125, 7_100_021.0625, 312.375];
      const s = projectPoint(m, size.w, size.h, p[0], p[1], p[2]);
      expect(s).not.toBeNull();
      const ray = projectRay(cam, opts, size, origin, vexag, s![0], s![1])!;
      expect(Math.hypot(...ray.dir)).toBeCloseTo(1, 12);
      // distance from the point to the ray
      const w = [p[0] - ray.origin[0], p[1] - ray.origin[1], p[2] - ray.origin[2]];
      const t = w[0] * ray.dir[0] + w[1] * ray.dir[1] + w[2] * ray.dir[2];
      const d = Math.hypot(w[0] - t * ray.dir[0], w[1] - t * ray.dir[1], w[2] - t * ray.dir[2]);
      expect(d).toBeLessThan(1e-6);
      // the exaggeration is undone: the vertical part of the ray is 5 times flatter than on the screen
      expect(projectRay(cam, opts, size, null, vexag, 1, 1)).toBeNull();
      const v = projectView(viewProjection(cam, opts), origin, vexag, size, (x, y) => x > 0 && y > 0)!;
      expect(v.visible!(origin.x + 1, origin.y + 1, 0)).toBe(true);
      expect(v.visible!(origin.x - 1, origin.y + 1, 0)).toBe(false);
    });
  }
});

describe("buffers of the editing layer", () => {
  const o = { x: 1000, y: 2000, z: 0 };
  it("a line changed with as many segments is rewritten in place; a new vertex rebuilds", () => {
    const layer = layerOf([
      { points: [[1000, 2000, 5], [1010, 2000, 5], [1010, 2010, 5]], closed: true },
      { points: [[1000, 2000, NaN], [1001, 2001, NaN]] },
      { points: [[999, 1999, 1]] },
    ]);
    expect(layer.lines.map(segmentCount)).toEqual([3, 1, 0]);
    const b = new SegmentBuffer();
    expect(b.update(layer.lines, o, 7).all).toBe(true);
    expect(b.count).toBe(4);
    expect(Array.from(b.positions.slice(12, 18))).toEqual([10, 10, 5, 0, 0, 5]); // the closing segment
    expect(Array.from(b.positions.slice(18, 24))).toEqual([0, 0, 7, 1, 1, 7]); // no height: the Z of the panel
    expect(b.update(layer.lines, o, 7)).toEqual({ all: false, ranges: [] });
    const moved = { ...layer.lines[1], points: [[1000, 2000, NaN], [1005, 2001, NaN]] as [number, number, number][] };
    const w = b.update([layer.lines[0], moved, layer.lines[2]], o, 7);
    expect(w).toEqual({ all: false, ranges: [[18, 6]] });
    expect(b.positions[21]).toBe(5);
    const grown = { ...moved, points: [...moved.points, [1006, 2002, NaN] as [number, number, number]] };
    expect(b.update([layer.lines[0], grown, layer.lines[2]], o, 7).all).toBe(true);
    expect(b.count).toBe(5);
  });

  it("marks, rubber band, box and reach", () => {
    const layer = layerOf([{ points: [[0, 0, 1], [10, 0, 1], [10, 10, 2]] }, { points: [[5, 5, NaN]] }]);
    const ids = markedLines({ lines: [], vertices: [{ line: 2, index: 0 }] }, null, []);
    expect([...ids]).toEqual([2]);
    expect(Array.from(vertexMarks(layer, new Set([1, 2]), { x: 0, y: 0, z: 0 }, 0, 2))).toEqual([0, 0, 1, 10, 0, 1]);
    expect(draftPoints(layer, { line: 1, atStart: false }, true, [3, 3, 1])).toEqual([[10, 10, 2], [3, 3, 1], [3, 3, 1], [0, 0, 1]]);
    expect(draftPoints(layer, null, false, [3, 3, 1])).toEqual([]);
    expect(layerBbox(layer, 0)).toEqual([0, 0, 0, 10, 10, 2]);
    expect(outOfReach({ x: 0, y: 0, z: 0 }, [0, 0, 0, 4001, 0, 0])).toBe(true);
    expect(outOfReach({ x: 0, y: 0, z: 0 }, layerBbox(layer, 0))).toBe(false);
  });
});
