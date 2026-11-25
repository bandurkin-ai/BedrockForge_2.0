// The fixes of the review of the interface of the simulation (Э18б, 18.36в): the thresholds without `stats`, a read of the details that was overtaken,
// the choice of the simulation under a running post-processing, a project that changes under a job, the parameters of the kinds without the set,
// the form that is not replaced silently, the list of the models of variogram that follows the core, `format(t, "g")` of the core, the read-only
// button of the post-processing, the counts the core did not send.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { onVariogramEvent, loadVariogramModels as loadEstimationModels, wizardStore as estimationWizard } from "../../src/modules/estimation/wizardState";
import { projectStore } from "../../src/modules/projects/state";
import { readRun, readSummary } from "../../src/modules/simulation/api";
import { renderSimulationWindow } from "../../src/modules/simulation/bind";
import { choosePost, formatG, postBodyOf, postStore, sourceOf, thresholdName } from "../../src/modules/simulation/postState";
import { journalLines } from "../../src/modules/simulation/runState";
import { loadDetails, loadRows, simulationStore } from "../../src/modules/simulation/state";
import { StepVario } from "../../src/modules/simulation/StepVario";
import { chooseSummary, summaryFacts, SUMMARY_WINDOW } from "../../src/modules/simulation/summaryState";
import { onSimVariogramEvent, loadVariogramModels, setForm, simWizardStore } from "../../src/modules/simulation/wizardState";
import { runStore } from "../../src/modules/simulation/runState";
import { INFOS, installSimCore, MODEL_VERSION, newApp, requests, S, SID, teardown, waitFor, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(teardown);

const POSTS = (id: number) => requests(core, `POST /geostat/simulations/${id}/post`);
const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const answerDialog = async (ok: boolean) => {
  await waitFor(() => dialogStore.get().queue.length > 0);
  const d = dialogStore.get().queue[0];
  if (d.kind === "confirm") d.resolve(ok);
  closeDialog(d.id);
};

describe("1. the thresholds without `stats` give the probability", () => {
  const sgs = () => sourceOf(INFOS[SID.sgs].meta);
  it("the body: the defaults of the kind and the probability, the thresholds as numbers", () => {
    const b = postBodyOf({ prefix: "", stats: null, thresholds: "1; 2,5", replace: false }, sgs(), MODEL_VERSION);
    expect(b).toEqual({ expected_version: MODEL_VERSION, stats: ["mean", "std", "p10", "p50", "p90", "prob"], thresholds: [1, 2.5] });
  });
  it("a threshold that is not a number is told under its field, not dropped", () => {
    expect(postBodyOf({ prefix: "", stats: null, thresholds: "1; x", replace: false }, sgs(), MODEL_VERSION)).toMatchObject({ code: expect.any(String), param: "thresholds.1" });
  });
  it("the categories have no thresholds, the kind's defaults stay", () => {
    expect(postBodyOf({ prefix: "", stats: null, thresholds: "1", replace: false }, sourceOf(INFOS[SID.cat].meta), MODEL_VERSION)).toMatchObject({ param: "thresholds" });
  });
  it("`simulate.post {id, thresholds}` sends them and writes PROB_GT", async () => {
    const r = await app.registry.execute("simulate.post", { id: SID.sgs, thresholds: "1" }, "user");
    expect(r.ok).toBe(true);
    expect(POSTS(SID.sgs)[0].body).toMatchObject({ thresholds: [1], stats: ["mean", "std", "p10", "p50", "p90", "prob"] });
  });
});

describe("2. a read of the details that was overtaken does not make a categorical row an SGS one", () => {
  /** The first `hold` reads of the row are kept until `release`d (the answers come in the order the test wants). */
  function hold(id: number, count: number): (n: number) => void {
    const inner = globalThis.fetch;
    const gates: (() => void)[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET" && String(url).endsWith(`/geostat/simulations/${id}`) && gates.length < count) await new Promise<void>((r) => gates.push(r));
      return inner(url, init);
    });
    return (n) => gates[n]();
  }
  it("a newer read has put the details in the store: the post-processing takes them", async () => {
    simulationStore.set({ details: {} });
    const release = hold(SID.cat, 1);
    const run = app.registry.execute("simulate.post", { id: SID.cat, stats: "prob;mode", run: true }, "user");
    await new Promise((r) => setTimeout(r, 30)); // the first read is waiting
    await loadDetails(SID.cat); // the newer read overtakes it
    release(0);
    const r = await run;
    expect(r.ok).toBe(true);
    expect(POSTS(SID.cat)).toHaveLength(1);
    expect(POSTS(SID.cat)[0].body).toMatchObject({ stats: ["prob", "mode"] });
  });
  it("nothing in the store: «повторите», not the view of an SGS row", async () => {
    simulationStore.set({ details: {} });
    const release = hold(SID.cat, 2);
    const run = app.registry.execute("simulate.post", { id: SID.cat, stats: "prob;mode", run: true }, "user");
    await new Promise((r) => setTimeout(r, 30));
    const second = loadDetails(SID.cat); // the second read is kept too
    await new Promise((r) => setTimeout(r, 30));
    release(0); // the first one comes back overtaken: null
    const r = await run;
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining("Повторите") });
    expect(POSTS(SID.cat)).toHaveLength(0);
    expect(postStore.get().phase).not.toBe("running");
    release(1);
    await second;
  });
});

