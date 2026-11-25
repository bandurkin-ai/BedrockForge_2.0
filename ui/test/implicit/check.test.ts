// «Проверка по скважинам» (Э19, 19.25; ТЗ 7.35 Д-32; docs/progress/e19-api.md §8.1, §8.2) on the REAL answers of the core
// (`core/tests/data/implicit/contract_answers.json`: `check`, `check_csv`, `support`, `support_one`, `support_f4`, `support_mesh`): the readers (a gap stays `null`),
// the command `implicit.check` and its window (the table, the sort, the pages, the highlight, the file, the points in 3D), the command
// `implicit.support` (the shares of the area beyond D_оп, the map in the 3D window, the repaint when D_оп moves), the colours of the map.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import type { Scene } from "../../src/viz/Scene";
import { setCurrentScene } from "../../src/modules/scene/sceneHost";
import { csvName, readCheck, readSupport, readSupportF4, readSupportMesh, loadCheckCsv, DEFAULT_QUERY } from "../../src/modules/implicit/checkApi";
import { cellText, COLUMNS, groupText, numText, pointCloud, boxAround, checkLine, pagesOf } from "../../src/modules/implicit/checkModel";
import { checkStore, openCheck, pickRow, resetCheck, saveCsv, setHighlightText, applyHighlight, setQuery, toggleShown, parseField } from "../../src/modules/implicit/CheckState";
import { CheckWindow } from "../../src/modules/implicit/CheckWindow";
import { SupportPanel } from "../../src/modules/implicit/SupportPanel";
import { applyThreshold, hideMap, openSupport, resetSupport, setThresholdText, showMap, supportStore, thresholdOf, shareOf } from "../../src/modules/implicit/SupportState";
import { colorsOf, countBeyond, meshData, percent, supportColor, NO_DATA_COLOR } from "../../src/modules/implicit/supportModel";
import { bindChecks } from "../../src/modules/implicit/checkBind";
import { loadRows } from "../../src/modules/implicit/state";
import { hideSupport } from "../../src/modules/implicit/supportScene";
import { hideCheckPoints } from "../../src/modules/implicit/checkScene";
import { ID, newApp, teardown, waitFor } from "./harness";
import { distances, installCheckCore, meshAnswer, R, SECOND, SECOND_TRIANGLES, SECOND_VERTICES, SURFACE, type CheckCore } from "./checkHarness";

let app: App;
let core: CheckCore;
const added = new Map<string, { vertices?: number; setColors?: (c: Float32Array) => void }>();
const reveal = vi.fn(() => true);
const fakeScene = {
  clipPlanes: [],
  addLayer: (k: string, l: { vertices?: number }) => void added.set(k, l),
  removeLayer: (k: string) => void added.delete(k),
  reveal,
  invalidate: () => undefined,
  onRestored: () => () => undefined,
  setHandles: () => undefined, // the handles of the other modules that join every scene (the ellipsoids of the variography)
} as unknown as Scene;

beforeEach(async () => {
  core = installCheckCore();
  app = newApp();
  added.clear();
  reveal.mockClear();
  await loadRows();
});
afterEach(() => {
  hideCheckPoints();
  hideSupport();
  resetCheck();
  resetSupport();
  setCurrentScene(null);
  delete (globalThis as { window?: unknown }).window;
  teardown();
});

const flat = (s: string): string => s.replace(/[\u00a0\u202f]/g, " ");
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const CHECK = `GET /implicit/models/${ID.model}/check`;
const SUPPORT = `POST /implicit/models/${ID.model}/support`;
const render = (el: ReturnType<typeof createElement>) => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el)));
const windows = (kind: string) => windowStore.get().windows.filter((w) => w.kind === kind);

