// Trends (ТЗ 7.35 Д-15; ADR 0022 §10, §13, решения 20, 22; Э16 16.20): the commands `trend.*` of the registry —
// `confirm` for `trend.save` and `trend.delete`, a command without angles and a model opens the window «Тренд», with
// them the request of docs/progress/e16-api.md goes (checked first); a used trend is changed after a question and is
// not deleted (its consumers in our own window); `trend.show` writes the view key `ellipsoids`; the window saves
// through the registry and binds itself to the trend made; «Сохранить как тренд» of a variogram window. The core is
// a fetch stub answering with the real answers of the core of 16.16 (TestClient run, 2026-11-22).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { SaveAsTrendButton, TrendWindow } from "../src/modules/trends/TrendWindow";
import { TrendsTree } from "../src/modules/trends/TrendsTree";
import { setWork, variographyStore } from "../src/modules/variography/state";
import { forgetTrend, lastSaved, openTrendWindow, renderTrendWindow, saveAsTrend, saveWindow, shapeText, trendTitle } from "../src/modules/trends/bind";
import { commands, TREND_BUTTONS } from "../src/modules/trends/commands";
import { trendMenu } from "../src/modules/trends/TrendsTree";
import { readTrend } from "../src/modules/trends/api";
import { editOf, ELLIPSOIDS_KEY, findTrend, setEdit, trendArgsOfWork, trendsStore, TREND_WINDOW } from "../src/modules/trends/state";
import { newWork } from "../src/modules/variography/state";
import { viewStore } from "../src/modules/scene/bind";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { answerClaude } from "./claude-confirm";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
// the real answers of the core (16.16)
const GOLD = { id: 4, name: "Тренд · Золото", kind: "global", ang1: 112.5, ang2: -35.0, ang3: -20.0, ratios: [1.0, 0.36, 0.05], size: 250.0, centre: null, color: "#f46a1a", origin: { type: "model", model_id: 3, structure: 2 }, used: false, matrix: [[0.7567978077333783, -0.31347591593739127, -0.5735764363510465], [0.4954528111443995, 2.620098846694185, -0.7782402766478759], [12.576863108994473, 2.1944914377164477, 15.39502262640114]], version: 1, created_at: "2026-11-22T00:26:28.175081Z", updated_at: "2026-11-22T00:26:28.175081Z" }; // prettier-ignore
const MANUAL = { id: 5, name: "Вручную", kind: "global", ang1: 17.0, ang2: -3.0, ang3: 0.0, ratios: [1.0, 0.3, 0.07], size: 100.0, centre: [5000123.25, 7200456.5, 312.125], color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: [[0.29197101946266824, 0.9549941735309524, -0.05233595624294437], [-3.187682519876785, 0.9745723490757893, 0.0], [0.2185936106434886, 0.7149874837571574, 14.266136210779624]], version: 1, created_at: "2026-11-22T00:26:28.180727Z", updated_at: "2026-11-22T00:26:28.180727Z" }; // prettier-ignore
const USAGE = [{ id: 7, kind: "estimation", name: "Оценка AU", role: "trend" }];
const IN_USE = { usage: USAGE, code: "in_use", message: "Тренд используется: «Оценка AU».", hint: "Выберите другой тренд в этих объектах или удалите их, затем удалите тренд." };
const NOT_FOUND = { code: "trend_not_found", message: "Тренд не найден.", hint: "Обновите список трендов." };

let app: App;
let rows: Record<string, unknown>[];
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
const T = "/geostat/trends";

