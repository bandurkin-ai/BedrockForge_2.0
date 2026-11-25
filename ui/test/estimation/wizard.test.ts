// The wizard «Оценка содержаний» (Э18, 18.19) on the real answers of the core: the defaults of the core for the data of the form, the
// scale of the models of variogram (Д-38: a model in Y without its back transform is not chosen — `variogram_scale`), the save
// (`POST` / `PUT /estimations`, the refusals under their field, 409 «Перечитать» / «Сохранить как новые»), the three steps drawn.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { bodyOf, EMPTY_FORM, type EstimationForm } from "../../src/modules/estimation/form";
import { openWizard } from "../../src/modules/estimation/bind";
import { StepMethod } from "../../src/modules/estimation/StepMethod";
import { StepSamples, countsText, mappingNotes } from "../../src/modules/estimation/StepSamples";
import { g, nf } from "../../src/modules/estimation/Parts";
import { StepTarget } from "../../src/modules/estimation/StepTarget";
import { Wizard } from "../../src/modules/estimation/Wizard";
import { readDefaults } from "../../src/modules/estimation/api";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { loadVariogramModels, putForm, requestDefaults, rereadEstimation, saveWizard, scalesOf, setForm, setStep, startEdit, wizardStore } from "../../src/modules/estimation/wizardState";
import { A, FIELD, ID, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const D = A.defaults.body;
const ROW = A.estimation.body;
const form = (patch: Partial<EstimationForm> = {}): EstimationForm => ({ ...EMPTY_FORM, modelId: ID.model, domainField: "ZONE", label: "окисл", setId: ID.set, table: ID.table, field: FIELD, domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] }, variogramId: ID.vario, ...patch });
const flat = (s: string): string => s.replace(/[\u00a0\u202f]/g, " ");
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);

describe("the defaults of the core for the data of the form", () => {
  it("nothing is asked until the model, the set, the table and the field are chosen", async () => {
    putForm(form({ setId: null }));
    requestDefaults(0);
    expect(wizardStore.get().defaults.phase).toBe("idle");
    expect(requests("GET /geostat/estimate/defaults")).toHaveLength(0);
  });
  it("the request has the domain of the blocks and of the samples, the model; the answer is kept (the counts, the means, the mapping)", async () => {
    putForm(form());
    requestDefaults(0);
    expect(wizardStore.get().defaults.phase).toBe("loading");
    await vi_waitFor(() => wizardStore.get().defaults.phase === "ok");
    const q = new URLSearchParams(requests("GET /geostat/estimate/defaults")[0].query);
    expect(Object.fromEntries(q)).toEqual({ model_id: String(ID.model), set_id: String(ID.set), table: ID.table, field: FIELD, method: "ok", block_domain: "ZONE", domain_table: ID.domTable, domain_field: "ZONE", domain_values: "окисл", variogram_id: String(ID.vario) });
    const d = wizardStore.get().defaults.data!;
    expect(flat(countsText(d))).toBe(`Проб: ${nf(D.samples.counts.used)} из ${nf(D.samples.counts.rows)} (пропуск значения ${D.samples.counts.missing}, вне домена ${D.samples.counts.outside_domain})`);
    expect(mappingNotes(d, "окисл").matched).toContain("окисл");
    expect(mappingNotes(d, "нет такого").unmatched).toContain("не нашлось значения");
    expect(mappingNotes(d, "").matched).toBeNull();
  });
  it("the same request is not asked twice; another one overtakes the old one", async () => {
    putForm(form());
    requestDefaults(0);
    await vi_waitFor(() => wizardStore.get().defaults.phase === "ok");
    requestDefaults(0);
    expect(requests("GET /geostat/estimate/defaults")).toHaveLength(1);
    setForm({ method: "idw" });
    requestDefaults(0);
    const seq = wizardStore.get().defaults.seq;
    setForm({ method: "nn" });
    requestDefaults(0);
    expect(wizardStore.get().defaults.seq).toBe(seq + 1);
    await vi_waitFor(() => wizardStore.get().defaults.phase === "ok" && wizardStore.get().defaults.key.includes('"method":"nn"'));
  });
  it("a refusal of the core stands as the issue of the defaults, with the path of the body (`variogram_id` → `variogram.model_id`)", async () => {
    putForm(form({ variogramId: ID.varioY }));
    core.force("GET /geostat/estimate/defaults", { status: 422, body: { ...A.variogram_scale.body, param: "variogram_id" } });
    requestDefaults(0);
    await vi_waitFor(() => wizardStore.get().defaults.phase === "error");
    expect(wizardStore.get().defaults.error).toMatchObject({ code: "variogram_scale", param: "variogram.model_id", message: "Модель построена в нормальных оценках" });
    expect(wizardStore.get().defaults.data).toBeNull();
  });
});

