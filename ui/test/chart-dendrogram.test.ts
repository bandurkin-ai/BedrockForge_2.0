// The window «Дендрограмма» (ТЗ 7.27; ADR 0019 §11; перенос `DendrogramWindow` / `DendrogramChart` BF): no Ward
// among the links (Т7.27-2), groups at the cut (as `fcluster`, «distance»), the matrix in the order of the leaves
// and its CSV (a missing r stays empty), fewer than 2 components — the command unavailable with the reason, the
// command and the assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DendrogramChart } from "../src/charts/dendrogram/DendrogramChart";
import { DendrogramWindow } from "../src/charts/dendrogram/DendrogramWindow";
import { chosenFields, clustersAt, corrColor, dendrogramNotes, dendrogramRequest, dendrogramSummary, groupColor, LINKAGES, matrixCsv, strongest } from "../src/charts/dendrogram/model";
import type { DendrogramResult, NumericField } from "../src/modules/charts/api";
import { bindCharts } from "../src/modules/charts/bind";
import { commands } from "../src/modules/charts/commands";
import { defaultSettings, normalizeSettings, type DendrogramSettings } from "../src/modules/charts/settings";
import { chartsStore, readComponents } from "../src/modules/charts/state";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// A and B — r = 1 (merged at 0), C — far from both
const RESULT: DendrogramResult = {
  fields: ["A", "B", "C"],
  units: ["г/т", "г/т", "%"],
  correlation: [
    [1, 1, 0.2],
    [1, 1, null],
    [0.2, null, 1],
  ],
  pairs: [
    [5, 5, 4],
    [5, 5, 2],
    [4, 2, 5],
  ],
  rows: 6,
  used: 5,
  nonpositive_log: 1,
  linkage: [
    [0, 1, 0, 2],
    [2, 3, 0.8, 3],
  ],
  order: [0, 1, 2],
  icoord: [
    [5, 5, 15, 15],
    [10, 10, 25, 25],
  ],
  dcoord: [
    [0, 0, 0, 0],
    [0, 0.8, 0.8, 0],
  ],
  max_distance: 0.8,
  groups: null,
  conventions: "Расстояние 1 − r; связь — средняя (UPGMA).",
};

const NUMERIC: NumericField[] = [
  { name: "A", kind: "component", unit: "г/т", role: "component" },
  { name: "B", kind: "component", unit: "г/т", role: "component" },
  { name: "DENS", kind: "attribute", unit: null, role: "value" },
  { name: "length", kind: "length", unit: "м", role: "length" },
];

const base = (): DendrogramSettings => defaultSettings("dendrogram", 3);

describe("Ward is not offered (Т7.27-2)", () => {
  it("the links of the window, the command and the stored settings know no Ward", () => {
    expect(LINKAGES.map(([v]) => v)).toEqual(["average", "complete", "single", "weighted"]);
    expect(commands.find((c) => c.id === "chart.dendrogram")?.params?.linkage.enum).not.toContain("ward");
    expect(normalizeSettings("dendrogram", { linkage: "ward" }, 3).linkage).toBe("average");
    const html = renderToStaticMarkup(createElement(DendrogramWindow, { winId: "w", setId: 3, setName: "Набор", settings: base() }));
    expect(html).toContain('aria-label="Способ связи"');
    expect(html).not.toMatch(/value="ward"|Ward<\/option>/i);
    for (const label of ["Таблица", "Поля", "Коэффициент корреляции", "Вес", "Пропуски", "Расстояние", "Порог отсечения"]) expect(html).toContain(`aria-label="${label}"`);
  });
});

