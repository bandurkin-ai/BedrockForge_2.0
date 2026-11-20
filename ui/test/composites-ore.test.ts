// The window «Рудные интервалы (ГКЗ)» (ТЗ 7.35 Д-26; ADR 0018 §8–11, решения 19–22; Э12 12.30): the body of the job
// (thickness, «по контактам»), МС = Сб·Мрт and Смин = Сб as placeholders, the classes of the samples and of the
// rows in brand colours, `frame` hidden by default, LABEL only of `ore`, the protocol in the form И1, сл. 17,
// the summary, the export of the protocol, the table and the display after the job. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { assignLanes, BRAND, CLASS_STYLE, classColor, finalRows, laneCount, legendOf, mix, oreClass, ORE_CLASSES, sampleClasses, textOn } from "../src/charts/column/classes";
import { OreProtocol } from "../src/modules/composites/OreProtocol";
import { DISPLAY_NAME, OreColumns, OreSummaryView, OreWindow, showOreResult } from "../src/modules/composites/OreWindow";
import { NO_SOURCE, toolBody, defaultValues } from "../src/modules/composites/params";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { autoValues, eventText, fetchOrePreview, fetchProtocol, ore, oreFinish, oreLabel, protocolExportBody, protocolFileName, protocolGrid, startProtocolExport, summaryLines, toOreAnswer, type OreEvent } from "../src/modules/composites/tools/ore";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const sent: { path: string; method: string; body: Record<string, unknown> }[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "") + u.search;
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, method: init?.method ?? "GET", body });
      if (path === "/composites/ore/preview") return Promise.resolve(json(RAW));
      if (path.startsWith("/composites/results/9/protocol")) return Promise.resolve(init?.method === "POST" ? json({ source: "core", id: 77 }) : json(RAW));
      if (path.startsWith("/drillholes/sets/3/tables?") || path === "/drillholes/sets/3/tables")
        return Promise.resolve(json({ set: { id: 3, name: "Набор" }, tables: [{ table: "5", kind: "assay", name: "Пробы", rows: 8, rev: 1 }, { table: "12", kind: "interval", name: "Рудные интервалы", rows: 4, rev: 1 }] }));
      if (path.startsWith("/drillholes/sets/3/tables/12")) return Promise.resolve(json({ columns: [], rows: [], total: 0, rev: 1 }));
      return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  tablesStore.set({ setId: null, tabs: [], table: null });
});

// Т8.2-15 «строгий»: 1,5; 1,5; 0,8; 1,2; 0,9; 0,9; 1,5; 1,5 — ore 0–4 м @ 1,25 и 6–8 м @ 1,5; промежуток 4–6 м
const EV = (o: Partial<OreEvent>): Partial<OreEvent> => ({ line: 0, stage: 0, side: "", ore: false, compared: "", m: null, c: null, mc: null, from: null, to: null, ...o });
const RAW = {
  hole: "С-1",
  unit: "г/т",
  samples: {
    from: [0, 1, 2, 3, 4, 5, 6, 7],
    to: [1, 2, 3, 4, 5, 6, 7, 8],
    values: { AU: [1.5, 1.5, 0.8, 1.2, 0.9, 0.9, 1.5, 1.5], ORE_CLS: ["ore", "ore", "waste_in", "ore", "waste", "waste", "ore", "ore"], ORE_ID: [1, 1, 1, 1, null, null, 2, 2] },
  },
  result: {
    from: [0, 4, 6, 2, -1, 8],
    to: [4, 6, 8, 3, 0, 11],
    values: {
      CLASS: ["ore", "waste", "ore", "waste_in", "frame", "frame"],
      NESTED: [0, 0, 0, 1, 1, 1],
      M: [4, 2, 2, 1, 1, 3],
      MC: [5, 1.8, 3, 0.8, null, null],
      LABEL: ["4,0 м @ 1,25 г/т (МС 5,0)", "", "2,0 м @ 1,50 г/т (МС 3,0)", "", "", ""],
    },
  },
  protocol: {
    events: [
      EV({ step: 1, kind: "unit", ore: true, row0: 0, row1: 1, m: 2, c: 1.5, mc: 3 }),
      EV({ step: 1, kind: "unit", row0: 2, row1: 2, m: 1, c: 0.8, mc: 0.8 }),
      EV({ step: 1, kind: "unit", ore: true, row0: 3, row1: 3, m: 1, c: 1.2, mc: 1.2 }),
      EV({ step: 1, kind: "unit", row0: 4, row1: 5, m: 2, c: 0.9, mc: 1.8 }),
      EV({ step: 1, kind: "unit", ore: true, row0: 6, row1: 7, m: 2, c: 1.5, mc: 3 }),
      EV({ step: 2, stage: 1, kind: "trim", side: "upper", ore: true, compared: "cutoff", decision: "pass", row0: 0, row1: 2, m: 3, c: 1.2667 }),
      EV({ step: 2, stage: 1, kind: "trim", side: "lower", ore: true, compared: "cutoff", decision: "pass", row0: 2, row1: 3, m: 2, c: 1.0 }),
      EV({ step: 2, stage: 1, kind: "trim", side: "upper", ore: true, compared: "cutoff", decision: "rejected_c", row0: 3, row1: 5, m: 3, c: 1.0 - 0.0333 }),
      EV({ step: 2, stage: 1, kind: "unit", ore: true, decision: "joined", row0: 0, row1: 3, m: 4, c: 1.25, mc: 5 }),
      EV({ step: 5, kind: "interval", decision: "ore", compared: "m_min", row0: 0, row1: 3, m: 4, c: 1.25, mc: 5 }),
      EV({ step: 5, kind: "interval", decision: "ore", compared: "mc_min", row0: 6, row1: 7, m: 2, c: 1.5, mc: 3 }),
    ],
  },
  summary: {
    classes: [
      { cls: "ore", nested: false, count: 2, m: 6, m_mean: 3, c: 8 / 6, mc: 8 },
      { cls: "waste", nested: false, count: 1, m: 2, m_mean: 2, c: 0.9, mc: 1.8 },
      { cls: "waste_in", nested: true, count: 1, m: 1, m_mean: 1, c: 0.8, mc: 0.8 },
    ],
    domains: [],
    length: 8,
    rows: 4,
  },
  set_summary: null,
  notes: [],
};

