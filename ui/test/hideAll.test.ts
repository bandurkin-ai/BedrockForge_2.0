import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { clearedKeys, effectiveHidden, normalizeHideAll, showNew, showUnderHideAll } from "../src/modules/scene/hideAll";
import { shownDisplays } from "../src/modules/downhole/displayList";

const fixtures = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/view/hide_all.json"), "utf-8")) as {
  cases: { in: unknown; out: unknown }[];
};

describe("hide_all (ТЗ 7.35 Д-04, ADR 0008)", () => {
  it("normalizes as the core does (shared fixtures)", () => {
    for (const c of fixtures.cases) expect(normalizeHideAll(c.in)).toEqual(c.out);
  });

  it("off: the hidden list as it is", () => {
    expect(effectiveHidden(["set:1:collars"], { on: false, shown: [] }, ["set:1", "set:2"])).toEqual(["set:1:collars"]);
  });

  it("clear with 10 000 objects: everything hidden, stored keys ≤ 2000", () => {
    const objects = Array.from({ length: 10_000 }, (_, i) => `set:${i + 1}`);
    const cleared = clearedKeys([{ chart: 5, visible: true }, { chart: 6, visible: false }]);
    expect(cleared.displays).toEqual([{ chart: 5, visible: false }, { chart: 6, visible: false }]);
    const stored = JSON.stringify(cleared.hide_all).length + JSON.stringify(cleared.hidden).length;
    expect(cleared.hide_all.shown.length + cleared.hidden.length).toBeLessThanOrEqual(2000);
    expect(stored).toBeLessThan(100);
    const hidden = effectiveHidden(cleared.hidden, cleared.hide_all, objects);
    expect(hidden).toHaveLength(10_000);
    // displays of a hidden set are not drawn either
    expect(
      shownDisplays({ displays: [{ chart: 5, visible: true }], charts: [{ id: 5, set_id: 7, name: "x", settings: {} } as never], drafts: [], styles: [], hidden }),
    ).toEqual([]);
  });

  it("showing one set after the clear shows only it; its sub-layers still follow hidden", () => {
    let h: unknown = clearedKeys([]).hide_all;
    h = showUnderHideAll("set:3", true)(h);
    expect(effectiveHidden([], normalizeHideAll(h), ["set:1", "set:3"])).toEqual(["set:1"]);
    h = showUnderHideAll("set:3:collars", true)(h); // a sub-layer shows its object
    expect(normalizeHideAll(h).shown).toEqual(["set:3"]);
    h = showUnderHideAll("set:3", false)(h);
    expect(effectiveHidden(["set:3:paths"], normalizeHideAll(h), ["set:1", "set:3"]).sort()).toEqual(["set:1", "set:3", "set:3:paths"]);
  });

  it("new objects are shown; nothing changes while off", () => {
    expect(showNew(["set:9"])({ on: true, shown: ["set:1"] })).toEqual({ on: true, shown: ["set:1", "set:9"] });
    expect(showNew(["set:9"])({ on: false, shown: [] })).toEqual({ on: false, shown: [] });
    expect(showUnderHideAll("set:9", true)(undefined)).toEqual({ on: false, shown: [] });
  });
});
