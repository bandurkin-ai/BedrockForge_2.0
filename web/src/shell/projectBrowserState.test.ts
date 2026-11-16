import { describe, expect, it } from "vitest";
import type { Dataset } from "../api";
import { collectBrowserState, mergeBrowserState } from "./projectBrowserState";
import { DEFAULT_WORKSPACE, normalize } from "./workspace";

const datasets = [
  { id: 3, kind: "drillholes", charts: [{ id: 9 }], wireframes: null, lines: null, rasters: null },
  { id: 4, kind: "wireframes", wireframes: [{ id: 21 }], lines: null, rasters: null },
] as unknown as Dataset[];

describe("view settings of a project in a .bfggis file", () => {
  it("takes only the project's objects and translates their ids on import", () => {
    const ws = normalize({
      ...DEFAULT_WORKSPACE,
      hidden: ["set:3:collars", "wf:21", "wf:99", "rs:5"],
      logs: [9, 77],
      collarStyles: { 3: {}, 8: {} },
    });
    const state = collectBrowserState(ws, datasets);
    expect(state.hidden).toEqual(["set:3:collars", "wf:21"]);
    expect(state.logs).toEqual([9]);
    expect(Object.keys(state.collarStyles)).toEqual(["3"]);

    const maps = { dataset: { 3: 103, 4: 104 }, wireframe: { 21: 121 }, chart: { 9: 109 } };
    const merged = mergeBrowserState(DEFAULT_WORKSPACE, JSON.parse(JSON.stringify(state)), maps);
    expect(merged.hidden).toEqual(["set:103:collars", "wf:121"]);
    expect(merged.logs).toEqual([109]);
    expect(Object.keys(merged.collarStyles)).toEqual(["103"]);
  });

  it("ignores what it does not understand", () => {
    expect(mergeBrowserState(DEFAULT_WORKSPACE, null, {})).toBe(DEFAULT_WORKSPACE);
    expect(mergeBrowserState(DEFAULT_WORKSPACE, { hidden: [1, "zz:4"], logs: ["x"] }, {}).hidden).toEqual([]);
  });
});
