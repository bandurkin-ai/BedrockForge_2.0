import { describe, expect, it } from "vitest";
import { createApp } from "../src/shell/app";
import type { SeriesRow, SeriesTable } from "../src/modules/section/seriesApi";
import { seriesMenu, seriesTreeRows, TREE_SECTIONS_MAX } from "../src/modules/section/SeriesTree";

function row(id: number, extra: Partial<SeriesRow["params"]> = {}, name = "Профили", count = 5): SeriesRow {
  return {
    id, name, folder: null, version: 1, number: id, count, created_at: "", updated_at: "",
    params: { type: "vertical", p1: [1000, 5000, 0], p2: null, az: 90, phi: 90, s0: 0, s1: (count - 1) * 25, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null, ...extra },
  };
}
const tableOf = (n: number): SeriesTable => ({ version: 1, count: n, sections: Array.from({ length: n }, (_, k) => ({ k, name: `${5000 + 25 * k}N`, s: 25 * k, origin: [1000, 5000 + 25 * k, 0], towards: 12.5, away: 12.5 })) });

describe("the tree «Серии разрезов»", () => {
  it("series, their sections with names, the current one marked; a polyline — its segments", () => {
    const poly = row(2, { type: "polyline", polyline: [[0, 0], [100, 0], [100, 100]], corridor: 10, az: null, s0: 0, s1: 0 }, "Ломаный", 1);
    const state = { list: [row(1), poly, row(3, {}, "Ещё")], tables: { 1: tableOf(5) } };
    const rows = seriesTreeRows(state, new Set([1, 2]), { id: 1, k: 2, seg: null });
    expect(rows.map((r) => (r.kind === "series" ? `S${r.row.id}${r.active ? "*" : ""}` : r.kind === "section" ? `${r.label}${r.active ? "*" : ""}` : r.kind))).toEqual([
      "S1*", "1. 5000N", "2. 5025N", "3. 5050N*", "4. 5075N", "5. 5100N", "S2", "Сегмент 1", "Сегмент 2", "S3",
    ]);
    // the table not read yet: one row says so
    expect(seriesTreeRows({ list: [row(1)], tables: {} }, new Set([1]), null).map((r) => r.kind)).toEqual(["series", "loading"]);
    expect(seriesTreeRows(state, new Set(), null).every((r) => r.kind === "series")).toBe(true);
  });

  it("at most 1 000 rows of sections, then «Перейти…»", () => {
    const big = row(9, { s1: 4999 * 25 }, "Большая", 5000);
    const rows = seriesTreeRows({ list: [big], tables: { 9: tableOf(5000) } }, new Set([9]), { id: 9, k: 4500, seg: null });
    expect(rows.filter((r) => r.kind === "section")).toHaveLength(TREE_SECTIONS_MAX);
    expect(rows.at(-1)).toEqual({ kind: "more", seriesId: 9, rest: 4000 });
    expect(rows.some((r) => r.kind === "section" && r.active)).toBe(false); // the current section is beyond the rows
  });

  it("the menu: make active, parameters, copy, rename, delete — commands of the registry", () => {
    const app = createApp({ persist: false });
    const menu = seriesMenu(row(1), false);
    expect(menu.map((m) => m.label)).toEqual(["Сделать активной", "Параметры…", "Копировать", "Переименовать…", "Удалить"]);
    for (const m of menu) expect(app.registry.get(m.command), m.command).toBeDefined();
    expect(menu.every((m) => m.args.series_id === 1)).toBe(true);
    expect(menu.find((m) => m.command === "series.rename")?.rename).toBe(true);
    expect(seriesMenu(row(1), true)[0].label).toBe("Активная серия");
  });
});
