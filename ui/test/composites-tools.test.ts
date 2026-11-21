// The windows «Кодировать каркасами», «Объединить таблицы», «Координаты проб», «Эквивалент металла» (ТЗ 8.2.1–8.2.3;
// ADR 0018 §6–7, §10–11, decisions 24–25; Э12 12.28): the defaults and ranges of table 8.2.2, the bodies in the order of
// priority with the open ones refused with a reason, «да / нет» of the division, the fields of a merge with the
// clashing names, the units and k of the equivalent (Т8.2-10), the reasons a tool is unavailable, the result tables
// are read-only. The core is a fetch stub where a request is made.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toolReason } from "../src/modules/composites/bind";
import { BodyList, CodingWindow } from "../src/modules/composites/CodingWindow";
import { EquivalentWindow, metalOf, rowsFromOthers, unitsOf } from "../src/modules/composites/EquivalentWindow";
import { CoordsWindow, IntervalTable, MergeWindow, previewColumns } from "../src/modules/composites/MergeWindow";
import { checkParam, defaultValues, NO_SOURCE, toolBody, valuesFromArgs } from "../src/modules/composites/params";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { TOOLS } from "../src/modules/composites/tools";
import { bodyReason, chosenProblem, coding, codingName, moveId, orderBodies, toBodies } from "../src/modules/composites/tools/coding";
import { COORD_COLUMNS, coords, coordsName } from "../src/modules/composites/tools/coords";
import { defaultPriceUnit, equivalent, equivalentValue, factorText, gradeUnit, kFactor, metalFactor, othersText, parseOthers, withUnits, type Metal } from "../src/modules/composites/tools/equivalent";
import { merge, mergedFields, mergeName } from "../src/modules/composites/tools/merge";
import { sceneObjects } from "../src/modules/scene/objects";
import { commands as tableCommands } from "../src/modules/tables/commands";
import { editCell, RESULT_TABLE_LOCKED, rowsLocked, tablesStore, type TabInfo } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import type { FloatWin } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const fetchMock = vi.fn(async () => json({ code: "not_found", message: "Нет", hint: "" }, 404));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  tablesStore.set({ setId: null, tabs: [], table: null });
  sceneObjects.set({ wireframes: [], loaded: false });
});

const win = (kind: string, args: Record<string, unknown> = {}): FloatWin => ({ id: `${kind}-1`, kind, title: "", x: 0, y: 0, w: 600, h: 500, z: 1, maximized: false, props: { args } });
const html = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(el);

describe("defaults and ranges (table 8.2.2)", () => {
  it("division on the boundary of a wireframe — «да» by default; recovery 0,9 within 0–1", () => {
    expect(defaultValues(coding.params)).toEqual({ split: true, name: "" });
    expect(valuesFromArgs(coding.params, { split: false }).split).toBe(false);
    const rec = equivalent.params.find((p) => p.key === "base_recovery")!;
    expect(defaultValues(equivalent.params).base_recovery).toBe("0,9");
    expect(checkParam(rec, "1,5")).toEqual({ ok: false, error: "«Извлечение основного»: допустимо 0–1" });
    expect(checkParam(rec, "0,85")).toEqual({ ok: true, value: 0.85 });
    expect(defaultValues(merge.params)).toEqual({ name: "" });
    expect(defaultValues(coords.params)).toEqual({ name: "" });
  });

  it("the four windows are the shell's: the renderer finds each", () => {
    for (const [id, testId] of [
      ["coding", "composites-coding"],
      ["merge", "composites-merge"],
      ["coords", "composites-coords"],
      ["equivalent", "composites-equivalent"],
    ]) {
      const el = renderCompositesWindow(win(`composites-${id}`, { set_id: 3 }));
      expect(el, id).not.toBeNull();
      expect(html(el as ReturnType<typeof createElement>)).toContain(`data-testid="${testId}"`);
    }
    expect(TOOLS.map((t) => t.id)).toContain("coords");
  });
});

