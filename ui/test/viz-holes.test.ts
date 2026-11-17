import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { holeIndex, partKinds, readHolesManifest } from "../src/viz/holes/manifest";
import { buildPaths, transferables } from "../src/viz/worker/paths";
import { AUDIT_COLORS, auditColor, collarColors } from "../src/viz/holes/colors";

function manifestBuffer() {
  const parts = [
    { k: 0, n: 2, bbox: [500_000, 7_000_000, 100, 500_100, 7_000_050, 300], origin: [500_050, 7_000_025, 200], etag: '"a"' },
    { k: 1, n: 1, bbox: [510_000, 7_000_000, 90, 510_010, 7_000_010, 290], origin: [510_005, 7_000_005, 190], etag: '"b"' },
  ];
  return build(
    { kind: "holes", format: 1, set: { id: 3, name: "Скважины" }, version: 7, method: "minimum_curvature", n: 3, parts, names: ["DH-1", "DH-2", "TR-1"], skipped: ["DH-9"], audit: null },
    [
      ["id", "<u4", Uint32Array.of(11, 12, 13)],
      ["collar", "<f8", Float64Array.of(500_000.0001, 7_000_000.25, 300, 500_100, 7_000_050, 299, 510_000, 7_000_000, 290)],
      ["td", "<f8", Float64Array.of(200, NaN, 5)],
      ["length", "<f8", Float64Array.of(200, 150, 5)],
      ["plan_az", "<f4", Float32Array.of(45, NaN, 90)],
      ["kind", "<u1", Uint8Array.of(0, 2, 1)],
      ["audit", "<u1", Uint8Array.of(0, 4, 1)],
      ["part", "<u2", Uint16Array.of(0, 0, 1)],
      ["slot", "<u2", Uint16Array.of(1, 0, 0)],
    ],
  );
}

describe("holes manifest", () => {
  it("reads parts, names and the per-hole arrays without rounding the collars", () => {
    const m = readHolesManifest(manifestBuffer());
    expect(m.setId).toBe(3);
    expect(m.parts.map((p) => p.k)).toEqual([0, 1]);
    expect(m.collar[0]).toBe(500_000.0001); // float64 kept
    expect(Number.isNaN(m.td[1])).toBe(true); // missing stays missing
    expect(Array.from(m.byPart[0])).toEqual([1, 0]); // slot 0 of part 0 is manifest index 1
    expect(Array.from(partKinds(m, 0))).toEqual([2, 0]);
    expect(holeIndex(m, "TR-1")).toBe(2);
    expect(holeIndex(m, "нет")).toBe(-1);
    expect(m.skipped).toEqual(["DH-9"]);
  });

  it("audit colours: none, clean, notes, warnings, errors; a set colour overrides", () => {
    expect(AUDIT_COLORS).toEqual(["#9aa3b5", "#2e9d57", "#3b82d6", "#e0a030", "#c0392b"]);
    expect(auditColor(4)).toBe("#c0392b");
    expect(auditColor(9)).toBe("#9aa3b5");
    // vertex colours are linear: three's output turns them back into the exact sRGB of the theme
    const back = (v: number) => Math.round((v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055) * 255);
    const rgb = collarColors(Uint8Array.of(0, 4), "audit");
    expect(Array.from(rgb.slice(3, 6)).map(back)).toEqual([0xc0, 0x39, 0x2b]);
    expect(Array.from(rgb.slice(0, 3)).map(back)).toEqual([0x9a, 0xa3, 0xb5]);
    expect(Array.from(collarColors(Uint8Array.of(0, 4), "#ff0000")).map(back)).toEqual([255, 0, 0, 255, 0, 0]);
  });
});

function pathsBuffer() {
  // part of 3 holes: slot 0 — 3 points (2 segments), slot 1 — a collar only, slot 2 — 2 points
  const xyz = Float32Array.of(0, 0, 0, 0, 0, -10, 1, 0, -20, 5, 5, 0, 9, 9, 0, 9, 9, -3);
  return build(
    { kind: "paths", format: 1, k: 4, n: 3, points: 6, origin: [100, 200, 300], bbox: [0, 0, 0, 1, 1, 1] },
    [
      ["offsets", "<u4", Uint32Array.of(0, 3, 4, 6)],
      ["xyz", "<f4", xyz],
      ["md", "<f8", Float64Array.of(0, 10, 20.0249, 0, 0, 3)],
    ],
  );
}

describe("trace buffers of a part (Worker)", () => {
  it("segments by kind with their slots; design holes get cumulative lengths", () => {
    const b = buildPaths(pathsBuffer(), Uint8Array.of(0, 0, 2));
    expect(b.k).toBe(4);
    expect(b.segments).toBe(3);
    expect(b.groups.map((g) => g.kind)).toEqual([0, 2]);
    const holes = b.groups[0];
    expect(Array.from(holes.slots)).toEqual([0, 0]);
    expect(Array.from(holes.positions.slice(6, 12))).toEqual([0, 0, -10, 1, 0, -20]);
    const design = b.groups[1];
    expect(Array.from(design.slots)).toEqual([2]);
    expect(Array.from(design.distances!)).toEqual([0, 3]);
    expect(b.origin).toEqual([100, 200, 300]);
  });

  it("without kinds every hole is ordinary; buffers are transferred once each", () => {
    const b = buildPaths(pathsBuffer(), null);
    expect(b.groups.length).toBe(1);
    expect(b.groups[0].distances).toBeNull();
    const t = transferables(b);
    expect(new Set(t).size).toBe(t.length);
    expect(t).toContain(b.xyz.buffer);
  });

  it("a damaged answer is refused with a Russian message", () => {
    expect(() => buildPaths(new ArrayBuffer(4), null)).toThrow(/Данные 3D повреждены/);
  });
});
