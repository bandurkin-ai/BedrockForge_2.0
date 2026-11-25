// Steps 4–6 of the wizard «Оценка содержаний» and the window «Параметры оценки» (Э18, 18.20) on the real answers of the core
// (`core/tests/data/estimate/contract_answers.json`): the search and the passes (defaults = the answer of `defaults`), the rule of the
// gold-grade samples (Д-29), the output fields, the summary, the preview of one block (`POST /estimate/preview`), the run from the
// wizard (save, then `estimate.run`), the window of the stored row.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { blocksStore } from "../../src/modules/blocks/state";
import { readDefaults } from "../../src/modules/estimation/api";
import { bodyOf, EMPTY_FORM, formOfMeta, type EstimationForm } from "../../src/modules/estimation/form";
import { PASSES_MAX, PREFIX_MAX, RANGES } from "../../src/modules/estimation/formBase";
import { ALLOWED_FIELDS, DEFAULT_FIELDS, defaultPasses, defaultPrefix, effectiveFields, EMPTY_OUTPUTS, EMPTY_SEARCH, existingFields, fieldNames, outputsBody, searchBody, type PassForm, type SearchForm } from "../../src/modules/estimation/formSearch";
import { openParams } from "../../src/modules/estimation/bind";
import { PARAMS_WINDOW, ParamsWindow } from "../../src/modules/estimation/ParamsWindow";
import { readPreview } from "../../src/modules/estimation/previewApi";
import { StepOutputs } from "../../src/modules/estimation/StepOutputs";
import { StepRun, centreOf } from "../../src/modules/estimation/StepRun";
import { StepSearch } from "../../src/modules/estimation/StepSearch";
import { Wizard } from "../../src/modules/estimation/Wizard";
import { outputsOf, passLine, summaryRows, wizardWarnings, type SummaryCtx } from "../../src/modules/estimation/summary";
import { loadRows } from "../../src/modules/estimation/state";
import { addPass, putForm, removePass, requestPreview, resetPasses, runWizard, saveWizard, setForm, setHy, setPass, setPreviewAt, startEdit, toggleField, wizardStore } from "../../src/modules/estimation/wizardState";
import { A, FIELD, ID, installCore, MODEL, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const D = A.defaults.body;
const META = A.estimation.body.meta;
const defaults = readDefaults(D);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const render = (el: ReturnType<typeof createElement>) => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el)));
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const form = (patch: Partial<EstimationForm> = {}): EstimationForm => ({ ...EMPTY_FORM, modelId: ID.model, domainField: "ZONE", label: "окисл", setId: ID.set, table: ID.table, field: FIELD, domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] }, variogramId: ID.vario, ...patch });
const withDefaults = () => wizardStore.set({ defaults: { phase: "ok", key: "k", seq: 1, data: defaults, error: null } });
const pass = (patch: Partial<PassForm> = {}): PassForm => ({ ...defaultPasses(D.params)[0], ...patch });

async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await new Promise((r) => setTimeout(r, 5));
  }
}

