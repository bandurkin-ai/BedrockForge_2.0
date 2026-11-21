// The link of the selection of samples (ТЗ 8.3, № 237; ADR 0019 §8): the state of the application, the bodies of the
// frames and lassos of the charts, the marks of the points, the clearing on 409 `selection_stale` (the table, the 3D),
// the 3D layer — as many segments as the samples selected (Т8.3-3), `chart.select_clear`. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { ScatterChart } from "../src/charts/scatter/ScatterChart";
import { computeBox, computeScatter, type ProbPlotResult, type ScatterResult } from "../src/modules/charts/apiMore";
import { boxFrameGroups, probFrameSelection, scatterSelectionBody, valueSelectionBody } from "../src/modules/charts/selection";
import { bindSampleSelection, chartEscape } from "../src/modules/charts/selection";
import { SAMPLE_MARKS_KEY, SelectionScene } from "../src/modules/charts/selectionScene";
import { markReason } from "../src/modules/tables/ToolBar";
import { loadPage, showSelection, tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { clearSelection, hasId, makeSelection, markedRows, markIndices, revalidate, sampleSelectionStore, selectByFilter, selectedText, selectRows, setHoleSamples, setSelectionNotice, sortedIds, type SelectionInfo } from "../src/shell/sampleSelection";
import { readSelectionSegments, SampleMarks, segmentPositions } from "../src/viz/holes/sampleMarks";
import type { SceneLayer } from "../src/viz/sceneTypes";
import { build } from "./bfa1-build";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const flush = () => new Promise((r) => setTimeout(r, 0));

const INFO = (token: string, n: number, extra: Partial<SelectionInfo> = {}): SelectionInfo => ({ token, set_id: 3, table_id: 11, table_name: "Пробы", rev: 4, n, unknown: 0, source: "rows", ...extra });
const STALE = { code: "selection_stale", message: "Выделение устарело: таблица «Пробы» изменилась.", hint: "Выделение снято. Выделите пробы на графике заново.", token: "t1" };

/** The segments of 3 samples (one hole without a trajectory: 4 selected), from the integer origin. */
function segmentsBuffer(token = "t1"): ArrayBuffer {
  return build(
    { kind: "selection_segments", format: 1, token, set_id: 3, table_id: 11, rev: 4, method: "minimum_curvature", n: 4, segments: 3, no_position: 1, origin: [500000, 7000000, 300], bbox: [499990, 6999990, 250, 500010, 7000010, 300] },
    [
      ["row", "<u4", new Uint32Array([101, 102, 104])],
      ["hole", "<u4", new Uint32Array([1, 1, 2])],
      ["from", "<f8", new Float64Array([0, 1, 2])],
      ["to", "<f8", new Float64Array([1, 2, 3])],
      ["a", "<f4", new Float32Array([0, 0, 0, 0, 0, -1, 5, 5, -2])],
      ["b", "<f4", new Float32Array([0, 0, -1, 0, 0, -2, 5, 5, -3])],
    ],
  );
}

interface Sent {
  method: string;
  path: string;
  body: Record<string, unknown>;
}

/** The core: `answer(method, path, body)` or the default answers of the selection. */
function stubCore(answer: (m: string, path: string, body: Record<string, unknown>) => Response | ArrayBuffer | null = () => null): Sent[] {
  const sent: Sent[] = [];
  let made = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = `${u.pathname.replace("/api/v1", "")}${u.search}`;
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      const own = answer(method, path, body);
      if (own instanceof ArrayBuffer) return Promise.resolve(new Response(own, { status: 200 }));
      if (own) return Promise.resolve(own);
      if (method === "POST" && path === "/stats/selection") {
        made += 1;
        const rows = Array.isArray(body.rows) ? (body.rows as number[]) : [];
        return Promise.resolve(json(INFO(`t${made}`, rows.length || 2, { source: body.filter ? "table" : "rows" })));
      }
      const m = /^\/stats\/selection\/(\w+)$/.exec(path);
      if (m && method === "GET") return Promise.resolve(json({ ...INFO(m[1], 2), rows: [105, 101] }));
      if (m && method === "DELETE") return Promise.resolve(json({ deleted: true }));
      return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  return sent;
}

const notices: string[] = [];

beforeEach(() => {
  notices.length = 0;
  setSelectionNotice((t) => notices.push(t));
  clearSelection();
  sampleSelectionStore.set({ hole: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("the state of the selection", () => {
  it("counts in words; ids ascending, a binary search, the marked points of a chart", () => {
    expect(selectedText(1)).toBe("Выделено 1 проба");
    expect(selectedText(3)).toBe("Выделено 3 пробы");
    expect(selectedText(12)).toBe("Выделено 12 проб");
    expect(selectedText(21)).toBe("Выделено 21 проба");
    expect(selectedText(200000)).toBe(`Выделено ${(200000).toLocaleString("ru-RU")} проб`);
    const ids = sortedIds([105, 101, 2 ** 33, 103]);
    expect([...ids]).toEqual([101, 103, 105, 2 ** 33]);
    expect(hasId(ids, 2 ** 33)).toBe(true);
    expect(hasId(ids, 102)).toBe(false);
    expect(markIndices([100, 101, null, 105, 2 ** 33], ids)).toEqual([1, 3, 4]);
    expect(markIndices([101], null)).toEqual([]);
    const hole = { key: "3::1", token: "h", table_id: 11, rows: sortedIds([7]) };
    expect(markedRows({ current: null, rows: null, hole })).toBe(hole.rows);
    expect(markedRows({ current: INFO("t", 1), rows: ids, hole })).toBe(ids);
  });

  it("a selection: POST, its rows read for the marks; a new one forgets the old; clearing — DELETE", async () => {
    const sent = stubCore();
    const info = await selectRows(3, "пробы", [105, 101]);
    expect(info?.token).toBe("t1");
    expect(sent[0]).toMatchObject({ method: "POST", path: "/stats/selection", body: { set_id: 3, table: "пробы", rows: [105, 101] } });
    await flush();
    expect(sampleSelectionStore.get().current?.token).toBe("t1");
    expect([...(sampleSelectionStore.get().rows ?? [])]).toEqual([101, 105]);
    await selectByFilter(3, "11", " AU > 1 ", 7);
    expect(sent.find((s) => s.body.filter)?.body).toEqual({ set_id: 3, table: 11, filter: { where: "AU > 1", hole: 7 } });
    expect(sent.some((s) => s.method === "DELETE" && s.path === "/stats/selection/t1")).toBe(true);
    expect(clearSelection("снято")).toBe(true);
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(sent.at(-1)).toMatchObject({ method: "DELETE", path: "/stats/selection/t2" });
    expect(notices).toEqual(["снято"]);
    expect(clearSelection()).toBe(false);
  });

  it("a refusal of the core (too many rows) leaves no selection and is told", async () => {
    stubCore((m, p) => (m === "POST" && p === "/stats/selection" ? json({ code: "too_many_rows", message: "Выделено слишком много проб: 250 000, можно не больше 200 000.", hint: "Сузьте фильтр.", param: "filter" }, 422) : null));
    expect(await selectByFilter(3, "11", "", null)).toBeNull();
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(notices[0]).toMatch(/^Выделение не сделано: Выделено слишком много проб/);
  });

  it("409 selection_stale after an edit of the table: the selection goes, the reason in the status bar", async () => {
    let stale = false;
    stubCore((m, p) => (stale && m === "GET" && p.startsWith("/stats/selection/t1") ? json(STALE, 409) : null));
    await selectRows(3, null, [101]);
    await flush();
    stale = true;
    await revalidate(4); // another set: not asked
    expect(sampleSelectionStore.get().current?.token).toBe("t1");
    await revalidate(3);
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(notices.at(-1)).toBe("Выделение устарело: таблица «Пробы» изменилась. Выделение проб снято.");
  });

  it("the table shows only the rows of the selection; 409 there — all rows again and no selection", async () => {
    let stale = false;
    const sent = stubCore((_m, p) => {
      if (!p.startsWith("/drillholes/sets/3/tables/11?")) return null;
      if (stale && p.includes("selection=")) return json(STALE, 409);
      return json({ rows: [], total: 0, columns: [], rev: 4 });
    });
    await selectRows(3, "пробы", [101, 102]);
    tablesStore.set({ setId: 3, table: "11", tabs: [{ table: "11", kind: "assay", name: "Пробы", rows: 2, rev: 4 }], pages: {}, selection: null, where: "", hole: null });
    await showSelection("t1");
    expect(sent.find((s) => s.path.includes("selection=t1"))?.path).toMatch(/^\/drillholes\/sets\/3\/tables\/11\?offset=0&limit=200&selection=t1$/);
    expect(markReason(tablesStore.get(), false)).toBe("Показаны строки выделения");
    stale = true;
    tablesStore.set({ pages: {} });
    await loadPage(0);
    await flush();
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(tablesStore.get().selection).toBeNull();
    expect(notices.at(-1)).toMatch(/Выделение проб снято\.$/);
    expect(markReason(tablesStore.get(), false)).toBeNull();
    expect(markReason({ ...tablesStore.get(), table: "collar", tabs: [{ table: "collar", kind: "collar", name: "Устья", rows: 1, rev: 1 }] }, false)).toMatch(/интервальной/);
  });

  it("the samples of the selected hole: marked when there is no selection; another hole forgets the old token", async () => {
    const sent = stubCore();
    await setHoleSamples({ setId: 3, table: "11", hole: 7 });
    expect(sent[0].body).toEqual({ set_id: 3, table: 11, filter: { where: null, hole: 7 } });
    expect([...(markedRows(sampleSelectionStore.get()) ?? [])]).toEqual([101, 105]);
    await setHoleSamples({ setId: 3, table: "11", hole: 7 }); // the same: nothing asked
    expect(sent.length).toBe(2);
    await setHoleSamples(null);
    expect(sent.at(-1)).toMatchObject({ method: "DELETE", path: "/stats/selection/t1" });
    expect(sampleSelectionStore.get().hole).toBeNull();
  });
});

const SCATTER: ScatterResult = {
  set_id: 3, x_field: "AU", y_field: "AG", color_field: null, log_x: false, log_y: false, weighted: false,
  counts: { rows: 4, missing_x: 0, missing_y: 0, unmatched: 0, bad_weight: 0, negative: 0, zero_excluded: 0, nonpositive_log: 0, used: 4 },
  mode: "points", x: [1, 2, 3, 4], y: [1, 2, 3, 4], row: [101, 102, 103, 104], c: null, color: null,
  pearson: 1, spearman: 1, n: 4, fits: { ols: { a: 0, b: 1 }, rma: { a: 0, b: 1 }, weighted: null },
  x_stats: { mean: 2.5, std: 1, min: 1, max: 4 }, y_stats: { mean: 2.5, std: 1, min: 1, max: 4 },
};
const SQUARE = { x: [1.5, 3.5, 3.5, 1.5], y: [1.5, 1.5, 3.5, 3.5] };

describe("the frames and lassos of the charts", () => {
  it("scatter points: the rows inside, of the table X of the settings the answer was computed from", async () => {
    stubCore((_m, p) => (p === "/stats/scatter" ? json(SCATTER) : null));
    expect(scatterSelectionBody(SCATTER, SQUARE, "lasso", "linear", "linear")).toEqual({ set_id: 3, table: null, rows: [102, 103] });
    const r = await computeScatter({ set_id: 3, table: "пробы", x: "AU", y: "AG" } as never);
    expect(scatterSelectionBody(r, SQUARE, "lasso", "linear", "linear")).toEqual({ set_id: 3, table: "пробы", rows: [102, 103] });
  });

  it("scatter density: the settings and the polygon, a frame — rect; unknown settings — nothing", async () => {
    const dense: ScatterResult = { ...SCATTER, mode: "density", x: undefined, y: undefined, row: undefined, density: { nx: 1, ny: 1, x: [0, 5], y: [0, 5], counts: [4], max: 4 } };
    stubCore((_m, p) => (p === "/stats/scatter" ? json(dense) : null));
    expect(scatterSelectionBody(dense, SQUARE, "lasso", "log", "linear")).toBeNull();
    const r = await computeScatter({ set_id: 3, table: "пробы", x: "AU", y: "AG", log_x: true } as never);
    expect(scatterSelectionBody(r, SQUARE, "lasso", "log", "linear")).toEqual({ chart: { set_id: 3, table: "пробы", x: "AU", y: "AG", log_x: true, kind: "scatter" }, polygon: [[1.5, 1.5], [3.5, 1.5], [3.5, 3.5], [1.5, 3.5]] });
    expect(scatterSelectionBody(r, SQUARE, "rect", "log", "linear")).toMatchObject({ rect: [1.5, 3.5, 1.5, 3.5] });
    // the core takes ≤ 1000 vertices: a longer polygon goes thinned evenly
    const ring = Array.from({ length: 2500 }, (_, i) => (2 * Math.PI * i) / 2500);
    const long = scatterSelectionBody(r, { x: ring.map((a) => 2 + Math.cos(a)), y: ring.map((a) => 2 + Math.sin(a)) }, "lasso", "log", "linear")!;
    expect((long.polygon as number[][]).length).toBe(1000);
    expect((long.polygon as number[][])[0]).toEqual([3, 2]);
  });

  it("box plots: the boxes the frame covers, the range; the log-probability plot: the series with a point inside", async () => {
    expect(boxFrameGroups(["A", "B", null], 100, 120, 66, 702)).toEqual(["A"]);
    expect(boxFrameGroups(["A", "B", null], 300, 500, 66, 702)).toEqual(["B", null]);
    expect(boxFrameGroups(["A", "B", null], 70, 700, 66, 702)).toBeNull();
    stubCore((_m, p) => (p === "/stats/box" ? json({ set_id: 3, field: "AU", domain: "ДОМЕН", log: false, weighted: true, counts: {}, groups: [], outliers_shown: 0, outliers_total: 0 }) : null));
    const box = await computeBox({ set_id: 3, table: null, field: "AU", domain: "ДОМЕН" } as never);
    expect(valueSelectionBody("box", box, [9, 2], ["A"])).toEqual({ chart: { set_id: 3, table: null, field: "AU", domain: "ДОМЕН", kind: "box" }, range: [2, 9], groups: ["A"] });
    const prob: Pick<ProbPlotResult, "series"> = { series: [{ label: "A", points: [[1, 10], [2, 50]], count: 2, nonpositive: 0, stats: { mean: 1, median: 1, cv: 0, p90: 2 } }, { label: null, points: [[10, 90]], count: 1, nonpositive: 0, stats: { mean: 1, median: 1, cv: 0, p90: 2 } }] };
    // the frame narrowed by P: grades 0.5…3 at P 5…60 % hold only A's points 1 and 2 (not 10 of the other series)
    expect(probFrameSelection(prob, 0.5, 3, 0.05, 0.6)).toEqual({ range: [1, 2], groups: ["A"] });
    // P 5…20 %: only A's point 1 — the range is not the frame's 0.5…30
    expect(probFrameSelection(prob, 0.5, 30, 0.05, 0.2)).toEqual({ range: [1, 1], groups: ["A"] });
    expect(probFrameSelection(prob, 0.5, 30, 0.05, 0.95)).toEqual({ range: [1, 10], groups: null });
    // series narrowed differently: one request — the union of their grades
    expect(probFrameSelection(prob, 1.5, 30, 0.4, 0.95)).toEqual({ range: [2, 10], groups: null });
    expect(probFrameSelection(prob, 100, 200, 0.05, 0.95)).toBeNull();
    expect(probFrameSelection(prob, 0.5, 3, 0.7, 0.95)).toBeNull(); // grades inside, P outside
  });

  it("the open scatter marks the points of the selection, whoever made it", async () => {
    stubCore();
    await selectByFilter(3, "11", "AU > 1", null); // the core gives rows 101, 105
    await flush();
    const html = renderToStaticMarkup(createElement(ScatterChart, { result: SCATTER, kx: "linear", ky: "linear", regression: "none", diagonal: false, color: "#1f6bff", xLabel: "AU", yLabel: "AG" }));
    expect(html).toContain('data-marked="1"');
    expect(html.match(/data-selected="true"/g)?.length).toBe(1);
    clearSelection();
    const none = renderToStaticMarkup(createElement(ScatterChart, { result: SCATTER, kx: "linear", ky: "linear", regression: "none", diagonal: false, color: "#1f6bff", xLabel: "AU", yLabel: "AG" }));
    expect(none).not.toContain("data-marked");
  });
});

/** The scene as the layer of the selection sees it. */
function fakeScene() {
  const layers = new Map<string, SceneLayer>();
  const restored: (() => void)[] = [];
  const frames: (() => void)[] = [];
  const holes = { method: "tangential" as string | null };
  return {
    layers,
    holesMethod: holes,
    frame: () => frames.forEach((fn) => fn()),
    onFrame: (fn: () => void) => {
      frames.push(fn);
      return () => void frames.splice(frames.indexOf(fn), 1);
    },
    restore: () => restored.forEach((fn) => fn()),
    addLayer: (key: string, layer: SceneLayer) => void layers.set(key, layer),
    removeLayer: (key: string) => void layers.delete(key),
    invalidate: () => undefined,
    clipPlanes: [] as THREE.Plane[],
    resolution: new THREE.Vector2(800, 600),
    holes: () => (holes.method ? { manifest: { method: holes.method } } : null) as never,
    onRestored: (fn: () => void) => {
      restored.push(fn);
      return () => undefined;
    },
  };
}

describe("the selection in 3D", () => {
  it("BFA1 selection_segments: the ends from the integer origin, a segment per sample with a position", () => {
    const s = readSelectionSegments(segmentsBuffer());
    expect(s).toMatchObject({ token: "t1", setId: 3, n: 4, segments: 3, noPosition: 1, origin: { x: 500000, y: 7000000, z: 300 } });
    expect(Array.from(s.row)).toEqual([101, 102, 104]);
    expect([...segmentPositions(s).subarray(0, 12)]).toEqual([0, 0, 0, 0, 0, -1, 0, 0, -1, 0, 0, -2]);
    const marks = new SampleMarks(s, { clip: [], resolution: new THREE.Vector2(1, 1) });
    expect(marks.count).toBe(3);
    expect(marks.bbox).toEqual([499990, 6999990, 250, 500010, 7000010, 300]);
    marks.dispose();
  });

  it("the layer follows the selection: as many segments as the samples selected; cleared — gone; after a lost context — back", async () => {
    const asked: string[] = [];
    stubCore();
    const scene = fakeScene();
    const s = new SelectionScene(scene as never, async (token, method) => (asked.push(`${token}:${method}`), segmentsBuffer(token)));
    expect(scene.layers.size).toBe(0);
    await selectRows(3, null, [101, 102, 104, 109]);
    await flush();
    expect(asked).toEqual(["t1:tangential"]);
    const layer = scene.layers.get(SAMPLE_MARKS_KEY) as SampleMarks;
    expect(layer.count).toBe(3);
    expect(sampleSelectionStore.get()).toMatchObject({ segments: 3, noPosition: 1 });
    expect(s.count).toBe(3);
    s.refresh(4); // another set: nothing asked
    s.refresh(3); // a trajectory of the set edited: asked again, the layer kept meanwhile
    expect(scene.layers.has(SAMPLE_MARKS_KEY)).toBe(true);
    await flush();
    expect(asked).toEqual(["t1:tangential", "t1:tangential"]);
    scene.layers.clear();
    scene.restore();
    expect((scene.layers.get(SAMPLE_MARKS_KEY) as SampleMarks).count).toBe(3);
    clearSelection();
    expect(scene.layers.size).toBe(0);
    expect(s.count).toBeNull();
    s.dispose();
  });

  it("only the newest answer is drawn; the holes of the set by another method — the segments asked again", async () => {
    stubCore();
    const scene = fakeScene();
    const pending: { method: string; resolve: (b: ArrayBuffer) => void }[] = [];
    const s = new SelectionScene(scene as never, (token, method) => new Promise((resolve) => pending.push({ method, resolve: () => resolve(segmentsBuffer(token)) })));
    await selectRows(3, null, [101, 102, 104, 109]);
    await flush();
    expect(pending.map((p) => p.method)).toEqual(["tangential"]);
    scene.frame(); // the same method: nothing asked
    expect(pending).toHaveLength(1);
    // the set loaded again by another method of the trajectory before the first answer
    scene.holesMethod.method = "minimum_curvature";
    scene.frame();
    expect(pending.map((p) => p.method)).toEqual(["tangential", "minimum_curvature"]);
    pending[1].resolve(new ArrayBuffer(0));
    await flush();
    expect(s.count).toBe(3);
    const drawn = scene.layers.get(SAMPLE_MARKS_KEY);
    pending[0].resolve(new ArrayBuffer(0)); // the old answer, late: dropped
    await flush();
    expect(scene.layers.get(SAMPLE_MARKS_KEY)).toBe(drawn);
    scene.frame();
    expect(pending).toHaveLength(2);
    s.dispose();
    clearSelection();
  });

  it("409 for the segments: the selection goes with the reason", async () => {
    stubCore();
    const scene = fakeScene();
    const { CoreError } = await import("../src/shell/api");
    const s = new SelectionScene(scene as never, async () => {
      throw Object.assign(new CoreError(409, "selection_stale", STALE.message, STALE.hint), { body: STALE });
    });
    await selectRows(3, null, [101]);
    await flush();
    await flush();
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(scene.layers.size).toBe(0);
    expect(notices.at(-1)).toMatch(/изменилась\. Выделение проб снято\.$/);
    s.dispose();
  });
});

describe("chart.select_clear", () => {
  it("clears the selection; without one it is unavailable", async () => {
    const sent = stubCore();
    const app = createApp({ persist: false });
    bindSampleSelection(app);
    app.store.set({ projectName: "Проект" });
    expect(app.registry.reason("chart.select_clear")).toBe("Нет выделения проб");
    await makeSelection({ set_id: 3, table: null, rows: [101] });
    expect(app.registry.reason("chart.select_clear")).toBeNull();
    const r = await app.registry.execute("chart.select_clear", {});
    expect(r).toMatchObject({ ok: true, result: "Выделение проб снято" });
    expect(sampleSelectionStore.get().current).toBeNull();
    expect(sent.at(-1)).toMatchObject({ method: "DELETE", path: "/stats/selection/t1" });
    expect((await app.registry.execute("chart.select_clear", {}, "claude")).ok).toBe(false);
  });
});

describe("Esc of a chart window", () => {
  // the DOM of the node tests: a window of events, the focus — a field of a fake document
  const el = (inChart: boolean) => ({ closest: (sel: string) => (inChart && sel.includes("chart-window") ? {} : null) }) as unknown as Element;
  /** The listeners of a window in their order; `stopImmediatePropagation` stops the rest. */
  function fakeWindow() {
    const on: { type: string; fn: (e: unknown) => void }[] = [];
    return {
      addEventListener: (type: string, fn: (e: unknown) => void) => void on.push({ type, fn }),
      removeEventListener: () => undefined,
      dispatch(type: string, props: Record<string, unknown>) {
        let stop = false;
        const e = { type, ...props, stopImmediatePropagation: () => void (stop = true), preventDefault: () => undefined };
        for (const l of on.filter((x) => x.type === type)) {
          if (stop) break;
          l.fn(e);
        }
      },
    };
  }
  afterEach(() => vi.unstubAllGlobals());

  it("the focus in the window, or pressed there with the focus nowhere since", () => {
    expect(chartEscape(false, el(true))).toBe(true);
    expect(chartEscape(true, null)).toBe(true);
    expect(chartEscape(true, el(false))).toBe(false); // the palette, a dialog, a field took the focus
    expect(chartEscape(false, null)).toBe(false);
  });

  it("a click on the chart, then Ctrl+K: Esc is the palette's, the selection stays; the focus elsewhere forgets the click", async () => {
    stubCore();
    const win = fakeWindow();
    const body = el(false);
    const doc = { activeElement: body as Element | null, body };
    vi.stubGlobal("window", win);
    vi.stubGlobal("document", doc);
    const app = createApp({ persist: false }); // binds the selection with the window of events
    app.store.set({ projectName: "Проект" });
    let later = 0;
    win.addEventListener("keydown", () => void later++); // the palette's own Esc
    const esc = () => win.dispatch("keydown", { key: "Escape", repeat: false });

    await makeSelection({ set_id: 3, table: null, rows: [101] });
    win.dispatch("pointerdown", { target: el(true) });
    app.palette.set({ query: "" }); // Ctrl+K
    esc();
    expect(later).toBe(1);
    expect(sampleSelectionStore.get().current).not.toBeNull();
    // the palette's field took the focus: the click is forgotten, Esc after it closed — not the chart's
    win.dispatch("focusin", { target: el(false) });
    app.palette.set({ query: null });
    esc();
    expect(later).toBe(2);
    expect(sampleSelectionStore.get().current).not.toBeNull();
    // a click on the chart again, nothing open: Esc clears the selection and goes no farther
    win.dispatch("pointerdown", { target: el(true) });
    doc.activeElement = null;
    esc();
    expect(later).toBe(2);
    await flush();
    expect(sampleSelectionStore.get().current).toBeNull();
  });
});