describe("the models of variogram: the scale (Д-38)", () => {
  it("Z; Y without the back transform; Y with it", async () => {
    await loadVariogramModels();
    expect(scalesOf()).toEqual({ [ID.vario]: "z", [ID.varioY]: "y", [ID.varioBack]: "back" });
    expect(requests(`GET /geostat/variography/models/${ID.varioY}`)).toHaveLength(1);
    expect(requests(`GET /geostat/variography/models/${ID.vario}`)).toHaveLength(0); // a model of grades has nothing to look for
  });
  it("the step 3 offers the model of Y only with the back transform; the note names the way out", async () => {
    await loadVariogramModels();
    putForm(form(), { step: 3 });
    const html = render(createElement(StepMethod));
    expect(html).toMatch(new RegExp(`<option[^>]*value="${ID.vario}"[^>]*>${D.variogram.name} · ${FIELD}</option>`));
    expect(html).toMatch(new RegExp(`<option[^>]*value="${ID.varioY}"[^>]*disabled[^>]*>[^<]*в нормальных оценках — недоступна`));
    expect(html).toMatch(new RegExp(`<option[^>]*value="${ID.varioBack}"(?![^>]*disabled)[^>]*>${FIELD} · Y назад · ${FIELD} · Y с обратным преобразованием`));
    expect(html).toContain("Модель построена в нормальных оценках: такая модель не выбирается. Выполните обратное преобразование модели");
  });
  it("a model of Y chosen by a call is refused before the core is asked: `variogram_scale` under the model of the step 3", async () => {
    await loadVariogramModels();
    putForm(form({ variogramId: ID.varioY }));
    await expect(saveWizard()).rejects.toThrow("Модель построена в нормальных оценках");
    const s = wizardStore.get();
    expect(s.issue).toMatchObject({ code: "variogram_scale", param: "variogram.model_id" });
    expect(s.step).toBe(3);
    expect(requests("POST /geostat/estimations")).toHaveLength(0);
    expect(render(createElement(StepMethod))).toContain('data-testid="est-issue-variogram"');
  });
  it("the models in the options of IK «на порог»: one list for each threshold", async () => {
    await loadVariogramModels();
    putForm(form({ method: "ik", thresholds: "0,5; 1", variogramId: null, variogramIds: [ID.vario, null] }), { step: 3 });
    const html = render(createElement(StepMethod));
    expect(html).toContain('data-testid="est-variogram-0"');
    expect(html).toContain('data-testid="est-variogram-1"');
    expect(html).not.toContain('data-testid="est-variogram"');
  });
  it("the warnings of the core about the model (the Hermite share, the other field) are shown, not forbidden", async () => {
    await loadVariogramModels();
    const d = readDefaults({ ...A.defaults.body, variogram: { ...A.defaults.body.variogram, scale: "back", share: 0.9, warnings: ["Доля Эрмита 0,9 < 0,95 — обратное преобразование приближённое."] } });
    wizardStore.set({ defaults: { phase: "ok", key: "k", seq: 1, data: d, error: null } });
    putForm(form(), { step: 3 });
    expect(render(createElement(StepMethod))).toContain("Доля Эрмита 0,9");
  });
});

