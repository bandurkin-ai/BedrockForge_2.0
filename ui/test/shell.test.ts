import { describe, expect, it } from "vitest";
import { moduleCommands } from "../src/shell/catalog";
import { EMPTY_CONTEXT } from "../src/shell/commands";
import { buildRibbon, flattenRibbon, itemReason } from "../src/shell/ribbon";
import { assign, defaultShortcuts, effectiveShortcuts, functionKey, normalizeShortcuts } from "../src/shell/shortcuts";
import {
  DEFAULT_TREE_ORDER,
  DEFAULT_VIEW,
  DEFAULT_WORKSPACE,
  moveSection,
  normalizeView,
  normalizeWorkspace,
  setPanel,
} from "../src/shell/workspace";

const tabs = buildRibbon(DEFAULT_WORKSPACE, DEFAULT_VIEW);
const items = flattenRibbon(tabs);

describe("ribbon (ТЗ 7.0.4)", () => {
  it("has the five tabs", () => {
    expect(tabs.map((t) => t.title)).toEqual(["Главная", "Вид", "Моделирование", "Анализ", "Claude"]);
  });

  it("every button calls a registered command", () => {
    const ids = new Set(moduleCommands().map((c) => c.id));
    expect(items.filter((i) => !ids.has(i.command)).map((i) => i.command)).toEqual([]);
  });

  it("palette list drops repeats of command + arguments", () => {
    const keys = items.map((i) => `${i.command}:${JSON.stringify(i.args ?? {})}`);
    expect(new Set(keys).size).toBe(keys.length);
    expect(items.filter((i) => i.command === "scene.view" && i.args?.view === "top")).toHaveLength(1);
  });

  it("disabled buttons have a reason", () => {
    const clip = items.find((i) => i.command === "section.clip")!;
    expect(itemReason(clip, EMPTY_CONTEXT, () => null)).toBe("Сначала проведите линию разреза");
    expect(itemReason(clip, EMPTY_CONTEXT, () => "Функция появится на этапе Э6")).toBe("Функция появится на этапе Э6");
  });

  it("toggle labels follow the state", () => {
    const off = flattenRibbon(buildRibbon({ ...DEFAULT_WORKSPACE, theme: "light" }, { ...DEFAULT_VIEW, grid: false }));
    expect(off.some((i) => i.label === "Показать сетку")).toBe(true);
    expect(off.some((i) => i.label === "Тёмная тема")).toBe(true);
  });
});

describe("hotkeys assignment (ТЗ 7.0.6)", () => {
  const defaults = defaultShortcuts(items);
  const grid = functionKey({ command: "scene.style", args: { grid: false } });

  it("yes/no arguments are one function", () => {
    expect(grid).toBe(functionKey({ command: "scene.style", args: { grid: true } }));
  });

  it("a taken shortcut moves to the new function; defaults are not stored", () => {
    const fit = functionKey({ command: "scene.fit" });
    const top = functionKey({ command: "scene.view", args: { view: "top" } });
    const next = assign({}, defaults, defaults, fit, "t");
    expect(next[fit]).toBe("t");
    expect(next[top]).toBe("");
    const back = assign(next, defaults, effectiveShortcuts(items, next), fit, "f");
    expect(back[fit]).toBeUndefined();
  });

  it("reserved shortcuts and garbage are dropped on load", () => {
    expect(normalizeShortcuts({ a: "f5", b: "ctrl+q", c: 3 })).toEqual({ b: "ctrl+q" });
    expect(normalizeShortcuts("x")).toEqual({});
  });
});

describe("workspace (ТЗ 7.0.10, 7.0.14)", () => {
  it("first start: dark, tree 280 and properties 340 open, bottom closed, Главная", () => {
    const w = normalizeWorkspace(undefined);
    expect([w.theme, w.left, w.sizes.left, w.right, w.rightTab, w.sizes.right, w.bottom, w.ribbonTab]).toEqual([
      "dark", true, 280, true, "properties", 340, false, "home",
    ]);
    expect(DEFAULT_VIEW).toMatchObject({ grid: true, perspective: false, vexag: 1 });
  });

  it("sizes are clamped, garbage is replaced by defaults", () => {
    const w = normalizeWorkspace({ sizes: { left: 5000, right: 10, bottom: "x" }, theme: "pink", bottomTab: "nope", treeOrder: ["lines", "lines", "zzz"] });
    expect(w.sizes).toEqual({ left: 520, right: 260, bottom: 280 });
    expect(w.theme).toBe("dark");
    expect(w.bottomTab).toBe("table");
    expect(w.treeOrder[0]).toBe("lines");
    expect(w.treeOrder).toHaveLength(DEFAULT_TREE_ORDER.length);
  });

  it("the right dock: second call for the open tab closes it", () => {
    const a = setPanel(DEFAULT_WORKSPACE, "assistant");
    expect([a.right, a.rightTab]).toEqual([true, "assistant"]);
    expect(setPanel(a, "assistant").right).toBe(false);
    const b = setPanel(DEFAULT_WORKSPACE, "bottom", undefined, "log");
    expect([b.bottom, b.bottomTab]).toEqual([true, "log"]);
    expect(setPanel(b, "bottom", undefined, "log").bottom).toBe(false);
  });

  it("tree sections reorder by dragging", () => {
    expect(moveSection(DEFAULT_TREE_ORDER, "audit", "holes")[0]).toBe("audit");
    expect(moveSection(DEFAULT_TREE_ORDER, "holes", null).at(-1)).toBe("holes");
  });

  it("view settings: layers hidden up to 2000 keys, vexag only 1/2/3/5", () => {
    const v = normalizeView({ vexag: 4, hidden: Array.from({ length: 2500 }, (_, i) => `wf:${i}`), wireframeOpacity: 0.05 });
    expect(v.vexag).toBe(1);
    expect(v.hidden).toHaveLength(2000);
    expect(v.wireframeOpacity).toBe(0.7);
  });
});

import { searchPalette } from "../src/shell/palette";

describe("palette (ТЗ 7.0.7)", () => {
  const holes = Array.from({ length: 50_000 }, (_, i) => ({ holeId: `СКВ-${String(i + 1).padStart(5, "0")}`, setName: "Набор 1" }));

  it("finds commands by label and «tab › group»", () => {
    expect(searchPalette("тема", items, []).some((r) => r.kind === "command" && r.item.command === "theme.set")).toBe(true);
    expect(searchPalette("анализ графики", items, []).some((r) => r.kind === "command" && r.item.command === "chart.qq")).toBe(true);
  });

  it("«скв » searches only holes; at most 60 rows; fast on 50 000 holes", () => {
    const t0 = performance.now();
    const rows = searchPalette("скв 1", items, holes);
    expect(performance.now() - t0).toBeLessThan(50);
    expect(rows.every((r) => r.kind === "hole")).toBe(true);
    expect(rows.length).toBe(60);
    expect(searchPalette("с", items, holes).some((r) => r.kind === "hole")).toBe(false);
  });
});