describe("the body of the job", () => {
  const src = { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] };
  const values = (v: Record<string, string>) => ({ ...defaultValues(ore.params), cutoff: "1", m_min: "3", m_waste_max: "3", ...v });

  it("thickness — length along the hole or a field; МС and Смин left out when empty (the core: Сб·Мрт, = Сб)", () => {
    const b = toolBody(ore, src, values({})).body!;
    expect(b).toMatchObject({ set_id: 3, table: "5", field: "AU", cutoff: 1, m_min: 3, m_waste_max: 3, mode: "strict_excl", missing: "break", thickness: { source: "length" } });
    expect(b).not.toHaveProperty("mc_min");
    expect(b).not.toHaveProperty("c_min");
    expect(b).not.toHaveProperty("thickness_field");
    expect(toolBody(ore, src, values({ thickness_field: "TT", mc_min: "2,5", c_min: "1,2" })).body).toMatchObject({ thickness: { source: "field", field: "TT" }, mc_min: 2.5, c_min: 1.2 });
  });

  it("«по контактам» needs a domain; «Коды тела» → contacts_values (решение 22), wrong codes never reach the core", () => {
    expect(toolBody(ore, src, values({ mode: "contacts" })).reason).toMatch(/выберите домен/);
    const dom = { ...src, domain: { kind: "field" as const, table: "7", field: "LITO" } };
    expect(toolBody(ore, dom, values({ mode: "contacts" })).body).not.toHaveProperty("contacts_values");
    expect(toolBody(ore, dom, values({ mode: "contacts", contacts_values: "2, 5; 2" })).body).toMatchObject({ domain: { table: "7", field: "LITO" }, contacts_values: [2, 5] });
    expect(toolBody(ore, dom, values({ mode: "contacts", contacts_values: "2, -1" })).reason).toMatch(/Коды тела/);
    // the codes are a form of «по контактам» only
    expect(toolBody(ore, dom, values({ contacts_values: "2" })).body).not.toHaveProperty("contacts_values");
    expect(oreFinish({ mode: "strict", thickness_field: "" })).toEqual({ mode: "strict", thickness: { source: "length" } });
  });

  it("a value out of range is marked and the job cannot start", () => {
    const r = toolBody(ore, src, values({ cutoff: "-1" }));
    expect(r.body).toBeNull();
    expect(r.errors.cutoff).toMatch(/Бортовое/);
  });

  it("МС = Сб·Мрт and Смин = Сб are shown as the placeholders of the empty fields", () => {
    expect(autoValues({ cutoff: "1,5", m_min: "8" })).toEqual({ mc_min: "авто: 12", c_min: "выключено: 1,5" });
    expect(autoValues({ cutoff: "", m_min: "8" }).mc_min).toBe("авто: Сб·Мрт");
  });
});

