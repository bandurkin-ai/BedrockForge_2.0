import { describe, expect, it } from "vitest";
import { cellText, nextSort, PAGE, resolveTable, rowAt, type TabInfo, type TablesState } from "../src/modules/tables/state";

describe("«Таблица» panel state (ТЗ 7.10)", () => {
  it("header click: ascending → descending → off; another column starts ascending", () => {
    let s = { sort: null as string | null, desc: false };
    s = nextSort(s, "au");
    expect(s).toEqual({ sort: "au", desc: false });
    s = nextSort(s, "au");
    expect(s).toEqual({ sort: "au", desc: true });
    s = nextSort(s, "au");
    expect(s).toEqual({ sort: null, desc: false });
    expect(nextSort({ sort: "au", desc: true }, "cu")).toEqual({ sort: "cu", desc: false });
  });

  it("tabs by kind or id; cells with a decimal comma, missing stays empty (never 0)", () => {
    const tabs: TabInfo[] = [
      { table: "collar", kind: "collar", name: "Устья", rows: 2, rev: 1 },
      { table: "7", kind: "assay", name: "Пробы", rows: 9, rev: 3 },
    ];
    expect(resolveTable(tabs, "assay")).toBe("7");
    expect(resolveTable(tabs, "7")).toBe("7");
    expect(resolveTable(tabs, "survey")).toBeNull();
    expect(cellText(12.5, "number")).toBe("12,5");
    expect(cellText(1234567.25, "number")).toBe("1234567,25");
    expect(cellText(null, "number")).toBe("");
    expect(cellText(0, "number")).toBe("0");
    expect(cellText("гранит", "text")).toBe("гранит");
  });

  it("rows come from the page that holds them", () => {
    const row = (i: number) => ({ id: i, hole_id: 1, hole: "D1" });
    const s = { pages: { 0: [row(0), row(1)], [PAGE]: [row(PAGE)] } } as unknown as TablesState;
    expect(rowAt(s, 1)?.id).toBe(1);
    expect(rowAt(s, PAGE)?.id).toBe(PAGE);
    expect(rowAt(s, PAGE + 5)).toBeUndefined();
  });
});
