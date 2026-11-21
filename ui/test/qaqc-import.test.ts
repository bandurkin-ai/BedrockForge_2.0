// «Настройка QAQC → Данные» (ADR 0020 §2, §9–11; Э14 14.18): the mapping chosen by the names of the columns, the
// required SAMPLE_ID, TYPE and elements, the unit from the name «AU_ppm», a refusal of the core shown at its field,
// «устарели» → «Обновить» (the import again with the same mapping), the window opened on a tab, the import job
// followed to its totals. The commands are bound by id (declared by `commands.ts` of 14.15 — stubs here until
// then); the core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openSetup, renderQaqcSetupWindow, SETUP_COMMANDS, SETUP_WINDOW } from "../src/modules/qaqc/bindSetup";
import { dataMenu } from "../src/modules/qaqc/DataRows";
import { DataList } from "../src/modules/qaqc/setup/DataTab";
import { dataStore, reloadData, resetData } from "../src/modules/qaqc/setup/dataStore";
import { importArgs } from "../src/modules/qaqc/setup/ImportForm";
import { autoMapping, countLines, elementOfName, errorPlace, mappingFromArg, mappingProblems, normName, startMapping } from "../src/modules/qaqc/setup/mapping";
import type { QaqcData } from "../src/modules/qaqc/setupApi";
import { jobsStore } from "../src/modules/jobs/state";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { setupApp } from "./qaqcSetupHarness";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const COLUMNS = [
  { name: "Sample ID", kind: "text" as const },
  { name: "Hole", kind: "text" as const },
  { name: "QC_Type", kind: "text" as const },
  { name: "Parent_ID", kind: "text" as const },
  { name: "Standard", kind: "text" as const },
  { name: "Lab", kind: "text" as const },
  { name: "Batch", kind: "text" as const },
  { name: "Дата", kind: "text" as const },
  { name: "SEQ", kind: "number" as const },
  { name: "AU_ppm", kind: "number" as const },
  { name: "Cu, %", kind: "number" as const },
  { name: "Ag", kind: "number" as const },
  { name: "DL_Au", kind: "number" as const },
  { name: "Comment", kind: "text" as const },
];

const DATA: QaqcData = {
  id: 4,
  name: "Контроль 2024",
  version: 2,
  rows: 1200,
  elements: [{ name: "AU", unit: "ppm" }],
  type_codes: [
    { code: "STD", type: "CRM", n: 300 },
    { code: "XX", type: null, n: 7 },
  ],
  source: { kind: "table", set_id: 3, table: "5", name: "Пробы", rev: 4 },
  stale: true,
  mapping: { fields: { sample_id: "SAMPLE", type: "TYPE" }, elements: [{ column: "AU", name: "AU", unit: "г/т" }], extra: [] },
};

