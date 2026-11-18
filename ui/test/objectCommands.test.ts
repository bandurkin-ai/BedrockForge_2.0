/** Commands of lines, rasters, wireframes and DTMs (ТЗ 7.2, 7.18–7.20, 7.35 Д-07; ADR 0011 §5): every one is
 * in the registry with a Russian description and a ribbon button; the ones that change data are `confirm`; the
 * handlers send the version of the object and what was asked — an empty height stays empty. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { windowStore } from "../src/shell/windows";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { loadSceneObjects, pickedObject, sceneObjects, type LineFileInfo, type LineLayerInfo, type RasterInfo } from "../src/modules/scene/objects";
import type { WireframeInfo } from "../src/modules/wireframes/state";

/** The commands of Э7 objects: lines, rasters, wireframes, DTM. */
const OBJECT_COMMANDS = ["lines.export", "rasters.export", "raster.set", "raster.shift", "raster.place_on_section", "wireframe.dtm", "wireframe.attr", "dtm.settings", "model.dtm", "object.select", "object.rename", "object.color", "object.delete"];
const CHANGE_DATA = ["raster.shift", "raster.place_on_section", "wireframe.dtm", "wireframe.attr", "object.rename", "object.color", "object.delete"];

const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: 1, name: `Слой ${id}`, version: 3, color: null, points_only: false, line_count: 12, point_count: 340, length: 1520.46,
  closed_count: 0, closed: "none", flat: false, extent: null, attr_schema: [], ...p,
});
const raster = (id: number, p: Partial<RasterInfo> = {}): RasterInfo => ({
  id, name: `Растр ${id}`, version: 2, width: 4000, height: 3000, georef: null, elevation: null, vertical: false, opacity: 1, has_elevation: false,
  displays: [], images: {}, warnings: [], ...p,
});
const DTM = { raster_id: null, clip: false, tint: "#ffffff", opacity: 1, shade: true };

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | undefined;
}

