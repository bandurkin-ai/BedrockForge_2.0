// The bottom tab «Ресурсы» in motion (Э20, 20.13; ТЗ 8.10; ADR 0027 решения 16, 19, 20, 29): `resources.open` shows a saved report (the core makes the form again for a template, a cut-off and
// a page of another choice) or the result of the window, a run that ends goes to the panel, a report changed or deleted in the core is read again or leaves it, the file is chosen in the window of
// the system and written by `resources.export` (XLSX; CSV of the table or of the form), `resources.rename` (the words of the core when the name is taken). The core is a stub that answers with
// the real answers of `core/tests/data/resources/contract_answers.json`.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "../../src/shell/app";
import { projectStore } from "../../src/modules/projects/state";
import { exportArgs, exportFileName, saveReportFile } from "../../src/modules/resources/exportFile";
import { onResourcesEvent } from "../../src/modules/resources/panelBind";
import { chooseCutoff, choosePage, chooseTemplate, INITIAL_PANEL, openReport, PAGE_SIZE, pageCount, pageRows, panelStore, reportChanged, resetPanel, RESOURCES_TAB, setNote, shownReportId, shownResult } from "../../src/modules/resources/panelState";
import { resourcesStore } from "../../src/modules/resources/state";
import { answer, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(() => {
  core = installCore();
  ({ app, unbind } = newApp());
  resetPanel();
});
afterEach(() => teardown(unbind));

const gets = () => core.sent.filter((r) => r.method === "GET" && r.path === `/resources/reports/${ID.report}`);
const bottom = () => app.store.get().workspace;

describe("`resources.open`", () => {
  it("a saved report is read with a page of 500 and shown in the bottom tab «Ресурсы»", async () => {
    const r = await app.registry.execute("resources.open", { report_id: ID.report }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("записан в проект");
    expect(gets()).toHaveLength(1);
    expect(new URLSearchParams(gets()[0].query).get("size")).toBe(String(PAGE_SIZE));
    expect(panelStore.get()).toMatchObject({ mode: "report", reportId: ID.report, loading: false, error: null });
    expect(shownResult()?.form?.template_id).toBe("jorc");
    expect(shownReportId()).toBe(2);
    expect(bottom()).toMatchObject({ bottom: true, bottomTab: RESOURCES_TAB });
  });

  it("a template, a cut-off and a page of another choice are asked of the core: the form is the core's, made again", async () => {
    await app.registry.execute("resources.open", { report_id: ID.report }, "user");
    await chooseTemplate("gkz");
    let q = new URLSearchParams(gets().at(-1)?.query);
    expect(q.get("template")).toBe("gkz");
    expect(panelStore.get().template).toBe("gkz");
    await chooseCutoff(1);
    q = new URLSearchParams(gets().at(-1)?.query);
    expect([q.get("template"), q.get("cutoff")]).toEqual(["gkz", "1"]);
    await choosePage(2);
    q = new URLSearchParams(gets().at(-1)?.query);
    expect(q.get("page")).toBe("2");
    expect(panelStore.get().page).toBe(2);
    // the same report again keeps the choice; another report starts clean
    await openReport(ID.report);
    expect(panelStore.get().template).toBe("gkz");
    const opened = await app.registry.execute("resources.open", { report_id: ID.report, template: "jorc", cutoff: 1 }, "user");
    expect(opened.ok).toBe(true);
    q = new URLSearchParams(gets().at(-1)?.query);
    expect([q.get("template"), q.get("cutoff")]).toEqual(["jorc", "1"]);
  });

  it("without a report: the result of the window (the preview); with nothing — a refusal in Russian with the way out", async () => {
    const none = await app.registry.execute("resources.open", {}, "claude");
    expect(none.ok).toBe(false);
    expect(none.result).toMatch(/Нет отчёта для показа/);
    expect(none.result).toMatch(/Предпросмотр/);
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    expect(resourcesStore.get().run.phase).toBe("done");
    const r = await app.registry.execute("resources.open", {}, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Предпросмотр");
    expect(panelStore.get().mode).toBe("run");
    expect(shownResult()).toBe(resourcesStore.get().run.result);
    expect(shownReportId()).toBeNull(); // a preview is not in the project: no file, no template of another choice
    await chooseTemplate("gkz"); // asked of nobody: the core keeps nothing of a preview
    expect(gets()).toHaveLength(0);
  });

  it("the saved result of the window with a template asked is the saved report: the core makes the form", async () => {
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0, save: true }, "user");
    expect(resourcesStore.get().run.result?.saved).toBe(true);
    expect(shownReportId()).toBe(2);
    const r = await app.registry.execute("resources.open", { template: "gkz" }, "claude");
    expect(r.ok).toBe(true);
    expect(new URLSearchParams(gets().at(-1)?.query).get("template")).toBe("gkz");
    expect(panelStore.get().mode).toBe("report");
  });

  it("an unknown report is the words of the core; the panel keeps the error", async () => {
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const no = await app.registry.execute("resources.open", { report_id: ID.noReport }, "claude");
    expect(no.ok).toBe(false);
    expect(no.result).toMatch(/№999 не найден/);
    core.force(`GET /resources/reports/${ID.report}`, answer("report_not_found"));
    const gone = await app.registry.execute("resources.open", { report_id: ID.report }, "claude");
    expect(gone.ok).toBe(false);
    expect(gone.result).toContain("Отчёт по ресурсам не найден.");
    expect(panelStore.get().error).toContain("Обновите раздел");
    expect(panelStore.get().report).toBeNull();
  });

  it("without a project the command is not available", async () => {
    projectStore.set({ current: null, version: 0 });
    const r = await app.registry.execute("resources.open", {}, "claude");
    expect(r.ok).toBe(false);
  });
});

describe("the panel follows the run and the core", () => {
  it("a run that ends puts its result in the panel (the report opened from the tree is left)", async () => {
    await app.registry.execute("resources.open", { report_id: ID.report }, "user");
    expect(panelStore.get().mode).toBe("report");
    await app.registry.execute("resources.report", { model_id: ID.model, curve: 0 }, "user");
    await waitFor(() => panelStore.get().mode === "run");
    expect(shownResult()?.saved).toBe(false);
  });

  it("`resources.changed` of the report in the panel reads it again; a deletion takes it out of the panel; another report's event does nothing", async () => {
    await app.registry.execute("resources.open", { report_id: ID.report }, "user");
    const n = gets().length;
    onResourcesEvent("resources.changed", JSON.stringify({ kind: "resource_report", id: 77 }));
    onResourcesEvent("resources.changed", "не JSON");
    onResourcesEvent("jobs.changed", JSON.stringify({ kind: "resource_report", id: ID.report }));
    expect(gets()).toHaveLength(n);
    onResourcesEvent("resources.changed", JSON.stringify({ kind: "resource_report", id: ID.report }));
    await waitFor(() => gets().length === n + 1);
    onResourcesEvent("resources.changed", JSON.stringify({ kind: "resource_report", id: ID.report, deleted: true }));
    expect(panelStore.get()).toMatchObject({ mode: "run", reportId: null, report: null });
    reportChanged(ID.report, false); // nothing is shown: nothing is read
    expect(gets()).toHaveLength(n + 1);
  });

  it("a read that was overtaken does not write its end", async () => {
    const a = openReport(ID.report);
    const b = openReport(ID.report, { template: "gkz" });
    expect(await a).toBeNull();
    expect((await b)?.report_id).toBe(2);
    expect(panelStore.get()).toMatchObject({ template: "gkz", loading: false });
  });

  it("another project: the panel is clean", () => {
    panelStore.set({ mode: "report", reportId: 2, tab: "curve", template: "gkz" });
    resetPanel();
    expect(panelStore.get()).toMatchObject({ mode: INITIAL_PANEL.mode, reportId: null, tab: "form", template: "" });
  });

  it("the pages: a preview is cut here, the pages are counted", () => {
    const rows = Array.from({ length: 1201 }, (_, i) => i);
    expect(pageCount(0)).toBe(1);
    expect(pageCount(1201)).toBe(3);
    expect(pageRows(rows, { mode: "run", page: 3 })).toEqual(rows.slice(1000));
    expect(pageRows(rows.slice(0, 500), { mode: "report", page: 2 })).toHaveLength(500); // a saved report's rows are the page already
  });
});

describe("the files: the window of the system, then `resources.export`", () => {
  const pick = (path: string | null) => vi.stubGlobal("window", { bf: { pickSave: vi.fn(async () => path) } });
  const exports = () => core.sent.filter((r) => r.method === "POST" && r.path === `/resources/reports/${ID.report}/export`);

  it("the name offered is the report's, without the signs Windows does not allow; the form CSV has its own", () => {
    expect(exportFileName("Ресурсы: ЮЗ/Север", "xlsx")).toBe("Ресурсы_ ЮЗ_Север.xlsx");
    expect(exportFileName("  ", "csv")).toBe("Отчёт по ресурсам.csv");
    expect(exportFileName("Отчёт", "csv", "form")).toBe("Отчёт (форма).csv");
    expect(exportFileName("Отчёт", "xlsx", "form")).toBe("Отчёт.xlsx"); // `what` is of the CSV only
  });

  it("XLSX: the file is chosen, the command writes it (the template and the cut-off of the panel go with it)", async () => {
    pick("C:\\Отчёты\\ресурсы.xlsx");
    const note = await saveReportFile(app, { id: ID.report, name: "Отчёт по ресурсам", format: "xlsx", template: "gkz", cutoff: 1 });
    expect(note).toEqual({ text: expect.stringContaining("C:\\Отчёты\\resources.xlsx"), error: false });
    expect(exports()[0].body).toEqual({ format: "xlsx", path: "C:\\Отчёты\\ресурсы.xlsx", template: "gkz", cutoff: 1, overwrite: true });
    expect((window as unknown as { bf: { pickSave: ReturnType<typeof vi.fn> } }).bf.pickSave).toHaveBeenCalledWith("Отчёт по ресурсам.xlsx", "xlsx");
  });

  it("CSV of the table is the default, CSV of the form says `what`; the name of the form's file tells it", async () => {
    pick("C:\\Отчёты\\таблица.csv");
    await saveReportFile(app, { id: ID.report, name: "Отчёт", format: "csv", what: "table" });
    expect(exports()[0].body).toEqual({ format: "csv", path: "C:\\Отчёты\\таблица.csv", overwrite: true });
    pick("C:\\Отчёты\\форма.csv");
    await saveReportFile(app, { id: ID.report, name: "Отчёт", format: "csv", what: "form" });
    expect(exports()[1].body).toMatchObject({ format: "csv", what: "form" });
    expect((window as unknown as { bf: { pickSave: ReturnType<typeof vi.fn> } }).bf.pickSave).toHaveBeenCalledWith("Отчёт (форма).csv", "csv");
    expect(exportArgs({ id: 2, name: "x", format: "xlsx", what: "form", cutoff: null }, "p")).toEqual({ report_id: 2, format: "xlsx", path: "p", overwrite: true, cutoff: null });
  });

  it("a window closed writes nothing; a refusal of the core is told in its words; no bridge is told", async () => {
    pick(null);
    expect(await saveReportFile(app, { id: ID.report, name: "Отчёт", format: "xlsx" })).toBeNull();
    expect(exports()).toHaveLength(0);
    pick("C:\\Отчёты\\a.xlsx");
    core.force(`POST /resources/reports/${ID.report}/export`, answer("files_exist"));
    const note = await saveReportFile(app, { id: ID.report, name: "Отчёт", format: "xlsx" });
    expect(note?.error).toBe(true);
    expect(note?.text).toContain("Файл с таким именем уже есть");
    vi.stubGlobal("window", {});
    expect(await saveReportFile(app, { id: ID.report, name: "Отчёт", format: "xlsx" })).toEqual({ text: "Сохранение файлов доступно только в приложении.", error: true });
  });

  it("the note of an action is kept in the panel", () => {
    setNote({ text: "Готово", error: false });
    expect(panelStore.get().note).toEqual({ text: "Готово", error: false });
  });
});

describe("`resources.rename`", () => {
  const puts = () => core.sent.filter((r) => r.method === "PUT" && r.path === `/resources/reports/${ID.report}`);

  it("the new name goes with the version of the list; the list is read again", async () => {
    await waitFor(() => resourcesStore.get().reportsLoaded);
    core.force(`PUT /resources/reports/${ID.report}`, answer("report_put"));
    const r = await app.registry.execute("resources.rename", { report_id: ID.report, name: "  Ресурсы ЮЗ " }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("«Ресурсы ЮЗ»");
    expect(puts()[0].body).toEqual({ name: "Ресурсы ЮЗ", expected_version: 1 });
  });

  it("a name that is taken is the words of the core with its hint; an empty one is not sent; the same one is not a change", async () => {
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const taken = answer("name_taken");
    core.force(`PUT /resources/reports/${ID.report}`, taken);
    const r = await app.registry.execute("resources.rename", { report_id: ID.report, name: "ресурсы юз" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${(taken.body as { message: string }).message} ${(taken.body as { hint: string }).hint}`);
    const blank = await app.registry.execute("resources.rename", { report_id: ID.report, name: "   " }, "user");
    expect(blank.result).toMatch(/Не задано новое название/);
    const same = await app.registry.execute("resources.rename", { report_id: ID.report, name: "Отчёт по ресурсам" }, "user");
    expect(same.ok).toBe(true);
    expect(puts()).toHaveLength(1);
  });

  it("in a project open for reading the command is not available", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("resources.rename", { report_id: ID.report, name: "Новое" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/только для чтения/);
  });
});
