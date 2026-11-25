// The window «Фильтры» and the commands `filters.manage`, `filter.save`, `filter.delete`, `filter.test`, `filter.usage`
// (ТЗ 7.35 Д-02; ADR 0023 §8, §13; Э17 17.33) on the REAL answers of the core
// (`core/tests/data/blocks/filters_window_answers.json`, made by `core/tests/test_filters_window_answers.py` through the
// routes of `/filters`): «Где используется», the delete of a used filter (422 with the list), «Проверить на модели»
// (422 `not_ready` is told as a notice), the version conflict (409), the form that is not finished never reaches the
// core, the refusal of the core marks its node, the window and the tree section in Russian. The core is a fetch stub
// answering with those answers (the rows it makes itself — only where the core's answer depends on the request).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { blocksStore } from "../src/modules/blocks/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { readRow, readTest, readUsage, testRefusal, testText, usageOfError, usageText, useText } from "../src/modules/blocks/filters/api";
import { bindFilters, bodyOfArgs, copyWindow, lastSaved, lastTestNote, openFiltersWindow, refreshFilters, renderFiltersWindow, saveWindow, testWindow, windowOfFilters } from "../src/modules/blocks/filters/bind";
import { commands, FILTER_BUTTONS } from "../src/modules/blocks/filters/commands";
import { newGroup, type Draft } from "../src/modules/blocks/filters/constructor";
import { parseExpr, type ExprIssue } from "../src/modules/blocks/filters/expr";
import { FiltersWindow } from "../src/modules/blocks/filters/FiltersWindow";
import { filterMenu, FiltersTree } from "../src/modules/blocks/filters/FiltersTree";
import { currentEdit, FILTERS_WINDOW, filtersStore, resetFilters, setEdit, switchMode, type FilterEdit } from "../src/modules/blocks/filters/state";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/filters_window_answers.json"), "utf-8"));
const FULL: Record<string, Record<string, unknown>> = Object.fromEntries(Object.entries(A.full as Record<string, { body: Record<string, unknown> }>).map(([k, v]) => [k, v.body]));

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });


let app: App;
let rows: Record<string, unknown>[];
let usage: unknown;
let stored: Record<number, Record<string, unknown>>;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
const FL = "/filters";
const row = (f: Record<string, unknown>) => Object.fromEntries(Object.entries(f).filter(([k]) => k !== "rules" && k !== "wireframes"));
const TREE_ID = A.ids.tree as number;

beforeEach(() => {
  sent.length = 0;
  windowStore.set({ windows: [] });
  resetFilters();
  stored = Object.fromEntries(Object.values(FULL).map((f) => [f.id as number, structuredClone(f)]));
  rows = (A.list.body.filters as Record<string, unknown>[]).map((r) => structuredClone(r));
  usage = A.usage.body;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path === FL && method === "GET") return json({ filters: rows });
      if (path === FL && method === "POST") {
        const name = String(body.name);
        if (rows.some((r) => String(r.name).toLowerCase() === name.toLowerCase())) return json(A.refused.name_taken.body, 409);
        const id = 40 + rows.length;
        const f = { ...FULL.tree, id, name, version: 1, rules: body.rules ?? { expr: body.expr }, text: body.expr ?? null, note: body.note ?? null };
        stored[id] = f;
        rows.push(row(f));
        return json(f);
      }
      if (path === `${FL}/test` && method === "POST") return json(A.tests.inline.body, A.tests.inline.status);
      const m = /^\/filters\/(\d+)(\/usage|\/test)?$/.exec(path);
      const f = m ? stored[Number(m[1])] : undefined;
      if (m && !f) return json({ code: "filter_not_found", message: "Фильтр не найден.", hint: "Обновите список фильтров." }, 404);
      if (m?.[2] === "/usage") return json(Number(m[1]) === TREE_ID ? usage : A.usage_free.body);
      if (m?.[2] === "/test") return json(A.tests.stored.body, A.tests.stored.status);
      if (m && method === "GET") return json(f);
      if (m && method === "PUT") {
        if (body.expected_version !== f!.version) return json({ ...A.conflict.body, version: f!.version }, 409);
        const { expected_version: _v, ...fields } = body;
        Object.assign(f!, fields, { version: Number(f!.version) + 1 });
        return json(f);
      }
      if (m && method === "DELETE") {
        if (Number(m[1]) === TREE_ID) return json(A.in_use.body, 422);
        delete stored[Number(m[1])];
        rows = rows.filter((r) => r.id !== Number(m[1]));
        return json({ deleted: Number(m[1]) });
      }
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false });
  if (!app.registry.get("filters.manage")) app.registry.register(commands); // until shell/catalog.ts names modules/blocks/filters
  bindFilters(app);
  blocksStore.set({ list: A.blocks.body.block_models, loaded: true, error: null, details: {}, detailErrors: {} });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: A.wireframes.body.wireframes, loaded: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const writes = () => sent.filter((r) => r.path.startsWith(FL) && r.method !== "GET");

