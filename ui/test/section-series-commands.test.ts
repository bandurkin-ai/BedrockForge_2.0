import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { resolveKey, shortcutMap } from "../src/shell/hotkeys";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { windowStore } from "../src/shell/windows";
import { INITIAL_STATE } from "../src/shell/store";
import { DEFAULT_VIEW } from "../src/shell/workspace";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { viewStore } from "../src/modules/scene/bind";
import type { SeriesRow } from "../src/modules/section/seriesApi";
import { createForm, sceneFocused, sceneKey } from "../src/modules/section/seriesBind";
import { PRINT_REASON, seriesCommands, seriesTab } from "../src/modules/section/seriesCommands";
import { gotoHints, moveIndex, resolveGoto } from "../src/modules/section/goto";
import { seriesStore, seriesView, surfacesHidden } from "../src/modules/section/seriesState";
import { readForm } from "../src/modules/section/seriesForm";
import { sectionOf, seriesRibbon } from "../src/modules/section/state";
import { setCurrentScene } from "../src/modules/scene/sceneHost";
import type { Scene } from "../src/viz/Scene";

const LINE = { x0: 1000, y0: 5000, az: 90, length: null, towards: 12.5, away: 12.5 };

function row(id: number, extra: Partial<SeriesRow["params"]> = {}, name = "Профили"): SeriesRow {
  return {
    id, name, folder: null, version: 1, number: id, count: 5, created_at: "", updated_at: "",
    params: { type: "vertical", p1: [1000, 5000, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null, ...extra },
  };
}
const POLY = row(2, { type: "polyline", polyline: [[0, 0], [100, 0], [100, 100], [0, 100]], corridor: 10, az: null, s0: 0, s1: 0 }, "Ломаный");

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const table = { version: 1, count: 5, sections: [0, 1, 2, 3, 4].map((k) => ({ k, name: `${5000 + 25 * k}N`, s: 25 * k, origin: [1000, 5000 + 25 * k, 0], towards: 12.5, away: 12.5 })) };

let app: App;
const calls: { method: string; path: string; body: Record<string, unknown> }[] = [];

async function open(): Promise<void> {
  app = createApp({ persist: false });
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ method, path, body });
    if (path === "/view" && method === "GET") return json({ values: { section: { value: { line: LINE, clip: true }, version: 1 } } });
    if (path.startsWith("/view/")) return json({ value: body.value, version: 2 });
    if (path === "/section-series" && method === "GET") return json({ series: [row(1), POLY], limit: 1000 });
    if (path === "/section-series" && method === "POST") return json({ ...row(3, body.params), name: body.name ?? "Серия 3" });
    if (path === "/section-series/1/table") return json(table);
    if (path === "/section-series/2/table") return json({ version: 1, count: 1, sections: [{ k: 0, name: "Ломаный", s: 0, origin: [0, 0, 0], towards: 10, away: 10 }] });
    if (path === "/section-series/1" && method === "PATCH") return json({ ...row(1, body.params), version: 2 });
    if (path === "/lines/files") return json({ files: [] });
    if (path === "/rasters") return json({ rasters: [] });
    if (path === "/wireframes") return json({ wireframes: [] });
    return json({ code: "not_found", message: "Нет", hint: "" }, 404);
  }));
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await vi.waitFor(() => expect(seriesStore.get().list).toHaveLength(2));
  await vi.waitFor(() => expect(sectionOf(viewStore.get()).line).toEqual(LINE));
}

const k = () => sectionOf(viewStore.get()).series;

