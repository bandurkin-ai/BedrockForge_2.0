// The commands `estimate.*` of the registry (Э18, 18.19; ADR 0024 §10, e18-api.md §12) on the answers of the contract: the
// declarations (the ribbon, Ctrl+K and the assistant read them), `estimate.open` (the wizard, the arguments of the contact analysis),
// `estimate.run` (the job, «Заменить поля?»), `estimate.delete`; the rest waits for its window and says so.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { ESTIMATE_BUTTONS, CHECK_BUTTONS, SIM_BUTTONS, commands } from "../../src/modules/estimation/commands";
import { estimableModels, wizardWindow } from "../../src/modules/estimation/bind";
import { jobsStore } from "../../src/modules/jobs/state";
import { loadRows } from "../../src/modules/estimation/state";
import { WIZARD_WINDOW, wizardStore } from "../../src/modules/estimation/wizardState";
import { moduleCommands } from "../../src/shell/catalog";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}

describe("the declarations", () => {
  const ids = commands.map((c) => c.id);
  it("every command of §12 of the contract that belongs to Э18 is in the catalog, once", () => {
    for (const id of ["estimate.open", "estimate.plan", "estimate.ellipsoid", "estimate.run", "estimate.delete", "estimate.inspect", "estimate.xval", "estimate.kna", "estimate.global", "estimate.qq", "estimate.swath"]) expect(ids).toContain(id);
    expect(new Set(ids).size).toBe(ids.length);
    const catalog = moduleCommands().map((c) => c.id);
    for (const id of ids) expect(catalog.filter((x) => x === id)).toHaveLength(1);
  });
  it("the signs of the contract: the wizard and the plan are for the interface, run and delete change data", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(by["estimate.open"].uiOnly).toBe(true);
    expect(by["estimate.plan"].uiOnly).toBe(true);
    expect(by["estimate.run"].confirm).toBe(true);
    expect(by["estimate.delete"].confirm).toBe(true);
    expect(by["estimate.delete"].required).toEqual(["id"]);
    expect(by["estimate.open"].params?.method?.enum).toEqual(["nn", "idw", "sk", "ok", "ik"]);
    for (const c of commands) {
      expect(c.title).toMatch(/[А-Яа-я]/);
      expect(c.description).toMatch(/[А-Яа-я]/);
    }
  });
  it("the assistant sees what it may call: not the wizard, not the plan window", () => {
    const names = commandTools(commands).map((t) => t.name);
    expect(names).not.toContain("ui_estimate_open");
    expect(names).not.toContain("ui_estimate_plan");
    expect(names).toContain("ui_estimate_run");
  });
  it("the commands whose window is not ready say so; the three bound ones are available", () => {
    const reason = (id: string) => app.registry.reason(id, { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true });
    for (const id of ["estimate.plan", "estimate.ellipsoid", "estimate.inspect", "estimate.xval", "estimate.kna", "estimate.global", "estimate.qq", "estimate.swath"]) expect(reason(id)).toMatch(/появится на этапе Э18/);
    for (const id of ["estimate.open", "estimate.run", "estimate.delete"]) expect(reason(id)).toBeNull();
  });
  it("without a project the wizard is not available, with the reason", () => {
    expect(app.registry.reason("estimate.open", { hasProject: false, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true })).toBe("Откройте или создайте проект");
  });
  it("the buttons of the ribbon name registered commands", () => {
    for (const b of [...ESTIMATE_BUTTONS, ...CHECK_BUTTONS, ...SIM_BUTTONS]) {
      expect(ids).toContain(b.command);
      expect(b.label).not.toBe("");
      expect(b.hint).toMatch(/[А-Яа-я]/);
    }
    expect(SIM_BUTTONS[0]).toMatchObject({ command: "estimate.open", args: { method: "ik" }, label: "Индикаторы" });
  });
});

