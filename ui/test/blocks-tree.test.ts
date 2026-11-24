/** Block models in the data tree and «Свойства» (ADR 0017 §3, Э11 11.13): rows and menus as data, «Показать в 3D»
 * available since Э17 17.31, the grid and the fields with statistics (a missing value is «—», never 0),
 * the list following `objects.changed {kind: "block_model"}`, and `object.select / rename / delete` with
 * `kind: "blocks"` — own windows from the interface, the version of the model, 409 with the text of the core. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { menuItems } from "../src/shell/treeRun";
import { sectionActions, sectionPlus } from "../src/shell/treeSections";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { bindBlocks } from "../src/modules/blocks/bind";
import { bytes, fieldRow, gridFacts, sourceFacts, stepText } from "../src/modules/blocks/props";
import { blocksStore, clearBlocks, isBlocksEvent, loadBlockDetails, loadBlocks } from "../src/modules/blocks/state";
import { blockKey, blockMenu, blockRow, blockSummary } from "../src/modules/blocks/tree";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { sceneObjects } from "../src/modules/scene/objects";

const model = (id: number, p: Partial<BlockModelInfo> = {}): BlockModelInfo => ({
  id, name: `Модель ${id}`, folder: null, version: 1, extent: [5000, 6000, 100, 5200, 6150, 140], crs: null,
  grid: { corner: [5000, 6000, 100], counts: [40, 30, 20], size: [5, 5, 2], order: "F", variable: false },
  cells: 10000, fields: [{ name: "Au", kind: "number", unit: "г/т" }, { name: "Порода", kind: "category", unit: null }],
  source: { format: "omf", file: "model.omf", element: "Блоки" }, warnings: [], size: 1_572_864,
  created_at: "2026-11-19T10:00:00", updated_at: "2026-11-19T10:00:00", ...p,
});
const STATS = {
  Au: { name: "Au", kind: "number" as const, unit: "г/т", stats: { count: 9000, missing: 1000, min: 0.01, max: 12.5, mean: 1.234, std: 0.9 } },
  Rock: { name: "Порода", kind: "category" as const, unit: null, labels: ["руда", "порода"], stats: { count: 10000, missing: 0, counts: [400, 9600] } },
};
const detail = (b: BlockModelInfo): BlockModelInfo => ({ ...b, fields: [STATS.Au, STATS.Rock] });
const labels = (menu: { label: string; separator?: boolean }[]) => menu.filter((a) => !a.separator).map((a) => a.label);

describe("rows and menus of «Блочные модели»", () => {
  it("a row tells the grid, the cells and the fields; no check box, a click selects", () => {
    const b = model(7, { name: "Ресурсная" });
    expect(blockSummary(b)).toBe("40 × 30 × 20, 10 000 ячеек, 2 поля");
    expect(blockSummary(model(1, { fields: [], cells: 1, grid: { ...b.grid, counts: [1, 1, 1] } }))).toBe("1 × 1 × 1, 1 ячейка, без полей");
    const row = blockRow(b);
    expect(row.testId).toBe("tree-block-7");
    expect(row.click).toBe("select");
    expect(row.buttons).toEqual([]);
    expect(row.title).toContain("показать в 3D");
    expect(blockKey(7)).toBe("bm:7");
  });

  it("the menu: Свойства, Показать в 3D, Скрыть в 3D, Экспорт OMF…, Переименовать…, Удалить…", () => {
    const menu = blockMenu({ id: 7 });
    expect(labels(menu)).toEqual(["Свойства", "Показать в 3D", "Скрыть в 3D", "Экспорт OMF…", "Переименовать…", "Удалить…"]);
    expect(menu.find((a) => a.label === "Показать в 3D")).toMatchObject({ command: "blocks.show", args: { model_id: 7 } });
    expect(menu.find((a) => a.label === "Скрыть в 3D")).toMatchObject({ command: "blocks.show", args: { model_id: 7, visible: false } });
    expect(menu.find((a) => a.label === "Экспорт OMF…")).toMatchObject({ command: "omf.export", args: { dataset_ids: "7" } });
    expect(menu.find((a) => a.label === "Переименовать…")).toMatchObject({ command: "object.rename", args: { kind: "blocks", id: 7 } });
    expect(menu.find((a) => a.label === "Удалить…")).toMatchObject({ command: "object.delete", args: { kind: "blocks", id: 7 } });
  });

  it("«Показать в 3D» and «Скрыть в 3D» are available with a project (Э17); rename and delete too", () => {
    const app = createApp({ persist: false });
    app.store.set({ projectName: "Проект" });
    const items = menuItems(app, blockMenu({ id: 7 }), () => undefined).filter((i) => !i.separator);
    expect(items.find((i) => i.label === "Показать в 3D")?.reason).toBeNull();
    expect(items.find((i) => i.label === "Скрыть в 3D")?.reason).toBeNull();
    expect(items.find((i) => i.label === "Свойства")?.reason).toBeNull();
    expect(items.find((i) => i.label === "Переименовать…")?.reason).toBeNull();
    expect(items.find((i) => i.label === "Удалить…")?.reason).toBeNull();
    expect(app.registry.get("blocks.show")?.description).toMatch(/[А-Яа-яЁё]{4,}/);
  });

  it("the section header keeps its «+» and menu (orchestrator: `treeSections.ts`)", () => {
    expect(sectionPlus("blocks")?.title).toBe("Загрузить блочную модель");
    expect(sectionActions("blocks").length).toBeGreaterThan(0);
  });
});

describe("«Свойства» of a block model", () => {
  it("the grid: counts, the share of occupied cells, the step, the corner, the extent, the file", () => {
    const facts = Object.fromEntries(gridFacts(model(1)).map((f) => [f.label, f.value]));
    expect(facts["Сетка"]).toBe("40 × 30 × 20 (24 000 ячеек)");
    expect(facts["Занято ячеек"]).toBe("10 000 (41,7 %)");
    expect(facts["Размер ячейки"]).toBe("5 × 5 × 2 м");
    expect(facts["Угол модели"]).toBe("X 5 000, Y 6 000, Z 100");
    expect(facts["X"]).toBe("5 000 … 5 200");
    expect(facts["Файл ячеек"]).toBe("1,5 МБ");
    expect(facts["Система координат"]).toBeUndefined();
    expect(bytes(12)).toBe("12 Б");
  });

  it("a variable step: «переменный», with the ranges once the details came", () => {
    const g = { corner: [0, 0, 0] as [number, number, number], counts: [3, 2, 1] as [number, number, number], order: "F" as const, variable: true };
    expect(stepText({ grid: g })).toBe("переменный");
    expect(stepText({ grid: { ...g, tensors: { x: [1, 2, 4], y: [5, 5], z: [2.5] } } })).toBe("переменный: X 1…4, Y 5, Z 2,5 м");
  });

  it("fields: a number with its statistics, a category with its labels; a missing value is «—», never 0", () => {
    expect(fieldRow(STATS.Au)).toMatchObject({ name: "Au", kind: "число", unit: "г/т", count: "9 000", missing: "1 000", min: "0,01", max: "12,5", mean: "1,234", std: "0,9", labels: "" });
    expect(fieldRow({ name: "Cu", kind: "number", unit: null, stats: { count: 0, missing: 50, min: null, max: null, mean: null, std: null } })).toMatchObject({ unit: "—", count: "0", missing: "50", min: "—", mean: "—" });
    expect(fieldRow({ name: "Cu", kind: "number", unit: null })).toMatchObject({ count: "—", missing: "—", min: "—" }); // the list has no stats
    expect(fieldRow(STATS.Rock).labels).toBe("2 значения: порода — 9 600, руда — 400");
    const many = Array.from({ length: 8 }, (_, i) => `к${i}`);
    expect(fieldRow({ name: "К", kind: "category", unit: null, labels: many, stats: { count: 8, missing: 0, counts: many.map((_, i) => i) } }).labels).toBe("8 значений: к7 — 7, к6 — 6, к5 — 5, к4 — 4, к3 — 3 и ещё 3");
  });

  it("the source of the import", () => {
    expect(sourceFacts({ format: "omf", file: "model.omf", element: "Блоки" })).toEqual([
      { label: "Формат", value: "OMF" },
      { label: "Файл", value: "model.omf" },
      { label: "Элемент", value: "Блоки" },
    ]);
    expect(sourceFacts(null)).toEqual([]);
  });
});

// --- a small core -------------------------------------------------------------------------------------------

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | undefined;
}

function server() {
  const state = { blocks: [model(7, { name: "Ресурсная" }), model(8, { name: "Карьер", folder: "OMF/site" })] };
  const calls: Call[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({ method, url: path, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (path === "/blocks") return json({ block_models: state.blocks });
    const at = /^\/blocks\/(\d+)$/.exec(path);
    if (at) {
      const b = state.blocks.find((x) => x.id === Number(at[1]));
      if (!b) return json({ code: "not_found", message: "Блочная модель не найдена.", hint: "Обновите дерево проекта." }, 404);
      if (method === "GET") return json(detail(b));
      if (method === "DELETE") {
        state.blocks = state.blocks.filter((x) => x.id !== b.id);
        return json({ deleted: b.id });
      }
      if (body?.expected_version !== b.version) return json({ code: "version_conflict", message: "Модель изменили, пока вы её правили.", hint: "Повторите правку." }, 409);
      if (state.blocks.some((x) => x.id !== b.id && x.name.toLowerCase() === String(body?.name).toLowerCase()))
        return json({ code: "name_taken", message: `Название «${String(body?.name)}» уже занято.`, hint: "Введите другое название." }, 409);
      const next = { ...b, name: String(body?.name), version: b.version + 1 };
      state.blocks = state.blocks.map((x) => (x.id === b.id ? next : x));
      return json(next);
    }
    if (path === "/lines/files") return json({ files: [] });
    if (path === "/rasters") return json({ rasters: [] });
    if (path === "/wireframes") return json({ wireframes: [] });
    if (path === "/jobs") return json({ jobs: [], workers: null });
    return json({});
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
  bindBlocks(app);
  await vi.waitFor(() => expect(blocksStore.get().loaded).toBe(true));
  core.calls.length = 0;
  return app;
}

async function answer(value: string | boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind === "text") d.resolve(typeof value === "string" ? value : null);
  else if (d.kind === "confirm") d.resolve(value === true);
  return d.kind === "message" ? d.options.message : d.kind === "confirm" ? d.options.message : d.options.title;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  clearBlocks();
  dialogStore.set({ queue: [] });
});

describe("the list follows the project and the core", () => {
  it("is read when the project opens and cleared when it closes", async () => {
    await open();
    expect(blocksStore.get().list.map((b) => b.name)).toEqual(["Ресурсная", "Карьер"]);
    projectStore.set({ current: null });
    expect(blocksStore.get()).toMatchObject({ list: [], loaded: false });
  });

  it("`objects.changed` of a block model (or without a kind) reads it again; others do not", () => {
    expect(isBlocksEvent(JSON.stringify({ kind: "block_model", id: 7 }))).toBe(true);
    expect(isBlocksEvent(JSON.stringify({ kind: "block_model", id: 7, deleted: true }))).toBe(true);
    expect(isBlocksEvent("{}")).toBe(true);
    expect(isBlocksEvent(JSON.stringify({ kind: "section_series", id: 1 }))).toBe(false);
  });

  it("the details are read for «Свойства» and again when the version moved; a newer read wins", async () => {
    await open();
    await loadBlockDetails(7);
    expect(blocksStore.get().details[7].fields[0].stats).toMatchObject({ mean: 1.234 });
    core.state.blocks = core.state.blocks.map((b) => (b.id === 7 ? { ...b, version: 2, name: "Ресурсная 2" } : b));
    await loadBlocks();
    await vi.waitFor(() => expect(blocksStore.get().details[7]?.version).toBe(2));
    expect(core.calls.filter((c) => c.url === "/blocks/7").length).toBe(2);
  });
});

describe("object.* of a block model", () => {
  it("object.select selects it; a model that is gone is not selected any more", async () => {
    const app = await open();
    const r = await app.registry.execute("object.select", { kind: "blocks", id: 8 });
    expect(r).toEqual({ ok: true, result: "Выбрана блочная модель «Карьер»" });
    expect(app.store.get().selected).toEqual({ kind: "blocks", id: 8 });
    sceneObjects.set({ loaded: true }); // the other lists say nothing about block models
    expect(app.store.get().selected).toEqual({ kind: "blocks", id: 8 });
    core.state.blocks = core.state.blocks.filter((b) => b.id !== 8);
    await loadBlocks();
    expect(app.store.get().selected).toBeNull();
    expect((await app.registry.execute("object.select", { kind: "blocks", id: 99 })).result).toBe("Блочная модель №99 не найдена: обновите дерево проекта");
  });

  it("rename from the interface asks in an own window and sends the version", async () => {
    const app = await open();
    const run = app.registry.execute("object.rename", { kind: "blocks", id: 7 });
    expect(await answer("  Ресурсная   v2 ")).toBe("Переименовать: блочная модель");
    expect(await run).toEqual({ ok: true, result: "Блочная модель «Ресурсная» переименована в «Ресурсная v2»" });
    expect(core.calls.find((c) => c.method === "PATCH")).toEqual({ method: "PATCH", url: "/blocks/7", body: { name: "Ресурсная v2", expected_version: 1 } });
    expect(blocksStore.get().list.find((b) => b.id === 7)?.name).toBe("Ресурсная v2");
  });

  it("rename by Claude takes `name`; a name in use — 409 with the text and hint of the core", async () => {
    const app = await open();
    const r = await app.registry.execute("object.rename", { kind: "blocks", id: 7, name: "карьер" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Название «карьер» уже занято.");
    expect(r.result).toContain("Введите другое название.");
    expect(dialogStore.get().queue).toHaveLength(0);
  });

  it("delete from the interface asks «Удалить?» (danger) and removes the model", async () => {
    const app = await open();
    await app.registry.execute("object.select", { kind: "blocks", id: 7 });
    const run = app.registry.execute("object.delete", { kind: "blocks", id: 7 });
    await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.danger).toBe(true);
    expect(await answer(true)).toContain("Блочная модель «Ресурсная» удаляется из проекта");
    expect(await run).toEqual({ ok: true, result: "Блочная модель «Ресурсная» удалена" });
    expect(core.calls.some((c) => c.method === "DELETE" && c.url === "/blocks/7")).toBe(true);
    expect(blocksStore.get().list.map((b) => b.id)).toEqual([8]);
    expect(app.store.get().selected).toBeNull();
  });

  it("delete cancelled in the window changes nothing; the colour is not for block models", async () => {
    const app = await open();
    const run = app.registry.execute("object.delete", { kind: "blocks", id: 8 });
    await answer(false);
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(core.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect((await app.registry.execute("object.color", { kind: "blocks", id: 8, color: "#ff0000" })).ok).toBe(false);
  });
});