describe("«Кодировать каркасами»", () => {
  const RAW = [
    { id: 1, name: "Тело А", stats: { closed: true } },
    { id: 2, name: "Тело Б", folder: "Рудные", stats: { closed: false, open_edges: 12, nonmanifold_edges: 1 } },
    { id: 3, name: "Рельеф", dtm: { kind: "grid" }, stats: { closed: false } },
    { id: 4, name: "Тело В", stats: {} },
  ];
  const bodies = toBodies(RAW);

  it("an open body or a DTM cannot be chosen and says why; a body not checked — the core checks it", () => {
    expect(bodyReason(RAW[0])).toEqual({ closed: true, reason: null });
    expect(bodies[1].reason).toBe("незамкнут: 12 открытых рёбер, 1 неманифолдное ребро — «Проверить каркас» и исправить");
    expect(bodies[2].reason).toBe("поверхность ЦТМ — не тело");
    expect(bodies[3]).toMatchObject({ closed: null, reason: null });
    expect(bodyReason({ id: 5, name: "x", stats: { closed: false, open_edges: 3 } }).reason).toContain("3 открытых ребра");
  });

  it("the chosen bodies in the order of priority, ↑/↓ move them, an open one blocks «Применить»", () => {
    expect(orderBodies(bodies, [4, 1]).map((b) => b.id)).toEqual([4, 1, 2, 3]);
    expect(moveId([4, 1, 7], 1, -1)).toEqual([1, 4, 7]);
    expect(moveId([4, 1, 7], 7, 1)).toEqual([4, 1, 7]);
    expect(chosenProblem(bodies, [1, 4])).toBeNull();
    expect(chosenProblem(bodies, [1, 2])).toBe("Каркас «Тело Б»: незамкнут: 12 открытых рёбер, 1 неманифолдное ребро — «Проверить каркас» и исправить");
    expect(chosenProblem(bodies, [9])).toContain("не найден");
    const list = html(createElement(BodyList, { bodies, chosen: [4, 1], onChange: () => undefined }));
    expect(list.indexOf("Тело В")).toBeLessThan(list.indexOf("Тело А"));
    expect(list).toMatch(/data-testid="composites-body-2"[^]*?disabled=""/);
    expect(list).toContain("Рудные › Тело Б");
    expect(list).toContain("Выше: «Тело А»");
  });

  it("the body of the job: the bodies in their order, the division as chosen; the name «‹Таблица› — коды»", () => {
    const src = { ...NO_SOURCE, setId: 3, table: "5", wireframeIds: [4, 1] };
    expect(toolBody(coding, src, { split: false, name: "" }).body).toEqual({ set_id: 3, table: "5", wireframe_ids: [4, 1], split: false });
    expect(toolBody(coding, { ...src, wireframeIds: [] }, defaultValues(coding.params)).reason).toBe("Отметьте каркасы");
    expect(codingName("Пробы")).toBe("Пробы — коды");
    const page = html(createElement(CodingWindow, { winId: "w", args: { set_id: 3 } }));
    expect(page).toMatch(/type="radio"[^>]*checked=""[^>]*\/>да — пробы режутся/);
    expect(page).toContain("нет — код по середине пробы и доля длины внутри (INSIDE_FRAC)");
  });
});

describe("«Объединить таблицы» and «Координаты проб»", () => {
  it("the fields of the result: a name met in two tables is marked; the name «Объединение: …»", () => {
    const f = mergedFields([
      { name: "Пробы", fields: ["AU", "CU"] },
      { name: "Литология", fields: ["LITH", "au"] },
    ]);
    expect(f.filter((x) => x.clash).map((x) => `${x.table}.${x.field}`)).toEqual(["Пробы.AU", "Литология.au"]);
    expect(mergeName(["Пробы", "Литология"])).toBe("Объединение: Пробы, Литология");
    expect(toolBody(merge, { ...NO_SOURCE, setId: 3, tables: ["5"] }, { name: "" }).reason).toBe("Отметьте не меньше двух таблиц");
    expect(toolBody(merge, { ...NO_SOURCE, setId: 3, tables: ["5", "6"] }, { name: "" }).body).toEqual({ set_id: 3, tables: ["5", "6"] });
  });

  it("rows of the preview: XB…ZE first, a missing value «—», never 0", () => {
    const cols = { from: [0, 1], to: [1, 2], values: { AU: [1.5, null], ZE: [95, 94], XB: [100, 100] } };
    expect(previewColumns(cols, COORD_COLUMNS)).toEqual(["XB", "ZE", "AU"]);
    const t = html(createElement(IntervalTable, { cols, first: COORD_COLUMNS, caption: "Скважина С-1" }));
    expect(t).toContain("<td>—</td>");
    expect(t.match(/<td>—<\/td>/g)).toHaveLength(1); // the one missing AU; the other cells are numbers
    expect(coordsName("Пробы")).toBe("Пробы — координаты");
    expect(html(createElement(CoordsWindow, { winId: "w" }))).toContain("XB, YB, ZB, XM, YM, ZM, XE, YE, ZE");
    expect(html(createElement(MergeWindow, { winId: "w" }))).toContain("Объединение: …");
  });
});

