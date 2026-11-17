import { describe, expect, it } from "vitest";
import { toggleExpanded, treeKey, type KeyRow } from "../src/shell/tree";

// Скважины ▾ / Набор А ▾ / отображение / Набор Б / Каркасы ▸
const rows: KeyRow[] = [{ level: 1, expanded: true }, { level: 2, expanded: true }, { level: 3 }, { level: 2 }, { level: 1, expanded: false }];

describe("tree keyboard (ТЗ 7.35 Д-03, WAI-ARIA tree)", () => {
  it("↑/↓, Home/End move between visible rows", () => {
    expect(treeKey("ArrowDown", rows, 0)).toEqual({ kind: "focus", index: 1 });
    expect(treeKey("ArrowDown", rows, 4)).toBeNull();
    expect(treeKey("ArrowUp", rows, 0)).toBeNull();
    expect(treeKey("ArrowUp", rows, 3)).toEqual({ kind: "focus", index: 2 });
    expect(treeKey("Home", rows, 3)).toEqual({ kind: "focus", index: 0 });
    expect(treeKey("End", rows, 0)).toEqual({ kind: "focus", index: 4 });
  });

  it("→ expands a collapsed row, enters an expanded one, nothing on a leaf", () => {
    expect(treeKey("ArrowRight", rows, 4)).toEqual({ kind: "expand", index: 4 });
    expect(treeKey("ArrowRight", rows, 1)).toEqual({ kind: "focus", index: 2 });
    expect(treeKey("ArrowRight", rows, 2)).toBeNull();
  });

  it("← collapses an expanded row, else goes to the parent", () => {
    expect(treeKey("ArrowLeft", rows, 1)).toEqual({ kind: "collapse", index: 1 });
    expect(treeKey("ArrowLeft", rows, 2)).toEqual({ kind: "focus", index: 1 });
    expect(treeKey("ArrowLeft", rows, 3)).toEqual({ kind: "focus", index: 0 });
    expect(treeKey("ArrowLeft", rows, 4)).toBeNull();
  });

  it("Enter selects, Space switches the check box; other keys do nothing", () => {
    expect(treeKey("Enter", rows, 3)).toEqual({ kind: "select", index: 3 });
    expect(treeKey(" ", rows, 2)).toEqual({ kind: "check", index: 2 });
    expect(treeKey("a", rows, 2)).toBeNull();
  });

  it("expanded state changes only by the arrow's toggle", () => {
    expect(toggleExpanded([], "holes")).toEqual(["holes"]);
    expect(toggleExpanded(["holes", "audit"], "holes")).toEqual(["audit"]);
  });
});
