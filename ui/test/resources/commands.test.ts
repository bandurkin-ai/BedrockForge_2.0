// The commands `resources.*` and the window «Отчёт по ресурсам» (Э20, 20.12; ADR 0027 решения 16, 29–30; docs/progress/e20-api.md §5, §13) through a stub
// of the core that answers with the answers of `core/tests/data/resources/contract_answers.json`: the declarations (the ribbon, Ctrl+K and the
// assistant read them), the request of the window = the body the core gets, the refusals 404 / 422 / 423 in Russian with a hint in their place
// of the form, the preview in a project open for reading, the save, «Отменить», the export, the drill, the comparison, the deletion.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { moduleCommands } from "../../src/shell/catalog";
import { commandTools } from "../../src/shell/commands";
import { closeDialog, dialogStore } from "../../src/shell/dialogs";
import { projectStore } from "../../src/modules/projects/state";
import { windowStore } from "../../src/shell/windows";
import { jobsStore } from "../../src/modules/jobs/state";
import { cancelReport, openReportWindow, renderResourcesWindow, windowOfReport } from "../../src/modules/resources/bind";
import { commands, RESOURCES_BUTTONS } from "../../src/modules/resources/commands";
import { argsOfForm, EMPTY_FORM, paramsOfForm, type ResourceForm } from "../../src/modules/resources/form";
import { ReportWindow } from "../../src/modules/resources/ReportWindow";
import { currentForm, currentRun, resourcesStore, RESOURCES_WINDOW, setForm } from "../../src/modules/resources/state";
import { answer, FILE, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(() => {
  core = installCore();
  ({ app, unbind } = newApp());
});
afterEach(() => teardown(unbind));

const lists = () => core.sent.filter((r) => r.method === "GET" && r.path === "/resources/reports").length;
const posts = () => core.sent.filter((r) => r.method === "POST" && r.path === "/resources/reports");
const windows = () => windowStore.get().windows.filter((w) => w.kind === RESOURCES_WINDOW);
const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ReportWindow, { winId: windowOfReport() ?? "w" })));
const form = (patch: Partial<ResourceForm> = {}): ResourceForm => ({ ...EMPTY_FORM, modelId: ID.model, ...patch });
const built = (f: ResourceForm) => {
  const r = paramsOfForm(f);
  if (!r.ok) throw new Error(JSON.stringify(r.issue));
  return r.built;
};

describe("the declarations", () => {
  const ids = commands.map((c) => c.id);
  it("the commands of ADR 0027 решение 29 (and the two of the panel: `open`, `rename`), once in the catalog; the signs of the contract", () => {
    expect(ids).toEqual(["resources.report", "resources.curve", "resources.templates", "resources.export", "resources.open", "resources.rename", "resources.delete", "resources.drill", "resources.compare"]);
    const catalog = moduleCommands().map((c) => c.id);
    for (const id of ids) expect(catalog.filter((x) => x === id)).toHaveLength(1);
    const by = Object.fromEntries(commands.map((c) => [c.id, c]));
    expect(by["resources.templates"].uiOnly).toBe(true);
    expect(by["resources.delete"].confirm).toBe(true);
    expect(by["resources.report"].required).toBeUndefined(); // without a model the window opens
    expect(by["resources.export"].required).toEqual(["format", "path"]);
    expect(by["resources.compare"].required).toEqual(["report_a", "report_b"]);
    for (const c of commands) {
      expect(c.title, c.id).toMatch(/[А-Яа-я]/);
      expect(c.description, c.id).toMatch(/[А-Яа-я]/);
      expect(c.id.startsWith("resources."), c.id).toBe(true);
    }
  });
  it("the assistant sees the commands (all but the window of the templates), the parameters in flat words; no key is declared", () => {
    const names = commandTools(commands).map((t) => t.name);
    expect(names).toContain("ui_resources_report");
    expect(names).not.toContain("ui_resources_templates");
    const report = commandTools(commands).find((t) => t.name === "ui_resources_report");
    expect(Object.keys(report?.input_schema.properties ?? {})).toEqual(expect.arrayContaining(["model_id", "elements", "cutoffs", "groups", "restrict", "save", "template_id"]));
    expect(JSON.stringify(commands)).not.toMatch(/"key"/);
  });
  it("every button of the ribbon is a command of the module, with a Russian label and a hint", () => {
    for (const b of RESOURCES_BUTTONS) {
      expect(ids, b.command).toContain(b.command);
      expect(b.label + b.hint, b.command).toMatch(/[А-Яа-я]/);
    }
    expect(RESOURCES_BUTTONS[0].command).toBe("resources.report");
  });
  it("`resources.templates` is bound by `bindResources` (the window of 20.14): available with a project open", () => {
    expect(app.registry.reason("resources.templates")).toBeNull();
  });
});