describe("the answers of the core are read as they are (a gap is null, never 0)", () => {
  it("the table: the model, the groups, the rows with their place, the flags", () => {
    const p = readCheck(R.check.body);
    expect(p.model).toMatchObject({ name: "Литология", state: "ready", stale: false });
    expect(p.n).toBe(p.total);
    expect(p.groups.map((g) => g.name)).toEqual(["INT", "SS"]);
    expect(p.groups[0]).toMatchObject({ type: "domain", method: "field", wing: null, threshold: null });
    expect(p.rows).toHaveLength(6);
    const r = p.rows[0];
    expect(r).toMatchObject({ i: 50, group_name: "INT", type: "снаружи", source: "H001", vertex: null, over: false });
    expect(r.x).toBeCloseTo(17.6037, 3);
    expect(typeof r.residual).toBe("number");
    expect(p.sort).toBe("source");
    expect(p.highlight).toBe(0.5);
  });
  it("a gap of the answer stays null: a missing residual, distance and place are «—» in the cells", () => {
    const raw = JSON.parse(JSON.stringify(R.check.body));
    Object.assign(raw.rows[0], { f_model: null, residual: null, distance: null, x: null, to: null });
    const p = readCheck(raw);
    const r = p.rows[0];
    expect([r.f_model, r.residual, r.distance, r.x, r.to]).toEqual([null, null, null, null, null]);
    expect(cellText(r, "residual")).toBe("—");
    expect(cellText(r, "distance")).toBe("—");
    expect(cellText(r, "f_model")).toBe("—");
    expect(pointCloud({ ...p, rows: [r] })).toBeNull(); // no residual, no place: not a point of the cloud
  });
  it("the texts of the cells: Russian numbers, a contact has one depth, the group with its shell and wing", () => {
    const r = readCheck(R.check.body).rows[0];
    expect(cellText(r, "i")).toBe("50");
    expect(cellText(r, "type")).toBe("снаружи");
    expect(cellText(r, "to")).toBe("35,11");
    expect(numText(0.0000000032)).toBe("3,2e−9");
    expect(numText(-12.3456)).toBe("−12,35");
    expect(numText(null)).toBe("—");
    expect(numText(0)).toBe("0");
    expect(groupText({ ...r, group_name: "Au", threshold: 0.5, wing: 2 })).toBe("Au ≥ 0,5, крыло 2");
    expect(COLUMNS.map((c) => c.key)).toEqual(["i", "group", "type", "source", "from", "to", "f_model", "f_data", "residual", "distance"]);
    expect(pagesOf(3316, 6)).toBe(553);
    expect(pagesOf(0, 500)).toBe(1);
  });
  it("the file: the name the system accepts", () => {
    expect(csvName("Литология")).toBe("Проверка модели Литология.csv");
    expect(csvName('a/b:"c"')).toBe("Проверка модели a_b_c_.csv");
  });
  it("the map of the support: the surfaces with the shares; the answer of one surface and the header of the vector f4", () => {
    const s = readSupport(R.support.body);
    expect(s.threshold).toBe(60);
    expect(s.d_max).toBe(30);
    expect(s.recorded).toBe(true);
    expect(s.surfaces).toHaveLength(2);
    expect(s.surfaces[0]).toMatchObject({ kind: "surface", group: 1, vertices: SURFACE.vertices });
    expect(s.surfaces[0].share_beyond).toBeCloseTo(0.00481, 4);
    expect(readSupport(R.support_one.body).surfaces).toHaveLength(1);
    const f4 = readSupportF4(distances().buffer.slice(0) as ArrayBuffer, JSON.stringify(R.support_f4.body.head));
    expect(f4.values).toHaveLength(SURFACE.vertices);
    expect(f4.surface?.vertices).toBe(SURFACE.vertices);
    expect(f4.threshold).toBe(60);
    expect(Number.isNaN(f4.values[3])).toBe(true); // no data points: NaN, not 0
    expect(R.support_f4.body.bytes).toBe(SURFACE.vertices * 4);
    expect(() => readSupportF4(new ArrayBuffer(8), null)).toThrow(/заголовок ответа пуст/);
    expect(() => readSupportF4(new ArrayBuffer(7), "{}")).toThrow(/неверной длины/);
  });
  it("the answer f4mesh of the core: the vertices, the distances and the triangles of the whole wireframe, views of one body", () => {
    const head = R.support_mesh.body.head as { mesh: { origin: number[]; vertices: number; triangles: number } };
    expect(head.mesh.vertices).toBe(SURFACE.vertices);
    expect(R.support_mesh.body.bytes).toBe(head.mesh.vertices * 16 + head.mesh.triangles * 12); // the layout the reader expects
    const m = meshAnswer(SURFACE.id);
    const got = readSupportMesh(m.body, JSON.stringify(m.head));
    expect(got.values).toHaveLength(SURFACE.vertices);
    expect(got.mesh.positions).toHaveLength(SURFACE.vertices * 3);
    expect(got.mesh.index.length % 3).toBe(0);
    expect(got.mesh.index.length / 3).toBe((m.head.mesh as { triangles: number }).triangles);
    expect(got.mesh.origin).toEqual({ x: 200, y: 200, z: 250 });
    expect(got.surface?.vertices).toBe(SURFACE.vertices);
    expect(Number.isNaN(got.values[3])).toBe(true); // no data points: NaN, not 0
    expect(got.values.buffer).toBe(m.body); // no copy
    expect(() => readSupportMesh(new ArrayBuffer(7), JSON.stringify(m.head))).toThrow(/неверной длины/);
    expect(() => readSupportMesh(m.body, null)).toThrow(/заголовок ответа пуст/);
  });
});

