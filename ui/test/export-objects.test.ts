// «Экспорт проекта › Объекты в файлы» (Д-17; ADR 0010 §1, ADR 0017 §5): the table «тип → формат» of
// `GET /export/formats`, the choice, the body of `POST /export/objects`, the arguments of `project.export`,
// the report of the job `export.objects`. The core is a fetch stub (its API is made by another track).
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jobsStore } from "../src/modules/jobs/state";
import {
  choiceReason,
  chosenObjects,
  confirmQuestion,
  exportBody,
  exportFolderName,
  fitFormat,
  initialChoice,
  parseFormats,
  parseFormatsArg,
  parseTypesArg,
  relativePath,
  reportOf,
  reportSummary,
  setFormat,
  sizeText,
  toggleType,
  typeReason,
} from "../src/modules/projects/exportObjects";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";

const FORMATS = {
  types: [
    { kind: "line_file", count: 3, default: "str", formats: [{ id: "str", title: "Micromine STR", ext: "str" }, { id: "dxf", title: "DXF", ext: ".dxf" }, { id: "omf", title: "OMF", ext: ".omf", available: false, reason: "Писатель OMF — подшаг 11.4" }] },
    { kind: "drillhole_set", count: 1, default: "csv", formats: [{ id: "csv", title: "CSV", ext: ".csv" }, { id: "xlsx", title: "XLSX", ext: ".xlsx" }, { id: "dat", title: "DAT", ext: ".dat" }] },
    { kind: "wireframe", count: 2, default: "dxf", formats: [{ id: "dxf", title: "DXF", ext: ".dxf" }, { id: "dwg", title: "DWG", ext: ".dwg", reason: "Не найден ODA File Converter" }, { format: "tridb", title: "Micromine TRIDB", ext: ".tridb" }] },
    { kind: "dtm", count: 0, default: "dxf", formats: [{ id: "dxf", title: "DXF", ext: ".dxf" }] },
    { kind: "block_model", count: 1, default: "omf", formats: [{ id: "omf", title: "OMF", why: "Писатель OMF — подшаг 11.4" }, { id: "dat", title: "DAT", reason: "Экспорт блочных моделей в DAT — этап Э17" }] },
    { kind: "raster", count: 1, default_format: "geotiff", formats: [{ id: "source", title: "Исходный файл" }, { id: "geotiff", title: "GeoTIFF", ext: ".tif" }] },
    { kind: "raster", count: 9, formats: [] }, // a repeated type is ignored
    { count: 4 }, // no kind
  ],
};
const info = parseFormats(FORMATS);

