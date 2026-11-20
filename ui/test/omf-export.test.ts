/** «Экспорт OMF» (ТЗ 7.24; ADR 0017 §4, decision 9; Э11 11.12): three lists with what cannot be written and why,
 * what is checked, the name and the body of `POST /omf/export`; `omf.export` from the interface opens the window,
 * from the assistant with `dataset_ids` — our own confirmation and at once the job into
 * `Документы\BedrockForge\Экспорт`; 409 `files_exist` — «Заменить?» in our own window. No window.confirm / prompt / alert. */
import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { closeWindow, windowStore } from "../src/shell/windows";
import { followExportable, type ExportableLists } from "../src/modules/omf/api";
import { bindOmf } from "../src/modules/omf/bind";
import {
  chosenText,
  cleanFileName,
  exportBody,
  exportFolder,
  exportName,
  exportReason,
  initialChosen,
  isDevice,
  keepChosen,
  NONE,
  parseIds,
  resultText,
  rowNote,
  sortIds,
} from "../src/modules/omf/omfExport";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";

const LISTS: ExportableLists = {
  wireframes: [
    { id: 41, name: "Рудное тело", folder: "Импорт/model.omf", dtm: null, triangles: 196, available: true, reason: null },
    { id: 42, name: "Рельеф", folder: null, dtm: { shade: true }, triangles: 18, available: true, reason: null },
  ],
  line_files: [
    { id: 51, name: "roads.dxf", folder: null, layers: 3, points: 1200, available: true, reason: null },
    { id: 52, name: "Облако", folder: null, layers: 1, points: 10_000_000, available: false, reason: { message: "облако точек в OMF не записывается", hint: "Экспортируйте его в LAS." } },
  ],
  block_models: [{ id: 61, name: "Ресурсная", folder: null, cells: 3000, available: true, reason: null }],
};

