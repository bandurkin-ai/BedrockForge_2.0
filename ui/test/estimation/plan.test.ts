// The window «План оценки» (Э18, 18.22) on the answers of the contract (`core/tests/data/estimate/contract_answers.json`: `plan`,
// `estimations`, `run_result`; until the routes of 18.13 give real answers): the readers, the words (state, journal), the working
// copy of the plan (order, add, remove), the save (`POST` / `PUT /estimation-plans`, 409), the run from the window and its journal,
// the commands `estimate.plan`, `estimate.plan_delete`, `estimate.run` without arguments, the window drawn, the section of the tree.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { readPlan, readRun } from "../../src/modules/estimation/api";
import { renderEstimationWindow } from "../../src/modules/estimation/bind";
import { EstimationTree, planMenu, plansByName, planTitleText, rowMenu, rowsByName, rowTitleText } from "../../src/modules/estimation/EstimationTreeSection";
import { PlanWindow } from "../../src/modules/estimation/PlanWindow";
import { addAll, addItem, choosePlan, deletePlan, moveItem, pick, pickAll, PLAN_MAX, PLAN_WINDOW, planStore, pickModel, removeItem, rereadPlan, runArgsPicked, runArgsPlan, savePlan, setPlanName, syncPlan } from "../../src/modules/estimation/planState";
import { domainText, fieldsText, journalLines, lastRunText, passText, prefixWarnings, rowLine, runReason, samplesText, sampleField, stateText, statusText } from "../../src/modules/estimation/planView";
import { runBegin, runJob, runStore, singularText } from "../../src/modules/estimation/runState";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { jobsStore } from "../../src/modules/jobs/state";
import { A, installCore, newApp, PLANS, teardown, type Core } from "./harness";

let app: App;
let core: Core;

/** The second row of the plan of the contract: the same field and method, another domain, out of date. */
const ROW42 = { ...A.estimations.body.estimations[0], id: 42, name: "AU · OK · перех", label: "перех", stale: true, stale_reason: "изменена таблица «Композиты»", last_run: null };
/** A row of the same model with its own prefix, not in the plan. */
const ROW43 = { ...A.estimations.body.estimations[0], id: 43, name: "AU · IDW · перв", method: "idw", prefix: "AU_IDW_", label: "перв", plans: [], last_run: null };
/** A row of another model: it never gets into a plan of the model 7. */
const ROW44 = { ...ROW43, id: 44, name: "Другая модель", model_id: 8, prefix: "Q_" };

