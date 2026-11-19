// 10.26 (found by app/e2e/series.spec.ts): `useStore(viewStore, (k) => sectionOf(k).series ?? null)` in the tree,
// the manager and the table of series is a snapshot of `useSyncExternalStore`. A new object on every read made an
// endless render (React #185) as soon as a series became active: the window went blank. And the mini-plan was shown
// only with a free line — never under an active series (its key has no line), so the traces of the series were not.
import { describe, expect, it } from "vitest";
import { miniPlanWanted } from "../src/modules/section/SectionMap";
import { normalizeSeries, sectionOf } from "../src/modules/section/state";

describe("the active series of the view key is a stable snapshot", () => {
  it("the same stored key reads as the same object; a changed key — a new one", () => {
    const keys = { section: { clip: true, series: { id: 7, k: 2, seg: null } } };
    const a = sectionOf(keys).series;
    expect(a).toEqual({ id: 7, k: 2, seg: null });
    expect(sectionOf(keys).series).toBe(a);
    expect(sectionOf({ ...keys }).series).toBe(a); // other keys changed, the series key did not
    const next = { section: { clip: true, series: { id: 7, k: 3, seg: null } } };
    expect(sectionOf(next).series).not.toBe(a);
    expect(sectionOf(next).series).toEqual({ id: 7, k: 3, seg: null });
  });

  it("a key it cannot read stays null, every time", () => {
    const bad = { id: 0, k: 1 };
    expect(normalizeSeries(bad)).toBeNull();
    expect(normalizeSeries(bad)).toBeNull();
    expect(normalizeSeries({ id: 3, k: -1, seg: 2 })).toEqual({ id: 3, k: 0, seg: 2 });
    expect(normalizeSeries(null)).toBeNull();
  });
});

describe("the mini-plan", () => {
  const line = { x0: 0, y0: 0, az: 90, length: null, towards: 25, away: 25 };
  it("is shown for the free line, a line being drawn and the active series (its traces)", () => {
    expect(miniPlanWanted(sectionOf({ section: { line, clip: true } }), false)).toBe(true);
    expect(miniPlanWanted(sectionOf({}), true)).toBe(true);
    expect(miniPlanWanted(sectionOf({ section: { line, clip: true, series: { id: 4, k: 1 } } }), false)).toBe(true);
    expect(miniPlanWanted(sectionOf({ section: { clip: false, series: { id: 4, k: 0 } } }), false)).toBe(true);
    expect(miniPlanWanted(sectionOf({}), false)).toBe(false);
  });
});