describe("implicit.check", () => {
  it("opens the window and asks the table; the text is the summary of the page", async () => {
    const r = await app.registry.execute("implicit.check", {}, "claude");
    expect(r.ok).toBe(true);
    expect(windows("implicit-check")).toHaveLength(1);
    const q = requests(CHECK)[0].query;
    expect(q).toContain("sort=residual");
    expect(q).toContain("page=1");
    expect(q).toContain("size=500");
    const text = flat(String(r.result));
    expect(text).toContain("Модель «Литология»");
    expect(text).toContain("точек 3 316");
    expect(text).toContain("выше порога подсветки 0");
    expect(checkStore.get()).toMatchObject({ modelId: ID.model, phase: "ok", tab: "table" });
  });
  it("one window: a second call brings it forward and does not ask again", async () => {
    await app.registry.execute("implicit.check", {}, "user");
    await app.registry.execute("implicit.check", { model_id: ID.model }, "user");
    expect(windows("implicit-check")).toHaveLength(1);
    expect(requests(CHECK)).toHaveLength(1);
  });
  it("a model that is not there, or not built, is a refusal in Russian with a hint", async () => {
    const none = await app.registry.execute("implicit.check", { model_id: 999 }, "claude");
    expect(none.ok).toBe(false);
    expect(none.result).toContain("не найдена");
    core.models.push({ ...core.models[0], id: 9, name: "Пустая", outputs: 0, stats: null } as never);
    await loadRows();
    const raw = await app.registry.execute("implicit.check", { model_id: 9 }, "claude");
    expect(raw.ok).toBe(false);
    expect(raw.result).toContain("ещё не построена");
    expect(raw.result).toContain("Обновить");
  });
  it("a refusal of the core stands in the window under the bar (409 not_built → words)", async () => {
    core.force(CHECK, { status: 409, body: { code: "not_built", message: "Модель ещё не построена.", hint: "Постройте модель." } });
    const r = await app.registry.execute("implicit.check", {}, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("ещё не построена");
    expect(checkStore.get().phase).toBe("error");
    expect(render(createElement(CheckWindow, { winId: "w" }))).toContain("chk-error");
  });
  it("available with a project, also open for reading only (nothing is written)", () => {
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("implicit.check", { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true })).toBeNull();
    expect(app.registry.reason("implicit.check", { hasProject: false, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true })).toBe("Откройте или создайте проект");
  });
});