describe("`resources.report`", () => {
  it("without a model it opens the window on the model chosen in the tree, one window", async () => {
    app.store.set({ selected: { kind: "blocks", id: ID.model } });
    const r = await app.registry.execute("resources.report", {}, "user");
    expect(r.ok).toBe(true);
    expect(windows()).toHaveLength(1);
    expect(windows()[0].title).toBe("Отчёт по ресурсам · Рудное тело");
    expect(currentForm().modelId).toBe(ID.model);
    await app.registry.execute("resources.report", {}, "user");
    expect(windows()).toHaveLength(1);
    expect(posts()).toHaveLength(0);
  });

  it("the preview: the request is the body of the form; nothing is saved; the result and the text for the log", async () => {
    const f = form({ elements: [{ field: "AU", unit: "г/т" }, { field: "CU", unit: "" }], cutoffs: "без борта; 1", groups: [{ kind: "field", field: "CLASS" }], curvePoints: "5", templateId: "jorc", densityMode: "value", densityValue: "2,5" });
    const a = argsOfForm(f, false);
    if (!a.ok) throw new Error("form");
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const before = lists();
    const r = await app.registry.execute("resources.report", a.args, "user");
    expect(r.ok).toBe(true);
    expect(posts()).toHaveLength(1);
    expect(posts()[0].body).toEqual({ params: built(f).params, save: false }); // the window sends what the form means, nothing else
    expect(posts()[0].body.params).toMatchObject({ model_id: ID.model, cutoffs: [null, 1], groups: ["CLASS"], density: 2.5, curve: 5, template_id: "jorc" });
    expect(currentRun()).toMatchObject({ phase: "done", saved: false, modelName: "Рудное тело" });
    expect(currentRun().result?.rows.length).toBe(8);
    expect(r.result).toContain("Предпросмотр");
    expect(r.result).toContain("не записан");
    expect(r.result).toMatch(/Итог \(.+\): блоков/);
    expect(lists()).toBe(before); // the list is read again after a save only
  });

  it("the window keeps its form when it made the call («Карьер» and «Ниже рельефа» are shortcuts of the form)", async () => {
    const f = form({ pit: { ...EMPTY_FORM.pit, on: true, n: "2" }, below: { on: true, surfaceId: ID.dtm, by: "frac" } });
    resourcesStore.set({ form: f });
    const a = argsOfForm(currentForm(), false);
    if (!a.ok) throw new Error("form");
    await app.registry.execute("resources.report", a.args, "user");
    expect(currentForm().pit.on).toBe(true);
    expect(currentForm().below.on).toBe(true);
    expect(posts()[0].body.params).toMatchObject({ restrict: { op: "and", items: [{ expr: "PIT <= 2" }, { surface: { id: ID.dtm, side: "below", by: "frac" } }] } });
  });

  it("a call of the assistant shows in the window: the form is the arguments", async () => {
    await app.registry.execute("resources.report", { model_id: ID.model, elements: "AU:г/т", cutoffs: "без борта; 0,5", groups: "CLASS; уступ", bench_h: 5, curve: 0 }, "claude");
    expect(currentForm()).toMatchObject({ elements: [{ field: "AU", unit: "г/т" }], cutoffs: "без борта; 0,5", curveOn: false, benchH: "5" });
    expect(currentForm().groups).toEqual([{ kind: "field", field: "CLASS" }, { kind: "bench" }]);
    expect(posts()[0].body.params).toMatchObject({ groups: ["CLASS", { kind: "bench" }], bench: { h: 5 }, curve: 0 });
  });

  it("«Сохранить»: save = true with the name; the list of the reports is read again; the text names the report", async () => {
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const before = lists();
    const r = await app.registry.execute("resources.report", { model_id: ID.model, save: true, name: "Ресурсы Q4", curve: 0 }, "user");
    expect(r.ok).toBe(true);
    expect(posts()[0].body).toMatchObject({ save: true, name: "Ресурсы Q4" });
    expect(currentRun()).toMatchObject({ phase: "done", saved: true });
    expect(r.result).toContain("записан в проект");
    await waitFor(() => lists() > before);
  });

  it("in a project open for reading the preview works and the save is refused, in Russian, with nothing sent", async () => {
    app.store.set({ projectReadOnly: true });
    const save = await app.registry.execute("resources.report", { model_id: ID.model, save: true }, "user");
    expect(save.ok).toBe(false);
    expect(save.result).toMatch(/только для чтения/);
    expect(posts()).toHaveLength(0);
    const preview = await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    expect(preview.ok).toBe(true);
    expect(posts()).toHaveLength(1);
    expect(html()).toMatch(/data-testid="res-save"[^>]*disabled|disabled=""[^>]*data-testid="res-save"/); // the button says why: «Проект открыт только для чтения»
  });

  it("a wrong argument is told in Russian with a hint and the place before the core is asked", async () => {
    const r = await app.registry.execute("resources.report", { model_id: ID.model, cutoffs: "0; много" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/не число/);
    expect(resourcesStore.get().issue?.field).toBe("cutoffs");
    expect(posts()).toHaveLength(0);
    const no = await app.registry.execute("resources.report", { model_id: 999, curve: 0 }, "claude");
    expect(no.result).toBe("Блочная модель №999 не найдена. Номера моделей — в разделе «Блочные модели» дерева.");
  });

  it("a 422 of the core is shown in Russian with its hint in its place of the form: the numeric key of a group", async () => {
    const gn = answer("group_numeric");
    core.force("POST /resources/reports", gn);
    const r = await app.registry.execute("resources.report", { model_id: ID.model, groups: "AU", curve: 0 }, "user");
    expect(r.ok).toBe(false);
    const body = gn.body as { message: string; hint: string };
    expect(r.result).toBe(`${body.message} ${body.hint}`);
    expect(resourcesStore.get().issue).toMatchObject({ field: "groups", message: body.message, hint: body.hint });
    expect(currentRun()).toMatchObject({ phase: "failed", error: { code: "group_numeric" } });
    const text = html();
    expect(text).toContain(body.message);
    expect(text).toContain(body.hint);
    expect(text).toContain('data-testid="res-issue-groups"');
  });

  it("a 422 with the param of the body: «Бортов больше 200» stands under the cut-offs", async () => {
    const bp = answer("bad_param");
    core.force("POST /resources/reports", bp);
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    expect(resourcesStore.get().issue?.field).toBe("cutoffs");
    expect(html()).toContain('data-testid="res-issue-cutoffs"');
  });

  it("the refusals of the core named by their code: no filter, no field PIT (Э21), too big a report", async () => {
    const refusals = FILE.refusals as Record<string, { status: number; body: { message: string; hint: string } }>;
    for (const [code, field] of [["filter_not_found", "filter"], ["report_too_big", "groups"]] as const) {
      core.force("POST /resources/reports", refusals[code]);
      const r = await app.registry.execute("resources.report", { model_id: ID.model, curve: 0, filter_id: ID.filter }, "user");
      expect(r.ok, code).toBe(false);
      expect(r.result).toBe(`${refusals[code].body.message} ${refusals[code].body.hint}`);
      expect(resourcesStore.get().issue?.field, code).toBe(field);
    }
    core.force("POST /resources/reports", answer("filter_field_missing"));
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0, restrict: JSON.stringify({ cond: { field: "PIT", op: "<=", value: 1 } }) }, "user");
    expect(resourcesStore.get().issue).toMatchObject({ field: "restrict" });
    expect(JSON.stringify(resourcesStore.get().issue)).toMatch(/PIT/);
  });

  it("a job that failed is told with its words; a job that is cancelled leaves nothing", async () => {
    core.job = { status: "failed", result: null, error: { code: "report_too_big", message: "Слишком велик.", hint: "Уберите ключ." } };
    const r = await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    expect(r).toEqual({ ok: false, result: "Слишком велик. Уберите ключ." });
    expect(currentRun().phase).toBe("failed");
    core.job = { status: "running", result: null, error: null };
    jobsStore.set({ jobs: [], workers: null, error: null }); // the core numbers its jobs anew; the stub keeps one number
    const run = app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    await vi.waitFor(() => expect(currentRun().job).toEqual(ID.reportJob));
    const busy = await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "claude");
    expect(busy.result).toBe("Отчёт уже строится. Дождитесь конца или отмените его в окне «Отчёт по ресурсам».");
    await cancelReport();
    const done = await run;
    expect(core.sent.some((x) => x.path === "/jobs/jobs/41/cancel" && x.method === "POST")).toBe(true);
    expect(done).toEqual({ ok: true, result: "Отчёт по модели «Рудное тело» отменён: ничего не записано" });
    expect(currentRun()).toMatchObject({ phase: "cancelled", result: null });
  });

  it("another project: the window starts clean (the form and the result of the first one do not stand beside the second)", async () => {
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    expect(currentRun().phase).toBe("done");
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 1 });
    expect(currentRun().phase).toBe("idle");
    expect(currentForm()).toEqual(EMPTY_FORM);
  });
});

