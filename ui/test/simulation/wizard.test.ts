// The wizard «Моделирование» (Э18б, 18.35) on the real answers of the core: the defaults of the core for the data of the form (the samples, no
// model of variogram), the stored row in the form, the save (`POST` / `PUT /simulations`, the refusals under their field on their step, 409
// «Перечитать»), the six steps drawn (the steps of the estimation through the host, the models of a kind, the tails), the run (the job followed
// to its end, cancelled, failed) and its journal.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { FormHostContext } from "../../src/modules/estimation/host";
import { StepSamples } from "../../src/modules/estimation/StepSamples";
import { StepTarget } from "../../src/modules/estimation/StepTarget";
import { simulationHost } from "../../src/modules/simulation/host";
import { formOfMeta, hasModel, type SimForm } from "../../src/modules/simulation/form";
import { journalLines, runStore, runSummary } from "../../src/modules/simulation/runState";
import { readRun } from "../../src/modules/simulation/api";
import { SimWizard } from "../../src/modules/simulation/SimWizard";
import { StepKind } from "../../src/modules/simulation/StepKind";
import { StepReal } from "../../src/modules/simulation/StepReal";
import { StepRun, summaryRows, wizardWarnings } from "../../src/modules/simulation/StepRun";
import { StepSearch } from "../../src/modules/simulation/StepSearch";
import { modelOption, StepVario } from "../../src/modules/simulation/StepVario";
import { loadRows } from "../../src/modules/simulation/state";
import { defaultsQueryOf, loadVariogramModels, matchIndicatorModels, putForm, requestDefaults, rereadSimulation, runWizard, saveSimulation, setForm, setKind, simWizardStore, startEdit } from "../../src/modules/simulation/wizardState";
import { bodyOf } from "../../src/modules/simulation/form";
import { flat, ID, INFOS, installSimCore, newApp, requests, S, SG, SID, teardown, THRESHOLDS, VARIO_ROWS, VID, waitFor, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(teardown);

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(FormHostContext.Provider, { value: simulationHost }, el)));
const meta = (id: number) => INFOS[id].meta as Record<string, unknown>;
const sgs = (): SimForm => formOfMeta(meta(SID.sgs));
const THR = THRESHOLDS.map((t) => String(t).replace(".", ",")).join("; ");

describe("the defaults of the core for the data of the form", () => {
  it("nothing is asked until the model, the set, the table and the field are chosen", () => {
    putForm({ ...sgs(), setId: null });
    requestDefaults(0);
    expect(simWizardStore.get().defaults.phase).toBe("idle");
    expect(requests(core, "GET /geostat/estimate/defaults")).toHaveLength(0);
  });
  it("the request has the domains and no model of variogram and no method: a model in normal scores is not what `defaults` reads", async () => {
    const f = sgs();
    putForm(f);
    expect(defaultsQueryOf(f)).not.toHaveProperty("method");
    requestDefaults(0);
    await waitFor(() => simWizardStore.get().defaults.phase === "ok");
    const q = Object.fromEntries(new URLSearchParams(requests(core, "GET /geostat/estimate/defaults")[0].query));
    expect(q).toEqual({ model_id: String(ID.model), set_id: String(ID.set), table: ID.table, field: "AU", block_domain: "ZONE", domain_table: ID.domTable, domain_field: "ZONE", domain_values: "окисл" });
    expect(simWizardStore.get().defaults.data?.samples.counts.used).toBeGreaterThan(0);
  });
});