describe("groups at the cut", () => {
  it("merges at or below the cut join: r = 1 merges at height 0", () => {
    expect(clustersAt(RESULT.linkage, 3, RESULT.order, 0).leaf).toEqual([0, 0, 1]);
    expect(clustersAt(RESULT.linkage, 3, RESULT.order, 0.5)).toEqual({ leaf: [0, 0, 1], merge: [0, -1] });
    expect(clustersAt(RESULT.linkage, 3, RESULT.order, 0.8).leaf).toEqual([0, 0, 0]);
    expect(clustersAt(RESULT.linkage, 3, [2, 0, 1], 0.5).leaf).toEqual([1, 1, 0]); // numbered down the leaves
    expect(dendrogramNotes(RESULT, 0.5)).toContain("групп при пороге 0.5: 2");
  });

  it("the cut colours the leaves and the links under it; the brand colours first", () => {
    expect(groupColor(0)).toBe("#1f6bff");
    expect(groupColor(1)).toBe("#f46a1a");
    expect(groupColor(-1)).toBeNull();
    const html = renderToStaticMarkup(createElement(DendrogramChart, { result: RESULT, cut: 0.5, matrix: true, values: true, distanceLabel: "Расстояние 1 − r" }));
    expect(html).toContain('data-links="2"');
    expect(html).toContain('data-cut="0.5"');
    expect(html).toMatch(/fill:#1f6bff[^>]*data-leaf="A"/);
    expect(html).toMatch(/fill:#f46a1a[^>]*data-leaf="C"/);
    expect(html).toContain('data-matrix="3"');
    expect(html).not.toContain("NaN");
    const plain = renderToStaticMarkup(createElement(DendrogramChart, { result: RESULT, cut: null, matrix: false, values: false, distanceLabel: "d" }));
    expect(plain).not.toContain("data-matrix");
    expect(plain).not.toContain("data-cut");
  });
});

describe("the matrix", () => {
  it("CSV in the order of the leaves; a missing r stays empty", () => {
    expect(matrixCsv({ ...RESULT, order: [2, 0, 1] }).split("\r\n")).toEqual([';"C";"A";"B"', '"C";1;0.2;', '"A";0.2;1;1', '"B";;1;1']);
  });

  it("colours: positive — Copper Orange, negative — Cobalt, zero — Light Stone, missing — Slate", () => {
    expect(corrColor(1)).toBe("rgb(244,106,26)");
    expect(corrColor(-1)).toBe("rgb(31,107,255)");
    expect(corrColor(0)).toBe("rgb(230,233,238)");
    expect(corrColor(null)).toBe("#8a99a8");
    expect(strongest(RESULT)).toEqual({ a: "A", b: "B", r: 1 });
  });
});

describe("fields and the request", () => {
  it("every component by default (logarithms on); fewer than 2 fields — no request", () => {
    expect(base()).toMatchObject({ fields: [], log: true, method: "pearson", weight: "length", missing: "pairwise", distance: "1-r" });
    expect(chosenFields(base(), NUMERIC)).toEqual(["A", "B"]);
    expect(chosenFields({ fields: ["DENS", "A"] }, NUMERIC)).toEqual(["DENS", "A"]);
    expect(dendrogramRequest({ ...base(), fields: ["A"] }, NUMERIC)).toBeNull();
    expect(dendrogramRequest(base(), NUMERIC.slice(0, 1))).toBeNull();
    const r = dendrogramRequest({ ...base(), cut: 0.4 }, NUMERIC)!;
    expect(r).toMatchObject({ v: 1, set_id: 3, fields: [], cut: 0.4, negatives: "half_abs" });
    expect("view" in r).toBe(false);
  });
});

describe("the core: availability and the command", () => {
  let components = 1;
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    windowStore.set({ windows: [] });
    chartsStore.set({ components: {} });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        sent.push({ path: `${path}${u.search}`, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {} });
        if (path === "/stats/fields")
          return Promise.resolve(
            json({
              set_id: Number(u.searchParams.get("set_id")),
              tables: [
                { id: 12, name: "Литология", name_key: "литология", kind: "interval", rows: 3, rev: 1, numbers: [{ name: "length", unit: "м", role: "length" }], texts: [] },
                { id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 6, rev: 1, numbers: ["A", "B", "C"].slice(0, components).map((name) => ({ name, unit: null, role: "component" })), texts: [] },
              ],
            }),
          );
        if (path === "/stats/dendrogram") return Promise.resolve(json(RESULT));
        return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindCharts(app);
    app.store.set({ projectName: "Проект", projectReadOnly: true, setName: "Набор" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("fewer than 2 components — unavailable with the reason; more — available", async () => {
    components = 1;
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
    await vi.waitFor(() => expect(chartsStore.get().components[3]).toBe(1));
    expect(app.registry.reason("chart.dendrogram")).toBe("Нужно 2 и больше компонента");
    expect(app.registry.reason("chart.histogram")).toBeNull();
    expect((await app.registry.execute("chart.dendrogram", {})).result).toBe("Нужно 2 и больше компонента");
    components = 3;
    chartsStore.set({ components: {} });
    const { invalidateFields } = await import("../src/modules/charts/api");
    invalidateFields(3);
    await readComponents(3);
    expect(app.registry.reason("chart.dendrogram")).toBeNull();
  });

  it("chart.dendrogram opens on the table with the components; the assistant gets fields, groups, the strongest pair", async () => {
    components = 3;
    tablesStore.set({ sets: [{ id: 5, name: "Набор", holes: 2 }], setId: 5, tabs: [] });
    const r = await app.registry.execute("chart.dendrogram", { set_id: 5 });
    expect(r.ok).toBe(true);
    const win = windowStore.get().windows[0];
    expect(win).toMatchObject({ kind: "chart-dendrogram", title: "Дендрограмма · все компоненты · Пирсон · Набор" });
    expect(win.props?.settings).toMatchObject({ set_id: 5, table: "пробы", fields: [] });
    const a = await app.registry.execute("chart.dendrogram", { set_id: 5, cut: 0.5, method: "spearman" }, "claude");
    expect(sent.find((x) => x.path === "/stats/dendrogram")?.body).toMatchObject({ set_id: 5, table: "пробы", cut: 0.5, method: "spearman" });
    expect(a.result).toBe("Дендрограмма · все компоненты · Спирмен: полей 3, строк в расчёте 5, групп при пороге 0.5: 2; порядок листьев A, B, C; сильнее всего связаны A и B (r = 1)");
    expect(dendrogramSummary("Д", RESULT, null)).not.toContain("групп");
  });
});
