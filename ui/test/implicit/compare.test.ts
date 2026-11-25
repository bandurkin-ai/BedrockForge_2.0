// «Сверка данных и модели» и «Модель на скважины» (Э19, 19.25; ТЗ 7.35 Д-33, Д-34 ч. 1; docs/progress/e19-api.md §8.3, §8.4) on the REAL answers
// of the core (`contract_answers.json`: `compare_holes`, `compare_bad`, `from_model_*`): the readers, the matrix and the list of the mismatches,
// the command `implicit.compare_holes` and its window, the way to the window «Данные по скважинам», the command `drillholes.from_model` and its
// dialog (the new table, the name that is taken, the codes of the bodies, the field of a block model).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { readCompare, readFromModelResult } from "../../src/modules/implicit/compareApi";
import { cellValue, colSum, compareLine, facts, isDiagonal, mismatchRange, remarks, rowSum, shareText, valueText, weight } from "../../src/modules/implicit/compareModel";
import { COMPARE_WINDOW, compareStore, goPage, gotoHole, openCompare, resetCompare, runCompare } from "../../src/modules/implicit/CompareState";
import { CompareWindow } from "../../src/modules/implicit/CompareWindow";
import { bodyOf, codesLine, codesText, complete, fromStore, parseCodes, resetFrom, resultLine, setFrom, argsOf } from "../../src/modules/implicit/FromModelState";
import { FromModelDialog } from "../../src/modules/implicit/FromModelDialog";
import { loadRows } from "../../src/modules/implicit/state";
import { FIELD, ID, newApp, teardown, waitFor } from "./harness";
import { installCheckCore, R, type CheckCore } from "./checkHarness";

let app: App;
let core: CheckCore;
const picked: unknown[] = [];

beforeEach(async () => {
  core = installCheckCore();
  app = newApp();
  picked.length = 0;
  app.registry.bind("hole.select", (a) => void picked.push({ select: a }));
  app.registry.bind("holes.data_window", () => void picked.push("window"));
  resetCompare();
  resetFrom();
  await loadRows();
});
afterEach(() => {
  resetCompare();
  resetFrom();
  teardown();
});

const flat = (s: string): string => s.replace(/[  ]/g, " ");
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const COMPARE = `POST /implicit/models/${ID.model}/compare_holes`;
const FROM = "POST /drillholes/from_model";
const render = (el: ReturnType<typeof createElement>) => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el)));
const windows = (kind: string) => windowStore.get().windows.filter((w) => w.kind === kind);

