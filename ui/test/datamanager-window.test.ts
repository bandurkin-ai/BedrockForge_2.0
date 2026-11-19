// The window «Менеджер данных · ‹проект›» (Д-20; ADR 0017 §6; Э11 11.16): the command `data.manager`, the actions
// of the rows by the commands of the modules (unavailable ones with a reason), delete — our own question (danger),
// `dataset.move` → `POST /catalog/move`, «Экспорт…» / «Исходный файл…» → the window «Экспорт проекта», the virtual
// scroll (1 000 rows — ≤ 60 in the DOM). The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { catalogStore } from "../src/modules/datamanager/api";
import { bindDataManager, dataManagerTitle, deleteRows, exportRows, importInto, objectKind, rowActions, runRowAction } from "../src/modules/datamanager/bind";
import { DEFAULT_QUERY, visibleRange, scrollToRow, type CatalogRow } from "../src/modules/datamanager/catalog";
import { renderDataManagerWindow } from "../src/modules/datamanager/DataManager";
import { cellText, DataTable } from "../src/modules/datamanager/DataTable";
import { moveIds } from "../src/modules/projects/bind";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore, type FloatWin } from "../src/shell/windows";

const row = (id: number, p: Partial<CatalogRow> = {}): CatalogRow => ({ id, kind: "wireframe", name: `Объект ${id}`, folder: null, format: null, size: null, rows: null, modified: null, crs: null, state: "ready", ...p });

const sent: { method: string; path: string; body: unknown }[] = [];
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
let moveAnswer: Response | null = null;

