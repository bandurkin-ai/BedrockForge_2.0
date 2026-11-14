import { describe, expect, it } from "vitest";
import { commandTools, findByToolName, formatShortcut, matchesQuery, toolName, validateArgs, type Command } from "./commands";
import { buildRibbon, flattenRibbon } from "./ribbon";
import { DEFAULT_WORKSPACE, normalize, resize, setLayout, setPane, setPanel } from "./workspace";

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
  it("keeps the active pane inside the layout", () => {
    const quad = { ...setLayout(DEFAULT_WORKSPACE, "quad"), active: 3 };
    expect(setLayout(quad, "columns").active).toBe(1);
    expect(setLayout(quad, "single").active).toBe(0);
  });

  it("changes one pane and ignores bad indexes", () => {
    expect(setPane(DEFAULT_WORKSPACE, 1, "audit").panes).toEqual(["view3d", "audit", "section", "log"]);
    expect(setPane(DEFAULT_WORKSPACE, 4, "audit")).toBe(DEFAULT_WORKSPACE);
    expect(setPane(DEFAULT_WORKSPACE, 0, "view3d")).toBe(DEFAULT_WORKSPACE);
  });

  it("shares the right dock between properties and the assistant", () => {
    const assistant = setPanel(DEFAULT_WORKSPACE, "assistant");
    expect([assistant.right, assistant.rightTab]).toEqual([true, "assistant"]);
    expect(setPanel(assistant, "assistant").right).toBe(false); // toggling the shown tab closes the dock
    expect(setPanel(assistant, "properties").rightTab).toBe("properties"); // the other tab switches instead
    expect(setPanel(assistant, "properties", false)).toBe(assistant); // closing a tab that is not shown changes nothing
  });

  it("clamps dock sizes", () => {
    expect(resize(DEFAULT_WORKSPACE, "left", 10).sizes.left).toBe(180);
    expect(resize(DEFAULT_WORKSPACE, "bottom", 5000).sizes.bottom).toBe(600);
  });

  it("repairs stored state from an older version", () => {
    const state = normalize({ layout: "tabs", panes: ["plan", "gone"], active: 9, theme: "light", sizes: { left: 9999 }, bottom: "yes" });
    expect(state.layout).toBe("single");
    expect(state.panes).toEqual(["plan", "plan", "section", "log"]);
    expect(state.active).toBe(0);
    expect(state.theme).toBe("light");
    expect(state.sizes.left).toBe(480);
    expect(state.bottom).toBe(false);
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
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, { hasHole: false, hasData: true }));
    const views = items.filter((i) => i.command === "view.open" && i.args?.view === "view3d");
    expect(views).toHaveLength(1);
    expect(views[0].active).toBe(true);
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