/** A small core: the lists of the project and the changes the commands make. */
function server() {
  const state = {
    files: [{ id: 1, name: "roads.dxf", folder: null, version: 1, format: "dxf", label: null, filename: null, crs_name: null, warnings: [], layers: [layer(11, { name: "Дороги" }), layer(12, { name: "Реперы", points_only: true, line_count: 0, point_count: 5 })] }] as LineFileInfo[],
    rasters: [raster(31, { name: "Ортофото", elevation: 250.5, has_elevation: true }), raster(32, { name: "Карта" }), raster(33, { name: "Скан", vertical: true, has_elevation: true })],
    wireframes: [
      { id: 41, name: "Рудное тело", version: 1, color: "#e8873a", point_count: 100, triangle_count: 196, extent: null, dtm: null, attrs: [{ name: "Блок", value: 12.5 }] },
      { id: 42, name: "Рельеф", version: 4, color: null, point_count: 16, triangle_count: 18, extent: null, dtm: DTM, attrs: [] },
    ] as WireframeInfo[],
  };
  const calls: Call[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    if (method !== "GET") calls.push({ method, url: path, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET") {
      if (path === "/lines/files") return json({ files: state.files });
      if (path === "/rasters") return json({ rasters: state.rasters });
      if (path === "/wireframes") return json({ wireframes: state.wireframes });
      if (path === "/jobs") return json({ jobs: [], workers: null });
      return json({});
    }
    const rasterAt = /^\/rasters\/(\d+)/.exec(path);
    if (rasterAt && method !== "DELETE" && !path.endsWith("/export")) {
      const r = state.rasters.find((x) => x.id === Number(rasterAt[1]))!;
      if (body?.expected_version !== r.version) return json({ code: "version_conflict", message: "Растр изменили, пока вы его правили.", hint: "Повторите правку." }, 409);
      const next = { ...r, version: r.version + 1 };
      if (path.endsWith("/shift")) next.elevation = (r.elevation ?? 0) + Number(body?.dz);
      else if (path.endsWith("/place-on-section")) Object.assign(next, { vertical: true, elevation: null });
      else {
        if (body && "elevation" in body) Object.assign(next, { elevation: body.elevation, has_elevation: body.elevation !== null });
        if (typeof body?.opacity === "number") next.opacity = body.opacity;
        if (typeof body?.name === "string") next.name = body.name;
        if (body?.vertical) next.vertical = true;
      }
      state.rasters = state.rasters.map((x) => (x.id === r.id ? next : x));
      return json(next);
    }
    if (rasterAt && method === "DELETE") {
      if (Number(rasterAt[1]) === 32) return json({ code: "raster_in_use", message: "Растр наложен на ЦТМ: «Рельеф».", hint: "Снимите растр в настройках ЦТМ." }, 422);
      state.rasters = state.rasters.filter((x) => x.id !== Number(rasterAt[1]));
      return json({ deleted: Number(rasterAt[1]) });
    }
    const wfAt = /^\/wireframes\/(\d+)/.exec(path);
    if (wfAt && method !== "DELETE") {
      const w = state.wireframes.find((x) => x.id === Number(wfAt[1]))!;
      const next = { ...w, version: w.version + 1 };
      if (path.endsWith("/dtm")) {
        const { expected_version: _v, on, raster_id, ...rest } = body as { expected_version: number; on?: boolean; raster_id?: number } & Record<string, unknown>;
        next.dtm = on === false ? null : { ...(w.dtm ?? DTM), ...rest, ...(raster_id === undefined ? {} : { raster_id: raster_id || null }) };
      } else if (path.endsWith("/attrs")) {
        const patch = body as { rename?: Record<string, string>; remove?: string[]; set?: Record<string, string | number | boolean | null> };
        let attrs = (w.attrs ?? []).map((a) => ({ ...a, name: patch.rename?.[a.name] ?? a.name })).filter((a) => !patch.remove?.includes(a.name));
        for (const [name, value] of Object.entries(patch.set ?? {})) attrs = attrs.some((a) => a.name === name) ? attrs.map((a) => (a.name === name ? { name, value } : a)) : [...attrs, { name, value }];
        next.attrs = attrs;
      } else Object.assign(next, typeof body?.name === "string" ? { name: body.name } : {}, body && "color" in body ? { color: body.color } : {});
      state.wireframes = state.wireframes.map((x) => (x.id === w.id ? next : x));
      return json(next);
    }
    if (wfAt) state.wireframes = state.wireframes.filter((x) => x.id !== Number(wfAt[1]));
    const layerAt = /^\/lines\/layers\/(\d+)$/.exec(path);
    if (layerAt) {
      state.files = state.files.map((f) => ({ ...f, layers: f.layers.map((l) => (l.id === Number(layerAt[1]) ? { ...l, version: l.version + 1, ...(typeof body?.name === "string" ? { name: body.name } : {}), ...(body && "color" in body ? { color: body.color as string | null } : {}) } : l)) }));
      return json({});
    }
    if (/^\/lines\/files\/\d+$/.test(path)) state.files = [];
    return json({ source: "project", id: 7 });
  });
  return { state, calls, fetch };
}

let core: ReturnType<typeof server>;

async function open() {
  const app = createApp({ persist: false });
  app.registry.hooks = { ...app.registry.hooks, confirm: async () => true }; // Claude's calls are approved
  core = server();
  vi.stubGlobal("fetch", core.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  await loadSceneObjects();
  core.calls.length = 0;
  return app;
}

/** Answers the own dialog a command opened (`askText`, `askConfirm`). */
async function answer(value: string | boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind === "text") d.resolve(typeof value === "string" ? value : null);
  else if (d.kind === "confirm") d.resolve(value === true);
  return d.kind === "message" ? d.options.message : d.kind === "confirm" ? d.options.message : d.options.title;
}

