/** One rule for the tools of the 3D view (Э27, Д-47 А8): a general tool (the section line) ends the tool of the
 * string editor — a polyline with two points and more is finished, an empty one goes — and holds the pointer. */
import { describe, expect, it, vi } from "vitest";
import { asLoaded } from "../src/edit/io";
import { emptyLayer } from "../src/edit/model";
import { EditSession } from "../src/edit/session";
import type { EditPointer } from "../src/edit/tools";
import { attachSession } from "../src/modules/lines/editScene";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { setCurrentScene } from "../src/modules/scene/sceneHost";
import { sectionStore } from "../src/modules/section/state";
import { createApp } from "../src/shell/app";
import type { Scene } from "../src/viz/Scene";
import type { ToolHandler, ToolPointer } from "../src/viz/core/toolInput";
import { DRAW_OFF, startDraw } from "../src/viz/section/draw";
import { ToolChain } from "../src/viz/sceneHooks";
import { ortho } from "./edit/testUtil";

vi.mock("../src/viz/edit/overlay", () => ({
  EditOverlay: class {
    update = () => false;
  },
}));

const E0 = 512345.678901234;
const N0 = 6789012.345678901;
const ptr = (type: ToolPointer["type"], x = 1, y = 2, button = 0): ToolPointer => ({ type, x, y, clientX: x, clientY: y, button, shift: false, ctrl: false });
const at = (x: number, y: number): EditPointer => ({ type: "click", x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false });
const tick = () => new Promise((r) => setTimeout(r, 0));

/** The part of `Scene` the editor and the section line use: the chain of the pointer and nothing else. */
function fakeScene() {
  const tools = new ToolChain();
  const scene = {
    chain: tools,
    canvas: { getBoundingClientRect: () => ({ left: 0, top: 0 }) },
    origin: { value: { x: Math.round(E0), y: Math.round(N0), z: 0 } },
    clipPlanes: [],
    renderer: { pixelRatio: 1 },
    cursorRay: () => null,
    projectView: () => null,
    setTool: (t: ToolHandler | null) => tools.install(t),
    setDrawing: (state: { stage: string }) => void tools.suspend(state.stage !== "off"),
    addLayer: () => undefined,
    removeLayer: () => undefined,
    layerChanged: () => undefined,
    invalidate: () => undefined,
    view: () => undefined,
    onFrame: () => () => undefined,
    onRestored: () => () => undefined,
  };
  return scene as unknown as typeof scene & Scene;
}

/** A session looking straight down on a 200 x 200 m patch (400 x 400 px), tied to the scene. */
function attached(scene: Scene): { s: EditSession; detach: () => void } {
  const s = new EditSession(asLoaded(1, 3, emptyLayer()), "РУДА", "#ff8800");
  const detach = attachSession(s, scene);
  const transform = { matrix: ortho(E0, E0 + 200, N0, N0 + 200, -2000, 2000), width: 400, height: 400 };
  s.view = { transform: () => transform, ray: (cx, cy) => ({ origin: [E0 + cx / 2, N0 + 200 - cy / 2, 1000], dir: [0, 0, -1] }) };
  s.zLevel = 250;
  s.viewChanged();
  return { s, detach };
}

describe("ToolChain: a general tool holds the pointer", () => {
  it("tells the tool to end what it holds once, feeds it nothing meanwhile, and gives the pointer back", () => {
    const chain = new ToolChain();
    const got: string[] = [];
    chain.tool = { pointer: (e) => (got.push(e.type), true), deactivate: () => void got.push("deactivate") };
    chain.handles = { pointer: (e) => (got.push(`h:${e.type}`), true) };
    expect(chain.suspend(true)).toBe(true);
    expect(chain.suspend(true)).toBe(false); // the second click of the line: nothing again
    expect(got.filter((g) => g === "deactivate")).toHaveLength(1);
    got.length = 0;
    expect([chain.pointer(ptr("down")), chain.pointer(ptr("click")), chain.pointer(ptr("move", 1, 2, -1)), chain.pointer(ptr("dblclick"))]).toEqual([false, false, false, false]);
    expect(got).toEqual([]);
    chain.suspend(false);
    got.length = 0;
    expect(chain.pointer(ptr("down"))).toBe(true);
    expect(got).toEqual(["down"]);
  });

  it("a tool replaced by another one is told to end too", () => {
    const chain = new ToolChain();
    const ended = vi.fn();
    const first = { pointer: () => true, deactivate: ended };
    chain.install(first);
    chain.install(first);
    expect(ended).not.toHaveBeenCalled();
    chain.install({ pointer: () => true });
    expect(ended).toHaveBeenCalledTimes(1);
  });
});