afterEach(() => {
  projectStore.set({ current: null });
  windowStore.set({ windows: [] });
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

describe("commands of series", () => {
  it("Т8.1-8: `]` on the last section keeps k with a message; `Home` — k = 0", async () => {
    await open();
    expect((await app.registry.execute("series.activate", { series_id: 1, k: 4 })).result).toBe("Серия «Профили» активна: разрез 4 из 5");
    expect(k()).toEqual({ id: 1, k: 3, seg: null });
    expect(sectionOf(viewStore.get()).clip).toBe(true);
    expect((await app.registry.execute("series.next")).ok).toBe(true);
    expect(k()?.k).toBe(4);
    await vi.waitFor(() => expect(seriesStore.get().tables[1]).toBeDefined());
    const edge = await app.registry.execute("series.next");
    expect(edge).toEqual({ ok: true, result: "Последний разрез серии: дальше разрезов нет" });
    expect(k()?.k).toBe(4);
    expect(app.store.get().statusNote).toBe("Последний разрез серии: дальше разрезов нет");
    expect(sceneKey({ code: "Home", key: "Home", ctrlKey: false, altKey: false, shiftKey: false, target: null }, false)).toBe("series.first");
    expect((await app.registry.execute("series.first")).result).toBe("Серия «Профили» · разрез 1 из 5 · 5000N");
    expect(k()?.k).toBe(0);
    expect(app.store.get().statusNote).toBeNull();
    expect((await app.registry.execute("series.prev")).result).toBe("Первый разрез серии: раньше разрезов нет");
    expect((await app.registry.execute("series.last")).ok).toBe(true);
    expect(k()?.k).toBe(4);
    // a polyline: the segments
    await app.registry.execute("series.activate", { series_id: 2 });
    expect(k()).toEqual({ id: 2, k: 0, seg: 0 });
    await app.registry.execute("series.next");
    await app.registry.execute("series.next");
    expect(k()?.seg).toBe(2);
    expect((await app.registry.execute("series.next")).result).toBe("Последний сегмент ломаного разреза: дальше сегментов нет");
  });

  it("without a series `]` moves the free line one window on; a free section leaves the series", async () => {
    await open();
    expect((await app.registry.execute("series.first")).result).toMatch(/Нет активной серии/);
    await app.registry.execute("series.next");
    expect(sectionOf(viewStore.get()).line?.y0).toBe(5025); // A = 90°: v̂ = (0, 1, 0), the next profile — one window farther
    await app.registry.execute("series.activate", { series_id: 1, k: 2 });
    // clipping keeps the series
    await app.registry.execute("section.clip", { on: false });
    expect(k()?.k).toBe(1);
    expect(sectionOf(viewStore.get()).clip).toBe(false);
    // S / section.set: the free line again, the series stays in the project
    await app.registry.execute("section.set", { x0: 1, y0: 2, az: 0 });
    expect(k()).toBeUndefined();
    expect(sectionOf(viewStore.get()).line).toMatchObject({ x0: 1, y0: 2, az: 0, towards: 12.5 });
    expect(seriesStore.get().list).toHaveLength(2);
  });

  it("«Перейти» by name or number; the suggestions; the edges of a move", async () => {
    await open();
    await app.registry.execute("series.activate", { series_id: 1 });
    expect((await app.registry.execute("series.goto", { name: " 5050n " })).result).toBe("Серия «Профили» · разрез 3 из 5 · 5050N");
    expect(k()?.k).toBe(2);
    await app.registry.execute("series.goto", { number: 5 });
    expect(k()?.k).toBe(4);
    expect((await app.registry.execute("series.goto", { name: "9999N" })).result).toMatch(/Разрез «9999N» не найден в серии «Профили»: имя разреза или номер от 1 до 5/);
    expect((await app.registry.execute("series.goto")).result).toBe("Открыто окно «Перейти к разрезу»");
    expect(windowStore.get().windows.at(-1)?.kind).toBe("series-goto");
    const plans = [{ k: 0, name: "100" }, { k: 1, name: "90" }, { k: 2, name: "80" }];
    expect(resolveGoto(plans, "100")).toBe(0); // a name wins over a number
    expect(resolveGoto(plans, "2")).toBe(1);
    expect(resolveGoto(plans, 3)).toBe(2);
    expect(resolveGoto(plans, "4")).toBeNull();
    expect(gotoHints(table.sections, "50").map((h) => h.label)).toEqual(["1. 5000N", "2. 5025N", "3. 5050N", "4. 5075N"]);
    expect(gotoHints(table.sections, "75n")).toEqual([{ k: 3, label: "4. 5075N" }]);
    expect(gotoHints(table.sections, "51")).toEqual([{ k: 4, label: "5. 5100N" }]);
    expect(moveIndex(0, 1, 1)).toEqual({ index: 0, edge: true });
    expect(moveIndex(3, 5, "first")).toEqual({ index: 0, edge: false });
  });

  it("every command of series has a button; the print is unavailable with its reason", async () => {
    await open();
    const ribbon = flattenRibbon([seriesTab(DEFAULT_VIEW)]).map((i) => i.command);
    const inWindows = ["series.create", "series.copy", "series.delete", "series.activate", "series.params", "series.rename", "series.window"]; // «Новая серия», «Менеджер серий», the tree
    for (const c of seriesCommands) expect(ribbon.includes(c.id) || inWindows.includes(c.id), c.id).toBe(true);
    expect(app.registry.reason("series.print")).toBe(PRINT_REASON);
    expect((await app.registry.execute("series.print")).ok).toBe(false);
    expect(app.registry.reason("series.lock")).toBeNull(); // the free line is a section
    for (const id of ["series.create", "series.copy", "series.delete", "series.rename", "series.window"]) expect(app.registry.get(id)?.confirm, id).toBe(true);
    for (const id of ["series.new", "series.manager", "series.from_line"]) expect(app.registry.reason(id), id).toBeNull();
    expect((await app.registry.execute("series.manager")).result).toBe("Открыт «Менеджер серий»");
    expect((await app.registry.execute("series.from_line")).ok).toBe(true);
    expect(windowStore.get().windows.at(-1)?.props).toMatchObject({ mode: "create", form: { p1x: "1000", p1y: "5000", s0: "0", s1: "0" } });
  });

  it("«часть каркаса в окне»: off — the surfaces give way to their lines under the section of a series", async () => {
    await open();
    expect(surfacesHidden()).toBe(false);
    expect((await app.registry.execute("series.surfaces")).result).toBe("Каркасы под разрезом серии: только линии сечения");
    expect(surfacesHidden()).toBe(false); // the free line: the surfaces stay
    await app.registry.execute("series.activate", { series_id: 1 });
    expect(surfacesHidden()).toBe(true);
    await app.registry.execute("section.clip", { on: false });
    expect(surfacesHidden()).toBe(false);
    await app.registry.execute("series.surfaces", { on: true });
    expect(seriesView.get().surfaces).toBe(true);
    expect(seriesTab(DEFAULT_VIEW).groups.find((g) => g.title === "Вид")?.items.find((i) => i.command === "series.surfaces")?.active).toBe(true);
  });

  it("series.create from flat arguments; series.shadow writes the series", async () => {
    await open();
    const f = createForm({ type: "plan", x1: 1000, y1: 5000, top: 300.5, bottom: 100, step: 20 });
    expect(readForm(f)).toMatchObject({ params: { type: "plan", p1: [1000, 5000, 300.5], s0: 0, s1: 200.5, step: 20 } });
    expect(readForm(createForm({ type: "vertical", x1: 0, y1: 0, x2: 100, y2: 0, s0: 0, s1: 50, towards: 5, away: 5 }))).toMatchObject({ params: { p2: [100, 0], az: null, step: 10 } });
    const r = await app.registry.execute("series.create", { type: "vertical", x1: 1000, y1: 5000, az: 90, s0: 0, s1: 100, step: 25, name: "Новая" });
    expect(r).toEqual({ ok: true, result: "Серия «Новая» активна: разрез 1 из 5" });
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "Новая", params: { type: "vertical", p1: [1000, 5000, 0], az: 90, s1: 100 } });
    await app.registry.execute("series.activate", { series_id: 1 });
    expect((await app.registry.execute("series.shadow", { mode: "both" })).result).toBe("Тень серии «Профили»: оба");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ expected_version: 1, params: { shadow: "both" } });
  });
});

