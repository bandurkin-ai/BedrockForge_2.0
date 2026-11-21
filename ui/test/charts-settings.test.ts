// The contract of the charts of Э13 (ADR 0019 §2, §9): the 16 commands with their parameters and stage stubs,
// the ribbon buttons found by Ctrl+K, chart settings over the defaults (stored JSON and command arguments are the
// same keys), names after the settings (ТЗ 7.28), the calculator that drops an overtaken answer.
import { describe, expect, it } from "vitest";
import { CHART_BUTTONS, CHART_GROUP_TITLE } from "../src/modules/charts/commands";
import { STATS_BUTTONS } from "../src/modules/stats/commands";
import { createCalculator, defaultField, type Clock } from "../src/modules/charts/api";
import { CHART_KINDS, chartName, defaultSettings, needsWeightField, normalizeSettings, parseList, plural, settingsFromArgs, windowTitle } from "../src/modules/charts/settings";
import { createRegistry, moduleCommands } from "../src/shell/catalog";
import { commandTools, EMPTY_CONTEXT, validateArgs } from "../src/shell/commands";
import { searchPalette } from "../src/shell/paletteSearch";
import type { FlatItem } from "../src/shell/ribbonConfig";

const E13 = [
  "chart.histogram", "chart.qq", "chart.dendrogram", "chart.scatter", "chart.box", "chart.probplot", "chart.contact", "chart.ternary", "chart.domains",
  "chart.open", "chart.rename", "chart.delete", "chart.select_clear", "stats.open", "stats.swath", "stats.swath_3d",
];

describe("commands of Э13 (ADR 0019 §9)", () => {
  const all = moduleCommands();
  const byId = new Map(all.map((c) => [c.id, c]));

  it("all 16 are in the registry, unavailable with the stage until their windows are bound", () => {
    for (const id of E13) expect(byId.has(id), id).toBe(true);
    const r = createRegistry();
    for (const id of E13) expect(r.reason(id, { ...EMPTY_CONTEXT, hasProject: true, hasSet: true }), id).toBe("Функция появится на этапе Э13");
  });

  it("parameters as in §9: a chart without arguments opens with defaults; delete is confirmed; clearing is the UI's", () => {
    const c = (id: string) => byId.get(id)!;
    expect(Object.keys(c("chart.histogram").params ?? {}).sort()).toEqual(
      ["set_id", "table", "field", "weight", "weight_field", "log", "bin_width", "bins", "start", "end", "negatives", "exclude_zero", "bottom_cut", "bottom_mode", "top_cut", "top_mode"].sort(),
    );
    expect(Object.keys(c("chart.qq").params ?? {}).sort()).toEqual(["set_id", "table", "field", "distribution", "y_set_id", "y_table", "y_field", "weight", "holes", "y_holes", "quantiles", "log"].sort());
    expect(c("chart.dendrogram").params?.linkage.enum).toEqual(["average", "complete", "single", "weighted"]); // Т7.27-2: no Ward
    expect(Object.keys(c("chart.contact").params ?? {})).toEqual(expect.arrayContaining(["a", "b", "distance", "step", "wireframe_id", "domain_table"]));
    for (const id of E13.slice(0, 9)) expect(c(id).required ?? [], id).toEqual([]);
    expect(c("chart.open").required).toEqual(["chart_id"]);
    expect(c("chart.rename").required).toEqual(["chart_id", "name"]);
    expect(all.filter((x) => E13.includes(x.id) && x.confirm).map((x) => x.id)).toEqual(["chart.delete"]);
    expect(c("chart.select_clear").uiOnly).toBe(true);
    expect(c("stats.open").params?.analysis.enum).toEqual(["declus", "nscore", "bootstrap", "variogram", "map", "local"]);
    expect(Object.keys(c("stats.swath").params ?? {})).toEqual(["swath_id", "direction", "azimuth", "dip", "roll", "width", "anchor", "range_from", "range_to"]);
    expect(c("stats.swath_3d").required).toEqual(["swath_id", "visible"]);
    expect(validateArgs(c("chart.histogram"), { weight: "length_field", bins: 40, top_cut: 12.5 })).toBeNull();
    expect(validateArgs(c("chart.histogram"), { bins: 2.5 })).toMatch(/integer/);
  });

  it("the assistant sees them as tools (the UI-only one aside)", () => {
    const names = commandTools(all).map((t) => t.name);
    for (const id of E13.filter((x) => x !== "chart.select_clear")) expect(names, id).toContain(`ui_${id.replace(/\./g, "_")}`);
  });

  it("the ribbon buttons «Анализ → Графики» name registered chart commands; Ctrl+K finds each by its label", () => {
    expect(CHART_BUTTONS).toHaveLength(9);
    expect(CHART_BUTTONS.map((b) => b.command)).toEqual(E13.slice(0, 9));
    const items: FlatItem[] = [
      ...CHART_BUTTONS.map((b) => ({ ...b, icon: "chart" as const, group: `Анализ › ${CHART_GROUP_TITLE}` })),
      ...STATS_BUTTONS.map((b) => ({ command: "stats.open", args: { analysis: b.analysis }, label: b.label, icon: "chart" as const, group: "Анализ › Статистика" })),
      { command: "stats.swath", label: "SWATH PLOT", icon: "swath" as const, group: "Анализ › Статистика" },
    ];
    for (const it of items) {
      expect(byId.has(it.command), it.command).toBe(true);
      expect(searchPalette(it.label.toLowerCase(), items, []).some((r) => r.kind === "command" && r.item.label === it.label), it.label).toBe(true);
    }
    expect(searchPalette("анализ графики", items, []).filter((r) => r.kind === "command")).toHaveLength(9);
    expect(STATS_BUTTONS.filter((b) => b.stage === "Э16").map((b) => b.analysis)).toEqual(["variogram", "map", "local"]);
  });
});

