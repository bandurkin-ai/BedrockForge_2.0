// The section «Отчёты» → «Отчёты по ресурсам» of the tree and the «Свойства» of a report (Э20, 20.13; ТЗ 8.10; ADR 0027 решения 14, 16, 29; Д-35): the list is the core's
// (`reports_list`, `report_get` of `core/tests/data/resources/contract_answers.json`), a row has the badge of the graph and the menu of the commands of the registry, a click chooses the
// report and a double click shows it in the panel, the properties tell what the report was made of; every action is a command and an unavailable one says why.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { openMenu, menuStore } from "../../src/shell/ContextMenu";
import { readListRow, readReportResult, type ReportListRow } from "../../src/modules/resources/answers";
import { panelStore, RESOURCES_TAB } from "../../src/modules/resources/panelState";
import { askRename, NODE_KIND, readyReason, rebuildReason, reportActions, reportLabel, reportsByName, selectReport, stateTitle } from "../../src/modules/resources/reportActions";
import { cutoffsText, densityText, keysText, reportFacts } from "../../src/modules/resources/reportFacts";
import { ReportProps } from "../../src/modules/resources/ReportProps";
import { ResourceReportRows, ResourceReportsTree, rowTitle } from "../../src/modules/resources/ReportsTree";
import { resourcesStore } from "../../src/modules/resources/state";
import { dialogStore } from "../../src/shell/dialogs";
import { answer, ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

let app: App;
let core: Core;
let unbind: () => void;
beforeEach(() => {
  core = installCore();
  ({ app, unbind } = newApp());
});
afterEach(() => teardown(unbind));

const LIST = (answer("reports_list").body as { reports: unknown[] }).reports.map(readListRow) as ReportListRow[];
const ROW = LIST[0];
const SAVED = readReportResult(answer("report_get").body)!;
const withApp = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const plain = (s: string) => s.replace(/[\u00a0\u202f]/g, " ");

describe("the rows of the tree", () => {
  it("the real list: a row for a report with its name, the badge place, the tooltip with the model and the total", () => {
    resourcesStore.set({ reports: LIST, reportsLoaded: true });
    const html = plain(withApp(createElement(ResourceReportsTree)));
    expect(html).toContain('data-testid="tree-resource-reports"');
    expect(html).toContain("Отчёты по ресурсам");
    expect(html).toContain('data-testid="tree-resource-report-2"');
    expect(html).toContain('aria-label="Отчёт по ресурсам"');
    expect(html).toContain("по модели «Модель»");
    expect(html).toContain("итог: 12 500 т, блоков 5");
    expect(html).toContain("двойной щелчок — показать в панели «Ресурсы»");
    expect(html).toMatch(/<span class="[^"]*count[^"]*">1<\/span>/);
  });

  it("the states: «строится» and «ошибка» in the label, «устарел» and «источник удалён» in the place of the badge until the graph answers", () => {
    const rows: ReportListRow[] = [
      { ...ROW, id: 3, name: "Б строится", state: "building" },
      { ...ROW, id: 4, name: "В ошибка", state: "error" },
      { ...ROW, id: 5, name: "А устарел", graph: { stale: true, stale_reason: "изменена таблица", source_lost: false, pinned: false } },
      { ...ROW, id: 6, name: "Г без модели", graph: { stale: false, stale_reason: null, source_lost: true, pinned: false } },
    ];
    resourcesStore.set({ reports: rows, reportsLoaded: true });
    const html = withApp(createElement(ResourceReportRows));
    expect(html.indexOf("А устарел")).toBeLessThan(html.indexOf("Б строится")); // by name
    expect(html).toContain("Б строится · строится");
    expect(html).toContain("В ошибка · ошибка");
    expect(html).toMatch(/А устарел[\s\S]*устарел<\/span>/);
    expect(html).toContain("источник удалён");
    expect(reportLabel(ROW)).toBe("Отчёт по ресурсам");
    expect(stateTitle("building")).toBe("строится");
    expect(stateTitle("ready")).toBe("");
    expect(reportsByName([{ id: 2, name: "Б" }, { id: 1, name: "Б" }, { id: 3, name: "А" }]).map((r) => r.id)).toEqual([3, 1, 2]);
  });

  it("no reports: the word that says how to make one; before the list is read — «Чтение»", () => {
    resourcesStore.set({ reports: [], reportsLoaded: true });
    expect(withApp(createElement(ResourceReportRows))).toContain("Нет отчётов по ресурсам");
    resourcesStore.set({ reports: [], reportsLoaded: false });
    expect(withApp(createElement(ResourceReportRows))).toContain("Чтение отчётов…");
  });

  it("the tooltip tells a report that is building or failed", () => {
    expect(rowTitle({ ...ROW, state: "building" })).toContain("строится");
    expect(rowTitle({ ...ROW, state: "error" })).toContain("не построен");
  });
});

describe("a click and the menu", () => {
  it("a click chooses the report: its «Свойства» open; the kind is the tree's own", () => {
    selectReport(app, ID.report);
    expect(app.store.get().selected).toEqual({ kind: "resource_report", id: ID.report });
    expect(app.store.get().workspace).toMatchObject({ right: true, rightTab: "properties" });
  });

  it("the menu: the commands of the registry; every item runs one; the PDF waits for Э22", async () => {
    const items = reportActions(app, ROW);
    expect(items.map((i) => i.label)).toEqual(["Показать в панели «Ресурсы»", "Сохранить XLSX…", "Сохранить CSV таблицы…", "PDF", "Перестроить", "Переименовать…", "Удалить…"]);
    expect(items.find((i) => i.label === "PDF")?.reason).toBe("Появится на этапе Э22");
    for (const l of ["Показать в панели «Ресурсы»", "Сохранить XLSX…", "Переименовать…", "Удалить…"]) expect(items.find((i) => i.label === l)?.reason, l).toBeNull();
    items[0].run?.();
    await waitFor(() => app.store.get().workspace.bottomTab === RESOURCES_TAB && panelStore.get().mode === "report" && !panelStore.get().loading && panelStore.get().report !== null);
    expect(panelStore.get().reportId).toBe(ID.report);
    expect(core.sent.some((r) => r.method === "GET" && r.path === `/resources/reports/${ID.report}`)).toBe(true);
  });

  it("a report that is building cannot be shown, saved, renamed, deleted or rebuilt; a failed one cannot be shown or saved; a lost source cannot be rebuilt", () => {
    const building = reportActions(app, { ...ROW, state: "building" });
    for (const l of ["Показать в панели «Ресурсы»", "Сохранить XLSX…", "Переименовать…", "Удалить…"]) expect(building.find((i) => i.label === l)?.reason, l).toBe("Отчёт строится");
    expect(readyReason({ state: "error" })).toBe("Отчёт не построен: перестройте его");
    expect(readyReason({ state: "ready" })).toBeNull();
    expect(rebuildReason({ state: "ready", graph: { stale: false, stale_reason: null, source_lost: false, pinned: false } })).toBeNull();
    expect(rebuildReason({ state: "ready", graph: { stale: true, stale_reason: null, source_lost: true, pinned: false } })).toMatch(/Источник отчёта удалён/);
    expect(rebuildReason({ state: "building", graph: { stale: false, stale_reason: null, source_lost: false, pinned: false } })).toBe("Отчёт строится");
  });

  it("in a project open for reading: rename and delete are not available, show and save are", () => {
    app.store.set({ projectReadOnly: true });
    const items = reportActions(app, ROW);
    expect(items.find((i) => i.label === "Переименовать…")?.reason).toMatch(/только для чтения/);
    expect(items.find((i) => i.label === "Удалить…")?.reason).toMatch(/только для чтения/);
    expect(items.find((i) => i.label === "Показать в панели «Ресурсы»")?.reason).toBeNull();
    openMenu(10, 10, items);
    expect(menuStore.get().items?.length).toBe(7);
  });

  it("«Переименовать…» asks the name in our own window; the answer runs `resources.rename`", async () => {
    resourcesStore.set({ reports: LIST, reportsLoaded: true });
    core.force(`PUT /resources/reports/${ID.report}`, answer("report_put"));
    const run = askRename(ROW, (id, args) => void app.run(id, args));
    await waitFor(() => dialogStore.get().queue.length === 1);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("text");
    if (d.kind === "text") {
      expect(d.options.title).toBe("Переименовать отчёт по ресурсам");
      expect(d.options.initial).toBe("Отчёт по ресурсам");
      d.resolve("Ресурсы ЮЗ");
    }
    await run;
    await waitFor(() => core.sent.some((r) => r.method === "PUT"));
    expect(core.sent.find((r) => r.method === "PUT")?.body).toEqual({ name: "Ресурсы ЮЗ", expected_version: 1 });
  });
});

describe("«Свойства» of a report", () => {
  it("what the report was made of and what it says: the model, the cut-offs, the elements, the density, the keys, the template, the total", () => {
    const facts = Object.fromEntries(reportFacts(SAVED, ROW).map((f) => [f.key, plain(f.value)]));
    expect(facts).toMatchObject({
      model: "«Модель», версия 1",
      version: "1",
      cutoff_field: "AU",
      cutoffs: "1; 2",
      elements: "AU, г/т",
      density: "2,5 т/м³",
      keys: "CLASS",
      template: "JORC",
      rows: "8",
      total: "блоков 5, тоннаж 12 500 т",
      "total-AU": "2,5 г/т, металл 31 250 г",
    });
    expect(reportFacts(SAVED).find((f) => f.key === "created")?.value).toMatch(/^2026-11-26 \d\d:\d\d$/);
  });

  it("the cut-offs «без борта» and the density of a field, the groups with the bench, the files written", () => {
    expect(cutoffsText({ cutoffs: [null, 0.5, 1] })).toBe("без борта; 0,5; 1");
    expect(cutoffsText({})).toBe("без борта");
    expect(densityText({ ...SAVED, density: { field: "SG", value: null } })).toBe("поле SG");
    expect(densityText({ ...SAVED, density: { field: null, value: null } })).toBe("нет значения");
    expect(keysText({ ...SAVED, keys: [{ kind: "field", field: "CLASS", labels: [] }, { kind: "bench", field: null, labels: [] }] })).toBe("CLASS; Уступ");
    expect(keysText({ ...SAVED, keys: [] })).toBe("без групп");
    const files = reportFacts({ ...SAVED, files: [{ format: "xlsx", path: "C:\\Отчёты\\r.xlsx" }] }).find((f) => f.key === "files");
    expect(files?.value).toBe("XLSX: C:\\Отчёты\\r.xlsx");
  });

  it("a total with no grade is «нет значения», a grade of an unknown unit has no metal and says so", () => {
    const gap = { ...SAVED, totals: { ...SAVED.totals!, elements: { AU: { wt: 0, sum: 0, grade: null, metal: null, missing_t: 0, missing_n: 0 } } } };
    expect(reportFacts(gap).find((f) => f.key === "total-AU")?.value).toBe("нет значения");
    const noUnit = { ...SAVED, totals: { ...SAVED.totals!, elements: { AU: { wt: 1, sum: 1, grade: 2, metal: null, missing_t: 0, missing_n: 0 } } } };
    expect(reportFacts(noUnit).find((f) => f.key === "total-AU")?.value).toContain("не считается (единица не распознана)");
  });

  it("the panel: the name, the badge, the actions as commands (with their reasons), the block of the graph («Закрепить», «Происхождение», «Зависимые»)", () => {
    resourcesStore.set({ reports: LIST, reportsLoaded: true });
    const html = withApp(createElement(ReportProps, { id: ID.report }));
    expect(html).toContain('data-testid="props-resource-report"');
    expect(html).toContain("Отчёт по ресурсам");
    for (const t of ["props-resource-open", "props-resource-xlsx", "props-resource-csv", "props-resource-rebuild", "props-resource-rename", "props-resource-delete"]) expect(html, t).toContain(`data-testid="${t}"`);
    expect(html).toContain("Закрепить");
    expect(html).toContain("Происхождение");
    expect(html).toContain("Зависимые");
    expect(NODE_KIND).toBe("object:resource_report");
    expect(html).not.toMatch(/disabled=""[^>]*data-testid="props-resource-open"|data-testid="props-resource-open"[^>]*disabled=""/);
  });

  it("a report that is not in the list has no actions (the list is read again by the tree)", () => {
    resourcesStore.set({ reports: [], reportsLoaded: true });
    const html = withApp(createElement(ReportProps, { id: 99 }));
    expect(html).toMatch(/data-testid="props-resource-open"[^>]*disabled=""|disabled=""[^>]*data-testid="props-resource-open"/);
    expect(html).toContain("Отчёта нет в списке");
    expect(html).toContain("№99");
  });

  it("in a project open for reading the properties disable rename and delete", () => {
    app.store.set({ projectReadOnly: true });
    resourcesStore.set({ reports: LIST, reportsLoaded: true });
    const html = withApp(createElement(ReportProps, { id: ID.report }));
    expect(html).toMatch(/data-testid="props-resource-delete"[^>]*disabled=""|disabled=""[^>]*data-testid="props-resource-delete"/);
    expect(html).toMatch(/Недоступно: [^"]*только для чтения/);
  });
});