describe("the form mirrors the core (the answer of `defaults`)", () => {
  it("the ranges of the passes and of the ellipsoid, the limits", () => {
    for (const k of ["factor", "n_min", "n_max", "n_hole", "n_oct", "k_oct", "n_holes_min", "r1", "r2", "r3", "ang1", "ang2", "ang3"] as const) {
      const r = defaults.ranges[k];
      expect([k, RANGES[k].lo, RANGES[k].hi, !!RANGES[k].integer]).toEqual([k, r.lo, r.hi, r.integer]);
    }
    expect(PASSES_MAX).toBe(D.params.limits.passes);
    expect(PREFIX_MAX).toBe(D.params.limits.prefix);
  });
  it("the fields of each method: the allowed ones and the default ones", () => {
    expect(ALLOWED_FIELDS).toEqual(D.params.outputs.allowed);
    expect(DEFAULT_FIELDS).toEqual(D.params.outputs.fields);
  });
  it("the passes by default are those of the core", () => {
    const got = defaultPasses(D.params);
    expect(got).toHaveLength(D.params.search.passes.length);
    got.forEach((p, i) => {
      const c = D.params.search.passes[i];
      expect([p.factor, p.nMin, p.nMax, p.nHole, p.nOct, p.kOct, p.nHolesMin, p.hy.on]).toEqual([String(c.factor), String(c.n_min), String(c.n_max), String(c.n_hole), String(c.n_oct), String(c.k_oct), String(c.n_holes_min), c.hy !== null]);
    });
    expect(defaultPasses(null)).toHaveLength(3); // no answer yet: the three of 8.8.2
  });
  it("the prefix and the names of the fields are the core's (the row of the answer)", () => {
    expect(defaultPrefix(FIELD, "ok")).toBe(META.outputs.prefix);
    expect(defaultPrefix("Au g/t", "idw")).toBe("AU_G_T_IDW_");
    const f = formOfMeta(META);
    const { names } = outputsOf({ ...f, outputs: EMPTY_OUTPUTS }, defaults);
    expect(names).toEqual(A.estimation.body.columns.map((c: { name: string }) => c.name));
    expect(existingFields(["ZONE", ...names], names)).toEqual(A.estimation.body.fields_exist);
  });
  it("P of IK is P1…PK; NHY joins the default fields with the rule of Д-29", () => {
    expect(fieldNames("A_", ["STATUS", "P", "ETYPE"], 3)).toEqual(["A_STATUS", "A_P1", "A_P2", "A_P3", "A_ETYPE"]);
    expect(effectiveFields("ok", EMPTY_OUTPUTS, true)).toContain("NHY");
    expect(effectiveFields("ok", EMPTY_OUTPUTS, false)).not.toContain("NHY");
    expect(effectiveFields("nn", { prefix: "", fields: ["KV", "EST"] }, false)).toEqual(["STATUS", "EST"]); // KV is not for NN
  });
});

describe("the search → the body", () => {
  const model = (patch: Partial<SearchForm> = {}) => searchBody({ ...EMPTY_SEARCH, ...patch }, true);
  it("nothing typed — nothing sent: the core's defaults", () => {
    expect(model()).toEqual({});
    const got = bodyOf(form());
    expect(got.ok && "search" in got.body).toBe(false);
    expect(got.ok && "outputs" in got.body).toBe(false);
  });
  it("by hand: r1 is needed, r2 and r3 are optional, the angles are checked", () => {
    expect(searchBody({ ...EMPTY_SEARCH, from: "manual" }, false)).toMatchObject({ param: "search.r1", code: "bad_param" });
    expect(searchBody({ ...EMPTY_SEARCH, from: "manual", r: ["100", "60", ""], ang: ["45", "", ""] }, false)).toEqual({ search: { from: "manual", ang1: 45, r1: 100, r2: 60 } });
    expect(searchBody({ ...EMPTY_SEARCH, from: "manual", r: ["100", "", ""], ang: ["", "95", ""] }, false)).toMatchObject({ param: "search.ang2" });
    expect(searchBody({ ...EMPTY_SEARCH, from: "manual", r: ["0", "", ""] }, false)).toMatchObject({ param: "search.r1" });
  });
  it("by the model: no numbers go; without a model of variogram — told with the words of the core", () => {
    expect(model({ from: "model", ang: ["10", "", ""], r: ["50", "", ""] })).toEqual({ search: { from: "model" } });
    expect(searchBody({ ...EMPTY_SEARCH, from: "model" }, false)).toMatchObject({ param: "search.from", message: "Эллипсоид по модели вариограммы: модель не выбрана." });
  });
  it("by a trend: the trend is needed; r1 is sent only when typed", () => {
    expect(searchBody({ ...EMPTY_SEARCH, from: "trend" }, false)).toMatchObject({ param: "trend_id" });
    expect(searchBody({ ...EMPTY_SEARCH, from: "trend", trendId: 3 }, false)).toEqual({ search: { from: "trend" }, trend_id: 3 });
    expect(searchBody({ ...EMPTY_SEARCH, from: "trend", trendId: 3, r: ["80", "9", "9"] }, false)).toEqual({ search: { from: "trend", r1: 80 }, trend_id: 3 });
  });
  it("a pass: all its numbers, the rule of Д-29 off is null; the ranges and n_min ≤ n_max with the path of the pass", () => {
    const ok = model({ passes: [pass(), pass({ factor: "2", nMax: "16", hy: { on: true, t: "20", q: "0,5", mode: "cap" } })] });
    expect(ok).toEqual({
      search: {
        passes: [
          { factor: 1, n_min: 4, n_max: 24, n_hole: 0, n_oct: 0, k_oct: 0, n_holes_min: 0, hy: null },
          { factor: 2, n_min: 4, n_max: 16, n_hole: 0, n_oct: 0, k_oct: 0, n_holes_min: 0, hy: { t: 20, q: 0.5, mode: "cap" } },
        ],
      },
    });
    expect(model({ passes: [pass(), pass({ nMax: "200" })] })).toMatchObject({ param: "search.passes.1.n_max", message: expect.stringContaining("n_max") });
    expect(model({ passes: [pass({ nMin: "30" })] })).toMatchObject({ param: "search.passes.0.n_min", message: expect.stringContaining("n_min больше n_max") });
    expect(model({ passes: [pass({ factor: "0" })] })).toMatchObject({ param: "search.passes.0.factor" });
    expect(model({ passes: [pass({ hy: { on: true, t: "", q: "0,5", mode: "drop" } })] })).toMatchObject({ param: "search.passes.0.hy.t" });
    expect(model({ passes: [pass({ hy: { on: true, t: "20", q: "1,5", mode: "drop" } })] })).toMatchObject({ param: "search.passes.0.hy.q" });
    expect(model({ passes: [pass({ hy: { on: true, t: "20", q: "0", mode: "drop" } })] })).toMatchObject({ param: "search.passes.0.hy.q" });
  });
  it("up to five passes; none is not allowed", () => {
    expect(model({ passes: Array.from({ length: 5 }, () => pass()) })).toHaveProperty("search");
    expect(model({ passes: Array.from({ length: 6 }, () => pass()) })).toMatchObject({ param: "search.passes" });
    expect(model({ passes: [] })).toMatchObject({ param: "search.passes" });
  });
  it("the stored row goes back as it was: the passes of the answer of the core", () => {
    const got = bodyOf(formOfMeta(META));
    expect(got.ok && (got.body.search as { passes: unknown }).passes).toEqual(META.search.passes);
    expect(got.ok && got.body.outputs).toEqual(META.outputs);
  });
});

