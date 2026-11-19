import { afterEach, describe, expect, it, vi } from "vitest";
import { planeDistance, sectionPlane } from "../../src/edit/coords";
import { asLoaded } from "../../src/edit/io";
import { emptyLayer, type EditLayer, type Vec3 } from "../../src/edit/model";
import { readSetSnap } from "../../src/edit/snap";
import { EditSession } from "../../src/edit/session";
import * as cmd from "../../src/edit/tools";
import type { EditPointer } from "../../src/edit/tools";
import { decodeBfa1 } from "../../src/viz/format/bfa1";
import type { SectionLine } from "../../src/viz/section/math";
import { build } from "../bfa1-build";
import { lcg, ortho } from "./testUtil";

const E0 = 512345.678901234;
const N0 = 6789012.345678901;

/** Straight down on a 200 × 200 m patch; the screen is 400 × 400 px (2 px per metre). */
function planSession(layer: EditLayer = emptyLayer()): EditSession {
  const s = new EditSession(asLoaded(1, 3, layer), "РУДА", "#ff8800");
  const transform = { matrix: ortho(E0, E0 + 200, N0, N0 + 200, -2000, 2000), width: 400, height: 400 };
  s.view = { transform: () => transform, ray: (cx, cy) => ({ origin: [E0 + cx / 2, N0 + 200 - cy / 2, 1000], dir: [0, 0, -1] }) };
  s.zLevel = 250;
  s.viewChanged();
  return s;
}
const at = (x: number, y: number, type: EditPointer["type"] = "click", extra: Partial<EditPointer> = {}): EditPointer => ({ type, x, y, clientX: x, clientY: y, button: 0, shift: false, ctrl: false, ...extra });
const tick = () => new Promise((r) => setTimeout(r, 0));
const key = (k: string, ctrl = false, shift = false) => ({ key: k, ctrl, shift });
const px = (p: Vec3): [number, number] => [(p[0] - E0) * 2, (N0 + 200 - p[1]) * 2];

async function draw(s: EditSession, pts: [number, number][]): Promise<void> {
  for (const [x, y] of pts) {
    s.pointer(at(x, y));
    await tick();
  }
}

afterEach(() => vi.useRealTimers());

describe("ending and correcting a line (ТЗ 7.22 «Клавиши»)", () => {
  it("Enter ends the line; without a line it is not the editor's", async () => {
    const s = planSession();
    s.setTool("line");
    await draw(s, [[100, 300], [300, 300], [300, 100]]);
    expect(s.key(key("Enter"))).toBe("lines.finish");
    expect(s.drawing).toBeNull();
    expect(s.layer.lines[0].points).toHaveLength(3);
    expect(s.key(key("Enter"))).toBeNull();
  });

  it("Enter ends a polygon closed", async () => {
    const s = planSession();
    s.setTool("polygon");
    await draw(s, [[100, 300], [300, 300], [300, 100]]);
    s.key(key("Enter"));
    expect(s.layer.lines[0].closed).toBe(true);
  });

  it("a double click ends the line without a doubled last point", async () => {
    const s = planSession();
    s.setTool("line");
    await draw(s, [[100, 300], [300, 300], [300, 100], [300, 100]]);
    expect(s.pointer(at(300, 100, "dblclick"))).toBe(true);
    expect(s.drawing).toBeNull();
    expect(s.layer.lines[0].points).toEqual([[E0 + 50, N0 + 50, 250], [E0 + 150, N0 + 50, 250], [E0 + 150, N0 + 150, 250]]);
    // a polygon closes on a double click
    s.setTool("polygon");
    await draw(s, [[20, 380], [60, 380], [60, 340], [60, 340]]);
    s.pointer(at(60, 340, "dblclick"));
    expect(s.layer.lines[1]).toMatchObject({ closed: true });
    expect(s.layer.lines[1].points).toHaveLength(3);
  });

  it("a right click without a drag ends the line; with nothing drawn it is the view's", async () => {
    const s = planSession();
    s.setTool("line");
    await draw(s, [[100, 300], [300, 300]]);
    expect(s.pointer(at(10, 10, "click", { button: 2 }))).toBe(true);
    expect(s.drawing).toBeNull();
    expect(s.pointer(at(10, 10, "click", { button: 2 }))).toBe(false);
  });

  it("Backspace takes the last point off; the line goes with its only point", async () => {
    const s = planSession();
    s.setTool("line");
    await draw(s, [[100, 300], [300, 300], [300, 100]]);
    expect(s.key(key("Backspace"))).toBe("lines.drop_point");
    expect(s.layer.lines[0].points).toHaveLength(2);
    expect(s.drawing).not.toBeNull();
    // drawing goes on from the new last point
    await draw(s, [[200, 200]]);
    expect(s.layer.lines[0].points[2]).toEqual([E0 + 100, N0 + 100, 250]);
    s.key(key("Backspace"));
    s.key(key("Backspace"));
    s.key(key("Backspace"));
    expect(s.layer.lines).toHaveLength(0);
    expect(s.drawing).toBeNull();
    expect(s.key(key("Backspace"))).toBeNull();
    // every point was a step: undo brings them back one by one
    s.undo();
    expect(s.layer.lines[0].points).toHaveLength(1);
  });

  it("Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z, Delete, Esc, Ctrl+S in both layouts", async () => {
    const s = planSession();
    s.setTool("line");
    await draw(s, [[100, 300], [300, 300]]);
    s.key(key("Enter"));
    expect(s.key(key("z", true))).toBe("lines.undo");
    expect(s.layer.lines[0].points).toHaveLength(1);
    expect(s.key(key("Я", true))).toBe("lines.undo");
    expect(s.layer.lines).toHaveLength(0);
    expect(s.key(key("y", true))).toBe("lines.redo");
    expect(s.key(key("Z", true, true))).toBe("lines.redo");
    expect(s.layer.lines[0].points).toHaveLength(2);
    cmd.selectAll(s);
    expect(s.key(key("Delete"))).toBe("lines.delete");
    expect(s.layer.lines).toHaveLength(0);
    expect(s.key(key("s", true))).toBe("lines.save");
    expect(s.key(key("ы", true))).toBe("lines.save");
    s.setTool("polygon");
    expect(s.key(key("Escape"))).toBe("lines.escape");
    expect(s.tool).toBe("select");
    expect(s.key(key("q"))).toBeNull();
  });
});

