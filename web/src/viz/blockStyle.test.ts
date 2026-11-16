import { describe, expect, it } from "vitest";
import type { BlockAttribute, BlockModelMeta } from "../api";
import {
  blockColors, defaultBlockStyle, filterMask, normalizeBlockStyle, paletteFitsBlock, paletteOf, percentileRange, shellMask, withPalette,
  type BlockFilter, type BlockStyle,
} from "./blockStyle";
import { fieldNames, isBlockModelFields } from "../shell/sniffBlocks";
import { parseCutoffs, toReportParams } from "../charts/GradeTonnageWindow";

const num: BlockAttribute = { key: "a0", name: "AU", kind: "number", unit: "g/t", source: "R", stats: { count: 4, empty: 0 } };
const cat: BlockAttribute = { key: "a1", name: "ALT", kind: "category", labels: ["G", "Q"], source: "C", stats: { count: 4, empty: 0 } };
const meta = { attributes: [num, cat], report: null } as unknown as BlockModelMeta;
const style: BlockStyle = { ...defaultBlockStyle(meta), attribute: "a0", log: false };

describe("block model display", () => {
  it("colours numbers on a scale and empty values in the no-data colour", () => {
    const c = blockColors(4, num, new Float32Array([0.1, 1, 5, NaN]), { ...style, min: "0", max: "5", noDataColor: "#010203" });
    expect(c.shown).toBe(4);
    expect(c.scale).not.toBeNull();
    expect(c.legend).toMatchObject({ kind: "scale", min: 0, max: 5 });
    expect(Array.from(c.colors.slice(9))).toEqual([1, 2, 3]);
    // the ends of the gradient
    expect(Array.from(c.colors.slice(0, 3))).not.toEqual(Array.from(c.colors.slice(6, 9)));
  });

  it("colours numbers by classes; a hidden class is not shown; a bound belongs to the upper class", () => {
    const classes = [{ from: null, color: "#000001" }, { from: 1, color: "#000002", hidden: true }, { from: 3, color: "#000003" }];
    const c = blockColors(5, num, new Float32Array([0.1, 1, 2.9, 3, NaN]), { ...style, colorBy: "classes", classes, noDataColor: "#0000ff" });
    expect(Array.from(c.visible)).toEqual([1, 0, 0, 1, 1]);
    expect(c.colors[2]).toBe(1);
    expect(c.colors[11]).toBe(3);
    expect(c.colors[14]).toBe(255);
    expect(c.legend).toEqual({ kind: "swatches", items: [["< 1", "#000001"], ["≥ 3", "#000003"]] });
  });

  it("classes without an open one leave lower values in the no-data colour", () => {
    const c = blockColors(2, num, new Float32Array([0.5, 2]), { ...style, colorBy: "classes", classes: [{ from: 1, color: "#000002" }], noDataColor: "#0000ff" });
    expect([c.colors[2], c.colors[5]]).toEqual([255, 2]);
  });

  it("hides categories and takes colours set by hand", () => {
    const c = blockColors(3, cat, new Float32Array([0, 1, NaN]), { ...style, attribute: "a1", hidden: ["Q"], catColors: { G: "#0a0b0c" } });
    expect(Array.from(c.visible)).toEqual([1, 0, 1]);
    expect(Array.from(c.colors.slice(0, 3))).toEqual([10, 11, 12]);
  });

  it("takes the 5–95th percentile range", () => {
    const v = new Float32Array(Array.from({ length: 101 }, (_, i) => i));
    expect(percentileRange(v)).toEqual([5, 95]);
  });
});

describe("block model restriction", () => {
  const au = new Float32Array([0.1, 1, 5, NaN]);
  const alt = new Float32Array([0, 1, 1, NaN]);
  const valuesOf = (key: string) => (key === "a0" ? au : key === "a1" ? alt : null);
  const mask = (filter: Partial<BlockFilter>) => {
    const m = filterMask(4, { on: true, join: "and", invert: false, conditions: [], ...filter }, [num, cat], valuesOf);
    return m && Array.from(m);
  };

  it("is off without complete conditions", () => {
    expect(mask({ on: false, conditions: [{ field: "a0", op: "gt", value: "1" }] })).toBeNull();
    expect(mask({ conditions: [{ field: "a0", op: "gt", value: "" }] })).toBeNull();
    expect(mask({ conditions: [{ field: "nope", op: "gt", value: "1" }] })).toBeNull();
  });

  it("compares numbers (a comma is read) and categories by label", () => {
    expect(mask({ conditions: [{ field: "a0", op: "gt", value: "0,5" }] })).toEqual([0, 1, 1, 0]);
    expect(mask({ conditions: [{ field: "a0", op: "le", value: "1" }] })).toEqual([1, 1, 0, 0]);
    expect(mask({ conditions: [{ field: "a1", op: "eq", value: "Q" }] })).toEqual([0, 1, 1, 0]);
    expect(mask({ conditions: [{ field: "a1", op: "ne", value: "Q" }] })).toEqual([1, 0, 0, 1]);
    expect(mask({ conditions: [{ field: "a0", op: "empty", value: "" }] })).toEqual([0, 0, 0, 1]);
  });

  it("joins conditions by and / or and inverts", () => {
    const conditions = [{ field: "a1", op: "eq" as const, value: "Q" }, { field: "a0", op: "gt" as const, value: "1" }];
    expect(mask({ conditions })).toEqual([0, 0, 1, 0]);
    expect(mask({ conditions, join: "or" })).toEqual([0, 1, 1, 0]);
    expect(mask({ conditions, invert: true })).toEqual([1, 1, 0, 1]);
  });
});