describe("the window", () => {
  it("draws the form: the elements with the units, the cut-offs, the balance, the keys of the group, the shortcuts of the restrictor", async () => {
    await app.registry.execute("resources.report", {}, "user");
    setForm({ elements: [{ field: "AU", unit: "г/т" }], groups: [{ kind: "field", field: "CLASS" }, { kind: "bench" }], balanceOn: true, pit: { ...EMPTY_FORM.pit, on: true }, below: { on: true, surfaceId: null, by: "centre" } });
    const text = html();
    for (const id of ["res-window", "res-model", "res-elements", "res-element-AU", "res-unit-AU", "res-cutoff-field", "res-cutoffs", "res-balance-cutoff", "res-density-auto", "res-filter", "res-pit-on", "res-pit-n", "res-below-on", "res-below-surface", "res-restrict-on", "res-keys-available", "res-keys-chosen", "res-key-chosen-bench", "res-bench-h", "res-curve-on", "res-template", "res-name", "res-preview", "res-save", "res-close"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).toContain("Ниже рельефа");
    expect(text).toContain("Уступ");
    expect(text).toContain("нет ЦТМ в проекте");
  });
  it("the cut-offs as a range: three fields", async () => {
    await app.registry.execute("resources.report", {}, "user");
    setForm({ cutoffMode: "range" });
    const text = html();
    for (const id of ["res-rangeFrom", "res-rangeTo", "res-rangeStep"]) expect(text, id).toContain(`data-testid="${id}"`);
    expect(text).not.toContain('data-testid="res-cutoffs"');
  });
  it("the templates of the core are in the list; «без формы» is first", async () => {
    await app.registry.execute("resources.report", {}, "user");
    await waitFor(() => resourcesStore.get().templatesLoaded);
    const text = html();
    expect(text).toContain("JORC");
    expect(text).toContain("ГКЗ");
    expect(text.indexOf("без формы")).toBeLessThan(text.indexOf("JORC"));
  });
  it("the form of the window is the same as the arguments of the command: no extra key", async () => {
    openReportWindow(app);
    const a = argsOfForm(form(), false);
    expect(a.ok && a.args).toEqual({ model_id: ID.model, save: false, cutoffs: "без борта", curve: 100 });
  });
  it("the summary of the result: the total, the element, the warnings; the idle window says what to do", async () => {
    await app.registry.execute("resources.report", {}, "user");
    expect(html()).toContain('data-testid="res-idle"');
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    const text = html();
    expect(text).toContain('data-testid="res-summary"');
    expect(text).toContain('data-testid="res-total"');
    expect(text).toContain('data-testid="res-element-total-AU"');
    expect(text).toContain("Предпросмотр: отчёт не записан");
  });
  it("the shell draws the window of the module and nobody else's", async () => {
    await app.registry.execute("resources.report", {}, "user");
    const w = windows()[0];
    expect(renderResourcesWindow(w)).not.toBeNull();
    expect(renderResourcesWindow({ ...w, kind: "other" })).toBeNull();
  });
});

describe("the other commands", () => {
  it("`resources.curve`: the curve of a saved report as text; the preview of the window without an id", async () => {
    const r = await app.registry.execute("resources.curve", { report_id: ID.report }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/Кривая «тоннаж — содержание»/);
    expect(core.sent.some((x) => x.path === `/resources/reports/${ID.report}`)).toBe(true);
    const none = await app.registry.execute("resources.curve", {}, "claude");
    expect(none.ok).toBe(false);
    expect(none.result).toMatch(/Не задан отчёт/);
    await app.registry.execute("resources.report", { model_id: ID.model }, "user");
    const preview = await app.registry.execute("resources.curve", {}, "claude");
    expect(preview.ok).toBe(true);
    expect(preview.result).toMatch(/бортов по полю AU/);
  });

  it("`resources.export`: the job, the file written, the words of the core when the file is there", async () => {
    const r = await app.registry.execute("resources.export", { report_id: ID.report, format: "xlsx", path: "C:\\Отчёты\\resources.xlsx", template: "jorc", cutoff: 1 }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("C:\\Отчёты\\resources.xlsx");
    expect(core.sent.find((x) => x.path === `/resources/reports/${ID.report}/export`)?.body).toEqual({ format: "xlsx", path: "C:\\Отчёты\\resources.xlsx", template: "jorc", cutoff: 1 });
    const exists = answer("files_exist");
    core.force(`POST /resources/reports/${ID.report}/export`, exists);
    const e = await app.registry.execute("resources.export", { report_id: ID.report, format: "csv", path: "C:\\Отчёты\\resources.csv" }, "user");
    expect(e.ok).toBe(false);
    expect(e.result).toBe(`${(exists.body as { message: string }).message} ${(exists.body as { hint: string }).hint}`);
    const bad = await app.registry.execute("resources.export", { report_id: ID.report, format: "pdf", path: "x" }, "claude");
    expect(bad.ok).toBe(false);
    const blank = await app.registry.execute("resources.export", { report_id: ID.report, format: "csv", path: "  " }, "claude");
    expect(blank.result).toMatch(/Не задан путь/);
  });

  it("`resources.delete`: the user is asked, the answer «Отмена» leaves the report; «Удалить» deletes it", async () => {
    const answerDialog = async (ok: boolean) => {
      await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
      const d = dialogStore.get().queue[0];
      expect(d.kind).toBe("confirm");
      if (d.kind === "confirm") {
        expect(d.options.title).toBe("Удалить отчёт по ресурсам");
        d.resolve(ok);
      }
      closeDialog(d.id);
    };
    const run = app.registry.execute("resources.delete", { report_id: ID.report }, "user");
    await answerDialog(false);
    expect((await run).result).toBe("Отменено");
    expect(core.sent.some((x) => x.method === "DELETE")).toBe(false);
    const again = app.registry.execute("resources.delete", { report_id: ID.report }, "user");
    await answerDialog(true);
    const r = await again;
    expect(r.ok).toBe(true);
    expect(r.result).toContain("удалён");
    expect(core.sent.some((x) => x.method === "DELETE" && x.path === `/resources/reports/${ID.report}`)).toBe(true);
  });

  it("`resources.delete` in a project open for reading is not available; an unknown report is a refusal in Russian", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("resources.delete", { report_id: ID.report }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/только для чтения/);
    app.store.set({ projectReadOnly: false });
    await app.registry.execute("resources.report", {}, "user");
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const no = await app.registry.execute("resources.drill", { report_id: ID.noReport }, "claude");
    expect(no.ok).toBe(false);
    expect(no.result).toBe("Отчёт по ресурсам №999 не найден. Номера отчётов — в разделе «Отчёты» → «Отчёты по ресурсам» дерева.");
  });

  it("`resources.drill`: the numbers of the cell and the rules for the filter; the labels of the keys are a list; works in a project open for reading", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("resources.drill", { report_id: ID.report, group: "Измеренные; (пусто)", cutoff: 1 }, "claude");
    expect(r.ok).toBe(true);
    expect(core.sent.find((x) => x.path === `/resources/reports/${ID.report}/drill`)?.body).toEqual({ group: ["Измеренные", "(пусто)"], cutoff: 1 });
    expect(r.result).toMatch(/Ячейка отчёта .*: блоков 1, объём 1\s000 м³, тоннаж 2\s500 т/);
    expect(r.result).toContain('"cond"');
    const total = await app.registry.execute("resources.drill", { report_id: ID.report }, "claude");
    expect(total.ok).toBe(true);
    expect(core.sent.filter((x) => x.path.endsWith("/drill")).at(-1)?.body).toEqual({ group: null, cutoff: null });
  });

  it("`resources.drill` of a report that is out of date warns that the set of blocks may differ", async () => {
    core.force(`POST /resources/reports/${ID.report}/drill`, { status: 200, body: { ...(answer("drill").body as object), stale: true, stale_reason: "изменена таблица проб" } });
    const r = await app.registry.execute("resources.drill", { report_id: ID.report, cutoff: 1 }, "claude");
    expect(r.result).toMatch(/Отчёт устарел \(изменена таблица проб\): набор блоков может отличаться/);
  });

  it("`resources.compare`: the report against itself has no difference; a gap is not 0", async () => {
    const r = await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report }, "claude");
    expect(r.ok).toBe(true);
    expect(core.sent.find((x) => x.path === "/resources/compare")?.body).toEqual({ report_a: ID.report, report_b: ID.report });
    expect(r.result).toContain("Различий нет");
    const base = answer("compare_reports").body as { rows: { only_in: string | null; delta: unknown }[] };
    core.force("POST /resources/compare", { status: 200, body: { ...base, grids_differ: true, rows: [{ ...base.rows[0], only_in: "a", delta: null }, ...base.rows.slice(1)] } });
    const g = await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report }, "claude");
    expect(g.result).toContain("Сетки различаются");
    expect(g.result).toMatch(/Строки только в одном отчёте: 1 \(у них нет разности — это пропуск, не 0\)/);
  });
});