describe("the table of the window", () => {
  beforeEach(async () => {
    await openCheck(ID.model);
  });
  it("shows the columns of Д-32, the rows, the facts and the groups", () => {
    const html = render(createElement(CheckWindow, { winId: "w" }));
    for (const t of ["f модели", "f данных", "Невязка", "До каркаса, м", "Источник"]) expect(html).toContain(t);
    expect(html).toContain('data-testid="chk-row-50"');
    expect(html).toContain("H001");
    expect(html).toContain("Наибольшая невязка");
    expect(html).toContain("Страница 1 из 553");
    expect(html).toContain("Группы модели: 2");
    expect(html).toMatch(/data-testid="chk-n">3 316</);
  });
  it("the sort, the group and the page are a new query, the page goes back to the first", async () => {
    await setQuery({ sort: "residual" });
    await setQuery({ page: 3 });
    await setQuery({ group: 1 });
    const q = requests(CHECK).map((r) => r.query);
    expect(q[1]).toContain("sort=residual");
    expect(q[2]).toContain("page=3");
    expect(q[3]).toContain("group=1");
    expect(q[3]).toContain("page=1");
    expect(checkStore.get().query).toMatchObject({ group: 1, page: 1, sort: "residual" });
  });
  it("the threshold of the highlight: a decimal comma is a number, a wrong one is a note and no request", async () => {
    const before = requests(CHECK).length;
    setHighlightText("0,25");
    await applyHighlight();
    expect(requests(CHECK).at(-1)?.query).toContain("highlight=0.25");
    setHighlightText("−1");
    expect(applyHighlight()).toBeNull();
    expect(checkStore.get().note).toContain("не меньше нуля");
    setHighlightText("abc");
    expect(applyHighlight()).toBeNull();
    expect(requests(CHECK).length).toBe(before + 1);
    expect(parseField("1 250,5")).toBe(1250.5);
    expect(parseField("")).toBeNull();
    expect(Number.isNaN(parseField("x") as number)).toBe(true);
  });
  it("a point beyond the threshold is marked in the table; the others are not", () => {
    const p = readCheck(R.check.body);
    checkStore.set({ page: { ...p, over: 1, rows: p.rows.map((r, i) => (i === 1 ? { ...r, over: true } : r)) } });
    const html = render(createElement(CheckWindow, { winId: "w" }));
    expect(html.match(/data-over="true"/g)).toHaveLength(1);
    expect(html.match(/data-over="false"/g)).toHaveLength(5);
  });
  it("the file: every row of the selection as the core wrote it (the BOM is its own), through the save dialog of the application", async () => {
    const save = vi.fn(async (_n: string, _k: string, _d: unknown) => "C:\\Проект\\Проверка модели Литология.csv");
    (globalThis as { window?: unknown }).window = { bf: { saveFile: save } };
    const text = await saveCsv();
    expect(text).toContain("сохранена");
    const [name, kind, data] = save.mock.calls[0];
    expect(name).toBe("Проверка модели Литология.csv");
    expect(kind).toBe("csv");
    const bytes = data as Uint8Array;
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes)).toContain("№;Группа;Порог;Тип");
    const csv = requests(`GET /implicit/models/${ID.model}/check.csv`)[0];
    expect(csv.query).toContain("sort=residual");
    expect(csv.query).not.toContain("page="); // the file is the whole selection
    expect(requests(CHECK).length).toBe(1);
  });
  it("cancelled in the dialog or outside the application: a line, not an error", async () => {
    (globalThis as { window?: unknown }).window = { bf: { saveFile: vi.fn(async () => null) } };
    expect(await saveCsv()).toBe("Сохранение отменено");
    delete (globalThis as { window?: unknown }).window;
    expect(await saveCsv()).toContain("только в приложении");
    expect(checkStore.get().saving).toBe(false);
    await expect(loadCheckCsv(ID.model, DEFAULT_QUERY)).resolves.toBeInstanceOf(Uint8Array);
  });
});