describe("the table «тип → формат» of the core", () => {
  it("types in the order of ADR 0017 §5 with Russian titles; formats with their reasons", () => {
    expect(info.types.map((t) => [t.kind, t.title, t.count])).toEqual([
      ["drillhole_set", "Наборы скважин", 1],
      ["wireframe", "Каркасы", 2],
      ["dtm", "ЦТМ", 0],
      ["line_file", "Линии", 3],
      ["raster", "Растры", 1],
      ["block_model", "Блочные модели", 1],
    ]);
    const lines = info.types.find((t) => t.kind === "line_file")!;
    expect(lines.formats.map((f) => [f.id, f.ext, f.reason])).toEqual([["str", ".str", null], ["dxf", ".dxf", null], ["omf", ".omf", "Писатель OMF — подшаг 11.4"]]);
    expect(info.types.find((t) => t.kind === "wireframe")!.formats[2].id).toBe("tridb"); // `format` instead of `id`
    expect(info.types.find((t) => t.kind === "raster")!.default).toBe("geotiff"); // `default_format`
    expect(info.reason).toBeNull();
    expect(parseFormats({ read_only: true, types: [] }).reason).toBe("Проект открыт только для чтения: экспорт недоступен.");
    expect(parseFormats(null)).toEqual({ types: [], reason: null });
    expect(parseFormats({ types: [{ kind: "x", formats: [{ id: "a" }], available: false }] }).types[0].reason).toBe("Сейчас недоступно");
  });

  it("a type without objects or without a format the core writes is off with its reason", () => {
    const by = (k: string) => info.types.find((t) => t.kind === k)!;
    expect(typeReason(by("dtm"))).toBe("В проекте нет объектов этого типа");
    expect(typeReason(by("block_model"))).toBe("Писатель OMF — подшаг 11.4");
    expect(typeReason(by("wireframe"))).toBeNull();
    expect(fitFormat(by("wireframe"), "dwg")).toBe("dxf"); // DWG without ODA: the default
    expect(fitFormat(by("wireframe"), "tridb")).toBe("tridb");
    expect(fitFormat(by("block_model"), "omf")).toBeNull();
  });

  it("every exportable type is checked in its default format; the given choice is kept where it can be", () => {
    const c = initialChoice(info);
    expect(c.types).toEqual(["drillhole_set", "wireframe", "line_file", "raster"]);
    expect(c.formats).toEqual({ drillhole_set: "csv", wireframe: "dxf", dtm: "dxf", line_file: "str", raster: "geotiff" });
    const given = initialChoice(info, { formats: { line_file: "dxf", wireframe: "dwg" }, types: ["line_file", "dtm"] });
    expect(given.types).toEqual(["line_file"]); // dtm has no objects
    expect(given.formats.line_file).toBe("dxf");
    expect(given.formats.wireframe).toBe("dxf");
    expect(chosenObjects(info, c)).toBe(7);
  });

  it("tells before the request why «Экспортировать» is off", () => {
    const c = initialChoice(info);
    expect(choiceReason(info, c, null)).toBe("Выберите папку: «Выбрать папку…».");
    expect(choiceReason(info, c, "D:\\Выгрузка")).toBeNull();
    expect(choiceReason(info, { ...c, types: [] }, "D:\\")).toBe("Отметьте хотя бы один тип объектов.");
    expect(choiceReason(info, toggleType(c, "block_model", true), "D:\\")).toBe("Блочные модели: Писатель OMF — подшаг 11.4.");
    expect(choiceReason(info, setFormat(c, "line_file", "omf"), "D:\\")).toBe("Линии, OMF: Писатель OMF — подшаг 11.4.");
    expect(choiceReason(parseFormats({ ...FORMATS, read_only: true }), c, "D:\\")).toContain("только для чтения");
    expect(toggleType(toggleType(c, "wireframe", false), "wireframe", true).types).toContain("wireframe");
  });

  it("the body of `POST /export/objects`: formats of the checked types only, the folder, the types, ids", () => {
    const c = toggleType(setFormat(initialChoice(info), "drillhole_set", "xlsx"), "raster", false);
    expect(exportBody(info, c, "D:\\Выгрузка")).toEqual({ formats: { drillhole_set: "xlsx", wireframe: "dxf", line_file: "str" }, folder: "D:\\Выгрузка", types: ["drillhole_set", "wireframe", "line_file"] });
    expect(exportBody(info, c, "D:\\", [5, 7]).ids).toEqual([5, 7]);
    expect(exportBody(info, c, "D:\\", []).ids).toBeUndefined();
  });

  it("the folder the export makes", () => {
    expect(exportFolderName("Северный", new Date(2026, 10, 9))).toBe("Северный — экспорт 2026-11-09");
  });
});

describe("the arguments of `project.export`", () => {
  it("formats: «тип=формат» by ids or titles, JSON; unknown and unavailable ones are refused with a hint", () => {
    expect(parseFormatsArg(info, "wireframe=tridb, Линии: dxf; drillhole_set=XLSX")).toEqual({ wireframe: "tridb", line_file: "dxf", drillhole_set: "xlsx" });
    expect(parseFormatsArg(info, '{"raster": "source"}')).toEqual({ raster: "source" });
    expect(parseFormatsArg(info, "")).toEqual({});
    expect(parseFormatsArg(info, undefined)).toEqual({});
    expect(() => parseFormatsArg(info, "chart=png")).toThrow("Тип объектов «chart» не экспортируется.");
    expect(() => parseFormatsArg(info, "wireframe=3ds")).toThrow("Каркасы: формата «3ds» нет.");
    expect(() => parseFormatsArg(info, "wireframe=dwg")).toThrow("Каркасы, DWG: Не найден ODA File Converter.");
    expect(() => parseFormatsArg(info, "{oops")).toThrow("Параметр formats не разобран.");
    expect(() => parseFormatsArg(info, "wireframe")).toThrow("Не разобрано «wireframe» в formats.");
    try {
      parseFormatsArg(info, "wireframe=dwg");
    } catch (e) {
      expect((e as { hint: string }).hint).toBe("Доступные форматы: dxf, tridb.");
    }
  });

  it("types: ids or titles; a type without objects is refused", () => {
    expect(parseTypesArg(info, "wireframe, Линии, wireframe")).toEqual(["wireframe", "line_file"]);
    expect(parseTypesArg(info, "")).toBeUndefined();
    expect(() => parseTypesArg(info, "dtm")).toThrow("ЦТМ: В проекте нет объектов этого типа.");
    expect(() => parseTypesArg(info, "views")).toThrow("не экспортируется");
  });

  it("our own question lists the types, the formats and the folder", () => {
    const body = exportBody(info, initialChoice(info, { types: ["wireframe", "line_file"] }), "D:\\Выгрузка");
    const q = confirmQuestion(info, body, "Северный");
    expect(q.title).toBe("Экспорт объектов в файлы");
    expect(q.message).toContain("«D:\\Выгрузка»");
    expect(q.message).toContain("• Каркасы (2) → DXF\n• Линии (3) → Micromine STR");
    expect(q.okLabel).toBe("Экспортировать");
  });
});

