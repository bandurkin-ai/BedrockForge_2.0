// The windows «Ящики с усами» and «По доменам» (ТЗ 8.3; ADR 0019 §4): the box by the logarithms on a log scale, the
// outliers over 5000 shown in the notes, the table and its CSV (empty stays empty), the domain from another table,
// the command and the assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeScale } from "../src/charts/core/axisScale";
import { BoxChart } from "../src/charts/box/BoxChart";
import { BoxWindow } from "../src/charts/box/BoxWindow";
import { boxCsv, boxDomain, boxHeader, boxNotes, boxPixels, boxRequest, boxSummary, boxTable, groupLabel } from "../src/charts/box/model";
import { DomainStatsWindow, domainsCsv, domainsRequest, domainsSummary, domainsTable } from "../src/charts/domains/DomainStatsWindow";
import type { BoxGroup, BoxResult, DomainRow, DomainsResult } from "../src/modules/charts/apiMore";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings, normalizeSettings, type BoxSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const COUNTS = { rows: 12, missing: 1, bad_weight: 0, negative: 0, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 0, top_excluded: 0, used: 10, no_domain: 1 };

const group = (label: string | null, q: [number, number, number], extra: Partial<BoxGroup> = {}): BoxGroup => ({ label, n: 5, weight_sum: 5, mean: q[1] * 1.2, q1: q[0], median: q[1], q3: q[2], whisker_low: q[0] / 2, whisker_high: q[2] * 2, min: q[0] / 2, max: q[2] * 5, outliers: [q[2] * 5], outliers_total: 1, ...extra });

const BOX: BoxResult = {
  set_id: 3,
  field: "AU",
  domain: "ROCK",
  domain_table: null,
  domain_outside: 0,
  log: true,
  weighted: true,
  counts: COUNTS,
  groups: [group("OX", [1, 10, 100], { role: "domain", geometric_mean: 8, outliers_low: 0, outliers_high: 1 }), group("SU", [0.5, 1, 2]), group(null, [2, 3, 4], { mean: null, outliers: [], outliers_total: 0 })],
  outliers_shown: 5000,
  outliers_total: 7312,
};