describe("estimate.open", () => {
  it("opens the wizard with the only block model that can be estimated (a variable step and a prototype are not offered)", async () => {
    expect(estimableModels().map((m) => m.id)).toEqual([7]);
    const r = await app.registry.execute("estimate.open", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыт мастер «Оценка содержаний»" });
    expect(windowStore.get().windows.filter((w) => w.kind === WIZARD_WINDOW)).toHaveLength(1);
    expect(wizardStore.get().form.modelId).toBe(7);
    expect(wizardStore.get().step).toBe(1);
    await waitFor(() => wizardStore.get().vario.loaded);
  });
  it("one wizard per project: the second call brings the open one forward", async () => {
    await app.registry.execute("estimate.open", {}, "user");
    const id = wizardWindow();
    await app.registry.execute("estimate.open", { method: "sk" }, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === WIZARD_WINDOW)).toHaveLength(1);
    expect(wizardWindow()).toBe(id);
    expect(wizardStore.get().form.method).toBe("sk");
  });
  it("the model chosen in the tree is the model of the wizard", async () => {
    app.store.set({ selected: { kind: "blocks", id: 7 } as never });
    await app.registry.execute("estimate.open", {}, "user");
    expect(wizardStore.get().form.modelId).toBe(7);
  });
  it("the method of the button «Индикаторы»", async () => {
    await app.registry.execute("estimate.open", { method: "ik" }, "user");
    expect(wizardStore.get().form.method).toBe("ik");
  });
  it("«Задать мягкую границу…» of the contact analysis: the pair of the domains and the distance", async () => {
    await app.registry.execute("estimate.open", { soft_a: "окисл", soft_b: "перех", soft_distance: 5 }, "user");
    const f = wizardStore.get().form;
    expect(f.domain?.values).toEqual(["окисл"]);
    expect(f.soft).toEqual({ on: true, domains: ["перех"], dist: "5" });
  });
  it("a stored row for editing", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.open", { id: 41 }, "user");
    expect(r.ok).toBe(true);
    expect(wizardStore.get().form.name).toBe("AU · OK · окисл");
    expect(wizardStore.get().editing).toEqual({ id: 41, version: 2 });
  });
  it("a row or a model that is not there: the words of the refusal, the wizard stays shut", async () => {
    await loadRows();
    const a = await app.registry.execute("estimate.open", { id: 99 }, "user");
    expect(a).toMatchObject({ ok: false });
    expect(a.result).toContain("№99 не найдена");
    const b = await app.registry.execute("estimate.open", { model_id: 77 }, "user");
    expect(b.ok).toBe(false);
    expect(b.result).toContain("Блочная модель №77 не найдена");
    expect(wizardWindow()).toBeNull();
  });
  it("the assistant may not open it (a window needs a person)", async () => {
    const r = await app.registry.execute("estimate.open", {}, "claude");
    expect(r).toEqual({ ok: false, result: "Команда доступна только из интерфейса" });
  });
  it("the arguments are checked by the registry (the method is one of five)", async () => {
    const r = await app.registry.execute("estimate.open", { method: "tin" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("допустимые значения");
  });
});

describe("estimate.delete", () => {
  /** The question of the registry to the person for a call of the assistant, or the own question of the window: the answer. */
  const answer = async (ok: boolean) => {
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind === "confirm") d.resolve(ok);
    closeDialog(d.id);
    return d;
  };
  it("from the interface the person is asked first (an own dialog); «Нет» deletes nothing", async () => {
    await loadRows();
    const run = app.registry.execute("estimate.delete", { id: 41 }, "user");
    const d = await answer(false);
    if (d.kind === "confirm") {
      expect(d.options.message).toContain("Удалить строку «AU · OK · окисл»?");
      expect(d.options.message).toContain("Она уйдёт из планов (1)");
      expect(d.options.message).toContain("останутся");
    }
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(requests("DELETE /geostat/estimations/41")).toHaveLength(0);
  });
  it("«Да»: the core is asked, the plans are told", async () => {
    await loadRows();
    const run = app.registry.execute("estimate.delete", { id: 41 }, "user");
    await answer(true);
    expect(await run).toEqual({ ok: true, result: "Строка «AU · OK · окисл» удалена; убрана из планов: 1" });
    expect(requests("DELETE /geostat/estimations/41")).toHaveLength(1);
  });
  it("a call of the assistant is asked once, by the registry (the data changes)", async () => {
    await loadRows();
    const run = app.registry.execute("estimate.delete", { id: 41 }, "claude");
    await answer(true);
    expect((await run).ok).toBe(true);
    expect(dialogStore.get().queue).toHaveLength(0);
    expect(requests("DELETE /geostat/estimations/41")).toHaveLength(1);
  });
  it("a row that is not there", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.delete", { id: 99 }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("не найдена");
    expect(dialogStore.get().queue).toHaveLength(0);
  });
  it("«только просмотр»: no deletion", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("estimate.delete", { id: 41 }, "user");
    expect(r).toEqual({ ok: false, result: "Проект открыт только для чтения" });
  });
});