describe("acceptance (ТЗ 7.22): a snap to a collar on a section is a point in the section plane", () => {
  it("≤ 1e−9 m from the plane at UTM, any azimuth; the true collar with projection off", async () => {
    const rnd = lcg(725);
    const s = planSession();
    s.clip = true;
    s.setTool("point");
    // only the collars: the points put by the loop are vertices of the working copy near the cursor too
    s.snap = { ...s.snap, kinds: ["collar"] };
    let worst = 0;
    for (let i = 0; i < 300; i++) {
      const collar: Vec3 = [E0 + 20 + rnd() * 160, N0 + 20 + rnd() * 160, 200 + rnd() * 300];
      const buf = build({ kind: "snap", format: 1, set: 1, holes: 1, names: [`DH-${i}`], truncated: false }, [["collars", "<f8", Float64Array.from(collar)]]);
      for (const src of readSetSnap("set:1", buf).sources) s.snapIndex.setSource(src);
      const section: SectionLine = { x0: collar[0] + (rnd() - 0.5) * 40, y0: collar[1] + (rnd() - 0.5) * 40, az: rnd() * 360, length: null, towards: rnd() * 50, away: rnd() * 50 };
      s.section = section;
      s.snap = { ...s.snap, project: true };
      const [x, y] = px(collar);
      s.pointer(at(x + 1.5, y - 2));
      await tick();
      const p = s.layer.lines[s.layer.lines.length - 1].points[0];
      worst = Math.max(worst, Math.abs(planeDistance(p, sectionPlane(section))));
      expect(p[2]).toBe(collar[2]);
      s.snap = { ...s.snap, project: false };
      s.pointer(at(x + 1.5, y - 2));
      await tick();
      expect(s.layer.lines[s.layer.lines.length - 1].points[0]).toEqual(collar);
    }
    console.log(`9.25 collar on a section, 300 random UTM sections: worst distance to the plane ${worst.toExponential(2)} m`);
    expect(worst).toBeLessThanOrEqual(1e-9);
  });
});