describe("the stored row, the save", () => {
  it("«Изменить»: the row is the form, its version goes with the save; a PUT carries the version", async () => {
    await startEdit(SID.sgs);
    const s = simWizardStore.get();
    expect(s.editing).toEqual({ id: SID.sgs, version: INFOS[SID.sgs].version });
    expect(s.form).toMatchObject({ kind: "sgs", nReal: "5", seed: "99" });
    setForm({ nReal: "6" });
    expect(simWizardStore.get().dirty).toBe(true);
    await loadVariogramModels();
    const saved = await saveSimulation();
    const put = requests(core, `PUT /geostat/simulations/${SID.sgs}`)[0];
    expect(put.body).toMatchObject({ expected_version: INFOS[SID.sgs].version, n_real: 6, kind: "sgs" });
    expect(saved).toMatchObject({ id: SID.sgs, version: INFOS[SID.sgs].version + 1 });
    expect(simWizardStore.get()).toMatchObject({ dirty: false, editing: { id: SID.sgs, version: INFOS[SID.sgs].version + 1 } });
  });
  it("a new row: POST with the body of the form; «Сохранить как новое» does not take the version", async () => {
    putForm(sgs());
    await loadVariogramModels();
    const saved = await saveSimulation();
    const post = requests(core, "POST /geostat/simulations")[0];
    const want = bodyOf(sgs(), simWizardStore.get().vario);
    expect(want.ok && post.body).toEqual(want.ok && want.body);
    expect(saved).toEqual({ id: S.simulation_post.body.id, name: S.simulation_post.body.name, version: S.simulation_post.body.version });
    await startEdit(SID.sgs);
    await loadVariogramModels();
    await saveSimulation({ asNew: true });
    expect(requests(core, "POST /geostat/simulations")).toHaveLength(2);
    expect(requests(core, `PUT /geostat/simulations/${SID.sgs}`)).toHaveLength(0);
  });
  it("a wrong form is not sent: the words stand under the field, on the step of the field", async () => {
    putForm({ ...sgs(), nReal: "0" });
    await expect(saveSimulation()).rejects.toThrow(/реализаций/);
    expect(requests(core, "POST /geostat/simulations")).toHaveLength(0);
    expect(simWizardStore.get()).toMatchObject({ step: 5, issue: { param: "n_real", code: "bad_param" } });
  });
  it("the refusals of the core stand under their field: the name taken (step 1), a range (step 5), the scale of the model (step 3)", async () => {
    putForm(sgs());
    core.force("POST /geostat/simulations", { status: 409, body: S.simulation_name_taken.body });
    await expect(saveSimulation()).rejects.toThrow();
    expect(simWizardStore.get()).toMatchObject({ step: 1, issue: { param: "name", code: "name_taken" } });
    core.force("POST /geostat/simulations", { status: 422, body: S.bad_param.body });
    await expect(saveSimulation()).rejects.toThrow();
    expect(simWizardStore.get()).toMatchObject({ step: 5, issue: { param: "n_real" } });
    core.force("POST /geostat/simulations", { status: 422, body: S.variogram_scale.body });
    await expect(saveSimulation()).rejects.toThrow();
    expect(simWizardStore.get()).toMatchObject({ step: 3, issue: { param: "variogram.model_id", code: "variogram_scale", message: S.variogram_scale.body.message } });
  });
  it("409 `version_conflict`: «Перечитать» takes the stored row and its new version", async () => {
    await startEdit(SID.sgs);
    await loadVariogramModels();
    core.force(`PUT /geostat/simulations/${SID.sgs}`, { status: 409, body: S.simulation_put_conflict.body });
    setForm({ nReal: "7" });
    await expect(saveSimulation()).rejects.toThrow();
    expect(simWizardStore.get().issue).toMatchObject({ code: "version_conflict", reread: true });
    await rereadSimulation();
    expect(simWizardStore.get()).toMatchObject({ form: { nReal: "5" }, editing: { id: SID.sgs, version: INFOS[SID.sgs].version }, dirty: false });
  });
});

describe("the kind", () => {
  it("another kind drops the models of the old one (SGS wants normal scores, SIS the units of the field); SIS «по категориям» has no top cut", () => {
    putForm(sgs());
    setKind("sis");
    expect(simWizardStore.get().form).toMatchObject({ kind: "sis", variogramId: null, variogramIds: [] });
    setKind("sgs");
    expect(simWizardStore.get().form).toMatchObject({ kind: "sgs", sisBy: "thresholds", ikModels: "per" });
  });
  it("«Подобрать по порогам»: the model of an indicator of each threshold; a threshold without one keeps what was chosen", async () => {
    putForm({ ...formOfMeta(meta(SID.sis)), variogramIds: [] });
    await loadVariogramModels();
    expect(matchIndicatorModels(THRESHOLDS)).toBe(3);
    expect(simWizardStore.get().form.variogramIds).toEqual(VID.ind);
    setForm({ variogramIds: [null, VID.vz, null] });
    expect(matchIndicatorModels([0.7, 99, 1.5])).toBe(2);
    expect(simWizardStore.get().form.variogramIds).toEqual([VID.ind[0], VID.vz, VID.ind[2]]);
  });
});