describe("box plots", () => {
  it("settings → request: a field and a weight field; the domain from another table goes along", () => {
    const d = defaultSettings("box", 3);
    expect(d).toMatchObject({ weight: "length", top_mode: "cap", log: false, domain: null, domain_table: null, view: { mean: true, outliers: true } });
    expect(boxRequest(d)).toBeNull();
    const s: BoxSettings = { ...d, field: "AU", domain: "ROCK", domain_table: "литология" };
    const r = boxRequest(s)!;
    expect(r).toMatchObject({ field: "AU", domain: "ROCK", domain_table: "литология", weight: "length" });
    expect("view" in r).toBe(false);
    expect(boxRequest({ ...s, weight: "length_field" })).toBeNull();
    expect(chartName("box", s)).toBe("Ящики с усами · AU · по ROCK");
    expect(normalizeSettings("box", { field: "AU", top_cut: 30, view: { outliers: false } }, 3)).toMatchObject({ top_cut: 30, view: { mean: true, outliers: false } });
  });

  it("log scale: the box is by the logarithms — Q1 = 1, median = 10, Q3 = 100 are equal steps", () => {
    const sy = makeScale("log", boxDomain(BOX, true)!, 400, 0);
    const px = boxPixels(BOX.groups[0], sy)!;
    expect(px.q1 - px.median).toBeCloseTo(px.median - px.q3, 9);
    // on a linear axis they are not
    const lin = boxPixels(BOX.groups[0], makeScale("linear", boxDomain({ groups: [BOX.groups[0]] }, false)!, 400, 0))!;
    expect(Math.abs(lin.q1 - lin.median - (lin.median - lin.q3))).toBeGreaterThan(50);
    // an empty mean — no diamond, not 0
    expect(boxPixels(BOX.groups[2], sy)?.mean).toBeNull();
    expect(boxPixels({ ...BOX.groups[1], q1: null }, sy)).toBeNull();
    expect(boxDomain({ groups: [group("X", [-1, 0, 1])] }, true)).toMatchObject({ min: expect.any(Number) });
  });

  it("the chart: a box, a median and n per domain; outliers over 5000 — how many are not drawn", () => {
    const html = renderToStaticMarkup(createElement(BoxChart, { result: BOX, mean: true, outliers: true, color: null, yLabel: "AU, г/т" }));
    expect(html).toContain('data-boxes="3"');
    for (const b of ["OX", "SU", "без домена"]) expect(html).toContain(`data-box="${b}"`);
    expect(html.match(/data-median=/g)).toHaveLength(3);
    expect(html.match(/data-mean="1"/g)).toHaveLength(2);
    expect(html).not.toContain("NaN");
    expect(boxNotes(BOX, { negatives: "keep" })).toEqual([
      "строк 12, в расчёте 10",
      "без значения 1",
      "без кода домена 1",
      `выбросов показано ${(5000).toLocaleString("ru-RU")} из ${(7312).toLocaleString("ru-RU")} (крайние оставлены)`,
      "квартили и усы — по логарифмам; ◇ — среднее, ○ — среднее геометрическое",
    ]);
    expect(boxNotes({ ...BOX, outliers_shown: 3, outliers_total: 3, log: false }, { negatives: "keep" }).slice(-1)).toEqual(["выбросов 3"]);
    expect(groupLabel(null, null)).toBe("все пробы");
  });

  it("a log scale: the geometric mean — a circle at its value, in the tip and in the assistant's text", () => {
    const sy = makeScale("log", boxDomain(BOX, true)!, 400, 0);
    expect(boxPixels(BOX.groups[0], sy)!.geo).toBeCloseTo(sy.map(8)!, 9);
    expect(boxPixels(BOX.groups[1], sy)!.geo).toBeNull(); // not given — nothing drawn
    const html = renderToStaticMarkup(createElement(BoxChart, { result: BOX, mean: true, outliers: true, color: null, yLabel: "AU, г/т" }));
    expect(html.match(/data-geomean="1"/g)).toHaveLength(1);
    expect(renderToStaticMarkup(createElement(BoxChart, { result: BOX, mean: false, outliers: true, color: null, yLabel: "AU" }))).not.toContain("data-geomean");
    expect(boxSummary("Я", BOX)).toContain("OX: n 5, среднее 12, среднее геометрическое 8, медиана 10");
  });

  it("the table and its CSV: a missing mean stays «—» / empty", () => {
    const lin = { ...BOX, log: false };
    expect(boxTable(lin)[2]).toEqual(["без домена", "5", "—", "2", "3", "4", "1", "8", "1", "20", "0"]);
    const csv = boxCsv(lin).split("\r\n");
    expect(csv[0]).toBe('"Домен";"n";"Среднее";"Q1";"Медиана";"Q3";"Ус нижний";"Ус верхний";"Мин";"Макс";"Выбросов"');
    expect(csv[3]).toBe('"без домена";5;;2;3;4;1;8;1;20;0');
    // a log scale: the geometric mean after the mean (none — «—» / empty)
    expect(boxHeader(BOX)).toEqual(["Домен", "n", "Среднее", "Среднее геом.", "Q1", "Медиана", "Q3", "Ус нижний", "Ус верхний", "Мин", "Макс", "Выбросов"]);
    expect(boxTable(BOX)[2]).toEqual(["без домена", "5", "—", "—", "2", "3", "4", "1", "8", "1", "20", "0"]);
    expect(boxCsv(BOX).split("\r\n")[3]).toBe('"без домена";5;;;2;3;4;1;8;1;20;0');
    expect(boxSummary("Я", lin)).toBe("Я: OX: n 5, среднее 12, медиана 10; SU: n 5, среднее 1,2, медиана 1; без домена: n 5, среднее —, медиана 3");
  });
});

const row = (label: string | null, n: number, mean: number | null, decl: number | null = null): DomainRow => ({ label, n, length: n * 2, mean, decl_mean: decl, median: mean, std: 1, cv: mean ? 1 / mean : null, min: 0, max: 9, p10: 0.1, p50: mean, p90: 8 });