describe("classes (решение 20)", () => {
  it("every colour is a brand colour or a mix of two; ore is Copper Orange", () => {
    expect(CLASS_STYLE.ore.color).toBe(BRAND.copper);
    expect(CLASS_STYLE.interlayer.color).toBe(BRAND.cobalt);
    expect(CLASS_STYLE.subgrade.color).toBe(mix(BRAND.copper, BRAND.stone, 0.55));
    expect(new Set(ORE_CLASSES.map((c) => CLASS_STYLE[c].color)).size).toBe(6);
    expect(textOn(BRAND.copper)).toBe("#ffffff");
    expect(textOn(BRAND.stone)).toBe(BRAND.anthracite);
    expect(oreClass(3)).toBe("interlayer");
    expect(classColor("x")).toBeNull();
  });

  it("a sample without a value has no class (never «безрудный»); an ore sample below Сб is a включённый прослой", () => {
    expect(sampleClasses(["ore", "ore", "waste", null], [1.5, 0.8, null, 2], 1)).toEqual(["ore", "waste_in", null, null]);
    expect(sampleClasses(["ore"], [1], 1)).toEqual(["ore"]); // equality is ore
  });

  it("the final rows: NESTED = 0 first, frame hidden by default, LABEL only of ore", () => {
    const a = toOreAnswer(RAW);
    const rows = finalRows(a.result, false);
    expect(rows.map((r) => r.cls)).toEqual(["ore", "waste", "ore", "waste_in"]);
    expect(rows.filter((r) => r.label).map((r) => r.cls)).toEqual(["ore", "ore"]);
    expect(finalRows(a.result, true).filter((r) => r.cls === "frame")).toHaveLength(2);
    expect(legendOf(["ore", "frame"], false).map((l) => l.cls)).toEqual(["ore"]);
  });

  it("lanes: overlapping spans side by side, disjoint ones share a lane", () => {
    const lanes = assignLanes([{ row0: 0, row1: 2 }, { row0: 2, row1: 3 }, { row0: 3, row1: 5 }, { row0: 6, row1: 7 }]);
    expect(lanes).toEqual([0, 1, 0, 0]);
    expect(laneCount(lanes)).toBe(2);
    expect(laneCount([])).toBe(0);
  });
});

describe("the answers and the protocol", () => {
  it("the preview is asked with {params, hole_id}; the protocol of a result by the name of the hole", async () => {
    const a = await fetchOrePreview({ cutoff: 1 }, 41);
    expect(sent[0]).toMatchObject({ path: "/composites/ore/preview", method: "POST", body: { params: { cutoff: 1 }, hole_id: 41 } });
    expect(a.events).toHaveLength(11);
    expect(a.unit).toBe("г/т");
    await fetchProtocol(9, "С-1/2");
    expect(sent[1].path).toBe(`/composites/results/9/protocol?hole=${encodeURIComponent("С-1/2")}`);
    expect(toOreAnswer({}).events).toEqual([]);
    expect(toOreAnswer({ protocol: [{ step: 1, kind: "unit", row0: 2, row1: 1 }] }).events[0].row1).toBe(2);
  });

  it("the form И1: N / IN₀, C₁ with two lanes for overlapping прирезки, IN₁, Итог", () => {
    const a = toOreAnswer(RAW);
    const g = protocolGrid(a.events, 8, "г/т");
    expect(g.heads.map((h) => [h.title, h.lanes])).toEqual([
      ["N / IN₀", 1],
      ["C₁", 2],
      ["IN₁", 1],
      ["Итог", 1],
    ]);
    expect(g.rows).toHaveLength(8);
    const first = g.rows[0][0];
    expect(first).toMatchObject({ text: "Р 2 м · 1,5", tone: "ore", rowSpan: 2 });
    expect(g.rows[1][0]).toBe("covered");
    expect(g.rows[0][4]).toMatchObject({ text: "4,0 м @ 1,25 г/т (МС 5,0)", tone: "ore", rowSpan: 4 });
    expect(g.rows[4][4]).toBeNull();
    expect(eventText(a.events[5])).toBe("Этап 1 · прирезка · висячий бок · пробы 1–3 · M 3 · C 1,267 — C ≥ Сб");
  });

  it("LABEL as the core writes it (Т8.2-12)", () => {
    expect(oreLabel(8, 2.8375, 22.7, "г/т")).toBe("8,0 м @ 2,84 г/т (МС 22,7)");
    expect(oreLabel(5, 1.16, 5.8, null)).toBe("5,0 м @ 1,16 (МС 5,8)");
  });

  it("the summary by class: number, Σ M, средняя M, C = Σ MC / Σ M, Σ MC", () => {
    const lines = summaryLines(toOreAnswer(RAW).summary);
    expect(lines[0]).toMatchObject({ title: "кондиционный рудный", count: "2", m: "6", mMean: "3", c: "1,333", mc: "8" });
    expect(lines[2].title).toBe("включённый прослой (внутри интервалов)");
    expect(summaryLines(null)).toEqual([]);
  });

  it("the export: a file is required, at most 50 sheets of holes, the names once", async () => {
    expect(protocolExportBody("xlsx", "", [], false)).toMatch(/Сохранить как/);
    expect(protocolExportBody("xlsx", "C:/П/п.xlsx", Array.from({ length: 51 }, (_, i) => `С-${i}`), false)).toMatch(/не больше 50/);
    expect(protocolExportBody("csv", "C:/П/п.csv", [" С-1", "С-1", ""], true)).toEqual({ format: "csv", path: "C:/П/п.csv", holes: ["С-1"], overwrite: true });
    expect(protocolFileName("Рудные/интервалы", "xlsx")).toBe("Рудные_интервалы · протокол.xlsx");
    const s = await startProtocolExport(9, { format: "xlsx", path: "C:/п.xlsx" });
    expect(s).toEqual({ source: "core", id: 77 });
    expect(sent.at(-1)).toMatchObject({ path: "/composites/results/9/protocol", method: "POST", body: { format: "xlsx", path: "C:/п.xlsx" } });
  });
});

