// The commands of the simulation (Э18б, 18.35; ТЗ 6.9, 7.35 Д-23; docs/progress/e18-api.md §12) on the real answers of the core: they are in the
// registry and bound (the ribbon, Ctrl+K, the keys and the assistant run the same ones), the wizard opens without arguments, a stored row runs
// by its number, the flat arguments run as a new row (the body is that of the form), the save, the deletion with its own question, the project
// that changes, the read-only project.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { commandTools } from "../../src/shell/commands";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { Icon } from "../../src/shell/Icon";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { CHECK_BUTTONS, ESTIMATE_BUTTONS, SIM_BUTTONS as ESTIMATION_SIM_BUTTONS, SIM_GROUP_TITLE as ESTIMATION_SIM_TITLE } from "../../src/modules/estimation/commands";
import { projectStore } from "../../src/modules/projects/state";
import { blocksStore } from "../../src/modules/blocks/state";
import { commands, SIM_BUTTONS, SIM_GROUP_TITLE } from "../../src/modules/simulation/commands";
import { isSimulationEvent, loadRows, simById, simulationStore } from "../../src/modules/simulation/state";
import { runStore } from "../../src/modules/simulation/runState";
import { wizardWindow } from "../../src/modules/simulation/bind";
import { loadVariogramModels, putForm, setForm, simWizardStore, SIM_WIZARD_WINDOW } from "../../src/modules/simulation/wizardState";
import { formOfMeta, bodyOf } from "../../src/modules/simulation/form";
import { ID, INFOS, installSimCore, newApp, requests, S, SG, SID, SIM_MODEL, teardown, THRESHOLDS, VID, waitFor, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(teardown);

const ids = commands.map((c) => c.id);
const CTX = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const NO_PROJECT = { ...CTX, hasProject: false, hasSet: false };

describe("the registry", () => {
  it("the commands are those of §12 and Д-23: the wizard, the three kinds, save, run, summary, post-processing, delete", () => {
    expect(ids).toEqual(["simulate.open", "simulate.sgs", "simulate.sis", "simulate.cosgs", "simulate.save", "simulate.run", "simulate.summary", "simulate.post", "simulate.delete"]);
    for (const c of commands) {
      expect(app.registry.get(c.id)).toBeDefined();
      expect(c.title).toMatch(/[А-Яа-я]/);
      expect(c.description).toMatch(/[А-Яа-я]/);
    }
  });
  it("every command of the module is bound: none says «появится на этапе»; none is available without a project", () => {
    const waiting = ids.filter((id) => (app.registry.reason(id, CTX) ?? "").includes("появится на этапе"));
    expect(waiting).toEqual([]);
    for (const id of ids) {
      expect(app.registry.reason(id, CTX)).toBeNull();
      expect(app.registry.reason(id, NO_PROJECT)).toBe("Откройте или создайте проект");
    }
  });
  it("the assistant sees what it may call: the kinds, run, save, post-processing, deletion — not the wizard and the window of the summary", () => {
    const names = commandTools(commands).map((t) => t.name);
    for (const n of ["ui_simulate_sgs", "ui_simulate_sis", "ui_simulate_cosgs", "ui_simulate_run", "ui_simulate_post", "ui_simulate_delete", "ui_simulate_save"]) expect(names).toContain(n);
    expect(names).not.toContain("ui_simulate_open");
    expect(names).not.toContain("ui_simulate_summary");
  });
  it("what writes data asks the person when the assistant calls it: the post-processing writes to the block model, the deletion removes the realizations", () => {
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(["simulate.post", "simulate.delete"]);
    expect(commands.find((c) => c.id === "simulate.run")?.required).toEqual(["id"]);
  });
  it("the parameters are flat, of the three kinds: SIS has the thresholds and the models of the indicators, co-SGS the secondary field and ρ", () => {
    const p = (id: string) => Object.keys(commands.find((c) => c.id === id)!.params ?? {});
    expect(p("simulate.sgs")).toEqual(expect.arrayContaining(["id", "model_id", "set_id", "table", "field", "variogram_id", "n_real", "seed", "nodes", "n_max"]));
    expect(p("simulate.sgs")).not.toContain("thresholds");
    expect(p("simulate.sis")).toEqual(expect.arrayContaining(["thresholds", "categories", "labels", "models", "variogram_ids"]));
    expect(p("simulate.cosgs")).toEqual(expect.arrayContaining(["secondary_field", "rho"]));
    expect(p("simulate.sis")).not.toContain("secondary_field");
  });
  it("the group «Моделирование» of the ribbon: SGS, SIS, Ко-SGS, the summary, the post-processing — registered commands with icons of the set", () => {
    expect(SIM_GROUP_TITLE).toBe("Моделирование");
    expect(SIM_BUTTONS.map((b) => b.command)).toEqual(["simulate.sgs", "simulate.sis", "simulate.cosgs", "simulate.summary", "simulate.post"]);
    expect(SIM_BUTTONS.map((b) => b.label)).toEqual(["SGS", "SIS", "Ко-SGS", "Сводка реализаций", "Постобработка"]);
    for (const b of SIM_BUTTONS) {
      expect(ids).toContain(b.command);
      expect(b.hint).toMatch(/[А-Яа-я]/);
      expect(renderToStaticMarkup(createElement(Icon, { name: b.icon }))).toMatch(/<path d="M/);
    }
    // the shell takes the group from the estimation's module until it imports it from here: the same
    expect(ESTIMATION_SIM_BUTTONS).toBe(SIM_BUTTONS);
    expect(ESTIMATION_SIM_TITLE).toBe(SIM_GROUP_TITLE);
    expect([...ESTIMATE_BUTTONS, ...CHECK_BUTTONS].some((b) => b.command.startsWith("simulate."))).toBe(false);
  });
});

describe("simulate.open and the kinds without arguments", () => {
  it("opens the wizard with the only block model that can be used; one wizard per project", async () => {
    const r = await app.registry.execute("simulate.open", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыт мастер «Моделирование»" });
    expect(windowStore.get().windows.filter((w) => w.kind === SIM_WIZARD_WINDOW)).toHaveLength(1);
    expect(simWizardStore.get().form).toMatchObject({ modelId: ID.model, kind: "sgs" });
    expect(simWizardStore.get().step).toBe(1);
    await waitFor(() => simWizardStore.get().vario.loaded);
    const id = wizardWindow();
    await app.registry.execute("simulate.sis", {}, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === SIM_WIZARD_WINDOW)).toHaveLength(1);
    expect(wizardWindow()).toBe(id);
    expect(simWizardStore.get().form.kind).toBe("sis");
  });
  it("each kind opens the wizard of its kind; the kind drops the models of the other", async () => {
    await app.registry.execute("simulate.cosgs", {}, "user");
    expect(simWizardStore.get().form.kind).toBe("cosgs");
    setForm({ variogramId: VID.vy });
    await app.registry.execute("simulate.sgs", {}, "user");
    expect(simWizardStore.get().form).toMatchObject({ kind: "sgs", variogramId: null });
    setForm({ variogramId: VID.vy });
    await app.registry.execute("simulate.sgs", {}, "user");
    expect(simWizardStore.get().form).toMatchObject({ kind: "sgs", variogramId: VID.vy });
    await app.registry.execute("simulate.sis", {}, "user");
    expect(simWizardStore.get().form).toMatchObject({ kind: "sis", variogramId: null, variogramIds: [] });
  });
  it("another kind than the row being edited opens a new row: the stored row of the old kind is not rewritten", async () => {
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    expect(simWizardStore.get()).toMatchObject({ form: { kind: "sgs" }, editing: { id: SID.sgs } });
    await app.registry.execute("simulate.sis", {}, "user");
    expect(simWizardStore.get()).toMatchObject({ form: { kind: "sis" }, editing: null, saved: null });
    // the same kind goes on with the row
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    await app.registry.execute("simulate.sgs", {}, "user");
    expect(simWizardStore.get().editing).toMatchObject({ id: SID.sgs });
  });
  it("`id` of `simulate.open` opens the stored row for the edit; the kind of the row decides", async () => {
    const r = await app.registry.execute("simulate.open", { id: SID.co }, "user");
    expect(r.ok).toBe(true);
    expect(simWizardStore.get()).toMatchObject({ form: { kind: "cosgs", secField: SG }, editing: { id: SID.co }, step: 1 });
  });
  it("a row or a model that is not there is refused with the way to find the numbers", async () => {
    const a = await app.registry.execute("simulate.open", { id: SID.none }, "user");
    expect(a).toMatchObject({ ok: false, result: expect.stringContaining(`Моделирование №${SID.none} не найдено`) });
    const b = await app.registry.execute("simulate.open", { model_id: 999 }, "user");
    expect(b).toMatchObject({ ok: false, result: expect.stringContaining("Блочная модель №999 не найдена") });
    expect(windowStore.get().windows).toHaveLength(0);
  });
  it("the model chosen in the tree is the model of a new wizard", async () => {
    blocksStore.set({ list: [SIM_MODEL, { ...SIM_MODEL, id: 77, name: "Вторая" }] });
    app.store.set({ selected: { kind: "blocks", id: 77 } as never });
    await app.registry.execute("simulate.open", {}, "user");
    expect(simWizardStore.get().form.modelId).toBe(77);
  });
});

describe("a stored row runs; the parameters run as a new row", () => {
  it("`id` runs the row (the same as `simulate.run`)", async () => {
    const r = await app.registry.execute("simulate.sgs", { id: SID.sgs }, "user");
    expect(r.ok).toBe(true);
    expect(requests(core, "POST /geostat/simulate/run")[0].body).toEqual({ simulation_id: SID.sgs });
    expect(runStore.get().phase).toBe("done");
  });
  it("SGS by the flat parameters: the body is that of the form and goes as `simulation`; the row is made by the job", async () => {
    const r = await app.registry.execute("simulate.sgs", { model_id: ID.model, domain_field: "ZONE", label: "окисл", set_id: ID.set, table: ID.table, field: "AU", sample_domain_field: "ZONE", domain_table: ID.domTable, domain_values: "окисл", variogram_id: VID.vy, n_real: 4, seed: 99, nodes: 6, name: "Из команды" }, "claude");
    expect(r.ok).toBe(true);
    const body = requests(core, "POST /geostat/simulate/run")[0].body;
    expect(Object.keys(body)).toEqual(["simulation"]);
    expect(body.simulation).toEqual({
      name: "Из команды",
      kind: "sgs",
      model_id: ID.model,
      target: { domain_field: "ZONE", label: "окисл", filter_id: null },
      samples: { set_id: ID.set, table: ID.table, field: "AU", domain: { table: ID.domTable, field: "ZONE", values: ["окисл"] } },
      variogram: { model_id: VID.vy },
      search: { passes: [{ factor: 1, n_max: 24 }] },
      nodes: 6,
      n_real: 4,
      seed: 99,
      nscore: { ltail: "linear", utail: "linear", ltpar: null, utpar: null },
    });
    expect(r.result).toContain("Моделирование «Из команды» выполнено: 200 узлов × 5 реализаций");
  });
  it("SIS by the thresholds and ко-SGS by the secondary field", async () => {
    const base = { model_id: ID.model, set_id: ID.set, table: ID.table, field: "AU", n_real: 4 };
    await app.registry.execute("simulate.sis", { ...base, thresholds: THRESHOLDS.join("; "), variogram_ids: VID.ind.join(";") }, "claude");
    const sis = requests(core, "POST /geostat/simulate/run")[0].body.simulation as Record<string, unknown>;
    expect(sis).toMatchObject({ kind: "sis", method: { ik: { models: "per", thresholds: THRESHOLDS } }, variograms: VID.ind.map((id, i) => ({ model_id: id, t: THRESHOLDS[i] })) });
    runStore.set({ phase: "idle" });
    await app.registry.execute("simulate.cosgs", { ...base, variogram_id: VID.vy, secondary_field: SG, rho: -0.25 }, "claude");
    const co = requests(core, "POST /geostat/simulate/run")[1].body.simulation as Record<string, unknown>;
    expect(co).toMatchObject({ kind: "cosgs", secondary: { field: SG, rho: -0.25 }, variogram: { model_id: VID.vy } });
  });
  it("a wrong set of parameters is refused with the words under the field — the core is not asked", async () => {
    const r = await app.registry.execute("simulate.sgs", { model_id: ID.model, set_id: ID.set, table: ID.table, field: "AU" }, "user");
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining("Не выбрана модель вариограммы") });
    const n = await app.registry.execute("simulate.sgs", { model_id: ID.model, set_id: ID.set, table: ID.table, field: "AU", variogram_id: VID.vy, n_real: 101 }, "user");
    expect(n).toMatchObject({ ok: false, result: expect.stringContaining("Число реализаций") });
    expect(requests(core, "POST /geostat/simulate/run")).toHaveLength(0);
  });
  it("the refusals of the core before the job (the scale of the model, a name taken) are told as the core says them", async () => {
    core.force("POST /geostat/simulate/run", { status: 422, body: S.variogram_scale.body });
    const r = await app.registry.execute("simulate.sgs", { id: SID.sgs }, "user");
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining(S.variogram_scale.body.message) });
    expect(runStore.get()).toMatchObject({ phase: "failed", error: { code: "variogram_scale", hint: S.variogram_scale.body.hint } });
  });
  it("«только просмотр»: nothing runs", async () => {
    app.store.set({ projectReadOnly: true });
    for (const id of ["simulate.run", "simulate.save", "simulate.post", "simulate.delete"]) expect(await app.registry.execute(id, { id: SID.sgs }, "user")).toEqual({ ok: false, result: "Проект открыт только для чтения" });
    // the kinds run a row or a body: refused; without parameters they open the wizard for looking
    for (const id of ["simulate.sgs", "simulate.sis", "simulate.cosgs"]) {
      expect(await app.registry.execute(id, { id: SID.sgs }, "user")).toMatchObject({ ok: false, result: expect.stringContaining("только для чтения") });
      expect(await app.registry.execute(id, { set_id: ID.set }, "user")).toMatchObject({ ok: false, result: expect.stringContaining("только для чтения") });
      expect((await app.registry.execute(id, {}, "user")).ok).toBe(true);
    }
    expect(requests(core, "POST /geostat/simulate/run")).toHaveLength(0);
    // the windows open for reading
    expect((await app.registry.execute("simulate.summary", { id: SID.sgs }, "user")).ok).toBe(true);
    expect((await app.registry.execute("simulate.open", { id: SID.sgs }, "user")).ok).toBe(true);
  });
});