beforeEach(() => {
  sent.length = 0;
  rows = [structuredClone(GOLD), structuredClone(MANUAL)];
  windowStore.set({ windows: [] });
  trendsStore.set({ trends: [], loaded: false, selected: null, edits: {}, dragging: null });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1 });
      if (path === T && method === "GET") return json({ trends: rows });
      if (path === T && method === "POST") {
        const id = 8 + rows.length;
        const name = typeof body.name === "string" ? body.name : body.model_id ? "Тренд · Золото (2)" : "Тренд";
        rows.push({ ...MANUAL, id, name, ang1: body.ang1 ?? 0, centre: body.centre ?? null, origin: body.model_id ? { type: "model", model_id: body.model_id, structure: body.structure ?? 2 } : (body.origin ?? { type: "manual" }) });
        return json({ id, name });
      }
      const m = /^\/geostat\/trends\/(\d+)(\/usage)?$/.exec(path);
      const row = m ? rows.find((r) => r.id === Number(m[1])) : undefined;
      if (m && !row) return json(NOT_FOUND, 404);
      if (m && m[2]) return json({ usage: row!.used ? USAGE : [] });
      if (m && method === "GET") return json(row);
      if (m && method === "PUT") {
        if (body.expected_version !== row!.version) return json({ code: "version_conflict", message: "Тренд изменён в другом окне.", hint: "Перечитайте тренд или сохраните свой как новый.", version: row!.version }, 409);
        const { expected_version: _v, ...fields } = body;
        Object.assign(row!, fields, body.ratios ? { ratios: body.ratios } : {}, { version: Number(row!.version) + 1 });
        return json({ id: row!.id, name: row!.name, version: row!.version });
      }
      if (m && method === "DELETE") {
        if (row!.used) return json(IN_USE, 409);
        rows = rows.filter((r) => r !== row);
        return json({ deleted: row!.id });
      }
      if (path === "/geostat/variography/models") return json({ models: [] });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const trends = () => sent.filter((r) => r.path.startsWith(T));
const writes = () => trends().filter((r) => r.method !== "GET");

async function answer(ok: boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind === "confirm") d.resolve(ok);
  if (d.kind === "message") d.resolve();
  return d.kind === "text" ? "" : d.options.message;
}

describe("registry", () => {
  it("has `trend.*` of ADR 0022 §13 (and the window, «Где используется»): confirm — save and delete; every one bound", () => {
    expect(commands.map((c) => c.id)).toEqual(["trend.open", "trend.save", "trend.delete", "trend.show", "trend.usage"]);
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(["trend.save", "trend.delete"]);
    for (const c of commands) {
      const r = app.registry.get(c.id)!;
      expect(r.run, c.id).toBeTypeOf("function");
      expect(c.stage).toBeUndefined();
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description.length).toBeGreaterThan(40);
    }
    for (const b of TREND_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    const menu = trendMenu(readTrend(GOLD), false, () => null, () => undefined);
    expect(menu.map((i) => i.label)).toEqual(["Изменить", "Показать в 3D", "Где используется", "Удалить…"]);
  });

  it("is unavailable without a project; writing — in a read-only project", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("trend.open", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect((await app.registry.execute("trend.show", { trend_id: 4 })).ok).toBe(true);
    expect((await app.registry.execute("trend.save", { ang1: 10 })).result).toMatch(/только для чтения/);
    expect(writes()).toEqual([]);
  });
});