describe("the comparison: the answer of the core as it is", () => {
  const a = readCompare(R.compare.body);
  it("the codes of the data are the rows, the codes of the model the columns, «Прочее» and «Вне модели» are the last two", () => {
    expect(a.model_codes).toEqual(["INT", "SS", "Прочее", "Вне модели"]);
    expect(a.other_col).toBe(2);
    expect(a.outside_col).toBe(3);
    expect(a.data_codes).toEqual(["ALV", "GR", "INT", "SH", "SS", "VN"]);
    expect(a.no_code_row).toBeNull(); // no interval without a code: no row of «Нет кода»
    expect(a.length).toHaveLength(6);
    expect(a.length[0]).toHaveLength(4);
    expect(a.table).toMatchObject({ name: "Литология", field: "CODE" });
    expect(a.mismatches.rows).toHaveLength(6);
    expect(a.mismatches.total).toBe(250);
  });
  it("the diagonal is what agrees (INT↔INT, SS↔SS); the sums of the rows are the lengths of the codes of the data", () => {
    expect(isDiagonal(a, 2, 0)).toBe(true);
    expect(isDiagonal(a, 4, 1)).toBe(true);
    expect(isDiagonal(a, 0, 2)).toBe(false);
    expect(cellValue(a, "length", 2, 0)).toBeGreaterThan(0);
    const diag = a.data_codes.reduce((s, _c, r) => s + a.model_codes.reduce((x, _m, c) => x + (isDiagonal(a, r, c) ? (a.length[r][c] ?? 0) : 0), 0), 0);
    expect(diag).toBeCloseTo(a.agree_length as number, 6);
    const all = a.data_codes.reduce((s, _c, r) => s + rowSum(a, "length", r), 0);
    expect(all).toBeCloseTo(a.total_length as number, 6);
    expect(a.model_codes.reduce((s, _m, c) => s + colSum(a, "length", c), 0)).toBeCloseTo(all, 6);
    expect((a.agree_share as number) * (a.coded_length as number)).toBeCloseTo(a.agree_length as number, 6);
    expect(weight(a, "length", 0, 2)).toBeGreaterThan(0);
    expect(weight(a, "length", 0, 0)).toBe(0);
  });
  it("a gap stays a gap: «—», a true zero is a dot, never the other way", () => {
    expect(valueText(null, "length")).toBe("—");
    expect(valueText(0, "length")).toBe("·");
    expect(flat(valueText(1234.5, "length"))).toBe("1 234,5");
    expect(valueText(41, "count")).toBe("41");
    const raw = JSON.parse(JSON.stringify(R.compare.body));
    raw.length[0][2] = null;
    raw.agree_share = null;
    const b = readCompare(raw);
    expect(cellValue(b, "length", 0, 2)).toBeNull();
    expect(shareText(b.agree_share)).toBe("—");
  });
  it("the facts, the remarks and the line for the assistant", () => {
    expect(facts(a).map((f) => f.key)).toEqual(["share", "agree", "total", "mismatch", "intervals"]);
    expect(flat(facts(a).find((f) => f.key === "share")!.value)).toBe("35,88 %");
    expect(flat(facts(a).find((f) => f.key === "mismatch")!.value)).toBe("250");
    expect(remarks(a)).toEqual([]);
    const line = flat(compareLine(a));
    expect(line).toContain("Модель «Литология» и таблица «Литология» (поле CODE): совпадение 35,88 % длины");
    expect(line).toContain("не совпало интервалов 250");
    expect(line).toContain("H096 0–59,33 м: данные ALV, модель Прочее");
    expect(mismatchRange(a.mismatches.rows[0])).toBe("0–59,33");
    expect(remarks({ ...a, no_code: { count: 2, length: 4.5 }, no_trajectory: { holes: 1, count: 3, length: 7, }, notes: ["Нет траектории"] }).join(" ")).toContain("без кода");
  });
});