describe("the save", () => {
  it("POST /estimations with the body of the steps 1–3; the row is read again; the version is kept for the next save", async () => {
    await loadVariogramModels();
    putForm(form());
    const saved = await saveWizard();
    expect(saved).toEqual(A.estimation_post.body);
    const sent = requests("POST /geostat/estimations")[0].body;
    const built = bodyOf(form(), { scales: scalesOf() });
    expect(built.ok && sent).toEqual(built.ok && built.body);
    const s = wizardStore.get();
    expect(s.editing).toEqual({ id: saved.id, version: saved.version });
    expect(s.dirty).toBe(false);
    expect(s.saving).toBe(false);
    await vi_waitFor(() => estimationStore.get().loaded);
    expect(estimationStore.get().rows.map((r) => r.id)).toEqual(A.estimations.body.estimations.map((r: { id: number }) => r.id));
    // the second save is a replacement over the version
    setForm({ name: "Другое имя" });
    await saveWizard();
    const put = requests(`PUT /geostat/estimations/${saved.id}`);
    expect(put).toHaveLength(1);
    expect(put[0].body).toMatchObject({ name: "Другое имя", expected_version: saved.version, model_id: ID.model });
  });
  it("a wrong form is told at its step and does not reach the core", async () => {
    putForm(form({ disc: ["4", "11", "2"] }), { step: 3 });
    await expect(saveWizard()).rejects.toThrow("от 1 до 10");
    expect(wizardStore.get().step).toBe(1);
    expect(requests("POST /geostat/estimations")).toHaveLength(0);
    expect(render(createElement(StepTarget))).toContain('data-testid="est-issue-disc"');
  });
  it("a refusal of the core stands under its field; the step is the one of the path", async () => {
    putForm(form());
    core.force("POST /geostat/estimations", { status: 422, body: { code: "domain_unmapped", message: "В модели нет домена «окисл».", hint: "Выберите другое значение.", param: "target.label" } });
    await expect(saveWizard()).rejects.toThrow("В модели нет домена");
    expect(wizardStore.get().issue).toMatchObject({ code: "domain_unmapped", param: "target.label" });
    expect(wizardStore.get().step).toBe(1);
    expect(render(createElement(StepTarget))).toContain("В модели нет домена");
    expect(wizardStore.get().saving).toBe(false);
    // the next change of the form takes the old refusal away
    setForm({ label: "перех" });
    expect(wizardStore.get().issue).toBeNull();
  });
  it("a refusal of a step the wizard has not got yet (the search of 18.20) takes the person to it", async () => {
    putForm(form());
    core.force("POST /geostat/estimations", { status: A.bad_param.status, body: A.bad_param.body });
    await expect(saveWizard()).rejects.toThrow("n_max");
    expect(wizardStore.get().step).toBe(4);
  });
  it("an open project «только просмотр»: the core says 423, the wizard tells it", async () => {
    putForm(form());
    core.force("POST /geostat/estimations", { status: 423, body: { code: "project_read_only", message: "Проект открыт только для чтения.", hint: "Откройте проект для записи." } });
    await expect(saveWizard()).rejects.toThrow("только для чтения");
    expect(wizardStore.get().issue?.code).toBe("project_read_only");
  });
});