describe("trend.save", () => {
  it("without angles and a model — the window «Новый тренд», prefilled; nothing is sent", async () => {
    const r = await app.registry.execute("trend.save", { name: "СВ", color: "#1f6bff" });
    expect(r).toEqual({ ok: true, result: "Открыто окно «Новый тренд»" });
    const [w] = windowStore.get().windows;
    expect(w.kind).toBe(TREND_WINDOW);
    expect(editOf(w.id)).toMatchObject({ trendId: null, values: { name: "СВ", color: "#1f6bff", ang1: "0" } });
    expect(trends()).toEqual([]);
    expect(renderTrendWindow(w)).not.toBeNull();
    expect(renderTrendWindow({ ...w, kind: "swath" })).toBeNull();
  });

  it("with angles — `POST /trends` with the body of the contract; the assistant is asked first", async () => {
    const p = app.registry.execute("trend.save", { name: "СВ", ang1: 45, ang2: -10, ratios: "0,5; 0,2", size: 80, centre: "1000; 2000; 300" }, "claude");
    await answerClaude(true);
    const r = await p;
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/^Тренд «СВ» сохранён \(№10\)/);
    expect(writes()).toEqual([{ method: "POST", path: T, body: { name: "СВ", ang1: 45, ang2: -10, ratios: [1, 0.5, 0.2], size: 80, centre: [1000, 2000, 300] } }]);
    expect(lastSaved()).toEqual({ id: 10, name: "СВ" });
    expect(findTrend(10)?.name).toBe("СВ");
  });

  it("a copy of a model: `model_id`, `structure`; with `trend_id` — «Обновить из модели» (`PUT`)", async () => {
    expect((await app.registry.execute("trend.save", { model_id: 3, structure: 1 })).ok).toBe(true);
    expect(writes()[0]).toEqual({ method: "POST", path: T, body: { model_id: 3, structure: 1 } });
    const r = await app.registry.execute("trend.save", { trend_id: 4, model_id: 3 });
    expect(r.result).toMatch(/^Тренд «Тренд · Золото» обновлён из модели вариограммы: ang1 112,5°/);
    expect(writes()[1]).toEqual({ method: "PUT", path: `${T}/4`, body: { model_id: 3, expected_version: 1 } }); // the version read
  });

  it("out of range never reaches the core; an unknown trend — 404 in Russian", async () => {
    for (const a of [{ ang1: 400 }, { ang1: 0, ratios: "0; 1" }, { ang1: 0, size: 0 }, { ang1: 0, kind: "structural" }, { structure: 2, ang1: 0 }]) {
      const r = await app.registry.execute("trend.save", a);
      expect(r.ok, JSON.stringify(a)).toBe(false);
    }
    expect(writes()).toEqual([]);
    expect((await app.registry.execute("trend.save", { trend_id: 99, ang1: 1 })).result).toMatch(/Тренд №99 не найден/);
  });

  it("a used trend: the person is asked (our window, the consumers named); «Отмена» sends nothing", async () => {
    rows[0].used = true;
    const p = app.registry.execute("trend.save", { trend_id: 4, ang1: 120 });
    expect(await answer(false)).toMatch(/используется: «Оценка AU»/);
    expect((await p).result).toBe("Отменено");
    expect(writes()).toEqual([]);
    const q = app.registry.execute("trend.save", { trend_id: 4, ang1: 120 });
    await answer(true);
    expect((await q).ok).toBe(true);
    expect(writes()).toEqual([{ method: "PUT", path: `${T}/4`, body: { ang1: 120, expected_version: 1 } }]);
  });

  it("`trend_id` alone opens its window, one per trend", async () => {
    await app.registry.execute("trend.save", { trend_id: 5 });
    await app.registry.execute("trend.open", { trend_id: 5 });
    const wins = windowStore.get().windows;
    expect(wins).toHaveLength(1);
    expect(wins[0].title).toBe(trendTitle("Вручную"));
    expect(editOf(wins[0].id)).toMatchObject({ trendId: 5, values: { cx: "5000123,25", r2: "0,3" }, dirty: false });
    expect(writes()).toEqual([]);
  });
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));