const DOMAINS: DomainsResult = {
  set_id: 3,
  field: "AU",
  domain: "ROCK",
  weighted: true,
  counts: COUNTS,
  rows: [row("OX", 6, 2), row("SU", 4, null)],
  total: row(null, 10, 1.5),
  decl_field: null,
  decl_hint: "В таблице нет поля DECL_W: «Обработка проб → Декластеризация».",
};

describe("statistics by domains", () => {
  it("the table: the domains and «Итого»; without DECL_W — empty; the CSV keeps empty cells", () => {
    expect(domainsRequest(defaultSettings("domains", 3))).toBeNull();
    expect(domainsRequest({ ...defaultSettings("domains", 3), field: "AU" })).toMatchObject({ field: "AU", weight: "length" });
    const t = domainsTable(DOMAINS);
    expect(t.map((r) => r[0])).toEqual(["OX", "SU", "Итого"]);
    expect(t[1][3]).toBe("—");
    expect(t[0][4]).toBe("—");
    const csv = domainsCsv(DOMAINS).split("\r\n");
    expect(csv).toHaveLength(4);
    expect(csv[2]).toBe('"SU";4;8;;;;1;;0;9;0.1;;8');
    expect(domainsSummary("Д", DOMAINS)).toBe("Д: проб 10, среднее 1,5, медиана 1,5, CV 0,667; OX: n 6, среднее 2; SU: n 4, среднее —");
  });
});

describe("the windows and the commands", () => {
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let app: App;
  const FIELDS = {
    set_id: 3,
    tables: [
      { id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 12, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }, { name: "length", unit: "м", role: "length" }], texts: [{ name: "ROCK", role: "value", values: 2, labels: ["OX", "SU"], more: false }] },
      { id: 12, name: "Литология", name_key: "литология", kind: "interval", rows: 4, rev: 1, numbers: [], texts: [{ name: "LITH", role: "value", values: 3, labels: ["A", "B", "C"], more: false }] },
    ],
  };

  beforeEach(() => {
    sent.length = 0;
    windowStore.set({ windows: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method: init?.method ?? "GET", path: `${path}${u.search}`, body });
        if (path === "/stats/fields") return Promise.resolve(json(FIELDS));
        if (path === "/stats/box") return Promise.resolve(json(BOX));
        if (path === "/stats/domains") return Promise.resolve(json(DOMAINS));
        return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindCharts(app);
    app.store.set({ projectName: "Проект", projectReadOnly: true, setName: "Набор" });
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("the windows render their forms before any answer", () => {
    const box = renderToStaticMarkup(createElement(BoxWindow, { winId: "w", setId: 3, setName: "Набор", settings: { ...defaultSettings("box", 3), field: "AU" } }));
    for (const label of ["Таблица", "Поле", "Таблица домена", "Домен", "Вес", "Отсечка сверху", "Цвет ящика"]) expect(box, label).toContain(`aria-label="${label}"`);
    const dom = renderToStaticMarkup(createElement(DomainStatsWindow, { winId: "w", setId: 3, setName: "Набор", settings: { ...defaultSettings("domains", 3), field: "AU" } }));
    for (const label of ["Таблица", "Поле", "Таблица домена", "Домен", "Вес"]) expect(dom, label).toContain(`aria-label="${label}"`);
  });

  it("chart.box and chart.domains: the first component; the domain of another table goes to the core", async () => {
    expect((await app.registry.execute("chart.box", { domain: "LITH", domain_table: "литология" })).ok).toBe(true);
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "chart-box", title: "Ящики с усами · AU · по LITH · Набор" });
    const a = await app.registry.execute("chart.box", { field: "AU", domain: "ROCK", log: true }, "claude");
    expect(sent.find((x) => x.path === "/stats/box")?.body).toMatchObject({ field: "AU", domain: "ROCK", log: true, table: "пробы" });
    expect(a.result).toMatch(/: OX: n 5, среднее 12, среднее геометрическое 8, медиана 10; SU/);
    const d = await app.registry.execute("chart.domains", { domain: "ROCK" }, "claude");
    expect(sent.find((x) => x.path === "/stats/domains")?.body).toMatchObject({ field: "AU", domain: "ROCK" });
    expect(d.result).toMatch(/^По доменам · AU · ROCK.*: проб 10, среднее 1.5/);
  });
});
