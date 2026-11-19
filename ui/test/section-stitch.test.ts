// Э10 10.24 (ADR 0016 §8): «Каркас по серии» — the automatic links of the core, edited by clicks (link, unlink,
// 1 → N, only neighbouring sections), the bodies and the groups N : M follow; the edited links go into the
// parameters of `stitch_series` as pairs of lines (layer_id, lid), untouched — none (the core links itself).
import { describe, expect, it } from "vitest";
import {
  bodiesOf, clickContour, conflictsOf, contourTitle, initialState, linkLines, resetLinks, sectionsText, stitchParams, type LinkContour, type LinkTable,
} from "../src/modules/section/seriesStitch";

const contour = (i: number, k: number, lid: number, layer = 9, x = 100): LinkContour => ({
  i, k, name: k >= 0 ? `${5000 + 25 * k}N` : null, layer_id: layer, lid, vertices: 4, uw: k >= 0 ? [x, 50] : null, area: 100, centre: k >= 0 ? [500000 + x, 7000000 + 25 * k, 50] : null, off_plane: 0,
});

/** Sections 0…2: one contour on 0 (lid 10), two on 1 (lid 11, 12), one on 2 (lid 13), one outside (lid 14). */
function table(): LinkTable {
  return {
    series_id: 4, version: 2, count: 3, step: 25, names: ["5000N", "5025N", "5050N"], layers: [{ id: 9, name: "Контуры", rev: 3 }],
    contours: [contour(0, 0, 10), contour(1, 1, 11), contour(2, 1, 12, 9, 300), contour(3, 2, 13), contour(4, -1, 14)],
    links: [[0, 1, "overlap"], [1, 3, "centres"]],
    bodies: [[0, 1, 3], [2]], conflicts: [], skipped: { open: 1, short: 0, outside: 1, no_height: 0 }, off_plane_limit: 0.001, warnings: [],
  };
}