describe("the points in the 3D window", () => {
  beforeEach(async () => {
    await openCheck(ID.model);
  });
  it("without the 3D window: a refusal in words under the table", () => {
    toggleShown();
    expect(checkStore.get().shown).toBe(false);
    expect(checkStore.get().note).toContain("3D-окно ещё не готово");
  });
  it("with it: the layer appears with the points of the page, a second press takes it away", async () => {
    setCurrentScene(fakeScene);
    toggleShown();
    await waitFor(() => added.has("implicit-check"));
    expect(checkStore.get().shown).toBe(true);
    expect(checkStore.get().note).toContain("В 3D показано точек: 6");
    toggleShown();
    expect(added.has("implicit-check")).toBe(false);
    expect(checkStore.get().shown).toBe(false);
  });
  it("a row: the camera goes to the box around its point; a row without a place says so", async () => {
    setCurrentScene(fakeScene);
    const p = checkStore.get().page!;
    pickRow(p.rows[1]);
    expect(checkStore.get().picked).toBe(p.rows[1].i);
    expect(reveal).toHaveBeenCalledTimes(1); // the box of the row (the layer is made after its lazy chunk)
    const box = (reveal.mock.calls[0] as unknown as number[][][])[0][0];
    const r = p.rows[1];
    expect(box[0]).toBeLessThan(r.x as number);
    expect(box[3]).toBeGreaterThan(r.x as number);
    expect(boxAround(r, 0.5)).toEqual(box);
    await waitFor(() => added.has("implicit-check")); // the layer made for the first time fits the camera to its points once itself
    const seen = reveal.mock.calls.length;
    pickRow({ ...p.rows[0], x: null });
    expect(checkStore.get().note).toContain("нет места");
    expect(reveal).toHaveBeenCalledTimes(seen);
  });
  it("the cloud: one point per row that has a place and a residual; the colour at the threshold is the full one", () => {
    const p = checkStore.get().page!;
    const cloud = pointCloud(p)!;
    expect(cloud.n).toBe(6);
    expect(cloud.limit).toBe(0.5);
    expect(cloud.xyz[0]).toBeCloseTo(p.rows[0].x as number, 9); // float64: the place as the core gave it
    expect(checkLine(p)).toContain("наибольшая невязка");
  });
});