async function answer(ok: boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind === "confirm") d.resolve(ok);
  if (d.kind === "message") d.resolve();
  return d.kind === "text" ? "" : d.options.message;
}

describe("registry", () => {
  it("has the commands of ADR 0023 §13 (and «Где используется»): confirm — save and delete; every one bound; the window is uiOnly", () => {
    expect(commands.map((c) => c.id)).toEqual(["filters.manage", "filter.save", "filter.delete", "filter.test", "filter.usage"]);
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(["filter.save", "filter.delete"]);
    expect(commands.filter((c) => c.uiOnly).map((c) => c.id)).toEqual(["filters.manage"]);
    for (const c of commands) {
      const r = app.registry.get(c.id)!;
      expect(r.run, c.id).toBeTypeOf("function");
      expect(r.stage).toBeUndefined();
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description.length).toBeGreaterThan(40);
    }
    for (const b of FILTER_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    expect(filterMenu({ id: 3 }, () => null, () => undefined).map((i) => i.label)).toEqual(["Изменить", "Где используется", "Удалить…"]);
  });

  it("is unavailable without a project; writing — in a read-only project", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("filters.manage", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect((await app.registry.execute("filter.usage", { filter_id: TREE_ID })).ok).toBe(true);
    expect((await app.registry.execute("filter.save", { name: "Х", expr: "AU > 1" })).result).toMatch(/только для чтения/);
    expect((await app.registry.execute("filter.delete", { filter_id: TREE_ID })).result).toMatch(/только для чтения/);
    expect(writes()).toEqual([]);
  });
});