describe("simulate.save", () => {
  it("without a wizard there is nothing to save; with one the row is written (the same as the button), `id` is checked", async () => {
    const none = await app.registry.execute("simulate.save", {}, "user");
    expect(none).toMatchObject({ ok: false, result: expect.stringContaining("Нет открытой формы моделирования") });
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    await loadVariogramModels();
    const wrong = await app.registry.execute("simulate.save", { id: SID.sis }, "user");
    expect(wrong).toMatchObject({ ok: false, result: expect.stringContaining(`не строка №${SID.sis}`) });
    setForm({ nReal: "7" });
    const r = await app.registry.execute("simulate.save", { id: SID.sgs }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toBe(`Сохранено: «${S.simulation.body.name}», версия ${INFOS[SID.sgs].version + 1}`);
    expect(requests(core, `PUT /geostat/simulations/${SID.sgs}`)[0].body).toMatchObject({ n_real: 7 });
    const asNew = await app.registry.execute("simulate.save", { as_new: true }, "user");
    expect(asNew.ok).toBe(true);
    expect(requests(core, "POST /geostat/simulations")).toHaveLength(1);
  });
  it("a refusal stands under its field and is told by the command", async () => {
    await app.registry.execute("simulate.open", {}, "user");
    const r = await app.registry.execute("simulate.save", {}, "user");
    expect(r.ok).toBe(false);
    expect(simWizardStore.get().issue).toMatchObject({ param: "samples.set_id" });
    expect(simWizardStore.get().step).toBe(2);
  });
});

describe("simulate.delete", () => {
  const answer = async (ok: boolean) => {
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind === "confirm") d.resolve(ok);
    closeDialog(d.id);
    return d;
  };
  it("from the interface the person is asked first (an own dialog); «Нет» deletes nothing", async () => {
    const run = app.registry.execute("simulate.delete", { id: SID.sgs }, "user");
    const d = await answer(false);
    if (d.kind === "confirm") {
      expect(d.options.message).toContain(`Удалить «${S.simulation.body.name}» вместе с реализациями?`);
      expect(d.options.message).toContain("останутся");
    }
    expect(await run).toEqual({ ok: true, result: "Отменено" });
    expect(requests(core, `DELETE /geostat/simulations/${SID.sgs}`)).toHaveLength(0);
  });
  it("«Да»: the row goes with its realizations; the list is read again; what pointed at it lets go", async () => {
    await app.registry.execute("simulate.open", { id: SID.sgs }, "user");
    const run = app.registry.execute("simulate.delete", { id: SID.sgs }, "user");
    await answer(true);
    expect(await run).toEqual({ ok: true, result: `«${S.simulation.body.name}» удалено вместе с реализациями` });
    expect(requests(core, `DELETE /geostat/simulations/${SID.sgs}`)).toHaveLength(1);
    expect(simWizardStore.get().editing).toBeNull();
    expect(requests(core, "GET /geostat/simulations").length).toBeGreaterThan(1);
  });
  it("a call of the assistant is asked once, by the registry (the data goes)", async () => {
    const run = app.registry.execute("simulate.delete", { id: SID.sis }, "claude");
    await answer(true);
    expect((await run).ok).toBe(true);
    expect(dialogStore.get().queue).toHaveLength(0);
    expect(requests(core, `DELETE /geostat/simulations/${SID.sis}`)).toHaveLength(1);
  });
  it("a row that is not there", async () => {
    const r = await app.registry.execute("simulate.delete", { id: SID.none }, "user");
    expect(r).toMatchObject({ ok: false, result: expect.stringContaining("не найдено") });
    expect(dialogStore.get().queue).toHaveLength(0);
  });
});