describe("the table of links", () => {
  it("starts with the automatic links of the core: two bodies, the parameters without links", () => {
    const st = initialState(table());
    expect(st.pairs).toEqual([[0, 1], [1, 3]]);
    expect(st.how).toEqual({ "0:1": "overlap", "1:3": "centres" });
    expect(bodiesOf(st.table, st.pairs)).toEqual([[0, 1, 3], [2]]);
    expect(stitchParams(st)).toEqual({ series_id: 4, layer_ids: [9], start_cap: "extend", end_cap: "extend", single: false });
  });

  it("a click picks, a click on a neighbour links (1 → N: the picked stays), a second click unlinks", () => {
    let st = initialState(table());
    st = clickContour(st, 0);
    expect(st.picked).toBe(0);
    st = clickContour(st, 2); // 0 → 1 and 2: a branch
    expect(st.pairs).toEqual([[0, 1], [0, 2], [1, 3]]);
    expect(st.picked).toBe(0);
    expect(st.message).toBe("Связаны: линия 11, 5000N — линия 13, 5025N");
    st = clickContour(st, 1); // unlink 0 — 1
    expect(st.pairs).toEqual([[0, 2], [1, 3]]);
    expect(st.edited).toBe(true);
    expect(bodiesOf(st.table, st.pairs)).toEqual([[0, 2], [1, 3]]);
  });

  it("only neighbouring sections are linked; a contour outside the windows takes no part", () => {
    let st = clickContour(initialState(table()), 0);
    st = clickContour(st, 3); // 5000N and 5050N: not neighbours — the pick moves
    expect(st.pairs).toEqual([[0, 1], [1, 3]]);
    expect(st.picked).toBe(3);
    expect(st.message).toContain("только контуры соседних разрезов");
    const out = clickContour(st, 4);
    expect(out.picked).toBe(3);
    expect(out.message).toContain("вне окон разрезов");
    expect(clickContour(clickContour(st, 3), 3).picked).toBe(3); // picked again
  });

  it("the edited links go into the parameters of stitch_series as pairs of lines (layer_id, lid)", () => {
    let st = initialState(table());
    st = clickContour(clickContour(st, 2), 3); // + 12 ↔ 13
    st = { ...st, start: "flat", end: "point", single: true };
    expect(stitchParams(st)).toEqual({
      series_id: 4, layer_ids: [9], start_cap: "flat", end_cap: "point", single: true,
      links: [
        [{ layer_id: 9, line: 10 }, { layer_id: 9, line: 11 }],
        [{ layer_id: 9, line: 11 }, { layer_id: 9, line: 13 }],
        [{ layer_id: 9, line: 12 }, { layer_id: 9, line: 13 }],
      ],
    });
    // «Автосвязи»: the links of the core again, the caps kept
    const back = resetLinks(st);
    expect([back.pairs, back.edited, back.start, back.single]).toEqual([[[0, 1], [1, 3]], false, "flat", true]);
    expect(stitchParams(back).links).toBeUndefined();
  });

  it("N : M on one pair of sections is found before the run, named by the sections", () => {
    const t = table();
    t.contours.push(contour(5, 0, 15, 9, 300));
    let st = initialState(t);
    for (const [a, b] of [[0, 2], [5, 1], [5, 2]]) st = clickContour(clickContour({ ...st, picked: null }, a), b);
    const c = conflictsOf(st.table, st.pairs);
    expect(c).toEqual([{ sections: [0, 1], contours: [0, 1, 2, 5] }]);
    expect(sectionsText(t, c[0].sections)).toBe("между 5000N и 5025N");
    expect(conflictsOf(t, initialState(t).pairs)).toEqual([]);
  });

  it("the links in 3D: segments between the centres of the contours; a layer named when there are several", () => {
    const st = initialState(table());
    const lines = linkLines(st.table, st.pairs)!;
    expect(Array.from(lines.offsets)).toEqual([0, 2, 4]);
    expect(Array.from(lines.xyz.subarray(0, 6))).toEqual([500100, 7000000, 50, 500100, 7000025, 50]);
    expect(linkLines(st.table, [])).toBeNull();
    const two = { ...table(), layers: [{ id: 9, name: "Контуры", rev: 3 }, { id: 10, name: "Жилы", rev: 1 }] };
    expect(contourTitle(two, 1)).toBe("линия 12 («Контуры»), 5025N");
  });
});

describe("series.stitch and series.digitize", () => {
  it("bound: without an active series — the reason; with one — the window «Каркас по серии» of that series", async () => {
    const { createApp } = await import("../src/shell/app");
    const { windowStore } = await import("../src/shell/windows");
    const { viewStore } = await import("../src/modules/scene/bind");
    const { seriesStore } = await import("../src/modules/section/seriesState");
    const { bindStitch, SERIES_STITCH } = await import("../src/modules/section/seriesStitch");
    const { bindDigitize, NO_SERIES } = await import("../src/modules/section/digitize");
    const app = createApp({ persist: false });
    bindStitch(app);
    bindDigitize(app);
    app.store.set({ projectName: "Проект" });
    expect(app.registry.reason("series.stitch")).toBe(NO_SERIES);
    expect(app.registry.reason("series.digitize")).toBe(NO_SERIES);
    const row = {
      id: 4, name: "Профили", folder: null, version: 1, number: 1, count: 3, created_at: "", updated_at: "",
      params: { type: "vertical", p1: [0, 0, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 50, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null },
    };
    seriesStore.set({ list: [row as never], limit: 1000, loaded: true, error: null, tables: {} });
    viewStore.set({ section: { clip: true, series: { id: 4, k: 1, seg: null } } });
    expect(app.registry.reason("series.stitch")).toBeNull();
    expect(app.registry.reason("series.digitize")).toBeNull();
    const r = await app.registry.execute("series.stitch");
    expect(r).toMatchObject({ ok: true });
    expect(windowStore.get().windows.map((w) => [w.kind, w.props])).toEqual([[SERIES_STITCH, { seriesId: 4 }]]);
    windowStore.set({ windows: [] });
    seriesStore.set({ list: [], loaded: false, tables: {} });
    viewStore.set({});
  });
});