describe("the output fields → the body", () => {
  it("the prefix is checked (letters, digits, «_», ≤ 40); the keys of the method only, STATUS first", () => {
    expect(outputsBody({ prefix: "AU OK", fields: null }, "ok")).toMatchObject({ param: "outputs.prefix", message: "Префикс полей: допустимы буквы, цифры и «_»." });
    expect(outputsBody({ prefix: "я".repeat(41), fields: null }, "ok")).toMatchObject({ param: "outputs.prefix" });
    expect(outputsBody({ prefix: "Золото_1", fields: ["EST", "KV", "NHY"] }, "ok")).toEqual({ outputs: { prefix: "Золото_1", fields: ["STATUS", "EST", "KV", "NHY"] } });
    expect(outputsBody({ prefix: "", fields: ["KV", "EST"] }, "nn")).toEqual({ outputs: { fields: ["STATUS", "EST"] } });
    expect(outputsBody(EMPTY_OUTPUTS, "ok")).toEqual({});
  });
});

describe("the edits of the wizard", () => {
  it("the first touch of a pass takes the core's passes; «Как в ядре» returns them; the last pass stays; five at most", () => {
    putForm(form());
    withDefaults();
    setPass(1, { nMax: "12" });
    const ps = wizardStore.get().form.search.passes!;
    expect(ps).toHaveLength(D.params.search.passes.length);
    expect(ps[1].nMax).toBe("12");
    expect(ps[0].nMax).toBe(String(D.params.search.passes[0].n_max));
    resetPasses();
    expect(wizardStore.get().form.search.passes).toBeNull();
    for (let i = 0; i < 6; i++) addPass();
    expect(wizardStore.get().form.search.passes).toHaveLength(5);
    for (let i = 0; i < 9; i++) removePass(0);
    expect(wizardStore.get().form.search.passes).toHaveLength(1);
  });
  it("a new pass: the last one with the next multiplier, without the rule of Д-29", () => {
    putForm(form());
    withDefaults();
    setHy(2, { on: true, t: "20" });
    addPass();
    const last = wizardStore.get().form.search.passes!.at(-1)!;
    expect(last.factor).toBe("4");
    expect(last.hy.on).toBe(false);
  });
  it("the rule of Д-29 on brings NHY into the typed fields; the default fields get it by themselves", () => {
    putForm(form());
    withDefaults();
    expect(outputsOf(wizardStore.get().form, defaults).keys).not.toContain("NHY");
    setHy(0, { on: true, t: "20" });
    expect(outputsOf(wizardStore.get().form, defaults).keys).toContain("NHY");
    toggleField("KV", false, outputsOf(wizardStore.get().form, defaults).keys);
    setHy(1, { on: true, t: "30" });
    const fields = wizardStore.get().form.outputs.fields!;
    expect(fields).toContain("NHY");
    expect(fields).not.toContain("KV");
    expect(fields[0]).toBe("STATUS");
  });
  it("STATUS cannot be switched off; another method forgets the typed keys", () => {
    putForm(form());
    toggleField("EST", false, ["STATUS", "EST", "KV"]);
    expect(wizardStore.get().form.outputs.fields).toEqual(["STATUS", "KV"]);
    toggleField("STATUS", false, ["STATUS", "KV"]);
    expect(wizardStore.get().form.outputs.fields).toEqual(["STATUS", "KV"]);
    setForm({ method: "nn" });
    expect(wizardStore.get().form.outputs.fields).toBeNull();
  });
  it("a refusal of the core about a pass takes the person to step 4 and stands under the pass", async () => {
    putForm(form({ search: { ...EMPTY_SEARCH, passes: defaultPasses(D.params) } }));
    core.force("POST /geostat/estimations", { status: A.bad_param.status, body: A.bad_param.body });
    await expect(saveWizard()).rejects.toBeTruthy();
    expect(wizardStore.get().step).toBe(4);
    withDefaults();
    expect(render(createElement(StepSearch))).toContain('data-testid="est-issue-pass-1"');
  });
});