describe("10.28: the commands of the review", () => {
  const patches = () => calls.filter((c) => c.method === "PATCH");

  it("series.window: the own window of section k (from 1) — the side not given stays; without both — the window of the series", async () => {
    await open();
    const r = await app.registry.execute("series.window", { series_id: 1, k: 3, towards: 10 });
    expect(r).toEqual({ ok: true, result: "Разрез 3 серии «Профили»: своё окно 10 / 12,5 м" });
    expect(patches().at(-1)?.body).toEqual({ expected_version: 1, params: { overrides: { "2": { towards: 10 } } } });
    const back = await app.registry.execute("series.window", { series_id: 1, k: 3 });
    expect(back.result).toBe("Разрез 3 серии «Профили»: окно серии");
    expect(patches().at(-1)?.body).toEqual({ expected_version: 2, params: { overrides: {} } });
    expect((await app.registry.execute("series.window", { series_id: 1, k: 6, towards: 5 })).result).toBe("Разрез 6: в серии «Профили» разрезы с 1 по 5");
    expect((await app.registry.execute("series.window", { series_id: 1, k: 1, away: -1 })).result).toBe("Окно «от себя»: нужно число больше 0, м");
    expect((await app.registry.execute("series.window", { series_id: 2, k: 1, towards: 5 })).result).toMatch(/У ломаного разреза окно — коридор/);
  });

  it("a read-only project: the commands that change series are unavailable (series.digitize had it already)", async () => {
    await open();
    await app.registry.execute("series.activate", { series_id: 1 });
    app.store.set({ projectReadOnly: true });
    for (const id of ["series.create", "series.rename", "series.copy", "series.delete", "series.shadow", "series.window", "series.digitize", "series.stitch"]) expect(app.registry.reason(id), id).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("series.activate")).toBeNull(); // looking is not changing
    expect((await app.registry.execute("series.copy", { series_id: 1 })).ok).toBe(false);
    expect(calls.some((c) => c.path === "/section-series/1/copy")).toBe(false);
  });

  it("«Тень» without a mode toggles off / the previous section; the ribbon knows the shadow of the active series", async () => {
    await open();
    expect(seriesRibbon.get().shadow).toBeNull();
    await app.registry.execute("series.activate", { series_id: 1 });
    expect(seriesRibbon.get().shadow).toBe("prev");
    expect((await app.registry.execute("series.shadow")).result).toBe("Тень серии «Профили»: нет");
    expect(patches().at(-1)?.body).toMatchObject({ params: { shadow: "none" } });
    expect(seriesRibbon.get().shadow).toBe("none");
    const tab = (shadow: typeof seriesRibbon extends { get(): { shadow: infer S } } ? S : never) => flattenRibbon([seriesTab(DEFAULT_VIEW, { shadow })]).find((i) => i.command === "series.shadow")!;
    expect(tab(seriesRibbon.get().shadow).active).toBe(false);
    expect((await app.registry.execute("series.shadow")).result).toBe("Тень серии «Профили»: предыдущий");
    expect(tab(seriesRibbon.get().shadow).active).toBe(true);
    await app.registry.execute("section.set", { x0: 1, y0: 2, az: 0 }); // the free line: no series, no shadow
    expect(seriesRibbon.get().shadow).toBeNull();
  });

  it("«Перейти» on a polyline section goes to its segments (its table has one row)", async () => {
    await open();
    await app.registry.execute("series.activate", { series_id: 2 });
    const r = await app.registry.execute("series.goto", { number: 3 });
    expect(r.result).toBe("Серия «Ломаный» · сегмент 3 из 3");
    expect(k()).toEqual({ id: 2, k: 0, seg: 2 });
  });

  it("a step of `]` shows the section in the scene once: the scene follows the key, the series only when the list comes", async () => {
    await open();
    await app.registry.execute("series.activate", { series_id: 1 });
    const shown: string[] = [];
    const scene = { setSection: () => shown.push("line"), setSectionPlane: () => shown.push("plane"), onRestored: () => () => undefined, onFrame: () => () => undefined, debug: () => ({ sets: [] }) };
    setCurrentScene(scene as unknown as Scene);
    try {
      await app.registry.execute("series.next");
      expect(shown).toEqual([]); // `SceneView` applies the changed key `section` (as for every change of it)
      seriesStore.set((st) => ({ list: st.list.map((r) => (r.id === 1 ? { ...r, version: 9 } : r)) })); // the series read again
      expect(shown).toEqual(["plane"]);
    } finally {
      setCurrentScene(null);
    }
  });
});