describe("implicit.compare_holes", () => {
  it("without the table: the window opens; the set, the table and the field of the data of the model are taken and counted", async () => {
    const r = await app.registry.execute("implicit.compare_holes", {}, "claude");
    expect(r.ok).toBe(true);
    expect(windows(COMPARE_WINDOW)).toHaveLength(1);
    expect(compareStore.get()).toMatchObject({ modelId: ID.model, setId: 3, table: ID.table, field: FIELD }); // `data` of the model of the contract
    expect(requests(COMPARE)[0].body).toMatchObject({ set_id: 3, table: ID.table, field: FIELD });
    expect(flat(String(r.result))).toContain("совпадение 35,88 % длины");
  });
  it("a model with no data to take: the window waits for the choice and says what to choose", async () => {
    await openCompare(ID.model, { setId: null });
    compareStore.set({ setId: null, table: null, field: null });
    expect(compareStore.get().setId).toBeNull();
    const r = await runCompare();
    expect(r).toBeNull();
    expect(compareStore.get().error).toContain("Выберите набор скважин, таблицу кодов и поле кода");
    expect(render(createElement(CompareWindow, { winId: "w" }))).toContain('data-testid="cmp-error"');
    expect(requests(COMPARE)).toHaveLength(0);
  });
  it("with the table and the field: the matrices are counted; the text is the share and the first mismatches", async () => {
    const r = await app.registry.execute("implicit.compare_holes", { set_id: ID.set, table: ID.table, field: FIELD }, "claude");
    expect(r.ok).toBe(true);
    expect(requests(COMPARE)[0].body).toEqual({ set_id: ID.set, table: ID.table, field: FIELD, page: 1, size: 100 });
    const text = flat(String(r.result));
    expect(text).toContain("совпадение 35,88 % длины");
    expect(text).toContain("самые длинные: H096");
    expect(compareStore.get().phase).toBe("ok");
  });
  it("the window shows the matrix with its diagonal, the facts and the list; the zero is a dot", async () => {
    await app.registry.execute("implicit.compare_holes", { set_id: ID.set, table: ID.table, field: FIELD }, "user");
    const html = render(createElement(CompareWindow, { winId: "w" }));
    for (const t of ["Совпадение", "35,88 %", "Не совпало, интервалов", "INT", "SS", "Прочее", "Вне модели", "ALV", "VN"]) expect(html, t).toContain(t);
    expect(html).toContain('data-testid="cmp-cell-2-0"');
    expect(html.match(/data-agree="true"/g)).toHaveLength(2);
    expect(html).toContain("·");
    expect(html).toContain('data-testid="cmp-row-0"');
    expect(html).toContain("H096");
    expect(html).toContain("Страница 1 из 42"); // 250 intervals, the page of the recorded answer is 6
    expect(html).toContain("К скважине");
  });
  it("the list goes by pages and the size is a new request", async () => {
    await app.registry.execute("implicit.compare_holes", { set_id: ID.set, table: ID.table, field: FIELD }, "user");
    await goPage(2);
    expect(requests(COMPARE)[1].body.page).toBe(2);
    compareStore.set({ size: 250 });
    await runCompare(1);
    expect(requests(COMPARE)[2].body).toMatchObject({ page: 1, size: 250 });
  });
  it("the way to 7.17: the hole is selected, the window «Данные по скважинам» is opened, the depth is told", async () => {
    await app.registry.execute("implicit.compare_holes", { set_id: ID.set, table: ID.table, field: FIELD }, "user");
    const m = compareStore.get().answer!.mismatches.rows[0];
    const text = flat(await gotoHole(app, m));
    expect(picked).toEqual([{ select: { hole_id: "H096", set_id: ID.set } }, "window"]);
    expect(text).toContain("H096");
    expect(text).toContain("0–59.33");
    expect(text).toContain("глубина середины 29,67 м");
    expect(flat(compareStore.get().note ?? "")).toBe(text);
  });
  it("a refusal of the core stands under the bar (404 table_not_found)", async () => {
    core.force(COMPARE, R.compare_bad);
    const r = await app.registry.execute("implicit.compare_holes", { set_id: ID.set, table: "нет такой", field: FIELD }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Таблица не найдена");
    expect(compareStore.get().phase).toBe("error");
    expect(render(createElement(CompareWindow, { winId: "w" }))).toContain("cmp-error");
  });
  it("a model that is not there says so", async () => {
    expect((await app.registry.execute("implicit.compare_holes", { model_id: 404 }, "claude")).result).toContain("не найдена");
  });
});

describe("the request of «Модель на скважины»", () => {
  it("the arguments are flat: the numbers of the wireframes and the codes are text; one source at a time", () => {
    expect(bodyOf({ set_id: 3, name: "Т", model_id: 7, codes: "1=Дайка; 3=Песчаник" })).toEqual({ set_id: 3, name: "Т", model_id: 7, codes: { "1": "Дайка", "3": "Песчаник" } });
    expect(bodyOf({ set_id: 3, name: "Т", mesh_ids: "50; 52" })).toEqual({ set_id: 3, name: "Т", mesh_ids: [50, 52] });
    expect(bodyOf({ set_id: 3, name: "Т", block_model_id: 21, field: "AU", table: "Литология" })).toEqual({ set_id: 3, name: "Т", block_model_id: 21, field: "AU", table: "Литология" });
    expect(() => bodyOf({ set_id: 3, name: "Т" })).toThrow(/одно/);
    expect(() => bodyOf({ set_id: 3, name: "Т", model_id: 7, mesh_ids: "50" })).toThrow(/одно/);
    expect(() => bodyOf({ set_id: 3, name: "Т", block_model_id: 21 })).toThrow(/field и table/);
    expect(() => parseCodes("1=Дайка; Песчаник")).toThrow(/номер=код/);
    expect(parseCodes("")).toEqual({});
    expect(codesText({ "1": "Дайка", "3": " ", "5": "Руда" })).toBe("1=Дайка; 5=Руда");
  });
  it("a command that has everything writes; one that does not opens the dialog", () => {
    expect(complete({ set_id: 3, name: "Т", model_id: 7 })).toBe(true);
    expect(complete({ set_id: 3, name: "Т", mesh_ids: "50;51" })).toBe(true);
    expect(complete({ set_id: 3, name: "  ", model_id: 7 })).toBe(false);
    expect(complete({ set_id: 3, name: "Т", block_model_id: 21, field: "AU" })).toBe(false);
    expect(complete({ name: "Т", model_id: 7 })).toBe(false);
  });
  it("the form of the dialog turns into the same arguments", () => {
    fromStore.set({ setId: 3, name: " Т ", source: "meshes", meshIds: [50, 52], codes: { "50": "Руда" } });
    expect(argsOf(fromStore.get())).toEqual({ set_id: 3, name: "Т", mesh_ids: "50; 52", codes: "50=Руда" });
    fromStore.set({ source: "field", blockId: 21, field: "AU", table: "Литология" });
    expect(argsOf(fromStore.get())).toEqual({ set_id: 3, name: "Т", block_model_id: 21, field: "AU", table: "Литология" });
  });
});

describe("drillholes.from_model", () => {
  it("the result of the core is read: the rows, the holes, the codes with their lengths, the holes without a trajectory", () => {
    const r = readFromModelResult(R.from_result.body)!;
    expect(r).toMatchObject({ name: "Модель на скважины", rows: 306, holes: 100, no_trajectory: 0, mode: "bodies" });
    expect(r.codes.map((c) => c.code)).toEqual(["INT", "SS"]);
    expect(r.length_inside).toBeGreaterThan(0);
    expect(flat(resultLine(r))).toContain("Таблица «Модель на скважины» записана в набор: строк 306, скважин 100");
    expect(flat(codesLine(r))).toMatch(/INT — 5 инт\., 252,7 м; SS — 100 инт\., 4 067,6 м/);
    expect(readFromModelResult({ rows: 3 })).toBeNull();
  });
  it("without arguments the dialog opens; the arguments prefill it", async () => {
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model }, "user");
    expect(r.ok).toBe(true);
    expect(String(r.result)).toContain("Открыт диалог «Модель на скважины»");
    expect(windowStore.get().windows.filter((w) => w.kind === "implicit-from-model")).toHaveLength(1);
    expect(fromStore.get()).toMatchObject({ setId: ID.set, modelId: ID.model, source: "model" });
    expect(requests(FROM)).toHaveLength(0);
    const m = await app.registry.execute("drillholes.from_model", { mesh_ids: "50;52", name: "Каркасы" }, "user");
    expect(m.ok).toBe(true);
    expect(fromStore.get()).toMatchObject({ source: "meshes", meshIds: [50, 52], name: "Каркасы", nameTouched: true });
  });
  it("with the whole request: the table is written, the job followed, the result in the dialog and in the text", async () => {
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, name: "Модель на скважины", codes: "1=Дайка" }, "claude");
    expect(r.ok).toBe(true);
    expect(requests(FROM)[0].body).toEqual({ set_id: ID.set, name: "Модель на скважины", model_id: ID.model, codes: { "1": "Дайка" } });
    expect(flat(String(r.result))).toContain("Таблица «Модель на скважины» записана в набор: строк 306, скважин 100");
    expect(fromStore.get().result?.rows).toBe(306);
    expect(fromStore.get().busy).toBe(false);
    const html = render(createElement(FromModelDialog, { winId: "w" }));
    expect(html).toContain('data-testid="fm-result"');
    expect(html).toContain("INT — 5 инт.");
  });
  it("a name that is taken: 409 with the free name; it stands under the field, the table is not replaced", async () => {
    core.taken = ["Модель на скважины"];
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, name: "Модель на скважины" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("уже есть таблица");
    expect(r.result).toContain("Свободное название: «Модель на скважины (2)»");
    expect(fromStore.get().suggested).toBe("Модель на скважины (2)");
    expect(fromStore.get().error?.code).toBe("table_exists");
    expect(fromStore.get().busy).toBe(false);
    fromStore.set({ setId: ID.set, name: "Модель на скважины", nameTouched: true, source: "model", modelId: ID.model });
    const html = render(createElement(FromModelDialog, { winId: "w" }));
    expect(html).toContain('data-testid="fm-name-taken"');
    expect(html).toContain("Взять «Модель на скважины (2)»");
    expect(html).toContain('aria-invalid="true"');
  });
  it("a refusal of the core (422 bad_param) is words under the dialog; a job that failed is a refusal", async () => {
    const bad = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, mesh_ids: "50", name: "Т" }, "claude");
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Выберите одно");
    core.force(FROM, R.from_bad);
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, name: "Т2" }, "claude");
    expect(r.ok).toBe(false);
    expect(fromStore.get().error?.param).toBe("model_id");
    core.job = { status: "failed", result: null, error: { code: "no_bodies", message: "В модели нет тел для кодирования.", hint: "Постройте модель." } };
    const f = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, name: "Т3" }, "claude");
    expect(f.ok).toBe(false);
    expect(f.result).toContain("нет тел");
    expect(fromStore.get().error?.code).toBe("no_bodies");
  });
  it("the field of a block model: the request carries the block model, the field and the table", async () => {
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, block_model_id: 21, field: "AU", table: "Литология", name: "AU по блокам" }, "claude");
    expect(r.ok).toBe(true);
    expect(requests(FROM)[0].body).toEqual({ set_id: ID.set, name: "AU по блокам", block_model_id: 21, field: "AU", table: "Литология" });
  });
  it("a project open for reading only: the command says so and writes nothing", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("drillholes.from_model", { set_id: ID.set, model_id: ID.model, name: "Т" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Проект открыт только для чтения");
    expect(requests(FROM)).toHaveLength(0);
  });
  it("the dialog: the sources, the groups of the model by the priority, the name that follows the source", async () => {
    setFrom({ setId: ID.set, modelId: ID.model, source: "model" });
    await waitFor(() => true);
    let html = render(createElement(FromModelDialog, { winId: "w" }));
    for (const t of ["Тела неявной модели", "Замкнутые каркасы", "Поле блочной модели", "Название новой таблицы", "Записать"]) expect(html, t).toContain(t);
    expect(html).toContain("Литология — на скважинах"); // the default of the core for a model
    setFrom({ source: "field" });
    html = render(createElement(FromModelDialog, { winId: "w" }));
    expect(html).toContain('data-testid="fm-block"');
    setFrom({ source: "meshes", meshIds: [] });
    html = render(createElement(FromModelDialog, { winId: "w" }));
    expect(html).toContain('data-testid="fm-mesh-50"'); // the closed solid of the project; the open one and the DTM are not offered
    expect(html).not.toContain('data-testid="fm-mesh-51"');
    expect(html).not.toContain('data-testid="fm-mesh-41"');
    expect(dialogStore.get().queue).toHaveLength(0); // no window.confirm / alert: own windows only
  });
});