describe("«Эквивалент металла»", () => {
  const AU: Metal = { field: "AU", price: 2000, recovery: 0.9, unit: "г/т", priceUnit: "$/унц" };
  const CU: Metal = { field: "CU", price: 9000, recovery: 0.9, unit: "%", priceUnit: "$/т" };

  it("Т8.2-10: Au 1 г/т, Cu 1 %, 2000 $/унц, 9000 $/т, R = 0,9 → AuEq = 2,399656 г/т", () => {
    expect(equivalentValue(AU, [CU], { AU: 1, CU: 1 })!).toBeCloseTo(2.399656, 6);
    expect(kFactor("г/т", "$/унц")).toBeCloseTo(1 / 31.1034768, 15);
    expect(kFactor("%", "$/т")).toBeCloseTo(0.01, 15);
    expect(kFactor("ppb", "$/унц")).toBeCloseTo(0.001 / 31.1034768, 15);
    expect(kFactor("ppm", "$/унц")).toBe(kFactor("г/т", "$/унц"));
    expect(metalFactor(AU, CU)).toBeCloseTo(1.399656, 6);
    expect(factorText(AU, CU)).toMatch(/^1 % CU = 1,3996\d* г\/т AU-экв$/);
  });

  it("a missing grade gives a missing Eq unless «считать нулём»", () => {
    expect(equivalentValue(AU, [CU], { AU: 1, CU: null })).toBeNull();
    expect(equivalentValue(AU, [CU], { AU: 1, CU: null }, true)).toBe(1);
    expect(equivalentValue(AU, [CU], { AU: Number.NaN, CU: 1 })).toBeNull();
    expect(equivalentValue(AU, [CU], { AU: null, CU: 1 }, true)!).toBeCloseTo(1.399656, 6);
    // a sample without a single grade stays missing even with «считать нулём» (the core: NaN, never 0)
    expect(equivalentValue(AU, [CU], { AU: null, CU: Number.NaN }, true)).toBeNull();
    expect(equivalentValue(AU, [CU], {}, true)).toBeNull();
  });

  it("units: by the unit of the field, the price by the grade; given ones win", () => {
    expect(gradeUnit("g/t")).toBe("г/т");
    expect(gradeUnit(" % ")).toBe("%");
    expect(gradeUnit("ppb")).toBe("ppb");
    expect(gradeUnit("м")).toBeNull();
    expect(defaultPriceUnit("%")).toBe("$/т");
    expect(defaultPriceUnit("г/т")).toBe("$/унц");
    const fields = [{ key: "c1", name: "AU", kind: "number" as const, unit: "г/т" }, { key: "c2", name: "CU", kind: "number" as const, unit: "%" }];
    expect(unitsOf({ field: "CU", unit: null, priceUnit: null }, fields)).toEqual({ unit: "%", priceUnit: "$/т" });
    expect(unitsOf({ field: "CU", unit: null, priceUnit: "$/кг" }, fields)).toEqual({ unit: "%", priceUnit: "$/кг" });
    expect(unitsOf({ field: "ZN", unit: null, priceUnit: null }, fields)).toEqual({ unit: "г/т", priceUnit: "$/унц" });
    expect(metalOf({ field: "CU", price: "9 000", recovery: "0,9", unit: null, priceUnit: null }, fields)).toEqual(CU);
    expect(metalOf({ field: "CU", price: "0", recovery: "0,9", unit: null, priceUnit: null }, fields)).toBeNull();
  });

  it("the body of the job: others parsed, units of the window added; a price of zero never reaches the core", () => {
    const src = { ...NO_SOURCE, setId: 3, table: "5" };
    const values = { ...defaultValues(equivalent.params), base: "AU", base_price: "2000", others: "CU 9000 0,9" };
    const r = toolBody(equivalent, src, values);
    expect(r.body).toEqual({ set_id: 3, table: "5", base: "AU", base_price: 2000, base_recovery: 0.9, others: [{ field: "CU", price: 9000, recovery: 0.9 }], nan_zero: false });
    expect(withUnits(r.body!, { unit: "г/т", priceUnit: "$/унц" }, [{ field: "CU", unit: "%", priceUnit: "$/т" }])).toMatchObject({ base_unit: "г/т", base_price_unit: "$/унц", others: [{ field: "CU", unit: "%", price_unit: "$/т" }] });
    expect(toolBody(equivalent, src, { ...values, base_price: "0" }).reason).toBe("«Цена основного»: нужна цена больше нуля");
    expect(toolBody(equivalent, src, { ...values, others: "AU 2000 0,9" }).reason).toContain("основной металл не может быть среди прочих");
    expect(parseOthers("CU 9000 1,2")).toContain("извлечение 0–1");
    expect(othersText([{ field: "CU", price: "9000", recovery: "0,9" }, { field: "", price: "", recovery: "0,9" }])).toBe("CU 9000 0,9");
    expect(rowsFromOthers("CU 9000 0,9; AG 25 0,8").map((x) => x.field)).toEqual(["CU", "AG"]);
    const page = html(createElement(EquivalentWindow, { winId: "w", args: { set_id: 3, others: "CU 9000 0,9" } }));
    expect(page).toContain('data-testid="composites-metal-Основной"');
    expect(page).toContain('data-testid="composites-metal-Прочий 1"');
    expect(page).toContain("Добавить металл");
  });
});