describe("implicit.support", () => {
  it("opens the tab and tells the share of the area beyond D_оп of every surface; a model with two surfaces asks which to paint", async () => {
    const r = await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    expect(r.ok).toBe(true);
    const text = flat(String(r.result));
    expect(text).toContain("порог D_оп 60 м");
    expect(text).toContain("за порогом 0,5 % площади");
    expect(text).toContain("за порогом 0 % площади");
    expect(text).toContain("Выберите поверхность");
    expect(requests(SUPPORT)[0].body).toEqual({ threshold: 60 });
    expect(checkStore.get().tab).toBe("support");
    expect(windows("implicit-check")).toHaveLength(1);
    expect(requests(CHECK)).toHaveLength(0); // the table is not read for the map
    expect(shareOf(SURFACE.id)).toBeCloseTo(0.00481, 4);
    expect(percent(null)).toBe("—");
  });
  it("a threshold that is not a number of metres above zero is a refusal", async () => {
    for (const bad of [0, -5]) {
      const r = await app.registry.execute("implicit.support", { threshold: bad }, "claude");
      expect(r.ok, String(bad)).toBe(false);
      expect(r.result).toContain("больше нуля");
    }
  });
  it("the tab lists the surfaces with the shares, the facts of the chosen one, the threshold field", async () => {
    await app.registry.execute("implicit.support", { threshold: 60 }, "user");
    supportStore.set({ surfaceId: SURFACE.id });
    const html = render(createElement(SupportPanel, { modelId: ID.model }));
    expect(html).toContain('data-testid="sup-table"');
    expect(html).toContain(`data-testid="sup-share-${SURFACE.id}"`);
    expect(html).toContain("0,5 %");
    expect(html).toContain("Площадь за порогом");
    expect(html).toContain("Показать карту в 3D");
    expect(html).toContain("по умолчанию 60");
  });
  it("the answer of another model that came late is not painted (the window moved to the other model meanwhile)", async () => {
    setCurrentScene(fakeScene);
    await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    const prev = globalThis.fetch;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(init?.body).includes("f4mesh")) await gate;
        return prev(url, init);
      }),
    );
    const late = showMap(SURFACE.id);
    await openSupport(ID.model2); // the window is now the other model's
    release();
    expect(await late).toBe("Отменено");
    expect(supportStore.get()).toMatchObject({ modelId: ID.model2, shownId: null, painting: false });
    expect(added.has("implicit-support")).toBe(false);
  });
  it("«Показать карту в 3D»: the answer f4mesh is one layer painted by the distance; the counts and the legend", async () => {
    setCurrentScene(fakeScene);
    await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    const line = await showMap(SURFACE.id);
    expect(flat(line)).toContain("за порогом 0,5 % площади");
    expect(requests(SUPPORT).at(-1)?.body).toEqual({ wireframe_id: SURFACE.id, format: "f4mesh", threshold: 60 });
    await waitFor(() => added.has("implicit-support"));
    expect(added.get("implicit-support")?.vertices).toBe(SURFACE.vertices);
    const s = supportStore.get();
    expect(s.shownId).toBe(SURFACE.id);
    expect(s.counts?.vertices).toBe(SURFACE.vertices);
    expect(s.counts?.noData).toBe(1); // the vertex with no data points
    expect(s.counts?.beyond).toBeGreaterThan(0);
    const html = render(createElement(SupportPanel, { modelId: ID.model }));
    expect(html).toContain('data-testid="sup-legend"');
    expect(html).toContain("без данных 1");
    expect(html).toContain("Убрать из 3D");
    hideMap();
    expect(added.has("implicit-support")).toBe(false);
    expect(supportStore.get().shownId).toBeNull();
  });
  it("a surface named in the command is painted in the same call", async () => {
    setCurrentScene(fakeScene);
    const r = await app.registry.execute("implicit.support", { threshold: 60, wireframe_id: SURFACE.id }, "claude");
    expect(r.ok).toBe(true);
    expect(flat(String(r.result))).toContain("Литология · INT · поверхность: за порогом 0,5 % площади");
    await waitFor(() => added.has("implicit-support"));
    const bad = await app.registry.execute("implicit.support", { wireframe_id: 12345 }, "claude");
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("не поверхность модели");
  });
  it("a surface of two bodies and of over 64 000 triangles is painted whole: every vertex has its colour, no vertices are matched", async () => {
    setCurrentScene(fakeScene);
    await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    const line = await showMap(SECOND);
    expect(line).toContain("за порогом");
    expect(requests(SUPPORT).at(-1)?.body).toEqual({ wireframe_id: SECOND, format: "f4mesh", threshold: 60 });
    expect(SECOND_TRIANGLES).toBeGreaterThan(64_000);
    await waitFor(() => added.has("implicit-support"));
    expect(added.get("implicit-support")?.vertices).toBe(SECOND_VERTICES);
    const s = supportStore.get();
    expect(s.shownId).toBe(SECOND);
    expect(s.counts?.vertices).toBe(SECOND_VERTICES);
    expect(s.counts?.noData).toBe(1);
    const m = meshAnswer(SECOND);
    const data = meshData(readSupportMesh(m.body, JSON.stringify(m.head)).mesh);
    expect(data.index.length / 3).toBe(SECOND_TRIANGLES);
    expect(data.index).toBeInstanceOf(Uint32Array); // 32-bit indices: more than 65 535 vertices
    let top = 0;
    for (const i of data.index) if (i > top) top = i;
    expect(top).toBe(SECOND_VERTICES - 1); // every vertex of both bodies is used by a triangle
    expect(data.bbox?.[0]).toBeGreaterThan(900_000); // absolute: the origin is added back
    expect(data.bbox?.[1]).toBeGreaterThan(4_999_000);
    hideMap();
    expect(added.has("implicit-support")).toBe(false);
  });
  it("without the 3D window: a line, the shares stay", async () => {
    await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    const line = await showMap(SURFACE.id);
    expect(line).toContain("3D-окно ещё не готово");
    expect(supportStore.get().answer?.surfaces).toHaveLength(2);
  });
  it("D_оп moved: the vertices are painted again without a read of the distances, the shares are asked again", async () => {
    setCurrentScene(fakeScene);
    await app.registry.execute("implicit.support", { threshold: 60 }, "claude");
    await showMap(SURFACE.id);
    await waitFor(() => added.has("implicit-support"));
    const layer = added.get("implicit-support")!;
    const paint = vi.spyOn(layer as { setColors: (c: Float32Array) => void }, "setColors");
    const f4 = requests(SUPPORT).filter((r) => r.body.format === "f4").length;
    setThresholdText("30");
    await applyThreshold();
    expect(requests(SUPPORT).filter((r) => r.body.format === "f4")).toHaveLength(f4); // the distances do not depend on the threshold
    expect(requests(SUPPORT).at(-1)?.body).toEqual({ threshold: 30 });
    expect(paint).toHaveBeenCalledTimes(1);
    expect(supportStore.get().repaintMs).toBeGreaterThanOrEqual(0);
    expect(supportStore.get().counts!.beyond).toBeGreaterThan(countBeyond(distances(), 60).beyond);
    setThresholdText("−3");
    expect(Number.isNaN(thresholdOf(supportStore.get()) as number)).toBe(true);
    await applyThreshold();
    expect(supportStore.get().note).toContain("больше нуля");
  });
});

