import { afterEach, describe, expect, it, vi } from "vitest";

const coreEvents = new Set<(event: string, data: string) => void>();
vi.mock("../src/modules/jobs/state", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/modules/jobs/state")>()),
  onCoreEvent: (fn: (event: string, data: string) => void) => {
    coreEvents.add(fn);
    return () => coreEvents.delete(fn);
  },
}));

import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { viewStore } from "../src/modules/scene/bind";
import { seriesApi, seriesFault, faultText, type SeriesRow } from "../src/modules/section/seriesApi";
import { applySectionKey, currentSection, ensureTable, seriesRange, seriesStatus, seriesStore, setActiveSeries, sectionPlaneOf } from "../src/modules/section/seriesState";
import { freeLine, normalizeSeries, sectionOf } from "../src/modules/section/state";

const LINE = { x0: 5, y0: 6, az: 45, length: 100, towards: 10, away: 20 };

function seriesRow(id: number, version = 1, extra: Partial<SeriesRow["params"]> = {}, name = "Профили"): SeriesRow {
  return {
    id, name, folder: null, version, number: id, count: 5, created_at: "2026-11-19T10:00:00", updated_at: "2026-11-19T10:00:00",
    params: { type: "vertical", p1: [1000, 5000, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null, ...extra },
  };
}

const tableOf = (row: SeriesRow) => ({
  version: row.version,
  count: 5,
  sections: [0, 1, 2, 3, 4].map((k) => ({ k, name: `${5000 + 25 * k}N`, s: 25 * k, origin: [1000, 5000 + 25 * k, 0], towards: 12.5, away: 12.5 })),
});

/** A fake core: /view with versions, /section-series with a table and its errors. */
function server() {
  const calls: { method: string; path: string; body: unknown; at: number }[] = [];
  const state = { list: [seriesRow(1)] as SeriesRow[], view: { value: undefined as unknown, version: 0 } };
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body, at: Date.now() });
    if (path === "/view" && method === "GET") return json({ values: state.view.version ? { section: state.view } : {} });
    if (path === "/view/section" && method === "PUT") {
      state.view = { value: body.value, version: state.view.version + 1 };
      return json(state.view);
    }
    if (path === "/section-series" && method === "GET") return json({ series: state.list, limit: 1000 });
    if (path === "/section-series" && method === "POST")
      return json({ code: "bad_series", message: "Шаг серии: от 0,01 до 10 000 м.", hint: "Введите расстояние в этих пределах.", field: "step" }, 422);
    const t = /^\/section-series\/(\d+)\/table$/.exec(path);
    if (t) {
      const row = state.list.find((r) => r.id === Number(t[1]));
      return row ? json(tableOf(row)) : json({ code: "series_not_found", message: "Серия разрезов не найдена: её удалили.", hint: "Обновите список серий." }, 404);
    }
    if (/^\/section-series\/\d+$/.test(path) && method === "PATCH")
      return json({ code: "version_conflict", message: "Серию разрезов только что изменили в другом окне.", hint: "Параметры серии обновлены — проверьте их и повторите изменение.", version: 2, series: seriesRow(1, 2) }, 409);
    if (path === "/lines/files") return json({ files: [] });
    if (path === "/rasters") return json({ rasters: [] });
    if (path === "/wireframes") return json({ wireframes: [] });
    return json({ code: "not_found", message: "Нет", hint: "" }, 404);
  });
  return { calls, state, fetch };
}

let app: App;
let core: ReturnType<typeof server>;