describe("filter.save", () => {
  it("without a name and rules — the window «Фильтры» with a new filter; nothing is sent", async () => {
    const r = await app.registry.execute("filter.save", {});
    expect(r).toEqual({ ok: true, result: "Открыто окно «Фильтры»: новый фильтр" });
    const [w] = windowStore.get().windows;
    expect(w.kind).toBe(FILTERS_WINDOW);
    expect(w.title).toBe("Фильтры · Проект");
    expect(currentEdit()).toMatchObject({ filterId: null, name: "Новый фильтр", mode: "builder", dirty: false });
    expect(writes()).toEqual([]);
    expect(renderFiltersWindow(w)).not.toBeNull();
    expect(renderFiltersWindow({ ...w, kind: "swath" })).toBeNull();
  });

  it("a text — `POST /filters {expr}` (the assistant is asked first); the window binds to the filter made", async () => {
    const p = app.registry.execute("filter.save", { name: "Борт", expr: "AU > 1 and DOMAIN = 2" }, "claude");
    await answer(true);
    const r = await p;
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/^Фильтр «Борт» сохранён \(№\d+\)/);
    expect(writes()).toEqual([{ method: "POST", path: FL, body: { name: "Борт", expr: "AU > 1 and DOMAIN = 2" } }]);
    expect(lastSaved()?.name).toBe("Борт");
  });

  it("a tree as JSON — `POST /filters {rules}`; both a text and a tree, or a wrong text — never reach the core", async () => {
    const rules = { op: "and", items: [{ cond: { field: "AU", op: ">", value: 1 } }] };
    expect((await app.registry.execute("filter.save", { name: "Дерево", rules: JSON.stringify(rules) })).ok).toBe(true);
    expect(writes()).toEqual([{ method: "POST", path: FL, body: { name: "Дерево", rules } }]);
    for (const a of [{ name: "Х", expr: "AU > 1", rules: JSON.stringify(rules) }, { name: "Х", expr: "AU > (1" }, { name: "Х", rules: "{не json" }, { rules: JSON.stringify(rules) }, { name: "Х" }]) {
      const r = await app.registry.execute("filter.save", a);
      expect(r.ok, JSON.stringify(a)).toBe(false);
      expect(r.result).toMatch(/[А-Яа-яЁё]/);
    }
    expect(writes()).toHaveLength(1);
    expect((await app.registry.execute("filter.save", { name: "Х", expr: "AU > (1" })).result).toMatch(/Не закрыта скобка \(строка 1, столбец 6/);
    expect(bodyOfArgs({ name: "Х", expr: "AU >" })).toMatchObject({ ok: false });
  });

  it("a name in use — 409 `name_taken` in Russian; an unknown filter — 404 in Russian", async () => {
    const r = await app.registry.execute("filter.save", { name: "руда 1 г/т ", expr: "AU > 1" });
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/[А-Яа-яЁё]/);
    expect((await app.registry.execute("filter.save", { filter_id: 99, expr: "AU > 1" })).result).toMatch(/Фильтр №99 не найден/);
  });

  it("a used filter: the person is asked (our window, the places named); «Отмена» sends nothing; then `PUT` over the version read", async () => {
    const p = app.registry.execute("filter.save", { filter_id: TREE_ID, expr: "AU > 2" });
    expect(await answer(false)).toMatch(/используется: «Поле AU2», «Вид проекта · Модель Б»/);
    expect((await p).result).toBe("Отменено");
    expect(writes()).toEqual([]);
    const q = app.registry.execute("filter.save", { filter_id: TREE_ID, expr: "AU > 2" });
    await answer(true);
    expect((await q).ok).toBe(true);
    expect(writes()).toEqual([{ method: "PUT", path: `${FL}/${TREE_ID}`, body: { expr: "AU > 2", expected_version: 2 } }]);
  });

  it("another version stored (409): «Сохранить как новый» — a new filter with the next name; «Перечитать» — the stored one", async () => {
    const free = A.ids.list as number;
    const a = { filter_id: free, expr: "Порода > 1", expected_version: 7 };
    const p = app.registry.execute("filter.save", a);
    expect(await answer(true)).toMatch(/изменили в другом окне/);
    const r = await p;
    expect(r.result).toMatch(/^Фильтр «Список \(2\)» сохранён как новый/);
    expect(writes().map((w) => w.method)).toEqual(["PUT", "POST"]);
    expect(writes()[1].body).toMatchObject({ name: "Список (2)", expr: "Порода > 1" });
    const q = app.registry.execute("filter.save", { ...a, expr: "Порода > 2" });
    await answer(false);
    expect(await answer(true)).toMatch(/Правки этого окна будут заменены/);
    expect((await q).result).toBe("Фильтр «Список» перечитан из проекта");
    // the assistant gets the refusal as it is
    const c = app.registry.execute("filter.save", { ...a, expr: "Порода > 3" }, "claude");
    await answer(true); // the confirmation of the assistant
    const out = await c;
    expect(out.ok).toBe(false);
    expect(out.result).toMatch(/Фильтр изменён в другом окне/);
  });
});