beforeEach(() => {
  sent.length = 0;
  moveAnswer = null;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const p = new URL(url).pathname.replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    if (p.startsWith("/catalog")) sent.push({ method, path: p, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (p === "/catalog/move") return moveAnswer ?? json({ moved: (JSON.parse(String(init?.body)) as { ids: number[] }).ids.length, folder: "x" });
    if (p === "/catalog/folders") return json({ folders: [] });
    if (p === "/catalog") return json({ total: 0, rows: [] });
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
  catalogStore.set({ rows: [], folders: [], loaded: false, error: null });
});

function open() {
  const app = createApp({ persist: false });
  bindDataManager(app); // shell/app.ts binds it (a line of the orchestrator)
  app.registry.hooks = { ...app.registry.hooks, confirm: async () => true };
  app.store.set({ projectName: "Северный" });
  return app;
}

async function answer(ok: boolean) {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind !== "confirm") throw new Error(d.kind);
  d.resolve(ok);
  return d.options;
}

describe("the command `data.manager`", () => {
  it("opens one window «Менеджер данных · ‹проект›»; uiOnly; without a project — the reason", async () => {
    const app = open();
    const c = app.registry.get("data.manager")!;
    expect(c.uiOnly).toBe(true);
    expect((await app.registry.execute("data.manager")).ok).toBe(true);
    await app.registry.execute("data.manager");
    expect(windowStore.get().windows.map((w) => [w.kind, w.title])).toEqual([["data-manager", "Менеджер данных · Северный"]]);
    expect(dataManagerTitle(null)).toBe("Менеджер данных · проект");
    app.store.set({ projectName: null });
    expect(app.registry.reason("data.manager")).toBe("Откройте или создайте проект");
    expect(renderDataManagerWindow({ kind: "other" } as FloatWin)).toBeNull();
  });
});

describe("the actions of the rows", () => {
  const wf = row(1, { name: "Тело", format: "TRIDB" });
  const set = row(2, { kind: "drillhole_set", name: "Скважины", rows: 100 });
  const lf = row(3, { kind: "line_file", name: "Контуры.str", format: "STR" });
  const bm = row(4, { kind: "block_model", name: "Модель" });
  const busy = row(5, { state: "building" });
  const reasons = (rows: CatalogRow[]) => Object.fromEntries(rowActions(rows, (c) => (c === "blocks.show" ? "Функция появится на этапе Э17" : null)).map((a) => [a.id, a.reason]));

  it("the object commands by the kind of the row", () => {
    expect(["wireframe", "dtm", "raster", "line_file", "cloud", "block_model", "drillhole_set"].map(objectKind)).toEqual(["wireframe", "wireframe", "raster", "line_file", "line_file", "blocks", null]);
  });

  it("unavailable ones say why, in Russian", () => {
    expect(reasons([])).toMatchObject({ properties: "Выберите строку", show: "Выберите строки", delete: "Выберите строки" });
    expect(reasons([wf])).toEqual({ properties: null, show: null, export: null, source: null, rename: null, delete: null });
    expect(reasons([wf, set]).properties).toBe("Выберите одну строку");
    expect(reasons([set])).toMatchObject({ rename: "Наборы скважин: переименование — в дереве проекта", delete: "Наборы скважин: удаление — в дереве проекта", source: "Объект создан в программе: исходного файла нет" });
    expect(reasons([lf]).rename).toBe("Файл линий не переименовывается: переименуйте его слой в дереве");
    expect(reasons([bm]).show).toBe("Функция появится на этапе Э17");
    expect(reasons([bm, wf]).show).toBeNull();
    expect(reasons([busy]).delete).toBe("Объект ещё строится: дождитесь конца задания");
    expect(reasons([set, wf]).delete).toBeNull(); // the wireframe is deleted, the set is told
  });

  it("«Экспорт…» and «Исходный файл…» open «Экспорт проекта › Объекты в файлы» with the ids", () => {
    exportRows([wf, lf, set]);
    exportRows([wf, set], true);
    expect(windowStore.get().windows.map((w) => w.props)).toEqual([
      { mode: "objects", ids: [1, 3, 2], types: ["wireframe", "line_file", "drillhole_set"] },
      { mode: "objects", ids: [1], types: ["wireframe"], formats: { wireframe: "source" } },
    ]);
  });

  it("one row: `object.delete` asks itself; several: one own question (danger), then each without asking again", async () => {
    const app = open();
    const calls: [unknown, string][] = [];
    app.registry.bind("object.delete", async (a, source) => {
      calls.push([a, source]);
      return "удалено";
    });
    expect(await deleteRows(app, [wf])).toBe("удалено");
    expect(calls).toEqual([[{ kind: "wireframe", id: 1 }, "user"]]);

    calls.length = 0;
    const run = deleteRows(app, [wf, lf, bm, set]);
    const q = await answer(true);
    expect(q.danger).toBe(true);
    expect(q.okLabel).toBe("Удалить");
    expect(q.message).toContain("Удалить из проекта 3 объекта?\n• Тело (Каркасы)\n• Контуры.str (Линии)\n• Модель (Блочные модели)");
    expect(q.message).toContain("1 объект здесь не удаляются");
    expect(await run).toBe("Удалено 3 из 3");
    expect(calls).toEqual([[{ kind: "wireframe", id: 1 }, "claude"], [{ kind: "line_file", id: 3 }, "claude"], [{ kind: "blocks", id: 4 }, "claude"]]);

    calls.length = 0;
    const no = deleteRows(app, [wf, lf]);
    await answer(false);
    expect(await no).toBe("Отменено");
    expect(calls).toEqual([]);
  });

  it("an action not available is refused with its reason", async () => {
    await expect(runRowAction(open(), "rename", [set])).rejects.toThrow("Наборы скважин: переименование — в дереве проекта");
  });

  it("«Импорт…» of a folder: the folder goes to `import.files` once it takes it (11.11), else the status says so", async () => {
    const app = open();
    const got: unknown[] = [];
    app.registry.bind("import.files", async (a) => void got.push(a));
    app.registry.get("import.files")!.params = { kind: { type: "string" } };
    await importInto(app, "Участок 1");
    expect(got).toEqual([{}]);
    expect(app.store.get().statusNote).toContain("Папка назначения «Участок 1»");
    app.registry.get("import.files")!.params = { kind: { type: "string" }, folder: { type: "string" } };
    await importInto(app, "Участок 1");
    expect(got[1]).toEqual({ folder: "Участок 1" });
  });
});

describe("`dataset.move` → `POST /catalog/move`", () => {
  it("ids by commas, or one id; the folder as given; the answer of the core", async () => {
    const app = open();
    const r = await app.registry.execute("dataset.move", { ids: "4, 7,4", folder: "/Участок 1/" }, "user");
    expect(r).toEqual({ ok: true, result: "Перенесено 2 из 2 в папку «Участок 1»" });
    expect(sent.find((s) => s.path === "/catalog/move")).toEqual({ method: "POST", path: "/catalog/move", body: { ids: [4, 7], folder: "Участок 1" } });
    expect((await app.registry.execute("dataset.move", { kind: "wireframe", id: 9, folder: "" }, "claude")).result).toBe("Перенесено 1 из 1 в корень");
    expect((await app.registry.execute("dataset.move", { folder: "a" }, "user")).result).toBe("Укажите объект: id или ids (номера через запятую)");
    moveAnswer = json({ code: "object_not_found", message: "Объект №99 не найден: ничего не перенесено.", hint: "Обновите менеджер данных." }, 404);
    expect((await app.registry.execute("dataset.move", { ids: "99", folder: "a" }, "claude")).result).toBe("Объект №99 не найден: ничего не перенесено. Обновите менеджер данных.");
    expect(app.registry.get("dataset.move")!.confirm).toBe(true); // Claude's call — after the user's confirmation
  });

  it("bad numbers are refused", () => {
    expect(moveIds({ ids: "1,2" })).toEqual([1, 2]);
    expect(moveIds({ id: 3, ids: "3;5" })).toEqual([3, 5]);
    expect(() => moveIds({ ids: "1,x" })).toThrow("Номер объекта «x» — не целое число больше 0");
    expect(() => moveIds({ ids: "0" })).toThrow();
  });
});

describe("the table", () => {
  it("cells: an empty value is «—», never 0", () => {
    const r = row(1, { kind: "drillhole_set", rows: 12400, size: 2048, modified: "2026-11-19T11:05:00Z", state: "building" });
    expect([cellText(r, "kind"), cellText(r, "rows"), cellText(r, "size"), cellText(r, "folder"), cellText(r, "crs"), cellText(r, "state")]).toEqual(["Наборы скважин", "12 400", "2,0 КБ", "—", "—", "строится"]);
    expect(cellText(row(2), "size")).toBe("—");
    expect(cellText(row(2, { modified: "вчера" }), "modified")).toBe("вчера");
  });

  it("the virtual scroll: the rows in view and a few around", () => {
    expect(visibleRange(0, 600, 1000)).toEqual([0, 41]);
    expect(visibleRange(24 * 500, 600, 1000)).toEqual([492, 533]);
    expect(visibleRange(24 * 2000, 600, 1000)[1]).toBe(1000);
    expect(visibleRange(0, 600, 0)).toEqual([0, 0]);
    expect(scrollToRow(30, 0, 600)).toBe(24 * 31 - 600);
    expect(scrollToRow(2, 0, 600)).toBeNull();
    expect(scrollToRow(2, 240, 600)).toBe(48);
  });

  it("1 000 rows: ≤ 60 rows in the DOM", () => {
    const rows = Array.from({ length: 1000 }, (_, i) => row(i + 1));
    const noop = () => undefined;
    const html = renderToStaticMarkup(
      createElement(DataTable, { rows, chosen: new Set([2]), focus: 2, query: DEFAULT_QUERY, onSort: noop, onRowClick: noop, onRowMenu: noop, onRowActivate: noop, onRowDrag: noop, onKeyDown: noop, initialHeight: 600 }),
    );
    const drawn = html.match(/data-dm-row=""/g)?.length ?? 0;
    expect(drawn).toBeGreaterThan(20);
    expect(drawn).toBeLessThanOrEqual(60);
    expect(html).toContain('aria-rowcount="1000"');
    expect(html).toContain('aria-selected="true"');
  });
});
