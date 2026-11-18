/**
 * «Проверки» and «Источник» of «Свойства» of a wireframe, the commands `wireframe.check` and
 * `model.self_intersections`, and the source of the red layers of the 3D window (ТЗ 7.21; ADR 0013 §3, §5–§6):
 * a closed cube 10 × 10 × 10 shows the volume 1000, an open wireframe — the number of its open edges; what is
 * not known is said so, never 0; «Проверить» starts the job once; the layer is asked with `If-None-Match`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { build } from "./bfa1-build";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { buildRibbon, flattenRibbon, itemReason } from "../src/shell/ribbonConfig";
import { EMPTY_CONTEXT } from "../src/shell/commands";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";
import { jobsStore, type Job } from "../src/modules/jobs/state";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { ChecksSource, checksKey } from "../src/modules/scene/checksSource";
import { loadSceneObjects, pickedObject, sceneObjects, type WireframeChecks, type WireframeInfo, type WireframeStats } from "../src/modules/scene/objects";
import { activeCheck, checkedText, checkError, checkFacts, checkNotes, checkProgress, checksLayerStore, checkSummary, doneChecks, durationText, measureFacts, sourceFacts } from "../src/modules/wireframes/checks";
import type { Scene } from "../src/viz/Scene";
import type { ChecksView } from "../src/viz/mesh/checks";

const NB = " "; // the thousands separator of ru-RU
const facts = (list: { label: string; value: string }[]) => Object.fromEntries(list.map((f) => [f.label, f.value]));

const CUBE: WireframeStats = { area: 600, plan_area: 200, volume: 1000.0000000001, closed: true, orientation: "outward", open_edges: 0, nonmanifold_edges: 0, degenerate: 0 };
const OPEN: WireframeStats = { area: 14_250.456, plan_area: 9000, volume: null, closed: false, orientation: null, open_edges: 7471, nonmanifold_edges: 2, degenerate: 1 };
const AT = "2026-11-18T09:05:07.250+00:00";
const done = (p: Partial<WireframeChecks> = {}): WireframeChecks => ({ state: "done", normal_conflicts: 0, pairs: 0, triangles: 0, listed: 0, seconds: 0.649, checked_at: AT, ...p });
const wf = (id: number, p: Partial<WireframeInfo> = {}): WireframeInfo => ({ id, name: `Каркас ${id}`, version: 1, color: null, point_count: 100, triangle_count: 196, extent: null, dtm: null, stats: CUBE, source: null, ...p });
const job = (p: Partial<Job> = {}): Job =>
  ({ id: 7, source: "project", kind: "wireframe.check", title: "Проверка каркаса", queue: "heavy", status: "running", progress: 0.456, message: null, error: null, result: null, params: { wireframe_ids: [41] }, attempts: 1, max_attempts: 3, cancel_requested: false, created_at: AT, started_at: AT, finished_at: null, why_waiting: null, ...p }) as Job;

describe("«Проверки» of «Свойства» as data", () => {
  it("a closed cube 10 × 10 × 10: the volume 1000, closed, no open edges (the acceptance of 7.21)", () => {
    expect(facts(measureFacts(CUBE))).toEqual({
      Площадь: "600 м²", "Площадь в плане": "200 м²", Объём: `1${NB}000 м³`, Замкнутость: "замкнут",
      "Открытых рёбер": "0", "Неманифолдных рёбер": "0", "Вырожденных треугольников": "0",
    });
  });

  it("an open wireframe shows the number of its open edges and has no volume — never 0", () => {
    const f = facts(measureFacts(OPEN));
    expect(f).toMatchObject({ Площадь: `14${NB}250,46 м²`, Объём: "нет: каркас не замкнут", Замкнутость: "не замкнут", "Открытых рёбер": `7${NB}471`, "Неманифолдных рёбер": "2", "Вырожденных треугольников": "1" });
    // a wireframe stored by an older writer: what was not measured is not shown at all
    expect(measureFacts({ area: 12.5 })).toEqual([{ label: "Площадь", value: "12,5 м²" }]);
    expect(measureFacts(undefined)).toEqual([]);
    expect(measureFacts({ area: 0.123456, closed: true, volume: 0.0312345 }).map((x) => x.value)).toEqual(["0,1235 м²", "0,0312 м³", "замкнут"]);
  });

  it("before the job: the normals and the crossings are «не проверены», after it — what it found", () => {
    expect(facts(checkFacts(CUBE))).toEqual({ Нормали: "смотрят наружу; согласованность не проверена", Самопересечения: "не проверены", Проверено: "не проверялся" });
    expect(facts(checkFacts(OPEN))).toMatchObject({ Нормали: "не проверены" });
    expect(checkNotes(CUBE)).toEqual(["Согласованность нормалей и самопересечения считает задание «Проверка и копии для показа»: нажмите «Проверить»."]);
    const clean = { ...CUBE, checks: done() };
    expect(facts(checkFacts(clean))).toEqual({ Нормали: "согласованы, смотрят наружу", Самопересечения: "нет", Проверено: `${checkedText(AT)}, за 0,65 с` });
    expect(facts(checkFacts({ ...CUBE, orientation: "inward", checks: done() })).Нормали).toBe("согласованы, смотрят внутрь");
    const bad = { ...OPEN, checks: done({ normal_conflicts: 1860, pairs: 2723, triangles: 2057, listed: 2057, seconds: 75.4 }) };
    expect(facts(checkFacts(bad))).toEqual({ Нормали: `конфликтов: 1${NB}860`, Самопересечения: `пар: 2${NB}723, треугольников: 2${NB}057`, Проверено: `${checkedText(AT)}, за 1 мин 15 с` });
    expect(doneChecks(bad)?.pairs).toBe(2723);
    expect(doneChecks(CUBE)).toBeNull();
  });

  it("the time of a check is the time of the computer; durations are short", () => {
    const d = new Date(AT);
    const two = (n: number) => String(n).padStart(2, "0");
    expect(checkedText(AT)).toBe(`${two(d.getDate())}.${two(d.getMonth() + 1)}.${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`);
    expect(checkedText("когда-то")).toBe("когда-то");
    expect([durationText(0.649), durationText(6.2), durationText(27.61), durationText(59.6), durationText(185)]).toEqual(["0,65 с", "6,2 с", "28 с", "60 с", "3 мин 05 с"]);
  });

  it("notes: where the crossing triangles are seen, what the check does not find, a list that is gone", () => {
    const coplanar = "Проверка находит треугольники, которые протыкают друг друга; наложения в одной плоскости она не находит.";
    expect(checkNotes({ ...CUBE, checks: done() })).toEqual([coplanar]);
    const some = { ...OPEN, checks: done({ pairs: 12, triangles: 20, listed: 20 }) };
    expect(checkNotes(some)).toEqual(["Пересекающиеся треугольники показаны в 3D-окне красным.", coplanar]);
    const many = { ...OPEN, checks: done({ pairs: 900_000, triangles: 450_000, listed: 200_000 }) };
    expect(checkNotes(many)[0]).toBe(`Пересекающиеся треугольники показаны в 3D-окне красным: первые 200${NB}000 из 450${NB}000.`);
    expect(checkNotes(some, true)[0]).toBe("Список пересекающихся треугольников для 3D-окна не сохранён в кеше проекта: нажмите «Проверить» — он будет построен заново.");
    expect(checkNotes({ ...CUBE, checks: done() }, true)).toEqual([coplanar]); // nothing crosses: no list is needed
  });

  it("a check that could not be made tells why, with the hint of the core", () => {
    const failed: WireframeStats = { ...OPEN, checks: { state: "failed", checked_at: AT, error: { code: "bad_coordinates", message: "Координаты каркаса слишком велики для проверки самопересечений.", hint: "Проверьте исходные данные: размер каркаса больше 10¹⁵ м." } } };
    expect(checkError(failed)).toBe("Проверка не выполнена. Координаты каркаса слишком велики для проверки самопересечений. Проверьте исходные данные: размер каркаса больше 10¹⁵ м.");
    expect(facts(checkFacts(failed))).toEqual({ Нормали: "не проверены", Самопересечения: "не проверены", Проверено: checkedText(AT) });
    expect(checkNotes(failed)).toEqual([]);
    expect(checkError(CUBE)).toBeNull();
    expect(checkError({ ...CUBE, checks: done() })).toBeNull();
    expect(checkSummary("Тело", failed)).toContain("«Тело»: Проверка не выполнена.");
  });

  it("the source of an imported wireframe: the file, the format, the name in the file, float32 of an STL", () => {
    expect(sourceFacts(null)).toEqual([]);
    expect(sourceFacts({})).toEqual([]);
    expect(facts(sourceFacts({ format: "stl", file: "ore.stl", name: "solid ore", precision: "float32" }))).toEqual({ Файл: "ore.stl", Формат: "STL", "Имя в файле": "solid ore", Точность: "координаты float32 (как в файле)" });
    expect(facts(sourceFacts({ format: "tridb", file: "ORE SEP 2025.tridb", name: "Zone 1" }))).toEqual({ Файл: "ORE SEP 2025.tridb", Формат: "Micromine TRIDB", "Имя в файле": "Zone 1" });
    expect(facts(sourceFacts({ format: "dxf", name: "FAULTS", layer: "FAULTS" }))).toEqual({ Формат: "AutoCAD DXF", "Имя в файле": "FAULTS" }); // a layer named as the wireframe is told once
    expect(facts(sourceFacts({ format: "dxf", name: "Тело", layer: "0" })).Слой).toBe("0");
    expect(sourceFacts({ format: "vulcan" })).toEqual([{ label: "Формат", value: "vulcan" }]);
  });

  it("the job that checks a wireframe now, and how far it is", () => {
    const jobs = [job({ id: 5, status: "done" }), job({ id: 6, kind: "model.dtm" }), job({ id: 7, params: { wireframe_ids: [40, 41] } }), job({ id: 8, status: "queued", params: { wireframe_ids: [42] } })];
    expect(activeCheck(jobs, 41)?.id).toBe(7);
    expect(activeCheck(jobs, 42)?.id).toBe(8);
    expect(activeCheck(jobs, 43)).toBeNull();
    expect(activeCheck([job({ params: null }), job({ params: undefined })], 41)).toBeNull();
    expect([checkProgress(job()), checkProgress(job({ status: "queued" })), checkProgress(job({ progress: 1.7 }))]).toEqual(["Проверяется: 46 %", "В очереди…", "Проверяется: 100 %"]);
  });

  it("what a command says about a checked wireframe", () => {
    expect(checkSummary("Куб", CUBE)).toBe("«Куб» не проверялся");
    expect(checkSummary("Куб", { ...CUBE, checks: done() })).toBe("«Куб»: нормали согласованы, самопересечений нет");
    expect(checkSummary("Разломы", { ...OPEN, checks: done({ pairs: 2723, triangles: 2057, listed: 2057 }) })).toBe(`«Разломы»: нормали согласованы; самопересечений — пар: 2${NB}723, треугольников: 2${NB}057 (красные в 3D-окне)`);
    expect(checkSummary("Тело", { ...CUBE, checks: done({ normal_conflicts: 3 }) })).toBe("«Тело»: конфликтов нормалей: 3; самопересечений нет");
  });
});

// --- the commands ------------------------------------------------------------------------------------------

interface Call {
  method: string;
  url: string;
}

function server(wireframes: WireframeInfo[], jobs: Job[] = []) {
  const calls: Call[] = [];
  const state = { wireframes, jobs, refuse: false };
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ method, url: path });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET") {
      if (path === "/lines/files") return json({ files: [] });
      if (path === "/rasters") return json({ rasters: [] });
      if (path === "/wireframes") return json({ wireframes: state.wireframes });
      if (path === "/jobs") return json({ jobs: state.jobs, workers: null });
      return json({});
    }
    if (state.refuse) return json({ code: "wireframe_not_found", message: "Каркас не найден.", hint: "Обновите дерево проекта." }, 404);
    const started = job({ id: 9, status: "queued", params: { wireframe_ids: [Number(/\/wireframes\/(\d+)\/check/.exec(path)?.[1])] } });
    state.jobs = [started, ...state.jobs];
    return json({ source: "project", id: 9 });
  });
  return { state, calls, fetch };
}

async function open(wireframes: WireframeInfo[], jobs: Job[] = []) {
  const app = createApp({ persist: false });
  const core = server(wireframes, jobs);
  vi.stubGlobal("fetch", core.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await loadSceneObjects();
  return { app, core };
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  pickedObject.set({ picked: null });
  jobsStore.set({ jobs: [], workers: null, error: null });
  checksLayerStore.set({ missing: [] });
});

describe("`wireframe.check` and `model.self_intersections` (ADR 0013 §6)", () => {
  it("are in the registry with a Russian description, bound, on the ribbon in «Правка каркасов»", () => {
    const app = createApp({ persist: false });
    const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));
    for (const id of ["wireframe.check", "model.self_intersections"]) {
      const c = app.registry.get(id)!;
      expect(c.stage, id).toBeUndefined(); // bound: not «появится на этапе…»
      expect(c.run, id).toBeTypeOf("function");
      expect(c.description, id).toMatch(/[А-Яа-яЁё]{4,}/);
      expect([c.confirm ?? false, c.uiOnly ?? false], id).toEqual([false, false]); // the data of the wireframe do not change
      expect(Object.keys(c.params!), id).toEqual(["wireframe_id"]);
      expect(app.registry.reason(id), id).toBe("Откройте или создайте проект");
      expect(items.find((i) => i.command === id)?.group, id).toBe("Моделирование › Правка каркасов");
    }
    const button = items.find((i) => i.command === "wireframe.check")!;
    expect(button.label).toBe("Проверить");
    expect(itemReason(button, EMPTY_CONTEXT, () => null)).toContain("Объект не выбран");
    expect(itemReason(button, { ...EMPTY_CONTEXT, hasObject: true }, () => null)).toBeNull();
  });

  it("«Проверить» starts the job of the wireframe once: a wireframe being checked is not queued again", async () => {
    const { app, core } = await open([wf(41, { name: "Рудное тело" }), wf(42, { name: "Рельеф" })]);
    const first = await app.registry.execute("wireframe.check", { wireframe_id: 41 });
    expect(first.ok).toBe(true);
    expect(first.result).toContain("Проверка «Рудное тело» запущена: задание №9");
    expect(core.calls).toEqual([{ method: "POST", url: "/wireframes/41/check" }]);
    await vi.waitFor(() => expect(activeCheck(jobsStore.get().jobs, 41)?.id).toBe(9)); // the list of jobs was read again
    const again = await app.registry.execute("wireframe.check", { wireframe_id: 41 });
    expect(again).toEqual({ ok: true, result: "«Рудное тело» уже проверяется: задание №9. Итог появится в «Свойствах»" });
    expect(core.calls).toHaveLength(1);
    // without an id — the selected wireframe
    await app.run("object.select", { kind: "wireframe", id: 42 });
    expect((await app.registry.execute("wireframe.check")).result).toContain("Проверка «Рельеф» запущена");
    expect(core.calls[1]).toEqual({ method: "POST", url: "/wireframes/42/check" });
    await vi.waitFor(() => expect(activeCheck(jobsStore.get().jobs, 42)?.id).toBe(9)); // the refresh it started is over
  });

  it("a job started elsewhere is seen before another one is queued", async () => {
    const { app, core } = await open([wf(41, { name: "Рудное тело" })], [job({ id: 3, status: "queued" })]);
    expect(jobsStore.get().jobs).toEqual([]); // this window has not read the list yet
    expect((await app.registry.execute("wireframe.check", { wireframe_id: 41 })).result).toBe("«Рудное тело» уже проверяется: задание №3. Итог появится в «Свойствах»");
    expect(core.calls).toEqual([]);
  });

  it("tells what is wrong: nothing selected, no such wireframe, no triangles, a refusal of the core with its hint", async () => {
    const { app, core } = await open([wf(41), wf(43, { name: "Пустой", triangle_count: 0 })]);
    expect(await app.registry.execute("wireframe.check")).toEqual({ ok: false, result: "Выберите каркас в дереве или укажите wireframe_id" });
    expect((await app.registry.execute("wireframe.check", { wireframe_id: 99 })).result).toBe("Каркас №99 не найден: обновите дерево проекта");
    expect((await app.registry.execute("wireframe.check", { wireframe_id: 43 })).result).toBe("В каркасе «Пустой» нет треугольников: проверять нечего");
    core.state.refuse = true;
    expect(await app.registry.execute("wireframe.check", { wireframe_id: 41 })).toEqual({ ok: false, result: "Каркас не найден. Обновите дерево проекта." });
    expect(app.store.get().statusNote).toBe("Каркас не найден. Обновите дерево проекта."); // the ribbon has no other place for it
  });

  it("«Самопересечения»: a checked wireframe is selected, shown and told about; one not checked yet is checked", async () => {
    const stats = { ...OPEN, checks: done({ pairs: 12, triangles: 20, listed: 20 }) };
    const { app, core } = await open([wf(41, { name: "Разломы", stats }), wf(42, { name: "Куб" })]);
    app.store.set((s) => ({ view: { ...s.view, hidden: ["wf:41"] } }));
    const told = await app.registry.execute("model.self_intersections", { wireframe_id: 41 });
    expect(told).toEqual({ ok: true, result: "«Разломы»: нормали согласованы; самопересечений — пар: 12, треугольников: 20 (красные в 3D-окне)" });
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 41 });
    expect(app.store.get().view.hidden).not.toContain("wf:41"); // the red triangles follow their wireframe
    expect(core.calls).toEqual([]);
    expect((await app.registry.execute("model.self_intersections", { wireframe_id: 42 })).result).toContain("Проверка «Куб» запущена");
    expect(core.calls).toEqual([{ method: "POST", url: "/wireframes/42/check" }]);
  });
});

// --- the red layers in the scene -----------------------------------------------------------------------------

/** A core that serves `GET /wireframes/{id}/checks` with ETags; `layers[id]` — the triangles of the answer, or a state. */
function layerServer(layers: Record<number, number | "missing" | "broken">) {
  const asked: { id: number; tag: string | null; status: number }[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const id = Number(/\/wireframes\/(\d+)\/checks$/.exec(String(url))![1]);
    const what = layers[id];
    const tag = `"${id}:${String(what)}"`;
    const sent = new Headers(init?.headers).get("If-None-Match");
    const status = what === "broken" ? 500 : sent === tag ? 304 : 200;
    asked.push({ id, tag: sent, status });
    if (status === 500) return new Response(JSON.stringify({ code: "internal", message: "Внутренняя ошибка ядра.", hint: "Отправьте журнал разработчику." }), { status });
    if (status === 304) return new Response(null, { status, headers: { ETag: tag } });
    const n = typeof what === "number" ? what : 0;
    const header = { kind: "checks", format: 1, state: what === "missing" ? "missing" : "done", triangles: n, crossing: what === "missing" ? 20 : n, groups: n ? [{ origin: [512_346, 6_234_567, 412], first: 0, n }] : [], bbox: null };
    return new Response(build(header, [["xyz", "<f4", new Float32Array(n * 9).fill(1.5)], ["tri", "<u4", Uint32Array.from({ length: n }, (_, i) => i)]]), { status, headers: { ETag: tag } });
  });
  return { asked, fetch };
}

