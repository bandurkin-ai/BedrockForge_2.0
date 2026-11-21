// The filter of QAQC (ADR 0020 §5): the stored form `v: 1`, the edit and the reset, the body sent to the core
// (only the conditions set), the arguments of `qaqc.filter` (lists as text or JSON, dates in two forms, conditions
// «любое поле» up to 20), the label «записей N из M»; the state of the tab: the key of a calculation, the counts of
// the current key only, an overtaken answer dropped, another project. Pure.
import { afterEach, describe, expect, it } from "vitest";
import type { QaqcData } from "../src/modules/qaqc/api";
import { conditionCount, countLabel, EMPTY_FILTER, filterFromArgs, filterJson, filterKey, filterSummary, isEmptyFilter, listArg, MAX_WHERE, normalizeFilter, parseDate, patchFilter, toggleValue, type QaqcFilter } from "../src/modules/qaqc/filters";
import { activeData, activeElement, calcBody, calcKey, currentCounts, DROPPED, initialQaqc, latest, qaqcStore, reportCounts, resetQaqc, setFilter, setSection } from "../src/modules/qaqc/state";

const data = (id: number, elements: string[], version = 1): QaqcData => ({
  id, name: `Данные ${id}`, version, rows: 100, elements: elements.map((name) => ({ name, unit: "г/т" })), fields: [], labs: ["ALS"], batches: [], date_from: null, date_to: null,
  type_codes: [], source: null, stale: false, sha256: "x", counts: {},
});

const ok = (f: QaqcFilter | string): QaqcFilter => {
  if (typeof f === "string") throw new Error(f);
  return f;
};

afterEach(() => qaqcStore.set(initialQaqc()));

