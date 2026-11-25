// The profile of the blocks beside the contact analysis (Д-28, ТД28-3; Э18, 18.Д2) on the REAL answers of the core
// (`core/tests/data/stats/contact_blocks_answers.json`, made by `core/tests/test_stats_contact_blocks.py` through
// `POST /stats/contact` with `blocks`: a cube with a hole through it («до каркаса») and the domain field of a model):
// the second line of the chart in the steps of the samples, the CSV, the notes, the request the core accepted, what the window
// offers, «Задать мягкую границу…» → `estimate.open` with the pair and d_м, the link to the saved chart in the wizard.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContactChart } from "../src/charts/contact/ContactChart";
import { ContactWindow } from "../src/charts/contact/ContactWindow";
import { blockDefaults, blockSteps, blocksNote, contactCsv, contactNotes, contactProblem, contactRequest, contactSteps, contactSummary, openSoft, softArgs, softProblem } from "../src/charts/contact/model";
import { blocksStore } from "../src/modules/blocks/state";
import type { ChartRow } from "../src/modules/charts/api";
import { computeContact, type ContactResult } from "../src/modules/charts/apiMore";
import { chartsStore } from "../src/modules/charts/state";
import { defaultSettings, normalizeSettings, settingsFromArgs, type ContactSettings } from "../src/modules/charts/settings";
import { contactChartsOf } from "../src/modules/estimation/contactLink";
import { StepSamples } from "../src/modules/estimation/StepSamples";
import { EMPTY_FORM, type EstimationForm } from "../src/modules/estimation/form";
import { putForm, wizardStore } from "../src/modules/estimation/wizardState";
import { setCoreTarget } from "../src/shell/api";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { ID, installCore, json, MODEL, newApp, teardown } from "./estimation/harness";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const A: Record<string, { request: any; answer: ContactResult }> = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/stats/contact_blocks_answers.json"), "utf-8"));
const WIRE = A.wireframe;
const DOM = A.domain;
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const settingsOf = (r: { request: Record<string, unknown> }): ContactSettings => normalizeSettings("contact", r.request, Number(r.request.set_id));

describe("the answer of the core: the profile of the blocks in the steps of the samples", () => {
  it("the same edges; a step without blocks is empty, never 0 (the cube: 8 steps of 10)", () => {
    for (const r of [WIRE.answer, DOM.answer]) {
      expect(r.blocks!.edges).toEqual(r.edges);
      expect(blockSteps(r)).toHaveLength(r.edges.length - 1);
    }
    expect(blockSteps(WIRE.answer).map((s) => s.mean)).toEqual([null, null, 3, 3, 2, 2, 1, 1, 1, 1]);
    // the layers beside the faces are softened (2): the line of the blocks leaves the samples' 1 and 3 there
    expect(contactSteps(WIRE.answer).map((s) => s.mean)).toEqual([1, 1, 1, 1, 1, 3, 3, 3, 3, 3]);
    expect(blockSteps(DOM.answer).map((s) => s.mean)).toEqual([1, 1, 1, 1, 1, 3, 3, 3, 3, 3]);
    expect(blockSteps({ ...DOM.answer, blocks: { ...DOM.answer.blocks!, edges: [0, 1] } })).toEqual([]);
    expect(blockSteps({ ...DOM.answer, blocks: undefined })).toEqual([]);
  });

  it("the chart draws the second line, the tooltip data and d_м marks only inside the steps", () => {
    const x = (r: ContactResult, soft: number | null) => renderToStaticMarkup(createElement(ContactChart, { result: r, counts: true, xLabel: "PB", soft }));
    let html = x(WIRE.answer, 2);
    expect(html).toContain('data-blocks="8"');
    expect(html).toContain("блоки «EST»");
    expect(html).toContain('data-soft="-2"');
    expect(html).toContain('data-soft="2"');
    expect(html).not.toContain("NaN");
    html = x(DOM.answer, null);
    expect(html).toContain('data-blocks="10"');
    expect(html).not.toContain("data-soft");
    expect(x(DOM.answer, 50)).not.toContain("data-soft"); // beyond the steps
    // without the blocks the chart is that of Э13
    expect(x({ ...DOM.answer, blocks: undefined }, null)).not.toContain("data-blocks");
  });

  it("the CSV keeps the empty steps and adds the blocks, their number and volume; the notes and the text for the assistant", () => {
    const csv = contactCsv(WIRE.answer).split("\r\n");
    expect(csv[0]).toBe('"От, м";"До, м";"Сторона";"Среднее";"Проб";"Блоки, среднее";"Блоков";"Объём блоков, м³"');
    expect(csv[1]).toBe('-5;-4;"снаружи";1;2;;0;0');
    expect(csv[3]).toBe('-3;-2;"снаружи";1;2;3;150;150');
    expect(contactCsv({ ...WIRE.answer, blocks: undefined }).split("\r\n")[0]).toBe('"От, м";"До, м";"Сторона";"Среднее";"Проб"');
    expect(flat(blocksNote(WIRE.answer)!)).toBe("блоки «EST»: 2 742 в шагах, не оценено 2");
    expect(contactNotes(WIRE.answer).at(-1)).toContain("блоки «EST»");
    expect(contactNotes({ ...WIRE.answer, blocks: undefined }).join(" ")).not.toContain("блоки");
    const text = flat(contactSummary("К", DOM.answer));
    expect(text).toContain("блоки «EST»: гранит: блоков 480, среднее 1; диорит: блоков 480, среднее 3");
  });
});