describe("decisions 24–25", () => {
  let app: App;
  let hasSet = true;
  beforeEach(() => {
    hasSet = true;
    app = createApp({ persist: false });
    app.store.set({ projectName: "Проект", projectReadOnly: false });
  });
  const ctx = () => ({ hasProject: true, hasSet, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true });
  const tab = (over: Partial<TabInfo>): TabInfo => ({ table: "5", kind: "assay", name: "Пробы", rows: 10, rev: 1, ...over });

  it("a tool is unavailable with a reason: no active set, no sample tables, no closed wireframes", () => {
    expect(toolReason(app, coding, ctx())).toBeNull();
    hasSet = false;
    expect(toolReason(app, merge, ctx())).toBe("Нет активного набора скважин");
    hasSet = true;
    tablesStore.set({ tabs: [tab({ table: "collar", kind: "collar", name: "Устья" })] });
    expect(toolReason(app, coords, ctx())).toBe("В наборе нет таблиц проб");
    tablesStore.set({ tabs: [tab({})] });
    sceneObjects.set({ loaded: true, wireframes: [{ id: 2, name: "Б", dtm: null, stats: { closed: false } }] as never });
    expect(toolReason(app, coding, ctx())).toBe("В проекте нет замкнутых каркасов");
    expect(toolReason(app, coords, ctx())).toBeNull();
    app.store.set({ projectReadOnly: true });
    expect(toolReason(app, coding, ctx())).toBe("Проект открыт только для чтения");
  });

  it("a result table is read-only: cells and rows are not edited, no request is made", async () => {
    tablesStore.set({ setId: 3, table: "12", tabs: [tab({}), tab({ table: "12", kind: "interval", name: "Композиты 2 м", origin: { result_id: 9, op: "length", stale: false, reason: null } })] });
    expect(rowsLocked(tablesStore.get())).toBe(RESULT_TABLE_LOCKED);
    expect(RESULT_TABLE_LOCKED).toBe("Таблица — результат расчёта; правьте источник и перестройте");
    const before = fetchMock.mock.calls.length;
    expect(await editCell({ id: 1, hole_id: 1, hole: "С-1" }, "c1", "2")).toBe(RESULT_TABLE_LOCKED);
    expect(fetchMock.mock.calls.length).toBe(before);
    tablesStore.set({ table: "5" });
    expect(rowsLocked(tablesStore.get())).toBeNull();
    expect(tableCommands.find((c) => c.id === "table.append")?.title).toBe("Добавить строки из таблицы");
  });
});