describe("the model of the window", () => {
  it("ids of the catalog: numbers by commas, sorted into the three lists; unknown and unavailable told", () => {
    expect(parseIds("41, 61,41 ; 52")).toEqual([41, 61, 52]);
    expect(parseIds("41, x")).toBeNull();
    expect(parseIds(" ")).toBeNull();
    const s = sortIds(LISTS, [41, 51, 61, 52, 99]);
    expect(s.chosen).toEqual({ wireframes: [41], line_files: [51], block_models: [61] });
    expect(s.unknown).toEqual([99]);
    expect(s.unavailable).toEqual(["«Облако»: облако точек в OMF не записывается Экспортируйте его в LAS."]);
  });

  it("checked when it opens: the named objects; nothing named — every one that can be written", () => {
    expect(initialChosen(LISTS, [42])).toEqual({ wireframes: [42], line_files: [], block_models: [] });
    expect(initialChosen(LISTS, null)).toEqual({ wireframes: [41, 42], line_files: [51], block_models: [61] });
    const gone = { ...LISTS, wireframes: LISTS.wireframes.slice(1) };
    expect(keepChosen(gone, { wireframes: [41, 42], line_files: [52], block_models: [] })).toEqual({ wireframes: [42], line_files: [], block_models: [] });
  });

  it("what is checked, why it cannot start, the name offered and the body", () => {
    const c = { wireframes: [41, 42], line_files: [51], block_models: [61] };
    expect(chosenText(c)).toBe("2 каркаса, 1 файл линий, 1 блочная модель");
    expect(chosenText(NONE)).toBe("Ничего не отмечено");
    expect(exportReason({ wireframes: [], line_files: [], block_models: [] }, NONE, null)).toMatch(/экспортировать нечего/);
    expect(exportReason(LISTS, NONE, "C:\\a.omf")).toBe("Отметьте каркасы, файлы линий или блочные модели.");
    expect(exportReason(LISTS, c, null)).toBe("Выберите файл: «Сохранить как…».");
    expect(exportReason(LISTS, c, "C:\\a.omf")).toBeNull();
    expect(exportName(LISTS, { ...NONE, line_files: [51] }, "Карьер")).toBe("roads.omf");
    expect(exportName(LISTS, c, "Карьер: 2026")).toBe("Карьер_ 2026.omf");
    expect(exportName(LISTS, c, null)).toBe("Экспорт.omf");
    expect(exportBody(c, "D:\\out\\a.omf")).toEqual({ wireframe_ids: [41, 42], line_file_ids: [51], block_model_ids: [61], path: "D:\\out\\a.omf" });
  });

  it("the file of the assistant: Документы\\BedrockForge\\Экспорт, a name without folders, .omf", () => {
    expect(exportFolder("C:\\Users\\Иван\\Documents\\BedrockForge\\Проекты")).toBe("C:\\Users\\Иван\\Documents\\BedrockForge\\Экспорт");
    expect(exportFolder("/home/u/Documents/BedrockForge/Проекты/")).toBe("/home/u/Documents/BedrockForge/Экспорт");
    expect(exportFolder("D:\\Работа")).toBe("D:\\Работа\\Экспорт");
    expect(cleanFileName("..\\..\\Windows\\evil", "x")).toBe("evil.omf");
    expect(cleanFileName("Карьер.OMF", "x")).toBe("Карьер.omf");
    expect(cleanFileName(undefined, "Рельеф.omf")).toBe("Рельеф.omf");
    expect(cleanFileName("a:b?", "x")).toBe("a_b_.omf");
  });

  it("the name of the file by the rules of the core (names.py): devices, dots and spaces at the end, 120 characters (Э11 11.21)", () => {
    expect(cleanFileName("CON", "x")).toBe("_CON.omf");
    expect(cleanFileName("nul.omf", "x")).toBe("_nul.omf");
    expect(cleanFileName("com1", "x")).toBe("_com1.omf");
    expect(cleanFileName("LPT9.txt", "x")).toBe("_LPT9.txt.omf");
    expect(cleanFileName("COM¹", "x")).toBe("_COM¹.omf");
    expect(cleanFileName("Тело. . ", "x")).toBe("Тело.omf");
    expect(cleanFileName("...", "Рельеф")).toBe("Рельеф.omf");
    expect(cleanFileName("a\u007fb", "x")).toBe("a_b.omf");
    expect(cleanFileName("Консоль", "x")).toBe("Консоль.omf"); // not a device
    expect(cleanFileName("COM10", "x")).toBe("COM10.omf");
    const long = cleanFileName("я".repeat(300), "x");
    expect(long.length).toBe(120);
    expect(cleanFileName(`${"я".repeat(115)}.  x`, "x").length).toBeLessThanOrEqual(120);
    expect(cleanFileName(`${"я".repeat(115)}. x`, "x")).not.toMatch(/[. ]\.omf$/);
    expect([isDevice("Aux.txt"), isDevice("con "), isDevice("conx")]).toEqual([true, true, false]);
    expect(exportName(LISTS, { ...NONE, wireframes: [41] }, null)).toBe("Рудное тело.omf");
  });

  it("the lists read again on `objects.changed`: an older answer coming late is dropped, the check of a new object stays (Э11 11.21)", async () => {
    const answers: ((l: ExportableLists) => void)[] = [];
    let emit: (event: string) => void = () => undefined;
    let chosen = NONE;
    const off = followExportable(
      (l, first) => (chosen = first ? initialChosen(l, null) : keepChosen(l, chosen)),
      (e) => {
        throw e;
      },
      () => new Promise<ExportableLists>((resolve) => answers.push(resolve)),
      (fn) => ((emit = fn), () => undefined),
    );
    const old: ExportableLists = { ...LISTS, block_models: [] };
    answers[0](old);
    await Promise.resolve();
    expect(chosen.block_models).toEqual([]);
    emit("objects.changed"); // a read that answers late: the list without the new model
    emit("objects.changed"); // the newest: the new model is there
    answers[2](LISTS);
    await Promise.resolve();
    chosen = { ...chosen, block_models: [61] }; // the user checks the new model
    answers[1](old);
    await Promise.resolve();
    expect(chosen.block_models).toEqual([61]); // the older answer did not uncheck it
    emit("jobs.changed");
    expect(answers).toHaveLength(3);
    off();
    emit("objects.changed");
    answers[3](old);
    await Promise.resolve();
    expect(chosen.block_models).toEqual([61]); // nothing after the unsubscribe
  });

  it("the report and the notes of the rows", () => {
    expect(resultText({ path: "D:\\out\\a.omf", wireframes: 2, line_layers: 3, block_models: 1, cells: 3000 })).toBe("Записан a.omf: каркасов 2, слоёв линий 3, блочных моделей 1 (3\u00a0000 ячеек)");
    expect(rowNote("wireframes", LISTS.wireframes[1])).toBe("18 треугольников · ЦТМ");
    expect(rowNote("line_files", LISTS.line_files[0])).toBe("3 слоя, 1\u00a0200 точек");
    expect(rowNote("block_models", LISTS.block_models[0])).toBe("3\u00a0000 ячеек");
  });

  it("no window.confirm / prompt / alert in the module", () => {
    const dir = path.resolve(__dirname, "../src/modules/omf");
    for (const f of fs.readdirSync(dir)) {
      const text = fs.readFileSync(path.join(dir, f), "utf8");
      expect(text, f).not.toMatch(/window\.(confirm|prompt|alert|open)\b|\b(confirm|prompt|alert)\(/);
    }
  });
});

// --- omf.export against a small core ------------------------------------------------------------------------

function core(clash: boolean) {
  const calls: { method: string; url: string; body: Record<string, unknown> | undefined }[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const p = String(url).replace(/^.*\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({ method, url: p, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (p === "/omf/exportable") return json(LISTS);
    if (p === "/projects") return json({ root: "C:\\Users\\u\\Documents\\BedrockForge\\Проекты", projects: [] });
    if (p === "/omf/export") {
      if (clash && !body?.overwrite) return json({ code: "files_exist", message: "Файл уже есть.", hint: "Выберите другое имя.", files: ["Рельеф.omf"] }, 409);
      return json({ source: "project", id: 12 });
    }
    if (p === "/lines/files") return json({ files: [] });
    if (p === "/rasters") return json({ rasters: [] });
    if (p === "/wireframes") return json({ wireframes: [] });
    if (p === "/blocks") return json({ block_models: [] });
    return json({ jobs: [], workers: null });
  });
  return { calls, fetch };
}

function open(clash = false) {
  const app = createApp({ persist: false });
  bindOmf(app); // the line of `shell/app.ts` (orchestrator)
  const c = core(clash);
  vi.stubGlobal("fetch", c.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "Карьер" } as ProjectInfo });
  app.store.set({ projectName: "Карьер" });
  return { app, c };
}

async function answer(yes: boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind !== "confirm") throw new Error(`not a confirm: ${d.kind}`);
  d.resolve(yes);
  return `${d.options.title}: ${d.options.message}`;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  dialogStore.set({ queue: [] });
  for (const w of windowStore.get().windows) closeWindow(w.id);
});