describe("step 4 drawn", () => {
  it("the passes of the core, the three sources of the ellipsoid, the rule of Д-29 off", () => {
    putForm(form());
    withDefaults();
    const html = render(createElement(StepSearch));
    for (const id of ["est-search-from-model", "est-search-from-trend", "est-search-from-manual", "est-pass-add", "est-passes-reset", "est-hy-order"]) expect(html).toContain(`data-testid="${id}"`);
    D.params.search.passes.forEach((p: { n_max: number }, i: number) => expect(html).toMatch(new RegExp(`data-testid="est-pass-${i}-n-max"[^>]*value="${p.n_max}"|value="${p.n_max}"[^>]*data-testid="est-pass-${i}-n-max"`)));
    expect(html).not.toContain("est-pass-0-hy-t");
    expect(html).toContain("Из модели: углы 0 / 0 / 0°, радиусы 60 × 60 × 30 м.");
    expect(D.params.search.order).toEqual(["ellipsoid", "hy", "sort_hs", "n_max_hole_octants", "n_min"]); // the order of the core is the one told
    expect(html).toContain("эллипсоид → правило ураганных проб → сортировка по расстоянию → n_max, лимиты скважины и октантов → n_min");
    expect(html).not.toMatch(/data-testid="est-pass-add"[^>]*disabled=""|disabled=""[^>]*data-testid="est-pass-add"/); // 3 < 5
  });
  it("the rule of Д-29 on: t, q and the mode of the pass", () => {
    putForm(form({ search: { ...EMPTY_SEARCH, passes: [pass({ hy: { on: true, t: "20", q: "0,5", mode: "cap" } })] } }));
    withDefaults();
    const html = render(createElement(StepSearch));
    for (const id of ["est-pass-0-hy-t", "est-pass-0-hy-q", "est-pass-0-hy-mode"]) expect(html).toContain(`data-testid="${id}"`);
    expect(html).toContain("урезать значение до t");
  });
  it("by hand: the angles and the radii; by a trend: the list of the trends", () => {
    putForm(form({ search: { ...EMPTY_SEARCH, from: "manual" } }));
    expect(render(createElement(StepSearch))).toContain('data-testid="est-r-0"');
    putForm(form({ search: { ...EMPTY_SEARCH, from: "trend" } }));
    expect(render(createElement(StepSearch))).toContain('data-testid="est-trend"');
  });
  it("without a model of variogram the source «по модели» says what to do", () => {
    putForm(form({ method: "nn", variogramId: null, search: { ...EMPTY_SEARCH, from: "model" } }));
    expect(render(createElement(StepSearch))).toContain("Модель вариограммы не выбрана");
  });
});

