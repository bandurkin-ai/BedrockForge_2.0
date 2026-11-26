// The commands `implicit.*` of the registry (Э19, 19.22; ADR 0026 «Команды реестра», e19-api.md §12): the declarations (the ribbon, Ctrl+K and
// the assistant read them), `implicit.new` (the wizard; a boundary creates an empty model), `implicit.domains` (the wizard on the step of the
// domains; the codes add a domain), `implicit.save`, `implicit.update` («Построить» / «Обновить»); the structural data wait for their window and say so.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Icon } from "../../src/shell/Icon";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { App } from "../../src/shell/app";
import { commandTools } from "../../src/shell/commands";
import { moduleCommands } from "../../src/shell/catalog";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { commands, IMPLICIT_BUTTONS } from "../../src/modules/implicit/commands";
import { wizardWindow } from "../../src/modules/implicit/bind";
import { implicitStore, loadRows } from "../../src/modules/implicit/state";
import { putForm, setForm, WIZARD_WINDOW, wizardStore } from "../../src/modules/implicit/wizardState";
import { EMPTY_FORM } from "../../src/modules/implicit/form";
import { buildResult, FIELD, ID, installCore, NAME, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
});
afterEach(teardown);

const ids = commands.map((c) => c.id);
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const ctx = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const flat = (s: string): string => s.replace(/[\u00a0\u202f]/g, " ");
const wizards = () => windowStore.get().windows.filter((w) => w.kind === WIZARD_WINDOW);

describe("the declarations", () => {
  it("every command of §12 that belongs to Э19 is in the catalog, once", () => {
    for (const id of ["implicit.new", "implicit.domains", "implicit.shells", "implicit.vein", "implicit.expand", "implicit.structural", "implicit.update", "implicit.to_wireframe", "implicit.delete", "implicit.check", "implicit.support", "implicit.compare_holes", "drillholes.from_model", "implicit.relations", "implicit.fault", "implicit.group_faults", "implicit.vein_system"]) expect(ids).toContain(id);
    expect(new Set(ids).size).toBe(ids.length);
    const catalog = moduleCommands().map((c) => c.id);
    for (const id of ids) expect(catalog.filter((x) => x === id)).toHaveLength(1);
    expect(catalog.filter((x) => x === "trend.save")).toHaveLength(1);
  });
  it("the signs of the contract: delete asks the assistant's confirmation; no key is declared; Russian titles", () => {
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(by["implicit.delete"].confirm).toBe(true);
    expect(by["implicit.delete"].required).toEqual(["model_id"]);
    expect(by["implicit.to_wireframe"].required).toBeUndefined(); // without a wireframe the window «В каркас» lists the outputs (19.24)
    for (const c of commands) {
      expect(c.title, c.id).toMatch(/[А-Яа-я]/);
      expect(c.description, c.id).toMatch(/[А-Яа-я]/);
      expect(c.id.startsWith("implicit.") || c.id === "drillholes.from_model", c.id).toBe(true);
    }
  });
  it("the assistant sees the commands with parameters in flat words: the box and the codes are text", () => {
    const tools = commandTools(commands);
    const nw = tools.find((t) => t.name === "ui_implicit_new" || t.name.endsWith("implicit_new"));
    expect(nw).toBeDefined();
    expect(JSON.stringify(nw)).toContain("box");
    expect(tools.map((t) => t.name).some((n) => n.endsWith("implicit_domains"))).toBe(true);
  });
  it("the buttons of the ribbon name registered commands, with a hint; the icons that are in the set are drawn", () => {
    expect(IMPLICIT_BUTTONS.map((b) => b.command)).toEqual(["implicit.new", "implicit.domains", "implicit.shells", "implicit.vein", "implicit.structural", "implicit.expand", "implicit.relations", "implicit.fault", "implicit.vein_system", "implicit.update", "implicit.to_wireframe", "implicit.check", "implicit.support", "implicit.compare_holes", "drillholes.from_model"]);
    for (const b of IMPLICIT_BUTTONS) {
      expect(ids).toContain(b.command);
      expect(b.hint).toMatch(/[А-Яа-я]/);
      // `implicit` and `vein` are drawn by the orchestrator (shell/Icon.tsx); the rest is in the set already
      if (!["implicit", "vein"].includes(b.icon)) expect(renderToStaticMarkup(createElement(Icon, { name: b.icon }))).toMatch(/<path d="M/);
    }
  });
  it("every command is bound and available with a project", () => {
    const reason = (id: string) => app.registry.reason(id, ctx);
    for (const id of ["implicit.new", "implicit.domains", "implicit.save", "implicit.update", "implicit.shells", "implicit.vein", "implicit.expand", "implicit.structural", "implicit.to_wireframe", "implicit.delete", "implicit.check", "implicit.support", "implicit.compare_holes", "drillholes.from_model", "implicit.relations", "implicit.fault", "implicit.group_faults", "implicit.vein_system"]) expect(reason(id), id).toBeNull();
    for (const c of commands) expect(c.stage, c.id).toBeUndefined(); // every command of Э19 has its handler
    expect(app.registry.reason("implicit.new", { ...ctx, hasProject: false })).toBe("Откройте или создайте проект");
  });
  it("a project open for reading only: the commands that write say so", () => {
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("implicit.new", ctx)).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("implicit.update", ctx)).toBe("Проект открыт только для чтения");
  });
});