beforeEach(() => {
  windowStore.set({ windows: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  pickedObject.set({ picked: null });
  dialogStore.set({ queue: [] });
});

describe("the registry of object commands (ТЗ 7.2; ADR 0011 §5)", () => {
  const app = createApp({ persist: false });
  const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));

  it("every command is registered, bound and described in Russian for people and for the model", () => {
    for (const id of OBJECT_COMMANDS) {
      const c = app.registry.get(id);
      expect(c, id).toBeDefined();
      expect(c!.stage, id).toBeUndefined(); // bound: not «появится на этапе…»
      expect(c!.run, id).toBeTypeOf("function");
      expect(c!.title, id).toMatch(/[А-Яа-яЁё]/);
      expect(c!.description.length, id).toBeGreaterThanOrEqual(40);
      expect(c!.description, id).toMatch(/[А-Яа-яЁё]{4,}/);
    }
  });

  it("every command has a ribbon button", () => {
    const onRibbon = new Set(items.map((i) => i.command));
    expect(OBJECT_COMMANDS.filter((id) => id !== "object.select" && !onRibbon.has(id))).toEqual([]); // object.select is the click itself
    expect(items.find((i) => i.command === "model.dtm")?.group).toBe("Моделирование › Поверхности");
    expect(items.find((i) => i.command === "dtm.settings")?.label).toBe("Настройки ЦТМ");
    expect(items.filter((i) => i.command === "lines.export").map((i) => i.args?.format)).toEqual(["str", "dxf"]);
    expect(items.find((i) => i.command === "rasters.export")?.group).toBe("Моделирование › Растры");
  });

  it("commands that change data are `confirm`; exports and windows are not", () => {
    expect(OBJECT_COMMANDS.filter((id) => app.registry.get(id)!.confirm)).toEqual(CHANGE_DATA);
    expect(app.registry.get("model.dtm")!.uiOnly).toBe(true); // a dialog: a person fills it
  });

  it("the formats of the exports and the parameters named in the ADR", () => {
    expect(app.registry.get("lines.export")!.params!.format.enum).toEqual(["str", "surpac", "dxf", "csv", "dat"]);
    expect(app.registry.get("rasters.export")!.params!.format.enum).toEqual(["source", "geotiff"]);
    expect(Object.keys(app.registry.get("raster.set")!.params!)).toEqual(["raster_id", "elevation", "opacity", "x1", "y1", "x2", "y2", "z_top", "z_bottom"]);
    expect(Object.keys(app.registry.get("raster.place_on_section")!.params!)).toEqual(["raster_id", "z_top"]);
    expect(Object.keys(app.registry.get("wireframe.dtm")!.params!).sort()).toEqual(["clip", "on", "opacity", "raster_id", "shade", "tint", "wireframe_id"]);
  });

  it("without a project the commands tell why they are not available", () => {
    for (const id of OBJECT_COMMANDS) expect(app.registry.reason(id), id).toBe("Откройте или создайте проект");
  });
});