describe("the window", () => {
  it("is routed and shows Сб, Мрт, Мпп, МС, Смин, the mode, the thickness, the gaps and the three tabs", () => {
    const html = renderToStaticMarkup(createElement(OreWindow, { winId: "w", args: { set_id: 3, cutoff: 1, m_min: 3 } }));
    for (const k of ["cutoff", "m_min", "m_waste_max", "mc_min", "c_min", "mode", "thickness_field", "missing"]) expect(html).toContain(`composites-param-${k}`);
    expect(html).not.toContain("composites-param-contacts_values");
    expect(html).toContain("авто: 3");
    for (const t of ["column", "protocol", "summary"]) expect(html).toContain(`ore-tab-${t}`);
    const routed = renderToStaticMarkup(renderCompositesWindow({ id: "w", kind: "composites-ore", title: "", props: {} } as never) as never);
    expect(routed).toContain('data-testid="composites-ore"');
  });

  it("the columns: classes of the samples, прирезки, the final rows with labels; frame only when asked", () => {
    const a = toOreAnswer(RAW);
    const html = renderToStaticMarkup(createElement(OreColumns, { answer: a, field: "AU", cutoff: 1, showFrame: false }));
    expect(html).toContain('data-testid="ore-classes"');
    expect((html.match(/data-value="ore"/g) ?? []).length).toBe(5 + 2); // 5 samples + 2 final rows
    expect(html).toContain('data-value="waste_in"');
    expect(html).toContain("4,0 м @ 1,25 г/т (МС 5,0)");
    expect(html).not.toContain('data-value="frame"');
    expect(html).toContain("прирезка (C ≥ Сб)");
    expect(renderToStaticMarkup(createElement(OreColumns, { answer: a, field: "AU", cutoff: 1, showFrame: true }))).toContain('data-value="frame"');
  });

  it("the protocol and the summary render", () => {
    const a = toOreAnswer(RAW);
    const p = renderToStaticMarkup(createElement(OreProtocol, { answer: a, field: "AU" }));
    expect(p).toContain('data-testid="ore-protocol-grid"');
    expect(p).toContain('rowspan="4"');
    expect(p).toContain("C₁");
    const s = renderToStaticMarkup(createElement(OreSummaryView, { answer: a }));
    expect(s).toContain("кондиционный рудный");
    expect(s).toContain("Сводка по набору — после расчёта");
  });

  it("after the job: the table in the bottom panel; the display is looked for when the job does not name it", async () => {
    const app = createApp({ persist: false });
    await showOreResult(app, { set_id: 3, table_id: 12, name: "Рудные интервалы" });
    expect(tablesStore.get().setId).toBe(3);
    expect(tablesStore.get().table).toBe("12");
    expect(app.store.get().workspace.bottom).toBe(true);
    expect(app.store.get().workspace.bottomTab).toBe("table");
    expect(DISPLAY_NAME).toBe("Рудные интервалы");
  });
});
