import { describe, expect, it } from "vitest";
import { commandTools, findByToolName, formatShortcut, matchesQuery, toolName, validateArgs, type Command } from "./commands";
import { buildRibbon, flattenRibbon } from "./ribbonConfig";
import { DEFAULT_WORKSPACE, isShown, layerKey, normalize, openBottom, resize, setPanel, setShown } from "./workspace";

const open: Command = {
  id: "view.open",
  title: "Открыть вид",
  description: "Показывает вид.",
  params: {
    view: { type: "string", enum: ["plan", "view3d"] },
    window: { type: "integer", enum: [1, 2] },
    hole_id: { type: "string", nullable: true },
  },
  required: ["view"],
  run: () => "ok",
};
const hidden: Command = { id: "palette.open", title: "Палитра", description: "", uiOnly: true, run: () => undefined };

describe("workspace", () => {
  it("shares the right dock between properties and the assistant", () => {
    const assistant = setPanel(DEFAULT_WORKSPACE, "assistant");
    expect([assistant.right, assistant.rightTab]).toEqual([true, "assistant"]);
    expect(setPanel(assistant, "assistant").right).toBe(false); // toggling the shown tab closes the dock
    expect(setPanel(assistant, "properties").rightTab).toBe("properties"); // the other tab switches instead
    expect(setPanel(assistant, "properties", false)).toBe(assistant); // closing a tab that is not shown changes nothing
  });

  it("opens a bottom tab together with the dock", () => {
    expect(openBottom(DEFAULT_WORKSPACE, "audit")).toMatchObject({ bottom: true, bottomTab: "audit" });
  });

  it("hides and shows layers by key", () => {
    const hidden = setShown(DEFAULT_WORKSPACE, layerKey.wireframe(7), false);
    expect(isShown(hidden, "wf:7")).toBe(false);
    expect(isShown(hidden, layerKey.set(7))).toBe(true);
    expect(setShown(setShown(hidden, "wf:7", false), "wf:7", false).hidden).toEqual(["wf:7"]); // no duplicates
    expect(isShown(setShown(hidden, "wf:7", true), "wf:7")).toBe(true);
  });

  it("clamps dock sizes", () => {
    expect(resize(DEFAULT_WORKSPACE, "left", 10).sizes.left).toBe(200);
    expect(resize(DEFAULT_WORKSPACE, "bottom", 5000).sizes.bottom).toBe(640);
  });

  it("repairs stored state from an older version", () => {
    const state = normalize({ mode: "plan", clip: "yes", perspective: true, layout: "quad", theme: "light", sizes: { left: 9999 }, bottom: "yes", vexag: 7, hidden: ["wf:1", 5], wireframeOpacity: 3 });
    expect(state).not.toHaveProperty("mode"); // the window is always 3D
    expect([state.clip, state.perspective]).toEqual([false, true]);
    expect(state.theme).toBe("light");
    expect(state.sizes.left).toBe(520);
    expect(state.bottom).toBe(false);
    expect(state.vexag).toBe(1);
    expect(state.hidden).toEqual(["wf:1"]);
    expect(state.wireframeOpacity).toBe(DEFAULT_WORKSPACE.wireframeOpacity);
    expect(normalize(null)).toBe(DEFAULT_WORKSPACE);
  });
});

describe("commands as Claude tools", () => {
  it("exports only commands Claude may run, with a strict schema", () => {
    const tools = commandTools([open, hidden]);
    expect(tools).toHaveLength(1);
    expect(tools[0].name).toBe("ui_view_open");
    expect(tools[0].input_schema).toEqual({
      type: "object",
      properties: {
        view: { type: "string", enum: ["plan", "view3d"] },
        window: { type: "integer", enum: [1, 2] },
        hole_id: { type: ["string", "null"] },
      },
      required: ["view"],
      additionalProperties: false,
    });
  });

  it("finds a command by tool name but never a UI-only one", () => {
    expect(findByToolName([open, hidden], "ui_view_open")).toBe(open);
    expect(findByToolName([open, hidden], toolName(hidden.id))).toBeUndefined();
  });

  it("validates arguments that come from outside", () => {
    expect(validateArgs(open, { view: "plan", window: 2, hole_id: null })).toBeNull();
    expect(validateArgs(open, {})).toContain("view");
    expect(validateArgs(open, { view: "audit" })).toContain("допустимые значения");
    expect(validateArgs(open, { view: "plan", window: 1.5 })).toContain("integer");
    expect(validateArgs(open, { view: "plan", extra: 1 })).toContain("extra");
    expect(validateArgs(open, { view: null })).toContain("не может быть пустым");
    expect(validateArgs(open, "plan")).not.toBeNull();
  });
});

describe("ribbon and palette", () => {
  it("lists each button once and marks the active view", () => {
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, { hasHole: false, hasSet: true, hasSection: false }));
    const views = items.filter((i) => i.command === "scene.view");
    expect(views.map((i) => i.args?.view)).toEqual(["top", "front", "side", "iso", "section"]);
    expect(items.find((i) => i.command === "section.clip")?.disabled).toBe(true); // no section line yet
    expect(items.find((i) => i.command === "hole.select")?.disabled).toBe(true);
    const shortcuts = items.map((i) => i.shortcut).filter(Boolean);
    expect(new Set(shortcuts).size).toBe(shortcuts.length); // no two buttons share a hotkey
  });

  it("matches every word of the query and formats hotkeys", () => {
    expect(matchesQuery("Четыре окна Вид › Компоновка", "окна комп")).toBe(true);
    expect(matchesQuery("Четыре окна", "окна план")).toBe(false);
    expect(formatShortcut("ctrl+k")).toBe("Ctrl+K");
    expect(formatShortcut("escape")).toBe("Esc");
  });
});
