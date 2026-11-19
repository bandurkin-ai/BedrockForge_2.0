// Э10 10.28 — the review of the interface of series of sections: stable selectors of `useStore`, the windows of
// series through commands of the registry, the packet of a section that failed, the signature of the prepared
// sections by geometry, the cache of the core around a far k, «Перейти» on a polyline, one PATCH of a window cell.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { encodeBfa1 } from "../src/edit/bfa1";
import { toolKey } from "../src/modules/lines/editKeys";
import { gotoHints, resolveGoto } from "../src/modules/section/goto";
import type { SeriesRow } from "../src/modules/section/seriesApi";
import { seriesTab } from "../src/modules/section/seriesCommands";
import { SeriesSections, type SeriesTarget } from "../src/modules/section/seriesSections";
import { geometryKey } from "../src/modules/section/seriesState";
import { singleFlight } from "../src/modules/section/SeriesTable";
import { activeSeriesOf, sectionOf } from "../src/modules/section/state";
import { buildRibbon, flattenRibbon } from "../src/shell/ribbonConfig";
import { DEFAULT_VIEW, DEFAULT_WORKSPACE } from "../src/shell/workspace";

const SRC = join(__dirname, "../src");
const source = (path: string) => readFileSync(join(SRC, path), "utf8");

function sources(dir: string): string[] {
  return readdirSync(join(SRC, dir), { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []));
}

describe("selectors of useStore (React 18: a new object every time re-renders without end)", () => {
  it("the active series is the same object while id, k and seg are", () => {
    const keys = () => ({ section: { clip: true, series: { id: 4, k: 2, seg: null } } });
    // the same series in a key written anew (`updateView`): a new object of `sectionOf` — a new snapshot
    expect(sectionOf(keys()).series).not.toBe(sectionOf(keys()).series);
    const a = activeSeriesOf(keys());
    expect(activeSeriesOf(keys())).toBe(a);
    expect(activeSeriesOf({ section: { clip: false, series: { id: 4, k: 2, seg: null } } })).toBe(a);
    expect(activeSeriesOf({ section: { series: { id: 4, k: 3, seg: null } } })).toEqual({ id: 4, k: 3, seg: null });
    expect(activeSeriesOf({ section: { line: null } })).toBeNull();
  });

  it("no selector builds an object or an array of the state (sectionOf, lineLayers, map, filter)", () => {
    const bad = /useStore\(\s*[\w.]+\s*,\s*\(?\w*\)?\s*=>\s*(?:sectionOf\([^)]*\)\.series|lineLayers\([^)]*\)\)|\[|\{|[\w.]+\.(?:map|filter|flatMap|slice)\([^)]*\)\))/;
    const found = [...sources("modules"), ...sources("shell")].filter((f) => f.endsWith(".tsx") && bad.test(source(f)));
    expect(found).toEqual([]);
  });
});