describe("filter.delete and «Где используется»", () => {
  it("«Где используется»: the users of the real answer — an object and a view with their roles", async () => {
    const list = readUsage(A.usage.body);
    expect(list.map((u) => [u.kind, u.name, u.role])).toEqual([
      ["object", "Поле AU2", "filter"],
      ["view", "Вид проекта · Модель Б", "block_style"],
    ]);
    expect(list.map(useText)).toEqual(["Поле AU2 (объект, применяет фильтр)", "Вид проекта · Модель Б (вид, показ модели в виде проекта)"]);
    expect(usageText(list)).toBe("«Поле AU2», «Вид проекта · Модель Б»");
    expect(usageText([...list, ...list, ...list], 2)).toBe("«Поле AU2», «Вид проекта · Модель Б» и ещё 4");
    expect(readUsage(A.usage_free.body)).toEqual([]);
    const r = await app.registry.execute("filter.usage", { filter_id: TREE_ID });
    expect(r.result).toMatch(/^Фильтр «Руда 1 г\/т» используется \(2\): Поле AU2 \(объект, применяет фильтр\); Вид проекта/);
    expect(await answer(true)).toMatch(/Поле AU2 \(объект, применяет фильтр\)\nВид проекта · Модель Б \(вид/);
    const free = await app.registry.execute("filter.usage", { filter_id: A.ids.expr });
    expect(free.result).toBe("Фильтр «Выражение» нигде не используется — его можно удалить");
    await answer(true);
  });

  it("a used filter is not deleted: the list in our own window, nothing is sent; the 422 of the core says the same", async () => {
    const p = app.registry.execute("filter.delete", { filter_id: TREE_ID });
    expect(await answer(true)).toMatch(/не удалён — он выбран здесь:\nПоле AU2 \(объект, применяет фильтр\)\nВид проекта · Модель Б/);
    expect((await p).result).toBe("Фильтр «Руда 1 г/т» используется: «Поле AU2», «Вид проекта · Модель Б» — не удалён");
    expect(writes()).toEqual([]);
    // the list was empty when read, but the core knows the users (a race): 422 `filter_in_use`
    usage = { usage: [] };
    const q = app.registry.execute("filter.delete", { filter_id: TREE_ID });
    await answer(true); // «Удалить фильтр?»
    expect(await answer(true)).toMatch(/Поле AU2/); // the list of the 422 in our window
    const r = await q;
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Фильтр «Руда 1 г\/т» используется: «Поле AU2», «Вид проекта · Модель Б» — не удалён\. Выберите другой фильтр/);
    expect(writes()).toEqual([{ method: "DELETE", path: `${FL}/${TREE_ID}`, body: {} }]);
    expect(usageOfError(new CoreError(422, "other", "x", ""))).toEqual([]);
  });

  it("a free filter: a question, then `DELETE`; the row, the selection and the form go", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.expr });
    expect(currentEdit()?.filterId).toBe(A.ids.expr);
    const p = app.registry.execute("filter.delete", { filter_id: A.ids.expr });
    expect(await answer(true)).toBe("Удалить фильтр «Выражение»?");
    expect((await p).result).toBe("Фильтр «Выражение» удалён");
    expect(writes()).toEqual([{ method: "DELETE", path: `${FL}/${A.ids.expr}`, body: {} }]);
    expect(currentEdit()?.filterId).toBeNull();
    expect(filtersStore.get().rows.some((r) => r.id === A.ids.expr)).toBe(false);
    const gone = app.registry.execute("filter.delete", { filter_id: A.ids.list }, "user");
    await answer(false);
    expect((await gone).result).toBe("Отменено");
  });
});

describe("«Проверить на модели»", () => {
  it("an old core: 422 `not_ready` is told as a notice with its hint", () => {
    const e = new CoreError(422, "not_ready", "Проверка фильтра на модели пока недоступна.", "Обновите программу.");
    expect(testRefusal(e)).toEqual({ text: "Проверка фильтра на модели пока недоступна. Обновите программу.", notReady: true });
    expect(testRefusal(new CoreError(404, "block_model_not_found", "Блочная модель не найдена.", "Обновите дерево проекта."))).toEqual({ text: "Блочная модель не найдена. Обновите дерево проекта.", notReady: false });
  });

  it("the real answer of the restrictor {passed, total, weight, warnings}: the command and the window", async () => {
    const r = await app.registry.execute("filter.test", { filter_id: TREE_ID, model_id: A.model_id });
    expect(r.ok).toBe(true);
    expect(sent.filter((s) => s.path.endsWith("/test"))).toEqual([{ method: "POST", path: `${FL}/${TREE_ID}/test`, body: { model_id: A.model_id } }]);
    const note = lastTestNote();
    expect(note?.kind).toBe("ok");
    expect(readTest(A.tests.stored.body)).toEqual({ passed: A.tests.stored.body.passed, total: A.tests.stored.body.total, weight: A.tests.stored.body.weight });
    // the window: a changed form goes as it is (`/filters/test`)
    await app.registry.execute("filters.manage", { filter_id: TREE_ID });
    setEdit({ modelId: A.model_id, dirty: true });
    const inline = await testWindow(app);
    expect(inline.kind).toBe("ok");
    expect(sent.filter((s) => s.path === `${FL}/test`)).toEqual([{ method: "POST", path: `${FL}/test`, body: { model_id: A.model_id, rules: { expr: "AU > 1 and DOMAIN = 2" } } }]);
  });

  it("without a model, or with a form not finished, nothing is asked of the core", async () => {
    await app.registry.execute("filter.save", {});
    expect(await testWindow(app)).toEqual({ kind: "error", text: "Выберите блочную модель для проверки." });
    setEdit({ modelId: A.model_id });
    expect((await testWindow(app)).text).toMatch(/нет значения|Имя поля/);
    expect(sent.filter((s) => s.path.includes("/test"))).toEqual([]);
  });

  it("the result of the contract {passed, total, weight}: the words of the window", () => {
    expect(readTest({ passed: 6, total: 12, weight: 6 })).toEqual({ passed: 6, total: 12, weight: 6 });
    expect(readTest({ passed: 6 })).toBeNull();
    expect(testText("Модель Б", { passed: 6, total: 12, weight: 6 })).toBe("Модель «Модель Б»: прошло 6 из 12 блоков");
    expect(testText("Модель Б", { passed: 6, total: 12000, weight: 6.5 })).toBe("Модель «Модель Б»: прошло 6 из 12 000 блоков, вес по долям 6,5");
  });
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));