describe("mapping", () => {
  it("chooses the fields by the names of the columns and the rest numeric as elements", () => {
    const m = autoMapping(COLUMNS);
    expect(m.fields).toEqual({ sample_id: "Sample ID", hole: "Hole", type: "QC_Type", parent_id: "Parent_ID", crm_id: "Standard", lab: "Lab", batch: "Batch", date: "Дата", seq: "SEQ" });
    expect(m.elements).toEqual([
      { column: "AU_ppm", name: "AU", unit: "ppm", dl: "DL_Au" },
      { column: "Cu, %", name: "Cu", unit: "%" },
      { column: "Ag", name: "Ag", unit: "" },
    ]);
    expect(m.extra).toEqual(["Comment"]);
    expect(normName(" № пробы ")).toBe("no_пробы");
  });

  it("takes the unit from the name", () => {
    expect(elementOfName("AU_ppm")).toEqual({ name: "AU", unit: "ppm" });
    expect(elementOfName("Cu, %")).toEqual({ name: "Cu", unit: "%" });
    expect(elementOfName("Au (g/t)")).toEqual({ name: "Au", unit: "г/т" });
    expect(elementOfName("Ag_ppb")).toEqual({ name: "Ag", unit: "ppb" });
    expect(elementOfName("Zn")).toEqual({ name: "Zn", unit: "" });
  });

  it("requires SAMPLE_ID, TYPE, an element and a name; one column is not two fields", () => {
    expect(mappingProblems({ fields: {}, elements: [], extra: [] }, "").map((p) => p.at)).toEqual(["name", "sample_id", "type", "elements"]);
    const m = autoMapping(COLUMNS);
    expect(mappingProblems(m, "Контроль")).toEqual([]);
    const twice = { ...m, fields: { ...m.fields, hole: "Sample ID" } };
    expect(mappingProblems(twice, "Контроль")).toEqual([{ at: "hole", message: "Колонка «Sample ID» уже выбрана для SAMPLE_ID" }]);
    const sameName = { ...m, elements: [m.elements[0], { ...m.elements[1], name: "au" }] };
    expect(mappingProblems(sameName, "Контроль")[0]).toEqual({ at: "el:Cu, %", message: "Элемент «au» уже есть" });
  });

  it("starts from the core's mapping, dropping columns it does not have", () => {
    const m = startMapping({ columns: COLUMNS, mapping: { fields: { sample_id: "Sample ID", type: "Gone" }, elements: [{ column: "Ag", name: "Ag", unit: "г/т" }, { column: "Gone", name: "X", unit: "" }], extra: [] } });
    expect(m).toEqual({ fields: { sample_id: "Sample ID" }, elements: [{ column: "Ag", name: "Ag", unit: "г/т", dl: null }], extra: [] });
    expect(startMapping({ columns: COLUMNS, mapping: null })).toEqual(autoMapping(COLUMNS));
  });

  it("puts a refusal of the core at its field", () => {
    expect(errorPlace(new CoreError(422, "no_type", "Нет TYPE.", ""))).toBe("type");
    expect(errorPlace(new CoreError(409, "name_taken", "Имя занято.", ""))).toBe("name");
    const e = new CoreError(422, "bad_param", "Колонка не найдена.", "");
    e.body = { param: "mapping.fields.batch" };
    expect(errorPlace(e)).toBe("batch");
    expect(errorPlace(new Error("x"))).toBeNull();
  });

  it("reads a mapping of the assistant and the totals of an import", () => {
    expect(mappingFromArg('{"sample_id": "S", "type": "T", "elements": ["Au_ppm"]}')).toEqual({ fields: { sample_id: "S", type: "T" }, elements: [{ column: "Au_ppm", name: "Au", unit: "ppm" }], extra: [] });
    expect(mappingFromArg("[1]")).toBe("mapping — не JSON-объект");
    expect(countLines(1200, { no_parent: 3, dup_ids: 0, bad_dates: 2 })).toEqual(["Записей: 1\u00a0200", "исходная проба не найдена: 3", "неразобранные даты: 2"]);
  });

  it("gives the form's import as the arguments of `qaqc.import`", () => {
    const m = autoMapping(COLUMNS);
    const a = importArgs({ kind: "file", path: "C:/QC/контроль.xlsx", sheet: "Лист2" }, " Контроль ", m, 4);
    expect(a).toMatchObject({ path: "C:/QC/контроль.xlsx", name: "Контроль", replace_id: 4 });
    expect(JSON.parse(String(a.mapping))).toEqual({ ...m, read: { sheet: "Лист2" } });
  });
});