describe("the steps drawn", () => {
  it("step 1: the three kinds, the name with the default of the core, the model; no sub-blocks and no discretization (the nodes are the parents)", () => {
    putForm(sgs());
    const html = render(createElement("div", null, createElement(StepKind), createElement(StepTarget)));
    for (const k of ["sgs", "sis", "cosgs"]) expect(html).toContain(`data-testid="sim-kind-${k}"`);
    expect(html).toContain('data-testid="sim-kind-sgs"');
    expect(html).toMatch(/data-testid="sim-kind-sgs"[^>]*checked/);
    expect(html).toContain("AU · SGS · окисл");
    expect(html).toContain('data-testid="est-model"');
    expect(html).not.toContain("est-disc");
    expect(html).not.toContain("est-sub-parent");
  });
  it("step 2: the samples of the estimation — the set, the table, the field, the domain, the soft boundary, the weights", () => {
    putForm(sgs());
    const html = render(createElement(StepSamples));
    for (const id of ["est-set", "est-table", "est-field", "est-domain", "est-soft-on", "est-weights", "est-topcut"]) expect(html).toContain(`data-testid="${id}"`);
  });
  it("step 3, SGS: the models in normal scores only are offered (the others are disabled, with the words)", async () => {
    putForm(sgs());
    await loadVariogramModels();
    const html = flat(render(createElement(StepVario)));
    expect(html).toContain('data-testid="sim-variogram"');
    expect(html).toContain("в нормальных оценках");
    expect(html).toMatch(new RegExp(`<option value="${VID.vz}" disabled="" data-usable="false">[^<]*для SGS не подходит`));
    expect(html).toMatch(new RegExp(`<option value="${VID.vy}" data-usable="true"`));
    expect(html).not.toContain("sim-secondary");
    expect(modelOption({ name: "М", field: "AU", transform: "none" }, false, true)).toEqual({ text: "М · AU · в единицах поля — для SGS не подходит", usable: false });
  });
  it("step 3, SIS by thresholds: the thresholds, the model of each, the button of the match; a model in Y without the back transform is disabled", async () => {
    putForm(formOfMeta(meta(SID.sis)));
    await loadVariogramModels();
    const html = flat(render(createElement(StepVario)));
    expect(html).toContain(`value="${THR}"`);
    for (let i = 0; i < THRESHOLDS.length; i++) expect(html).toContain(`data-testid="sim-variogram-${i}"`);
    expect(html).toContain('data-testid="sim-match"');
    expect(html).toMatch(new RegExp(`<option value="${VID.vy}" disabled="" data-usable="false">[^<]*в нормальных оценках — недоступна`));
    expect(html).toMatch(new RegExp(`<option value="${VID.back}" data-usable="true">[^<]*Y с обратным преобразованием`));
    expect(html).toMatch(/индикатор 0,7/);
  });
  it("step 3, categorical SIS: the codes and the names; the median model — one select", async () => {
    putForm(formOfMeta(meta(SID.cat)));
    await loadVariogramModels();
    const html = flat(render(createElement(StepVario)));
    expect(html).toContain('value="1; 2; 3"');
    expect(html).toContain("Окисленные; Переходные; Первичные");
    expect(html).toContain('data-testid="sim-variogram"');
    expect(html).not.toContain("sim-variogram-0");
  });
  it("step 3, co-SGS: the numeric fields of the block model, ρ by the samples or typed", async () => {
    putForm({ ...formOfMeta(meta(SID.co)), rhoMode: "value", rho: "0,5" });
    await loadVariogramModels();
    const html = render(createElement(StepVario));
    expect(html).toContain('data-testid="sim-secondary"');
    expect(html).toMatch(new RegExp(`<option value="${SG}"[^>]*>${SG}`));
    expect(html).toContain('data-testid="sim-rho"');
    expect(html).not.toContain('<option value="ZONE"');
    putForm(formOfMeta(meta(SID.co)));
    expect(render(createElement(StepVario))).not.toContain('data-testid="sim-rho"');
  });
  it("step 4: the ellipsoid from the model or by hand (a model not chosen is told), the one pass", () => {
    putForm(sgs());
    let html = render(createElement(StepSearch));
    expect(html).toContain('data-testid="sim-search-model"');
    expect(html).toContain('data-testid="sim-n-max"');
    expect(html).not.toContain("sim-r-0");
    putForm({ ...sgs(), variogramId: null });
    expect(hasModel(simWizardStore.get().form)).toBe(false);
    html = render(createElement(StepSearch));
    expect(html).toContain("Модель вариограммы не выбрана");
    putForm({ ...sgs(), search: { ...sgs().search, from: "manual" } });
    html = render(createElement(StepSearch));
    expect(html).toContain('data-testid="sim-r-0"');
    expect(html).toContain('data-testid="sim-ang-2"');
  });
  it("step 5: the number, the seed, the nodes, the tails — and no tails for the categories", () => {
    putForm(sgs());
    let html = render(createElement(StepReal));
    expect(html).toContain('value="5"');
    expect(html).toContain('value="99"');
    expect(html).toContain('data-testid="sim-ltail"');
    putForm({ ...sgs(), nscore: { ltail: "power", utail: "hyperbolic", ltpar: "2", utpar: "1,5" } });
    html = render(createElement(StepReal));
    expect(html).toContain('data-testid="sim-ltpar"');
    expect(html).toContain('data-testid="sim-utpar"');
    putForm(formOfMeta(meta(SID.cat)));
    html = render(createElement(StepReal));
    expect(html).toContain("обратного преобразования нет");
    expect(html).not.toContain('data-testid="sim-ltail"');
  });
  it("step 6: what will be done (the rows of the summary), the warnings, the button of the run", async () => {
    putForm(formOfMeta(meta(SID.sis)));
    await loadVariogramModels();
    requestDefaults(0);
    await waitFor(() => simWizardStore.get().defaults.phase === "ok");
    const f = simWizardStore.get().form;
    const ctx = { model: "Модель оценки", defaults: simWizardStore.get().defaults.data, vario: simWizardStore.get().vario };
    const rows = summaryRows(f, ctx);
    expect(rows.map((r) => r.label)).toEqual(["Вид", "Блочная модель", "Пробы", "Пороги", "Модели индикаторов", "Поиск", "Реализации"]);
    expect(rows.find((r) => r.label === "Пороги")?.value).toBe(THR);
    expect(rows.find((r) => r.label === "Модели индикаторов")?.value).toBe("AU · индикатор 0.7; AU · индикатор 1; AU · индикатор 1.5");
    expect(rows.find((r) => r.label === "Реализации")?.value).toBe("4, зерно 99, ранее смоделированных узлов 6");
    expect(wizardWarnings(f, ctx)).toEqual([]);
    expect(wizardWarnings({ ...f, label: "нет такого" }, ctx).join(" ")).toContain("не нашлось значения");
    expect(wizardWarnings({ ...formOfMeta(meta(SID.cat)), topcut: { mode: "value", field: "", value: "3" } }, ctx).join(" ")).toContain("урезка");
    expect(wizardWarnings({ ...formOfMeta(meta(SID.co)) }, ctx).join(" ")).toContain("10 пар");
    const html = render(createElement(StepRun));
    expect(html).toContain('data-testid="sim-run-go"');
    expect(html).toContain("Выполнить моделирование");
  });
  it("the window: the six steps on the left, the buttons of the footer, the conflict with «Перечитать» and «Сохранить как новое»", () => {
    putForm(sgs(), { editing: { id: SID.sgs, version: 2 } });
    let html = render(createElement(SimWizard, { winId: "w" }));
    for (let i = 1; i <= 6; i++) expect(html).toContain(`data-testid="sim-step-${i}-tab"`);
    expect(html).toContain("Правка строки №8");
    expect(html).not.toContain("sim-conflict");
    simWizardStore.set({ issue: { param: null, code: "version_conflict", message: "Параметры моделирования изменены в другом окне.", hint: "Перечитайте.", reread: true } });
    html = render(createElement(SimWizard, { winId: "w" }));
    expect(html).toContain('data-testid="sim-reread"');
    expect(html).toContain('data-testid="sim-save-new"');
  });
});