describe("chart settings (ADR 0019 §2)", () => {
  it("defaults: weight by length, top cut caps, bottom excludes; auto bins; v: 1", () => {
    const h = defaultSettings("histogram", 3);
    expect(h).toMatchObject({ v: 1, set_id: 3, table: null, field: null, weight: "length", top_mode: "cap", bottom_mode: "exclude", bin_width: null, bins: null, log: false });
    expect(h.view).toMatchObject({ y: "percent", cumulative: true, marks: { mean: true, median: true } });
    for (const k of CHART_KINDS) expect(defaultSettings(k, 1).v).toBe(1);
    expect(defaultSettings("qq", 1).quantiles).toBe(99);
    expect(defaultSettings("dendrogram", 1).linkage).toBe("average");
  });

  it("stored settings keep only valid values over the defaults; a missing cut stays empty, not 0", () => {
    const h = normalizeSettings("histogram", { field: " AU ", weight: "bogus", bins: 2.5, bin_width: 0.25, top_cut: null, bottom_cut: "1", extra: 1, view: { y: "count", color: "#ABCDEF", marks: { p90: true } } }, 7);
    expect(h).toMatchObject({ set_id: 7, field: "AU", weight: "length", bins: null, bin_width: 0.25, top_cut: null, bottom_cut: null });
    expect(h.view).toEqual({ y: "count", cumulative: true, marks: { mean: true, median: true, p90: true, p975: false, cuts: true }, color: "#abcdef" });
    expect("extra" in h).toBe(false);
    expect(normalizeSettings("histogram", "garbage", 2)).toEqual(defaultSettings("histogram", 2));
  });

  it("command arguments are the settings: lists from text, the active set by default", () => {
    const q = settingsFromArgs("qq", { field: "AU", distribution: "sample", holes: "H1, H2  H3;H1", y_set_id: 5 }, 2);
    expect(q).toMatchObject({ set_id: 2, field: "AU", distribution: "sample", holes: ["H1", "H2", "H3"], y_set_id: 5, y_holes: [] });
    expect(settingsFromArgs("histogram", { set_id: 9, field: "CU" }, 2).set_id).toBe(9);
    // `distance` is a choice of the dendrogram and a number of the contact analysis
    expect(settingsFromArgs("dendrogram", { distance: "1-abs" }, 1).distance).toBe("1-abs");
    expect(settingsFromArgs("contact", { distance: 15, step: 0.5, a: "OX", b: "SU" }, 1)).toMatchObject({ distance: 15, step: 0.5, a: "OX", b: "SU" });
    expect(settingsFromArgs("contact", { distance: "1-r" }, 1).distance).toBe(20);
    expect(parseList(" a ,b;; c ")).toEqual(["a", "b", "c"]);
    expect(needsWeightField({ weight: "length_field", weight_field: null })).toBe(true);
    expect(needsWeightField({ weight: "length", weight_field: null })).toBe(false);
  });

  it("names follow the settings (ТЗ 7.28)", () => {
    const h = defaultSettings("histogram", 1);
    expect(chartName("histogram", { ...h, field: "AU" })).toBe("Гистограмма · AU · по длине");
    expect(chartName("histogram", { ...h, field: "AU", weight: "length_field", weight_field: "DENS", log: true })).toBe("Гистограмма · AU · длина × DENS · log");
    expect(chartName("histogram", { ...h, field: "length", weight: "none" })).toBe("Гистограмма · длина · без веса");
    const q = defaultSettings("qq", 1);
    expect(chartName("qq", { ...q, field: "AU" })).toBe("Q-Q · AU · логнормальное");
    expect(chartName("qq", { ...q, field: "AU", distribution: "sample", y_field: "AG" })).toBe("Q-Q · AU / AG · две выборки");
    const d = defaultSettings("dendrogram", 1);
    expect(chartName("dendrogram", { ...d, fields: ["A", "B", "C", "D", "E"], method: "spearman" })).toBe("Дендрограмма · 5 полей · Спирмен");
    expect(chartName("dendrogram", d)).toBe("Дендрограмма · все компоненты · Пирсон");
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, "поле", "поля", "полей"))).toEqual(["поле", "поля", "полей", "полей", "поле", "поля", "полей"]);
    for (const k of CHART_KINDS) expect(chartName(k, defaultSettings(k, 1)).length, k).toBeGreaterThan(3);
    expect(windowTitle("Гистограмма · AU · по длине", "Демо")).toBe("Гистограмма · AU · по длине · Демо");
  });

  it("the default field is the first component", () => {
    expect(defaultField({ numeric: [{ name: "length", kind: "length", unit: "м", count: 3 }, { name: "DENS", kind: "attribute", unit: null, count: 3 }, { name: "AU", kind: "component", unit: "г/т", count: 3 }] })).toBe("AU");
    expect(defaultField({ numeric: [{ name: "length", kind: "length", unit: "м", count: 3 }] })).toBeNull();
  });
});