describe("step 5 drawn", () => {
  it("the fields of the method with their words; STATUS is on and cannot be switched off; the names go to the model", () => {
    putForm(form());
    withDefaults();
    const html = render(createElement(StepOutputs));
    for (const k of ALLOWED_FIELDS.ok) expect(html).toContain(`data-testid="est-field-${k}"`);
    expect(html).toMatch(/data-testid="est-field-STATUS"[^>]*disabled=""|disabled=""[^>]*data-testid="est-field-STATUS"/);
    expect(html).toContain(`placeholder="${META.outputs.prefix}"`);
    expect(html).toContain(`В модель запишутся поля: ${A.estimation.body.columns.map((c: { name: string }) => c.name).join(", ")}.`);
    expect(html).toContain("0 — оценён; 1 — вне домена; 2 — мало проб; 3 — вырожденная система");
  });
  it("IK: P1…PK by the thresholds; the fields the model has are told", () => {
    putForm(form({ method: "ik", thresholds: "0,5; 1; 2,5", variogramId: null, variogramIds: [ID.vario, ID.vario, ID.vario] }));
    expect(render(createElement(StepOutputs))).toContain("P1…P3");
    expect(render(createElement(StepOutputs))).toContain("AU_IK_P1, AU_IK_P2, AU_IK_P3");
    blocksStore.set({ details: { [ID.model]: { ...MODEL, fields: [...MODEL.fields, { name: "AU_IK_ETYPE", kind: "number", unit: null }] } } });
    const html = render(createElement(StepOutputs));
    expect(html).toContain('data-testid="est-fields-exist"');
    expect(html).toContain("уже есть поля: AU_IK_ETYPE");
  });
});

