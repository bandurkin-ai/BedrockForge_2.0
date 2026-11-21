// The view key `swaths` (ADR 0019 §2): swath plots shown in 3D, `[{swath, visible}]` ≤ 50 — read as the core
// reads it (the shared fixtures `core/tests/data/view/swaths.json`: junk dropped, `visible` true unless false, a
// repeated id once, the newest 50), shown and hidden by operations that survive a 409 (ViewSync applies them again).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { dropSwathEntry, MAX_SWATHS_SHOWN, shownSwaths, showSwath, swathEntries } from "../src/modules/scene/viewSync";

const fixtures = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/view/swaths.json"), "utf-8")) as {
  cases: { in: unknown; out: unknown }[];
  long: { count: number; kept_from: number };
};

describe("view key swaths", () => {
  it("reads every case as the core does", () => {
    for (const c of fixtures.cases) expect(swathEntries(c.in), JSON.stringify(c.in)).toEqual(c.out);
  });

  it("keeps the newest 50", () => {
    const list = Array.from({ length: fixtures.long.count }, (_, i) => ({ swath: i + 1, visible: true }));
    const out = swathEntries(list);
    expect(out).toHaveLength(MAX_SWATHS_SHOWN);
    expect(out[0].swath).toBe(fixtures.long.kept_from);
  });

  it("shows, hides and drops by operations", () => {
    let v: unknown = undefined;
    v = showSwath(5, true)(v);
    v = showSwath(7, true)(v);
    v = showSwath(5, false)(v);
    expect(v).toEqual([{ swath: 5, visible: false }, { swath: 7, visible: true }]);
    expect(shownSwaths(v)).toEqual([7]);
    // an operation applied again on a value changed elsewhere keeps the other entry
    expect(showSwath(9, true)([{ swath: 3, visible: true }])).toEqual([{ swath: 3, visible: true }, { swath: 9, visible: true }]);
    expect(dropSwathEntry(7)(v)).toEqual([{ swath: 5, visible: false }]);
    // past the limit the oldest hidden one goes first
    const full = [{ swath: 1, visible: true }, { swath: 2, visible: false }, ...Array.from({ length: 48 }, (_, i) => ({ swath: i + 3, visible: true }))];
    const next = showSwath(100, true)(full) as { swath: number }[];
    expect(next).toHaveLength(50);
    expect(next.some((e) => e.swath === 2)).toBe(false);
    expect(next[next.length - 1].swath).toBe(100);
  });
});