describe("calculation of a chart (ADR 0019 §11 «Отклик»)", () => {
  function fakeClock() {
    let now = 0;
    const timers: { at: number; fn: () => void; id: number }[] = [];
    let n = 0;
    const clock: Clock = {
      set: (fn, ms) => {
        timers.push({ at: now + ms, fn, id: ++n });
        return n;
      },
      clear: (t) => {
        const i = timers.findIndex((x) => x.id === t);
        if (i >= 0) timers.splice(i, 1);
      },
    };
    const advance = (ms: number) => {
      now += ms;
      for (const t of [...timers].sort((a, b) => a.at - b.at)) {
        if (t.at > now) continue;
        timers.splice(timers.indexOf(t), 1);
        t.fn();
      }
    };
    return { clock, advance };
  }

  it("sends 300 ms after the last edit, the same settings once, drops an overtaken answer", async () => {
    const { clock, advance } = fakeClock();
    const pending: { body: number; resolve: (v: string) => void }[] = [];
    const applied: string[] = [];
    const busy: boolean[] = [];
    const calc = createCalculator<number, string>((body) => new Promise((resolve) => pending.push({ body, resolve })), (a) => applied.push(a), () => undefined, (b) => busy.push(b), 300, clock);
    calc.request(1);
    advance(200);
    calc.request(2);
    advance(299);
    expect(pending).toHaveLength(0);
    advance(1);
    expect(pending.map((p) => p.body)).toEqual([2]);
    calc.request(2); // unchanged — not sent again
    calc.request(3);
    advance(300);
    expect(pending.map((p) => p.body)).toEqual([2, 3]);
    pending[1].resolve("три");
    pending[0].resolve("два"); // overtaken: dropped
    await Promise.resolve();
    await Promise.resolve();
    expect(applied).toEqual(["три"]);
    expect(busy.at(-1)).toBe(false);
    calc.stop();
    calc.request(4);
    advance(300);
    expect(pending).toHaveLength(3);
    pending[2].resolve("четыре");
    await Promise.resolve();
    expect(applied).toEqual(["три"]);
  });
});
