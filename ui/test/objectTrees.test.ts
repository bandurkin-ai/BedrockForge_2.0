/** Sections «Линии», «Растры», «Каркасы» and «ЦТМ» of the data tree (ТЗ 7.18–7.20, 7.35 Д-07; ADR 0011 §5):
 * rows as data — check boxes, menus, the DTM without a gear — and the selection of `object.select`. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { isSelected, keepSelected } from "../src/shell/selection";
import { counted, plural } from "../src/shell/treeActions";
import { sectionActions, sectionPlus } from "../src/shell/treeSections";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";
import { AUTO_OPEN_LAYERS, fileCheck, fileOpen, fileToggle, FILE_TOGGLE_LIMIT, layerCounts, layerMenu, layerTitle, MAX_LAYER_ROWS, shownLayers } from "../src/modules/lines/tree";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { inUseBy, rasterMenu, rasterPlace } from "../src/modules/rasters/tree";
import { pickedObject, sceneObjects, type LineFileInfo, type LineLayerInfo, type RasterInfo } from "../src/modules/scene/objects";
import { dtms, loadWireframes, solids, wireframesStore, type WireframeInfo } from "../src/modules/wireframes/state";
import { wireframeRow } from "../src/modules/wireframes/tree";

const layer = (id: number, p: Partial<LineLayerInfo> = {}): LineLayerInfo => ({
  id, file_id: 1, name: `Слой ${id}`, version: 1, color: null, points_only: false, line_count: 12, point_count: 340, length: 1520.46,
  closed_count: 0, closed: "none", flat: false, extent: null, attr_schema: [], ...p,
});
const lineFile = (id: number, layers: LineLayerInfo[], p: Partial<LineFileInfo> = {}): LineFileInfo => ({
  id, name: `file${id}.str`, folder: null, version: 1, format: "micromine_str", label: null, filename: null, crs_name: null, warnings: [], layers, ...p,
});
const raster = (id: number, p: Partial<RasterInfo> = {}): RasterInfo => ({
  id, name: `Растр ${id}`, version: 1, width: 4000, height: 3000, georef: null, elevation: null, vertical: false, opacity: 1, has_elevation: false,
  displays: [], images: {}, warnings: [], ...p,
});
const DTM = { raster_id: null, clip: false, tint: "#ffffff", opacity: 1, shade: true };
const wireframe = (id: number, p: Partial<WireframeInfo> = {}): WireframeInfo => ({
  id, name: `Каркас ${id}`, folder: null, version: 1, color: "#e8873a", point_count: 100, triangle_count: 196, extent: null, dtm: null, ...p,
});
const labels = (menu: { label: string; separator?: boolean }[]) => menu.filter((a) => !a.separator).map((a) => a.label);

describe("«Линии»: files → layers", () => {
  it("counts in Russian", () => {
    expect([1, 2, 5, 11, 21, 22, 25, 101, 111, 112].map((n) => plural(n, "слой", "слоя", "слоёв"))).toEqual(["слой", "слоя", "слоёв", "слоёв", "слой", "слоя", "слоёв", "слой", "слоёв", "слоёв"]);
    expect(counted(1234, "линия", "линии", "линий")).toBe("1\u00a0234 линии");
    expect(layerCounts(layer(1))).toBe("12 линий, 340 вершин");
    expect(layerCounts(layer(1, { points_only: true, line_count: 0, point_count: 21 }))).toBe("21 точка");
    expect(layerTitle(layer(1, { closed: "some", closed_count: 3, flat: true }))).toBe("Слой 1: 12 линий, 340 вершин, длина 1\u00a0520,5 м, замкнутых 3, без высот. Щелчок — выбор, галочка — показать в 3D");
  });

  it("the check box of a file is on while every layer is shown and switches only what differs", () => {
    const f = lineFile(1, [layer(11), layer(12), layer(13)]);
    expect(fileCheck(f, [])).toEqual({ checked: true });
    expect(fileCheck(f, ["ln:12", "set:1"])).toEqual({ checked: false });
    expect(fileToggle(f, ["ln:12"], true)).toEqual([12]); // show the file: only the hidden layer
    expect(fileToggle(f, ["ln:12"], false)).toEqual([11, 13]); // hide the file: only the shown ones
    expect(fileToggle(f, [], true)).toEqual([]);
    expect(fileCheck(lineFile(2, []), [])).toBeNull();
  });

  it("a file of thousands of layers: no check box of its own, closed at first, rows are capped", () => {
    const many = lineFile(3, Array.from({ length: 2000 }, (_, i) => layer(100 + i)));
    expect(fileCheck(many, [])).toBeNull();
    expect(fileCheck(lineFile(4, many.layers.slice(0, FILE_TOGGLE_LIMIT)), [])).toEqual({ checked: true });
    expect(fileOpen(many, [])).toBe(false);
    expect(fileOpen(many, ["3"])).toBe(true); // its arrow was used
    const small = lineFile(5, many.layers.slice(0, AUTO_OPEN_LAYERS));
    expect([fileOpen(small, []), fileOpen(small, ["5"])]).toEqual([true, false]);
    expect(shownLayers(many)).toMatchObject({ rest: 2000 - MAX_LAYER_ROWS });
    expect(shownLayers(many).layers).toHaveLength(MAX_LAYER_ROWS);
    expect(shownLayers(small).rest).toBe(0);
  });

  it("visibility goes through `layer.show {layer: \"lines\", id}`", () => {
    expect(layerMenu(layer(7), true)[1]).toEqual({ label: "Скрыть в 3D", command: "layer.show", args: { layer: "lines", id: 7, visible: false } });
    expect(layerMenu(layer(7), false)[1]).toEqual({ label: "Показать в 3D", command: "layer.show", args: { layer: "lines", id: 7, visible: true } });
  });
});

describe("«Растры»", () => {
  it("tells where a raster stands; an empty height is not 0", () => {
    expect(rasterPlace(raster(1))).toBe("в плане, под самым низким объектом");
    expect(rasterPlace(raster(1, { elevation: 0 }))).toBe("в плане, высота 0 м");
    expect(rasterPlace(raster(1, { elevation: 250.5 }))).toBe("в плане, высота 250,5 м");
    expect(rasterPlace(raster(1, { vertical: true }))).toBe("вертикальный");
  });

  it("visibility goes through `layer.show {layer: \"raster\", id}`; a refused delete names the DTMs", () => {
    expect(rasterMenu(raster(9), true)[1]).toEqual({ label: "Скрыть в 3D", command: "layer.show", args: { layer: "raster", id: 9, visible: false } });
    expect(labels(rasterMenu(raster(9), true))).toEqual(["Свойства", "Скрыть в 3D", "Переименовать…", "Удалить…"]);
    expect(inUseBy({ code: "raster_in_use", wireframes: [{ id: 3, name: "Рельеф 2026" }, { id: 4, name: "Рельеф карьера" }] })).toEqual(["Рельеф 2026", "Рельеф карьера"]);
    expect(inUseBy({ code: "raster_not_found" })).toBeNull();
    expect(inUseBy(undefined)).toBeNull();
  });
});

describe("«Каркасы» and «ЦТМ» (Д-07)", () => {
  const list = [wireframe(1), wireframe(2, { name: "Рельеф", dtm: DTM }), wireframe(3, { dtm: { ...DTM, raster_id: 9 } })];

  it("a DTM is a wireframe with `dtm` set: each section gets its own", () => {
    expect(solids(list).map((w) => w.id)).toEqual([1]);
    expect(dtms(list).map((w) => w.id)).toEqual([2, 3]);
  });

  it("a wireframe: the sign and the menu item «Перенести в ЦТМ»; a click selects", () => {
    const row = wireframeRow(list[0], true);
    expect(row.click).toBe("select");
    expect(row.buttons).toEqual([{ label: "Перенести в ЦТМ", icon: "dtm", command: "wireframe.dtm", args: { wireframe_id: 1, on: true } }]);
    expect(labels(row.menu)).toEqual(["Свойства", "Скрыть в 3D", "Перенести в ЦТМ", "Переименовать…", "Удалить…"]);
    expect(row.menu[1].args).toEqual({ layer: "wireframe", id: 1, visible: false });
    expect(row.testId).toBe("tree-wireframe-1");
  });

  it("a DTM has no gear — no button at all: the click on the row opens its settings in «Свойства»", () => {
    const row = wireframeRow(list[1], true);
    expect(row.buttons).toEqual([]);
    expect(row.click).toBe("properties");
    expect(row.title).toContain("Щелчок — настройки ЦТМ в «Свойствах»");
    expect(labels(row.menu)).toEqual(["Настройки ЦТМ…", "Скрыть в 3D", "Вернуть в каркасы", "Переименовать…", "Удалить…"]);
    expect(row.menu[0].local).toBe("properties"); // the same as the click
    const back = row.menu[2];
    expect([back.command, back.args]).toEqual(["wireframe.dtm", { wireframe_id: 2, on: false }]);
    expect(back.confirm?.message).toContain("Настройки рельефа сбросятся");
    expect(row.menu.some((a) => /шестер|gear/i.test(`${a.label} ${a.icon ?? ""}`))).toBe(false);
    expect(row.testId).toBe("tree-dtm-2");
  });
});

describe("the header of a section: «+» and the menu", () => {
  it("«Скважины»: «+» opens the ways to add — with «Импорт канав…» (Д-16)", () => {
    expect(sectionPlus("holes")?.command).toBeUndefined();
    expect(sectionActions("holes")).toEqual([
      { label: "Новый набор…", command: "set.create" },
      { label: "Импорт скважин…", command: "import.open" },
      { label: "Импорт канав…", command: "import.open", args: { mode: "trenches" } },
    ]);
  });

  it("«Линии» and «Растры»: «+» runs the upload; every command of a header is in the registry", () => {
    expect(sectionPlus("lines")?.command).toBe("lines.upload");
    expect(sectionPlus("rasters")?.command).toBe("rasters.upload");
    const app = createApp({ persist: false });
    for (const s of ["views", "holes", "wireframes", "dtm", "lines", "rasters", "blocks", "audit"] as const) {
      for (const a of sectionActions(s)) expect(app.registry.get(a.command), a.command).toBeDefined();
      const plus = sectionPlus(s);
      if (plus?.command) expect(app.registry.get(plus.command), plus.command).toBeDefined();
    }
  });
});

describe("`object.select`, «Снять выбор» (Esc) and `wireframe.dtm`", () => {
  const files = [lineFile(1, [layer(11, { name: "Дороги" }), layer(12)])];
  const rasters = [raster(31, { name: "Ортофото" })];
  const wfs = [wireframe(41, { name: "Рудное тело" }), wireframe(42, { name: "Рельеф", dtm: DTM })];

  function open() {
    const app = createApp({ persist: false });
    app.store.set({ projectName: "Проект" });
    sceneObjects.set({ files, rasters, loaded: true, error: null });
    wireframesStore.set({ wireframes: wfs, loaded: true, error: null });
    return app;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    sceneObjects.set({ files: [], rasters: [], loaded: false, error: null });
    wireframesStore.set({ wireframes: [], loaded: false, error: null });
    pickedObject.set({ picked: null });
  });

  it("selects a layer, a raster, a wireframe and a DTM by kind and id; tells what is not there", async () => {
    const app = open();
    expect(await app.registry.execute("object.select", { kind: "lines", id: 11 })).toEqual({ ok: true, result: "Выбран слой линий «Дороги»" });
    expect(app.store.get().selected).toEqual({ kind: "lines", id: 11 });
    expect(isSelected(app.store.get().selected, "lines", 11)).toBe(true);
    expect(isSelected(app.store.get().selected, "raster", 11)).toBe(false);
    expect((await app.registry.execute("object.select", { kind: "raster", id: 31 })).result).toBe("Выбран растр «Ортофото»");
    expect((await app.registry.execute("object.select", { kind: "wireframe", id: 41 })).result).toBe("Выбран каркас «Рудное тело»");
    expect((await app.registry.execute("object.select", { kind: "wireframe", id: 42 })).result).toBe("Выбрана ЦТМ «Рельеф»");
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 42 });
    const missing = await app.registry.execute("object.select", { kind: "lines", id: 99 });
    expect(missing).toEqual({ ok: false, result: "Слой линий №99 не найден: обновите дерево проекта" });
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 42 }); // a wrong id changes nothing
    expect((await app.registry.execute("object.select", { kind: "blocks", id: 1 })).ok).toBe(false);
  });

  it("Esc is «Снять выбор»: the object and the pick of the 3D window are cleared", async () => {
    const esc = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW)).find((i) => i.shortcut === "escape")!;
    expect([esc.command, esc.label]).toEqual(["selection.clear", "Снять выбор"]);
    expect(esc.unavailable?.({ hasProject: true, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true })).toBe("Ничего не выбрано");
    expect(esc.unavailable?.({ hasProject: true, hasSet: false, hasHole: false, hasObject: true, hasSection: false, editing: false, coreOnline: true })).toBeNull();
    const app = open();
    await app.run("object.select", { kind: "lines", id: 11 });
    pickedObject.set({ picked: { kind: "lines", id: 11, line: 0, on: "line", xyz: [1, 2, 3] } });
    app.store.set({ holeId: "DH-1" });
    expect(await app.registry.execute(esc.command, esc.args ?? {})).toEqual({ ok: true, result: "Выбор снят" });
    expect(app.store.get().selected).toBeNull();
    expect(pickedObject.get().picked).toBeNull();
    expect(app.store.get().holeId).toBeNull(); // the hole too, as before
    expect((await app.registry.execute("selection.clear")).result).toBe("Ничего не выбрано");
  });

  it("a click on nothing in the 3D window clears the object picked there, not another one", async () => {
    const app = open();
    pickedObject.set({ picked: { kind: "raster", id: 31, xyz: [1, 2, 3] } });
    await app.run("object.select", { kind: "raster", id: 31 });
    pickedObject.set({ picked: null });
    expect(app.store.get().selected).toBeNull();
    pickedObject.set({ picked: { kind: "raster", id: 31, xyz: [1, 2, 3] } });
    await app.run("object.select", { kind: "wireframe", id: 41 }); // chosen in the tree meanwhile
    pickedObject.set({ picked: null });
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 41 });
  });

  it("an object that left the project is not selected any more", async () => {
    const app = open();
    await app.run("object.select", { kind: "lines", id: 12 });
    sceneObjects.set({ files: [lineFile(1, [layer(11)])] });
    expect(app.store.get().selected).toBeNull();
    await app.run("object.select", { kind: "wireframe", id: 41 });
    sceneObjects.set({ files: [] }); // other lists do not touch it
    expect(app.store.get().selected).toEqual({ kind: "wireframe", id: 41 });
    wireframesStore.set({ wireframes: [wfs[1]] });
    expect(app.store.get().selected).toBeNull();
    expect(keepSelected({ kind: "raster", id: 31 }, () => true)).toEqual({ kind: "raster", id: 31 });
    expect(keepSelected(null, () => true)).toBeNull();
  });

  it("`wireframe.dtm`: into «ЦТМ» and back with the version of the wireframe; without an id — the selected one", async () => {
    const app = open();
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    wireframesStore.set({ wireframes: wfs, loaded: true, error: null }); // the project change cleared the list
    const calls: { method: string; url: string; body: unknown }[] = [];
    let server = wfs;
    vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ method: init?.method ?? "GET", url: String(url).replace(/^.*\/api\/v1/, ""), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (init?.method === "PUT") {
        const on = (JSON.parse(String(init.body)) as { on: boolean }).on;
        server = server.map((w) => (String(url).endsWith(`/wireframes/${w.id}/dtm`) ? { ...w, version: w.version + 1, dtm: on ? DTM : null } : w));
      }
      return new Response(JSON.stringify(init?.method === "PUT" ? server[0] : { wireframes: server }), { status: 200 });
    }));
    await loadWireframes();
    calls.length = 0;
    const moved = await app.registry.execute("wireframe.dtm", { wireframe_id: 41, on: true });
    expect(moved).toEqual({ ok: true, result: "«Рудное тело» перенесён в раздел «ЦТМ»" });
    expect(calls[0]).toEqual({ method: "PUT", url: "/wireframes/41/dtm", body: { expected_version: 1, on: true } });
    expect(dtms(wireframesStore.get().wireframes).map((w) => w.id)).toEqual([41, 42]); // the list was read again
    expect((await app.registry.execute("wireframe.dtm", { wireframe_id: 41 })).result).toBe("«Рудное тело» уже в разделе «ЦТМ»");
    await app.run("object.select", { kind: "wireframe", id: 42 });
    const back = await app.registry.execute("wireframe.dtm", { on: false });
    expect(back.result).toBe("«Рельеф» возвращён в раздел «Каркасы»: настройки рельефа сброшены");
    expect(solids(wireframesStore.get().wireframes).map((w) => w.id)).toEqual([42]);
    app.store.set({ selected: null });
    expect(await app.registry.execute("wireframe.dtm", { on: true })).toEqual({ ok: false, result: "Выберите каркас в дереве или укажите wireframe_id" });
    expect((await app.registry.execute("wireframe.dtm", { wireframe_id: 77 })).result).toBe("Каркас №77 не найден: обновите дерево проекта");
  });
});