beforeEach(async () => {
  core = installCore();
  core.estimations = { estimations: [A.estimations.body.estimations[0], ROW42, ROW43, ROW44] };
  app = newApp();
  runStore.set({ phase: "idle", job: null, modelId: null, modelName: "", planId: null, ids: [], result: null, summary: null, error: null, note: null });
  planStore.set({ modelId: null, planId: null, name: "", version: null, items: [], picked: [], dirty: false, saving: false, issue: null });
  await loadRows();
});
afterEach(teardown);

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const plan51 = () => estimationStore.get().plans.find((p) => p.id === 51)!;
const render = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(PlanWindow, { winId: "w1" }))));
/** The opening tag of the element with a `data-testid` (the attributes of a button come in the order of the markup). */
const tag = (html: string, id: string): string => html.match(new RegExp(`<[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? "";
const off = (html: string, id: string): boolean => tag(html, id).includes('disabled=""');
const tree = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(EstimationTree))));

describe("the readers on the samples of the contract", () => {
  it("the plan: the rows in the order of the run with their states", () => {
    const p = readPlan(A.plan.body);
    expect(p).toMatchObject({ id: 51, name: "План · Модель", model_id: 7, version: 1 });
    expect(p.items.map((i) => [i.id, i.method, i.prefix, i.label, i.stale])).toEqual([[41, "ok", "AU_OK_", "окисл", false], [42, "ok", "AU_OK_", "перех", true]]);
    expect(readPlan(null).items).toEqual([]);
  });
  it("the result of the run: the passes, the singular systems, the samples, the rows that share the fields", () => {
    const r = readRun(A.run_result.body)!;
    expect(r.rows[0].counts).toMatchObject({ targets: 1200, by_status: [1100, 60, 35, 5], by_pass: [990, 90, 20], singular: [3, 1, 5] });
    expect(r.rows[0].counts.samples).toMatchObject({ rows: 1196, used: 880, merged: 2, missing: 4, outside_domain: 310 });
    expect(r.merged).toEqual([{ prefix: "AU_OK_", estimation_ids: [41, 42] }]);
    expect(readRun({ model_id: 7, fields: [], rows: [{ estimation_id: 1, counts: {} }] })!.rows[0].counts).toEqual({ targets: 0, by_status: [], by_pass: [], singular: [], samples: null });
  });
});

describe("the words", () => {
  const rows = estimationStore.get;
  it("the state of a row: out of date with its reason, the source gone, up to date", () => {
    const [r41, r42] = [rows().rows[0], rows().rows[1]];
    expect(stateText(r41)).toBeNull();
    expect(stateText(r42)).toBe("устарел: изменена таблица «Композиты»");
    expect(stateText({ stale: true, stale_reason: null, source_lost: false })).toBe("устарел");
    expect(stateText({ stale: true, stale_reason: "x", source_lost: true })).toBe("источник удалён");
  });
  it("domain × fields × method in one line; a row without a domain is the whole model", () => {
    const r = rows().rows[0];
    expect([domainText(r), fieldsText(r), rowLine(r)]).toEqual(["окисл", "AU_OK_*", "OK · окисл · AU_OK_*"]);
    expect(domainText({ label: null })).toBe("вся модель");
  });
  it("the last run: the date and the blocks by the status; a gap in the counts is not told", () => {
    const t = flat(lastRunText(rows().rows[0])!);
    expect(t).toContain("оценён 1 100, вне домена 60, мало проб 35, вырожденная система 5");
    expect(lastRunText(rows().rows[1])).toBeNull();
    expect(flat(lastRunText({ last_run: { at: "", by_status: [0, 0, 0, 0] } })!)).toBe("—");
  });
  it("rows with one prefix: another domain and the same method are fine, the same domain or another method are told", () => {
    const [a, b, c] = rows().rows;
    expect(prefixWarnings([a, b])).toEqual([]);
    expect(prefixWarnings([a, c])).toEqual([]);
    expect(prefixWarnings([a, { ...b, label: "окисл" }])[0]).toContain("домены у них совпадают");
    expect(prefixWarnings([a, { ...b, method: "idw" as const }])[0]).toContain("методы у них разные (OK, IDW)");
  });
  it("why a plan cannot be run now", () => {
    const [a, b] = rows().rows;
    const ok = { readOnly: false, busy: false, modelFound: true };
    expect(runReason([a, b], 2, ok)).toBeNull();
    expect(runReason([a, b], 2, { ...ok, readOnly: true })).toBe("Проект открыт только для чтения");
    expect(runReason([a, b], 2, { ...ok, busy: true })).toBe("Оценка уже выполняется");
    expect(runReason([a, b], 2, { ...ok, modelFound: false })).toBe("Выберите блочную модель");
    expect(runReason([], 0, ok)).toBe("В плане нет строк");
    expect(runReason([a], 2, ok)).toBe("Часть строк плана удалена: сохраните план");
    expect(runReason([{ ...a, source_lost: true }], 1, ok)).toBe("Строка «AU · OK · окисл»: источник удалён");
  });
  it("the journal: the log of the core, the singular systems by the passes of each row, the merged rows", () => {
    const r = readRun(A.run_result.body)!;
    const lines = journalLines(r, rows().rows);
    expect(lines[0]).toBe("Проход 1: 3 блока — вырожденная система, переход к проходу 2.");
    expect(lines).toContain("«AU · OK · окисл»: вырожденная система — проход 1 — 3, проход 2 — 1, проход 3 — 5.");
    expect(lines.at(-1)).toBe("Строки «AU · OK · окисл», «AU · OK · перех» пишут в поля «AU_OK_*»: значения доменов объединены.");
    expect(singularText([0, 0])).toBe("");
    expect(statusText([1100, 60, 35, 5])).toBe("оценён 1 100 · вне домена 60 · мало проб 35 · вырожденная система 5");
    expect(passText([990, 0, 20])).toBe("проход 1 — 990, проход 3 — 20");
    expect(flat(samplesText(r.rows[0].counts))).toBe("Проб: 880 из 1 196; усреднено в одну точку: 2; пропуск значения: 4; вне домена: 310");
    expect(samplesText({ targets: 0, by_status: [], by_pass: [], singular: [], samples: null })).toBe("");
  });
});

describe("the plan as a working copy", () => {
  it("a stored plan: the rows in its order, nothing changed; the details of its rows are read for the field of the samples", async () => {
    choosePlan(51);
    expect(planStore.get()).toMatchObject({ modelId: 7, planId: 51, name: "План · Модель", version: 1, items: [41, 42], dirty: false });
    await waitFor(() => !!estimationStore.get().details[41]);
    expect(sampleField(estimationStore.get().details[41])).toBe("AU");
    expect(sampleField(undefined)).toBeNull();
  });
  it("up and down swap neighbours; at the edge nothing moves; the copy is changed, the plan is not", () => {
    choosePlan(51);
    moveItem(41, -1);
    expect(planStore.get().items).toEqual([41, 42]);
    expect(planStore.get().dirty).toBe(false);
    moveItem(41, 1);
    expect(planStore.get()).toMatchObject({ items: [42, 41], dirty: true });
    moveItem(41, 1);
    expect(planStore.get().items).toEqual([42, 41]);
    expect(plan51().items).toEqual([41, 42]);
  });
  it("add: only the rows of the model, once; remove; the ticks follow the rows", () => {
    choosePlan(51);
    addItem(43);
    addItem(43);
    addItem(44); // the row of another model
    addItem(99); // a row that is not there
    expect(planStore.get().items).toEqual([41, 42, 43]);
    pick(43, true);
    pick(41, true);
    removeItem(43);
    expect(planStore.get().items).toEqual([41, 42]);
    expect(planStore.get().picked).toEqual([41]);
  });
  it("«Добавить все»: the rest of the rows of the model in the order of the list; the plan is 100 rows at most", () => {
    choosePlan(51);
    addAll();
    expect(planStore.get().items).toEqual([41, 42, 43]);
    expect(PLAN_MAX).toBe(100);
  });
  it("a new plan has no rows; the model chosen in the tree, else the only one with rows, is the default", () => {
    planStore.set({ ...planStore.get(), modelId: 7 });
    expect(pickModel(7)).toBe(7);
    expect(pickModel(8)).toBe(7);
    expect(pickModel(null)).toBe(7);
  });
  it("what is run: a stored plan as it is (plan_id), a changed one by its rows; the ticked rows in the order of the plan", () => {
    choosePlan(51);
    expect(runArgsPlan()).toEqual({ plan_id: 51 });
    moveItem(41, 1);
    expect(runArgsPlan()).toEqual({ estimation_ids: "42; 41" });
    expect(runArgsPicked()).toBeNull();
    pick(41, true);
    pick(42, true);
    expect(runArgsPicked()).toEqual({ estimation_ids: "42; 41" });
    pickAll(false);
    expect(runArgsPicked()).toBeNull();
    removeItem(41);
    removeItem(42);
    expect(runArgsPlan()).toBeNull();
  });
});

describe("the save", () => {
  it("a stored plan: PUT over the version read, the order and the name; the copy is clean after it", async () => {
    choosePlan(51);
    moveItem(42, -1);
    setPlanName("Порядок 2");
    core.plans = { plans: [{ ...PLANS.plans[0], name: "Порядок 2", items: [42, 41], version: 2 }] }; // what the core stores
    const saved = await savePlan();
    expect(saved).toMatchObject({ id: 51, version: 2 });
    const put = requests("PUT /geostat/estimation-plans/51")[0];
    expect(put.body).toEqual({ expected_version: 1, name: "Порядок 2", items: [42, 41] });
    expect(planStore.get()).toMatchObject({ version: 2, dirty: false, saving: false, issue: null });
  });
  it("a new plan: POST with the model and the rows; the name stays out of the body while it is empty (the core names it)", async () => {
    planStore.set({ ...planStore.get(), modelId: 7 });
    addItem(43);
    core.plans = { plans: [...PLANS.plans, { id: 52, name: "План · Модель", model_id: 7, items: [43], version: 1 }] };
    await savePlan();
    expect(requests("POST /geostat/estimation-plans")[0].body).toEqual({ model_id: 7, items: [43] });
    expect(planStore.get()).toMatchObject({ planId: 52, version: 1, dirty: false });
  });
  it("«Сохранить как новый» after a conflict: POST of the copy under a name that is not the old one", async () => {
    choosePlan(51);
    moveItem(41, 1);
    core.plans = { plans: [...PLANS.plans, { id: 52, name: "План · Модель", model_id: 7, items: [42, 41], version: 1 }] };
    await savePlan({ asNew: true });
    expect(requests("POST /geostat/estimation-plans")[0].body).toEqual({ name: "План · Модель (копия)", model_id: 7, items: [42, 41] });
    expect(requests("PUT /geostat/estimation-plans/51")).toHaveLength(0);
  });
  it("another version stored: the refusal stands with «Перечитать»; reading takes the stored plan", async () => {
    choosePlan(51);
    moveItem(41, 1);
    core.force("PUT /geostat/estimation-plans/51", { status: 409, body: { code: "version_conflict", version: 2, message: "План изменён в другом окне.", hint: "Перечитайте план." } });
    await expect(savePlan()).rejects.toBeTruthy();
    expect(planStore.get().issue).toMatchObject({ code: "version_conflict", reread: true });
    expect(planStore.get().items).toEqual([42, 41]); // the work is not lost
    expect(render()).toContain('data-testid="plan-conflict"');
    await rereadPlan();
    expect(planStore.get()).toMatchObject({ items: [41, 42], dirty: false, issue: null });
    expect(requests("GET /geostat/estimation-plans/51")).toHaveLength(1);
  });
  it("a refusal of the core under the bar with its words; no model — told before any request", async () => {
    choosePlan(51);
    core.force("PUT /geostat/estimation-plans/51", { status: 422, body: { code: "bad_param", param: "items.1", message: "Строки с одним префиксом пишут в одни поля.", hint: "Задайте разные домены." } });
    moveItem(41, 1);
    await expect(savePlan()).rejects.toBeTruthy();
    expect(planStore.get().issue).toMatchObject({ code: "bad_param", param: "items.1", reread: false });
    expect(render()).toContain("Строки с одним префиксом пишут в одни поля. Задайте разные домены.");
    planStore.set({ modelId: null, planId: null, issue: null });
    await expect(savePlan()).rejects.toThrow("Блочная модель не выбрана");
  });
  it("the deletion: the rows stay, the window goes to a new plan of the model", async () => {
    choosePlan(51);
    core.plans = { plans: [] };
    const r = await deletePlan(51);
    expect(r.name).toBe("План · Модель");
    expect(requests("DELETE /geostat/estimation-plans/51")).toHaveLength(1);
    expect(planStore.get()).toMatchObject({ modelId: 7, planId: null, items: [] });
    expect(estimationStore.get().rows).toHaveLength(4);
  });
});

describe("the window follows the project", () => {
  it("a plan deleted by another window: the rows stay in the copy as a new plan, with the reason", async () => {
    choosePlan(51);
    moveItem(41, 1);
    core.plans = { plans: [] };
    await loadRows();
    expect(planStore.get()).toMatchObject({ planId: null, version: null, dirty: true, items: [42, 41] });
    expect(planStore.get().issue).toMatchObject({ code: "plan_not_found" });
  });
  it("a plan changed elsewhere and not touched here is taken as stored; a changed copy keeps its rows", async () => {
    choosePlan(51);
    core.plans = { plans: [{ ...PLANS.plans[0], items: [42], version: 2 }] };
    await loadRows();
    expect(planStore.get()).toMatchObject({ items: [42], version: 2, dirty: false });
    addItem(43);
    core.plans = { plans: [{ ...PLANS.plans[0], items: [42, 41], version: 3 }] };
    await loadRows();
    expect(planStore.get()).toMatchObject({ items: [42, 43], version: 2, dirty: true });
  });
  it("a row deleted elsewhere leaves a changed copy", async () => {
    choosePlan(51);
    addItem(43);
    core.estimations = { estimations: [A.estimations.body.estimations[0], ROW42] };
    await loadRows();
    expect(planStore.get().items).toEqual([41, 42]);
    syncPlan();
  });
  it("another project: the window is clean", async () => {
    choosePlan(51);
    const { projectStore } = await import("../../src/modules/projects/state");
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(planStore.get()).toMatchObject({ modelId: null, planId: null, items: [] });
    expect(runStore.get().phase).toBe("idle");
  });
});

describe("the commands", () => {
  it("`estimate.plan {plan_id}` opens the window with the plan; again — the same window", async () => {
    const r = await app.registry.execute("estimate.plan", { plan_id: 51 }, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «План оценки»" });
    const w = windowStore.get().windows.filter((x) => x.kind === PLAN_WINDOW);
    expect(w).toHaveLength(1);
    expect(planStore.get()).toMatchObject({ planId: 51, items: [41, 42] });
    await app.registry.execute("estimate.plan", { plan_id: 51 }, "user");
    expect(windowStore.get().windows.filter((x) => x.kind === PLAN_WINDOW)).toHaveLength(1);
    expect(renderEstimationWindow(w[0])).not.toBeNull();
  });
  it("`estimate.plan {model_id}` and without arguments (the only model with rows); a plan or a model that is not there is told", async () => {
    expect((await app.registry.execute("estimate.plan", {}, "user")).ok).toBe(true);
    expect(planStore.get()).toMatchObject({ modelId: 7, planId: 51 });
    expect((await app.registry.execute("estimate.plan", { model_id: 99 }, "user")).result).toContain("Блочная модель №99 не найдена");
    expect((await app.registry.execute("estimate.plan", { plan_id: 9 }, "user")).result).toContain("План оценки №9 не найден");
  });
  it("a plan that is changed is not replaced without the person's word", async () => {
    await app.registry.execute("estimate.plan", { plan_id: 51 }, "user");
    core.plans = { plans: [...(core.plans as { plans: unknown[] }).plans, { id: 53, name: "Другой", model_id: 7, items: [43], version: 1 }] };
    await loadRows();
    moveItem(41, 1);
    const run = app.registry.execute("estimate.plan", { plan_id: 53 }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.message).toContain("не сохранён");
    if (d.kind === "confirm") d.resolve(false);
    expect((await run).result).toBe("Отменено");
    expect(planStore.get()).toMatchObject({ planId: 51, items: [42, 41] });
  });
  it("`estimate.run` without arguments opens the window; with a plan it runs it", async () => {
    expect((await app.registry.execute("estimate.run", {}, "user")).result).toBe("Открыто окно «План оценки»");
    const r = await app.registry.execute("estimate.run", { plan_id: 51 }, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /geostat/estimate/run")[0].body).toMatchObject({ plan_id: 51, replace: false });
  });
  it("`estimate.plan_delete`: the person is asked; «Удалить» sends DELETE; «Отмена» sends nothing; a plan that is not there is told", async () => {
    const no = app.registry.execute("estimate.plan_delete", { id: 51 }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.message).toContain("Удалить план «План · Модель»?");
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    expect((await no).result).toBe("Отменено");
    expect(requests("DELETE /geostat/estimation-plans/51")).toHaveLength(0);
    core.plans = { plans: [] };
    const yes = app.registry.execute("estimate.plan_delete", { id: 51 }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const e = dialogStore.get().queue[0];
    if (e.kind === "confirm") e.resolve(true);
    expect((await yes).result).toBe("План «План · Модель» удалён; строки остались");
    expect(requests("DELETE /geostat/estimation-plans/51")).toHaveLength(1);
    expect((await app.registry.execute("estimate.plan_delete", { id: 77 }, "user")).result).toContain("План оценки №77 не найден");
  });
});

describe("the run from the window and its journal", () => {
  it("the whole plan: the job is followed, the result with the passes stays for the journal", async () => {
    choosePlan(51);
    const r = await app.registry.execute("estimate.run", runArgsPlan()!, "user");
    expect(r.ok).toBe(true);
    const s = runStore.get();
    expect(s).toMatchObject({ phase: "done", modelId: 7, modelName: "Модель", planId: 51, ids: [41, 42] });
    expect(s.job).toMatchObject({ source: "proj" });
    expect(s.result?.rows[0].counts.by_pass).toEqual([990, 90, 20]);
    const html = render();
    expect(html).toContain('data-testid="plan-log"');
    expect(html).toContain("Оценка записана в модель «Модель» (версия 5)");
    expect(html).toContain("Поля: AU_OK_STATUS, AU_OK_EST");
    expect(html).toContain("проход 1 — 990, проход 2 — 90, проход 3 — 20");
    expect(html).toContain("Проход 1: 3 блока — вырожденная система, переход к проходу 2.");
    expect(html).toContain("вырожденная система — проход 1 — 3, проход 2 — 1, проход 3 — 5.");
    expect(html).toContain("значения доменов объединены");
    expect(html).toContain("Проб: 880 из 1 196; усреднено в одну точку: 2");
  });
  it("the ticked rows: `estimation_ids` in the order of the plan, the version of the model goes with them", async () => {
    choosePlan(51);
    pick(42, true);
    const r = await app.registry.execute("estimate.run", runArgsPicked()!, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /geostat/estimate/run")[0].body).toEqual({ estimation_ids: [42], expected_version: 4, replace: false });
    expect(runStore.get().ids).toEqual([42]);
  });
  it("the fields are in the model: the question «Заменить поля»; «Нет» leaves a line in the window, not an error", async () => {
    choosePlan(51);
    core.force("POST /geostat/estimate/run", A.run_field_exists);
    const run = app.registry.execute("estimate.run", runArgsPlan()!, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.title).toBe("Заменить поля");
    if (d.kind === "confirm") d.resolve(false);
    expect((await run).ok).toBe(false);
    expect(runStore.get()).toMatchObject({ phase: "idle", error: null, note: "Оценка не запущена." });
    expect(render()).toContain('data-testid="plan-run-note"');
    expect(render()).not.toContain("plan-run-error");
  });
  it("a refusal of the core stands in the window with its hint; the job that failed too", async () => {
    choosePlan(51);
    core.force("POST /geostat/estimate/run", { status: 409, body: { code: "version_conflict", version: 5, message: "Блочная модель изменена.", hint: "Перечитайте модель." } });
    expect((await app.registry.execute("estimate.run", runArgsPlan()!, "user")).ok).toBe(false);
    expect(runStore.get()).toMatchObject({ phase: "failed", error: { code: "version_conflict" } });
    expect(render()).toContain("Блочная модель изменена. Перечитайте модель.");
    core.job = { status: "failed", result: null, error: { code: "no_samples", message: "В домене нет проб.", hint: "Проверьте домен." } };
    expect((await app.registry.execute("estimate.run", runArgsPlan()!, "user")).ok).toBe(false);
    expect(runStore.get().error).toMatchObject({ code: "no_samples" });
    expect(render()).toContain("В домене нет проб. Проверьте домен.");
  });
  it("a cancelled job: nothing was written, the window says so", async () => {
    choosePlan(51);
    core.job = { status: "cancelled", result: null, error: null };
    const r = await app.registry.execute("estimate.run", runArgsPlan()!, "user");
    expect(r.result).toBe("Оценка модели «Модель» отменена: ничего не записано");
    expect(runStore.get().phase).toBe("cancelled");
    expect(render()).toContain("ничего не записано");
  });
});

describe("the window drawn", () => {
  it("without a project model it asks for one; the models offered are those with blocks and a regular step", () => {
    const html = render();
    expect(html).toContain('data-testid="plan-model"');
    expect(html).toContain("Выберите блочную модель.");
    expect(html).not.toContain("Переменный шаг");
    expect(html).not.toContain("Прототип");
    expect(html).toContain('data-testid="plan-run"');
  });
  it("a plan: the rows in the order of the run, the field of the samples, the state with its reason, the last run", async () => {
    choosePlan(51);
    await waitFor(() => !!estimationStore.get().details[41]);
    const html = render();
    expect(html.indexOf('data-testid="plan-row-41"')).toBeLessThan(html.indexOf('data-testid="plan-row-42"'));
    expect(html).toContain("окисл");
    expect(html).toContain("AU_OK_*");
    expect(html).toContain("устарел: изменена таблица «Композиты»");
    expect(html).toContain("оценён 1 100, вне домена 60");
    expect(html).toContain("не выполнялась");
    expect(off(html, "plan-up-41")).toBe(true);
    expect(off(html, "plan-down-42")).toBe(true);
    expect(off(html, "plan-down-41")).toBe(false);
    expect(html).toContain("План №51, версия 1");
  });
  it("a changed copy says so; the add list has only the rows of the model that are not in the plan", () => {
    choosePlan(51);
    moveItem(41, 1);
    const html = render();
    expect(html).toContain("Есть несохранённые изменения");
    expect(html).toContain("AU · IDW · перв");
    expect(html).not.toContain("Другая модель");
    expect(html.indexOf('data-testid="plan-row-42"')).toBeLessThan(html.indexOf('data-testid="plan-row-41"'));
  });
  it("one prefix, one domain: the warning stands under the rows", () => {
    choosePlan(51);
    estimationStore.set((s) => ({ rows: s.rows.map((r) => (r.id === 42 ? { ...r, label: "окисл" } : r)) }));
    expect(render()).toContain("а домены у них совпадают");
  });
  it("only for reading: the run and the save are not available, with the reason", () => {
    choosePlan(51);
    moveItem(41, 1);
    app.store.set({ projectReadOnly: true });
    const html = render();
    expect(off(html, "plan-run")).toBe(true);
    expect(off(html, "plan-save")).toBe(true);
    expect(html).toContain("Проект открыт только для чтения");
  });
  it("the run goes: the progress with the stage of the job, «Отменить»; the run is not offered again", () => {
    choosePlan(51);
    runBegin(7, "Модель", 51, [41, 42]);
    runJob({ source: "proj", id: 5 });
    jobsStore.set({ jobs: [{ id: 5, source: "proj", kind: "estimate.run", title: "Оценка", queue: "heavy", status: "running", progress: 0.4, message: "Поиск и кригинг: проход 2", error: null, result: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null }], workers: null, error: null });
    const html = render();
    expect(html).toContain('data-testid="plan-progress"');
    expect(html).toContain('value="0.4"');
    expect(html).toContain("Поиск и кригинг: проход 2");
    expect(html).toContain('data-testid="plan-cancel"');
    expect(off(html, "plan-run")).toBe(true);
  });
  it("nothing is ticked: «Выполнить выбранные» says why; a ticked row makes it available", () => {
    choosePlan(51);
    expect(render()).toContain("Отметьте строки в таблице");
    pick(41, true);
    const html = render();
    expect(html).toContain("Выполнить выбранные (1)");
    expect(off(html, "plan-run-picked")).toBe(false);
  });
});

describe("the section «Параметры оценки» of the tree", () => {
  it("the rows and the plans by name; the state of a row; the number of the rows of a plan", () => {
    const html = tree();
    expect(html).toContain('data-testid="tree-est-row-41"');
    expect(html).toContain('data-testid="tree-est-plan-51"');
    expect(html).toContain("устарел");
    expect(html.indexOf("AU · IDW · перв")).toBeLessThan(html.indexOf("AU · OK · окисл"));
    expect(rowsByName(estimationStore.get().rows).map((r) => r.name)).toEqual(["Другая модель", "AU · IDW · перв", "AU · OK · окисл", "AU · OK · перех"]);
    expect(plansByName(estimationStore.get().plans).map((p) => p.id)).toEqual([51]);
  });
  it("the tooltips: the row tells its method, domain, fields, state and the last run; the plan its rows and the model", () => {
    const [r41, r42] = estimationStore.get().rows;
    expect(flat(rowTitleText(r41))).toContain("«AU · OK · окисл»: Обычный кригинг (OK)");
    expect(rowTitleText(r41)).toContain("Модель «Модель», домен: окисл, поля AU_OK_*");
    expect(rowTitleText(r41)).toContain("Последний запуск");
    expect(rowTitleText(r42)).toContain("Устарел: изменена таблица «Композиты»");
    expect(rowTitleText(r42)).toContain("Не выполнялась");
    expect(planTitleText(plan51())).toBe("План «План · Модель»: строк 2, модель «Модель»\nДвойной щелчок — открыть в окне «План оценки»");
  });
  it("the menus run the commands of the registry; a row whose source is gone cannot be run", async () => {
    const calls: [string, Record<string, unknown>][] = [];
    const run = (id: string, a: Record<string, unknown>) => void calls.push([id, a]);
    const reason = (id: string) => app.registry.reason(id, { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true });
    const items = rowMenu({ id: 41, source_lost: false }, reason, run);
    expect(items.map((i) => i.label)).toEqual(["Изменить", "Выполнить", "Удалить…"]);
    for (const i of items) i.run?.();
    expect(calls).toEqual([["estimate.open", { id: 41 }], ["estimate.run", { estimation_ids: "41" }], ["estimate.delete", { id: 41 }]]);
    expect(rowMenu({ id: 41, source_lost: true }, reason, run)[1].reason).toContain("Источник удалён");
    calls.length = 0;
    const plan = planMenu(plan51(), reason, run);
    expect(plan.map((i) => i.label)).toEqual(["Открыть", "Выполнить", "Удалить…"]);
    for (const i of plan) i.run?.();
    expect(calls).toEqual([["estimate.plan", { plan_id: 51 }], ["estimate.run", { plan_id: 51 }], ["estimate.plan_delete", { id: 51 }]]);
    expect(planMenu({ id: 9, items: [] }, reason, run)[1].reason).toBe("В плане нет строк");
  });
});