describe("the window «Тренд»", () => {
  it("shows the form of a stored trend in Russian; the tree lists the trends; «Сохранить как тренд» knows its window", async () => {
    await app.registry.execute("trend.open", { trend_id: 4 });
    const [w] = windowStore.get().windows;
    const html = render(createElement(TrendWindow, { winId: w.id }));
    for (const text of ["Азимут ang1, °", "a2 / a1", "Размер показа a1, м", "Из модели вариограммы", "Обновить из модели", "Эллипсоид в 3D", "Сохранить", "Где используется", "Удалить…", "Из модели №3, структура 2"]) expect(html, text).toContain(text);
    expect(html).toContain('value="112,5"');
    const fresh = openTrendWindow(null);
    const empty = render(createElement(TrendWindow, { winId: fresh }));
    expect(empty).toContain("Взять оси модели");
    expect(empty).not.toContain("Удалить…");
    trendsStore.set({ trends: [readTrend(GOLD), readTrend(MANUAL)], loaded: true });
    const tree = render(createElement(TrendsTree));
    expect(tree.indexOf("Вручную")).toBeLessThan(tree.indexOf("Тренд · Золото")); // by name
    variographyStore.set({ works: {}, models: [], modelsLoaded: true });
    setWork("vario-1", newWork());
    expect(render(createElement(SaveAsTrendButton, { winId: "vario-1" }))).toMatch(/disabled=""[^>]*title="В модели нет структуры/);
  });

  it("«Сохранить» of a new trend — `trend.save` through the registry; the window binds to the trend made", async () => {
    const win = openTrendWindow(null);
    setEdit(win, (e) => ({ values: { ...e.values, name: "Тренд СВ", ang1: "45", r2: "0,5", r3: "0,2", cx: "10", cy: "20", cz: "30" }, dirty: true }));
    expect(await saveWindow(app, win)).toBeNull();
    expect(writes()[0]).toEqual({ method: "POST", path: T, body: { name: "Тренд СВ", size: 100, color: "#f46a1a", centre: [10, 20, 30], ang1: 45, ang2: 0, ang3: 0, ratios: [1, 0.5, 0.2] } });
    expect(editOf(win)).toMatchObject({ trendId: 10, dirty: false });
    expect(windowStore.get().windows[0]).toMatchObject({ title: "Тренд · Тренд СВ", props: { trendId: 10 } });
    expect(app.registry.entries()[0]).toMatchObject({ commandId: "trend.save", source: "user", ok: true });
    // a wrong value: its text, nothing sent
    setEdit(win, (e) => ({ values: { ...e.values, ang2: "95" } }));
    expect(await saveWindow(app, win)).toMatch(/ang2/);
    expect(writes()).toHaveLength(1);
    // a stored one: PUT, the centre emptied — null
    setEdit(win, (e) => ({ values: { ...e.values, ang2: "0", cx: "", cy: "", cz: "" } }));
    expect(await saveWindow(app, win)).toBeNull();
    expect(writes()[1]).toMatchObject({ method: "PUT", path: `${T}/10`, body: { centre: null, ang1: 45, expected_version: 1 } });
    expect(editOf(win)).toMatchObject({ version: 2, dirty: false });
  });

  it("saved in another window meanwhile (409 `version_conflict`): «Сохранить как новый» or «Перечитать», in our own window", async () => {
    await app.registry.execute("trend.open", { trend_id: 5 });
    const win = windowStore.get().windows[0].id;
    expect(editOf(win)).toMatchObject({ trendId: 5, version: 1 });
    rows[1] = { ...rows[1], ang1: 33, version: 2 }; // another window saved it
    setEdit(win, (e) => ({ values: { ...e.values, ang1: "50" }, dirty: true }));
    // «Перечитать»: the stored trend fills the window, nothing written
    let saving = saveWindow(app, win);
    expect(await answer(false)).toMatch(/изменили в другом окне/);
    expect(await answer(true)).toMatch(/Перечитать тренд «Вручную»/);
    expect(await saving).toBeNull();
    expect(editOf(win)).toMatchObject({ version: 2, dirty: false, values: { ang1: "33" } });
    expect(writes().filter((w) => w.method === "PUT")).toHaveLength(1); // the refused one only
    // «Сохранить как новый»: a POST with the next free name, the window holds the new trend
    rows[1] = { ...rows[1], version: 3 };
    setEdit(win, (e) => ({ values: { ...e.values, ang1: "50" }, dirty: true }));
    saving = saveWindow(app, win);
    expect(await answer(true)).toMatch(/Сохранить тренд этого окна как новый/);
    expect(await saving).toBeNull();
    const post = writes().filter((w) => w.method === "POST").at(-1)!;
    expect(post.body).toMatchObject({ name: "Вручную (2)", ang1: 50 });
    expect(editOf(win)).toMatchObject({ trendId: rows.at(-1)!.id, dirty: false });
    // the assistant: the refusal of the core, no window
    const asked = app.registry.execute("trend.save", { trend_id: 5, ang1: 1, expected_version: 1 }, "claude");
    await answerClaude(true); // `confirm`: the assistant's command is asked first
    const r = await asked;
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Тренд изменён в другом окне/);
  });
});

