import { describe, expect, it } from "vitest";
import { formatRoute, parseRoute } from "./route";

describe("route", () => {
  it("parses tabs and table kinds", () => {
    expect(parseRoute("#/table/assay")).toEqual({ tab: "table", kind: "assay", hole: null });
    expect(parseRoute("#/3d")).toEqual({ tab: "view3d", kind: "collar", hole: null });
    expect(parseRoute("#/log?hole=H1")).toEqual({ tab: "log", kind: "collar", hole: "H1" });
    expect(parseRoute("#/audit")).toEqual({ tab: "audit", kind: "collar", hole: null });
  });

  it("falls back to the import tab and collar for unknown input", () => {
    expect(parseRoute("")).toEqual({ tab: "import", kind: "collar", hole: null });
    expect(parseRoute("#/nonsense/whatever")).toEqual({ tab: "import", kind: "collar", hole: null });
    expect(parseRoute("#/table/unknown").kind).toBe("collar");
  });

  it("round-trips every tab", () => {
    for (const tab of ["table", "plan", "section", "log", "view3d", "audit", "import"] as const) {
      const route = { tab, kind: "survey" as const, hole: null };
      const again = parseRoute(formatRoute(route));
      expect(again.tab).toBe(tab);
    }
    expect(parseRoute(formatRoute({ tab: "table", kind: "survey", hole: null })).kind).toBe("survey");
  });

  it("only table routes carry the kind", () => {
    expect(formatRoute({ tab: "audit", kind: "assay", hole: null })).toBe("#/audit");
    expect(formatRoute({ tab: "table", kind: "assay", hole: null })).toBe("#/table/assay");
  });

  it("keeps the selected hole, including characters that need escaping", () => {
    const route = { tab: "audit" as const, kind: "collar" as const, hole: "BHTSE-2-07/A?&=" };
    expect(parseRoute(formatRoute(route))).toEqual(route);
    expect(formatRoute({ tab: "table", kind: "assay", hole: "H1" })).toBe("#/table/assay?hole=H1");
    expect(parseRoute("#/plan?hole=")).toEqual({ tab: "plan", kind: "collar", hole: null });
  });
});