describe("the project and the events", () => {
  it("the rows are read when the project opens and follow it: another project — clean rows, a clean wizard", async () => {
    expect(simulationStore.get().rows.map((r) => r.id)).toEqual([SID.sgs, SID.sis, SID.cat, SID.co]);
    expect(simById(SID.sgs)).toMatchObject({ kind: "sgs", n_real: 5, seed: 99, label: "окисл", field: "AU", last_run: { realizations: 5, nodes: 200 }, stale: false });
    putForm(formOfMeta(INFOS[SID.sgs].meta));
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(simulationStore.get().rows).toEqual([]);
    expect(simWizardStore.get().form.modelId).toBeNull();
    await waitFor(() => simulationStore.get().loaded);
  });
  it("`geostat.changed` of a simulation (or without a body) means «read the list again»; of an estimation — not", () => {
    expect(isSimulationEvent(JSON.stringify({ kind: "simulation", id: 8 }))).toBe(true);
    expect(isSimulationEvent(JSON.stringify({ kind: "simulation", id: 8, deleted: true }))).toBe(true);
    expect(isSimulationEvent("{}")).toBe(true);
    expect(isSimulationEvent("не json")).toBe(true);
    expect(isSimulationEvent(JSON.stringify({ kind: "estimation", id: 1 }))).toBe(false);
  });
  it("a model that is gone leaves the form", async () => {
    await app.registry.execute("simulate.open", {}, "user");
    expect(simWizardStore.get().form.modelId).toBe(ID.model);
    blocksStore.set({ list: [] });
    expect(simWizardStore.get().form.modelId).toBeNull();
  });
  it("the form built from a stored row is the body the core stored (the round trip of the wizard)", () => {
    const f = formOfMeta(INFOS[SID.sgs].meta);
    const b = bodyOf(f);
    expect(b.ok).toBe(true);
  });
});