describe("editing a stored row; 409 version_conflict", () => {
  it("«Изменить»: the form is the stored row, the version goes with the save", async () => {
    await startEdit(ID.row);
    const s = wizardStore.get();
    expect(s.form).toMatchObject({ name: ROW.name, modelId: ID.model, variogramId: ID.vario });
    expect(s.editing).toEqual({ id: ID.row, version: ROW.version });
    await loadVariogramModels();
    setForm({ disc: ["2", "2", "2"] });
    await saveWizard();
    const put = requests(`PUT /geostat/estimations/${ID.row}`)[0].body;
    expect(put).toMatchObject({ expected_version: ROW.version, disc: [2, 2, 2] });
    expect(put.search).toEqual((A.estimation.body.meta as { search: unknown }).search); // the steps 4–6 go back as they were
  });
  it("another version stored: the words of the core, «Перечитать» and «Сохранить как новые»", async () => {
    await startEdit(ID.row);
    await loadVariogramModels();
    setForm({ name: "Моя правка" });
    core.force(`PUT /geostat/estimations/${ID.row}`, A.estimation_put_conflict);
    await expect(saveWizard()).rejects.toThrow(A.estimation_put_conflict.body.message);
    expect(wizardStore.get().issue).toMatchObject({ code: "version_conflict", reread: true, param: null });
    expect(wizardStore.get().step).toBe(6);
    const html = render(createElement(Wizard, { winId: "w1" }));
    expect(html).toContain(A.estimation_put_conflict.body.message);
    expect(html).toContain("Перечитать");
    expect(html).toContain("Сохранить как новые");
    // «Сохранить как новые»: a new row, not the one of the other window
    await saveWizard({ asNew: true });
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    expect(requests("POST /geostat/estimations")[0].body.name).toBe("Моя правка");
  });
  it("«Перечитать»: the stored row replaces the form, the version is the new one", async () => {
    await startEdit(ID.row);
    setForm({ name: "Моя правка" });
    await rereadEstimation();
    expect(wizardStore.get().form.name).toBe(ROW.name);
    expect(wizardStore.get().dirty).toBe(false);
  });
});

