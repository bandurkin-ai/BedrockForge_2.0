import { afterEach, describe, expect, it } from "vitest";
import { asLoaded, layerOf } from "../src/edit/io";
import { EditSession } from "../src/edit/session";
import { TOOLS } from "../src/edit/tools";
import { createApp } from "../src/shell/app";
import { enterCoordinates } from "../src/modules/lines/CoordInput";
import { editStore } from "../src/modules/lines/editState";
import { editorKeyAllowed, isTyping, toolKey } from "../src/modules/lines/editKeys";
import { buttonState, PANEL, PANEL_COMMANDS, tooltip } from "../src/modules/lines/editTools";
import { MODIFY } from "../src/modules/lines/modify";
import { toggledKinds } from "../src/modules/lines/SnapPanel";

function session(): EditSession {
  const layer = layerOf([{ points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]] }, { points: [[20, 0, 0], [30, 0, 0]] }]);
  return new EditSession(asLoaded(5, 2, layer), "Контуры");
}

afterEach(() => editStore.set({ session: null }));

describe("the panel of the editor", () => {
  it("every button has a title, what it does, its keys; an unavailable one tells why", () => {
    const s = session();
    const none = () => null;
    for (const b of PANEL) {
      expect(b.title.length, b.id).toBeGreaterThan(2);
      expect(b.action.length, b.id).toBeGreaterThan(8);
      expect(typeof b.keys).toBe("string");
      const tip = tooltip(b, buttonState(b, s, none));
      expect(tip).toContain(b.title.replace("…", ""));
      expect(tip).toContain(b.action);
      if (b.keys) expect(tip).toContain(`(${b.keys})`);
    }
    const close = PANEL.find((b) => b.id === "modify-close")!;
    const st = buttonState(close, s, none);
    expect(st.reason).toMatch(/Сначала выберите линию/);
    expect(tooltip(close, st)).toMatch(/Недоступно: Сначала выберите линию/);
    expect(buttonState(PANEL.find((b) => b.id === "undo")!, s, none).reason).toBe("Отменять нечего");
    expect(buttonState(PANEL.find((b) => b.id === "save")!, s, none).reason).toBe("Изменений нет");
    expect(buttonState(PANEL.find((b) => b.id === "tool-select")!, s, none)).toEqual({ reason: null, active: true });
    s.setSelection({ lines: [1], vertices: [] });
    expect(buttonState(close, s, none).reason).toBeNull();
    expect(buttonState(PANEL.find((b) => b.id === "modify-join")!, s, none).reason).toMatch(/ровно две/);
    // the reason of the registry goes first
    expect(buttonState(close, s, () => "Проект открыт только для чтения").reason).toBe("Проект открыт только для чтения");
    expect(PANEL.find((b) => b.id === "tool-line")!.keys).toBe("L");
    expect(PANEL.find((b) => b.id === "modify-delete")!.keys).toBe("Delete");
  });

  it("every command of the editor has a button, every button a command of the registry", () => {
    const app = createApp({ persist: false });
    for (const id of PANEL_COMMANDS) {
      const c = app.registry.get(id);
      expect(c, id).toBeDefined();
      expect(c!.stage, id).toBeUndefined(); // bound: not «появится на этапе Э9»
      expect(PANEL.some((b) => b.command === id), id).toBe(true);
    }
    for (const b of PANEL) expect(app.registry.get(b.command), b.command).toBeDefined();
    for (const t of TOOLS) expect(PANEL.some((b) => b.command === "lines.tool" && b.args?.tool === t.id), t.id).toBe(true);
    for (const m of MODIFY) expect(PANEL.some((b) => b.command === "lines.modify" && b.args?.action === m.id), m.id).toBe(true);
    for (const id of ["lines.new_layer", "lines.edit", "lines.add_line", "lines.snap"]) expect(app.registry.get(id)!.stage, id).toBeUndefined();
    expect(app.registry.get("lines.move_selected")!.title).toBe("Перенести в другой слой");
    expect(new Set(PANEL.map((b) => b.id)).size).toBe(PANEL.length);
  });

  it("keys of the editor do not work in fields of input, outside the editing mode, under a dialog or out of the 3D window", () => {
    const ok = { editing: true, typing: false, modal: false, focused: true };
    expect(editorKeyAllowed(ok)).toBe(true);
    expect(editorKeyAllowed({ ...ok, typing: true })).toBe(false);
    expect(editorKeyAllowed({ ...ok, editing: false })).toBe(false);
    expect(editorKeyAllowed({ ...ok, modal: true })).toBe(false);
    expect(editorKeyAllowed({ ...ok, focused: false })).toBe(false);
    expect(isTyping({ tagName: "INPUT" } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: "CANVAS" } as unknown as EventTarget)).toBe(false);
    expect(toolKey({ key: "l", ctrl: false, shift: false })).toBe("line");
    expect(toolKey({ key: "д", ctrl: false, shift: false })).toBe("line"); // the Russian layout
    expect(toolKey({ key: "P", ctrl: false, shift: true })).toBe("polygon");
    expect(toolKey({ key: "l", ctrl: true, shift: false })).toBeNull();
    expect(toolKey({ key: "Enter", ctrl: false, shift: false })).toBeNull();
    expect(toolKey({ key: "q", ctrl: false, shift: false })).toBeNull();
  });

  it("the field of coordinates: «@90 100» from (0, 0, 0) gives (100, 0, 0); a comma marks the decimals", () => {
    const s = session();
    expect(enterCoordinates(s, "0 0 0")).toMatch(/Выберите инструмент рисования/);
    s.setTool("line");
    expect(enterCoordinates(s, "0 0 0")).toBeNull();
    expect(enterCoordinates(s, "@90 100")).toBeNull();
    const line = s.layer.lines.at(-1)!;
    expect(line.points).toEqual([[0, 0, 0], [100, 0, 0]]);
    expect(enterCoordinates(s, "@@0 10,5")).toBeNull();
    expect(s.layer.lines.at(-1)!.points[2]).toEqual([100, 10.5, 0]);
    expect(enterCoordinates(s, "абв")).not.toBeNull();
    // a selected vertex gets the coordinates with the select tool
    s.setTool("select");
    s.setSelection({ lines: [], vertices: [{ line: 1, index: 0 }] });
    expect(enterCoordinates(s, "1 2 3")).toBeNull();
    expect(s.layer.lines[0].points[0]).toEqual([1, 2, 3]);
  });

  it("snap targets switched one by one keep the order of the list", () => {
    expect(toggledKinds(["vertex", "collar"], "midpoint")).toEqual(["vertex", "midpoint", "collar"]);
    expect(toggledKinds(["vertex", "collar"], "vertex")).toEqual(["collar"]);
  });
});