describe("implicit.new", () => {
  it("without arguments opens the wizard on the first step, with the defaults asked", async () => {
    const r = await app.registry.execute("implicit.new", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыт мастер «Новая модель»" });
    expect(wizards()).toHaveLength(1);
    expect(wizardStore.get()).toMatchObject({ step: 1, editing: null });
    await waitFor(() => wizardStore.get().defaults.phase === "ok");
  });
  it("one wizard of a new model: the second call brings the open one forward and keeps what is typed", async () => {
    await app.registry.execute("implicit.new", {}, "user");
    const id = wizardWindow();
    setForm({ name: "Моя" });
    await app.registry.execute("implicit.new", { h: 4 }, "user");
    expect(wizards()).toHaveLength(1);
    expect(wizardWindow()).toBe(id);
    expect(wizardStore.get().form).toMatchObject({ name: "Моя", h: "4" });
  });
  it("the arguments without a boundary prefill the wizard (nothing is created)", async () => {
    await app.registry.execute("implicit.new", { name: "Скарн", h: 2.5 }, "user");
    expect(wizardStore.get().form).toMatchObject({ name: "Скарн", h: "2,5" });
    expect(requests("POST /implicit/models")).toHaveLength(0);
  });
  it("with a box: POST /models, an empty model; nothing is built", async () => {
    const r = await app.registry.execute("implicit.new", { name: "Скарн", box: "0; 0; −300; 1000; 800; 100", h: 5 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Создана модель «Скарн» (№8)");
    const sent = requests("POST /implicit/models")[0].body;
    expect(sent).toMatchObject({ name: "Скарн", h: 5, boundary: { box: [0, 0, -300, 1000, 800, 100] }, groups: [], data: null });
    expect(requests(`POST /implicit/models/${ID.model2}/build`)).toHaveLength(0);
    expect(wizards()).toHaveLength(0);
    expect(implicitStore.get().rows.map((m) => m.id)).toContain(ID.model2);
  });
  it("with a wireframe: its id is the boundary", async () => {
    await app.registry.execute("implicit.new", { wireframe_id: ID.wire }, "claude");
    expect(requests("POST /implicit/models")[0].body.boundary).toEqual({ wireframe_id: ID.wire });
  });
  it("a wrong box is a refusal in Russian with the hint, nothing is sent", async () => {
    const a = await app.registry.execute("implicit.new", { box: "0; 0; 1" }, "claude");
    expect(a).toMatchObject({ ok: false });
    expect(a.result).toContain("нужно шесть чисел");
    expect(a.result).toContain("Шесть чисел через «;»");
    const b = await app.registry.execute("implicit.new", { box: "0; 0; 0; 10; 10; 10", h: 500 }, "claude");
    expect(b.ok).toBe(false);
    expect(b.result).toContain("Шаг h");
    expect(requests("POST /implicit/models")).toHaveLength(0);
  });
  it("the refusal of the core reaches the one who asked, with its hint", async () => {
    core.force("POST /implicit/models", { status: 422, body: { code: "boundary_empty", message: "Граница модели пуста.", hint: "Задайте охват или выберите замкнутый каркас границы." } });
    const r = await app.registry.execute("implicit.new", { box: "0; 0; 0; 10; 10; 10" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Граница модели пуста.");
    expect(r.result).toContain("замкнутый каркас");
  });
});

describe("implicit.domains", () => {
  it("opens the wizard of the model chosen in the tree on the step of the domains", async () => {
    await loadRows();
    app.store.set({ selected: { kind: "implicit", id: ID.model } as never });
    const r = await app.registry.execute("implicit.domains", {}, "user");
    expect(r.ok).toBe(true);
    expect(wizardStore.get()).toMatchObject({ step: 2, editing: { id: ID.model } });
    expect(wizardStore.get().form.groups.map((g) => g.name)).toEqual(["Дайка", "Песчаник", "Алевролит"]);
    expect(wizards()[0].title).toBe(`Модель «${NAME}»`);
  });
  it("the only model of the project is the model; several without a choice — a refusal that says what to do", async () => {
    await loadRows();
    await app.registry.execute("implicit.domains", {}, "user");
    expect(wizardStore.get().editing?.id).toBe(ID.model);
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    await loadRows();
    windowStore.set({ windows: [] });
    wizardStore.set({ dirty: false, editing: null });
    const r = await app.registry.execute("implicit.domains", {}, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("в проекте их несколько");
  });
  it("no model in the project: the domains are the second step of a new one", async () => {
    core.models.length = 0;
    await loadRows();
    const r = await app.registry.execute("implicit.domains", {}, "user");
    expect(r.ok).toBe(true);
    expect(wizardStore.get()).toMatchObject({ step: 2, editing: null });
  });
  it("a model that is not there", async () => {
    await loadRows();
    const r = await app.registry.execute("implicit.domains", { model_id: ID.noModel }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain(`№${ID.noModel} не найдена`);
  });
  it("with codes: POST /groups, a domain last, the name by default «Домен N»", async () => {
    await loadRows();
    const r = await app.registry.execute("implicit.domains", { model_id: ID.model, codes: "GR; GRc" }, "claude");
    expect(r.ok).toBe(true);
    const sent = requests(`POST /implicit/models/${ID.model}/groups`)[0].body;
    expect(sent).toEqual({ expected_version: core.version, group: { name: "Домен 4", type: "domain", codes: ["GR", "GRc"] } });
    expect(r.result).toContain("«Домен 4»");
    expect(r.result).toContain("последним");
    const named = await app.registry.execute("implicit.domains", { model_id: ID.model, codes: "GR", name: "Гранит" }, "claude");
    expect(named.ok).toBe(true);
    expect(requests(`POST /implicit/models/${ID.model}/groups`)[1].body.group).toMatchObject({ name: "Гранит" });
  });
  it("codes without a model: a refusal; a refusal of the core stands as it is", async () => {
    const a = await app.registry.execute("implicit.domains", { codes: "GR" }, "claude");
    expect(a.ok).toBe(false);
    expect(a.result).toContain("Модель не выбрана");
    await loadRows();
    core.force(`POST /implicit/models/${ID.model}/groups`, { status: 422, body: { code: "bad_param", param: "groups.0.codes.0", message: "Код «GR» уже есть в группе «Дайка».", hint: "Код входит в одну группу модели." } });
    const b = await app.registry.execute("implicit.domains", { model_id: ID.model, codes: "GR" }, "claude");
    expect(b.ok).toBe(false);
    expect(b.result).toContain("уже есть в группе");
  });
});

describe("implicit.save", () => {
  it("without a wizard there is nothing to save", async () => {
    const r = await app.registry.execute("implicit.save", {}, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Нет открытой формы модели");
  });
  it("writes the form of the wizard; as_new — a new model next to the open one", async () => {
    await app.registry.execute("implicit.new", {}, "user");
    putForm({ ...EMPTY_FORM, name: "Новая", box: ["0", "0", "0", "10", "10", "10"] });
    const r = await app.registry.execute("implicit.save", {}, "user");
    expect(r).toEqual({ ok: true, result: "Сохранено: «Новая», версия 1" });
    expect(requests("POST /implicit/models")).toHaveLength(1);
    // the model is the open one now: the next save is a new version of it (PUT with the version read), not a second model
    const again = await app.registry.execute("implicit.save", {}, "user");
    expect(requests("POST /implicit/models")).toHaveLength(1);
    expect(requests(`PUT /implicit/models/${ID.model2}`)[0].body.expected_version).toBe(1);
    expect(again.ok).toBe(false); // the stub keeps the version 3 for it: a 409, told in words
    expect(again.result).toContain("изменена другим окном");
  });
  it("a form with a wrong field is a refusal with the words of the field; the issue stays under the field", async () => {
    await app.registry.execute("implicit.new", {}, "user");
    putForm({ ...EMPTY_FORM, box: ["0", "0", "0", "10", "10", "10"], nMin: "10" });
    const r = await app.registry.execute("implicit.save", {}, "user");
    expect(r.ok).toBe(false);
    expect(wizardStore.get().issue).toMatchObject({ param: "pou.n_min" });
  });
  it("with build: the model is built after the save (a new model — «build», not «update»)", async () => {
    await app.registry.execute("implicit.new", {}, "user");
    putForm({ ...EMPTY_FORM, name: "Новая", box: ["0", "0", "0", "10", "10", "10"], setId: ID.set, table: ID.table, field: FIELD });
    core.job.result = buildResult({ model_id: ID.model2, version: 2 });
    const r = await app.registry.execute("implicit.save", { build: true }, "user");
    expect(r.ok).toBe(true);
    expect(requests("POST /implicit/models")).toHaveLength(1);
    expect(requests(`POST /implicit/models/${ID.model2}/build`)).toHaveLength(1);
    expect(requests(`POST /implicit/models/${ID.model2}/update`)).toHaveLength(0);
    expect(r.result).toContain("Сохранено: «Новая», версия 1. Модель «Новая» построена (версия 2)");
  });
});

describe("implicit.update", () => {
  it("a built model is updated (POST …/update with the version stored), the answer tells the result", async () => {
    await loadRows();
    const r = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(r.ok).toBe(true);
    expect(requests(`POST /implicit/models/${ID.model}/update`)[0].body).toEqual({ expected_version: core.version });
    expect(requests(`POST /implicit/models/${ID.model}/build`)).toHaveLength(0);
    expect(r.result).toContain(`Модель «${NAME}» обновлена (версия 4)`);
    expect(r.result).toContain("поверхностей 1, тел 2");
    expect(flat(r.result)).toContain("41 230 точек");
    expect(r.result).toContain("57 участков");
  });
  it("a model that was never built is built; the model of the tree is the model", async () => {
    core.models[0] = { ...core.models[0], stats: null, outputs: 0 } as never;
    await loadRows();
    app.store.set({ selected: { kind: "implicit", id: ID.model } as never });
    const r = await app.registry.execute("implicit.update", {}, "user");
    expect(r.ok).toBe(true);
    expect(requests(`POST /implicit/models/${ID.model}/build`)).toHaveLength(1);
    expect(r.result).toContain("построена");
  });
  it("the patches and the bricks taken from the earlier run are told", async () => {
    await loadRows();
    core.job.result = buildResult({ stats: { points: 41230, patches: 57, max_residual: 3.2e-9, cond_max: 2.4e7, eta_max: 0, seconds: 10.5, reused: { patches: 55, bricks: 1840 }, by_group: [] }, reused: { patches: 55, bricks: 1840 } });
    const r = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(flat(r.result)).toContain("взято участков 55, кирпичей 1 840");
    expect(r.result).toContain("наибольшая невязка 3,2e-9 м");
  });
  it("the checks of the surface failed (`iso_check`): the job is done, the answer is the refusal of the core with its words", async () => {
    await loadRows();
    core.job.result = buildResult({ state: "error", error: { code: "iso_check", group: 3, message: "Поверхность группы «Алевролит» не прошла проверки: самопересечения.", hint: "Уменьшите шаг h или проверьте данные группы." } });
    const r = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("«Алевролит» не прошла проверки");
    expect(r.result).toContain("Уменьшите шаг h");
  });
  it("the job failed, was cancelled", async () => {
    await loadRows();
    core.job = { status: "failed", result: null, error: { code: "rbf_singular", message: "Система RBF участка вырождена.", hint: "Включите сглаживание «Авто» или задайте s > 0." } };
    const f = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(f.ok).toBe(false);
    expect(f.result).toContain("вырождена");
    expect(f.result).toContain("«Авто»");
    core.job = { status: "cancelled", result: null, error: null };
    jobsStore.set({ jobs: [], workers: null, error: null }); // the same job number: the stub reuses it
    const c = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(c).toMatchObject({ ok: true });
    expect(c.result).toContain("отменено: ничего не записано");
  });
  it("the refusals before the job: a model that is building, a version that moved, a model that is not there, none chosen", async () => {
    await loadRows();
    core.force(`POST /implicit/models/${ID.model}/update`, { status: 409, body: { code: "version_conflict", version: 5, message: "Модель изменена другим окном.", hint: "Перечитайте модель." } });
    const a = await app.registry.execute("implicit.update", { model_id: ID.model }, "claude");
    expect(a.ok).toBe(false);
    expect(a.result).toContain("изменена другим окном");
    const b = await app.registry.execute("implicit.update", { model_id: ID.noModel }, "claude");
    expect(b.result).toContain(`№${ID.noModel} не найдена`);
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    await loadRows();
    const c = await app.registry.execute("implicit.update", {}, "claude");
    expect(c.result).toContain("Модель не выбрана");
  });
  it("the list follows the core: `implicit.changed` reads it again, another project clears it", async () => {
    await loadRows();
    expect(implicitStore.get().rows).toHaveLength(1);
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    const { jobsStore } = await import("../../src/modules/jobs/state");
    void jobsStore;
    const { projectStore } = await import("../../src/modules/projects/state");
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    await waitFor(() => implicitStore.get().rows.length === 2);
    expect(implicitStore.get().loaded).toBe(true);
  });
});

describe("the wizard guards its changes", () => {
  it("the wizard of another model does not replace the form the person has changed without asking", async () => {
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    await loadRows();
    await app.registry.execute("implicit.domains", { model_id: ID.model }, "user");
    setForm({ name: "Изменено" });
    const p = app.registry.execute("implicit.domains", { model_id: 9 }, "user");
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    expect(await p).toEqual({ ok: true, result: "Отменено" });
    expect(wizardStore.get().form.name).toBe("Изменено");
    expect(wizardStore.get().editing?.id).toBe(ID.model);
  });
  it("«Заменить» takes the other model; a call of the assistant is not asked (it has its own confirmation)", async () => {
    core.models.push({ ...core.models[0], id: 9, name: "Вторая" });
    await loadRows();
    await app.registry.execute("implicit.domains", { model_id: ID.model }, "user");
    setForm({ name: "Изменено" });
    const r = await app.registry.execute("implicit.domains", { model_id: ID.model }, "claude");
    expect(r.ok).toBe(true);
    expect(dialogStore.get().queue).toHaveLength(0);
  });
});