describe("rasters: `raster.set`, «Сдвинуть», «На текущий разрез» (ТЗ 7.19)", () => {
  it("the height and the opacity go with the version of the raster; an empty height is null, not 0", async () => {
    const app = await open();
    const set = await app.registry.execute("raster.set", { raster_id: 32, elevation: 120.25, opacity: 0.6 });
    expect(set).toEqual({ ok: true, result: "Растр «Карта»: в плане, высота 120,25 м, непрозрачность 0,6" });
    expect(core.calls).toEqual([{ method: "PATCH", url: "/rasters/32", body: { expected_version: 2, elevation: 120.25, opacity: 0.6 } }]);
    await app.registry.execute("raster.set", { raster_id: 32, elevation: null });
    expect(core.calls[1].body).toEqual({ expected_version: 3, elevation: null }); // the list was read again: the new version
    expect(sceneObjects.get().rasters.find((r) => r.id === 32)?.elevation).toBeNull();
    expect((await app.registry.execute("raster.set", { raster_id: 32, opacity: 0.01 })).result).toBe("Непрозрачность растра — число от 0,05 до 1");
    expect((await app.registry.execute("raster.set", { raster_id: 33, elevation: 5 })).result).toContain("У вертикального растра нет одной высоты");
    expect((await app.registry.execute("raster.set", { raster_id: 99, opacity: 0.5 })).result).toBe("Растр №99 не найден: обновите дерево проекта");
  });

  it("the six edges of a vertical raster are set together", async () => {
    const app = await open();
    expect((await app.registry.execute("raster.set", { raster_id: 33, x1: 1, y1: 2 })).result).toBe("Края вертикального растра задаются вместе: x1, y1, x2, y2, z_top, z_bottom");
    const edges = { x1: 5000.5, y1: 8000, x2: 5400, y2: 8100.25, z_top: 320, z_bottom: 120 };
    expect((await app.registry.execute("raster.set", { raster_id: 33, ...edges })).ok).toBe(true);
    expect(core.calls).toEqual([{ method: "PATCH", url: "/rasters/33", body: { expected_version: 2, vertical: edges } }]);
  });

  it("without values `raster.set` opens «Свойства» of the selected raster", async () => {
    const app = await open();
    app.store.set((s) => ({ workspace: { ...s.workspace, right: false } }));
    await app.run("object.select", { kind: "raster", id: 31 });
    expect((await app.registry.execute("raster.set")).result).toContain("Свойства растра «Ортофото»");
    expect([app.store.get().workspace.right, app.store.get().workspace.rightTab]).toEqual([true, "properties"]);
    expect(core.calls).toEqual([]);
  });

  it("«Сдвинуть»: −5 lowers the raster; without a height there is nothing to move", async () => {
    const app = await open();
    const moved = await app.registry.execute("raster.shift", { raster_id: 31, dz: -5 }, "claude");
    expect(moved).toEqual({ ok: true, result: "Растр «Ортофото» сдвинут по Z на −5 м: в плане, высота 245,5 м, непрозрачность 1" });
    expect(core.calls).toEqual([{ method: "POST", url: "/rasters/31/shift", body: { expected_version: 2, dz: -5 } }]);
    const none = await app.registry.execute("raster.shift", { raster_id: 32, dz: 5 });
    expect(none).toEqual({ ok: false, result: "У растра не задана высота: сдвигать нечего. Введите «Высоту» и нажмите «Применить»." });
    expect(app.store.get().statusNote).toBe(none.result); // a failure of a call from the interface is seen in the status bar
    expect((await app.registry.execute("raster.shift", { raster_id: 31 }, "claude")).result).toBe("Укажите сдвиг по Z в метрах: dz");
    expect(core.calls).toHaveLength(1);
  });

  it("«Сдвинуть» of the ribbon asks the shift in an own window; a comma is a decimal sign", async () => {
    const app = await open();
    await app.run("object.select", { kind: "raster", id: 31 });
    const run = app.registry.execute("raster.shift");
    expect(await answer("2,5")).toBe("Сдвинуть растр «Ортофото» по Z");
    expect((await run).result).toContain("сдвинут по Z на +2,5 м");
    expect(core.calls[0].body).toEqual({ expected_version: 2, dz: 2.5 });
    const bad = app.registry.execute("raster.shift");
    await answer("вверх");
    expect((await bad).result).toBe("«Сдвиг по Z»: «вверх» — не число. Введите число, например 12,5.");
    const cancelled = app.registry.execute("raster.shift");
    await answer(false);
    expect(await cancelled).toEqual({ ok: true, result: "Отменено" });
    expect(core.calls).toHaveLength(1);
  });

  it("«На текущий разрез» needs the section line and asks in its own window", async () => {
    const app = await open();
    expect(await app.registry.execute("raster.place_on_section", { raster_id: 31 })).toEqual({ ok: false, result: "Сначала проведите линию разреза" });
    app.store.set({ hasSection: true });
    const declined = app.registry.execute("raster.place_on_section", { raster_id: 31 });
    expect(await answer(false)).toContain("Растр «Ортофото» встанет в плоскость линии разреза");
    expect(await declined).toEqual({ ok: true, result: "Отменено" });
    expect(core.calls).toEqual([]);
    const placed = app.registry.execute("raster.place_on_section", { raster_id: 31, z_top: 320.5 });
    expect(await answer(true)).toContain("верх — на отметке 320,5 м");
    expect((await placed).result).toBe("Растр «Ортофото» поставлен в плоскость линии разреза, верх — 320,5 м");
    expect(core.calls).toEqual([{ method: "POST", url: "/rasters/31/place-on-section", body: { expected_version: 2, z_top: 320.5 } }]);
    // Claude's call was confirmed by the registry: no second window; without z_top — by the heights of the data
    expect((await app.registry.execute("raster.place_on_section", { raster_id: 32 }, "claude")).ok).toBe(true);
    expect(core.calls[1].body).toEqual({ expected_version: 2, z_top: null });
    expect(dialogStore.get().queue).toHaveLength(0);
  });

  it("export: a window, or the job at once when the path is given", async () => {
    const app = await open();
    await app.run("object.select", { kind: "raster", id: 32 });
    expect((await app.registry.execute("rasters.export", { format: "geotiff" })).result).toBe("Открыто окно «Экспорт растра»");
    expect(windowStore.get().windows.map((w) => [w.kind, w.props])).toEqual([["raster-export", { rasterId: 32, format: "geotiff" }]]);
    const started = await app.registry.execute("rasters.export", { raster_id: 31, format: "source", path: "C:\\out\\ortho.jpg" });
    expect(started.result).toBe("Экспорт растра «Ортофото» (Исходный файл с привязкой) запущен: задание №7");
    expect(core.calls).toEqual([{ method: "POST", url: "/rasters/31/export", body: { path: "C:\\out\\ortho.jpg", format: "source" } }]);
    expect((await app.registry.execute("rasters.export", { raster_id: 32, format: "geotiff", path: "C:\\out\\a.tif" })).result).toContain("У растра нет привязки");
  });
});