async function open(): Promise<void> {
  app = createApp({ persist: false });
  core = server();
  vi.stubGlobal("fetch", core.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await vi.waitFor(() => expect(seriesStore.get().loaded).toBe(true));
  await vi.waitFor(() => expect(core.calls.some((c) => c.path === "/view")).toBe(true));
  await new Promise((r) => setTimeout(r, 20)); // the keys of the project are read
}

afterEach(() => {
  projectStore.set({ current: null });
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setCoreTarget(null);
});

describe("the view key section.series", () => {
  it("an old key `section` reads without changes; series {id, k, seg} as the core reads it", () => {
    expect(sectionOf({ section: { line: null, clip: true } })).toEqual({ line: null, clip: false });
    const old = sectionOf({ section: { line: LINE, clip: true } });
    expect(old).toEqual({ line: LINE, clip: true });
    expect("series" in old).toBe(false);
    expect(normalizeSeries({ id: 3, k: 2, seg: null })).toEqual({ id: 3, k: 2, seg: null });
    expect(normalizeSeries({ id: 3, k: -1, seg: 1.5 })).toEqual({ id: 3, k: 0, seg: null });
    expect(normalizeSeries({ id: 0, k: 1 })).toBeNull();
    expect(normalizeSeries({ k: 1 })).toBeNull();
    expect(normalizeSeries(null)).toBeNull();
    // an active series hides the free line from what reads {line, clip}; it stays for S and profiles
    const s = sectionOf({ section: { line: LINE, clip: true, series: { id: 3, k: 4, seg: null } } });
    expect(s).toEqual({ line: null, clip: true, series: { id: 3, k: 4, seg: null }, free: LINE });
    expect(freeLine({ section: { line: LINE, clip: true, series: { id: 3, k: 4 } } })).toEqual(LINE);
    expect(sectionOf({ section: { line: null, clip: true, series: { id: 3 } } }).clip).toBe(true);
  });

  it("the plane of the series section, its name from the table of its version, the status line", async () => {
    await open();
    const keys = { section: { line: LINE, clip: true, series: { id: 1, k: 2, seg: null } } };
    let cur = currentSection(keys)!;
    expect(cur.k).toBe(2);
    expect(cur.steps).toBe(5);
    expect(cur.plane.origin[1]).toBe(5050); // O_k = P1 + s_k·v̂, A = 90°
    expect(cur.name).toBeNull(); // the table is not read yet
    await ensureTable(1);
    await ensureTable(1);
    expect(core.calls.filter((c) => c.path === "/section-series/1/table")).toHaveLength(1); // once per version
    cur = currentSection(keys)!;
    expect(seriesStatus(cur)).toBe("Серия «Профили» · разрез 3 из 5 · 5050N");
    // k beyond the series is the last section; a new version of the series reads its table again
    expect(currentSection({ section: { series: { id: 1, k: 99 } } })!.k).toBe(4);
    core.state.list = [seriesRow(1, 2)];
    coreEvents.forEach((fn) => fn("objects.changed", JSON.stringify({ kind: "section_series", id: 1 })));
    await vi.waitFor(() => expect(seriesStore.get().list[0].version).toBe(2));
    expect(currentSection(keys)!.name).toBeNull();
    await ensureTable(1);
    expect(core.calls.filter((c) => c.path === "/section-series/1/table")).toHaveLength(2);
    // other kinds of objects do not reread the list
    const n = core.calls.filter((c) => c.path === "/section-series").length;
    coreEvents.forEach((fn) => fn("objects.changed", JSON.stringify({ kind: "view", id: 9 })));
    expect(core.calls.filter((c) => c.path === "/section-series").length).toBe(n);
  });

  it("the scene gets the plane of the series or the free line; a polyline — its segment", async () => {
    await open();
    const calls: string[] = [];
    const scene = {
      setSection: (line: unknown, clip: boolean) => calls.push(`line ${line ? "set" : "none"} ${clip}`),
      setSectionPlane: (plane: { seg: number | null; origin: number[] } | null, clip: boolean) => calls.push(`plane ${plane?.origin[1]} ${plane?.seg} ${clip}`),
    };
    applySectionKey(scene, { section: { line: LINE, clip: true } });
    applySectionKey(scene, { section: { line: LINE, clip: true, series: { id: 1, k: 1 } } });
    applySectionKey(scene, { section: { line: LINE, clip: false, series: { id: 77, k: 1 } } }); // gone: the free line
    seriesStore.set({ list: [...seriesStore.get().list, seriesRow(2, 1, { type: "polyline", polyline: [[0, 0], [100, 0], [100, 100]], corridor: 10, p1: [0, 0, 0], az: null, s0: 0, s1: 0 })] });
    applySectionKey(scene, { section: { series: { id: 2, k: 0, seg: 1 }, clip: true } });
    expect(calls).toEqual(["line set true", "plane 5025 null true", "line set false", "plane 0 1 true"]);
    expect(seriesStatus(currentSection({ section: { series: { id: 2, k: 0, seg: 1 } } }))).toBe("Серия «Профили» · сегмент 2 из 2");
    // before the list is read the scene keeps what it shows
    seriesStore.set({ loaded: false, list: [] });
    expect(sectionPlaneOf({ section: { series: { id: 1, k: 0 } } }).pending).toBe(true);
  });

  it("the key is written not more often than once in 300 ms, the last value always", async () => {
    await open();
    vi.useFakeTimers();
    for (let k = 1; k <= 10; k++) {
      setActiveSeries({ id: 1, k, seg: null }, true);
      expect((viewStore.get().section as { series: { k: number } }).series.k).toBe(k); // applied at once
      await vi.advanceTimersByTimeAsync(50);
    }
    for (let k = 9; k >= 7; k--) {
      setActiveSeries({ id: 1, k, seg: null });
      await vi.advanceTimersByTimeAsync(350);
    }
    await vi.advanceTimersByTimeAsync(1000);
    const puts = core.calls.filter((c) => c.method === "PUT" && c.path === "/view/section");
    expect(puts.length).toBeGreaterThanOrEqual(1);
    for (let i = 1; i < puts.length; i++) expect(puts[i].at - puts[i - 1].at).toBeGreaterThanOrEqual(300);
    expect((puts.at(-1)!.body as { value: unknown }).value).toMatchObject({ clip: true, series: { id: 1, k: 7, seg: null } });
    // the free line again: series null
    setActiveSeries(null);
    await vi.advanceTimersByTimeAsync(1000);
    expect((core.calls.at(-1)!.body as { value: { series: unknown } }).value.series).toBeNull();
  });

  it("errors of the core {code, message, hint, field} reach the window; 409 carries the current series", async () => {
    await open();
    const bad = seriesFault(await seriesApi.create({ type: "vertical", step: 0 }).catch((e: unknown) => e));
    expect(bad).toMatchObject({ status: 422, code: "bad_series", field: "step", hint: "Введите расстояние в этих пределах." });
    expect(faultText(bad)).toBe("Шаг серии: от 0,01 до 10 000 м. Введите расстояние в этих пределах.");
    const stale = seriesFault(await seriesApi.patch(1, 1, { name: "Новое" }).catch((e: unknown) => e));
    expect(stale).toMatchObject({ status: 409, code: "version_conflict" });
    expect(stale.current?.version).toBe(2);
    const gone = seriesFault(await seriesApi.table(9).catch((e: unknown) => e));
    expect(gone).toMatchObject({ status: 404, code: "series_not_found", hint: "Обновите список серий." });
    expect(seriesFault(new Error("Сбой"))).toMatchObject({ code: "error", message: "Сбой", field: null });
  });

  it("an active series deleted elsewhere leaves the free line, with a message", async () => {
    await open();
    setActiveSeries({ id: 1, k: 3, seg: null }, true);
    const notes: string[] = [];
    app.store.subscribe(() => notes.push(app.store.get().statusNote ?? ""));
    core.state.list = [];
    coreEvents.forEach((fn) => fn("objects.changed", JSON.stringify({ kind: "section_series", id: 1, deleted: true })));
    await vi.waitFor(() => expect(sectionOf(viewStore.get()).series).toBeUndefined());
    expect(notes).toContain("Активная серия разрезов удалена: показана свободная линия разреза");
  });
});

describe("s0 and s1 over the visible data", () => {
  it("multiples of Δ around the data along v̂; K ≤ 5 000", () => {
    const pts: [number, number, number][] = [[1000, 5013, 0], [1000, 5087, -40], [1200, 4990, 10]];
    expect(seriesRange(pts, [1000, 5000, 0], [0, 1, 0], 25)).toEqual({ s0: -25, s1: 100, step: 25, count: 6 });
    expect(seriesRange([[0, 0, 0], [0, 2.5, 0]], [0, 0, 0], [0, 1, 0], 0.1)).toEqual({ s0: 0, s1: 2.5, step: 0.1, count: 26 });
    // a plan: v̂ = (0, 0, −1) from the top
    expect(seriesRange([[0, 0, 120], [0, 0, 7]], [0, 0, 120], [0, 0, -1], 10)).toEqual({ s0: 0, s1: 120, step: 10, count: 13 });
    const far = seriesRange([[0, 0, 0], [0, 100_000, 0]], [0, 0, 0], [0, 1, 0], 1);
    expect(far.count).toBeLessThanOrEqual(5000);
    expect(far.step).toBeGreaterThan(1);
    expect(far.s0 % far.step).toBe(0);
    expect(far.s1).toBeGreaterThanOrEqual(100_000);
    expect(seriesRange([], [0, 0, 0], [0, 1, 0], 25)).toEqual({ s0: 0, s1: 0, step: 25, count: 1 });
  });
});