describe("the request: what the core accepted survives the settings of the window", () => {
  it("blocks stay in the settings and in the request; without the field there is no request", () => {
    for (const r of [WIRE, DOM]) {
      const s = settingsOf(r);
      expect(s.blocks).toEqual(r.request.blocks);
      expect(contactRequest(s)).toMatchObject({ distance: r.request.distance, step: r.request.step, blocks: r.request.blocks });
    }
    const s = settingsOf(DOM);
    expect(contactProblem({ ...s, blocks: { model_id: 3, field: "" } })).toBe("Выберите поле оценки блочной модели.");
    expect(contactRequest({ ...s, blocks: null })).toMatchObject({ blocks: null });
    // a bad value is dropped when the settings are read
    expect(normalizeSettings("contact", { ...DOM.request, blocks: { model_id: "3", field: "EST" } }, 3).blocks).toBeNull();
    expect(normalizeSettings("contact", { ...DOM.request, blocks: { model_id: 3, field: "EST", status_field: " " } }, 3).blocks).toEqual({ model_id: 3, field: "EST" });
    expect(defaultSettings("contact", 3).blocks).toBeNull();
  });

  it("the flat arguments of chart.contact (the assistant) become the blocks", () => {
    const s = settingsFromArgs("contact", { a: "гранит", b: "диорит", blocks_model_id: 21, blocks_field: "EST", blocks_status_field: "STATUS", blocks_domain_field: "ROCK" }, 3);
    expect(s.blocks).toEqual({ model_id: 21, field: "EST", status_field: "STATUS", domain_field: "ROCK" });
    expect(settingsFromArgs("contact", { blocks_model_id: 21 }, 3).blocks).toBeNull(); // no field
    expect(settingsFromArgs("contact", {}, 3).blocks).toBeNull();
  });

  it("computeContact sends the blocks and gives the whole answer", async () => {
    const sent: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json(DOM.answer);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    try {
      const body = contactRequest(settingsOf(DOM))!;
      const got = await computeContact(body);
      expect(sent[0].blocks).toEqual(DOM.request.blocks);
      expect(got.blocks).toEqual(DOM.answer.blocks);
    } finally {
      vi.unstubAllGlobals();
      setCoreTarget(null);
    }
  });

  it("the fields preselected for a model: the estimate, its status, the domain of the samples (or the one with A and B)", () => {
    const fields = [
      { name: "STATUS", kind: "number" },
      { name: "EST", kind: "number" },
      { name: "KV", kind: "number" },
      { name: "ROCK", kind: "category", labels: ["гранит", "диорит"] },
      { name: "ZONE", kind: "category", labels: ["окисл", "перех"] },
    ];
    expect(blockDefaults(fields, settingsOf(DOM))).toEqual({ field: "EST", status_field: "STATUS", domain_field: "ROCK" });
    expect(blockDefaults(fields, { domain: null, a: "окисл", b: "перех" })).toEqual({ field: "EST", status_field: "STATUS", domain_field: "ZONE" });
    expect(blockDefaults(fields, { domain: null, a: null, b: null })).toEqual({ field: "EST", status_field: "STATUS" });
    expect(blockDefaults([{ name: "ROCK", kind: "category" }], settingsOf(DOM))).toBeNull();
  });
});