describe("the report of `export.objects`", () => {
  const result = {
    folder: "D:\\Выгрузка\\Северный — экспорт 2026-11-19",
    files: [
      { path: "D:\\Выгрузка\\Северный — экспорт 2026-11-19\\Каркасы\\Рудное тело.dxf", size: 1_572_864, object: "Рудное тело", format: "dxf" },
      { path: "D:\\Выгрузка\\Северный — экспорт 2026-11-19\\Линии\\Контуры.str", size: 2048, object: "Контуры", format: "str" },
      "D:\\elsewhere\\x.csv",
      { size: 5 },
      7,
    ],
    skipped: [{ object: "Слой точек", reason: "Слои точек в DXF не пишутся" }, { name: "Модель" }, null],
  };

  it("files, sizes and what was skipped; junk is dropped", () => {
    const r = reportOf(result);
    expect(r.folder).toBe(result.folder);
    expect(r.files.map((f) => [f.object, f.size])).toEqual([["Рудное тело", 1_572_864], ["Контуры", 2048], ["", null]]);
    expect(r.skipped).toEqual([{ object: "Слой точек", reason: "Слои точек в DXF не пишутся" }, { object: "Модель", reason: "причина не указана" }]);
    expect(r.bytes).toBe(1_574_912);
    expect(reportSummary(r)).toBe("Записано 3 файла (1,5 МБ); пропущено 2 объекта");
    expect(reportSummary(reportOf(null))).toBe("Файлы не записаны");
    expect(reportOf({ path: "D:\\a" }).folder).toBe("D:\\a"); // the field the «Задания» tab reads
  });

  it("sizes in Russian and paths inside the folder", () => {
    expect([sizeText(12), sizeText(2048), sizeText(839_680), sizeText(3.4 * 1024 ** 2), sizeText(1.25 * 1024 ** 3)]).toEqual(["12 Б", "2,0 КБ", "820 КБ", "3,4 МБ", "1,3 ГБ"]);
    expect(relativePath("D:\\a\\b\\Каркасы\\x.dxf", "D:\\a\\b")).toBe("Каркасы\\x.dxf");
    expect(relativePath("D:\\a\\bc\\x.dxf", "D:\\a\\b")).toBe("D:\\a\\bc\\x.dxf");
    expect(relativePath("/tmp/out/x.str", "/tmp/out/")).toBe("x.str");
    expect(relativePath("x", null)).toBe("x");
  });
});

