/** Э8, подшаг 8.25 (ADR 0013 §5; ТЗ 7.21, 9.4, 9.5): the export of wireframes — the window «Экспорт каркасов»
 * as data, the command `wireframe.export` (the format, its options and the wireframes; a command never writes to
 * a path of its own), the tree and the ribbon that call it, the start of the job (409 `files_exist` is asked about,
 * `oda_not_found` offers «Внешние программы»), and no holes in the registry of Э8. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { sectionActions } from "../src/shell/treeSections";
import { windowStore } from "../src/shell/windows";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { overwriteQuestion, startWithOverwrite } from "../src/modules/rasters/export";
import { loadSceneObjects, sceneObjects } from "../src/modules/scene/objects";
import {
  defaultWireframes,
  DWG_VERSIONS,
  exportBody,
  exportGroups,
  exportName,
  exportOptions,
  exportReason,
  exportTotals,
  odaMissing,
  stlWarning,
  WIREFRAME_FORMAT_IDS,
  wireframeFormat,
} from "../src/modules/wireframes/export";
import { startWireframesExport } from "../src/modules/wireframes/exportCommands";
import type { WireframeInfo } from "../src/modules/wireframes/state";
import { folderMenu, wireframeRow } from "../src/modules/wireframes/tree";

const DTM = { raster_id: null, clip: false, tint: "#ffffff", opacity: 1, shade: true };
const wf = (id: number, p: Partial<WireframeInfo> = {}): WireframeInfo => ({
  id, name: `Тело ${id}`, version: 1, color: null, point_count: 100, triangle_count: 196, extent: [10, 20, 30, 110, 120, 130], dtm: null, folder: null, attrs: [], ...p,
});
const LIST = [
  wf(41, { name: "Рудное тело", folder: "model.tridb" }),
  wf(42, { name: "Жила", folder: "model.tridb" }),
  wf(43, { name: "Разлом", folder: "Тектоника/Разломы" }),
  wf(44, { name: "Рельеф", dtm: DTM, point_count: 16, triangle_count: 18, extent: [500_000, 6_000_000, 100, 501_000, 6_001_000, 300] }),
];

describe("«Экспорт каркасов» as data (ADR 0013 §2, §5)", () => {
  it("the six formats of the core, a DTM comes with its STR", () => {
    expect(WIREFRAME_FORMAT_IDS).toEqual(["tridb", "dxf", "dwg", "obj", "stl", "dtm"]);
    expect(wireframeFormat("dwg")).toMatchObject({ ext: ".dwg", filter: "dwg" });
    expect(wireframeFormat("dtm").note).toContain(".str");
    expect(wireframeFormat("shp").id).toBe("tridb"); // unknown → the default
    expect(DWG_VERSIONS).toEqual(["R2000", "R2004", "R2007", "R2010", "R2013", "R2018"]);
  });

  it("the wireframes checked when the window opens", () => {
    expect(defaultWireframes(LIST, { wireframeId: 43 })).toEqual([43]);
    expect(defaultWireframes(LIST, { wireframeId: 99 })).toEqual([]);
    expect(defaultWireframes(LIST, { folder: "model.tridb" })).toEqual([41, 42]); // every wireframe of an imported file
    expect(defaultWireframes(LIST, { folder: "Тектоника" })).toEqual([43]); // with the folders in it
    expect(defaultWireframes(LIST, { folder: "model.tridb", section: "dtm" })).toEqual([]);
    expect(defaultWireframes(LIST, { section: "dtm" })).toEqual([44]);
    expect(defaultWireframes(LIST, { section: "wireframes" })).toEqual([41, 42, 43]);
    expect(defaultWireframes(LIST, { selected: 42 })).toEqual([42]);
    expect(defaultWireframes(LIST, {})).toEqual([]);
    expect(defaultWireframes([LIST[0]], {})).toEqual([41]); // the only one
  });

  it("why the export cannot start, in Russian", () => {
    expect(exportReason([], [], null)).toBe("В проекте нет каркасов: загрузите каркасы или постройте ЦТМ.");
    expect(exportReason(LIST, [], null)).toBe("Отметьте каркасы, которые нужно записать.");
    expect(exportReason([wf(1, { triangle_count: 0 })], [1], "C:\\a.obj")).toBe("В отмеченных каркасах нет треугольников: записывать нечего.");
    expect(exportReason(LIST, [41], null)).toBe("Выберите файл: «Сохранить как…».");
    expect(exportReason(LIST, [41], "C:\\a.tridb")).toBeNull();
  });

  it("the name of the file: the only wireframe, the common folder, or «Каркасы»", () => {
    expect(exportName(LIST, [41], "obj")).toBe("Рудное тело.obj");
    expect(exportName(LIST, [41, 42], "tridb")).toBe("model.tridb"); // the folder of an imported file without its extension
    expect(exportName(LIST, [41, 43], "dxf")).toBe("Каркасы.dxf");
    expect(exportName([wf(1, { name: 'A/B:"C"' })], [1], "stl")).toBe("A_B__C_.stl");
    expect(exportTotals(LIST, [41, 42])).toBe("2 каркаса: 200 точек, 392 треугольника");
    expect(exportTotals(LIST, [])).toBe("Каркасы не отмечены");
  });

  it("the options go only with their format; the path of ODA is not sent (the core keeps it)", () => {
    const opts = { version: "R2018" as const, binary: false };
    expect(exportBody(LIST, [42, 41, 99], "dwg", "C:\\out\\м.dwg", opts)).toEqual({ path: "C:\\out\\м.dwg", format: "dwg", wireframe_ids: [41, 42], options: { version: "R2018" } });
    expect(exportBody(LIST, [41], "stl", "C:\\a.stl", opts).options).toEqual({ binary: false });
    expect(exportBody(LIST, [41], "dxf", "C:\\a.dxf", opts).options).toEqual({});
    expect(exportOptions({ version: "r2004", binary: false })).toEqual({ version: "R2004", binary: false });
    expect(exportOptions({ version: "R14" })).toEqual({ version: "R2013", binary: true });
  });

  it("a binary STL warns about float32; on UTM coordinates — about 0,5 m; a text STL does not", () => {
    expect(stlWarning(LIST, [41], true)).toContain("float32");
    expect(stlWarning(LIST, [44], true)).toContain("больше 100 000");
    expect(stlWarning(LIST, [44], false)).toBeNull();
  });

  it("the groups of the window: «Каркасы» and «ЦТМ» by the folders of the tree", () => {
    const groups = exportGroups(LIST).map((g) => [g.title, g.items.map((w) => w.id)]);
    expect(groups).toHaveLength(3);
    expect(groups).toEqual(expect.arrayContaining([["Каркасы › model.tridb", [41, 42]], ["Каркасы › Тектоника › Разломы", [43]]]));
    expect(groups.at(-1)).toEqual(["ЦТМ", [44]]); // «Каркасы» first, as in the tree
  });
});

describe("the start of `export.wireframes`: 409 `files_exist`, 422 `oda_not_found`", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("asks about the files a DTM would replace and starts again with `overwrite`", async () => {
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        bodies.push(body);
        if (!body.overwrite) return new Response(JSON.stringify({ code: "files_exist", message: "В папке уже есть файлы.", hint: "Подтвердите замену.", files: ["м.dtm", "м.str"] }), { status: 409 });
        return new Response(JSON.stringify({ source: "project", id: 5 }));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const body = exportBody(LIST, [41], "dtm", "C:\\out\\м.dtm");
    const asked: string[][] = [];
    const started = await startWithOverwrite((o) => startWireframesExport(body, o), async (files) => (asked.push(files), true));
    expect(started).toEqual({ source: "project", id: 5 });
    expect(asked).toEqual([["м.dtm", "м.str"]]);
    expect(overwriteQuestion(asked[0]).message).toBe("В папке уже есть файлы с такими именами. Экспорт заменит их:\n• м.dtm\n• м.str");
    expect(bodies.map((b) => b.overwrite)).toEqual([false, true]);
    expect(bodies[1]).toEqual({ path: "C:\\out\\м.dtm", format: "dtm", wireframe_ids: [41], options: {}, overwrite: true });
  });

  it("knows a DWG without ODA File Converter", () => {
    expect(odaMissing(new CoreError(422, "oda_not_found", "Запись DWG недоступна: не найден ODA File Converter.", "Укажите путь…"))).toBe(true);
    expect(odaMissing(new CoreError(422, "bad_version", "…", ""))).toBe(false);
    expect(odaMissing(null)).toBe(false);
  });
});

describe("`wireframe.export`: the window with the format and the wireframes", () => {
  let posts = 0;
  async function open(list: WireframeInfo[] = LIST) {
    const app = createApp({ persist: false });
    app.registry.hooks = { ...app.registry.hooks, confirm: async () => true };
    posts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = String(url).replace(/^.*\/api\/v1/, "");
        if ((init?.method ?? "GET") !== "GET") posts++;
        const json = (data: unknown) => new Response(JSON.stringify(data));
        if (path === "/wireframes") return json({ wireframes: list });
        if (path === "/lines/files") return json({ files: [] });
        if (path === "/rasters") return json({ rasters: [] });
        return json({ jobs: [], workers: null });
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    app.store.set({ projectName: "Проект" });
    await loadSceneObjects();
    return app;
  }
  beforeEach(() => windowStore.set({ windows: [] }));
  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  });
  const shown = () => windowStore.get().windows.map((w) => [w.kind, w.title, w.props]);

  it("is described for people and for the model, is not `confirm` (the file is chosen in the window)", () => {
    const c = createApp({ persist: false }).registry.get("wireframe.export")!;
    expect(c.stage).toBeUndefined();
    expect([c.confirm ?? false, c.uiOnly ?? false]).toEqual([false, false]);
    expect(c.required ?? []).toEqual([]); // without a format — the window
    expect(c.params!.format.enum).toEqual(["tridb", "dxf", "dwg", "obj", "stl", "dtm"]);
    expect(Object.keys(c.params!)).toEqual(["format", "wireframe_id", "folder", "section", "version", "binary"]);
    expect(c.description).toMatch(/TRIDB.*DXF.*DWG.*OBJ.*STL.*Surpac DTM/);
  });

  it("opens the window: the selected wireframe, a wireframe, a folder, a section; options preset", async () => {
    const app = await open();
    await app.run("object.select", { kind: "wireframe", id: 42 });
    expect((await app.registry.execute("wireframe.export")).result).toBe("Открыто окно «Экспорт каркасов» (Micromine TRIDB): выберите файл и нажмите «Экспортировать»");
    expect(shown()).toEqual([["wireframes-export", "Экспорт каркасов", { format: "tridb", wireframeIds: [42], options: { version: "R2013", binary: true } }]]);
    expect((await app.registry.execute("wireframe.export", { format: "dwg", version: "R2018", wireframe_id: 41 }, "claude")).result).toContain("(DWG R2018)");
    expect(windowStore.get().windows[1].props).toMatchObject({ format: "dwg", wireframeIds: [41], options: { version: "R2018" } });
    expect((await app.registry.execute("wireframe.export", { format: "stl", binary: false, folder: "model.tridb" })).result).toContain("(STL, текстовый)");
    expect(windowStore.get().windows[2].props).toMatchObject({ wireframeIds: [41, 42], options: { binary: false } });
    await app.registry.execute("wireframe.export", { section: "dtm", format: "dtm" });
    expect(windowStore.get().windows[3].props).toMatchObject({ format: "dtm", wireframeIds: [44] });
    expect(posts).toBe(0); // the job starts only from the window
  });

  it("says why in Russian; a command never writes to a path of its own", async () => {
    const app = await open();
    expect((await app.registry.execute("wireframe.export", { wireframe_id: 99 })).result).toBe("Каркас №99 не найден: обновите дерево проекта");
    expect((await app.registry.execute("wireframe.export", { folder: "Нет такой" })).result).toBe("В папке «Нет такой» нет каркасов: обновите дерево проекта");
    expect((await app.registry.execute("wireframe.export", { format: "ply" })).result).toContain("допустимые значения — tridb, dxf, dwg, obj, stl, dtm");
    expect((await app.registry.execute("wireframe.export", { format: "dwg", version: "R14" })).result).toContain("допустимые значения — R2000");
    expect((await app.registry.execute("wireframe.export", { path: "C:\\out\\a.obj" })).result).toBe("Неизвестный параметр «path»");
    expect(shown()).toEqual([]);
    const empty = await open([]);
    expect((await empty.registry.execute("wireframe.export")).result).toBe("В проекте нет каркасов: загрузите каркасы или постройте ЦТМ");
    expect(createApp({ persist: false }).registry.reason("wireframe.export")).toBe("Откройте или создайте проект");
  });
});

describe("the tree and the ribbon call `wireframe.export` (ТЗ 7.0.4, 7.1)", () => {
  const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));

  it("a row, a folder and a section of the tree", () => {
    for (const w of [LIST[0], LIST[3]]) {
      const menu = wireframeRow(w, true).menu;
      expect(menu.find((a) => a.label === "Экспорт…")).toEqual({ label: "Экспорт…", command: "wireframe.export", args: { wireframe_id: w.id } });
      expect(menu.find((a) => a.label === "Проверить")?.command).toBe("wireframe.check");
    }
    expect(folderMenu("model.tridb", "wireframes")).toEqual([{ label: "Экспорт каркасов папки…", command: "wireframe.export", args: { folder: "model.tridb", section: "wireframes" } }]);
    expect(folderMenu("Съёмка", "dtm")[0].label).toBe("Экспорт ЦТМ папки…");
    expect(sectionActions("wireframes").find((a) => a.command === "wireframe.export")?.args).toEqual({ section: "wireframes" });
    expect(sectionActions("dtm")).toEqual([{ label: "Экспорт ЦТМ…", command: "wireframe.export", args: { section: "dtm" } }]);
  });

  it("«Моделирование › Экспорт каркасов»: the window and a button per format", () => {
    const buttons = items.filter((i) => i.command === "wireframe.export");
    expect(new Set(buttons.map((b) => b.group))).toEqual(new Set(["Моделирование › Экспорт каркасов"]));
    expect(buttons.map((b) => b.args?.format ?? null)).toEqual([null, "dxf", "dwg", "obj", "stl", "dtm"]);
    expect(buttons.every((b) => b.hint && /[А-Яа-яЁё]/.test(b.hint))).toBe(true);
  });

  it("every command of wireframes of Э8 is in the ribbon (and so in Ctrl+K) and bound", () => {
    const app = createApp({ persist: false });
    const onRibbon = new Set(items.map((i) => i.command));
    for (const id of ["wireframes.upload", "wireframe.check", "model.self_intersections", "wireframe.export"]) {
      expect(onRibbon.has(id), id).toBe(true);
      expect(app.registry.get(id)!.run, id).toBeTypeOf("function");
      expect(app.registry.get(id)!.stage, id).toBeUndefined();
    }
  });
});

/** Commands of stages up to Э8 that are still «появится на этапе…»: a hole of the registry. The known one:
 * `dataset.move` — moving objects between the folders of the tree (left after Э3). */