function fakeScene() {
  const layers = new Map<string, ChecksView>();
  const scene = {
    clipPlanes: [],
    invalidate: vi.fn(),
    addLayer: vi.fn((key: string, layer: ChecksView) => void layers.set(key, layer)),
    removeLayer: vi.fn((key: string) => void layers.delete(key)),
  };
  return { scene: scene as unknown as Scene, layers, calls: scene };
}

describe("the red layers of checked wireframes in the scene", () => {
  const checked = (id: number, listed: number, at = AT): WireframeInfo => wf(id, { stats: { ...OPEN, checks: done({ pairs: listed, triangles: listed, listed, checked_at: at }) } });

  async function start(layers: Record<number, number | "missing" | "broken">, list: WireframeInfo[], off: string[] = []) {
    const core = layerServer(layers);
    vi.stubGlobal("fetch", core.fetch);
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const { scene, layers: shown, calls } = fakeScene();
    const state = { list, hidden: off };
    const source = new ChecksSource(scene, { wireframes: () => state.list, hidden: () => state.hidden });
    const settle = async () => {
      source.sync();
      await vi.waitFor(() => expect(source.idle).toBe(true));
    };
    await settle();
    return { core, source, shown, calls, state, settle };
  }

  it("only a shown wireframe with crossing triangles is asked; its layer follows its visibility", async () => {
    const t = await start({ 1: 2, 4: 3 }, [checked(1, 2), wf(2), checked(3, 0), checked(4, 3)], ["wf:4"]);
    expect(t.core.asked).toEqual([{ id: 1, tag: null, status: 200 }]); // 2 was not checked, 3 crosses nowhere, 4 is hidden
    expect([...t.shown.keys()]).toEqual([checksKey(1)]);
    expect(t.source.stats()).toEqual([{ wireframe: 1, triangles: 2, groups: 1, visible: true }]);
    expect(t.shown.get("chk:1")!.origin).toEqual({ x: 512_346, y: 6_234_567, z: 412 });
    // hidden: the layer stays loaded and is not seen; the other wireframe is shown: its layer comes
    t.state.hidden = ["wf:1"];
    await t.settle();
    expect(t.source.stats()).toEqual([{ wireframe: 1, triangles: 2, groups: 1, visible: false }, { wireframe: 4, triangles: 3, groups: 1, visible: true }]);
    expect(t.core.asked).toHaveLength(2);
    t.state.hidden = [];
    await t.settle();
    expect(t.source.stats().map((s) => s.visible)).toEqual([true, true]);
    expect(t.core.asked).toHaveLength(2); // nothing is asked for a change of visibility
  });

  it("a new check asks again with the ETag: the same triangles are a 304, other ones replace the layer", async () => {
    const layers: Record<number, number | "missing" | "broken"> = { 1: 2 };
    const t = await start(layers, [checked(1, 2)]);
    const first = t.shown.get("chk:1");
    t.state.list = [checked(1, 2, "2026-11-18T10:00:00.000+00:00")]; // «Проверить» again: the same result
    await t.settle();
    expect(t.core.asked[1]).toEqual({ id: 1, tag: '"1:2"', status: 304 });
    expect(t.shown.get("chk:1")).toBe(first);
    expect(t.calls.addLayer).toHaveBeenCalledTimes(1);
    layers[1] = 5; // the geometry is the same, the check of a newer program finds more
    t.state.list = [checked(1, 5, "2026-11-18T11:00:00.000+00:00")];
    await t.settle();
    expect(t.core.asked[2].status).toBe(200);
    expect(t.shown.get("chk:1")).not.toBe(first);
    expect(t.source.stats()).toEqual([{ wireframe: 1, triangles: 5, groups: 1, visible: true }]);
    // a check that finds nothing, and a deleted wireframe: the layer leaves
    t.state.list = [checked(1, 0, "2026-11-18T12:00:00.000+00:00")];
    await t.settle();
    expect([...t.shown.keys()]).toEqual([]);
    expect(t.core.asked).toHaveLength(3);
    // found again later: asked in full — the ETag of a layer that left is forgotten
    t.state.list = [checked(1, 5, "2026-11-18T13:00:00.000+00:00")];
    await t.settle();
    expect(t.core.asked[3]).toEqual({ id: 1, tag: null, status: 200 });
    expect(t.source.stats()).toHaveLength(1);
  });

  it("a list that is not in the cache is told to «Свойства»; «Проверить» brings the layer back", async () => {
    const layers: Record<number, number | "missing" | "broken"> = { 1: "missing" };
    const t = await start(layers, [checked(1, 20)]);
    expect(t.source.stats()).toEqual([]);
    expect(checksLayerStore.get().missing).toEqual([1]);
    layers[1] = 20;
    t.state.list = [checked(1, 20, "2026-11-18T10:00:00.000+00:00")];
    await t.settle();
    expect(t.core.asked[1]).toEqual({ id: 1, tag: null, status: 200 });
    expect(checksLayerStore.get().missing).toEqual([]);
    expect(t.source.stats()).toEqual([{ wireframe: 1, triangles: 20, groups: 1, visible: true }]);
    // the wireframe is deleted while its list is missing: it is not named any more
    layers[1] = "missing";
    t.state.list = [checked(1, 20, "2026-11-18T11:00:00.000+00:00")];
    await t.settle();
    expect(checksLayerStore.get().missing).toEqual([1]);
    t.state.list = [];
    await t.settle();
    expect(checksLayerStore.get().missing).toEqual([]);
  });

  it("a failure is asked again with the next change; after a lost context everything comes in full; dispose removes the layers", async () => {
    const layers: Record<number, number | "missing" | "broken"> = { 1: "broken", 2: 4 };
    const t = await start(layers, [checked(1, 2), checked(2, 4)]);
    expect(t.source.stats().map((s) => s.wireframe)).toEqual([2]);
    layers[1] = 2;
    await t.settle(); // the list changed elsewhere: the failed layer is tried again
    expect(t.source.stats().map((s) => s.wireframe).sort()).toEqual([1, 2]);
    const before = t.core.asked.length;
    t.shown.clear(); // the scene dropped its layers with the context
    t.source.restore();
    await vi.waitFor(() => expect(t.source.idle).toBe(true));
    expect(t.core.asked.slice(before).map((a) => [a.id, a.tag, a.status])).toEqual([[1, null, 200], [2, null, 200]]);
    expect([...t.shown.keys()].sort()).toEqual(["chk:1", "chk:2"]);
    t.source.dispose();
    expect([...t.shown.keys()]).toEqual([]);
    t.source.sync(); // after dispose nothing is asked
    expect(t.core.asked).toHaveLength(before + 2);
  });
});
