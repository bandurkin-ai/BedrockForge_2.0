/** «Карта разностей» of a point cloud (ADR 0014 §5, подшаг 7.1.17): what `cloud.dz` of the core tells, the
 * surfaces offered and the one by default, the texts of «Свойства» and of the window, the command `cloud.dz`
 * (the window, or the job at once; its refusals), its button, the menu of the tree, and «Карта разностей для
 * облаков» of «Построение ЦТМ» (`dz` of the body). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildBody, EMPTY_FORM } from "../src/modules/dtm/build";
import { requestOf } from "../src/modules/dtm/cloud";
import type { CloudSummary } from "../src/modules/lines/classes";
import { builtFrom, defaultSurface, dzFacts, dzJobs, dzRecord, dzResult, dzSummary, jobText, measuresText, surfaceOptions } from "../src/modules/lines/dz";
import { layerMenu } from "../src/modules/lines/tree";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { loadSceneObjects, sceneObjects, type LineFileInfo, type LineLayerInfo } from "../src/modules/scene/objects";
import type { WireframeInfo } from "../src/modules/wireframes/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { windowStore } from "../src/shell/windows";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";

const plain = (s: string): string => s.replace(/[\u00a0\u202f]/g, " ");

const summary = (p: Partial<CloudSummary> = {}): CloudSummary => ({
  parts: 1, levels: [0], bbox: [0, 0, 0, 100, 100, 10], classes: { "1": 100, "2": 2627 }, modes: ["class", "h"], source: null, ground: null, dz: null, ...p,
});
const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: id, name: `Слой ${id}`, version: 1, color: null, points_only: false, line_count: 3, point_count: 30, length: 10, closed_count: 1, closed: "some",
  flat: false, extent: null, attr_schema: [], ...p,
});
const cloud = (id: number, s: Partial<CloudSummary> = {}) => layer(id, { name: `survey${id}`, points_only: true, line_count: 0, point_count: 3000, closed_count: 0, closed: "none", cloud: summary(s) });
const file = (id: number, layers: LineLayerInfo[]): LineFileInfo => ({ id, name: `f${id}`, folder: null, version: 1, format: null, label: null, filename: null, crs_name: null, warnings: [], layers });
const DTM = { raster_id: null, clip: false, tint: "#ffffff", opacity: 1, shade: true };
const surface = (id: number, name: string, dtm: boolean, from: number[] = []): WireframeInfo & { built: unknown } => ({
  id, name, version: 1, color: null, point_count: 10, triangle_count: 18, extent: null, dtm: dtm ? DTM : null,
  built: from.length ? { kind: "dtm", sources: from.map((c) => ({ kind: "line_layer", id: c, name: `survey${c}` })) } : null,
});

const record = { wireframe_id: 42, name: "Земля облака", at: "2026-11-19T10:00:00", points: 2900, outside: 100, by: "ground", count: 2600, rms: 0.0123, p95: 0.025, max: 0.0512 };
const measured = cloud(8, { modes: ["class", "h", "dz"], dz: record });
const raw = cloud(7);
const ore = surface(41, "Рудное тело", false);
const relief = surface(42, "Земля облака", true, [8]);
const older = surface(43, "Рельеф (старый)", true, [7]);
const newer = surface(44, "Рельеф", true, [7]);

describe("what `cloud.dz` of the core tells (ADR 0014 §5)", () => {
  it("the record of the cloud and the result of the job", () => {
    expect(dzRecord(raw)).toBeNull();
    expect(dzRecord(measured)).toEqual({ wireframeId: 42, name: "Земля облака", at: "2026-11-19T10:00:00", points: 2900, outside: 100, by: "ground", count: 2600, rms: 0.0123, p95: 0.025, max: 0.0512 });
    expect(dzRecord(cloud(9, { dz: { ...record, wireframe_id: null } }))?.wireframeId).toBeNull(); // the surface was deleted
    const result = { cloud_id: 8, wireframe_id: 42, version: 5, points: 3000, with_dz: 2900, outside: 100, by: "ground", count: 2600, rms: 0.0123, p95: 0.025, max: 0.0512, threads: 4 };
    expect(dzResult(result)).toEqual({ points: 2900, outside: 100, by: "ground", count: 2600, rms: 0.0123, p95: 0.025, max: 0.0512 });
    expect(dzResult(null)).toBeNull();
  });

  it("the texts: «Свойства», the window; a gap is not a zero", () => {
    expect(plain(dzSummary(dzRecord(measured)))).toMatch(/^Карта разностей относительно «Земля облака» от \d\d\.\d\d\.2026, \d\d:\d\d: СКО 0,012 м, P95 0,025 м, наибольшее 0,051 м \(по земле, 2 600 точек\); вне поверхности 100 точек\.$/);
    expect(dzSummary(null)).toContain("не считалась");
    expect(dzSummary(dzRecord(cloud(9, { dz: { ...record, wireframe_id: null, outside: 0 } })))).toContain("«Земля облака» (удалена)");
    const none = { points: 0, outside: 3000, by: "all" as const, count: 0, rms: null, p95: null, max: null };
    expect(measuresText(none)).toBe("мерить не по чему: под поверхностью нет точек");
    expect(dzFacts(none).map((f) => f.value).slice(0, 3)).toEqual(["—", "—", "—"]);
    expect(plain(measuresText({ ...none, points: 3000, outside: 0, count: 3000, rms: 1, p95: 2, max: 3 }))).toContain("по всем точкам — земли под поверхностью нет, 3 000 точек");
    expect(dzFacts(dzRecord(measured)!).map((f) => [f.label, plain(f.value)])).toEqual([
      ["СКО", "0,012 м"],
      ["P95", "0,025 м"],
      ["Наибольшее по модулю", "0,051 м"],
      ["Мерено", "по земле (класс 2): 2 600"],
      ["Точек с отклонением", "2 900"],
      ["Вне поверхности", "100"],
    ]);
  });

  it("the state of the job of the window", () => {
    const job = (p: object) => ({ status: "running" as const, progress: 0.42, message: "Карта разностей", error: null, ...p });
    expect(jobText(undefined)).toBeNull();
    expect(jobText(job({ status: "queued" }))).toBe("Задание в очереди…");
    expect(jobText(job({}))).toBe("Считается: 42 % — Карта разностей");
    expect(jobText(job({ status: "done" }))).toBeNull();
    expect(jobText(job({ status: "failed", error: { code: "no_overlap", message: "Под точками облака нет поверхности.", hint: "Выберите другую." } }))).toBe("Под точками облака нет поверхности. Выберите другую.");
  });
});

describe("the surfaces of the window", () => {
  it("DTMs first, then wireframes, by name", () => {
    expect(surfaceOptions([ore, newer, relief, older]).map((o) => [o.id, o.dtm])).toEqual([[42, true], [44, true], [43, true], [41, false]]);
  });

  it("by default: the one compared last, else the last DTM from this cloud, else the only surface", () => {
    expect(defaultSurface(measured, [ore, relief, newer])).toBe(42);
    expect(defaultSurface(cloud(8, { dz: { ...record, wireframe_id: 99 } }), [ore, relief])).toBe(42); // 99 is gone: the DTM from it
    expect(defaultSurface(raw, [ore, relief, older, newer])).toBe(44); // the last DTM built from survey7
    expect(defaultSurface(raw, [ore, relief])).toBeNull(); // none from it: the person chooses
    expect(defaultSurface(raw, [ore])).toBe(41);
    expect(defaultSurface(null, [ore])).toBe(41);
    expect([builtFrom(relief, 8), builtFrom(relief, 7), builtFrom(ore, 8)]).toEqual([true, false, false]);
  });
});

describe("«Построение ЦТМ»: «Карта разностей для облаков»", () => {
  const files = [file(2, [measured])];
  it("on by default: the core counts it, nothing is sent; off — `dz: false` to both requests", () => {
    expect(EMPTY_FORM.dz).toBe(true);
    const on = buildBody({ ...EMPTY_FORM, clouds: [{ layerId: 8, classes: [2], cell: "" }] }, files).body!;
    expect(on).not.toHaveProperty("dz");
    expect(requestOf(on).body).not.toHaveProperty("dz");
    const off = buildBody({ ...EMPTY_FORM, dz: false, clouds: [{ layerId: 8, classes: [2], cell: "" }] }, files).body!;
    expect(off.dz).toBe(false);
    expect(requestOf(off)).toMatchObject({ path: "/wireframes/from-points", body: { cloud_id: 8, dz: false } });
    expect(requestOf({ ...off, layer_ids: [21] })).toMatchObject({ path: "/wireframes/build-dtm", body: { dz: false } });
    expect(buildBody({ ...EMPTY_FORM, dz: false, layerIds: [8] }, files).body).not.toHaveProperty("dz"); // no cloud: no map
  });
});

describe("the command `cloud.dz`: the window, the job, a button, the menu of the tree", () => {
  const calls: { url: string; body: unknown }[] = [];
  const state = { files: [file(1, [layer(21, { name: "Контур" })]), file(2, [measured]), file(3, [raw])], wireframes: [ore, relief] };

  async function open() {
    const app = createApp({ persist: false });
    app.registry.hooks = { ...app.registry.hooks, confirm: async () => true };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = String(url).replace(/^.*\/api\/v1/, "");
        const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
        if ((init?.method ?? "GET") !== "GET") {
          calls.push({ url: path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
          return json({ source: "project", id: 77 });
        }
        if (path === "/lines/files") return json({ files: state.files });
        if (path === "/wireframes") return json({ wireframes: state.wireframes });
        if (path === "/rasters") return json({ rasters: [] });
        if (path === "/jobs") return json({ jobs: [], workers: null });
        return json({});
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    app.store.set({ projectName: "Проект" });
    await loadSceneObjects();
    return app;
  }

  beforeEach(() => {
    calls.length = 0;
    windowStore.set({ windows: [] });
    dzJobs.set({});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  });

  it("only cloud_id opens the window; with wireframe_id the job starts and the window follows it", async () => {
    const app = await open();
    expect((await app.registry.execute("cloud.dz", { cloud_id: 8 })).result).toContain("Открыто окно «Карта разностей» для облака «survey8»");
    expect(windowStore.get().windows.map((w) => [w.kind, w.title, w.props])).toEqual([["cloud-dz", "Карта разностей", { cloudId: 8 }]]);
    const r = await app.registry.execute("cloud.dz", { cloud_id: 8, wireframe_id: 42 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("относительно «Земля облака»");
    expect(calls).toEqual([{ url: "/lines/layers/8/dz", body: { wireframe_id: 42 } }]);
    expect(dzJobs.get()["8"]).toEqual({ source: "project", id: 77 });
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: "jobs" });
  });

  it("refusals in Russian with a hint; nothing is sent", async () => {
    const app = await open();
    expect((await app.registry.execute("cloud.dz", { cloud_id: 21, wireframe_id: 42 })).result).toContain("Слой №21 — не облако точек.");
    expect((await app.registry.execute("cloud.dz", { cloud_id: 99, wireframe_id: 42 })).result).toContain("Облако №99 не найдено.");
    expect((await app.registry.execute("cloud.dz", { cloud_id: 8, wireframe_id: 99 })).result).toContain("Каркас или ЦТМ №99 не найдены.");
    expect((await app.registry.execute("cloud.dz", { wireframe_id: 42 })).result).toContain("Не выбрано облако точек."); // two clouds, none selected
    expect(calls).toEqual([]);
    await app.run("object.select", { kind: "lines", id: 7 });
    expect((await app.registry.execute("cloud.dz", { wireframe_id: 41 })).ok).toBe(true); // the selected cloud
    expect(calls).toEqual([{ url: "/lines/layers/7/dz", body: { wireframe_id: 41 } }]);
  });

  it("a button of «Моделирование › Поверхности» beside «Фильтр земли»; the menu of a cloud", () => {
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));
    const at = items.findIndex((i) => i.command === "cloud.dz");
    expect(items[at]).toMatchObject({ group: "Моделирование › Поверхности", label: "Карта разностей" });
    expect(items[at - 1].command).toBe("terrain.from_points");
    expect(layerMenu(measured, true)).toContainEqual({ label: "Карта разностей…", command: "cloud.dz", args: { cloud_id: 8 } });
    expect(layerMenu(layer(21), true).some((a) => a.command === "cloud.dz")).toBe(false);
  });
});
