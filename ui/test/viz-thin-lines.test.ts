/** The threshold of thin lines by the class of graphics (ADR 0007 §7; Э11 11.18): under software rendering a
 * million fat segments took ~21 s a frame, so above 200 thousand segments the lines are 1 px there; real graphics
 * keep a million. The choice is made before the first build: a heavy layer is never built fat first. */
import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { describe, expect, it } from "vitest";
import { build } from "./bfa1-build";
import { fatLines, gpuClass, THIN_LINE_SEGMENTS } from "../src/viz/core/budget";
import { THIN_SEGMENTS, useFatLines } from "../src/viz/holes/paths";
import { DEFAULT_TRACE_STYLE } from "../src/viz/holes/traceStyle";
import type { LabelCache } from "../src/viz/labels/sprites";
import type { LinesManifest } from "../src/viz/lines/build";
import { LINE_WIDTH_PX, LineLayerView, manifestSegments } from "../src/viz/lines/layer";
import { buildLines } from "../src/viz/worker/lines";

/** The layer of the editor's measure (perfEditor): 1000 lines × 1000 vertices in one part. */
const GRID: LinesManifest = {
  layerId: 3, name: "Миллион", fileId: 1, version: 2, pointsOnly: false, color: null, lines: 1000, points: 1_000_000,
  bbox: [500_000, 7_000_000, 0, 500_999, 7_000_999, 0], tols: [0, 4, 32], labels: false,
  parts: [{ k: 0, n: 1000, points: 1_000_000, bbox: [500_000, 7_000_000, 0, 500_999, 7_000_999, 0], origin: [500_000, 7_000_000, 0], etags: ['"a"', '"b"', '"c"'] }],
};

describe("the threshold by the class of graphics", () => {
  it("software rendering: 200 thousand segments; real graphics: a million (as before)", () => {
    expect(THIN_LINE_SEGMENTS).toEqual({ integrated: 1_000_000, discrete: 1_000_000, software: 200_000 });
    expect(gpuClass("Google SwiftShader")).toBe("software");
    expect(fatLines(LINE_WIDTH_PX, 200_000, "software")).toBe(true);
    expect(fatLines(LINE_WIDTH_PX, 200_001, "software")).toBe(false);
    expect(fatLines(LINE_WIDTH_PX, 999_000, "software")).toBe(false);
    for (const gpu of ["integrated", "discrete"] as const) {
      expect(fatLines(LINE_WIDTH_PX, 999_000, gpu)).toBe(true);
      expect(fatLines(LINE_WIDTH_PX, 1_000_001, gpu)).toBe(false);
    }
    expect(fatLines(1, 10, "discrete")).toBe(false); // one pixel wide: plain lines anyway
  });

  it("hole traces keep their threshold unless the class is given", () => {
    const style = { ...DEFAULT_TRACE_STYLE, width: 2 };
    expect(THIN_SEGMENTS).toBe(1_000_000);
    expect(useFatLines(style, 999_000)).toBe(true);
    expect(useFatLines(style, 999_000, "software")).toBe(false);
    expect(useFatLines({ ...style, width: 1 }, 10)).toBe(false);
  });
});

describe("the choice before the first build", () => {
  it("the segments of a layer come from its manifest: n − 1 per polyline; points and clouds have none", () => {
    expect(manifestSegments(GRID)).toBe(999_000);
    expect(manifestSegments({ ...GRID, pointsOnly: true })).toBe(0);
    expect(manifestSegments({ ...GRID, cloud: { value: null } as unknown as LinesManifest["cloud"] })).toBe(0);
    // one-vertex lines are markers, not segments
    expect(manifestSegments({ ...GRID, parts: [{ ...GRID.parts[0], n: 5, points: 5 }] })).toBe(0);
    // the editor's layer under SwiftShader is thin from the start; on real graphics it stays fat
    expect(fatLines(LINE_WIDTH_PX, manifestSegments(GRID), "software")).toBe(false);
    expect(fatLines(LINE_WIDTH_PX, manifestSegments(GRID), "integrated")).toBe(true);
  });

  it("a view made thin before its levels builds plain LineSegments only; the segments of a level are known", () => {
    const ctx = { clip: [] as THREE.Plane[], resolution: new THREE.Vector2(800, 600), labels: null as unknown as LabelCache, pixelRatio: 1 };
    const m: LinesManifest = { ...GRID, parts: [{ ...GRID.parts[0], n: 2, points: 5 }] };
    const v = new LineLayerView(m, ctx, { color: null, visible: true }, 0);
    v.setFat(false);
    const origin = m.parts[0].origin;
    const pts = [[500_000, 7_000_000, 0], [500_010, 7_000_000, 0], [500_020, 7_000_005, 0], [500_000, 7_000_100, 0], [500_005, 7_000_120, 0]];
    const xyz = new Float32Array(pts.length * 3);
    pts.forEach((p, i) => p.forEach((c, j) => (xyz[i * 3 + j] = c - origin[j])));
    const built = buildLines(
      build({ kind: "lines", k: 0, lod: 0, points_only: false, origin, bbox: m.parts[0].bbox, n: 2, points: 5, labels: null, simplified: false }, [
        ["offsets", "<u4", Uint32Array.from([0, 3, 5])], ["xyz", "<f4", xyz], ["line", "<u4", Uint32Array.from([1, 2])], ["closed", "<u1", new Uint8Array(2)],
      ]),
    );
    expect(v.levelSegments(0, 0)).toBe(0);
    v.setLevel(0, built);
    expect(v.levelSegments(0, 0)).toBe(3);
    const objects: THREE.Object3D[] = [];
    v.object.traverse((o) => objects.push(o));
    expect(objects.some((o) => o instanceof LineSegments2)).toBe(false);
    expect(objects.filter((o) => o instanceof THREE.LineSegments)).toHaveLength(1);
    // back to fat when the scene is light again
    v.setFat(true);
    const again: THREE.Object3D[] = [];
    v.object.traverse((o) => again.push(o));
    expect(again.some((o) => o instanceof LineSegments2)).toBe(true);
    v.dispose();
  });
});
