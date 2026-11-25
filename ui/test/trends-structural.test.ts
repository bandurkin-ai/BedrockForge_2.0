// The structural trend (ТЗ 7.35 Д-15, 8.9.2; ADR 0026 решение 27; вопросы № 97–98; Э19 19.26): the forms of the answers of the core
// (`geostat/trend_store.py::_form`, `routes_trend.py`: `source`, `source_info`, `source_lost`, `radius`, `n_measurements`; `/discs`),
// the check of the source before a request, the body and the arguments of `trend.save`, the window «Тренд» of that kind, `trend.show`
// (the view key `ellipsoids` — the discs). The core is a fetch stub answering with the forms of the code (tests ТД15-5…9 are the core's).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { readDiscs, readSource, readTrend } from "../src/modules/trends/api";
import { openTrendWindow, runSave } from "../src/modules/trends/bind";
import { commands } from "../src/modules/trends/commands";
import { checkSource, checkStructural, editFieldsOf, EMPTY_STRUCTURAL, notStructural, sourceOfArgs, sourceText, structuralArgs, structuralBodyOfArgs, structuralOf, structuralSummary } from "../src/modules/trends/structural";
import { discInfoText, editOf, ELLIPSOIDS_KEY, newEdit, setDiscInfo, setEdit, trendsStore } from "../src/modules/trends/state";
import { TrendWindow } from "../src/modules/trends/TrendWindow";
import { trendMenu, trendTitleText } from "../src/modules/trends/TrendsTree";
import { viewStore } from "../src/modules/scene/bind";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