describe("lines: `lines.export` (ТЗ 7.18)", () => {
  it("opens the window with the format and the layers; with a path starts the job", async () => {
    const app = await open();
    await app.run("object.select", { kind: "lines", id: 11 });
    expect((await app.registry.execute("lines.export", { format: "dxf" })).result).toBe("Открыто окно «Экспорт линий»");
    expect(windowStore.get().windows.map((w) => [w.kind, w.title, w.props])).toEqual([["lines-export", "Экспорт линий", { format: "dxf", layerIds: [11] }]]);
    await app.registry.execute("lines.export", { file_id: 1 });
    expect(windowStore.get().windows[1].props).toEqual({ format: "str", layerIds: [11, 12] });
    const started = await app.registry.execute("lines.export", { format: "str", file_id: 1, path: "C:\\out\\roads.str" });
    expect(started.result).toBe("Экспорт линий (Micromine STR) запущен: задание №7");
    expect(core.calls).toEqual([{ method: "POST", url: "/lines/export", body: { path: "C:\\out\\roads.str", format: "str", layer_ids: [11] } }]); // points are not written to STR
    expect((await app.registry.execute("lines.export", { format: "surpac", layer_id: 12, path: "C:\\out\\p.str" })).result).toContain("Surpac STR пишет только линии");
    expect((await app.registry.execute("lines.export", { layer_id: 99 })).result).toBe("Слой линий №99 не найден: обновите дерево проекта");
    expect((await app.registry.execute("lines.export", { format: "shp" })).result).toContain("допустимые значения — str, surpac, dxf, csv, dat");
  });
});

