/** OMF in «Импорт файлов» (ТЗ 7.24; ADR 0017 §1–§2; Э11 11.11): an `.omf` goes to the composition (nothing waits for
 * a later stage), the composition tells every element — what it becomes or why it will not load, with the hint of
 * the core — and the block models; an OMF 2 is refused in the card of its file with the code and the hint;
 * `omf.import` and `import.files {kind: "omf", folder}` open the wizard, the folder goes with the job. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeWindow, windowStore } from "../src/shell/windows";
import { folderNote, importPaths } from "../src/modules/importing/bind";
import { OmfPart } from "../src/modules/importing/OmfPart";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import fs from "node:fs";
import path from "node:path";
import { cleanFolder, fileCounts, ObjectsWizard, startBody, totalsText, type Composition, type FileOk } from "../src/modules/importing/objects";
import { blockEntryText, elementBecomes, elementData, elementNotes, elementSize, leftOutText, omfHeader, type OmfElement } from "../src/modules/importing/omf";
import { routeImport } from "../src/modules/importing/route";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import type { OpLog } from "../src/shell/opLog";

const element = (p: Partial<OmfElement>): OmfElement => ({
  key: "e", index: 0, class: "SurfaceElement", kind: "surface", name: "Рельеф", title: "Поверхность", as: "каркас", geometry: "SurfaceGeometry", color: null,
  description: "", points: 121, triangles: 200, counts: null, cells: null, data: [], textures: 0, dtm: false, skip: null, error: null, ...p,
});

const ELEMENTS: OmfElement[] = [
  element({ key: "s1", name: "trisurf", textures: 2, data: [{ key: "d1", name: "Высота", class: "ScalarData", location: "vertices", loads: false, reason: "данные на вершинах и гранях каркаса не переносятся", unit: null }] }),
  element({ key: "s2", name: "Рельеф 2026", dtm: true }),
  element({ key: "l1", class: "LineSetElement", kind: "lines", name: "Random Line", title: "Линии", as: "слой линий", points: 100, triangles: null, data: [{ key: "d2", name: "Тип", class: "StringData", location: "segments", loads: null, reason: null, unit: null }] }),
  element({ key: "v1", class: "VolumeElement", kind: "volume", name: "vol", title: "Объём", as: "блочная модель", points: null, triangles: null, counts: [10, 15, 20], cells: 3000, data: [{ key: "d3", name: "Au", class: "ScalarData", location: "cells", loads: true, reason: null, unit: "г/т" }] }),
  element({ key: "p1", class: "PointSetElement", kind: "points", name: "Random Points", title: "Точки", as: null, points: 50, triangles: null, skip: "точки не переносятся" }),
  element({ key: "b1", name: "broken", as: null, triangles: null, points: null, error: { code: "omf_broken_element", message: "Поверхность «broken» повреждена.", hint: "Сохраните файл заново." } }),
];

const omfFile = (p: Partial<FileOk> = {}): FileOk => ({
  path: "/d/model.omf", file: "model.omf", ok: true, format: "omf", label: "OMF v1", kind: "omf", encoding: null, crs: null, sidecars: [], layers: [], rasters: [],
  wireframes: [], totals: { layers: 1, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 2, triangles: 400, block_models: 1, cells: 3000 }, unread: 1,
  preliminary: false, choices: {}, warnings: [], size: 4096,
  left_out: [
    { key: "s1", element: "trisurf", kind: "surface", what: "текстуры", count: 2, reason: "текстуры не переносятся" },
    { key: "p1", element: "Random Points", kind: "points", what: "элемент", count: 1, reason: "точки не переносятся" },
  ],
  block_models: [{ name: "vol", counts: [10, 15, 20], cells: 3000, fields: ["Au"] }],
  omf: { format: "OMF", file: "model.omf", version: "1.0", project: "Карьер", description: "", units: "м", crs: null, elements: ELEMENTS },
  ...p,
});

describe("an .omf goes to the composition", () => {
  it("dropped or chosen: no stage to wait for", () => {
    expect(routeImport(["/d/model.omf"])).toEqual({ holes: [], objects: ["/d/model.omf"], project: [], skipped: [] });
    expect(routeImport(["/d/a.OMF", "/d/collar.csv"]).objects).toEqual(["/d/a.OMF"]);
  });
});

describe("the composition of an OMF file", () => {
  it("every element: what it becomes, its size, its data, what is not carried over", () => {
    expect(ELEMENTS.map(elementBecomes)).toEqual(["каркас", "ЦТМ", "слой линий", "блочная модель", "не загрузится", "не загрузится"]);
    expect(ELEMENTS.map(elementSize)).toEqual(["200 треугольников", "200 треугольников", "100 точек", "10 × 15 × 20, 3 000 ячеек", "50 точек", ""]);
    expect(elementNotes(ELEMENTS[0])).toEqual(["текстуры (2) не переносятся", "«Высота»: данные на вершинах и гранях каркаса не переносятся"]);
    expect(elementNotes(ELEMENTS[2])).toEqual(["«Тип»: перенесётся, если постоянно вдоль каждой линии"]);
    expect(elementNotes(ELEMENTS[4])).toEqual(["точки не переносятся"]);
    // the error of the core with its hint
    expect(elementNotes(ELEMENTS[5])).toEqual(["Поверхность «broken» повреждена. Сохраните файл заново."]);
    expect(elementData(ELEMENTS[3])).toBe("Au");
    expect(omfHeader(omfFile().omf!)).toBe("OMF 1.0 · проект «Карьер» · единицы: м");
  });

  it("the counts of the file and of the selection tell block models and cells; what is left out names its element", () => {
    const f = omfFile();
    expect(fileCounts(f)).toBe("слоёв: 1 · каркасов: 2 · треугольников: 400 · не загрузятся: 1 · блочных моделей: 1 · ячеек: 3 000");
    expect(totalsText({ files: 1, failed: 0, layers: 1, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 2, triangles: 400, block_models: 1, cells: 3000 })).toBe(
      "Файлов: 1 · слоёв: 1 · каркасов: 2 · треугольников: 400 · блочных моделей: 1 · ячеек: 3 000",
    );
    expect(f.left_out.map(leftOutText)).toEqual(["«trisurf»: текстуры — 2 (текстуры не переносятся)", "«Random Points»: элемент — 1 (точки не переносятся)"]);
    expect(leftOutText({ what: "колонка «I»", count: null, reason: "облако хранит X, Y, Z и класс" })).toBe("колонка «I» (облако хранит X, Y, Z и класс)");
    expect(blockEntryText(f.block_models![0])).toBe("vol — 10 × 15 × 20, 3 000 ячеек; поля: Au");
    expect(blockEntryText({ name: "пусто", counts: [1, 1, 1], cells: null, fields: [] })).toBe("пусто — 1 × 1 × 1; без полей");
  });
});

// --- the wizard and the commands against a small core ---------------------------------------------------------

const OMF2 = { path: "/d/new.omf", file: "new.omf", ok: false as const, size: 2048, error: { code: "omf2_not_supported", message: "Файл «new.omf» — OMF 2 (ZIP).", hint: "Сохраните его как OMF 0.9 (OMF v1) в программе, где он создан." } };

function core() {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    calls.push({ url: path, body });
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (path === "/import/objects/inspect") {
      const files = (body.files as string[]).map((p) => (p.endsWith("new.omf") ? OMF2 : omfFile({ path: p })));
      const c: Composition = { files, rest: [], totals: { files: 1, failed: files.length - 1, layers: 1, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 2, triangles: 400, block_models: 1, cells: 3000 }, raster_z: { applies: false, elevation: null, shift: 0 }, section: null };
      return json(c);
    }
    if (path === "/import/objects") return json({ source: "project", id: 9 });
    return json({ jobs: [], workers: null });
  });
  return { calls, fetch };
}

const log = (): OpLog => ({ id: "t", title: "Импорт файлов", kind: "import", started: new Date(), lines: [], failed: false }) as unknown as OpLog;

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  for (const w of windowStore.get().windows) closeWindow(w.id);
});

describe("the wizard with an OMF file", () => {
  it("an OMF 2 is refused in its card with the code and the hint; the folder goes with the job", async () => {
    const c = core();
    vi.stubGlobal("fetch", c.fetch);
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const w = new ObjectsWizard(log(), undefined, null, "Импорт/Карьер");
    await w.add(["/d/model.omf", "/d/new.omf"]);
    const files = w.store.get().composition!.files;
    expect(files[1]).toMatchObject({ ok: false, error: { code: "omf2_not_supported" } });
    expect(files[1].ok === false && files[1].error.hint).toContain("OMF 0.9");
    expect(startBody(w.store.get()).folder).toBe("Импорт/Карьер");
    expect(await w.start()).toEqual({ source: "project", id: 9 });
    expect(c.calls.find((x) => x.url === "/import/objects")?.body).toMatchObject({ files: ["/d/model.omf", "/d/new.omf"], folder: "Импорт/Карьер" });
    // without a folder nothing is sent: the folder of the file
    expect("folder" in startBody({ ...w.store.get(), folder: null })).toBe(false);
  });

  it("the folder of `import.files`: spaces squeezed, slashes at the ends dropped, 200 characters at most", () => {
    expect(cleanFolder("  Импорт /  Карьер/ ")).toBe("Импорт / Карьер");
    expect(cleanFolder("")).toBeNull();
    expect(cleanFolder(undefined)).toBeNull();
    expect(() => cleanFolder("x".repeat(201))).toThrow("200 символов");
  });
});

describe("omf.import and import.files {kind: omf, folder}", () => {
  const open = (picked: string[]) => {
    const pickFiles = vi.fn(async () => picked);
    vi.unstubAllGlobals();
    const app = createApp({ persist: false }); // before the stub: the app listens to the real window (if any)
    vi.stubGlobal("window", { bf: { pickFiles }, innerWidth: 1280, innerHeight: 800 });
    projectStore.set({ current: { uid: "p1" } as ProjectInfo });
    app.store.set({ projectName: "Проект" });
    return { app, pickFiles };
  };

  it("omf.import is bound: it offers OMF files and opens «Импорт файлов» with them", async () => {
    const { app, pickFiles } = open(["/d/model.omf"]);
    expect(app.registry.reason("omf.import")).toBeNull();
    const r = await app.registry.execute("omf.import", {});
    expect(r).toEqual({ ok: true, result: "Открыт состав импорта файлов" });
    expect(pickFiles).toHaveBeenCalledWith("omf", true);
    const win = windowStore.get().windows.find((w) => w.kind === "import-objects");
    expect(win?.props).toEqual({ paths: ["/d/model.omf"] });
  });

  it("import.files {kind: omf, folder} — the folder is a prop of the window; cancelled — nothing opens", async () => {
    const { app, pickFiles } = open(["/d/model.omf"]);
    expect((await app.registry.execute("import.files", { kind: "omf", folder: " Импорт/Карьер " })).ok).toBe(true);
    expect(pickFiles).toHaveBeenCalledWith("omf", true);
    expect(windowStore.get().windows.find((w) => w.kind === "import-objects")?.props).toEqual({ paths: ["/d/model.omf"], folder: "Импорт/Карьер" });
    const none = open([]);
    expect(await none.app.registry.execute("import.files", {})).toEqual({ ok: true, result: "Отменено" });
    expect(none.pickFiles).toHaveBeenCalledWith("objects", true);
  });

  it("files dropped onto a folder (the data manager): importPaths with the folder", async () => {
    const { app } = open([]);
    expect(await importPaths(app, ["/d/model.omf"], "auto", "Карьер")).toBe("Открыт состав импорта файлов");
    expect(windowStore.get().windows.find((w) => w.kind === "import-objects")?.props).toEqual({ paths: ["/d/model.omf"], folder: "Карьер" });
  });

  it("drillhole tables and a .bfggis dropped onto a folder: the user is told the folder does not apply to them (Э11 11.21)", async () => {
    expect(folderNote(null, 2, 1)).toBeNull();
    expect(folderNote("Карьер", 0, 0)).toBeNull();
    expect(folderNote("Карьер", 2, 0)).toBe("Папка «Карьер» не применится к таблицам скважин: они загрузятся без папки. Перенесите их потом в «Менеджере данных».");
    expect(folderNote("Карьер", 1, 1)).toContain("к таблицам скважин и файлу проекта .bfggis");
    const { app } = open([]);
    app.registry.bind("project.import", async () => "ok");
    const run = importPaths(app, ["/d/model.omf", "/d/collar.csv", "/d/old.bfggis"], "auto", "Карьер");
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("message");
    expect((d.options as { message: string }).message).toBe(folderNote("Карьер", 1, 1));
    closeDialog(d.id);
    (d.resolve as () => void)();
    expect(await run).toBe("Открыт состав импорта файлов, мастер импорта скважин, импорт проекта");
    expect(windowStore.get().windows.find((w) => w.kind === "import-objects")?.props).toEqual({ paths: ["/d/model.omf"], folder: "Карьер" });
    dialogStore.set({ queue: [] });
  });
});

describe("the block models of an OMF card", () => {
  it("two block models of one name: keys by their place, not the name (Э11 11.21)", () => {
    const text = fs.readFileSync(path.join(__dirname, "../src/modules/importing/OmfPart.tsx"), "utf8");
    expect(text).not.toMatch(/key=\{b\.name\}/);
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => void errors.push(a));
    const info = omfFile().omf!;
    const html = renderToStaticMarkup(createElement(OmfPart, { file: "model.omf", info, blocks: [{ name: "vol", counts: [10, 15, 20], cells: 3000, fields: [] }, { name: "vol", counts: [2, 2, 2], cells: 8, fields: [] }] }));
    spy.mockRestore();
    expect(html.match(/Блочная модель/g)).toHaveLength(2);
    expect(errors.filter((e) => String(e).includes("same key"))).toEqual([]);
  });
});