describe("saving", () => {
  const stored = (): EditLayer => ({ lines: [{ id: 1, points: [[E0, N0, 1], [E0 + 10, N0, 2], [E0 + 10, N0 + 10, 3]], closed: true, attrs: { code: "A" }, src: 0 }], nextId: 2 });

  it("sends a new line, takes its lid after the save, and then sends it by reference", async () => {
    const s = planSession(stored());
    expect(s.summary()).toMatchObject({ lines: 1, touched: 0 });
    expect(cmd.blocked(s, "lines.save")).toBe("Изменений нет");
    s.setTool("tie");
    await draw(s, [[100, 300], [300, 300]]);
    expect(s.drawing).toBeNull();
    expect(s.layer.lines[1].attrs).toEqual({ ROLE: "TIE" });
    expect(cmd.blocked(s, "lines.save")).toBeNull();
    const first = decodeBfa1(s.saveBody("тай-линия"));
    expect(Array.from(first.arrays.src as Int32Array)).toEqual([0, -1]);
    expect(first.header.new_attrs).toEqual({ "1": { ROLE: "TIE" } });
    // the core saves version 4 and gives the new line lid 1
    s.saved(asLoaded(1, 4, s.layer));
    expect(s.baseVersion).toBe(4);
    expect(s.dirty).toBe(false);
    expect(s.message).toContain("версия 4");
    const second = decodeBfa1(s.saveBody());
    expect(Array.from(second.arrays.src as Int32Array)).toEqual([0, 1]);
    expect(Array.from(second.arrays.touched as Uint8Array)).toEqual([0, 0]);
    expect(second.header).toMatchObject({ base_version: 4, new_attrs: {} });
    // the step before the save is still there
    s.undo();
    expect(s.layer.lines[1].points).toHaveLength(1);
    expect(s.dirty).toBe(true);
  });

  it("moves the selected stored lines and takes the source back without them", () => {
    const s = planSession(stored());
    cmd.addLine(s, [[E0, N0, 0], [E0 + 1, N0, 0]], false);
    cmd.selectLines(s, [2]);
    expect(() => s.moveBody(9, 2)).toThrow(/новые линии/);
    cmd.selectLines(s, [1]);
    const body = decodeBfa1(s.moveBody(9, 2));
    expect(Array.from(body.arrays.moved_src as Int32Array)).toEqual([0]);
    expect(Array.from(body.arrays.src as Int32Array)).toEqual([-1]);
    const left = asLoaded(1, 4, { lines: [{ ...s.layer.lines[1], src: 1 }], nextId: 3 });
    s.saved(left, [1]);
    expect(s.layer.lines.map((l) => l.id)).toEqual([2]);
    expect(s.dirty).toBe(false);
  });
});

describe("panel and probe", () => {
  it("names why a command is not available", () => {
    const s = planSession();
    expect(cmd.blocked(s, "lines.undo")).toBe("Отменять нечего");
    expect(cmd.blocked(s, "lines.join")).toMatch(/две/);
    expect(cmd.blocked(s, "lines.break")).toMatch(/одну её вершину/);
    expect(cmd.blocked(s, "lines.offset")).toMatch(/выберите линию/);
    expect(cmd.blocked(s, "lines.finish")).toBe("Линия не рисуется");
    const id = cmd.addLine(s, [[E0, N0, 0], [E0 + 1, N0, 0]], false);
    cmd.selectLines(s, [id]);
    expect(cmd.blocked(s, "lines.close")).toMatch(/трёх вершин/);
    expect(cmd.blocked(s, "lines.offset")).toBeNull();
    expect(cmd.TOOLS.map((t) => t.id)).toEqual(["select", "line", "polygon", "point", "tie", "insert"]);
  });

  it("asks the core for the wireframe under the cursor after a 30 ms pause and aborts a stale request", async () => {
    vi.useFakeTimers();
    const s = planSession();
    const signals: AbortSignal[] = [];
    s.probe = (_ray, signal) => {
      signals.push(signal);
      return new Promise(() => {});
    };
    s.snap = { ...s.snap, kinds: [...s.snap.kinds, "wf-vertex"] };
    s.pointer(at(100, 100, "move"));
    vi.advanceTimersByTime(20);
    s.pointer(at(110, 100, "move"));
    vi.advanceTimersByTime(29);
    expect(signals).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(signals).toHaveLength(1);
    s.pointer(at(150, 100, "move"));
    vi.advanceTimersByTime(30);
    expect(signals).toHaveLength(2);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it("drags a vertex without a height in plan: it stays without one", () => {
    const s = planSession({ lines: [{ id: 1, points: [[E0 + 50, N0 + 50, NaN], [E0 + 100, N0 + 50, 7]], closed: false, attrs: {}, src: 0 }], nextId: 2 });
    s.snap = { ...s.snap, enabled: false };
    expect(s.pointer(at(100, 300, "down"))).toBe(true);
    s.pointer(at(140, 260, "move"));
    s.pointer(at(140, 260, "up"));
    const p = s.layer.lines[0].points[0];
    expect([p[0], p[1]]).toEqual([E0 + 70, N0 + 70]);
    expect(Number.isNaN(p[2])).toBe(true);
  });
});