describe("windows of series run commands of the registry", () => {
  it("«Менеджер серий» and the table of sections call no actions and no API themselves", () => {
    for (const f of ["modules/section/SeriesManager.tsx", "modules/section/SeriesTable.tsx"]) {
      const text = source(f);
      expect(text, f).not.toMatch(/\b(copySeries|deleteSeries|activateSeries)\(|seriesApi\./);
    }
    expect(source("modules/section/SeriesManager.tsx")).toMatch(/"series\.copy" \| "series\.delete" \| "series\.activate"/);
    expect(source("modules/section/SeriesTable.tsx")).toMatch(/execute\("series\.window"/);
  });

  it("a window cell sends one change at a time (Enter, then leaving the field — one PATCH)", async () => {
    const sent: string[] = [];
    let answer: () => void = () => undefined;
    const send = singleFlight((t: string) => (sent.push(t), t === "10" ? new Promise<void>((res) => (answer = res)) : Promise.resolve()));
    const first = send("10");
    void send("10"); // blur while the answer of Enter is awaited
    expect(sent).toEqual(["10"]);
    answer();
    await first;
    await send("12");
    expect(sent).toEqual(["10", "12"]);
    const failing = singleFlight(async () => Promise.reject(new Error("409")));
    await expect(failing()).rejects.toThrow("409");
    await expect(failing()).rejects.toThrow("409"); // free again after a failure
  });
});

describe("the ribbon «Разрезы» shows its state", () => {
  const item = (tab: ReturnType<typeof seriesTab>, command: string) => flattenRibbon([tab]).find((i) => i.command === command)!;

  it("the lock, the surfaces and the shadow pressed; «Тень» toggles without arguments", () => {
    const off = seriesTab(DEFAULT_VIEW, { locked: false, surfaces: false, shadow: "none" });
    expect([item(off, "series.lock").active, item(off, "series.surfaces").active, item(off, "series.shadow").active]).toEqual([false, false, false]);
    const on = seriesTab(DEFAULT_VIEW, { locked: true, surfaces: true, shadow: "prev" });
    expect([item(on, "series.lock").active, item(on, "series.surfaces").active, item(on, "series.shadow").active]).toEqual([true, true, true]);
    expect(item(on, "series.shadow").args).toBeUndefined();
    expect(item(seriesTab(DEFAULT_VIEW, { shadow: null }), "series.shadow").active).toBe(false);
    // buildRibbon passes the flags to the tab
    const tabs = buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW, { locked: true, shadow: "both" });
    const sections = tabs.find((t) => t.id === "sections")!;
    expect([item(sections, "series.lock").active, item(sections, "series.shadow").active]).toEqual([true, true]);
  });

  it("L: «Зафиксировать вид» while a series is active, the tool «Линия» — Shift+L; without a series L is the tool", () => {
    expect(toolKey({ key: "l", ctrl: false, shift: false }, true)).toBeNull();
    expect(toolKey({ key: "д", ctrl: false, shift: false }, true)).toBeNull();
    expect(toolKey({ key: "L", ctrl: false, shift: true }, true)).toBe("line");
    expect(toolKey({ key: "Д", ctrl: false, shift: true }, true)).toBe("line");
    expect(toolKey({ key: "p", ctrl: false, shift: false }, true)).toBe("polygon");
    expect(toolKey({ key: "l", ctrl: false, shift: false }, false)).toBe("line");
    expect(item(seriesTab(DEFAULT_VIEW), "series.lock").shortcut).toBe("l");
  });
});

// ---------------------------------------------------------------- the sections of the wireframes on a series

function packet(path: string): ArrayBuffer {
  const k = Number(/sections\/(\d+)\//.exec(path)![1]);
  const y = 5000 + 25 * k;
  return encodeBfa1({ kind: "series_section", format: 1, series: 7, key: "abc", k, seg: null, point: [1000, y, 0], normal: [0, 1, 0], clip: null, wireframes: [1], missing: [], lines: 1, points: 3 }, [
    ["xyz", "<f8", Float64Array.from([1, y, 0, 2, y, 0, 2, y, -1])],
    ["offsets", "<u4", Uint32Array.from([0, 3])],
    ["closed", "<u1", Uint8Array.from([1])],
    ["wf", "<u4", Uint32Array.from([1])],
  ]);
}

function sections(fail: (path: string) => boolean) {
  const loads: string[] = [];
  const posts: { around: number }[] = [];
  const errors: unknown[] = [];
  const shown: unknown[] = [];
  const s = new SeriesSections({
    load: async (path) => {
      loads.push(path);
      if (fail(path)) throw Object.assign(new Error("Сечение не построено."), { hint: "Повторите позже." });
      return packet(path);
    },
    cancel: () => undefined,
    post: async (_path, body) => void posts.push(body as { around: number }),
    show: (p) => shown.push(p),
    onError: (e) => errors.push(e),
    setTimer: () => 0,
    clearTimer: () => undefined,
  });
  return { s, loads, posts, errors, shown };
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const target = (index: number, extra: Partial<SeriesTarget> = {}): SeriesTarget => ({ seriesId: 7, geometry: "g", polyline: false, index, steps: 10, wireframes: [[1, 1]], ...extra });

describe("the packet of a section of a series", () => {
  it("a failed packet is said ({message, hint} to the window), asked again on the next sync", async () => {
    let broken = true;
    const h = sections(() => broken);
    h.s.set(target(2));
    await flush();
    expect(h.errors).toHaveLength(1);
    expect(h.errors[0]).toMatchObject({ message: "Сечение не построено.", hint: "Повторите позже." });
    expect(h.s.mark).toMatchObject({ series: 7, k: 2, ready: false });
    broken = false;
    h.s.set(target(2)); // the same section: asked again (it was skipped as «the same» before)
    await flush();
    expect(h.loads).toHaveLength(2);
    expect(h.s.mark).toMatchObject({ k: 2, ready: true });
    h.s.set(target(2)); // shown: nothing more
    await flush();
    expect(h.loads).toHaveLength(2);
    expect(h.errors).toHaveLength(1);
  });

  it("the prepared sections follow the geometry of the series, not its name, shadow or layer of contours", async () => {
    const row = (extra: Partial<SeriesRow> = {}, params: Partial<SeriesRow["params"]> = {}): SeriesRow => ({
      id: 7, name: "Профили", folder: null, version: 1, number: 1, count: 10, created_at: "", updated_at: "",
      params: { type: "vertical", p1: [1000, 5000, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 225, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null, ...params },
      ...extra,
    });
    const g = geometryKey(row());
    expect(geometryKey(row({ version: 5, name: "Новое" }, { shadow: "both", contour_layer: 9, template: "{k}" }))).toBe(g);
    for (const p of [{ step: 20 }, { az: 45 }, { s1: 250 }, { towards: 10 }, { overrides: { "3": { away: 5 } } }]) expect(geometryKey(row({}, p)), JSON.stringify(p)).not.toBe(g);
    const h = sections(() => false);
    h.s.set(target(2, { geometry: g }));
    await flush();
    h.s.set(target(2, { geometry: geometryKey(row({ version: 2, name: "Новое" }, { shadow: "none" })) })); // a rename, the shadow
    await flush();
    expect(h.loads).toHaveLength(1);
    expect(h.posts).toHaveLength(1);
  });

  it("a long series: the cache of the core is asked again around k once k has gone more than 40 from it", async () => {
    const h = sections(() => false);
    const at = (k: number) => target(k, { steps: 1000 });
    for (const k of [100, 120, 140, 141, 160, 60]) {
      h.s.set(at(k));
      await flush();
    }
    expect(h.posts.map((p) => p.around)).toEqual([100, 141, 60]);
    // a short series: once
    const s = sections(() => false);
    for (const k of [0, 50, 99]) s.s.set(target(k, { steps: 100 }));
    expect(s.posts.map((p) => p.around)).toEqual([0]);
  });
});

describe("«Перейти» on a polyline section", () => {
  const one = [{ k: 0, name: "Ломаный" }];
  it("the number goes to 1…segments (its table has one section), the suggestions are the segments", () => {
    expect(resolveGoto(one, 3)).toBeNull(); // what it was
    expect(resolveGoto(one, 3, 4)).toBe(2);
    expect(resolveGoto(one, "4", 4)).toBe(3);
    expect(resolveGoto(one, 5, 4)).toBeNull();
    expect(gotoHints(one, "", 10, 3)).toEqual([{ k: 0, label: "Сегмент 1" }, { k: 1, label: "Сегмент 2" }, { k: 2, label: "Сегмент 3" }]);
    expect(gotoHints(one, "2", 10, 3)).toEqual([{ k: 1, label: "Сегмент 2" }]);
    expect(gotoHints(one, "9", 10, 3)).toEqual([]);
  });
});