describe("commands", () => {
  let app: App;
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let importAnswer: () => Response;
  let jobs: Record<string, unknown>[];

  beforeEach(() => {
    sent.length = 0;
    jobs = [];
    importAnswer = () => json({ source: "project", id: 77 }, 202);
    windowStore.set({ windows: [] });
    jobsStore.set({ jobs: [], workers: null, error: null });
    resetData();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        const method = init?.method ?? "GET";
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method, path, body });
        if (path === "/qaqc/data" && method === "GET") return json({ data: [DATA] });
        if (path === "/qaqc/data/4" && method === "GET") return json(DATA);
        if (path === "/qaqc/data/inspect") return json({ rows: 3, columns: COLUMNS, preview: [] });
        if (path === "/qaqc/data" && method === "POST") return importAnswer();
        if (path === "/qaqc/data/4" && method === "PUT") return json({ ...DATA, name: body.name, version: 3 });
        if (path === "/qaqc/data/4" && method === "DELETE") return json({ ok: true });
        if (path === "/jobs") return json({ jobs, workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
        return json({ code: "not_found", message: "Нет", hint: "" }, 404);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = setupApp();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    for (const d of dialogStore.get().queue) closeDialog(d.id);
  });

  const posts = () => sent.filter((r) => r.method === "POST" && r.path === "/qaqc/data");
  const doneJob = (id: number, result: Record<string, unknown>) => ({ id, source: "project", kind: "qaqc.import", title: "", queue: "heavy", status: "done", progress: 1, message: null, error: null, result, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null });

  it("binds the commands of the setup by id", () => {
    for (const id of SETUP_COMMANDS) expect(app.registry.get(id)?.run, id).toBeTypeOf("function");
    app.store.set({ projectName: null });
    expect(app.registry.reason("qaqc.import")).toBe("Откройте или создайте проект");
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect(app.registry.reason("qaqc.import")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("qaqc.setup")).toBeNull();
  });

  it("opens one window on the tab asked for", async () => {
    expect((await app.registry.execute("qaqc.setup", { tab: "crm" })).ok).toBe(true);
    await app.registry.execute("qaqc.setup", { tab: "limits" });
    const wins = windowStore.get().windows.filter((w) => w.kind === SETUP_WINDOW);
    expect(wins).toHaveLength(1);
    expect(wins[0].props?.tab).toBe("limits");
    expect(wins[0].title).toBe("Настройка QAQC");
    expect(renderQaqcSetupWindow({ ...wins[0], kind: "other" })).toBeNull();
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, renderQaqcSetupWindow(wins[0])));
    expect(html).toContain('aria-selected="true" data-testid="qaqc-setup-tab-limits"');
  });

  it("without a source the interface opens the form; the assistant is refused", async () => {
    const r = await app.registry.execute("qaqc.import", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыта загрузка контрольных проб" });
    expect(dataStore.get().form).toMatchObject({ mode: "file" });
    expect(windowStore.get().windows.some((w) => w.kind === SETUP_WINDOW && w.props?.tab === "data")).toBe(true);
    const c = await app.registry.execute("qaqc.import", {}, "claude");
    expect(c.ok).toBe(false);
    expect(c.result).toContain("Не задан источник контрольных проб");
  });

  it("the assistant's import: the source is read, the mapping chosen automatically, the job followed to its totals", async () => {
    jobs = [doneJob(77, { data_id: 9, rows: 400, counts: { no_parent: 2 } })];
    const r = await app.registry.execute("qaqc.import", { path: "C:/QC/qc.csv" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toBe("Загружены данные QAQC «qc» (данные QAQC №9): Записей: 400; исходная проба не найдена: 2");
    const [post] = posts();
    expect(post.body).toMatchObject({ name: "qc", source: { kind: "file", path: "C:/QC/qc.csv" }, mapping: autoMapping(COLUMNS) });
    expect(post.body).not.toHaveProperty("replace_id");
    expect(dataStore.get().done).toEqual({ dataId: 9, name: "qc", rows: 400, counts: { no_parent: 2 } });
  });

  it("a refusal of the core is shown at its field and the form stays", async () => {
    importAnswer = () => json({ code: "no_type", message: "В файле нет колонки типа пробы.", hint: "Выберите колонку TYPE.", param: "type" }, 422);
    await app.registry.execute("qaqc.import", {}, "user");
    const m = JSON.stringify(autoMapping(COLUMNS));
    const r = await app.registry.execute("qaqc.import", { path: "C:/QC/qc.csv", name: "qc", mapping: m }, "user");
    expect(r.ok).toBe(false);
    expect(dataStore.get().refused).toEqual({ at: "type", message: "В файле нет колонки типа пробы. Выберите колонку TYPE." });
    expect(dataStore.get().form).not.toBeNull();
    expect(dataStore.get().run).toBeNull();
  });

  it("a mapping without SAMPLE_ID is refused before the core", async () => {
    const r = await app.registry.execute("qaqc.import", { path: "C:/QC/qc.csv", mapping: '{"fields": {"type": "QC_Type"}, "elements": ["AU_ppm"]}' }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Выберите колонку номера пробы (SAMPLE_ID)");
    expect(posts()).toHaveLength(0);
    expect(dataStore.get().refused?.at).toBe("sample_id");
  });

  it("«устарели» → «Обновить»: the import again from the same table with the same mapping", async () => {
    await reloadData();
    const html = renderToStaticMarkup(createElement(DataList, { list: dataStore.get().list, reason: () => null, run: () => undefined }));
    expect(html).toContain("· устарели");
    expect(html).toContain("не распознано: 7");
    expect(html).toContain(">Обновить<");
    const fresh = renderToStaticMarkup(createElement(DataList, { list: [{ ...DATA, stale: false, source: { kind: "file", name: "qc.csv" } }], reason: () => null, run: () => undefined }));
    expect(fresh).not.toContain("устарели");
    expect(fresh).not.toContain(">Обновить<");
    jobs = [doneJob(77, { data_id: 4, rows: 1250, counts: {} })];
    const r = await app.registry.execute("qaqc.data_refresh", { data_id: 4 }, "claude");
    expect(r.result).toBe("Загружены данные QAQC «Контроль 2024» (данные QAQC №4): Записей: 1\u00a0250");
    expect(posts()[0].body).toEqual({ name: "Контроль 2024", source: { kind: "table", set_id: 3, table: "5" }, mapping: DATA.mapping, replace_id: 4 });
  });

  it("from the interface: «Обновить» and «Удалить» ask our own question", async () => {
    await reloadData();
    const p = app.registry.execute("qaqc.data_delete", { data_id: 4 }, "user");
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind === "confirm") {
      expect(d.options.message).toContain("«Контроль 2024»");
      d.resolve(true);
    }
    closeDialog(d.id);
    expect((await p).result).toBe("Удалены данные QAQC «Контроль 2024»");
    expect(sent.some((r) => r.method === "DELETE" && r.path === "/qaqc/data/4")).toBe(true);
    expect(dataStore.get().list).toEqual([]);
  });

  it("renames with the version of the list", async () => {
    await reloadData();
    const r = await app.registry.execute("qaqc.data_rename", { data_id: 4, name: "Контроль 2024 г." }, "claude");
    expect(r.result).toBe("Данные QAQC «Контроль 2024» переименованы в «Контроль 2024 г.»");
    expect(sent.find((x) => x.method === "PUT")?.body).toEqual({ name: "Контроль 2024 г.", expected_version: 2 });
    expect(dataStore.get().list[0].name).toBe("Контроль 2024 г.");
    expect((await app.registry.execute("qaqc.data_rename", { data_id: 99, name: "x" }, "claude")).result).toContain("Данные QAQC №99 не найдены");
  });

  it("the menu of a data row in «Аудит и статистика»", () => {
    const labels = dataMenu(DATA, () => null, () => undefined).map((i) => i.label);
    expect(labels).toEqual(["Открыть", "Настройка QAQC…", "Обновить", "Переименовать…", "Удалить…"]);
    openSetup("types");
    expect(windowStore.get().windows[0].props?.tab).toBe("types");
  });
});