describe("omf.export", () => {
  it("is bound, described in Russian and not `confirm` (the handler asks itself)", () => {
    const { app } = open();
    const cmd = app.registry.get("omf.export")!;
    expect(cmd.stage).toBeUndefined();
    expect(cmd.confirm).toBeFalsy();
    expect(cmd.description).toMatch(/Документы\\BedrockForge\\Экспорт/);
    expect(app.registry.list().filter((x) => x.id === "omf.export")).toHaveLength(1);
  });

  it("from the interface (a row of the tree) — the window with the object checked; nothing is written", async () => {
    const { app, c } = open();
    const r = await app.registry.execute("omf.export", { dataset_ids: "42" });
    expect(r.ok).toBe(true);
    expect(windowStore.get().windows.find((w) => w.kind === "omf-export")?.props).toEqual({ ids: [42] });
    expect(c.calls.some((x) => x.url === "/omf/export")).toBe(false);
  });

  it("from the assistant without dataset_ids — the window", async () => {
    const { app } = open();
    expect((await app.registry.execute("omf.export", {}, "claude")).result).toMatch(/Открыто окно «Экспорт OMF»/);
    expect(windowStore.get().windows.find((w) => w.kind === "omf-export")?.props).toBeUndefined();
  });

  it("from the assistant with dataset_ids — own confirmation, then the job into Документы\\BedrockForge\\Экспорт", async () => {
    const { app, c } = open();
    const run = app.registry.execute("omf.export", { dataset_ids: "42, 61", filename: "C:\\tmp\\Рельеф" }, "claude");
    expect(await answer(true)).toContain("C:\\Users\\u\\Documents\\BedrockForge\\Экспорт\\Рельеф.omf");
    const r = await run;
    expect(r).toEqual({ ok: true, result: "Экспорт OMF начат (задание №12): 1 каркас, 1 блочная модель → C:\\Users\\u\\Documents\\BedrockForge\\Экспорт\\Рельеф.omf" });
    expect(c.calls.find((x) => x.url === "/omf/export")?.body).toEqual({ wireframe_ids: [42], line_file_ids: [], block_model_ids: [61], path: "C:\\Users\\u\\Documents\\BedrockForge\\Экспорт\\Рельеф.omf", overwrite: false });
  });

  it("the file is there: «Заменить?» in our own window, then again with overwrite; «нет» — nothing written", async () => {
    let { app, c } = open(true);
    let run = app.registry.execute("omf.export", { dataset_ids: "42" }, "claude");
    await answer(true);
    expect(await answer(true)).toContain("Рельеф.omf");
    expect((await run).ok).toBe(true);
    expect(c.calls.filter((x) => x.url === "/omf/export").map((x) => x.body?.overwrite)).toEqual([false, true]);
    ({ app, c } = open(true));
    run = app.registry.execute("omf.export", { dataset_ids: "42" }, "claude");
    await answer(true);
    await answer(false);
    expect(await run).toEqual({ ok: true, result: "Отменено: файл не заменён" });
    expect(c.calls.filter((x) => x.url === "/omf/export")).toHaveLength(1);
  });

  it("declined, wrong ids, unknown and unavailable objects are told in Russian; nothing is started", async () => {
    const { app, c } = open();
    const run = app.registry.execute("omf.export", { dataset_ids: "41" }, "claude");
    await answer(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect((await app.registry.execute("omf.export", { dataset_ids: "41, x" }, "claude")).result).toMatch(/номера объектов через запятую/);
    expect((await app.registry.execute("omf.export", { dataset_ids: "99" }, "claude")).result).toMatch(/Объекты №99 не найдены/);
    expect((await app.registry.execute("omf.export", { dataset_ids: "52" }, "claude")).result).toMatch(/Не записываются в OMF: «Облако»/);
    expect(c.calls.some((x) => x.url === "/omf/export")).toBe(false);
  });
});
