import { describe, expect, it } from "vitest";
import { setStyle } from "../src/modules/scene/SetStyleWindow";

describe("styles of collars and traces per set (ТЗ 7.15)", () => {
  it("come from the project view keys, broken values fall back to the defaults", () => {
    const keys = { collar_styles: { "3": { shape: "star", size: 99, color: "audit" } }, trace_styles: { "3": { width: 3, tickStep: -1 } } };
    expect(setStyle("collars", 3, keys)).toMatchObject({ shape: "star", size: 40, color: "audit" });
    expect(setStyle("collars", 4, keys).shape).toBe("circle");
    expect(setStyle("traces", 3, keys).width).toBe(3);
  });
});