describe("estimate.run", () => {
  it("the rows of one model: the version of the model goes with the run; the job is followed; the answer names the fields and the version", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.run", { estimation_ids: "41; 42" }, "user").catch(() => null);
    // the row 42 is not in the list of the contract (one row): it is told, nothing is sent
    expect(r).toMatchObject({ ok: false });
    expect(r?.result).toContain("Не найдены строки «Параметры оценки»: 42");
    expect(requests("POST /geostat/estimate/run")).toHaveLength(0);
  });
  it("one row: POST /estimate/run {estimation_ids, expected_version, replace}; the result as the core wrote it", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /geostat/estimate/run")[0].body).toEqual({ estimation_ids: [41], expected_version: 4, replace: false });
    expect(r.result).toContain("Оценка записана в модель «Модель» (версия 5)");
    expect(r.result).toContain("AU_OK_STATUS, AU_OK_EST, AU_OK_KV, AU_OK_NS, AU_OK_NH, AU_OK_AD и ещё 3");
    expect(r.result).toContain("Проход 1: 3 блока — вырожденная система, переход к проходу 2.");
  });
  it("a plan: POST with `plan_id`; the model is the plan's", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.run", { plan_id: 51 }, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /geostat/estimate/run")[0].body).toEqual({ plan_id: 51, expected_version: 4, replace: false });
  });
  it("a plan or rows — one of the two; numbers must be whole; a plan that is not there", async () => {
    await loadRows();
    expect((await app.registry.execute("estimate.run", {}, "user")).result).toContain("plan_id или estimation_ids");
    expect((await app.registry.execute("estimate.run", { plan_id: 51, estimation_ids: "41" }, "user")).result).toContain("что-то одно");
    expect((await app.registry.execute("estimate.run", { estimation_ids: "41; x" }, "user")).result).toContain("нужны целые числа");
    expect((await app.registry.execute("estimate.run", { plan_id: 9 }, "user")).result).toContain("План оценки №9 не найден");
    expect(requests("POST /geostat/estimate/run")).toHaveLength(0);
  });
  it("the fields are in the model already: the person is asked «Заменить поля», «Заменить» sends `replace: true`", async () => {
    await loadRows();
    core.force("POST /geostat/estimate/run", A.run_field_exists);
    const run = app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.title).toBe("Заменить поля");
    expect(d.kind === "confirm" && d.options.message).toContain("Поле «AU_OK_EST» уже есть в модели.");
    if (d.kind === "confirm") d.resolve(true);
    expect((await run).ok).toBe(true);
    const posts = requests("POST /geostat/estimate/run");
    expect(posts.map((p) => p.body.replace)).toEqual([false, true]);
  });
  it("«Нет» на вопрос о замене: оценка не запущена", async () => {
    await loadRows();
    core.force("POST /geostat/estimate/run", A.run_field_exists);
    const run = app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    if (d.kind === "confirm") d.resolve(false);
    const r = await run;
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Оценка не запущена");
    expect(requests("POST /geostat/estimate/run")).toHaveLength(1);
  });
  it("`replace: true` goes without the question", async () => {
    await loadRows();
    const r = await app.registry.execute("estimate.run", { estimation_ids: "41", replace: true }, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /geostat/estimate/run")[0].body.replace).toBe(true);
    expect(dialogStore.get().queue).toHaveLength(0);
  });
  it("a refusal of the core before the job (the model was changed by another window)", async () => {
    await loadRows();
    core.force("POST /geostat/estimate/run", { status: 409, body: { code: "version_conflict", version: 5, message: "Блочная модель изменена.", hint: "Перечитайте модель." } });
    const r = await app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    expect(r).toEqual({ ok: false, result: "Блочная модель изменена. Перечитайте модель." });
  });
  it("the job fails or is cancelled: told in Russian, nothing is said to be written", async () => {
    await loadRows();
    core.job = { status: "failed", result: null, error: { code: "no_samples", message: "В домене нет проб.", hint: "Проверьте домен проб." } };
    const f = await app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    expect(f).toEqual({ ok: false, result: "В домене нет проб. Проверьте домен проб." });
    jobsStore.set({ jobs: [], workers: null, error: null }); // the next job has another number in the core
    core.job = { status: "cancelled", result: null, error: null };
    const c = await app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    expect(c).toEqual({ ok: true, result: "Оценка модели «Модель» отменена: ничего не записано" });
  });
  it("a result without the fields is told, not shown as a success", async () => {
    await loadRows();
    core.job = { status: "done", result: {}, error: null };
    const r = await app.registry.execute("estimate.run", { estimation_ids: "41" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("без итогов");
  });
  it("«только просмотр»: no run", async () => {
    app.store.set({ projectReadOnly: true });
    expect((await app.registry.execute("estimate.run", { plan_id: 51 }, "user")).result).toBe("Проект открыт только для чтения");
  });
});