/** Blocks of a regular grid nx × ny × nz of unit cells: centre and size, 6 numbers each. */
function gridBlocks(nx: number, ny: number, nz: number): Float32Array {
  const out: number[] = [];
  for (let x = 0; x < nx; x++) for (let y = 0; y < ny; y++) for (let z = 0; z < nz; z++) out.push(x + 0.5, y + 0.5, z + 0.5, 1, 1, 1);
  return new Float32Array(out);
}

describe("block model shell", () => {
  const grid = { origin: [100, 200, 300], parent: [1, 1, 1], counts: [3, 3, 3], subblocked: false };
  const at = (x: number, y: number, z: number) => (x * 3 + y) * 3 + z;

  it("hides a block closed on every side", () => {
    const shell = shellMask(gridBlocks(3, 3, 3), new Uint8Array(27).fill(1), grid, [100, 200, 300]) as Uint8Array;
    expect(shell.reduce((a, b) => a + b, 0)).toBe(26);
    expect(shell[at(1, 1, 1)]).toBe(0);
  });

  it("is the shell of the blocks shown: a hidden neighbour opens the block behind it", () => {
    const visible = new Uint8Array(27).fill(1);
    visible[at(1, 1, 2)] = 0;
    const shell = shellMask(gridBlocks(3, 3, 3), visible, grid, [100, 200, 300]) as Uint8Array;
    expect(shell[at(1, 1, 1)]).toBe(1);
    expect(shell[at(1, 1, 2)]).toBe(0);
    expect(shell.reduce((a, b) => a + b, 0)).toBe(26);
  });

  it("counts sub-blocks: a cell filled by its sub-blocks closes its neighbours, a part-filled one does not", () => {
    // 3 cells in a row along x; the middle one is split into two halves along x.
    const render = new Float32Array([0.5, 0.5, 0.5, 1, 1, 1, 1.25, 0.5, 0.5, 0.5, 1, 1, 1.75, 0.5, 0.5, 0.5, 1, 1, 2.5, 0.5, 0.5, 1, 1, 1]);
    const g = { origin: [0, 0, 0], parent: [1, 1, 1], counts: [3, 1, 1], subblocked: true };
    // every block touches the outside of a 3 × 1 × 1 row
    expect(Array.from(shellMask(render, new Uint8Array(4).fill(1), g, [0, 0, 0]) as Uint8Array)).toEqual([1, 1, 1, 1]);
    const tall = { ...g, counts: [3, 1, 1] };
    expect(shellMask(render, new Uint8Array([1, 1, 0, 1]), tall, [0, 0, 0])).not.toBeNull();
  });

  it("gives up on a grid too large for the browser", () => {
    expect(shellMask(new Float32Array(6), new Uint8Array(1), { ...grid, counts: [2000, 2000, 2000] }, [0, 0, 0])).toBeNull();
  });
});