describe("the windows follow the model", () => {
  it("a model built again meanwhile: the table is read again, the map leaves the 3D window", async () => {
    setCurrentScene(fakeScene);
    await app.registry.execute("implicit.check", {}, "user");
    await app.registry.execute("implicit.support", { threshold: 60, wireframe_id: SURFACE.id }, "user");
    await waitFor(() => added.has("implicit-support"));
    const n = requests(CHECK).length;
    core.models[0] = { ...core.models[0], version: 9 };
    await loadRows();
    await waitFor(() => !added.has("implicit-support"));
    await waitFor(() => requests(CHECK).length === n + 1);
    expect(supportStore.get().shownId).toBeNull();
  });
  it("with BF_E2E the state of the windows is readable as window.__bfImplicitCheck (the E2E of 19.28)", async () => {
    setCurrentScene(fakeScene);
    (globalThis as { window?: unknown }).window = { bf: { flags: () => ({ e2e: true, softwareGl: false }) } };
    bindChecks(app);
    const hook = (globalThis as unknown as { window: { __bfImplicitCheck: Record<string, () => Record<string, unknown>> } }).window.__bfImplicitCheck;
    await app.registry.execute("implicit.check", {}, "user");
    expect(hook.check()).toMatchObject({ modelId: ID.model, phase: "ok", total: 3316, rows: 6, over: 0, shown: false });
    await app.registry.execute("implicit.support", { threshold: 60, wireframe_id: SURFACE.id }, "user");
    await waitFor(() => hook.support().layer !== null && (hook.support().layer as { vertices: number | null }).vertices !== null);
    expect(hook.support()).toMatchObject({ shownId: SURFACE.id, threshold: 60, layer: { vertices: SURFACE.vertices } });
    expect((hook.support().shares as Record<number, number>)[SURFACE.id]).toBeCloseTo(0.00481, 4);
    expect(hook.compare()).toMatchObject({ phase: "idle", share: null });
    expect(hook.fromModel()).toMatchObject({ busy: false, rows: null, error: null });
  });
  it("a model that is gone takes the window with it", async () => {
    await app.registry.execute("implicit.check", {}, "user");
    expect(windows("implicit-check")).toHaveLength(1);
    core.models.length = 0;
    await loadRows();
    expect(windows("implicit-check")).toHaveLength(0);
    expect(checkStore.get().modelId).toBeNull();
  });
});