describe("step 6: the summary, the preview, the run", () => {
  const ctx = (): SummaryCtx => ({ model: { name: MODEL.name, cells: MODEL.cells, fields: MODEL.fields.map((x) => x.name) }, defaults, names: { filter: () => null, trend: () => null, variogram: () => D.variogram.name } });
  it("the summary tells the model, the domain, the samples, the method, the ellipsoid, the passes, the fields", () => {
    const f = form();
    const text = summaryRows(f, ctx()).map((r) => `${r.label}: ${r.value}`).join("\n");
    expect(flat(text)).toContain(`Блочная модель: «${MODEL.name}», блоков ${D.model.rows}`);
    expect(text).toContain("Домен блоков: ZONE = «окисл»");
    expect(flat(text)).toContain(`Проб в оценке: ${D.samples.counts.used} из ${D.samples.counts.rows}`);
    expect(text).toContain(`Модель вариограммы: «${D.variogram.name}»`);
    expect(text).toContain("Эллипсоид поиска: по модели вариограммы; радиусы 60 × 60 × 30 м");
    expect(text).toContain("Проходы: проход 1 — радиусы ×1, проб 4–24");
    expect(text).toContain(`Выходные поля: ${A.estimation.body.columns.length}`);
    expect(passLine(pass({ nHole: "3", hy: { on: true, t: "20", q: "0,5", mode: "drop" } }), 0)).toBe("проход 1 — радиусы ×1, проб 4–24, до 3 из скважины, ураганные: t = 20, до 0,5·r1, отбросить");
  });
  it("the warnings: no samples, the fields of the model, the warnings of the core about the variogram", () => {
    const f = form();
    expect(wizardWarnings(f, ctx())).toEqual([]);
    const names = outputsOf(f, defaults).names;
    const have = { ...ctx(), model: { ...ctx().model!, fields: [...ctx().model!.fields, names[1]] } };
    expect(wizardWarnings(f, have)[0]).toContain(`В модели уже есть поля: ${names[1]}.`);
    const none = readDefaults({ ...D, samples: { ...D.samples, counts: { ...D.samples.counts, used: 0 } }, variogram: { ...D.variogram, warnings: ["Доля Эрмита 0,9 < 0,95."] } });
    expect(wizardWarnings(f, { ...ctx(), defaults: none })).toEqual(["В домене нет проб: оценка не даст ни одного блока со статусом «оценён».", "Доля Эрмита 0,9 < 0,95."]);
  });
  it("the preview: the body of the form and the block go to the core; the answer is read; a gap is a dash", async () => {
    putForm(form());
    setPreviewAt(["4", "4", "7"]);
    await requestPreview();
    const sent = requests("POST /geostat/estimate/preview")[0].body as { estimation: Record<string, unknown>; at: unknown };
    expect(sent.at).toEqual({ ijk: [4, 4, 7] });
    const built = bodyOf(form());
    expect(built.ok && sent.estimation).toEqual(built.ok && built.body);
    const s = wizardStore.get().preview;
    expect(s.phase).toBe("ok");
    expect(s.data?.counts).toEqual(A.preview.body.counts);
    const html = render(createElement(StepRun));
    expect(html).toContain('data-testid="est-preview-result"');
    expect(html).toContain(`Блок ${A.preview.body.at.ijk.join("; ")}`);
    expect(html).toContain("оценён");
    expect(html).toContain(`Кандидатов в эллипсоиде ${A.preview.body.counts.candidates}, выбрано ${A.preview.body.counts.selected}`);
    expect(html).toContain('data-testid="est-preview-lambda-sum"');
    // the weights: the heaviest sample first
    const lam = A.preview.body.samples.filter((x: { lambda: number | null }) => x.lambda !== null).sort((a: { lambda: number }, b: { lambda: number }) => Math.abs(b.lambda) - Math.abs(a.lambda));
    expect(html.indexOf(`est-preview-sample-${lam[0].i}"`)).toBeGreaterThan(0);
    expect(html.indexOf(`est-preview-sample-${lam[0].i}"`)).toBeLessThan(html.indexOf(`est-preview-sample-${lam[1].i}"`));
    expect(readPreview({ ...A.preview.body, status: 2, values: { EST: null } }).values.EST).toBeNull();
  });
  it("a wrong block or a wrong form is told under the preview, nothing is asked of the core", async () => {
    putForm(form());
    setPreviewAt(["4", "x", "7"]);
    await requestPreview();
    expect(wizardStore.get().preview.error).toMatchObject({ param: "at.ijk.1" });
    expect(requests("POST /geostat/estimate/preview")).toHaveLength(0);
    setPreviewAt(["4", "4", "7"]);
    putForm(form({ disc: ["4", "11", "2"] }));
    await requestPreview();
    expect(wizardStore.get().preview.error).toMatchObject({ param: "disc.1" });
    expect(render(createElement(StepRun))).toContain('data-testid="est-preview-error"');
  });
  it("a refusal of the core stands under the preview; a change of the form makes the answer old", async () => {
    putForm(form());
    setPreviewAt(["4", "4", "7"]);
    core.force("POST /geostat/estimate/preview", { status: 422, body: { code: "bad_param", param: "at.ijk", message: "Блока нет в модели.", hint: "Выберите ячейку." } });
    await requestPreview();
    expect(wizardStore.get().preview.error).toMatchObject({ code: "bad_param", message: "Блока нет в модели." });
    await requestPreview();
    expect(wizardStore.get().preview.phase).toBe("ok");
    setForm({ disc: ["2", "2", "2"] });
    expect(wizardStore.get().preview.stale).toBe(true);
    expect(render(createElement(StepRun))).toContain('data-testid="est-preview-stale"');
  });
  it("the preview starts from the middle of the model", () => {
    expect(centreOf([10, 10, 8])).toEqual(["5", "5", "4"]);
    expect(centreOf(undefined)).toBeNull();
  });
  it("«Выполнить оценку»: the new row is saved, then `estimate.run` runs it by its number", async () => {
    putForm(form());
    await runWizard(app);
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    const run = requests("POST /geostat/estimate/run")[0].body;
    expect(run).toMatchObject({ estimation_ids: [A.estimation_post.body.id], expected_version: D.model.version, replace: false });
    // a saved, unchanged row is not saved again
    await runWizard(app);
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    expect(requests("POST /geostat/estimate/run")).toHaveLength(2);
  });
  it("a wrong form is not run: the refusal stands under its field", async () => {
    putForm(form({ disc: ["4", "11", "2"] }));
    await expect(runWizard(app)).rejects.toBeTruthy();
    expect(requests("POST /geostat/estimate/run")).toHaveLength(0);
    expect(wizardStore.get().step).toBe(1);
  });
  it("the step drawn: the buttons, the progress, the journal after the run", async () => {
    putForm(form());
    withDefaults();
    expect(render(createElement(StepRun))).toContain('data-testid="est-run-go"');
    await runWizard(app);
    await waitFor(() => wizardStore.get().saved !== null);
    const html = render(createElement(StepRun));
    expect(html).toContain('data-testid="est-run-done"');
    expect(html).toContain(A.run_result.body.log[0]);
    app.store.set({ projectReadOnly: true });
    expect(render(createElement(StepRun))).toMatch(/data-testid="est-run-go"[^>]*disabled|disabled=""[^>]*data-testid="est-run-go"/);
  });
});