describe("DTM: `wireframe.dtm`, `dtm.settings`, `model.dtm`, attributes (ТЗ 7.20, Д-07)", () => {
  it("the settings go one by one with the version; a vertical raster is not put on a relief", async () => {
    const app = await open();
    const draped = await app.registry.execute("wireframe.dtm", { wireframe_id: 42, raster_id: 31, opacity: 0.8 });
    expect(draped).toEqual({ ok: true, result: "ЦТМ «Рельеф»: растр «Ортофото», цвет растра #ffffff, непрозрачность 0,8, тени рельефа включены, данные за ЦТМ видны" });
    expect(core.calls).toEqual([{ method: "PUT", url: "/wireframes/42/dtm", body: { expected_version: 4, raster_id: 31, opacity: 0.8 } }]);
    const vertical = await app.registry.execute("wireframe.dtm", { wireframe_id: 42, raster_id: 33 });
    expect(vertical.result).toContain("Растр «Скан» стоит в вертикальной плоскости: на рельеф кладётся только растр в плане");
    expect(core.calls).toHaveLength(1);
    await app.registry.execute("wireframe.dtm", { wireframe_id: 42, raster_id: 0, clip: true });
    expect(core.calls[1].body).toEqual({ expected_version: 5, raster_id: 0, clip: true }); // 0 takes the raster off
    expect(sceneObjects.get().wireframes.find((w) => w.id === 42)?.dtm).toMatchObject({ raster_id: null, clip: true, opacity: 0.8 });
    // settings without `on` move a wireframe to «ЦТМ»
    const moved = await app.registry.execute("wireframe.dtm", { wireframe_id: 41, shade: false });
    expect(moved.result).toContain("«Рудное тело» перенесён в раздел «ЦТМ». ЦТМ «Рудное тело»: без растра");
    expect((await app.registry.execute("wireframe.dtm", { wireframe_id: 41, tint: "red" })).result).toBe("Цвет растра задаётся как #rrggbb, например #ffffff — без изменений");
  });

  it("«Настройки ЦТМ…» selects the DTM and opens «Свойства»", async () => {
    const app = await open();
    app.store.set((s) => ({ workspace: { ...s.workspace, right: false } }));
    expect(await app.registry.execute("dtm.settings")).toEqual({ ok: true, result: "Настройки ЦТМ «Рельеф» — вверху панели «Свойства»" }); // the only DTM
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 42 });
    expect([app.store.get().workspace.right, app.store.get().workspace.rightTab]).toEqual([true, "properties"]);
    expect((await app.registry.execute("dtm.settings", { wireframe_id: 41 })).result).toBe("«Рудное тело» — каркас, а не ЦТМ: сначала перенесите его в раздел «ЦТМ» («В ЦТМ»)");
    sceneObjects.set({ wireframes: sceneObjects.get().wireframes.filter((w) => w.id !== 42) });
    expect((await app.registry.execute("dtm.settings")).result).toContain("В проекте нет ЦТМ");
  });

  it("«Построить ЦТМ» opens the window «Построение ЦТМ»", async () => {
    const app = await open();
    expect(await app.registry.execute("model.dtm")).toEqual({ ok: true, result: "Открыто окно «Построение ЦТМ»" });
    expect(windowStore.get().windows.map((w) => [w.kind, w.title])).toEqual([["dtm-build", "Построение ЦТМ"]]);
    expect((await app.registry.execute("model.dtm", {}, "claude")).ok).toBe(false); // a dialog is for a person
  });

  it("attributes of the user: add, change, rename, delete", async () => {
    const app = await open();
    const added = await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "Участок", value: "Северный" });
    expect(added).toEqual({ ok: true, result: "«Рудное тело»: Атрибут «Участок» = Северный" });
    await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "блок", value: "13,5" }); // found without case, a comma is a decimal sign
    await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "Блок", value: null });
    await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "Блок", rename: "Горизонт" });
    await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "Участок", remove: true });
    expect(core.calls.map((c) => c.body)).toEqual([
      { expected_version: 1, set: { Участок: "Северный" } },
      { expected_version: 2, set: { Блок: 13.5 } },
      { expected_version: 3, set: { Блок: null } }, // empty stays empty, not 0
      { expected_version: 4, rename: { Блок: "Горизонт" } },
      { expected_version: 5, remove: ["Участок"] },
    ]);
    expect((sceneObjects.get().wireframes.find((w) => w.id === 41) as WireframeInfo).attrs).toEqual([{ name: "Горизонт", value: null }]);
    expect((await app.registry.execute("wireframe.attr", { wireframe_id: 41, name: "Нет такого", remove: true })).result).toBe("Атрибута «Нет такого» у «Рудное тело» нет: обновите «Свойства»");
    expect((await app.registry.execute("wireframe.attr", { wireframe_id: 41, value: "x" })).result).toBe("Укажите название атрибута: name");
    await app.run("object.select", { kind: "wireframe", id: 41 });
    expect((await app.registry.execute("wireframe.attr")).result).toContain("Атрибуты «Рудное тело» — в панели «Свойства»");
  });
});

