import { describe, expect, it } from "vitest";
import { DEFAULT_ROUTE, formatRoute, parseRoute } from "./route";

describe("route", () => {
  it("parses set, hole and table", () => {
    expect(parseRoute("#/?set=3&hole=BH%201&table=assay")).toEqual({ set: 3, hole: "BH 1", kind: "assay" });
  });

  it("falls back to defaults on unknown input", () => {
    expect(parseRoute("")).toEqual(DEFAULT_ROUTE);
    expect(parseRoute("#/table/assay")).toEqual(DEFAULT_ROUTE); // the old tab addresses are ignored
    expect(parseRoute("#/?set=abc&table=nope")).toEqual(DEFAULT_ROUTE);
    expect(parseRoute("#/?set=-2")).toEqual(DEFAULT_ROUTE);
  });

  it("round-trips and omits defaults", () => {
    const route = { set: 12, hole: "H/7?", kind: "survey" as const };
    expect(parseRoute(formatRoute(route))).toEqual(route);
    expect(formatRoute(DEFAULT_ROUTE)).toBe("#/");
    expect(formatRoute({ ...DEFAULT_ROUTE, set: 1 })).toBe("#/?set=1");
  });
});