describe("the window «Параметры оценки»", () => {
  it("`estimate.params {id}` opens the stored row on one page, the same fields as the wizard", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.params", { id: ID.row }, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Параметры оценки»" });
    expect(windowStore.get().windows.filter((w) => w.kind === PARAMS_WINDOW)).toHaveLength(1);
    expect(wizardStore.get().editing).toEqual({ id: ID.row, version: A.estimation.body.version });
    await app.registry.execute("estimate.params", { id: ID.row }, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === PARAMS_WINDOW)).toHaveLength(1);
    await waitFor(() => wizardStore.get().vario.loaded);
    withDefaults();
    const html = render(createElement(ParamsWindow, { winId: "w1" }));
    for (const id of ["est-step-1", "est-step-2", "est-step-3", "est-step-4", "est-step-5", "params-save", "params-close"]) expect(html).toContain(`data-testid="${id}"`);
    expect(html).toContain(`Строка №${ID.row} «${META.name}», версия ${A.estimation.body.version}`);
    expect(html).toMatch(/data-testid="params-save"[^>]*disabled|disabled=""[^>]*data-testid="params-save"/); // nothing changed
  });
  it("a change is saved over the version of the row; a row that is not there, or none, is told", async () => {
    await loadRows();
    await app.registry.execute("estimate.params", { id: ID.row }, "user");
    await waitFor(() => wizardStore.get().vario.loaded);
    setForm({ disc: ["3", "3", "3"] });
    await saveWizard();
    expect(requests(`PUT /geostat/estimations/${ID.row}`)[0].body).toMatchObject({ expected_version: A.estimation.body.version, disc: [3, 3, 3] });
    const none = await app.registry.execute("estimate.params", { id: ID.none }, "user");
    expect(none.ok).toBe(false);
    expect(none.result).toContain(`№${ID.none} не найдена`);
    wizardStore.set({ editing: null });
    expect((await app.registry.execute("estimate.params", {}, "user")).result).toContain("Не указана строка");
    expect(openParams).toBeTypeOf("function");
  });
  it("the assistant may not open it (a window needs a person)", async () => {
    expect(await app.registry.execute("estimate.params", { id: ID.row }, "claude")).toEqual({ ok: false, result: "Команда доступна только из интерфейса" });
  });
  it("the stored row in the wizard: step 4 shows its passes, the step 6 its summary", async () => {
    await startEdit(ID.row);
    wizardStore.set({ step: 4 });
    withDefaults();
    expect(render(createElement(Wizard, { winId: "w1" }))).toContain('data-testid="est-passes"');
  });
});
