import { describe, expect, it } from "vitest";
import { logCsv } from "../src/charts/striplog/csv";
import { MAX_HOLES, toDownhole, type LogAnswer } from "../src/charts/striplog/data";
import { embedFonts } from "../src/charts/striplog/export";

const answer: LogAnswer = {
  tables: [
    { key: "5", id: 5, name: "Пробы", kind: "assay", columns: [{ name: "AU", kind: "number", unit: "г/т" }, { name: "Проба", kind: "text", unit: "" }] },
    { key: "6", id: 6, name: "Литология", kind: "interval", columns: [{ name: "LITH", kind: "text", unit: "" }] },
    { key: "7", id: 7, name: "Структуры", kind: "event", columns: [{ name: "TYPE", kind: "text", unit: "" }] },
  ],
  holes: [
    {
      name: "DH-1",
      collar_z: 420,
      td: 10,
      found: true,
      tables: {
        "5": { from: [0, 2, 4], to: [2, 4, 6], values: { AU: [0.5, null, 0], Проба: ["P1", "P2", null] } },
        "6": { from: [0], to: [3], values: { LITH: ["гранит"] } },
        "7": { from: [2.5], to: [null], values: { TYPE: ["разлом"] } },
      },
    },
  ],
};

describe("window «Данные по скважинам»", () => {
  it("turns the columns of the core into intervals; empty stays null", () => {
    const d = toDownhole(answer);
    expect(d.tables.map((t) => t.kind)).toEqual(["interval", "interval", "event"]);
    const h = d.holes[0];
    expect(h.max_depth).toBe(10);
    expect(h.tables["5"][1]).toEqual({ from: 2, to: 4, values: { AU: null, Проба: "P2" } });
    expect(h.tables["5"][2].values.AU).toBe(0);
    expect(h.tables["7"][0]).toEqual({ from: 2.5, to: 2.5, values: { TYPE: "разлом" } }); // an event at one depth
    expect(MAX_HOLES).toBe(30);
  });

  it("CSV holds every column by depth (boundaries merged, events at their depth)", () => {
    const csv = logCsv(toDownhole(answer), [
      { table: "5", field: "AU", title: "AU (Пробы)", event: false },
      { table: "6", field: "LITH", title: "LITH (Литология)", event: false },
      { table: "7", field: "TYPE", title: "TYPE (Структуры)", event: true },
    ]);
    const rows = csv.split("\r\n");
    expect(rows[0]).toBe('"Скважина";"От";"До";"AU (Пробы)";"LITH (Литология)";"TYPE (Структуры)"');
    expect(rows.slice(1)).toEqual([
      '"DH-1";0;2;0.5;"гранит";',
      '"DH-1";2;3;;"гранит";', // AU empty — not 0
      '"DH-1";2.5;2.5;;"гранит";"разлом"',
      '"DH-1";4;6;0;;', // 3–4 m: nothing in any column — no row
    ]);
  });

  it("SVG keeps the brand fonts inside", () => {
    const svg = embedFonts('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect/></svg>', [{ family: "Montserrat", weight: 600, src: "data:font/woff2;base64,AAAA" }]);
    expect(svg).toContain("@font-face{font-family:\"Montserrat\"");
    expect(svg.indexOf("<defs>")).toBeGreaterThan(svg.indexOf("<svg"));
    expect(svg.endsWith("<rect/></svg>")).toBe(true);
  });
});