describe("the run", () => {
  it("a stored row runs by its number; the job is followed to its end; the result and the journal are kept for the wizard", async () => {
    const r = await app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    expect(requests(core, "POST /geostat/simulate/run")[0].body).toEqual({ simulation_id: SID.sgs });
    const res = readRun(S.run_result.body)!;
    expect(r).toEqual({ ok: true, result: runSummary(S.simulation.body.name, res) });
    const run = runStore.get();
    expect(run).toMatchObject({ phase: "done", simId: S.run_result.body.simulation_id });
    expect(flat(run.summary ?? "")).toBe(flat(`Моделирование «${S.simulation.body.name}» выполнено: 200 узлов × 5 реализаций за 8,86 с.`));
    const lines = journalLines(run.result!);
    expect(lines[0]).toBe("Узлы: вырожденных систем и узлов без проб нет.");
    expect(flat(lines[1])).toBe("Условных данных в узле: наименьшее 24, среднее 24, наибольшее 24.");
    expect(lines).toContain(S.run_result.body.log[0]);
  });
  it("the journal of a run with trouble: singular systems, nodes with no datum; co-SGS tells ρ and its pairs", () => {
    const troubled = readRun({ ...S.run_result.body, counts: { degenerate: 3, free: 12, on_sample: 1, cond: { min: 0, mean: 5.5, max: 24 } } })!;
    expect(journalLines(troubled)[0]).toBe("Узлы: вырожденных систем 3, узлов без проб в окрестности 12, узлов на пробах 1.");
    const co = readRun(S.cosgs_run_result.body)!;
    expect(co.counts.gap).toBe(0);
    expect(co.secondary).toMatchObject({ field: SG, rho: null });
    expect(flat(journalLines(co)[2])).toBe(flat(`Вторичная переменная «${SG}»: ρ = -0,2479 (по 78 парам проба–узел).`));
  });
  it("the wizard runs the row it saved: POST /simulations, then the run by the number of the row", async () => {
    putForm(sgs());
    await loadVariogramModels();
    await runWizard(app);
    const order = core.sent.filter((r) => ["POST /geostat/simulations", "POST /geostat/simulate/run"].includes(`${r.method} ${r.path}`)).map((r) => `${r.method} ${r.path}`);
    expect(order).toEqual(["POST /geostat/simulations", "POST /geostat/simulate/run"]);
    expect(requests(core, "POST /geostat/simulate/run")[0].body).toEqual({ simulation_id: S.simulation_post.body.id });
    // the form is not changed since the save: the second run does not save again
    await runWizard(app);
    expect(requests(core, "POST /geostat/simulations")).toHaveLength(1);
    expect(requests(core, "POST /geostat/simulate/run")).toHaveLength(2);
  });
  it("a run cancelled in «Задания» is a line, not an error: nothing is written", async () => {
    core.job = { status: "cancelled", result: null, error: null };
    const r = await app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    expect(r).toEqual({ ok: true, result: `Моделирование «${S.simulation.body.name}» отменено: реализации не записаны` });
    expect(runStore.get()).toMatchObject({ phase: "cancelled", note: expect.stringContaining("отменено") });
  });
  it("a failed job tells its words with the hint; a refusal before the job does too", async () => {
    core.job = { status: "failed", result: null, error: { code: "no_samples", message: "В домене нет проб.", hint: "Выберите другой домен." } };
    const r = await app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    expect(r).toMatchObject({ ok: false });
    expect(runStore.get()).toMatchObject({ phase: "failed", error: { code: "no_samples", message: "В домене нет проб.", hint: "Выберите другой домен." } });
    core.force("POST /geostat/simulate/run", { status: 409, body: S.run_version_conflict.body });
    await app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    expect(runStore.get()).toMatchObject({ phase: "failed", error: { code: "version_conflict" } });
  });
  it("one run at a time; a row that is not in the project is refused before the core is asked", async () => {
    runStore.set({ phase: "running" });
    const busy = await app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    expect(busy).toMatchObject({ ok: false, result: expect.stringContaining("уже выполняется") });
    runStore.set({ phase: "idle" });
    const none = await app.registry.execute("simulate.run", { id: SID.none }, "user");
    expect(none).toMatchObject({ ok: false, result: expect.stringContaining(`№${SID.none}`) });
    expect(requests(core, "POST /geostat/simulate/run")).toHaveLength(0);
  });
  it("the variogram models of the project are read with their back transform (a model in Y with it is a model of the field)", async () => {
    await loadVariogramModels();
    const v = simWizardStore.get().vario;
    expect(v.rows.map((r) => r.id)).toEqual(VARIO_ROWS.map((r) => r.id));
    expect(v.backs[VID.back]).toBe(true);
    expect(v.backs[VID.vy]).toBe(false);
  });
});
