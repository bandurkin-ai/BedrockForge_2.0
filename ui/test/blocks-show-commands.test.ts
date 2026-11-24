// The commands of the display of block models (ТЗ 8.7.6; ADR 0023 §11, §13; Э17 17.31): `blocks.show` (the stub of Э11 is
// gone), `blocks.filter`, `blocks.quick`, `blocks.style`, `blocks.pick` are records of the registry (ribbon, Ctrl+K, the
// assistant) that change the style of the model in the view of the project (`block_styles`, written to the core and read
// back when the project opens) or ask the core; the model of a call without one is the selected one or the only one; a
// wrong value is refused in Russian with its hint before anything is written. The core is a fetch stub answering with
// the real answers (`viz_answers.json`, `filters_window_answers.json`).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commands, SHOW_BUTTONS } from "../src/modules/blocks/commands";
import { BLOCK_STYLES_KEY, blocksShowStore, pickedBlockStore, resetShow, revealOnShow, setPicked, styleIn } from "../src/modules/blocks/display";
import { FILTERS_WINDOW, filtersStore } from "../src/modules/blocks/filters/state";
import { quickText } from "../src/modules/blocks/showCommands";
import { blocksStore } from "../src/modules/blocks/state";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { viewStore, viewSyncOf } from "../src/modules/scene/bind";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const VIZ = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/viz_answers.json"), "utf-8")) as { cases: { name: string; json?: unknown }[] };
const FILTERS = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/filters_window_answers.json"), "utf-8"));
const AT = VIZ.cases.find((c) => c.name === "at_found")!.json;
const ORE = FILTERS.full.tree.body as { id: number; name: string };
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const info = (id: number, name: string, fields: BlockModelInfo["fields"]): BlockModelInfo => ({
  id, name, folder: null, version: 1, extent: null, crs: null,
  grid: { corner: [0, 0, 0], counts: [8, 6, 4], size: [10, 10, 5], order: "F", variable: false },
  cells: 177, fields, source: null, warnings: [], size: null, created_at: "", updated_at: "",
}); // prettier-ignore
const AU = { name: "AU", kind: "number" as const, unit: "г/т" };
const ROCK = { name: "ROCK", kind: "category" as const, unit: null, labels: ["руда", "порода", "окисленная"] };
const A = info(1, "Ресурсная", [AU, { name: "OK", kind: "number", unit: null }, ROCK]);
const B = info(2, "Карьер", [AU, ROCK]);

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let atAnswer: () => Response;