/** `GET /trends/{id}` of a structural trend of measurements (the form of `_form` + `n_measurements`). */
const MEAS = { id: 11, name: "Структурный тренд", kind: "structural", source: { type: "measurements", set_id: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", depth: "DEPTH" }, source_info: { set_name: "Скважины", table_name: "Замеры", wireframe_name: null, lost: false }, source_lost: false, radius: 200.0, ratios: [1.0, 0.36, 0.05], size: 80.0, centre: null, color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: null, version: 1, created_at: "2026-11-25T12:00:00Z", updated_at: "2026-11-25T12:00:00Z", n_measurements: 412 }; // prettier-ignore
const MESH = { ...MEAS, id: 12, name: "Кровля", source: { type: "mesh", wireframe_id: 50, r_n: 25.0 }, source_info: { set_name: null, table_name: null, wireframe_name: "Кровля пласта", lost: false }, radius: 150.0, n_measurements: null }; // prettier-ignore
const LOST = { ...MEAS, id: 13, name: "Потерян", source_info: { set_name: null, table_name: null, wireframe_name: null, lost: true }, source_lost: true, n_measurements: null }; // prettier-ignore
const GLOBAL = { id: 5, name: "Вручную", kind: "global", ang1: 17.0, ang2: -3.0, ang3: 0.0, ratios: [1.0, 0.3, 0.07], size: 100.0, centre: null, color: "#f46a1a", origin: { type: "manual" }, used: false, matrix: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], version: 1, created_at: "2026-11-22T00:26:28Z", updated_at: "2026-11-22T00:26:28Z" }; // prettier-ignore
/** `GET /trends/{id}/discs`. */
const DISCS = { radius: 200.0, n: 3, total: 3, thinned: false, discs: { x: [5000123.25, 5000130.5, 5000140], y: [7200456.5, 7200460, 7200470], z: [312.125, 300, 290], nx: [0, 0.6, 0], ny: [0, 0, 0.8], nz: [1, 0.8, 0.6] } }; // prettier-ignore

describe("the answers of the core", () => {
  it("a structural trend: the source, its names, R_тр, the ratios; no angles, no matrix, no centre", () => {
    const t = readTrend(MEAS);
    expect(t).toMatchObject({ id: 11, kind: "structural", source: { type: "measurements", set_id: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", depth: "DEPTH" }, sourceInfo: { set_name: "Скважины", table_name: "Замеры", lost: false }, sourceLost: false, radius: 200, ratios: [1, 0.36, 0.05], size: 80, centre: null, measurements: 412, used: false, version: 1 });
    expect(t.matrix).toEqual([]);
    const m = readTrend(MESH);
    expect(m).toMatchObject({ source: { type: "mesh", wireframe_id: 50, r_n: 25 }, sourceInfo: { wireframe_name: "Кровля пласта" }, measurements: null });
    expect(readTrend(LOST)).toMatchObject({ sourceLost: true, measurements: null });
    // the list has no `n_measurements`: null, not 0
    const { n_measurements: _n, ...inList } = MEAS;
    expect(readTrend(inList).measurements).toBeNull();
  });

  it("a global trend has none of it; a junk source is none (never a made-up one)", () => {
    expect(readTrend(GLOBAL)).toMatchObject({ kind: "global", source: null, sourceInfo: null, sourceLost: false, radius: null, measurements: null });
    expect(readSource({ type: "measurements", set_id: 3, table: "12", alpha: "A" })).toBeNull();
    expect(readSource({ type: "mesh", wireframe_id: "x" })).toBeNull();
    expect(readSource({ type: "mesh", wireframe_id: 7 })).toEqual({ type: "mesh", wireframe_id: 7 });
    expect(readTrend({ ...MEAS, source: { type: "x" } })).toMatchObject({ source: null, sourceLost: true });
  });

  it("the discs: columns of one length, world float64, a non-number stays NaN", () => {
    const d = readDiscs(DISCS);
    expect(d).toMatchObject({ radius: 200, n: 3, total: 3, thinned: false });
    expect(d.x[0]).toBe(5000123.25);
    expect(d.nz).toEqual([1, 0.8, 0.6]);
    const short = readDiscs({ radius: 10, total: 9, thinned: true, discs: { x: [1, 2, 3], y: [1, 2], z: [1, 2, 3], nx: [0, 0, 0], ny: [0, 0, 0], nz: [1, null, 1] } });
    expect(short).toMatchObject({ n: 2, total: 9, thinned: true });
    expect(short.nz[1]).toBeNaN();
    expect(readDiscs(null)).toMatchObject({ n: 0, total: 0, x: [] });
  });
});

describe("the source of the form", () => {
  it("of a stored trend and back: the form of the window ↔ `source` of the core", () => {
    const m = structuralOf(readTrend(MEAS));
    expect(m).toEqual({ type: "measurements", setId: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", depth: "DEPTH", wireframeId: null, rN: "0", radius: "200" });
    const c = checkSource(m);
    expect(c).toEqual({ ok: true, radius: 200, source: MEAS.source });
    const w = structuralOf(readTrend(MESH));
    expect(w).toMatchObject({ type: "mesh", wireframeId: 50, rN: "25", radius: "150" });
    expect(checkSource(w)).toEqual({ ok: true, radius: 150, source: MESH.source });
    expect(checkSource({ ...w, rN: "0" })).toMatchObject({ ok: true, source: { type: "mesh", wireframe_id: 50 } });
    expect((checkSource({ ...w, rN: "0" }) as { source: object }).source).not.toHaveProperty("r_n");
  });

  it("is checked before a request: what is missing or out of range is named, the radius is 10–5000 m, r_n 0–200 m", () => {
    expect(checkSource(EMPTY_STRUCTURAL)).toMatchObject({ ok: false, param: "set" });
    const base = { ...EMPTY_STRUCTURAL, setId: 3 };
    expect(checkSource(base)).toMatchObject({ ok: false, param: "table" });
    expect(checkSource({ ...base, table: "12" })).toMatchObject({ ok: false, param: "alpha", error: expect.stringMatching(/α/) });
    expect(checkSource({ ...base, table: "12", alpha: "A" })).toMatchObject({ ok: false, param: "dipdir" });
    expect(checkSource({ ...base, table: "12", alpha: "A", dipdir: "A" })).toMatchObject({ ok: false, param: "dipdir", error: expect.stringMatching(/разные/) });
    const ok = { ...base, table: "12", alpha: "A", dipdir: "B" };
    expect(checkSource(ok)).toMatchObject({ ok: true, radius: 200 });
    expect(checkSource({ ...ok, depth: "D" })).toMatchObject({ ok: true, source: { depth: "D" } });
    for (const radius of ["9,9", "5000,1", "", "abc"]) expect(checkSource({ ...ok, radius }), radius).toMatchObject({ ok: false, param: "radius" });
    for (const radius of ["10", "5000", "12,5"]) expect(checkSource({ ...ok, radius }), radius).toMatchObject({ ok: true });
    expect(checkSource({ ...EMPTY_STRUCTURAL, type: "mesh" })).toMatchObject({ ok: false, param: "wireframe" });
    for (const rN of ["-1", "200,5", "x"]) expect(checkSource({ ...EMPTY_STRUCTURAL, type: "mesh", wireframeId: 5, rN }), rN).toMatchObject({ ok: false, param: "r_n" });
  });

  it("the window's arguments of `trend.save`: kind, source, radius, ratios, size, colour; a stored trend — its id and version", () => {
    const edit = newEdit({ ...editFieldsOf(readTrend(MEAS)) });
    const got = structuralArgs(edit);
    expect(got).toEqual({ args: { kind: "structural", source: "measurements", radius: 200, ratios: "0,36; 0,05", size: 80, color: "#f46a1a", name: "Структурный тренд", trend_id: 11, expected_version: 1, set_id: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", depth: "DEPTH" } });
    const mesh = structuralArgs(newEdit({ ...editFieldsOf(readTrend(MESH)), trendId: null, version: undefined }));
    expect(mesh).toEqual({ args: { kind: "structural", source: "mesh", radius: 150, ratios: "0,36; 0,05", size: 80, color: "#f46a1a", name: "Кровля", wireframe_id: 50, r_n: 25 } });
    // a wrong ratio or colour of the shape stops it as a global trend's would; the angles and the centre are not its own
    expect(structuralArgs({ ...edit, values: { ...edit.values, r2: "0" } })).toMatchObject({ param: "r2" });
    expect(structuralArgs({ ...edit, values: { ...edit.values, color: "red" } })).toMatchObject({ param: "color" });
    expect(structuralArgs({ ...edit, values: { ...edit.values, ang1: "999", cx: "1" } })).toHaveProperty("args");
    expect(structuralArgs({ ...edit, structural: { ...edit.structural, radius: "5" } })).toMatchObject({ param: "radius" });
    const c = checkStructural(edit);
    expect(c.ok && c.body).toMatchObject({ kind: "structural", source: MEAS.source, radius: 200, ratios: [1, 0.36, 0.05], size: 80 });
  });

  it("the arguments of the assistant: the source and R_тр are checked, the angles refused", () => {
    expect(sourceOfArgs({ set_id: 3, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR" })).toEqual({ source: { type: "measurements", set_id: 3, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR" } });
    expect(sourceOfArgs({ source: "measurements", set_id: 3, table: 12, alpha: "A", dipdir: "B", depth: "D", radius: "150" })).toEqual({ source: { type: "measurements", set_id: 3, table: "12", alpha: "A", dipdir: "B", depth: "D" }, radius: 150 });
    expect(sourceOfArgs({ wireframe_id: 50, r_n: 0 })).toEqual({ source: { type: "mesh", wireframe_id: 50 } });
    expect(sourceOfArgs({ source: "mesh", wireframe_id: 50, r_n: "25" })).toEqual({ source: { type: "mesh", wireframe_id: 50, r_n: 25 } });
    expect(sourceOfArgs({ radius: 300 })).toEqual({ radius: 300 }); // an update of R_тр alone keeps the source
    expect(sourceOfArgs({})).toEqual({});
    expect(sourceOfArgs({ radius: 5 })).toMatchObject({ param: "radius" });
    expect(sourceOfArgs({ source: "plane" })).toMatchObject({ param: "source" });
    expect(sourceOfArgs({ source: "mesh" })).toMatchObject({ param: "wireframe_id" });
    expect(sourceOfArgs({ source: "mesh", wireframe_id: 5, r_n: 201 })).toMatchObject({ param: "r_n" });
    expect(sourceOfArgs({ source: "measurements", table: "T", alpha: "A", dipdir: "B" })).toMatchObject({ param: "set_id" });
    expect(sourceOfArgs({ set_id: 3, alpha: "A", dipdir: "B" })).toMatchObject({ param: "table" });
    expect(sourceOfArgs({ set_id: 3, table: "T", dipdir: "B" })).toMatchObject({ param: "alpha" });
    expect(notStructural({ ang1: 10 })).toMatchObject({ param: "ang1" });
    expect(notStructural({ model_id: 3 })).toMatchObject({ param: "model_id" });
    expect(notStructural({ name: "x", ratios: "1; 1" })).toBeNull();
    const body = structuralBodyOfArgs({ kind: "structural", name: " Кровля ", set_id: 3, table: "Замеры", alpha: "A", dipdir: "B", radius: 120, ratios: "0,5; 0,2", size: 60, color: "#1F6BFF" });
    expect(body).toEqual({ body: { name: "Кровля", ratios: [1, 0.5, 0.2], size: 60, color: "#1f6bff", kind: "structural", source: { type: "measurements", set_id: 3, table: "Замеры", alpha: "A", dipdir: "B" }, radius: 120 } });
    expect(structuralBodyOfArgs({ ang2: 5, set_id: 3 })).toMatchObject({ param: "ang2" });
    expect(structuralBodyOfArgs({ size: 1e9 })).toMatchObject({ param: "size" });
  });

  it("the texts: the source in Russian (names, a source that is gone), the line of the tree", () => {
    expect(sourceText(readTrend(MEAS))).toBe("Замеры: набор «Скважины», таблица «Замеры», α «ALPHA», Aп «DIPDIR», глубина «DEPTH»");
    expect(sourceText({ ...readTrend(MEAS), source: { ...MEAS.source, depth: undefined } as never })).toContain("середина интервала");
    expect(sourceText(readTrend(MESH))).toBe("Опорный каркас «Кровля пласта», r_n 25 м");
    expect(sourceText(readTrend(LOST))).toMatch(/№3.*№12.*удалён или недоступен/);
    expect(structuralSummary(readTrend(MEAS))).toBe("Структурный тренд «Структурный тренд»: R_тр 200 м; 1 : 0,36 : 0,05; замеров 412");
    expect(trendTitleText(readTrend(LOST))).toMatch(/источник удалён/);
    expect(trendTitleText(readTrend(MEAS))).toContain("галочка — диски в 3D");
    expect(trendMenu(readTrend(MEAS), false, () => null, () => undefined).map((i) => i.label)).toEqual(["Изменить", "Показать диски в 3D", "Где используется", "Удалить…"]);
    expect(discInfoText({ n: 50000, total: 120000, thinned: true, skipped: 0 })).toBe("Показано дисков: 50 000 из 120 000 (прорежено для 3D)");
    expect(discInfoText({ n: 3, total: 3, thinned: false, skipped: 2 })).toContain("без положения или нормали: 2");
  });

  it("`trend.save` names the structural arguments for the assistant", () => {
    const save = commands.find((c) => c.id === "trend.save")!;
    for (const k of ["kind", "source", "set_id", "table", "alpha", "dipdir", "depth", "wireframe_id", "r_n", "radius"]) expect(save.params![k], k).toBeDefined();
    expect(save.params!.kind.enum).toEqual(["global", "structural"]);
    expect(save.description).toMatch(/Структурный/);
  });
});

// ---- the commands and the window ----

let app: App;
let rows: Record<string, unknown>[];
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
const T = "/geostat/trends";
const writes = () => sent.filter((r) => r.path.startsWith(T) && r.method !== "GET");

beforeEach(() => {
  sent.length = 0;
  rows = [structuredClone(MEAS), structuredClone(MESH), structuredClone(LOST), structuredClone(GLOBAL)];
  windowStore.set({ windows: [] });
  trendsStore.set({ trends: [], loaded: false, selected: null, edits: {}, dragging: null, discs: {} });
  viewStore.set({ [ELLIPSOIDS_KEY]: undefined });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1 });
      if (path === T && method === "GET") return json({ trends: rows });
      if (path === T && method === "POST") {
        const id = 20 + rows.length;
        const name = typeof body.name === "string" ? body.name : "Структурный тренд";
        rows.push({ ...MEAS, id, name, source: body.source, radius: body.radius ?? 200 });
        return json({ id, name, version: 1 });
      }
      const m = /^\/geostat\/trends\/(\d+)$/.exec(path);
      const row = m ? rows.find((r) => r.id === Number(m[1])) : undefined;
      if (m && method === "GET") return row ? json(row) : json({ code: "trend_not_found", message: "Тренд не найден.", hint: "" }, 404);
      if (m && method === "PUT") {
        const { expected_version: _v, ...fields } = body;
        Object.assign(row!, fields, { version: Number(row!.version) + 1 });
        return json({ id: row!.id, name: row!.name, version: row!.version });
      }
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
});

describe("trend.save of a structural trend", () => {
  it("measurements: the body of the core — kind, source (as given: the core finds the table by its name), R_тр; the trend lands in the list", async () => {
    const r = await app.registry.execute("trend.save", { kind: "structural", name: "По замерам", set_id: 3, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR", radius: 150, ratios: "0,5; 0,2" });
    expect(r.ok, r.result).toBe(true);
    expect(writes()).toEqual([{ method: "POST", path: T, body: { name: "По замерам", ratios: [1, 0.5, 0.2], kind: "structural", source: { type: "measurements", set_id: 3, table: "Замеры", alpha: "ALPHA", dipdir: "DIPDIR" }, radius: 150 } }]);
    expect(r.result).toMatch(/Структурный тренд «По замерам»: R_тр 150 м/);
    expect(trendsStore.get().trends.some((t) => t.name === "По замерам" && t.kind === "structural")).toBe(true);
  });

  it("a wireframe: `wireframe_id` alone means the source mesh", async () => {
    const r = await app.registry.execute("trend.save", { kind: "structural", wireframe_id: 50, r_n: 25 });
    expect(r.ok, r.result).toBe(true);
    expect(writes()[0].body).toMatchObject({ kind: "structural", source: { type: "mesh", wireframe_id: 50, r_n: 25 } });
  });

  it("without a source — the window of a new structural trend; out of range, angles, a global trend turned structural — refused, nothing sent", async () => {
    const r = await app.registry.execute("trend.save", { kind: "structural", name: "Новый", color: "#1f6bff" });
    expect(r.ok).toBe(true);
    const [w] = windowStore.get().windows;
    expect(editOf(w.id)).toMatchObject({ trendId: null, kind: "structural", values: { name: "Новый", color: "#1f6bff" } });
    expect(writes()).toEqual([]);
    for (const a of [{ kind: "structural", set_id: 3, table: "T", alpha: "A", dipdir: "B", radius: 5 }, { kind: "structural", ang1: 10 }, { kind: "structural", wireframe_id: 5, r_n: 500 }, { kind: "structural", source: "plane" }, { kind: "structural", trend_id: 5, color: "#1f6bff" }]) {
      const out = await app.registry.execute("trend.save", a);
      expect(out.ok, JSON.stringify(a)).toBe(false);
    }
    // a structural trend takes no angles
    expect((await app.registry.execute("trend.save", { trend_id: 11, ang1: 10 })).ok).toBe(false);
    expect(writes()).toEqual([]);
  });

  it("a stored trend: R_тр and the colour change over its version (the version read, the source kept)", async () => {
    const r = await app.registry.execute("trend.save", { trend_id: 11, kind: "structural", radius: 300, color: "#1f6bff" });
    expect(r.ok, r.result).toBe(true);
    expect(writes()).toEqual([{ method: "PUT", path: `${T}/11`, body: { expected_version: 1, color: "#1f6bff", kind: "structural", radius: 300 } }]);
  });

  it("`trend.show`: the discs of a structural trend — the view key `ellipsoids`; a source that is gone — refused", async () => {
    await app.registry.execute("trend.open", { trend_id: 11 }); // reads the list
    windowStore.set({ windows: [] });
    const r = await app.registry.execute("trend.show", { trend_id: 11 });
    expect(r).toEqual({ ok: true, result: "Диски структурного тренда «Структурный тренд» показаны в 3D" });
    expect(viewStore.get()[ELLIPSOIDS_KEY]).toEqual([{ object: 11, visible: true }]);
    expect((await app.registry.execute("trend.show", { trend_id: 11, visible: false })).result).toMatch(/скрыты/);
    const lost = await app.registry.execute("trend.show", { trend_id: 13 });
    expect(lost.ok).toBe(false);
    expect(lost.result).toMatch(/Источник тренда «Потерян».*удалён/);
    expect(viewStore.get()[ELLIPSOIDS_KEY]).toEqual([{ object: 11, visible: false }]);
  });
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));

describe("the window «Тренд» of a structural trend", () => {
  it("shows the source, R_тр, the ratios and the discs — no angles, no ellipsoid, no model; the kind of a stored trend is fixed", async () => {
    await app.registry.execute("trend.open", { trend_id: 11 });
    const [w] = windowStore.get().windows;
    expect(editOf(w.id)).toMatchObject({ trendId: 11, kind: "structural", structural: { type: "measurements", setId: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", depth: "DEPTH", radius: "200" }, values: { r2: "0,36", r3: "0,05", size: "80" } });
    const html = render(createElement(TrendWindow, { winId: w.id }));
    for (const text of ["Структурный", "Глобальный", "Источник осей", "Замеры «точка, α, Aп»", "Опорный каркас", "Радиус влияния R_тр, м", "Поле α", "Поле Aп", "Глубина замера", "Диаметр диска в 3D, м", "Диски в 3D", "Цвет дисков", "замеров в расчёте: 412", "набор «Скважины», таблица «Замеры»", "Сохранить", "Где используется"]) expect(html, text).toContain(text);
    for (const text of ["Азимут ang1", "Из модели вариограммы", "Эллипсоид в 3D", "Происхождение"]) expect(html, text).not.toContain(text);
    expect(html).toMatch(/disabled=""[^>]*data-testid="trend-kind-global-/);
    expect(html).toMatch(/value="200"/);
    expect(html).not.toMatch(/disabled=""[^>]*data-testid="trend-discs-/); // the trend is stored: the discs may be shown
  });

  it("a new one: the kind is chosen; «Сохранить» waits for the source; the discs wait for the trend to be saved", () => {
    const win = openTrendWindow(null, {}, "structural");
    expect(editOf(win)).toMatchObject({ kind: "structural", trendId: null });
    const html = render(createElement(TrendWindow, { winId: win }));
    expect(html).toContain("Выберите набор скважин с замерами");
    expect(html).toContain("Сначала сохраните тренд");
    expect(html).toMatch(/disabled=""[^>]*data-testid="trend-save-/);
    expect(html).not.toContain("Удалить…");
    const mesh = render(createElement(TrendWindow, { winId: setEditKind(win, "mesh") }));
    expect(mesh).toContain("Радиус осреднения нормали r_n");
    expect(mesh).toContain("Выберите опорный каркас или поверхность");
  });

  it("a source that is gone is said so, with what to do; the discs are off", async () => {
    await app.registry.execute("trend.open", { trend_id: 13 });
    const [w] = windowStore.get().windows;
    const html = render(createElement(TrendWindow, { winId: w.id }));
    expect(html).toContain("удалён или недоступен");
    expect(html).toContain("Выберите другой источник и сохраните тренд или удалите его");
    expect(html).toContain("Источник тренда удалён");
  });

  it("the discs shown tell «Показано N из M»", async () => {
    await app.registry.execute("trend.open", { trend_id: 11 });
    const [w] = windowStore.get().windows;
    viewStore.set({ [ELLIPSOIDS_KEY]: [{ object: 11, visible: true }] });
    setDiscInfo(11, { n: 412, total: 412, thinned: false, skipped: 0 });
    const html = render(createElement(TrendWindow, { winId: w.id }));
    expect(html).toMatch(/data-testid="trend-discs-[^"]*"[^>]*checked=""/);
    expect(html).toContain("Показано дисков: 412 из 412");
    setDiscInfo(11, null);
    expect(trendsStore.get().discs).toEqual({});
  });

  it("«Сохранить» of a new one: POST with the body of the form; the window binds to the trend made", async () => {
    const { saveWindow } = await import("../src/modules/trends/bind");
    const win = openTrendWindow(null, { name: "Из окна" }, "structural");
    setEdit(win, { structural: { ...EMPTY_STRUCTURAL, setId: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR", radius: "250" }, dirty: true });
    expect(await saveWindow(app, win)).toBeNull();
    expect(writes()).toHaveLength(1);
    expect(writes()[0].body).toMatchObject({ name: "Из окна", kind: "structural", radius: 250, source: { type: "measurements", set_id: 3, table: "12", alpha: "ALPHA", dipdir: "DIPDIR" } });
    expect(editOf(win)).toMatchObject({ kind: "structural", dirty: false });
    expect(editOf(win)?.trendId).toBeGreaterThan(20);
    // the window of a wrong value says what and sends nothing
    setEdit(win, { structural: { ...editOf(win)!.structural, radius: "5" } });
    sent.length = 0;
    expect(await saveWindow(app, win)).toMatch(/R_тр/);
    expect(writes()).toEqual([]);
  });

  it("a structural trend of the list is not an ellipsoid: `runSave` of its id with the shape only changes the shape", async () => {
    const r = await runSave({ trend_id: 11, color: "#1f6bff" }, "user");
    expect(r).toMatch(/изменён/);
    expect(writes()[0]).toMatchObject({ method: "PUT", body: { color: "#1f6bff" } });
  });
});

/** The window of a new structural trend switched to the wireframe source. */
function setEditKind(win: string, type: "mesh" | "measurements"): string {
  setEdit(win, (e) => ({ structural: { ...e.structural, type } }));
  return win;
}