describe("block model palettes and stored styles", () => {
  it("keeps a colouring as a palette of the library and takes it back", () => {
    const classes = [{ from: null, color: "#111111" }, { from: 2, color: "#222222" }];
    const body = paletteOf({ ...style, colorBy: "classes", classes }, num);
    expect(body).toEqual({ kind: "classes", classes });
    expect(paletteFitsBlock(body, num)).toBe(true);
    expect(paletteFitsBlock(body, cat)).toBe(false);
    const taken = withPalette(style, body, { id: 7, name: "Fe" });
    expect(taken.colorBy).toBe("classes");
    expect(taken.classes).toEqual(classes);
    expect(taken.palette).toEqual({ id: 7, name: "Fe" });
    expect(paletteOf({ ...style, log: true, gradient: "viridis" }, num)).toEqual({ kind: "gradient", gradient: { name: "viridis", reverse: false, stretch: "log", min: "", max: "" } });
    expect(paletteOf({ ...style, catColors: { Q: "#abcdef" } }, cat)).toMatchObject({ kind: "categories", catColors: { Q: "#abcdef" } });
  });

  it("reads a stored style, dropping what is broken", () => {
    const s = normalizeBlockStyle({ attribute: "a0", colorBy: "classes", classes: [{ from: 1, color: "#123456" }, { from: 2, color: "red" }], view: "all", edgeWidth: 99, opacity: -1,
      filter: { on: true, join: "or", conditions: [{ field: "a0", op: "gt", value: "1" }, { field: "a0", op: "like", value: "x" }] } });
    expect(s.classes).toEqual([{ from: 1, color: "#123456" }]);
    expect(s.view).toBe("all");
    expect(s.edgeWidth).toBe(8);
    expect(s.opacity).toBe(0.1);
    expect(s.filter).toEqual({ on: true, join: "or", invert: false, conditions: [{ field: "a0", op: "gt", value: "1" }] });
    expect(normalizeBlockStyle(null)).toMatchObject({ attribute: null, view: "shell", slice: true, edges: false });
    expect(normalizeBlockStyle(JSON.parse(JSON.stringify(style)))).toEqual(style);
  });
});

describe("block model files and report", () => {
  it("recognises a block model by centre and size fields, BOCU-1 names included", () => {
    expect(isBlockModelFields(["X", "_X", "Y", "_Y", "Z", "_Z", "AU"])).toBe(true);
    expect(isBlockModelFields(["BHID", "FROM", "TO", "X", "Y", "Z"])).toBe(false);
    const head = new TextEncoder().encode("X,Y,Z,XINC,YINC,ZINC,AU\n1,2,3,5,5,5,0.4\n");
    expect(isBlockModelFields(fieldNames(head))).toBe(true);
    const magic = "THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n";
    const shift = (s: string) => Array.from(s, (c) => String.fromCharCode(c.charCodeAt(0) + 0x50)).join("");
    const vars = ["X", "_X", "Y", "_Y", "Z", "_Z"].map((n) => `${shift(n).padEnd(10)} R   8  3\r\n`).join("");
    const text = (magic + '<data codepage="BOCU-1"/>').padEnd(4096, "\0") + "6   VARIABLES\r\n" + vars;
    expect(fieldNames(Uint8Array.from(text, (c) => c.charCodeAt(0)))).toEqual(["X", "_X", "Y", "_Y", "Z", "_Z"]);
    // A 10-character name has no space before its type: "RUN_AU_CUTN 12 8".
    const tight = (magic + '<data codepage="BOCU-1"/>').padEnd(4096, "\0") + "7   VARIABLES\r\n" + ["X", "_X", "Y", "_Y", "Z", "_Z", "RUN_AU_CUT"].map((n, i) => `${shift(n).padEnd(10)}${i === 6 ? "N" : "R"} 12 8\r\n`).join("");
    expect(fieldNames(Uint8Array.from(tight, (c) => c.charCodeAt(0)))).toEqual(["X", "_X", "Y", "_Y", "Z", "_Z", "RUN_AU_CUT"]);
    // A long XML header padded to 8192 bytes, the codepage past byte 4096; and no codepage at all.
    const long = (magic + " ".repeat(5000) + '<data codepage="BOCU-1"/>').padEnd(8192, "\0") + "6   VARIABLES\r\n" + vars;
    expect(isBlockModelFields(fieldNames(Uint8Array.from(long, (c) => c.charCodeAt(0))))).toBe(true);
    const bare = magic.padEnd(4096, "\0") + "6   VARIABLES\r\n" + vars;
    expect(isBlockModelFields(fieldNames(Uint8Array.from(bare, (c) => c.charCodeAt(0))))).toBe(true);
  });

  it("reads cut-offs with a decimal comma and builds the request", () => {
    expect(parseCutoffs("0 0,3; 0.5 1,2")).toEqual([0, 0.3, 0.5, 1.2]);
    expect(parseCutoffs("0.3,0.5")).toEqual([0.3, 0.5]);
    const p = toReportParams({ density: null, densityValue: "2,7", grades: ["a0"], cutoffField: "a0", cutoffs: "0 1", filterField: null, filterLabels: [], groupBy: null });
    expect(p).toMatchObject({ density: null, density_value: 2.7, cutoffs: [0, 1], filters: [] });
  });
});
