// Э10 10.23 (ADR 0016 §5): the shadow — the contours of the neighbouring sections as grey dashes, not clipped, not
// picked, not snap targets; after a save of a contour (a new version of the layer) the shadow is asked again with
// If-None-Match and follows the new ETag; the same contours (304) keep the layer.
import { afterEach, describe, expect, it, vi } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import type { SeriesRow } from "../src/modules/section/seriesApi";
import { currentSection, type SeriesState } from "../src/modules/section/seriesState";
import { readShadow, shadowRequest, shadowSections, ShadowSource, type ShadowTarget } from "../src/modules/section/shadowSource";
import { setCoreTarget } from "../src/shell/api";
import { ShadowLayer, shadowSegments } from "../src/viz/section/shadow";

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const X0 = 512345.5;
const Y0 = 6789012.25;

function row(shadow: SeriesRow["params"]["shadow"], contour: number | null = 9, version = 1): SeriesRow {
  return {
    id: 4, name: "Профили", folder: null, version, number: 1, count: 5, created_at: "", updated_at: "",
    params: { type: "vertical", p1: [X0, Y0, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow, contour_layer: contour, layout: null },
  };
}
const state = (r: SeriesRow): SeriesState => ({ list: [r], limit: 1000, loaded: true, error: null, tables: {} });
const at = (k: number, r: SeriesRow) => currentSection({ section: { clip: true, series: { id: 4, k, seg: null } } }, state(r));

/** BFA1 `series_contours` of squares around (x, y) at section k. */
function contours(squares: [number, number, number][]): ArrayBuffer {
  const xyz: number[] = [];
  const offsets = [0];
  for (const [x, z, k] of squares) {
    const y = Y0 + 25 * k;
    xyz.push(x, y, z, x + 10, y, z, x + 10, y, z + 5, x, y, z + 5);
    offsets.push(offsets.at(-1)! + 4);
  }
  return encodeBfa1({ kind: "series_contours", format: 1, lines: squares.length }, [
    ["xyz", "<f8", Float64Array.from(xyz)],
    ["offsets", "<u4", Uint32Array.from(offsets)],
    ["layer", "<u4", new Uint32Array(squares.length).fill(9)],
    ["lid", "<u4", Uint32Array.from(squares.map((_, i) => i))],
    ["section", "<u4", Uint32Array.from(squares.map((s) => s[2]))],
  ]);
}

describe("shadow: what is shown", () => {
  it("the neighbours by the mode, inside the series", () => {
    expect(shadowSections("none", 2, 5)).toEqual([]);
    expect(shadowSections("prev", 2, 5)).toEqual([1]);
    expect(shadowSections("next", 2, 5)).toEqual([3]);
    expect(shadowSections("both", 2, 5)).toEqual([1, 3]);
    expect(shadowSections("both", 0, 5)).toEqual([1]);
    expect(shadowSections("both", 4, 5)).toEqual([3]);
    expect(shadowSections("prev", 0, 1)).toEqual([]);
  });

  it("one request for both neighbours; none without a layer of contours or with the mode «нет»", () => {
    const v = () => 7;
    expect(shadowRequest(at(2, row("both")), v)!.path).toBe("/section-series/4/contours?k=1,3");
    expect(shadowRequest(at(2, row("both")), () => 8)!.sig).not.toBe(shadowRequest(at(2, row("both")), v)!.sig);
    expect(shadowRequest(at(2, row("none")), v)).toBeNull();
    expect(shadowRequest(at(2, row("both", null)), v)).toBeNull();
    expect(shadowRequest(at(2, row("both")), () => null)).toBeNull();
    expect(shadowRequest(null, v)).toBeNull();
  });

  it("closed contours as dashed segments from an integer origin; the layer has no clipping, no pick, no extent", () => {
    const lines = readShadow(contours([[X0, 100, 1]]));
    expect(Array.from(lines.section)).toEqual([1]);
    const seg = shadowSegments(lines, { x: Math.round(X0), y: Math.round(Y0), z: 100 });
    expect(seg.count).toBe(4);
    expect(Array.from(seg.positions.subarray(18, 24))).toEqual([X0 - Math.round(X0), 25.25, 5, X0 - Math.round(X0), 25.25, 0].map(Math.fround));
    expect(Array.from(seg.lengths)).toEqual([0, 10, 0, 5, 0, 10, 0, 5]);
    const layer = new ShadowLayer("#888888");
    layer.show(lines);
    expect(layer.origin).toEqual({ x: Math.round(X0), y: Math.round(Y0 + 25), z: 100 });
    expect(layer.segments).toBe(4);
    expect(layer.bbox).toBeNull();
    expect("pick" in layer).toBe(false);
    const mat = (layer.object.children[0] as unknown as { material: { clippingPlanes: unknown } }).material;
    expect(mat.clippingPlanes ?? []).toEqual([]);
    layer.dispose();
  });
});

describe("ShadowSource", () => {
  it("after a save of a contour the shadow is asked with If-None-Match and follows the new ETag; a 304 keeps it", async () => {
    const answers = [
      () => new Response(contours([[X0, 100, 1], [X0 + 40, 100, 3]]), { status: 200, headers: { ETag: '"e1"' } }),
      () => new Response(contours([[X0, 100, 1], [X0 + 40, 100, 3], [X0 + 80, 90, 3]]), { status: 200, headers: { ETag: '"e2"' } }),
      () => new Response(null, { status: 304 }),
    ];
    const calls: { url: string; tag: string | null }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: new URL(url).pathname + new URL(url).search, tag: (init?.headers as Record<string, string>)["If-None-Match"] ?? null });
      return answers[calls.length - 1]();
    }));
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const added: string[] = [];
    const scene: ShadowTarget = { addLayer: (k) => void added.push(k), removeLayer: (k) => void added.push(`-${k}`), layerChanged: () => undefined, invalidate: () => undefined };
    const src = new ShadowSource(scene, async () => new ShadowLayer("#888888"));
    const cur = at(2, row("both"));
    let version = 1;
    src.sync(cur, () => version);
    await src.pending;
    expect(calls).toEqual([{ url: "/api/v1/section-series/4/contours?k=1,3", tag: null }]);
    expect(added).toEqual(["shadow"]);
    expect(src.segments).toBe(8);
    src.sync(cur, () => version); // nothing changed: nothing asked
    expect(calls).toHaveLength(1);
    version = 2; // the contour layer saved
    src.sync(cur, () => version);
    await src.pending;
    expect(calls[1].tag).toBe('"e1"');
    expect(src.segments).toBe(12);
    version = 3; // saved again, the contours of the neighbours the same
    src.sync(cur, () => version);
    await src.pending;
    expect(calls[2].tag).toBe('"e2"');
    expect(src.segments).toBe(12);
    src.sync(at(2, row("none")), () => version);
    expect(added).toEqual(["shadow", "-shadow"]);
    expect(src.segments).toBe(0);
    src.dispose();
  });
});
