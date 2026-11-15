import { describe, expect, it } from "vitest";
import type { BlockAttribute } from "../api";
import { blockColors, percentileRange } from "./blockStyle";
import { fieldNames, isBlockModelFields } from "../shell/sniffBlocks";
import { parseCutoffs, toReportParams } from "../charts/GradeTonnageWindow";

const num: BlockAttribute = { key: "a0", name: "AU", kind: "number", unit: "g/t", source: "R", stats: { count: 4, empty: 0 } };
const cat: BlockAttribute = { key: "a1", name: "ALT", kind: "category", labels: ["G", "Q"], source: "C", stats: { count: 4, empty: 0 } };
const style = { attribute: "a0", min: "", max: "", log: false, from: "", to: "", hidden: [] as string[], opacity: 1 };

describe("block model display", () => {
  it("filters numbers by from / to and colours the rest", () => {
    const c = blockColors(4, num, new Float32Array([0.1, 1, 5, NaN]), { ...style, from: "1" });
    expect(Array.from(c.visible)).toEqual([0, 1, 1, 0]);
    expect(c.shown).toBe(2);
    expect(c.scale).not.toBeNull();
  });

  it("hides categories and keeps codes' colours", () => {
    const c = blockColors(3, cat, new Float32Array([0, 1, NaN]), { ...style, attribute: "a1", hidden: ["Q"] });
    expect(Array.from(c.visible)).toEqual([1, 0, 1]);
  });

  it("takes the 5–95th percentile range", () => {
    const v = new Float32Array(Array.from({ length: 101 }, (_, i) => i));
    expect(percentileRange(v)).toEqual([5, 95]);
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
