import { describe, expect, it } from "vitest";
import { TREE_GROUPS, moveGroup, normalizeOrder } from "./treeOrder";

describe("order of the data tree's groups", () => {
  it("moves a group before or after another", () => {
    const order = [...TREE_GROUPS];
    expect(moveGroup(order, "cat:blocks", "cat:drillholes", false)[0]).toBe("cat:blocks");
    expect(moveGroup(order, "cat:drillholes", "cat:lines", true)).toEqual(["cat:wireframe", "cat:dtm", "cat:lines", "cat:drillholes", "cat:rasters", "cat:blocks"]);
    expect(moveGroup(order, "cat:lines", "cat:lines", true)).toBe(order);
    expect(moveGroup(order, "cat:nothing", "cat:lines", true)).toBe(order);
  });

  it("repairs a stored order", () => {
    expect(normalizeOrder(null)).toEqual([...TREE_GROUPS]);
    // Stored before ЦТМ existed: it lands after the wireframes, wherever they are.
    expect(normalizeOrder(["cat:blocks", "cat:wireframe", "cat:drillholes", "cat:lines", "cat:rasters"]))
      .toEqual(["cat:blocks", "cat:wireframe", "cat:dtm", "cat:drillholes", "cat:lines", "cat:rasters"]);
    expect(normalizeOrder(["cat:lines", "cat:lines", "x", 5])).toEqual(["cat:drillholes", "cat:wireframe", "cat:dtm", "cat:lines", "cat:rasters", "cat:blocks"]);
  });
});