describe("keys of series", () => {
  const ev = (code: string, target: unknown, extra: Partial<{ ctrlKey: boolean; repeat: boolean }> = {}) => ({ code, key: code, ctrlKey: false, altKey: false, shiftKey: false, target, ...extra });
  const el = (tagName: string, inside: string[] = []) => ({ tagName, closest: (sel: string) => (sel.split(",").some((s) => inside.includes(s)) ? {} : null) });

  it("Home / End / Ctrl+G only with the focus in the 3D window, never in fields, lists or under a modal window", () => {
    expect(sceneKey(ev("End", el("CANVAS", ['[data-testid="scene-host"]'])), false)).toBe("series.last");
    expect(sceneKey(ev("KeyG", el("BUTTON"), { ctrlKey: true }), false)).toBe("series.goto");
    expect(sceneKey(ev("Home", el("INPUT")), false)).toBeNull();
    expect(sceneKey(ev("Home", { tagName: "DIV", isContentEditable: true }), false)).toBeNull();
    expect(sceneKey(ev("Home", el("DIV", ['[role="tree"]'])), false)).toBeNull();
    expect(sceneKey(ev("Home", el("TD", ["table"])), false)).toBeNull();
    expect(sceneKey(ev("Home", el("DIV", ['[role="dialog"]'])), false)).toBeNull();
    expect(sceneKey(ev("Home", null), true)).toBeNull();
    expect(sceneKey(ev("Home", null, { repeat: true }), false)).toBeNull();
    expect(sceneKey(ev("KeyG", null), false)).toBeNull();
    expect(sceneFocused({ tagName: "BODY" })).toBe(true);
  });

  it("`]` / `[` / `L` are ribbon keys: not in text fields", () => {
    const ws = INITIAL_STATE.workspace;
    const map = shortcutMap(flattenRibbon([...buildRibbon(ws, DEFAULT_VIEW).filter((t) => t.id === "home"), seriesTab(DEFAULT_VIEW)]), {});
    expect(resolveKey("]", map, { typing: false, modal: false })?.command).toBe("series.next");
    expect(resolveKey("[", map, { typing: false, modal: false })?.command).toBe("series.prev");
    expect(resolveKey("l", map, { typing: false, modal: false })?.command).toBe("series.lock");
    for (const key of ["]", "[", "l"]) expect(resolveKey(key, map, { typing: true, modal: false }), key).toBeNull();
    expect(resolveKey("]", map, { typing: false, modal: true })).toBeNull();
    expect([...map.keys()].some((s) => ["home", "end", "ctrl+g"].includes(s))).toBe(false);
  });
});