describe("trend.delete, trend.show, trend.usage", () => {
  it("a used trend is not deleted: its consumers in our own window, no `DELETE`", async () => {
    rows[0].used = true;
    const p = app.registry.execute("trend.delete", { trend_id: 4 });
    expect(await answer(true)).toMatch(/не удалён — он выбран в объектах: «Оценка AU»/);
    expect((await p).result).toMatch(/используется: «Оценка AU» — не удалён/);
    expect(writes()).toEqual([]);
  });

  it("the assistant: the registry asks; 409 `in_use` of the core comes back in Russian", async () => {
    rows[0].used = true;
    const p = app.registry.execute("trend.delete", { trend_id: 4 }, "claude");
    await answerClaude(true);
    expect((await p).result).toMatch(/Тренд используется: «Оценка AU»/);
  });

  it("a free one — asked, deleted, gone from the list, the view key and its window", async () => {
    await app.registry.execute("trend.show", { trend_id: 5 });
    expect(viewStore.get()[ELLIPSOIDS_KEY]).toEqual([{ object: 5, visible: true }]);
    await app.registry.execute("trend.open", { trend_id: 5 });
    const p = app.registry.execute("trend.delete", { trend_id: 5 });
    expect(await answer(true)).toBe("Удалить тренд «Вручную»?");
    expect((await p).result).toBe("Тренд «Вручную» удалён");
    expect(writes()).toEqual([{ method: "DELETE", path: `${T}/5`, body: {} }]);
    expect(findTrend(5)).toBeUndefined();
    expect(windowStore.get().windows).toEqual([]);
  });

  it("`trend.show` — the view key `ellipsoids`; `trend.usage` — the consumers", async () => {
    expect((await app.registry.execute("trend.show", { trend_id: 4 })).result).toBe("Эллипсоид тренда «Тренд · Золото» показан в 3D — в центре вида (центр тренда не задан)");
    expect((await app.registry.execute("trend.show", { trend_id: 4, visible: false })).result).toBe("Эллипсоид тренда «Тренд · Золото» скрыт");
    expect(viewStore.get()[ELLIPSOIDS_KEY]).toEqual([{ object: 4, visible: false }]);
    expect((await app.registry.execute("trend.usage", { trend_id: 4 }, "claude")).result).toBe("Тренд «Тренд · Золото» нигде не используется — его можно удалить");
    rows[0].used = true;
    expect((await app.registry.execute("trend.usage", { trend_id: 4 }, "claude")).result).toBe("Тренд «Тренд · Золото» используется (1): Оценка AU (estimation, роль trend)");
    forgetTrend(4);
    expect(viewStore.get()[ELLIPSOIDS_KEY]).toEqual([]);
  });

  it("«Сохранить как тренд» of a variogram window: its arguments pass the registry as they are", async () => {
    const model = { type: "semivariogram" as const, c0: 0.1, common_axes: false, structures: [{ type: "spherical" as const, c: 0.5, ang1: 30, ang2: -10, ang3: 5, a1: 40, a2: 30, a3: 8 }] };
    const r = await saveAsTrend(app, trendArgsOfWork(newWork({ model, dirty: true, ellipsoid: { visible: true, structure: null, centre: [1, 2, 3] } }))!);
    expect(r).toMatchObject({ ok: true, id: 10 });
    expect(writes()[0].body).toEqual({ ang1: 30, ang2: -10, ang3: 5, ratios: [1, 0.75, 0.2], size: 40, centre: [1, 2, 3], origin: { type: "ellipsoid" } });
    const saved = await saveAsTrend(app, trendArgsOfWork(newWork({ model, modelId: 3 }))!);
    expect(saved.ok).toBe(true);
    expect(writes()[1].body).toEqual({ model_id: 3, structure: 1 });
    expect(shapeText(readTrend(GOLD))).toBe("ang1 112,5°, ang2 -35°, ang3 -20°, a1 : a2 : a3 = 1 : 0,36 : 0,05, размер 250 м");
  });
});
