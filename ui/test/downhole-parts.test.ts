import { describe, expect, it } from "vitest";
import { readIntervals, readManifest, readPaths } from "../src/viz/downhole/parts";
import { build } from "./bfa1-build";

describe("data of the displays from BFA1 parts", () => {
  const parts = [
    { k: 0, n: 2, bbox: [0, 0, 0, 1, 1, 1], origin: [500000, 7000000, 300], etag: "a" },
    { k: 1, n: 1, bbox: [0, 0, 0, 1, 1, 1], origin: [501000, 7000000, 300], etag: "b" },
  ];
  const manifest = build({ kind: "holes", set: { id: 3 }, version: 2, method: "minimum_curvature", parts, names: ["A", "B", "C"] }, [
    ["td", "<f8", new Float64Array([100, NaN, 50])],
    ["part", "<u2", new Uint16Array([0, 1, 0])],
    ["slot", "<u2", new Uint16Array([0, 0, 1])],
  ]);

  it("knows which part and slot each hole has", () => {
    const m = readManifest(manifest);
    expect(m.partNames).toEqual([["A", "C"], ["B"]]);
    expect(m.setId).toBe(3);
  });

  it("paths are float64: part origin plus the float32 offset", () => {
    const buf = build({ kind: "paths" }, [
      ["offsets", "<u4", new Uint32Array([0, 2, 3])],
      ["xyz", "<f4", new Float32Array([0.25, -0.5, 0, 0.25, -0.5, -100, 3, 4, 0])],
      ["md", "<f8", new Float64Array([0, 100, 0])],
    ]);
    const holes = readPaths(buf, parts[0] as never, ["A", "C"]);
    expect(holes.map((h) => h.hole_id)).toEqual(["A", "C"]);
    expect(holes[0].path[1]).toEqual([500000.25, 6999999.5, 200, 100]);
    expect(holes[1].path).toEqual([[500003, 7000004, 300, 0]]);
  });

  it("intervals: NaN and −1 are empty (null, not 0), events have one depth", () => {
    const buf = build({ kind: "intervals", table: { name: "Пробы", kind: "assay" }, fields: [{ name: "AU", kind: "number", unit: "г/т" }, { name: "LITH", kind: "text", unit: null }], cats: { LITH: ["гранит"] } }, [
      ["slot", "<u2", new Uint16Array([0, 0, 1])],
      ["from", "<f8", new Float64Array([0, 2, 5])],
      ["to", "<f8", new Float64Array([2, 4, 6])],
      ["v:AU", "<f8", new Float64Array([0.5, NaN, 0])],
      ["c:LITH", "<i4", new Int32Array([0, -1, 0])],
    ]);
    const p = readIntervals(buf, 2);
    expect(p.rows[0]).toEqual([
      { from: 0, to: 2, values: { AU: 0.5, LITH: "гранит" } },
      { from: 2, to: 4, values: { AU: null, LITH: null } },
    ]);
    expect(p.rows[1][0].values.AU).toBe(0);
    expect(p.fields[1]).toEqual({ name: "LITH", kind: "text", unit: "" });
    const ev = build({ kind: "intervals", table: { name: "Структуры", kind: "event" }, fields: [], cats: {} }, [
      ["slot", "<u2", new Uint16Array([0])],
      ["from", "<f8", new Float64Array([7.5])],
      ["to", "<f8", new Float64Array([NaN])],
    ]);
    expect(readIntervals(ev, 1).rows[0][0]).toEqual({ from: 7.5, to: 7.5, values: {} });
  });
});
