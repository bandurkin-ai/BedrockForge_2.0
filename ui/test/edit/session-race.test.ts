/** Э9, подшаг 9.38 (ревью интерфейса): the session after a save of `sent` while the edits went on (finding 1), a
 * click that waited for the probe of the core (finding 6), the cursor leaving the 3D window (finding 7). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { asLoaded } from "../../src/edit/io";
import { emptyLayer, NEW_LINE, type EditLayer, type Vec3 } from "../../src/edit/model";
import * as ops from "../../src/edit/ops";
import { EditSession } from "../../src/edit/session";
import * as cmd from "../../src/edit/tools";
import type { EditPointer } from "../../src/edit/tools";
import { ToolInput, type ToolPointer } from "../../src/viz/core/toolInput";
import { ortho } from "./testUtil";

const E0 = 512345.678901234;
const N0 = 6789012.345678901;

function planSession(layer: EditLayer = emptyLayer()): EditSession {
  const s = new EditSession(asLoaded(1, 3, layer), "РУДА", "#ff8800");
  const transform = { matrix: ortho(E0, E0 + 200, N0, N0 + 200, -2000, 2000), width: 400, height: 400 };
  s.view = { transform: () => transform, ray: (cx, cy) => ({ origin: [E0 + cx / 2, N0 + 200 - cy / 2, 1000], dir: [0, 0, -1] }) };
  s.zLevel = 250;
  s.viewChanged();
  return s;
}
const at = (x: number, y: number, type: EditPointer["type"] = "click"): EditPointer => ({ type, x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false });
const tick = () => new Promise((r) => setTimeout(r, 0));
const P = (x: number, y: number): Vec3 => [E0 + x, N0 + y, 250];
const stored = (): EditLayer => ({
  lines: [
    { id: 1, points: [P(0, 0), P(10, 0)], closed: false, attrs: {}, src: 0 },
    { id: 2, points: [P(0, 20), P(10, 20)], closed: false, attrs: {}, src: 1 },
  ],
  nextId: 3,
});

afterEach(() => vi.useRealTimers());

describe("a save of `sent` while the edits went on (finding 1)", () => {
  it("a line changed after the send stays changed; a line saved new takes its number, also when changed since", () => {
    const s = planSession(stored());
    const made = cmd.addLine(s, [P(50, 50), P(60, 50)], false);
    s.edit(ops.moveVertex(s.layer, { line: 1, index: 0 }, P(1, 1)), "Перемещение вершины");
    const sent = s.layer;
    // during the PUT: line 2 and the new line are moved
    s.edit(ops.moveVertex(s.layer, { line: 2, index: 0 }, P(2, 22)), "Перемещение вершины");
    s.edit(ops.moveVertex(s.layer, { line: made, index: 1 }, P(70, 55)), "Перемещение вершины");
    s.saved(asLoaded(1, 4, sent), [], sent);
    expect(s.baseVersion).toBe(4);
    expect(s.dirty).toBe(true);
    expect(s.message).toMatch(/Правки, сделанные во время сохранения, ещё не записаны/);
    expect(s.layer.lines.map((l) => l.src)).toEqual([0, 1, 2]);
    expect(s.summary()).toMatchObject({ lines: 3, touched: 2, created: 0, removed: 0 });
    expect(s.layer.lines[1].points[0]).toEqual(P(2, 22));
    // the steps: undoing the two edits made during the save comes back to the saved state — clean
    s.undo();
    s.undo();
    expect(s.dirty).toBe(false);
    expect(s.summary().touched).toBe(0);
  });

  it("a line deleted in `sent` and brought back by an undo during the save is new to the store", () => {
    const s = planSession(stored());
    s.edit(ops.deleteLines(s.layer, [2]), "Удаление линий");
    const sent = s.layer;
    s.undo();
    s.saved(asLoaded(1, 4, sent), [], sent);
    expect(s.layer.lines.map((l) => [l.id, l.src])).toEqual([[1, 0], [2, NEW_LINE]]);
    expect(s.dirty).toBe(true);
    expect(s.summary()).toMatchObject({ created: 1, removed: 0 });
    s.redo();
    expect(s.dirty).toBe(false);
  });

  it("without edits meanwhile: as before — clean, the earlier steps undoable", () => {
    const s = planSession(stored());
    s.edit(ops.moveVertex(s.layer, { line: 1, index: 0 }, P(1, 1)), "Перемещение вершины");
    s.saved(asLoaded(1, 4, s.layer));
    expect(s.dirty).toBe(false);
    expect(s.message).toBe("Сохранено: версия 4");
    s.undo();
    expect(s.dirty).toBe(true);
  });
});

describe("a click that waited for the probe of the core (finding 6)", () => {
  function probing() {
    const s = planSession();
    const answers: ((v: null) => void)[] = [];
    s.probe = () => new Promise((r) => answers.push(r));
    s.snap = { ...s.snap, kinds: [...s.snap.kinds, "wf-vertex"] };
    s.setTool("line");
    return { s, answers };
  }

  it("the tool changed while the probe was on its way: no point", async () => {
    const { s, answers } = probing();
    s.pointer(at(100, 300));
    expect(answers).toHaveLength(1);
    s.setTool("polygon");
    answers[0](null);
    await tick();
    expect(s.layer.lines).toHaveLength(0);
    expect(s.message).toMatch(/Точка не поставлена/);
  });

  it("an undo while the probe was on its way: no point on the old line", async () => {
    const { s, answers } = probing();
    s.pointer(at(100, 300));
    answers[0](null);
    await tick();
    s.pointer(at(300, 300));
    answers[1](null);
    await tick();
    s.pointer(at(300, 100));
    s.undo();
    answers[2](null);
    await tick();
    expect(s.layer.lines[0].points).toHaveLength(1);
  });

  it("nothing changed: the point is put", async () => {
    const { s, answers } = probing();
    s.pointer(at(100, 300));
    answers[0](null);
    await tick();
    expect(s.layer.lines[0].points).toEqual([P(50, 50)]);
  });
});

describe("the cursor leaves the 3D window (finding 7)", () => {
  it("the tool gets `leave`, the scene too", () => {
    const scene = { click: vi.fn(), move: vi.fn(), leave: vi.fn(), camera: vi.fn() };
    const input = new ToolInput(scene);
    const seen: ToolPointer[] = [];
    input.setTool({ pointer: (e) => (seen.push(e), false) });
    input.feed({ type: "leave", x: 5, y: 6, clientX: 5, clientY: 6, button: 0, shift: false, ctrl: false, t: 0 });
    expect(scene.leave).toHaveBeenCalledTimes(1);
    expect(seen.map((e) => e.type)).toEqual(["leave"]);
  });

  it("the session drops its hover and the probe that waits for its pause", () => {
    vi.useFakeTimers();
    const s = planSession();
    const probe = vi.fn(() => new Promise<null>(() => {}));
    s.probe = probe;
    s.snap = { ...s.snap, kinds: [...s.snap.kinds, "wf-vertex"] };
    s.pointer(at(100, 100, "move"));
    expect(s.hover).not.toBeNull();
    expect(s.pointer(at(100, 100, "leave"))).toBe(false);
    expect(s.hover).toBeNull();
    vi.advanceTimersByTime(100);
    expect(probe).not.toHaveBeenCalled();
  });

  it("the probe already on its way is aborted", () => {
    vi.useFakeTimers();
    const s = planSession();
    const signals: AbortSignal[] = [];
    s.probe = (_ray, signal) => (signals.push(signal), new Promise(() => {}));
    s.snap = { ...s.snap, kinds: [...s.snap.kinds, "wf-vertex"] };
    s.pointer(at(100, 100, "move"));
    vi.advanceTimersByTime(30);
    expect(signals).toHaveLength(1);
    s.leave();
    expect(signals[0].aborted).toBe(true);
  });
});