describe("the window «Фильтры»", () => {
  it("shows the library and the filter in Russian: the form, the fields it reads, «Где используется», «Проверить на модели»", async () => {
    await refreshFilters();
    await app.registry.execute("filters.manage", { filter_id: A.ids.misc });
    const w = windowStore.get().windows.find((x) => x.kind === FILTERS_WINDOW)!;
    const html = render(createElement(FiltersWindow, { winId: w.id }));
    const version = (FULL.misc as { version: number }).version;
    for (const text of ["Новый", "Копировать", "Имя", "Примечание", "Конструктор", "Текст", "+ Условие", "+ Группа", "+ Выражение", "+ Каркас", "+ Поверхность", "Проверить на модели", "Где используется", "Удалить…", "Сохранить", "Закрыть", `Фильтр №${A.ids.misc}, версия ${version}`, "Читает поля: Au ppm, CU"]) expect(html, text).toContain(text);
    expect(html).toContain('value="Разное"');
    expect(html).toContain("не пусто");
    for (const f of rows) expect(html).toContain(String(f.name));
    expect(html).toContain("Модель Б");
    expect(windowOfFilters()).toBe(w.id);
  });

  it("a filter with a wireframe: the leaves with their objects, no text mode for it; a lost object is told", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.wire });
    const e = currentEdit()!;
    expect(e.draft).toMatchObject({ op: "or" });
    expect(switchMode(e, "text")).toMatchObject({ refusal: expect.stringContaining("нет записи в 8.7.4") });
    const w = windowStore.get().windows[0];
    const html = render(createElement(FiltersWindow, { winId: w.id }));
    for (const text of ["внутри каркаса", "снаружи каркаса", "выше поверхности", "Рудное тело 1", "Рельеф", "по центру", "по доле"]) expect(html, text).toContain(text);
    setEdit({ lost: ["Каркас 7"] });
    expect(render(createElement(FiltersWindow, { winId: w.id }))).toContain("Объекты фильтра больше не в проекте: Каркас 7");
  });

  it("the text mode: the text of the core, the tree back in the constructor without loss; a wrong text — its place", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.list });
    const e0 = currentEdit()!;
    const toText = switchMode(e0, "text");
    expect("refusal" in toText).toBe(false);
    setEdit(toText as Partial<FilterEdit>);
    expect(currentEdit()!.text).toBe(FULL.list.text);
    const back = switchMode(currentEdit()!, "builder");
    expect((back as { draft: Draft }).draft).toEqual(e0.draft);
    setEdit({ text: "AU > (1" });
    expect(switchMode(currentEdit()!, "builder")).toMatchObject({ refusal: expect.stringContaining("Не закрыта скобка") });
    const w = windowStore.get().windows[0];
    setEdit({ mode: "text", text: "AU > (1", textIssue: (parseExpr("AU > (1") as { ok: false; issue: ExprIssue }).issue });
    const html = render(createElement(FiltersWindow, { winId: w.id }));
    expect(html).toContain("Не закрыта скобка (строка 1, столбец 6");
    expect(html).toContain("Добавьте закрывающую скобку");
  });

  it("«Сохранить»: a form not finished never reaches the core and its node is marked; the core's refusal marks the node it names", async () => {
    await app.registry.execute("filter.save", {});
    setEdit({ name: "Х", draft: { op: "and", items: [{ cond: { field: "AU", op: ">", value: "много", list: "" } }] }, dirty: true });
    expect(await saveWindow(app)).toMatch(/Значение для сравнения по порядку — число/);
    expect(currentEdit()?.issue).toMatchObject({ param: "rules.items.0.cond.value" });
    const w = windowStore.get().windows[0];
    expect(render(createElement(FiltersWindow, { winId: w.id }))).toContain("Значение для сравнения по порядку — число.");
    expect(writes()).toEqual([]);
    setEdit({ name: "  ", draft: newGroup() });
    expect(await saveWindow(app)).toMatch(/У фильтра нет имени/);
    // the core refuses (a text the form let in): the node of the answer is marked
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(A.refused.expr_in_tree.body, 422)),
    );
    setEdit({ name: "Новый", draft: { op: "and", items: [{ expr: "AU > 1" }] }, issue: null });
    const out = await saveWindow(app);
    expect(out).toMatch(/Выражение оборвано/);
    expect(currentEdit()?.issue).toMatchObject({ param: "rules.items.1.expr", expr: { reason: "syntax" } });
  });

  it("«Сохранить» of a stored filter: `PUT` with its version, the tree as the rules; the window takes what is stored", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.expr });
    setEdit({ draft: { op: "and", items: [{ cond: { field: "AU", op: ">", value: "1,5", list: "" } }] }, note: "борт", dirty: true });
    expect(await saveWindow(app)).toBeNull();
    expect(writes()).toEqual([{ method: "PUT", path: `${FL}/${A.ids.expr}`, body: { name: "Выражение", rules: { op: "and", items: [{ cond: { field: "AU", op: ">", value: 1.5 } }] }, note: "борт", expected_version: 1 } }]);
    expect(currentEdit()).toMatchObject({ filterId: A.ids.expr, version: 2, dirty: false });
  });

  it("«Копировать»: the same rules as a new filter with the next free name", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.expr });
    expect(await copyWindow(app)).toBeNull();
    expect(writes()).toHaveLength(1);
    expect(writes()[0]).toMatchObject({ method: "POST", body: { name: "Выражение (2)", rules: { expr: "if(au > 1, 1, 0) = 1 and domain in (1, 2)" } } });
  });

  it("a window with changes asks before another filter is put on edit; the list follows the core's rows", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.list });
    setEdit({ name: "Другое имя", dirty: true });
    const open = openFiltersWindow(app);
    expect(windowStore.get().windows).toHaveLength(1);
    expect(open).toBe(windowOfFilters());
    expect(currentEdit()?.name).toBe("Другое имя");
    await refreshFilters();
    expect(filtersStore.get().rows.map((r) => r.name).sort()).toEqual((A.list.body.filters as { name: string }[]).map((f) => f.name).sort());
    expect(readRow(A.list.body.filters[0])).toMatchObject({ id: expect.any(Number), version: 1, fields: expect.any(Array) });
  });

  it("unsaved changes: filters.manage and filter.save {} ask in our own window; «Нет» keeps the form, «Да» replaces it", async () => {
    await app.registry.execute("filters.manage", { filter_id: A.ids.list });
    setEdit({ name: "Другое имя", dirty: true });
    const no = app.registry.execute("filters.manage", { filter_id: A.ids.expr });
    expect(await answer(false)).toContain("несохранённые изменения");
    expect((await no).result).toBe("Отменено");
    expect(currentEdit()).toMatchObject({ name: "Другое имя", dirty: true });
    const noNew = app.registry.execute("filter.save", {});
    await answer(false);
    expect((await noNew).result).toBe("Отменено");
    expect(currentEdit()?.name).toBe("Другое имя");
    const yes = app.registry.execute("filters.manage", { filter_id: A.ids.expr });
    await answer(true);
    expect((await yes).ok).toBe(true);
    expect(currentEdit()).toMatchObject({ filterId: A.ids.expr, dirty: false });
  });

  it("the tree section: the filters by name, the tooltip tells the filter in words", async () => {
    await refreshFilters();
    const html = render(createElement(FiltersTree));
    for (const f of rows) expect(html).toContain(String(f.name));
    expect(html).toContain("tree-filter-" + TREE_ID);
    resetFilters();
    filtersStore.set({ loaded: true });
    expect(render(createElement(FiltersTree))).toContain("Нет фильтров — «+» создаёт новый");
  });
});