describe("the stored filter", () => {
  it("garbage gives the empty filter; what is valid is kept (types upper-case, dates as ISO, distinct values)", () => {
    expect(normalizeFilter(null)).toEqual(EMPTY_FILTER);
    expect(normalizeFilter([1])).toEqual(EMPTY_FILTER);
    const f = normalizeFilter({ v: 1, labs: ["ALS", " ALS ", "", 5, null], types: ["crm", "XXX", "Blank"], date_from: "01.03.2024", date_to: "2024-02-30", hole_prefix: "  ", where: [{ field: "AU", op: "ge", value: 1 }, { field: "", op: "eq", value: 1 }, { field: "X", op: "lt", value: 1 }] });
    expect(f).toEqual({ ...EMPTY_FILTER, labs: ["ALS", "5"], types: ["CRM", "BLANK"], date_from: "2024-03-01", where: [{ field: "AU", op: "ge", value: 1 }] });
    expect(isEmptyFilter(EMPTY_FILTER)).toBe(true);
    expect(isEmptyFilter(f)).toBe(false);
    expect(conditionCount(f)).toBe(4);
  });

  it("dates: ISO, «ДД.ММ.ГГГГ», «ДД/ММ/ГГГГ», a time after them; a wrong day — none", () => {
    expect(parseDate("2024-3-5")).toBe("2024-03-05");
    expect(parseDate("05.03.2024 10:30")).toBe("2024-03-05");
    expect(parseDate("05/03/2024")).toBe("2024-03-05");
    expect(parseDate("31.02.2024")).toBeNull();
    expect(parseDate("вчера")).toBeNull();
    expect(parseDate(20240305)).toBeNull();
  });

  it("an edit keeps the other conditions; a value toggles; the reset clears all", () => {
    const f = patchFilter(EMPTY_FILTER, { labs: ["ALS"], crm: ["OREAS 45"] });
    expect(patchFilter(f, { labs: [] })).toEqual({ ...EMPTY_FILTER, crm: ["OREAS 45"] });
    expect(toggleValue(["a", "b"], "a")).toEqual(["b"]);
    expect(toggleValue(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleValue(["a"], "a", true)).toEqual(["a"]);
    expect(ok(filterFromArgs(f, { reset: true }))).toEqual(EMPTY_FILTER);
    expect(ok(filterFromArgs(f, { reset: true, batches: "B1" }))).toEqual({ ...EMPTY_FILTER, batches: ["B1"] });
  });

  it("the body of the core is `v: 1` with only the conditions set; equal filters give one key", () => {
    expect(filterJson(EMPTY_FILTER)).toEqual({ v: 1 });
    const f = patchFilter(EMPTY_FILTER, { labs: ["ALS"], date_to: "2024-05-01", hole_prefix: "ДД", where: [{ field: "AU", op: "between", value: [1, 2] }] });
    expect(filterJson(f)).toEqual({ v: 1, labs: ["ALS"], date_to: "2024-05-01", hole_prefix: "ДД", where: [{ field: "AU", op: "between", value: [1, 2] }] });
    expect(filterKey(normalizeFilter(JSON.parse(JSON.stringify(f))))).toBe(filterKey(f));
  });
});

describe("the arguments of `qaqc.filter`", () => {
  it("lists: comma-separated text or a JSON array (a name with a comma); empty text — no condition", () => {
    expect(listArg("ALS, SGS ,")).toEqual(["ALS", "SGS"]);
    expect(listArg('["ALS, Чита", "SGS"]')).toEqual(["ALS, Чита", "SGS"]);
    expect(listArg("[oops")).toMatch(/JSON/);
    const f = ok(filterFromArgs(EMPTY_FILTER, { labs: '["ALS, Чита"]', types: "crm, blank", crm: "OREAS 45" }));
    expect(f).toMatchObject({ labs: ["ALS, Чита"], types: ["CRM", "BLANK"], crm: ["OREAS 45"] });
    expect(ok(filterFromArgs(f, { crm: "" }))).toMatchObject({ labs: ["ALS, Чита"], crm: [] });
  });

  it("refusals: an unknown type, a wrong date, «с» after «по», more than 20 conditions, a wrong condition", () => {
    expect(filterFromArgs(EMPTY_FILTER, { types: "CRM, STD" })).toMatch(/Тип «STD» неизвестен/);
    expect(filterFromArgs(EMPTY_FILTER, { date_from: "31.02.2024" })).toMatch(/не разобрана/);
    expect(filterFromArgs(EMPTY_FILTER, { date_from: "2024-05-01", date_to: "01.04.2024" })).toBe("Дата «с» (01.05.2024) позже даты «по» (01.04.2024)");
    const many = JSON.stringify(Array.from({ length: MAX_WHERE + 1 }, () => ({ field: "AU", op: "empty" })));
    expect(filterFromArgs(EMPTY_FILTER, { where: many })).toBe("Условий «любое поле» не больше 20");
    expect(filterFromArgs(EMPTY_FILTER, { where: '[{"field": "AU", "op": "between", "value": [1]}]' })).toMatch(/между/);
    expect(filterFromArgs(EMPTY_FILTER, { where: "{" })).toMatch(/не разобраны/);
  });

  it("conditions «любое поле»: text and range operations; empty text removes them; dates are cleared by empty text", () => {
    const f = ok(filterFromArgs(EMPTY_FILTER, { where: '[{"field": "x:PROJECT", "op": "in", "value": ["A", "B"]}, {"field": "AU", "op": "empty"}]', date_from: "2024-01-01" }));
    expect(f.where).toEqual([{ field: "x:PROJECT", op: "in", value: ["A", "B"] }, { field: "AU", op: "empty" }]);
    const g = ok(filterFromArgs(f, { where: "", date_from: "" }));
    expect(g.where).toEqual([]);
    expect(g.date_from).toBeNull();
  });

  it("the counter and the summary in Russian; a missing count is «…», never 0", () => {
    expect(countLabel(1234, 5000)).toBe(`записей ${(1234).toLocaleString("ru-RU")} из ${(5000).toLocaleString("ru-RU")}`);
    expect(countLabel(null, 10)).toBe("записей … из 10");
    expect(filterSummary(EMPTY_FILTER)).toBe("без фильтра");
    const f = patchFilter(EMPTY_FILTER, { labs: ["A", "B", "C", "D"], date_from: "2024-01-01", types: ["CRM"], where: [{ field: "AU", op: "ge", value: 1 }] });
    expect(filterSummary(f)).toBe("лаборатории: A, B, C и ещё 1; даты: с 01.01.2024; типы: CRM; AU не меньше 1");
  });
});

describe("the state of the tab", () => {
  it("the first data and element by default; the element in any case; the key follows data, element, filter", () => {
    qaqcStore.set({ datas: [data(4, ["AU", "AG"]), data(7, ["CU"])] });
    expect(activeData()?.id).toBe(4);
    expect(activeElement()).toBe("AU");
    qaqcStore.set({ element: "ag" });
    expect(activeElement()).toBe("AG");
    qaqcStore.set({ dataId: 7 });
    expect(activeElement()).toBe("CU"); // AG is not in these data
    const key = calcKey();
    setFilter(patchFilter(EMPTY_FILTER, { labs: ["ALS"] }));
    expect(calcKey()).not.toBe(key);
    expect(calcBody()).toEqual({ data_id: 7, element: "CU", filter: { v: 1, labs: ["ALS"] } });
    qaqcStore.set({ datas: [] });
    expect(calcKey()).toBeNull();
    expect(calcBody()).toBeNull();
  });

  it("the counter shows the counts of the current key only", () => {
    qaqcStore.set({ datas: [data(4, ["AU"])] });
    reportCounts(calcKey()!, { rows: 100, filtered: 40, control: 30, unmapped: 0, missing: 1 });
    expect(currentCounts()?.filtered).toBe(40);
    setFilter(patchFilter(EMPTY_FILTER, { labs: ["SGS"] }));
    expect(currentCounts()).toBeNull();
  });

  it("an overtaken answer is dropped, its failure too; another project drops every request", async () => {
    let release: (v: string) => void = () => {};
    const slow = latest("standards", () => new Promise<string>((r) => (release = r)));
    const fast = latest("standards", () => Promise.resolve("new"));
    expect(await fast).toBe("new");
    release("old");
    expect(await slow).toBe(DROPPED);
    const failing = latest("blanks", () => Promise.reject(new Error("old")));
    const newer = latest("blanks", () => Promise.resolve(1));
    expect(await failing).toBe(DROPPED);
    expect(await newer).toBe(1);
    let late: (v: number) => void = () => {};
    const pending = latest("x", () => new Promise<number>((r) => (late = r)));
    setSection("batches");
    qaqcStore.set({ datas: [data(1, ["AU"])], dataId: 1, filter: patchFilter(EMPTY_FILTER, { labs: ["A"] }) });
    resetQaqc();
    late(5);
    expect(await pending).toBe(DROPPED);
    expect(qaqcStore.get()).toMatchObject({ datas: null, dataId: null, filter: EMPTY_FILTER, section: "batches" });
  });
});