const KNOWN_HOLES = ["dataset.move"];
const stageNo = (s: string): number => Number(/^Э(\d+)/.exec(s)?.[1] ?? Infinity);

describe("the registry has no holes (ТЗ 7.2: a command = a record of the registry)", () => {
  const app = createApp({ persist: false });
  const items = flattenRibbon(buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW));

  it("no command of Э1–Э8 waits for its stage, but the known ones", () => {
    const holes = app.registry.list().filter((c) => c.stage && stageNo(c.stage) <= 8).map((c) => c.id);
    expect(holes.filter((id) => !KNOWN_HOLES.includes(id))).toEqual([]);
  });

  it("every command without a stage has its handler; every button and menu item names a command", () => {
    const list = app.registry.list();
    expect(list.filter((c) => !c.stage && !c.run).map((c) => c.id)).toEqual([]);
    const ids = new Set(list.map((c) => c.id));
    expect(items.filter((i) => !ids.has(i.command)).map((i) => i.command)).toEqual([]);
    const menus = [...wireframeRow(LIST[0], true).menu, ...wireframeRow(LIST[3], true).menu, ...folderMenu("a", "wireframes"), ...sectionActions("wireframes"), ...sectionActions("dtm")];
    expect(menus.filter((a) => a.command && !ids.has(a.command)).map((a) => a.command)).toEqual([]);
  });

  it("ids are unique and every visible command is described in Russian", () => {
    const list = app.registry.list();
    expect(new Set(list.map((c) => c.id)).size).toBe(list.length);
    for (const c of list.filter((x) => !x.uiOnly)) expect(c.description, c.id).toMatch(/[А-Яа-яЁё]{3,}/);
  });
});