describe("«Задать мягкую границу…»", () => {
  let app: App;
  beforeEach(() => {
    installCore();
    app = newApp();
  });
  afterEach(teardown);

  const pair = (): ContactSettings => ({ ...settingsOf(DOM), a: "окисл", b: "перех", blocks: { model_id: ID.model, field: "AU" } });

  it("d_м is the person's: no distance — no command; the wireframe has no pair", () => {
    const s = pair();
    expect(softProblem(s, null)).toMatch(/решение за вами/);
    expect(softProblem(s, 0)).toMatch(/больше 0/);
    expect(softProblem(s, 5)).toBeNull();
    expect(softProblem(s, 1e9)).toMatch(/не больше/);
    expect(softProblem({ ...s, a: null }, 5)).toMatch(/два разных домена/);
    expect(softProblem({ ...s, b: s.a }, 5)).toMatch(/два разных домена/);
    expect(softProblem({ ...s, wireframe_id: 4 }, 5)).toMatch(/для пары доменов/);
    expect(softArgs(s, 5)).toEqual({ soft_a: "окисл", soft_b: "перех", soft_distance: 5, model_id: ID.model });
    expect(softArgs({ ...s, blocks: null }, 5)).toEqual({ soft_a: "окисл", soft_b: "перех", soft_distance: 5 });
  });

  it("estimate.open gets soft_a, soft_b, soft_distance: the wizard has A as the domain of the samples, B as the neighbour, d_м", async () => {
    const before = windowStore.get().windows.length;
    expect(await openSoft(app, pair(), 5)).toBeNull();
    const f = wizardStore.get().form;
    expect(f.modelId).toBe(ID.model);
    expect(f.domain?.values).toEqual(["окисл"]);
    expect(f.soft).toEqual({ on: true, domains: ["перех"], dist: "5" });
    expect(windowStore.get().windows.length).toBe(before + 1);
    // the chart decides nothing: without the distance the wizard is not touched
    putForm({ ...EMPTY_FORM });
    expect(await openSoft(app, pair(), null)).toMatch(/решение за вами/);
    expect(wizardStore.get().form.soft.on).toBe(false);
  });

  it("the window: the model of the blocks, its fields, d_м and the button", () => {
    blocksStore.set({ list: [MODEL], loaded: true, error: null, details: { [ID.model]: MODEL }, detailErrors: {} });
    const win = (s: ContactSettings) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ContactWindow, { winId: "w", setId: 3, setName: "Набор", settings: s })));
    let html = win({ ...pair(), blocks: null });
    for (const label of ["Блочная модель", "Расстояние d_м"]) expect(html, label).toContain(`aria-label="${label}"`);
    expect(html).not.toContain('aria-label="Поле оценки блоков"');
    expect(html).toContain("Задать мягкую границу…");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Задать мягкую границу…/); // d_м is not given
    html = win(pair());
    for (const label of ["Поле оценки блоков", "Поле статуса блоков", "Поле домена блоков"]) expect(html, label).toContain(`aria-label="${label}"`);
    expect(html).toContain("<option value=\"AU\" selected=\"\">AU</option>");
    // by a wireframe the domain field of the model is not needed
    expect(win({ ...pair(), wireframe_id: 4 })).not.toContain('aria-label="Поле домена блоков"');
  });
});

describe("the wizard, step 2: the saved chart of the contact of this pair", () => {
  let app: App;
  beforeEach(() => {
    installCore();
    app = newApp();
  });
  afterEach(teardown);

  const row = (id: number, settings: Record<string, unknown>, over: Partial<ChartRow> = {}): ChartRow => ({ id, set_id: 3, kind: "contact", name: `Контакт ${id}`, auto_name: true, settings, version: 1, ...over });
  const chart = { ...DOM.request, set_id: 3, domain: "ZONE", a: "окисл", b: "перех" };
  const form = (patch: Partial<EstimationForm> = {}): EstimationForm => ({
    ...EMPTY_FORM,
    modelId: ID.model,
    setId: 3,
    table: "12",
    field: "AU",
    domain: { table: "14", field: "ZONE", values: ["окисл"] },
    soft: { on: true, domains: ["перех"], dist: "5" },
    ...patch,
  });

  it("the pair, either way round, of the same set and field of the domain, along the holes", () => {
    const rows = [row(1, chart), row(2, { ...chart, a: "перех", b: "окисл" }), row(3, { ...chart, b: "перв" }), row(4, { ...chart, domain: "ROCK" }), row(5, { ...chart, wireframe_id: 9 }), row(6, chart, { set_id: 8 }), row(7, chart, { kind: "histogram" })];
    expect(contactChartsOf(rows, form()).map((c) => c.id)).toEqual([1, 2]);
    expect(contactChartsOf(rows, form({ soft: { on: false, domains: ["перех"], dist: "0" } }))).toEqual([]);
    expect(contactChartsOf(rows, form({ soft: { on: true, domains: [], dist: "0" } }))).toEqual([]);
    expect(contactChartsOf(rows, form({ domain: null }))).toEqual([]);
    expect(contactChartsOf(rows, form({ setId: null }))).toEqual([]);
  });

  it("step 2 shows the link and opens nothing by itself", () => {
    const render = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(StepSamples)));
    putForm(form(), { step: 2 });
    chartsStore.set({ charts: [], loaded: true });
    expect(render()).not.toContain("est-soft-contact");
    chartsStore.set({ charts: [row(1, chart)], loaded: true });
    const html = render();
    expect(html).toContain('data-testid="est-soft-contact"');
    expect(html).toContain("Контакт 1");
    expect(html).toContain("расстояние d_м выбираете вы");
    expect(windowStore.get().windows).toHaveLength(0);
    expect(wizardStore.get().form.soft.dist).toBe("5"); // nothing is written to the form
    chartsStore.set({ charts: [], loaded: false });
  });
});