describe("the colours of the map", () => {
  it("the same distance, the same colour: near is Cobalt, half way Light Stone, at D_оп Copper Orange, beyond it darker; no data is Slate", () => {
    const [r0, , b0] = supportColor(0, 60);
    expect(b0).toBeGreaterThan(r0); // blue
    const mid = supportColor(30, 60);
    expect(Math.abs(mid[0] - mid[2])).toBeLessThan(0.1); // a stone: grey-ish
    const at = supportColor(60, 60);
    expect(at[0]).toBeGreaterThan(at[2]);
    const beyond = supportColor(120, 60);
    expect(beyond[0] + beyond[1] + beyond[2]).toBeLessThan(at[0] + at[1] + at[2]); // darker
    const slate = supportColor(Number.NaN, 60);
    expect(NO_DATA_COLOR).toBe("#8a99a8");
    expect(slate[0]).toBeCloseTo(0x8a / 255, 5);
    expect(supportColor(10, 0)).toEqual(slate); // no threshold: no colour of a distance
    expect(supportColor(1e9, 60)).toEqual(supportColor(120, 60)); // the end of the ramp
  });
  it("all the vertices at once equal the colour of each; the array of the layer is reused", () => {
    const v = distances(500);
    const out = new Float32Array(1500);
    const got = colorsOf(v, 60, out);
    expect(got).toBe(out);
    for (const i of [0, 3, 77, 499]) expect([...got.slice(i * 3, i * 3 + 3)].map((x) => Math.fround(x))).toEqual(supportColor(v[i], 60).map((x) => Math.fround(x)));
    expect(countBeyond(v, 60)).toEqual({ beyond: v.filter((d) => d > 60).length, noData: 1 });
  });
  it("the surface of the core is drawn as it is: the box is absolute (the origin added back), an empty surface has none", () => {
    const m = meshAnswer(SURFACE.id);
    const d = meshData(readSupportMesh(m.body, JSON.stringify(m.head)).mesh);
    expect(d.positions).toHaveLength(SURFACE.vertices * 3);
    expect(d.origin).toEqual({ x: 200, y: 200, z: 250 });
    expect(d.bbox?.[3]).toBeGreaterThan(d.bbox?.[0] ?? Infinity);
    expect(d.bbox?.[0]).toBeCloseTo(200 - 65, 0);
    expect(meshData({ origin: { x: 1, y: 2, z: 3 }, positions: new Float32Array(0), index: new Uint32Array(0) }).bbox).toBeNull();
  });
  it("the frame of the repaint: 100 000 vertices", () => {
    const n = 100_000;
    const v = new Float32Array(n);
    for (let i = 0; i < n; i++) v[i] = (i * 37) % 130;
    const out = new Float32Array(n * 3);
    colorsOf(v, 60, out); // warm up
    const t0 = performance.now();
    for (let k = 0; k < 20; k++) colorsOf(v, 40 + k, out);
    const ms = (performance.now() - t0) / 20;
    // eslint-disable-next-line no-console
    console.log(`[замер 19.25] раскраска 100 тыс. вершин (colorsOf): ${ms.toFixed(2)} мс`);
    expect(ms).toBeLessThan(5); // 19.25б: the frame of the repaint of 100 thousand vertices
  });
  it("the frame of the repaint in the layer: the colours of 100 000 vertices go to the buffer of the layer (the upload to the card is the next frame's)", async () => {
    const { SupportView } = await import("../../src/viz/support/layer");
    const nx = 400;
    const ny = 250; // 100 000 vertices
    const n = nx * ny;
    const positions = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) positions.set([(i % nx) * 5, Math.floor(i / nx) * 5, Math.sin(i / 50) * 4], i * 3);
    const index = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let t = 0;
    for (let y = 0; y < ny - 1; y++)
      for (let x = 0; x < nx - 1; x++) {
        const a = y * nx + x;
        index.set([a, a + 1, a + nx, a + 1, a + nx + 1, a + nx], t);
        t += 6;
      }
    const v = new Float32Array(n);
    for (let i = 0; i < n; i++) v[i] = (i * 37) % 130;
    const rgb = colorsOf(v, 60);
    const layer = new SupportView({ origin: { x: 0, y: 0, z: 0 }, positions, index, bbox: null }, rgb, []);
    expect(layer.vertices).toBe(n);
    colorsOf(v, 50, rgb);
    layer.setColors(rgb); // warm up
    const t0 = performance.now();
    for (let k = 0; k < 20; k++) {
      colorsOf(v, 40 + k, rgb);
      layer.setColors(rgb);
    }
    const ms = (performance.now() - t0) / 20;
    // eslint-disable-next-line no-console
    console.log(`[замер 19.25] раскраска 100 тыс. вершин с передачей в буфер слоя: ${ms.toFixed(2)} мс`);
    expect(ms).toBeLessThan(5);
    expect(() => layer.setColors(new Float32Array(3))).toThrow(/цвет|colour/);
    layer.dispose();
  });
});