beforeEach(() => {
  sent.length = 0;
  atAnswer = () => json(AT);
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1, value: body.value });
      if (path === "/filters" && method === "GET") return json({ filters: [] });
      if (path === `/filters/${ORE.id}`) return json(FILTERS.full.tree.body);
      if (/^\/filters\/\d+$/.test(path)) return json({ code: "filter_not_found", message: "Фильтр не найден.", hint: "Обновите список фильтров." }, 404);
      if (/^\/viz\/blocks\/\d+\/at$/.test(path)) return atAnswer();
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  blocksStore.set({ list: [A, B], loaded: true, error: null, details: {}, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  resetShow();
  setPicked(null);
  revealOnShow.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const run = (id: string, args: Record<string, unknown> = {}) => app.registry.execute(id, args);
const style = (id: number) => styleIn(viewStore.get(), id);
const writes = () => sent.filter((s) => s.path === `/view/${BLOCK_STYLES_KEY}`);

describe("registry", () => {
  it("has `blocks.show`, `.filter`, `.quick`, `.style`, `.pick`: Russian, bound, the stage of Э11 is gone; the contract of ADR 0023 §13", () => {
    expect(commands.map((c) => c.id)).toEqual(["blocks.show", "blocks.filter", "blocks.quick", "blocks.style", "blocks.pick"]);
    for (const c of commands) {
      const r = app.registry.get(c.id)!;
      expect(r.run, c.id).toBeTypeOf("function");
      expect(r.stage, c.id).toBeUndefined();
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description.length).toBeGreaterThan(60);
      expect(c.confirm).toBeUndefined(); // the view of the project, nothing of the data
      expect(c.uiOnly).toBeUndefined(); // the assistant runs them too
    }
    const p = (id: string) => Object.keys(commands.find((c) => c.id === id)!.params!);
    expect(p("blocks.show")).toEqual(expect.arrayContaining(["model_id", "visible", "mode"]));
    expect(p("blocks.filter")).toEqual(["model_id", "filter_id"]);
    expect(commands.find((c) => c.id === "blocks.filter")!.params!.filter_id.nullable).toBe(true);
    expect(commands.find((c) => c.id === "blocks.pick")!.required).toEqual(["x", "y", "z"]);
    for (const b of SHOW_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    expect(app.registry.reason("blocks.show")).toBeNull();
    app.store.set({ projectName: null });
    expect(app.registry.reason("blocks.show")).toBe("Откройте или создайте проект");
  });
});

describe("blocks.show", () => {
  it("shows the model: `block_styles[id].visible` in the view, written to the core; the camera goes to it once", async () => {
    const r = await run("blocks.show", { model_id: 1 });
    expect(r).toEqual({ ok: true, result: "Блочная модель «Ресурсная» показана в 3D (оболочка)" });
    expect(style(1)).toMatchObject({ visible: true, mode: "shell" });
    expect(revealOnShow.has(1)).toBe(true);
    await viewSyncOf()!.flush(BLOCK_STYLES_KEY);
    const w = writes();
    expect(w).toHaveLength(1);
    expect(w[0].method).toBe("PUT");
    expect((w[0].body.value as Record<string, unknown>)["1"]).toMatchObject({ visible: true, mode: "shell", filter_id: null, colour: { kind: "none" }, limit: 5_000_000 });
    // shown again: the camera stays where the user put it
    revealOnShow.clear();
    await run("blocks.show", { model_id: 1, mode: "all" });
    expect(style(1).mode).toBe("all");
    expect(revealOnShow.has(1)).toBe(false);
  });

  it("hides; «Менеджер данных» still calls it with `id`; the model of a call without one is the selected or the only one", async () => {
    await run("blocks.show", { id: 2 });
    expect(style(2).visible).toBe(true);
    expect((await run("blocks.show", { model_id: 2, visible: false })).result).toBe("Блочная модель «Карьер» скрыта в 3D");
    expect(style(2).visible).toBe(false);
    app.store.set({ selected: { kind: "blocks", id: 1 } });
    expect((await run("blocks.show", {})).result).toContain("«Ресурсная»");
    app.store.set({ selected: null });
    const r = await run("blocks.show", {});
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Не выбрана блочная модель. Выберите модель в дереве проекта или укажите model_id.");
    blocksStore.set({ list: [B] });
    expect((await run("blocks.show", {})).result).toContain("«Карьер»");
    expect((await run("blocks.show", { model_id: 9 })).result).toBe("Блочная модель №9 не найдена. Номера моделей — в дереве проекта, раздел «Блочные модели».");
    blocksStore.set({ list: [] });
    expect((await run("blocks.show", {})).result).toMatch(/нет блочных моделей/);
  });

  it("the view of the project comes back: what the core keeps is the style of the model", async () => {
    await run("blocks.show", { model_id: 1, mode: "all" });
    await run("blocks.style", { model_id: 1, field: "AU", from: 0.1, to: 10, log: true });
    await viewSyncOf()!.flush(BLOCK_STYLES_KEY);
    const stored = writes().at(-1)!.body.value;
    viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
    expect(style(1).visible).toBe(false);
    viewStore.set({ [BLOCK_STYLES_KEY]: stored });
    expect(style(1)).toMatchObject({ visible: true, mode: "all", colour: { field: "AU", kind: "scale", from: 0.1, to: 10, log: true } });
  });
});

describe("blocks.filter", () => {
  it("binds a filter of the library and shows the model; null takes it off; without filter_id — the window «Фильтры»", async () => {
    const r = await run("blocks.filter", { model_id: 1, filter_id: ORE.id });
    expect(r).toEqual({ ok: true, result: `Фильтр «${ORE.name}» привязан к модели «Ресурсная»; показаны блоки, которые он пропускает` });
    expect(style(1)).toMatchObject({ filter_id: ORE.id, visible: true });
    expect((await run("blocks.filter", { model_id: 1, filter_id: null })).result).toBe("Фильтр модели «Ресурсная» снят");
    expect(style(1)).toMatchObject({ filter_id: null, visible: true });
    const w = await run("blocks.filter", { model_id: 1 });
    expect(w.ok).toBe(true);
    expect(windowStore.get().windows.some((x) => x.kind === FILTERS_WINDOW)).toBe(true);
  });

  it("a filter that is not there is refused in Russian, nothing is written; a model that is not there too", async () => {
    const r = await run("blocks.filter", { model_id: 1, filter_id: 99 });
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Фильтр №99 не найден. Номера фильтров — в дереве, раздел «Фильтры».");
    expect(style(1).filter_id).toBeNull();
    expect((await run("blocks.filter", { model_id: 7, filter_id: ORE.id })).result).toMatch(/Блочная модель №7 не найдена/);
  });
});

describe("blocks.quick", () => {
  it("«поле — оператор — значение»: one condition, add, join, invert, clear; the text of the restrictor is in the answer", async () => {
    expect((await run("blocks.quick", { model_id: 1, field: "AU", op: ">", value: "0,5" })).result).toBe("Быстрый ограничитель модели «Ресурсная»: AU > 0.5");
    expect(style(1).quick).toEqual({ items: [{ field: "AU", op: ">", value: 0.5 }], join: "and", invert: false });
    await run("blocks.quick", { model_id: 1, field: "ROCK", op: "=", value: "руда", append: true });
    await run("blocks.quick", { model_id: 1, join: "or" });
    expect(style(1).quick).toEqual({ items: [{ field: "AU", op: ">", value: 0.5 }, { field: "ROCK", op: "=", value: "руда" }], join: "or", invert: false });
    expect((await run("blocks.quick", { model_id: 1, invert: true })).result).toBe("Быстрый ограничитель модели «Ресурсная»: НЕ (AU > 0.5 ИЛИ ROCK = руда)");
    await run("blocks.quick", { model_id: 1, field: "ROCK", op: "in", value: "руда; порода" });
    expect(style(1).quick.items).toEqual([{ field: "ROCK", op: "in", values: ["руда", "порода"] }]);
    await run("blocks.quick", { model_id: 1, field: "AU", op: "notempty" });
    expect(style(1).quick.items).toEqual([{ field: "AU", op: "notempty" }]);
    expect((await run("blocks.quick", { model_id: 1, clear: true })).result).toBe("Быстрый ограничитель модели «Ресурсная» снят");
    expect(style(1).quick).toEqual({ items: [], join: "and", invert: false });
    expect(quickText({ items: [], join: "and", invert: false })).toBe("нет");
  });

  it("a wrong condition is refused in Russian before it is written: no field, no such field, no value, a word where a number goes", async () => {
    const before = writes().length;
    expect((await run("blocks.quick", { model_id: 1, op: ">", value: "1" })).result).toBe("У условия нет поля. Укажите поле модели, например AU.");
    expect((await run("blocks.quick", { model_id: 1, field: "ZZ", op: ">", value: "1" })).result).toBe("Поля «ZZ» нет в модели «Ресурсная». Поля модели: AU, OK, ROCK.");
    expect((await run("blocks.quick", { model_id: 1, field: "AU", op: ">" })).result).toBe("У условия нет значения. Введите число или подпись категории, например 0,5.");
    expect((await run("blocks.quick", { model_id: 1, field: "AU", op: ">", value: "много" })).result).toMatch(/число/);
    expect(style(1).quick.items).toEqual([]);
    expect(writes().length).toBe(before);
  });
});

describe("blocks.style", () => {
  it("a field without a kind gets the kind of the field: a scale for a number, the codes for a category; no field — one colour", async () => {
    expect((await run("blocks.style", { model_id: 1, field: "AU" })).result).toBe("Блочная модель «Ресурсная»: по полю AU (шкала), оболочка");
    expect(style(1).colour).toMatchObject({ field: "AU", kind: "scale" });
    await run("blocks.style", { model_id: 1, field: "ROCK" });
    expect(style(1).colour).toMatchObject({ field: "ROCK", kind: "categories" });
    expect((await run("blocks.style", { model_id: 1, field: null })).result).toBe("Блочная модель «Ресурсная»: одним цветом, оболочка");
    expect(style(1).colour.kind).toBe("none");
  });

  it("the scale, the breaks, the hidden categories, the mode, the edges, the opacity, the limit", async () => {
    await run("blocks.style", { model_id: 1, field: "AU", from: 0.1, to: 10, log: true });
    expect(style(1).colour).toMatchObject({ from: 0.1, to: 10, log: true });
    await run("blocks.style", { model_id: 1, from: null, to: null, log: false });
    expect(style(1).colour).toMatchObject({ from: null, to: null, log: false }); // the core takes min / max of the field
    await run("blocks.style", { model_id: 1, kind: "classes", breaks: "0,5; 1; 2" });
    expect(style(1).colour).toMatchObject({ kind: "classes", breaks: [0.5, 1, 2] });
    await run("blocks.style", { model_id: 1, field: "ROCK", hidden: "1; 2" });
    expect(Object.fromEntries(Object.entries(style(1).colour.categories).map(([k, v]) => [k, v.on]))).toEqual({ "1": false, "2": false });
    expect((await run("blocks.style", { model_id: 1, hidden: "" })).ok).toBe(true);
    expect(Object.values(style(1).colour.categories).every((v) => v.on)).toBe(true);
    const r = await run("blocks.style", { model_id: 1, mode: "all", opacity: 0.6, edges: true, edge_color: "#1B222B", limit: 800_000 });
    expect(r.ok).toBe(true);
    expect(style(1)).toMatchObject({ mode: "all", opacity: 0.6, limit: 800_000, edges: { on: true, colour: "#1b222b" } });
  });

  it("refuses what the core would: a field that is not there, breaks out of order, a range upside down, a log scale from zero, the limit, the opacity", async () => {
    const before = writes().length;
    const says = async (args: Record<string, unknown>) => (await run("blocks.style", { model_id: 1, ...args })).result;
    expect(await says({ field: "ZZ" })).toBe("Поля «ZZ» нет в модели «Ресурсная». Поля модели: AU, OK, ROCK.");
    expect(await says({ field: "AU", kind: "classes", breaks: "2; 1" })).toMatch(/по возрастанию/);
    expect(await says({ field: "AU", kind: "classes" })).toMatch(/Границы градаций/);
    expect(await says({ field: "AU", breaks: "a; b" })).toBe("Границы градаций: нужны числа через «;». Например 0,5; 1; 2.");
    expect(await says({ field: "AU", from: 5, to: 1 })).toMatch(/«до» должна быть больше/);
    expect(await says({ field: "AU", from: 0, to: 1, log: true })).toMatch(/больше нуля/);
    expect(await says({ limit: 100 })).toBe("Лимит граней: от 500 000 до 10 000 000. Задайте число граней в этих пределах.");
    expect(await says({ opacity: 3 })).toMatch(/от 0,05 до 1/);
    expect(await says({ edge_color: "красный" })).toMatch(/#RRGGBB/);
    expect(await says({ hidden: "x" })).toMatch(/коды/);
    expect(await says({})).toBe("Раскраска модели «Ресурсная» не изменена: ничего не указано");
    expect(writes().length).toBe(before);
    expect(style(1).colour.field).toBeNull();
  });
});

describe("blocks.pick", () => {
  it("the block at a point as a click gives it, with the filter and the restrictor of the model; the panel keeps it", async () => {
    await run("blocks.filter", { model_id: 2, filter_id: ORE.id });
    await run("blocks.quick", { model_id: 2, field: "AU", op: ">", value: "1" });
    const r = await run("blocks.pick", { model_id: 2, x: 1035, y: 2025, z: 112, fields: "AU; ROCK" });
    expect(r.ok).toBe(true);
    expect(r.result).toBe("Модель «Карьер»: блок (3, 2, 2), центр 1035, 2025, 112,5, размер 10 × 10 × 5 м; AU 1,4376 г/т; ROCK дайка.");
    const at = sent.find((s) => s.path === "/viz/blocks/2/at")!;
    expect(at.body).toEqual({ point: [1035, 2025, 112], filter_id: ORE.id, restrict: { cond: { field: "AU", op: ">", value: 1 } }, fields: ["AU", "ROCK"] });
    expect(pickedBlockStore.get().picked).toMatchObject({ model: 2, status: "found", point: [1035, 2025, 112] });
  });

  it("no block, a hidden one, a failure of the core in Russian; the point must be three numbers", async () => {
    atAnswer = () => json({ found: false });
    expect((await run("blocks.pick", { model_id: 2, x: 0, y: 0, z: 0 })).result).toBe("Модель «Карьер»: в этой точке блока нет.");
    atAnswer = () => json({ ...(AT as object), passed: false });
    expect((await run("blocks.pick", { model_id: 2, x: 1, y: 1, z: 1 })).result).toContain("(скрыт фильтром)");
    atAnswer = () => json({ code: "bad_param", message: "Точка: три числа.", hint: "Проверьте координаты.", param: "point" }, 422);
    const bad = await run("blocks.pick", { model_id: 2, x: 1, y: 1, z: 1 });
    expect(bad).toEqual({ ok: false, result: "Точка: три числа. Проверьте координаты." });
    expect((await run("blocks.pick", { model_id: 2, x: 1, y: 1 })).result).toBe("Не задан параметр «z»");
    expect(blocksShowStore.get().models).toEqual({});
  });
});