describe("the editor under the section line", () => {
  async function drawPolyline(s: EditSession, points: [number, number][]) {
    for (const [x, y] of points) {
      s.pointer(at(x, y));
      await tick();
    }
  }

  it("a polyline of two points and more is finished and kept, the tool is «Выбор», the section line gets the clicks", async () => {
    const scene = fakeScene();
    const { s, detach } = attached(scene);
    s.setTool("line");
    await drawPolyline(s, [[20, 300], [120, 300], [200, 250]]);
    expect(s.drawing).not.toBeNull();
    expect(s.layer.lines).toHaveLength(1);
    scene.setDrawing(startDraw()); // «Провести разрез»
    expect(s.drawing).toBeNull();
    expect(s.tool).toBe("select");
    expect(s.layer.lines).toHaveLength(1);
    expect(s.layer.lines[0].points).toHaveLength(3);
    // the click now is the section's: the tool of the editor does not take it
    expect(scene.chain.pointer(ptr("down", 50, 50))).toBe(false);
    expect(scene.chain.pointer(ptr("click", 50, 50))).toBe(false);
    expect(s.layer.lines).toHaveLength(1);
    // Ctrl+Z after the finish still takes the steps back
    s.undo();
    expect(s.layer.lines[0].points).toHaveLength(2);
    // the section is drawn: the tool of the editor is back
    scene.setDrawing(DRAW_OFF);
    expect(scene.chain.pointer(ptr("down", 60, 60))).toBe(false); // «Выбор»: an empty place, the view turns
    detach();
  });

  it("an empty polyline and one with a single point are cancelled without a trace", async () => {
    const scene = fakeScene();
    const { s, detach } = attached(scene);
    s.setTool("polygon");
    scene.setDrawing(startDraw());
    expect(s.tool).toBe("select");
    expect(s.layer.lines).toHaveLength(0);
    scene.setDrawing(DRAW_OFF);
    s.setTool("line");
    await drawPolyline(s, [[20, 300]]);
    expect(s.layer.lines).toHaveLength(1);
    scene.setDrawing(startDraw());
    expect(s.drawing).toBeNull();
    expect(s.layer.lines).toHaveLength(0);
    detach();
  });

  it("closing the editor does not finish anything through the tool that goes", async () => {
    const scene = fakeScene();
    const { s, detach } = attached(scene);
    s.setTool("line");
    await drawPolyline(s, [[20, 300], [120, 300]]);
    const edits = s.layer;
    detach();
    expect(s.layer).toBe(edits);
    expect(s.tool).toBe("line");
  });
});

describe("closing the project while a section line is drawn (Э27, 27.10)", () => {
  it("calls the drawing off in the scene too: the pointer hold is let go and the editor's tool gets the pointer again", () => {
    createApp({ persist: false });
    const scene = fakeScene();
    const seen: string[] = [];
    scene.chain.tool = { pointer: (e) => (seen.push(e.type), true) };
    setCurrentScene(scene);
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    const draw = startDraw();
    sectionStore.set({ draw });
    scene.setDrawing(draw);
    expect(scene.chain.suspended).toBe(true);
    expect(scene.chain.pointer(ptr("down"))).toBe(false);

    projectStore.set({ current: null, version: 0 });

    expect(sectionStore.get().draw).toEqual(DRAW_OFF);
    expect(scene.chain.suspended).toBe(false);
    expect(scene.chain.pointer(ptr("down"))).toBe(true);
    expect(seen).toEqual(["down"]);
    setCurrentScene(null);
  });
});
