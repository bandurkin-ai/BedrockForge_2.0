// The wizard «Оценка содержаний» (Э18, 18.19) on the answers of the contract: the defaults of the core for the data of the form, the
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
import { StepTarget } from "../../src/modules/estimation/StepTarget";
import { Wizard } from "../../src/modules/estimation/Wizard";
import { readDefaults } from "../../src/modules/estimation/api";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { loadVariogramModels, putForm, requestDefaults, rereadEstimation, saveWizard, scalesOf, setForm, setStep, startEdit, wizardStore } from "../../src/modules/estimation/wizardState";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const form = (patch: Partial<EstimationForm> = {}): EstimationForm => ({ ...EMPTY_FORM, modelId: 7, domainField: "ZONE", label: "окисл", setId: 3, table: "12", field: "AU", domain: { table: "14", field: "ZONE", values: ["окисл"] }, variogramId: 21, ...patch });
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
    expect(Object.fromEntries(q)).toEqual({ model_id: "7", set_id: "3", table: "12", field: "AU", method: "ok", block_domain: "ZONE", domain_table: "14", domain_field: "ZONE", domain_values: "окисл", variogram_id: "21" });
    const d = wizardStore.get().defaults.data!;
    expect(flat(countsText(d))).toBe("Проб: 1 192 из 1 196 (пропуск значения 4)");
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
    putForm(form({ variogramId: 22 }));
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
    expect(scalesOf()).toEqual({ 21: "z", 22: "y", 23: "back" });
    expect(requests("GET /geostat/variography/models/22")).toHaveLength(1);
    expect(requests("GET /geostat/variography/models/21")).toHaveLength(0); // a model of grades has nothing to look for
  });
  it("the step 3 offers the model of Y only with the back transform; the note names the way out", async () => {
    await loadVariogramModels();
    putForm(form(), { step: 3 });
    const html = render(createElement(StepMethod));
    expect(html).toMatch(/<option[^>]*value="21"[^>]*>AU · окисл · AU<\/option>/);
    expect(html).toMatch(/<option[^>]*value="22"[^>]*disabled[^>]*>[^<]*в нормальных оценках — недоступна/);
    expect(html).toMatch(/<option[^>]*value="23"(?![^>]*disabled)[^>]*>AU · Y назад · AU · Y с обратным преобразованием/);
    expect(html).toContain("Модель построена в нормальных оценках: такая модель не выбирается. Выполните обратное преобразование модели");
  });
  it("a model of Y chosen by a call is refused before the core is asked: `variogram_scale` under the model of the step 3", async () => {
    await loadVariogramModels();
    putForm(form({ variogramId: 22 }));
    await expect(saveWizard()).rejects.toThrow("Модель построена в нормальных оценках");
    const s = wizardStore.get();
    expect(s.issue).toMatchObject({ code: "variogram_scale", param: "variogram.model_id" });
    expect(s.step).toBe(3);
    expect(requests("POST /geostat/estimations")).toHaveLength(0);
    expect(render(createElement(StepMethod))).toContain('data-testid="est-issue-variogram"');
  });
  it("the models in the options of IK «на порог»: one list for each threshold", async () => {
    await loadVariogramModels();
    putForm(form({ method: "ik", thresholds: "0,5; 1", variogramId: null, variogramIds: [21, null] }), { step: 3 });
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
    expect(saved).toEqual({ id: 41, name: "AU · OK · окисл", version: 1 });
    const sent = requests("POST /geostat/estimations")[0].body;
    const built = bodyOf(form(), { scales: scalesOf() });
    expect(built.ok && sent).toEqual(built.ok && built.body);
    const s = wizardStore.get();
    expect(s.editing).toEqual({ id: 41, version: 1 });
    expect(s.dirty).toBe(false);
    expect(s.saving).toBe(false);
    await vi_waitFor(() => estimationStore.get().loaded);
    expect(estimationStore.get().rows.map((r) => r.id)).toEqual([41]);
    // the second save is a replacement over the version
    setForm({ name: "Другое имя" });
    await saveWizard();
    const put = requests("PUT /geostat/estimations/41");
    expect(put).toHaveLength(1);
    expect(put[0].body).toMatchObject({ name: "Другое имя", expected_version: 1, model_id: 7 });
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
    await startEdit(41);
    const s = wizardStore.get();
    expect(s.form).toMatchObject({ name: "AU · OK · окисл", modelId: 7, variogramId: 21 });
    expect(s.editing).toEqual({ id: 41, version: 2 });
    await loadVariogramModels();
    setForm({ disc: ["2", "2", "2"] });
    await saveWizard();
    const put = requests("PUT /geostat/estimations/41")[0].body;
    expect(put).toMatchObject({ expected_version: 2, disc: [2, 2, 2] });
    expect(put.search).toEqual((A.estimation.body.meta as { search: unknown }).search); // the steps 4–6 go back as they were
  });
  it("another version stored: the words of the core, «Перечитать» and «Сохранить как новые»", async () => {
    await startEdit(41);
    await loadVariogramModels();
    setForm({ name: "Моя правка" });
    core.force("PUT /geostat/estimations/41", A.estimation_put_conflict);
    await expect(saveWizard()).rejects.toThrow("изменены в другом окне");
    expect(wizardStore.get().issue).toMatchObject({ code: "version_conflict", reread: true, param: null });
    expect(wizardStore.get().step).toBe(6);
    const html = render(createElement(Wizard, { winId: "w1" }));
    expect(html).toContain("Параметры оценки изменены в другом окне.");
    expect(html).toContain("Перечитать");
    expect(html).toContain("Сохранить как новые");
    // «Сохранить как новые»: a new row, not the one of the other window
    await saveWizard({ asNew: true });
    expect(requests("POST /geostat/estimations")).toHaveLength(1);
    expect(requests("POST /geostat/estimations")[0].body.name).toBe("Моя правка");
  });
  it("«Перечитать»: the stored row replaces the form, the version is the new one", async () => {
    await startEdit(41);
    setForm({ name: "Моя правка" });
    await rereadEstimation();
    expect(wizardStore.get().form.name).toBe("AU · OK · окисл");
    expect(wizardStore.get().dirty).toBe(false);
  });
});

describe("the steps drawn", () => {
  it("step 1: the models with blocks and a regular step only; the labels of the domain of the model", async () => {
    putForm(form({ domainField: "ZONE", label: "" }));
    const html = render(createElement(StepTarget));
    expect(html).toContain('data-testid="est-model"');
    expect(html).toMatch(/<option value="7"[^>]*>Модель<\/option>/);
    expect(html).not.toContain("Переменный шаг");
    expect(html).not.toContain(">Прототип<");
    for (const l of ["окисл", "перех", "перв"]) expect(html).toContain(`>${l}</option>`);
    expect(flat(html)).toContain("Блоков 1 200");
    expect(html).toContain("размер 10 × 10 × 5 м");
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
    expect(html).toContain("Проб: 1 192 из 1 196 (пропуск значения 4)");
    expect(html).toContain("декластерированное 1,62 г/т");
    expect(html).toContain("веса: DECL_W");
    expect(html).toMatch(/<td[^>]*>перв<\/td><td>112<\/td><td>0,9<\/td><td>—<\/td>/); // a gap is a dash, not 0
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
    expect(estimationStore.get().rows).toHaveLength(1);
    expect(wizardStore.get().form.modelId).toBe(7);
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
