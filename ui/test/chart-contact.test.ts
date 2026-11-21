// The window «Контактный анализ» (ТЗ 8.3; ADR 0019 §4): the steps of the core from −D to D (side A below 0), an empty
// step stays empty (never 0) and breaks the line, Т8.3-2 as the chart shows it (A = 1, B = 3), what blocks the
// request, the CSV, the command and the assistant's text. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContactChart } from "../src/charts/contact/ContactChart";
import { ContactWindow } from "../src/charts/contact/ContactWindow";
import { contactCsv, contactNotes, contactProblem, contactRequest, contactSteps, contactSummary, stepRuns } from "../src/charts/contact/model";
import type { ContactResult } from "../src/modules/charts/apiMore";
import { bindCharts } from "../src/modules/charts/bind";
import { chartName, defaultSettings, type ContactSettings } from "../src/modules/charts/settings";
import { tablesStore } from "../src/modules/tables/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const COUNTS = { rows: 40, missing: 0, bad_weight: 0, negative: 0, zero_excluded: 0, bottom_capped: 0, bottom_excluded: 0, capped: 0, top_excluded: 0, used: 40, no_domain: 2 };

// Т8.3-2: in A everywhere 1,0, in B — 3,0; D = 3, b = 1; the step 2…3 of B has no samples
const RES: ContactResult = {
  set_id: 3,
  field: "AU",
  domain: "ROCK",
  wireframe_id: null,
  distance: 3,
  step: 1,
  weighted: true,
  counts: COUNTS,
  contacts: 4,
  holes: 2,
  edges: [-3, -2, -1, 0, 1, 2, 3],
  mean: [1, 1, 1, 3, 3, null],
  n: [4, 4, 4, 4, 4, 0],
  a: { label: "OX", n: 12, mean: 1 },
  b: { label: "SU", n: 8, mean: 3 },
};

const base = (): ContactSettings => ({ ...defaultSettings("contact", 3), field: "AU", domain: "ROCK", a: "OX", b: "SU" });

describe("the contact analysis", () => {
  it("D = 20 м and b = 1 м by default; what blocks the request", () => {
    expect(defaultSettings("contact", 3)).toMatchObject({ distance: 20, step: 1, wireframe_id: null, weight: "length" });
    expect(contactProblem(defaultSettings("contact", 3))).toBe("Выберите поле.");
    expect(contactProblem({ ...base(), a: null })).toBe("Выберите домены A и B.");
    expect(contactProblem({ ...base(), b: "OX" })).toBe("Домены A и B должны различаться.");
    expect(contactProblem({ ...base(), step: 0 })).toBe("Расстояние и шаг — больше 0.");
    expect(contactProblem({ ...base(), distance: 1000, step: 1 })).toMatch(/^Не больше 500 шагов/);
    // by a wireframe the domains are not needed
    expect(contactProblem({ ...base(), domain: null, a: null, b: null, wireframe_id: 7 })).toBeNull();
    const r = contactRequest(base())!;
    expect(r).toMatchObject({ field: "AU", domain: "ROCK", a: "OX", b: "SU", distance: 20, step: 1, wireframe_id: null });
    expect("view" in r).toBe(false);
    expect(chartName("contact", base())).toBe("Контакт · AU · OX | SU");
  });

  it("Т8.3-2 as shown: the steps of A are 1, of B — 3; an empty step is empty, not 0", () => {
    const steps = contactSteps(RES);
    expect(steps.filter((s) => s.side === "a").map((s) => s.mean)).toEqual([1, 1, 1]);
    expect(steps.filter((s) => s.side === "b").map((s) => s.mean)).toEqual([3, 3, null]);
    expect(steps[5]).toMatchObject({ from: 2, to: 3, n: 0, mean: null });
    expect(stepRuns(steps).map((r) => r.length)).toEqual([5]);
    expect(stepRuns(contactSteps({ ...RES, mean: [1, null, 1, 3, 3, 3] })).map((r) => r.length)).toEqual([1, 4]);
  });

  it("the chart: the contact at 0, the means, the counts; the CSV keeps the empty step", () => {
    const html = renderToStaticMarkup(createElement(ContactChart, { result: RES, counts: true, xLabel: "AU, г/т" }));
    expect(html).toContain('data-contact="0"');
    expect(html).toContain('data-means="5"');
    expect(html).toContain('data-counts="6"');
    expect(html).toContain(">OX<");
    expect(html).not.toContain("NaN");
    const csv = contactCsv(RES).split("\r\n");
    expect(csv[0]).toBe('"От, м";"До, м";"Сторона";"Среднее";"Проб"');
    expect(csv[1]).toBe('-3;-2;"OX";1;4');
    expect(csv[6]).toBe('2;3;"SU";;0');
    expect(contactNotes(RES)).toEqual(["контактов 4 в 2 скважинах", "шаг 1 м, до 3 м", "без кода домена 2"]);
    expect(contactSummary("К", RES)).toBe("К: контактов 4 (скважин 2); OX: проб 12, среднее 1; SU: проб 8, среднее 3");
  });
});

describe("the window and the command", () => {
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  let app: App;

  beforeEach(() => {
    sent.length = 0;
    windowStore.set({ windows: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        sent.push({ path, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {} });
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 40, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }], texts: [{ name: "ROCK", role: "value", values: 2, labels: ["OX", "SU"], more: false }] }] }));
        if (path === "/stats/contact") return Promise.resolve(json(RES));
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

  it("the form; chart.contact sends the domains, D and b; the assistant gets the contacts and the sides", async () => {
    const html = renderToStaticMarkup(createElement(ContactWindow, { winId: "w", setId: 3, setName: "Набор", settings: base() }));
    for (const label of ["Поле", "Домен", "Контакт", "Домен A", "Домен B", "Расстояние D", "Шаг b", "Вес"]) expect(html, label).toContain(`aria-label="${label}"`);
    const a = await app.registry.execute("chart.contact", { domain: "ROCK", a: "OX", b: "SU", distance: 10, step: 0.5 }, "claude");
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "chart-contact", title: "Контакт · AU · OX | SU · Набор" });
    expect(sent.find((x) => x.path === "/stats/contact")?.body).toMatchObject({ field: "AU", domain: "ROCK", a: "OX", b: "SU", distance: 10, step: 0.5, table: "пробы" });
    expect(a.result).toMatch(/: контактов 4 \(скважин 2\); OX: проб 12, среднее 1; SU: проб 8, среднее 3$/);
    // without the domains the window opens; the assistant is told what is missing
    const b = await app.registry.execute("chart.contact", {}, "claude");
    expect(b.ok).toBe(false);
    expect(b.result).toMatch(/Выберите поле домена/);
  });
});
