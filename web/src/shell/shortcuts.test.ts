import { describe, expect, it } from "vitest";
import { parseAttr } from "./AttributeEditor";
import { buildRibbon, flattenRibbon } from "./ribbonConfig";
import { applyShortcuts, assign, functionKey, isComplete } from "./shortcuts";
import { DEFAULT_WORKSPACE } from "./workspace";

const ctx = { hasHole: false, hasSet: true, hasSection: false };

describe("hotkeys set by the user", () => {
  it("a toggle is one function whatever its state", () => {
    expect(functionKey({ command: "scene.style", args: { grid: true } })).toBe(functionKey({ command: "scene.style", args: { grid: false } }));
    expect(functionKey({ command: "scene.style", args: { grid: true } })).not.toBe(functionKey({ command: "scene.style", args: { perspective: true } }));
  });

  it("replaces, removes and moves shortcuts", () => {
    const tabs = buildRibbon(DEFAULT_WORKSPACE, ctx);
    const defaults = Object.fromEntries(flattenRibbon(tabs).map((i) => [functionKey(i), i.shortcut ?? ""]));
    const fit = functionKey({ command: "scene.fit" });
    const top = functionKey({ command: "scene.view", args: { view: "top" } });
    expect(defaults[fit]).toBe("f");
    // «В план» gets F: «Вписать» loses it.
    const next = assign({}, defaults, defaults, top, "f");
    expect(next).toEqual({ [top]: "f", [fit]: "" });
    const items = flattenRibbon(applyShortcuts(tabs, next));
    expect(items.find((i) => i.command === "scene.fit")?.shortcut).toBeUndefined();
    expect(items.filter((i) => i.shortcut === "f").map((i) => i.label)).toEqual(["В план"]);
    // back to the default: the entry is dropped
    expect(assign({ [top]: "q" }, defaults, defaults, top, "t")).toEqual({});
  });

  it("the settings button is on the home tab after the assistant", () => {
    const home = buildRibbon(DEFAULT_WORKSPACE, ctx)[0];
    const titles = home.groups.map((g) => g.title);
    expect(titles.indexOf("Настройка")).toBe(titles.indexOf("Ассистент") + 1);
    expect(isComplete("ctrl+control")).toBe(false);
    expect(isComplete("ctrl+k")).toBe(true);
  });
});

describe("attribute values typed by the user", () => {
  it("keeps the kind of the old value", () => {
    expect(parseAttr("12,5", 3)).toBe(12.5);
    expect(parseAttr("12,5", "текст")).toBe("12,5");
    expect(parseAttr("да", false)).toBe(true);
    expect(parseAttr("  ", "x")).toBeNull();
    expect(parseAttr("7", undefined)).toBe(7);
    expect(parseAttr("руда", undefined)).toBe("руда");
  });
});