describe("`object.rename`, `object.color`, `object.delete` (ADR 0011 §5)", () => {
  it("rename: the selected object, the name asked in an own window; Claude gives the name", async () => {
    const app = await open();
    await app.run("object.select", { kind: "lines", id: 11 });
    const run = app.registry.execute("object.rename");
    expect(await answer("  Дороги  2026 ")).toBe("Переименовать: слой линий");
    expect(await run).toEqual({ ok: true, result: "Слой линий «Дороги» переименован в «Дороги 2026»" });
    expect(core.calls).toEqual([{ method: "PATCH", url: "/lines/layers/11", body: { expected_version: 3, name: "Дороги 2026" } }]);
    expect((await app.registry.execute("object.rename", { kind: "wireframe", id: 42, name: "Рельеф 2026" }, "claude")).result).toBe("ЦТМ «Рельеф» переименована в «Рельеф 2026»");
    expect((await app.registry.execute("object.rename", { kind: "raster", id: 31, name: "Ортофото" })).result).toBe("Название «Ортофото» не изменено");
    expect((await app.registry.execute("object.rename", { kind: "raster", id: 31 }, "claude")).result).toBe("Укажите новое название: name");
    expect((await app.registry.execute("object.rename", { kind: "raster", id: 31, name: "  " })).result).toBe("Название не может быть пустым: введите название");
    expect((await app.registry.execute("object.rename", { kind: "raster" })).result).toContain("Укажите и kind, и id объекта");
    app.store.set({ selected: null });
    expect((await app.registry.execute("object.rename")).result).toContain("Объект не выбран");
  });

  it("colour: #rrggbb or null — no colour of its own", async () => {
    const app = await open();
    expect((await app.registry.execute("object.color", { kind: "lines", id: 11, color: "#FF8800" })).result).toBe("Слой линий «Дороги»: цвет #ff8800");
    expect((await app.registry.execute("object.color", { kind: "wireframe", id: 41, color: null })).result).toBe("Каркас «Рудное тело»: без собственного цвета");
    expect(core.calls.map((c) => [c.url, c.body])).toEqual([["/lines/layers/11", { expected_version: 3, color: "#ff8800" }], ["/wireframes/41", { expected_version: 1, color: null }]]);
    expect((await app.registry.execute("object.color", { kind: "lines", id: 11, color: "красный" })).result).toContain("Цвет задаётся как #rrggbb");
    await app.run("object.select", { kind: "raster", id: 31 });
    expect((await app.registry.execute("object.color", { color: "#ffffff" })).result).toBe("Свой цвет есть у слоя линий, каркаса и ЦТМ: выберите один из них");
  });

  it("delete: an own «Удалить?» for a person; a layer of lines goes only with its file", async () => {
    const app = await open();
    const declined = app.registry.execute("object.delete", { kind: "wireframe", id: 41 });
    expect(await answer(false)).toBe("«Рудное тело» удаляется из проекта. Отменить удаление нельзя.");
    expect(await declined).toEqual({ ok: true, result: "Отменено" });
    expect(core.calls).toEqual([]);
    const done = app.registry.execute("object.delete", { kind: "wireframe", id: 42 });
    await answer(true);
    expect((await done).result).toBe("ЦТМ «Рельеф» удалена");
    expect(core.calls).toEqual([{ method: "DELETE", url: "/wireframes/42", body: undefined }]);
    // a raster a DTM wears stays: the reason of the core with its hint
    const inUse = await app.registry.execute("object.delete", { kind: "raster", id: 32 }, "claude");
    expect(inUse).toEqual({ ok: false, result: "Растр наложен на ЦТМ: «Рельеф». Снимите растр в настройках ЦТМ." });
    await app.run("object.select", { kind: "lines", id: 11 });
    expect((await app.registry.execute("object.delete")).result).toBe("Слой линий отдельно не удаляется. Удалите файл линий «roads.dxf» (2 слоя) — в его меню в дереве: «Удалить…».");
    const file = await app.registry.execute("object.delete", { kind: "line_file", id: 1 }, "claude");
    expect(file.result).toBe("Файл линий «roads.dxf» удалён со всеми слоями (2 слоя)");
    expect(core.calls.at(-1)).toEqual({ method: "DELETE", url: "/lines/files/1", body: undefined });
    expect(app.store.get().selected).toBeNull(); // the layer left with its file
  });
});