describe("the steps drawn", () => {
  it("step 1: the models with blocks and a regular step only; the labels of the domain of the model", async () => {
    putForm(form({ domainField: "ZONE", label: "" }));
    const html = render(createElement(StepTarget));
    expect(html).toContain('data-testid="est-model"');
    expect(html).toMatch(new RegExp(`<option value="${ID.model}"[^>]*>${D.model.name}</option>`));
    expect(html).not.toContain("Переменный шаг");
    expect(html).not.toContain(">Прототип<");
    for (const l of D.model.domain_fields[0].labels) expect(html).toContain(`>${l}</option>`);
    expect(flat(html)).toContain(`Блоков ${nf(D.model.rows)}`);
    expect(html).toContain(`размер ${D.model.size.join(" × ")} м`);
    expect(html).toContain('placeholder="AU · OK"'); // the name the core will give
  });
  it("step 2: the data, the soft boundary, the top cut, the weights", () => {
    putForm(form({ soft: { on: true, domains: [], dist: "5" }, topcut: { mode: "value", field: "", value: "30" } }), { step: 2 });
    const html = render(createElement(StepSamples));
    for (const id of ["est-set", "est-table", "est-field", "est-domain", "est-soft-on", "est-soft-dist", "est-soft-domains", "est-topcut", "est-topcut-value", "est-weights"]) expect(html).toContain(`data-testid="${id}"`);
    expect(html).toContain("DECL_W, если есть");
  });
  it("step 2: the summary of the core under the data (the counts, the declustered means, the values of the domain)", () => {
    putForm(form(), { step: 2 });
    wizardStore.set({ defaults: { phase: "ok", key: "k", seq: 1, data: readDefaults(A.defaults.body), error: null } });
    const html = flat(render(createElement(StepSamples)));
    const c = D.samples.counts;
    expect(html).toContain(`Проб: ${nf(c.used)} из ${nf(c.rows)} (пропуск значения ${c.missing}, вне домена ${c.outside_domain})`);
    expect(html).toContain(`декластерированное ${g(D.samples.mean.declus)} ${D.samples.unit}`);
    expect(html).toContain(`веса: ${D.samples.weights}`);
    const v = D.samples.domain.values[1]; // the values of the domain: the count and the means of each
    expect(html).toContain(`${v.value}</td><td>${v.n}</td><td>${g(v.naive)}</td><td>${g(v.declus)}</td>`);
    // a gap is a dash, not 0
    wizardStore.set({ defaults: { phase: "ok", key: "k", seq: 1, data: readDefaults({ ...D, samples: { ...D.samples, domain: { ...D.samples.domain, values: [{ value: "перв", n: 112, naive: 0.9, declus: null }] } } }), error: null } });
    expect(flat(render(createElement(StepSamples)))).toMatch(/<td[^>]*>перв<\/td><td>112<\/td><td>0,9<\/td><td>—<\/td>/);
  });
  it("step 2: a label of the blocks with no value of the samples is told", () => {
    putForm(form({ label: "руда" }), { step: 2 });
    wizardStore.set({ defaults: { phase: "ok", key: "k", seq: 1, data: readDefaults(A.defaults.body), error: null } });
    expect(render(createElement(StepSamples))).toContain("не нашлось значения");
  });
  it("step 3: the five methods; the parameters of the chosen one", () => {
    putForm(form({ method: "idw" }), { step: 3 });
    let html = render(createElement(StepMethod));
    for (const m of ["nn", "idw", "sk", "ok", "ik"]) expect(html).toContain(`data-testid="est-method-${m}"`);
    expect(html).toContain('data-testid="est-power"');
    expect(html).not.toContain('data-testid="est-thresholds"');
    putForm(form({ method: "ik" }), { step: 3 });
    html = render(createElement(StepMethod));
    expect(html).toContain('data-testid="est-thresholds"');
    expect(html).toContain("своя модель на каждый порог");
    putForm(form({ method: "sk", meanMode: "value" }), { step: 3 });
    expect(render(createElement(StepMethod))).toContain('data-testid="est-mean-value"');
  });
  it("the frame: the six steps, the first three open, the others wait for 18.20; «Далее» stops at the third", () => {
    putForm(form(), { step: 3 });
    const html = render(createElement(Wizard, { winId: "w1" }));
    const tag = (id: string) => new RegExp(`<button[^>]*data-testid="${id}"[^>]*>`).exec(html)?.[0] ?? "";
    for (const t of ["Модель и цель", "Пробы", "Метод и вариограмма", "Поиск и проходы", "Выходные поля", "Задание"]) expect(html).toContain(t);
    for (const n of [4, 5, 6]) expect(tag(`est-step-${n}-tab`)).toContain("disabled");
    for (const n of [1, 2, 3]) expect(tag(`est-step-${n}-tab`)).not.toContain("disabled");
    expect(tag("est-step-3-tab")).toContain('aria-current="step"');
    expect(tag("est-next")).toContain("disabled");
    expect(tag("est-back")).not.toContain("disabled");
    setStep(1);
    expect(render(createElement(Wizard, { winId: "w1" }))).toMatch(/<button[^>]*disabled=""[^>]*data-testid="est-back"/);
  });
  it("the frame in a project open for reading only: «Сохранить» is off with the reason", () => {
    app.store.set({ projectReadOnly: true });
    expect(render(createElement(Wizard, { winId: "w1" }))).toMatch(/<button[^>]*disabled=""[^>]*title="Проект открыт только для чтения"[^>]*data-testid="est-save"/);
  });
  it("the wizard opened by the command reads the models and the project's rows", async () => {
    await openWizard(app);
    await vi_waitFor(() => wizardStore.get().vario.loaded);
    await loadRows();
    expect(estimationStore.get().rows).toHaveLength(A.estimations.body.estimations.length);
    expect(wizardStore.get().form.modelId).toBe(ID.model);
  });
});

/** Waits (the microtasks and the timers of the stub) until `cond` holds. */
async function vi_waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await new Promise((r) => setTimeout(r, 5));
  }
}
