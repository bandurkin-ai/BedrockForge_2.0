// The division of a parent that is not 2^L (ADR 0030 decision 1, подшаг 27.4а): the core says `levels: null`, the window of
// the model and the properties write «5 × 5 × 3, произвольное деление» and never «уровней» for it.
import { describe, expect, it } from "vitest";
import { divTail, isFreeDiv, subFacts } from "../src/modules/blocks/props";
import { readDetails, subText } from "../src/modules/blocks/window/details";
import type { BlockModelInfo } from "../src/modules/blocks/api";

const info = (grid: Record<string, unknown>): BlockModelInfo => ({ id: 1, name: "М", sub: true, rows: 405, parents: 98, grid }) as unknown as BlockModelInfo;

describe("the division of a parent: any whole number, not only 2^L", () => {
  it("tells a power of two from a free division", () => {
    expect(isFreeDiv([2, 4, 1])).toBe(false);
    expect(isFreeDiv([1, 1, 1])).toBe(false);
    expect(isFreeDiv([5, 5, 3])).toBe(true);
    expect(isFreeDiv([2, 1, 6])).toBe(true);
  });

  it("the tail of the text: the levels of 2^L, «произвольное деление» for 5 × 5 × 3, nothing for no division", () => {
    expect(divTail([2, 2, 1], [4, 4, 2])).toBe(", уровней 2 × 2 × 1");
    expect(divTail(null, [5, 5, 3])).toBe(", произвольное деление (не по уровням 2^L)");
    expect(divTail(undefined, [4, 4, 2])).toBe("");
    expect(divTail(null, [1, 1, 1])).toBe("");
  });

  it("the properties of a model divided by 5 × 5 × 3 (levels: null from the core)", () => {
    const facts = subFacts(info({ div: [5, 5, 3], levels: null, counts: [6, 5, 4], size: [10, 10, 10] }));
    const sub = facts.find((f) => f.label === "Субблоки");
    expect(sub?.value).toBe("родитель делится на 5 × 5 × 3, произвольное деление (не по уровням 2^L)");
    expect(sub?.value).not.toContain("уровней 0");
    const pow = subFacts(info({ div: [4, 4, 2], levels: [2, 2, 1], counts: [6, 5, 4], size: [10, 10, 10] })).find((f) => f.label === "Субблоки");
    expect(pow?.value).toBe("родитель делится на 4 × 4 × 2, уровней 2 × 2 × 1");
  });

  it("the window of the model reads `levels: null` as «not said» and writes the division in whole numbers", () => {
    const d = readDetails({ grid: { div: [5, 5, 3], levels: null, rotation: 0 }, sub: true, rows: 405, parents: 98 });
    expect(d.levels).toBeNull();
    expect(d.div).toEqual([5, 5, 3]);
    expect(subText(d)).toBe("деление блока 5 × 5 × 3; произвольное деление, не по уровням 2^L");
    const two = readDetails({ grid: { div: [2, 2, 2], levels: [1, 1, 1] }, sub: true });
    expect(subText(two)).toBe("деление блока 2 × 2 × 2; уровней деления 1 × 1 × 1");
    const none = readDetails({ grid: { div: [2, 2, 2] }, sub: true }); // an older core: no levels, a power of two
    expect(subText(none)).toBe("деление блока 2 × 2 × 2");
  });
});