describe("3. a running post-processing keeps its simulation", () => {
  it("the choice of another simulation does nothing while it runs; another run is refused", async () => {
    core.postJob = { status: "running", result: null, error: null };
    const run = app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    await waitFor(() => postStore.get().phase === "running" && postStore.get().job !== null);
    choosePost(SID.sis);
    expect(postStore.get()).toMatchObject({ simId: SID.sgs, phase: "running" });
    const other = await app.registry.execute("simulate.post", { id: SID.sis, run: true }, "user");
    expect(other).toMatchObject({ ok: false, result: expect.stringContaining("уже выполняется") });
    expect(POSTS(SID.sis)).toHaveLength(0);
    await app.registry.execute("simulate.post", { id: SID.sis }, "user"); // the window itself does not move under the job
    expect(postStore.get()).toMatchObject({ simId: SID.sgs, phase: "running" });
    // the end of the job (the project changes) lets the command go
    projectStore.set({ current: { uid: "p-end", name: "Другой" } as never, version: 9 });
    await run;
  });
  it("the selector of the window is disabled while it runs", async () => {
    core.postJob = { status: "running", result: null, error: null };
    const run = app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    await waitFor(() => postStore.get().phase === "running");
    await app.registry.execute("simulate.post", {}, "user");
    const w = windowStore.get().windows.find((x) => x.kind === "simulation-post")!;
    const html = render(renderSimulationWindow(w) as ReturnType<typeof createElement>);
    expect(html).toMatch(/data-testid="post-select"[^>]*disabled/);
    projectStore.set({ current: { uid: "p-end", name: "Другой" } as never, version: 9 });
    await run;
  });
});

describe("4. a project that changes under a job: nothing is written into the new one", () => {
  it("the run: no failure in the store of the new project", async () => {
    core.job = { status: "running", result: null, error: null };
    const run = app.registry.execute("simulate.run", { id: SID.sgs }, "user");
    await waitFor(() => runStore.get().phase === "running" && runStore.get().job !== null);
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    const r = await run;
    expect(r).toMatchObject({ ok: true, result: expect.stringContaining("Проект изменился") });
    expect(runStore.get()).toMatchObject({ phase: "idle", error: null });
  });
  it("the post-processing: no failure either", async () => {
    core.postJob = { status: "running", result: null, error: null };
    const run = app.registry.execute("simulate.post", { id: SID.sgs, run: true }, "user");
    await waitFor(() => postStore.get().phase === "running" && postStore.get().job !== null);
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    const r = await run;
    expect(r).toMatchObject({ ok: true, result: expect.stringContaining("Проект изменился") });
    expect(postStore.get()).toMatchObject({ phase: "idle", error: null, simId: null });
  });
});

describe("5. the kinds without the set keep the parameters of the call", () => {
  it("simulate.sgs {n_real, seed, name} opens the wizard with them", async () => {
    const r = await app.registry.execute("simulate.sgs", { n_real: 7, seed: 42, name: "Моя модель" }, "user");
    expect(r.ok).toBe(true);
    expect(simWizardStore.get().form).toMatchObject({ kind: "sgs", nReal: "7", seed: "42", name: "Моя модель" });
    expect(simWizardStore.get().dirty).toBe(true);
  });
  it("simulate.cosgs {rho}: ρ is given, not computed", async () => {
    await app.registry.execute("simulate.cosgs", { rho: 0.5 }, "user");
    expect(simWizardStore.get().form).toMatchObject({ kind: "cosgs", rhoMode: "value", rho: "0.5" });
  });
  it("without parameters the wizard is clean (the close guard does not ask)", async () => {
    await app.registry.execute("simulate.sis", {}, "user");
    expect(simWizardStore.get().dirty).toBe(false);
  });
});

describe("6. the form is not replaced silently", () => {
  it("`simulate.open {id}` over a changed form asks; «Нет» keeps the form", async () => {
    await app.registry.execute("simulate.open", {}, "user");
    setForm({ name: "правка" });
    const run = app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    await answerDialog(false);
    expect(await run).toMatchObject({ ok: true, result: "Отменено" });
    expect(simWizardStore.get()).toMatchObject({ editing: null, form: { name: "правка" } });
  });
  it("«Да» opens the row", async () => {
    await app.registry.execute("simulate.open", {}, "user");
    setForm({ name: "правка" });
    const run = app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    await answerDialog(true);
    expect((await run).ok).toBe(true);
    expect(simWizardStore.get().editing).toMatchObject({ id: SID.sgs });
  });
  it("a clean form is replaced without a question", async () => {
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    await app.registry.execute("simulate.open", { id: SID.sis }, "user");
    expect(dialogStore.get().queue).toHaveLength(0);
    expect(simWizardStore.get().editing).toMatchObject({ id: SID.sis });
  });
  it("another kind over the edit of a row: the row is not the row of the form any more", async () => {
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    expect(simWizardStore.get().editing).toMatchObject({ id: SID.sgs });
    await app.registry.execute("simulate.sis", {}, "user");
    expect(simWizardStore.get()).toMatchObject({ editing: null, saved: null, form: { kind: "sis" } });
  });
});