describe("the command `project.export`", () => {
  const sent: { method: string; path: string; body: unknown }[] = [];
  let formats: unknown = FORMATS;
  let refuse: Response | null = null;
  const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });

  beforeEach(() => {
    sent.length = 0;
    formats = FORMATS;
    refuse = null;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const p = u.pathname.replace(/^\/api\/v1/, "");
      const method = init?.method ?? "GET";
      if (p.startsWith("/export/")) sent.push({ method, path: p, body: init?.body ? JSON.parse(String(init.body)) : undefined }); // other modules read the opened project too
      if (p === "/export/formats") return json(formats);
      if (p === "/export/objects") return refuse ?? json({ source: "project", id: 41 });
      if (p === "/jobs") return json({ jobs: [], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    });
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    projectStore.set({ current: { uid: "p1", name: "Северный" } as ProjectInfo });
    windowStore.set({ windows: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    projectStore.set({ current: null });
    dialogStore.set({ queue: [] });
    jobsStore.set({ jobs: [] });
  });

  function open() {
    const app = createApp({ persist: false });
    app.registry.hooks = { ...app.registry.hooks, confirm: async () => true };
    app.store.set({ projectName: "Северный" });
    return app;
  }

  async function answer(ok: boolean): Promise<string> {
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    const d = dialogStore.get().queue[0];
    closeDialog(d.id);
    if (d.kind !== "confirm") throw new Error(`a ${d.kind} dialog`);
    d.resolve(ok);
    return d.options.message;
  }

  it("is described for Claude with formats, folder, types; it is not `confirm` as a whole (the .bfggis window)", () => {
    const c = createApp({ persist: false }).registry.get("project.export")!;
    expect(Object.keys(c.params!)).toEqual(["formats", "folder", "types", "project_id", "filename", "include_view", "start"]);
    expect(c.confirm).toBeFalsy();
    expect(c.description).toContain("после подтверждения пользователем");
  });

  it("without parameters: the .bfggis window as before, nothing asked of the core", async () => {
    const r = await open().registry.execute("project.export", {}, "claude");
    expect(r).toEqual({ ok: true, result: "Открыто окно экспорта" });
    expect(windowStore.get().windows.map((w) => [w.kind, w.props])).toEqual([["project-export", undefined]]);
    expect(sent).toEqual([]);
  });

  it("without a folder: the window «Объекты в файлы» with the choice", async () => {
    const r = await open().registry.execute("project.export", { formats: "wireframe=tridb", types: "wireframe, line_file" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("объекты в файлы");
    const w = windowStore.get().windows[0];
    expect(w.kind).toBe("project-export");
    expect(w.props).toEqual({ mode: "objects", formats: { drillhole_set: "csv", wireframe: "tridb", dtm: "dxf", line_file: "str", raster: "geotiff" }, types: ["wireframe", "line_file"] });
    expect(sent.map((s) => s.path)).toEqual(["/export/formats"]);
  });

  it("Claude with a folder: our own question, then the job; «Отмена» starts nothing", async () => {
    const app = open();
    const run = app.registry.execute("project.export", { formats: "drillhole_set=xlsx", folder: "D:\\Выгрузка", types: "drillhole_set,wireframe" }, "claude");
    expect(await answer(true)).toContain("• Наборы скважин (1) → XLSX\n• Каркасы (2) → DXF");
    const r = await run;
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Запущено задание №41");
    expect(r.result).toContain("«Северный — экспорт ");
    expect(sent.find((s) => s.path === "/export/objects")).toEqual({ method: "POST", path: "/export/objects", body: { formats: { drillhole_set: "xlsx", wireframe: "dxf" }, folder: "D:\\Выгрузка", types: ["drillhole_set", "wireframe"] } });
    expect(app.store.get().workspace.bottomTab).toBe("jobs");

    sent.length = 0;
    const no = app.registry.execute("project.export", { folder: "D:\\Выгрузка" }, "claude");
    expect(await answer(false)).toContain("• Линии (3) → Micromine STR");
    expect((await no).result).toBe("Отменено пользователем");
    expect(sent.some((s) => s.path === "/export/objects")).toBe(false);
  });

  it("refusals are Russian with a hint: no objects, unknown format, read-only, a 423 of the core", async () => {
    const app = open();
    expect((await app.registry.execute("project.export", { types: "dtm", folder: "D:\\" }, "claude")).result).toBe("ЦТМ: В проекте нет объектов этого типа. Уберите этот тип из types.");
    expect((await app.registry.execute("project.export", { formats: "raster=png", folder: "D:\\" }, "claude")).result).toBe("Растры: формата «png» нет. Форматы: source, geotiff.");
    formats = { ...FORMATS, read_only: true };
    expect((await app.registry.execute("project.export", { folder: "D:\\" }, "claude")).result).toBe("Проект открыт только для чтения: экспорт недоступен. Откройте проект для записи.");
    formats = FORMATS;
    refuse = json({ code: "read_only", message: "Проект открыт только для просмотра.", hint: "Экспорт недоступен в режиме просмотра." }, 423);
    expect((await app.registry.execute("project.export", { folder: "D:\\" }, "user")).result).toBe("Проект открыт только для просмотра. Экспорт недоступен в режиме просмотра.");
    expect(app.store.get().statusNote).toBe("Проект открыт только для просмотра. Экспорт недоступен в режиме просмотра.");
    expect(dialogStore.get().queue).toEqual([]); // a call of the user is not asked again
  });

  it("without a project the command says why", () => {
    expect(createApp({ persist: false }).registry.reason("project.export")).toBe("Проект не открыт");
  });
});

describe("own windows only (ТЗ 7.0.11)", () => {
  it("no window.confirm / prompt / alert / open in the files of the export", () => {
    const dir = path.join(__dirname, "../src/modules/projects");
    for (const f of ["ExportWindow.tsx", "ExportObjects.tsx", "exportObjects.ts", "bind.ts"]) {
      const text = fs.readFileSync(path.join(dir, f), "utf8");
      expect(text, f).not.toMatch(/window\.(confirm|prompt|alert|open)\s*\(|(^|[^.\w])(confirm|prompt|alert)\s*\(/m);
    }
  });
});
