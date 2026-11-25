// The window «Экспорт блочной модели» and the command `blocks.export` (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12; Э17 17.38) through a
// stubbed core that answers with the REAL answers (`core/tests/data/blocks/export_window_answers.json`, made by
// `core/tests/test_blocks_export_window_answers.py` through `POST /blocks/{id}/export` and the job `blocks.export`): the
// body the core got, the job followed to its end, the file written in the window, «Заменить?» in our own window when the
// file is there (409), a failure of the job under its field, OMF of a model with sub-blocks refused before the core is
// asked, «Выбрать файл…» through the save window of the system, «Отменить», the button of «Свойства».
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { jobsStore } from "../src/modules/jobs/state";
import { blocksStore } from "../src/modules/blocks/state";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { argsOfForm } from "../src/modules/blocks/export/args";
import { bindExport, cancelExport, chooseFile, exportTitle, openExportWindow, renderExportWindow, saveFromWindow, windowOfExport } from "../src/modules/blocks/export/bind";
import { commands, EXCHANGE_BUTTONS } from "../src/modules/blocks/export/commands";
import { ExportWindow } from "../src/modules/blocks/export/ExportWindow";
import { EMPTY_FORM, type ExportForm } from "../src/modules/blocks/export/model";
import { currentForm, currentRun, EXPORT_WINDOW, exportStore, resetExport } from "../src/modules/blocks/export/state";
import { commands as importCommands } from "../src/modules/importing/commands";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/export_window_answers.json"), "utf-8"));
const IDS = A.ids as { model: number; sub: number; filter: number };
type Run = { request: Record<string, unknown>; answer: { status: number; body: Record<string, unknown> }; job?: { status: string; progress: number; message: string; error: unknown; result: unknown } };
const RUNS = A.runs as Record<string, Run>;
const REFUSED = A.refused as Record<string, Run>;
const FAILED = A.failed as Record<string, Run>;
const ALL: Run[] = [...Object.values(RUNS), ...Object.values(REFUSED), ...Object.values(FAILED)];

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const JOB = { source: "proj", id: 5 };
const canon = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));
const ws = (text: string): string => text.replace(/ /g, " ");

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let jobRun: Run | null = null;
let jobStatus: string | null = null;
/** The files that are there already (the core answers 409 `files_exist` for them). */
const existing = new Set<string>();

const jobRow = () => {
  const j = jobRun?.job;
  const status = jobStatus ?? j?.status ?? "done";
  return { ...JOB, kind: "blocks.export", title: "Экспорт блочной модели", queue: "heavy", status, progress: status === "done" ? 1 : 0.4, message: j?.message ?? "Запись", error: status === "failed" ? j?.error : null, params: null, result: status === "done" ? j?.result : null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null };
};

const exportsSent = () => sent.filter((r) => /^\/blocks\/\d+\/export$/.test(r.path));

/** The recorded run whose request is this body (the window sends the defaults of the core out; the record has them). */
const recorded = (body: Record<string, unknown>): Run | undefined => {
  const { overwrite, ...rest } = body;
  const key = (r: Record<string, unknown>) => canon(Object.fromEntries(Object.entries(r).filter(([k, v]) => k !== "overwrite" && !(k === "decimal" && v === ","))));
  return ALL.find((r) => key(r.request) === key(rest) && (r.request.overwrite === true) === (overwrite === true));
};