describe("7. the list of the models of variogram follows the core", () => {
  const GET = "GET /geostat/variography/models";
  it("an event of a model reads the list again; of something else — not; before the first read — not", async () => {
    expect(simWizardStore.get().vario.loaded).toBe(false);
    const before = requests(core, GET).length;
    onSimVariogramEvent("geostat.changed", JSON.stringify({ kind: "variogram_model", id: 5 }));
    await new Promise((r) => setTimeout(r, 20));
    expect(requests(core, GET)).toHaveLength(before);
    await loadVariogramModels();
    const n = requests(core, GET).length;
    onSimVariogramEvent("geostat.changed", JSON.stringify({ kind: "trend", id: 5 }));
    onSimVariogramEvent("objects.changed", JSON.stringify({ kind: "variogram_model" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(requests(core, GET)).toHaveLength(n);
    onSimVariogramEvent("geostat.changed", JSON.stringify({ kind: "variogram_model", id: 5 }));
    await waitFor(() => requests(core, GET).length > n);
    expect(simWizardStore.get().vario.loaded).toBe(true);
  });
  it("the same in the wizard of the estimation", async () => {
    await loadEstimationModels();
    const n = requests(core, GET).length;
    onVariogramEvent("geostat.changed", JSON.stringify({ kind: "variogram_model", id: 5, deleted: true }));
    await waitFor(() => requests(core, GET).length > n);
    expect(estimationWizard.get().vario.loaded).toBe(true);
  });
  it("the step has the button «Обновить список»", () => {
    const html = render(createElement(StepVario));
    expect(html).toContain('data-testid="sim-vario-refresh"');
    expect(html).toContain("Обновить список");
  });
});

describe("8. `format(t, \"g\")` of the core", () => {
  it("the same as Python's: six digits, the exponent below 1e-4 and from 1e6", () => {
    const python: [number, string][] = [
      [1e-5, "1e-05"],
      [1e6, "1e+06"],
      [0.5, "0.5"],
      [-0.5, "-0.5"],
      [1234567, "1.23457e+06"],
      [100000, "100000"],
      [0.0001, "0.0001"],
      [123456.7, "123457"],
      [999999.5, "1e+06"],
      [0.00012345678, "0.000123457"],
      [1.5e-5, "1.5e-05"],
      [-1e6, "-1e+06"],
      [12345.6789, "12345.7"],
      [0, "0"],
      [1, "1"],
      [2.5, "2.5"],
    ];
    for (const [t, want] of python) expect(formatG(t)).toBe(want);
  });
  it("the name of a field: no dot", () => {
    expect(thresholdName(1e-5)).toBe("1e-05");
    expect(thresholdName(1e6)).toBe("1e+06");
    expect(thresholdName(0.5)).toBe("0_5");
    expect(thresholdName(-0.5)).toBe("-0_5");
    expect(thresholdName(1.23456789)).toBe("1_23457");
  });
});

describe("9. «Постобработка…» of the summary in a read-only project", () => {
  it("is disabled with the reason, like «Выполнить заново»", async () => {
    chooseSummary(SID.sgs);
    const w = { id: "w", kind: SUMMARY_WINDOW, title: "", x: 0, y: 0, w: 1, h: 1, z: 1 } as never;
    const open = render(renderSimulationWindow(w) as ReturnType<typeof createElement>);
    expect(open).not.toMatch(/disabled=""[^>]*data-testid="sum-post"/);
    app.store.set({ projectReadOnly: true });
    const ro = render(renderSimulationWindow(w) as ReturnType<typeof createElement>);
    expect(ro).toMatch(/<button[^>]*disabled=""[^>]*data-testid="sum-post"/);
    expect(ro).toContain("Проект открыт только для чтения: постобработка");
  });
});

describe("10. the counts the core did not send are unknown, not 0", () => {
  it("the run and the summary: null, and a dash in the facts", () => {
    const run = readRun({ ...S.run_result.body, counts: {} });
    expect(run?.counts).toMatchObject({ degenerate: null, free: null, on_sample: null });
    expect(journalLines(run!)[0]).toContain("не сообщило");
    const sum = readSummary({ ...S.summary.body, counts: {} });
    expect(sum.counts).toMatchObject({ degenerate: null, free: null, on_sample: null });
    const facts = Object.fromEntries(summaryFacts(sum));
    expect(facts["Вырожденных систем"]).toBe("—");
    expect(facts["Узлов на пробах"]).toBe("—");
  });
  it("a count of 0 is a count", () => {
    const sum = readSummary({ ...S.summary.body, counts: { degenerate: 0, free: 0, on_sample: 3 } });
    const facts = Object.fromEntries(summaryFacts(sum));
    expect(facts["Вырожденных систем"]).toBe("0");
    expect(facts["Узлов на пробах"]).toBe("3");
  });
});