beforeEach(() => {
  sent.length = 0;
  jobRun = null;
  jobStatus = null;
  existing.clear();
  windowStore.set({ windows: [] });
  resetExport();
  jobsStore.set({ jobs: [], workers: null, error: null });
  dialogStore.set({ queue: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      const post = /^\/blocks\/(\d+)\/export$/.exec(path);
      if (post && method === "POST") {
        if (existing.has(String(body.path)) && body.overwrite !== true) return json(REFUSED.files_exist.answer.body, 409);
        const found = recorded(body);
        if (!found) return json({ code: "unexpected", message: `Неожиданный запрос ${canon(body)}`, hint: "" }, 500);
        if (found.answer.status !== 200) return json(found.answer.body, found.answer.status);
        jobRun = found;
        return json(JOB);
      }
      if (path === "/jobs") return json({ jobs: jobRun ? [jobRow()] : [], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}` && method === "GET") return json(jobRow());
      if (path === `/jobs/${JOB.source}/${JOB.id}/cancel`) {
        jobStatus = "cancelled";
        return json({});
      }
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  app.registry.register(commands);
  bindExport(app);
  blocksStore.set({ list: A.blocks.body.block_models, loaded: true, error: null, details: { [IDS.model]: A.model.body, [IDS.sub]: A.sub_model.body }, detailErrors: {} });
  filtersStore.set({ rows: A.filters.body.filters, loaded: true, selected: null, edit: null });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [], loaded: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ExportWindow, { winId: windowOfExport() ?? "w" })));

/** The form of the window that asks for a recorded run (the model, the format and the file are the user's). */
const form = (patch: Partial<ExportForm>): ExportForm => ({ ...EMPTY_FORM, modelId: IDS.model, ...patch });
const answerDialog = async (ok: boolean): Promise<string> => {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind !== "confirm") throw new Error(`a ${d.kind} dialog`);
  d.resolve(ok);
  return d.options.message;
};

describe("the command `blocks.export`", () => {
  it("is in the registry with a Russian title and a description for the assistant; bound; the buttons of the ribbon are commands", () => {
    expect(commands.map((c) => c.id)).toEqual(["blocks.export"]);
    const c = app.registry.get("blocks.export")!;
    expect(c.run).toBeTypeOf("function");
    expect(c.stage).toBeUndefined();
    expect(c.uiOnly).toBeUndefined();
    expect(c.title).toBe("Экспорт блочной модели");
    expect(c.description).toMatch(/Пропуск пишется пустым значением, а не нулём/);
    expect(c.description).toMatch(/Datamine DM не записывается/);
    expect(c.required ?? []).toEqual([]); // without model_id and path — the window
    expect(Object.keys(c.params!)).toEqual(["model_id", "format", "path", "fields", "filter_id", "restrict", "header", "encoding", "delimiter", "decimal", "empty", "overwrite"]);
    expect(c.params!.format.enum).toEqual(["dat", "csv", "parquet", "omf"]);
    for (const b of EXCHANGE_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    expect(EXCHANGE_BUTTONS.map((b) => [b.command, b.args ?? null])).toEqual([["import.files", { kind: "blocks" }], ["blocks.export", null]]);
    const importer = importCommands.find((x) => x.id === "import.files")!;
    expect((importer.params as Record<string, { enum?: string[] }>).kind.enum).toContain("blocks");
  });

  it("is unavailable without a project and without block models; works in a project open for reading", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("blocks.export", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект" });
    blocksStore.set({ list: [], loaded: true });
    expect((await app.registry.execute("blocks.export", {})).result).toBe("В проекте нет блочных моделей");
    blocksStore.set({ list: A.blocks.body.block_models, loaded: true, details: {} });
    app.store.set({ projectReadOnly: true });
    const f = form({ format: "dat", path: RUNS.dat.request.path as string, pathFormat: "dat" });
    const r = await app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "claude");
    expect(r.ok, r.result).toBe(true); // nothing of the project is written
  });

  it("without a path it opens the window with the model of the tree, once; a model named in the call is the model of the window", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.sub } });
    const r = await app.registry.execute("blocks.export", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Экспорт блочной модели»" });
    const win = windowStore.get().windows.filter((w) => w.kind === EXPORT_WINDOW);
    expect(win).toHaveLength(1);
    expect(win[0].title).toBe("Экспорт блочной модели · sub");
    expect(currentForm().modelId).toBe(IDS.sub);
    expect(openExportWindow(app)).toBe(windowOfExport());
    expect(windowStore.get().windows.filter((w) => w.kind === EXPORT_WINDOW)).toHaveLength(1);
    expect(renderExportWindow({ ...win[0], kind: "other" })).toBeNull();
    await app.registry.execute("blocks.export", { model_id: IDS.model }, "user");
    expect(currentForm().modelId).toBe(IDS.model);
    expect(windowStore.get().windows.find((w) => w.kind === EXPORT_WINDOW)!.title).toBe(exportTitle("Повёрнутая"));
    expect((await app.registry.execute("blocks.export", { model_id: 999 }, "user")).result).toBe("Блочная модель №999 не найдена. Номера моделей — в разделе «Блочные модели» дерева.");
    expect(exportsSent()).toEqual([]);
  });

  it("writes the file: the body of the request, the job, the words for the assistant, the result in the window", async () => {
    const f = form({ format: "dat", allFields: false, fields: ["DOM", "AU"], emptyOn: true, empty: "-99", encoding: "bocu1", path: RUNS.dat_fields.request.path as string, pathFormat: "dat" });
    const r = await app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "claude");
    expect(r.ok, r.result).toBe(true);
    expect(exportsSent()).toHaveLength(1);
    expect(exportsSent()[0].path).toBe(`/blocks/${IDS.model}/export`);
    expect(exportsSent()[0].body).toEqual(RUNS.dat_fields.request);
    expect(ws(r.result)).toMatch(/^Модель «Повёрнутая» записана в Micromine DAT: блоков 350 \(\d+(,\d)? КБ\) — \/выгрузка\/fields\.dat$/);
    const run = currentRun();
    expect(run.phase).toBe("done");
    expect(run.job).toEqual(JOB);
    expect(run.result).toMatchObject({ path: "/выгрузка/fields.dat", format: "dat", rows: 350, total: 350 });
    expect(windowOfExport()).toBeNull(); // the assistant does not open windows
    expect(currentForm()).toMatchObject({ modelId: IDS.model, allFields: false, fields: ["DOM", "AU"], emptyOn: true, empty: "-99", encoding: "bocu1" });
    // a call of the user shows the window with the result
    const again = await app.registry.execute("blocks.export", argsOfForm(f, "/выгрузка/fields.dat"), "user");
    expect(again.ok).toBe(true);
    const page = html();
    for (const id of ["export-window", "export-model", "export-format-dat", "export-format-omf", "export-fields-all", "export-filter", "export-restrict-on", "export-encoding", "export-empty-on", "export-choose", "export-save", "export-result", "export-done", "export-show"]) expect(page, id).toContain(`data-testid="${id}"`);
    for (const t of ["Модель", "Формат", "Поля", "Отбор блоков", "Запись", "Файл", "Сохранить…", "Показать в папке", "/выгрузка/fields.dat", "Micromine DAT"]) expect(page, t).toContain(t);
  });

  it("the blocks that did not pass the filter: written of total, in the window", async () => {
    const f = form({ format: "dat", allFields: false, fields: ["AU", "N"], filterId: IDS.filter, restrictOn: true, path: RUNS.dat_filtered.request.path as string, pathFormat: "dat" });
    f.restrict = { cond: { field: "N", op: ">", value: "0", list: "" } };
    const r = await app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "user");
    expect(r.ok, r.result).toBe(true);
    expect(exportsSent()[0].body).toEqual(RUNS.dat_filtered.request);
    expect(ws(r.result)).toContain("(остальные не прошли отбор)");
    expect(ws(html())).toMatch(/Записано \d+ из 350 блоков: остальные не прошли фильтр или ограничитель\./);
  });

  it("every recorded run goes through the command: the body is what the core got", async () => {
    for (const [name, run] of Object.entries(RUNS)) {
      if (["dat_fields", "dat_filtered"].includes(name)) continue; // above
      sent.length = 0;
      resetExport();
      const model = name.startsWith("sub_") ? IDS.sub : IDS.model;
      const args: Record<string, unknown> = { model_id: model, ...run.request };
      if (Array.isArray(args.fields)) args.fields = args.fields.join(", "); // the command takes the names as text
      if (args.decimal === ",") delete (args as { decimal?: unknown }).decimal; // the default of the core need not be asked for
      const r = await app.registry.execute("blocks.export", args, "claude");
      expect(r.ok, `${name}: ${r.result}`).toBe(true);
      expect(exportsSent()[0].path, name).toBe(`/blocks/${model}/export`);
      expect(recorded(exportsSent()[0].body), name).toBe(run);
    }
  });

  it("the file is there: «Заменить?» in our own window, then the same request with `overwrite`; «Нет» writes nothing", async () => {
    const path = REFUSED.files_exist.request.path as string;
    existing.add(path);
    const args = { model_id: IDS.model, format: "dat", path };
    const run = app.registry.execute("blocks.export", args, "user");
    const question = await answerDialog(true);
    expect(question).toContain("model.dat");
    const r = await run;
    expect(r.ok, r.result).toBe(true);
    expect(exportsSent().map((x) => x.body.overwrite ?? false)).toEqual([false, true]);
    expect(exportsSent()[1].body).toEqual(REFUSED.overwrite.request);
    expect(currentRun().phase).toBe("done");
    sent.length = 0;
    const no = app.registry.execute("blocks.export", args, "user");
    await answerDialog(false);
    const out = await no;
    expect(out).toEqual({ ok: true, result: "Отменено: файл не заменён" });
    expect(exportsSent()).toHaveLength(1);
    expect(currentRun().phase).toBe("idle");
  });

  it("a failure of the job is told under its field in Russian; nothing is written", async () => {
    const f = form({ format: "dat", emptyOn: true, empty: "3", path: FAILED.empty_code_taken.request.path as string, pathFormat: "dat" });
    const r = await app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "user");
    const e = FAILED.empty_code_taken.job!.error as { message: string; hint: string };
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${e.message} ${e.hint}`);
    expect(currentRun().phase).toBe("failed");
    expect(currentRun().result).toBeNull();
    expect(exportStore.get().issue).toMatchObject({ field: "empty", message: e.message });
    const page = html();
    expect(page).toContain('data-testid="export-error"');
    expect(page).toContain('data-testid="export-issue-empty"');
    expect(page).not.toContain('data-testid="export-done"');
    resetExport();
    const g = form({ format: "dat", modelId: IDS.model, restrictOn: true, path: FAILED.nothing_passes.request.path as string, pathFormat: "dat" });
    g.restrict = { cond: { field: "AU", op: ">", value: "1000000000", list: "" } };
    const nothing = await app.registry.execute("blocks.export", argsOfForm(g, g.path as string), "user");
    expect(nothing.ok).toBe(false);
    expect(nothing.result).toContain("Ни один блок не прошёл отбор");
    expect(exportStore.get().issue?.field).toBe("restrict");
  });

  it("the refusals of the core: the words of the core, its place in the form", async () => {
    // a field the model has not: told before the core is asked, with the fields the model has
    const r = await app.registry.execute("blocks.export", { model_id: IDS.model, format: "dat", path: REFUSED.field_missing.request.path, fields: "NOPE" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe("В модели нет поля «NOPE». Поля модели: AU, CU, N, DOM.");
    expect(exportStore.get().issue).toMatchObject({ field: "fields" });
    expect(exportsSent()).toEqual([]);
    expect(html()).toContain('data-testid="export-issue-fields"');
    const gone = REFUSED.filter_missing.answer.body as { message: string; hint: string };
    const f = await app.registry.execute("blocks.export", { model_id: IDS.model, format: "dat", path: REFUSED.filter_missing.request.path, filter_id: 99999 }, "user");
    expect(f.result).toBe(`${gone.message} ${gone.hint}`);
    expect(exportStore.get().issue?.field).toBe("filter");
  });

  it("OMF of a model with sub-blocks is refused before the core is asked, with the words of the core", async () => {
    const r = await app.registry.execute("blocks.export", { model_id: IDS.sub, format: "omf", path: "/выгрузка/sub.omf" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("с субблоками: OMF не хранит субблоки — экспорт возможен только после регуляризации.");
    expect(r.result).toContain("Выполните регуляризацию модели");
    expect(exportStore.get().issue?.field).toBe("format");
    expect(exportsSent()).toEqual([]);
    // the window does not offer it for that model
    const page = html();
    expect(page).toContain("только после регуляризации");
    expect(/<input[^>]*data-testid="export-format-omf"[^>]*disabled/.test(page) || /disabled[^>]*data-testid="export-format-omf"/.test(page)).toBe(true);
    // the same model in another format goes through
    const ok = await app.registry.execute("blocks.export", { model_id: IDS.sub, format: "parquet", path: RUNS.sub_parquet.request.path }, "claude");
    expect(ok.ok, ok.result).toBe(true);
  });

  it("every wrong argument is told in Russian before the core is asked", async () => {
    for (const a of [{ model_id: IDS.model, format: "dat", path: "/a/b.dat", fields: "" , empty: "abc" }, { model_id: IDS.model, format: "gpx", path: "/a/b.gpx" }, { model_id: IDS.model, format: "csv", path: "/a/b.csv", delimiter: ",", decimal: "," }]) {
      const r = await app.registry.execute("blocks.export", a, "claude");
      expect(r.ok).toBe(false);
      expect(r.result).toMatch(/[А-Яа-яЁё]/);
    }
    expect(exportsSent()).toEqual([]);
  });

  it("the job that is cancelled leaves nothing: «Отменить» → the job is cancelled, the run is told so", async () => {
    jobStatus = "running";
    const f = form({ format: "csv", path: RUNS.csv.request.path as string, pathFormat: "csv" });
    const run = app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "user");
    await vi.waitFor(() => expect(currentRun().job).toEqual(JOB));
    expect(currentRun().phase).toBe("running");
    const busy = await app.registry.execute("blocks.export", argsOfForm(f, f.path as string), "claude");
    expect(busy.result).toBe("Экспорт уже идёт. Дождитесь конца или отмените его в окне «Экспорт блочной модели».");
    expect(html()).toContain('data-testid="export-progress"');
    expect(html()).toContain('data-testid="export-cancel"');
    await cancelExport();
    const r = await run;
    expect(sent.some((x) => x.path === "/jobs/proj/5/cancel" && x.method === "POST")).toBe(true);
    expect(r).toEqual({ ok: true, result: "Экспорт модели «Повёрнутая» отменён: файл не создан" });
    expect(currentRun().phase).toBe("cancelled");
    expect(currentRun().result).toBeNull();
    expect(html()).toContain("Экспорт отменён: файл не создан");
  });
});

describe("the window", () => {
  const bridgeStub = (pickSave: (name: string, kind?: string) => Promise<string | null>) => vi.stubGlobal("window", { bf: { pickSave, showInFolder: vi.fn() } });

  it("before a run: the form of the defaults and the invitation; the options follow the format", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.export", {}, "user");
    const page = html();
    expect(page).toContain("Выберите файл и нажмите «Сохранить…»");
    expect(page).toContain("не выбран");
    expect(page).toContain("Все поля модели");
    expect(page).toContain("по умолчанию (Windows-1251 (кириллица))");
    expect(page).not.toContain('data-testid="export-delimiter"'); // a DAT has no delimiter
    expect(page).toContain("Без числа пропуск пишется пустым значением — не нулём.");
    exportStore.set((s) => ({ form: { ...s.form, format: "csv" } }));
    const csv = html();
    for (const id of ["export-delimiter", "export-decimal", "export-header"]) expect(csv, id).toContain(`data-testid="${id}"`);
    expect(csv).toContain("по умолчанию (UTF-8 с меткой (для Excel))");
    exportStore.set((s) => ({ form: { ...s.form, format: "parquet" } }));
    const parquet = html();
    expect(parquet).not.toContain('data-testid="export-encoding"');
    expect(parquet).not.toContain('data-testid="export-empty-on"'); // a gap of a Parquet is a null, there is no code
    exportStore.set((s) => ({ form: { ...s.form, format: "omf" } }));
    expect(html()).toContain("нужно хотя бы одно поле");
  });

  it("the fields chosen are listed with their units, the order of the choice is told", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.export", {}, "user");
    exportStore.set((s) => ({ form: { ...s.form, allFields: false, fields: ["DOM", "AU"] } }));
    const page = html();
    for (const n of ["AU", "CU", "N", "DOM"]) expect(page).toContain(`data-testid="export-field-${n}"`);
    expect(page).toContain("Порядок столбцов: DOM, AU");
    expect(page).toContain(", г/т");
    expect(page).toContain("категория");
  });

  it("«Выбрать файл…»: the save window gets the name of the model with the extension and the filter of the format", async () => {
    const calls: [string, string | undefined][] = [];
    bridgeStub(async (n, k) => {
      calls.push([n, k]);
      return `/выгрузка/${n}`;
    });
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.export", {}, "user");
    expect(await chooseFile(currentForm())).toBe("/выгрузка/Повёрнутая.dat");
    expect(calls).toEqual([["Повёрнутая.dat", "dat"]]);
    exportStore.set((s) => ({ form: { ...s.form, format: "parquet" } }));
    expect(currentForm().path).toBe("/выгрузка/Повёрнутая.dat"); // kept, but it is not the file of this format
    expect(html()).toContain("не выбран");
    await chooseFile(currentForm());
    expect(calls[1]).toEqual(["Повёрнутая.parquet", "parquet"]);
    expect(html()).toContain("/выгрузка/Повёрнутая.parquet");
  });

  it("«Сохранить…»: the file is asked for, then the command runs with the form; «Отмена» of the save window starts nothing", async () => {
    bridgeStub(async () => null);
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.export", {}, "user");
    await saveFromWindow(app);
    expect(exportsSent()).toEqual([]);
    expect(currentRun().phase).toBe("idle");
    const picked: string[] = [];
    bridgeStub(async (n) => {
      picked.push(n);
      return "/выгрузка/model.csv";
    });
    exportStore.set((s) => ({ form: { ...s.form, format: "csv" } }));
    await saveFromWindow(app);
    expect(picked).toEqual(["Повёрнутая.csv"]);
    expect(exportsSent()).toHaveLength(1);
    expect(exportsSent()[0].body).toEqual(RUNS.csv.request);
    expect(currentRun().phase).toBe("done");
    // the file chosen stays for this format: the second «Сохранить…» does not ask again
    await saveFromWindow(app);
    expect(picked).toHaveLength(1);
  });

  it("the window of the project that closed starts clean", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.export", {}, "user");
    exportStore.set((s) => ({ form: { ...s.form, format: "csv" } }));
    resetExport();
    expect(currentForm()).toEqual(EMPTY_FORM);
    expect(currentRun().phase).toBe("idle");
  });
});
