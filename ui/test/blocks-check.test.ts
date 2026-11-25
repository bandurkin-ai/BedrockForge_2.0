// «Перепроверить» of a block model: the command `blocks.check` and the checks in «Свойства» (ТЗ 7.31, Т8.7-5; e17-api §11; Э17 17.41)
// on the REAL answers of the core (`core/tests/data/blocks/check_window_answers.json`, made by
// `core/tests/test_blocks_check_window_answers.py` through `POST /blocks/{id}/check`, the job `blocks.check` and `GET /blocks/{id}`):
// the request is `POST /blocks/{id}/check {}`; the checks of the job reach the details of the model (the version stays); the window
// of the model draws the duplicates, the overlaps, the empty and constant fields, the volume and the examples from them; the
// refusals 404 / 423 and the failed job `version_conflict` are told in Russian with their hints («Перечитать» for the conflict).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { jobsStore } from "../src/modules/jobs/state";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { bindBlocks } from "../src/modules/blocks/bind";
import { cancelCheck, checkStore, isChecking, readCheckResult, rereadModel, resetChecks, runOf, summaryText } from "../src/modules/blocks/check";
import { CheckControl } from "../src/modules/blocks/checkButton";
import { commands } from "../src/modules/blocks/checkCommands";
import { blocksStore } from "../src/modules/blocks/state";
import { checkLines, readDetails } from "../src/modules/blocks/window/details";
import { PropsPanel } from "../src/modules/blocks/window/PropsPanel";

const A: any = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/check_window_answers.json"), "utf-8"));
const CLEAN = A.ids.clean as number;
const DIRTY = A.ids.dirty as number;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

interface Case {
  request: Record<string, unknown>;
  status: number;
  body?: Record<string, any>;
  job?: Record<string, any>;
}

const stub = {
  sent: [] as { method: string; path: string; body: Record<string, unknown> }[],
  /** the models as the core tells them now: `GET /blocks/{id}` */
  models: new Map<number, Record<string, any>>(),
  /** what `POST /blocks/{id}/check` answers: a recorded case */
  answers: new Map<number, Case>(),
  /** the status of the job when it is started (a running one is finished by a cancel) */
  status: undefined as string | undefined,
  /** what the core tells about the model after the job: id → the body */
  after: new Map<number, Record<string, any>>(),
  jobs: new Map<number, Record<string, unknown>>(),
  next: 5,
};

function startJob(c: Case): Response {
  const id = stub.next++;
  const st = stub.status ?? (c.job?.status as string);
  const done = st === "done";
  stub.jobs.set(id, { source: "proj", id, ...c.job, status: st, progress: done ? 1 : 0.4, params: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null, result: done ? c.job?.result : null });
  if (done) {
    const mid = (c.job?.result as { model_id: number }).model_id;
    if (stub.after.has(mid)) stub.models.set(mid, stub.after.get(mid)!);
  }
  return json({ source: "proj", id });
}

let app: App;
const posts = () => stub.sent.filter((r) => r.method === "POST" && /\/check$/.test(r.path));
const html = (el: ReactElement) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const list = () => [...stub.models.values()] as BlockModelInfo[];

function load(): void {
  blocksStore.set({ list: list(), loaded: true, error: null, details: Object.fromEntries(list().map((m) => [m.id, m])), detailErrors: {} });
}

beforeEach(() => {
  stub.sent.length = 0;
  stub.status = undefined;
  stub.jobs.clear();
  stub.next = 5;
  stub.models = new Map([
    [CLEAN, A.clean.before.body],
    [DIRTY, A.dirty.before.body],
  ]);
  stub.after = new Map([
    [CLEAN, A.clean.after.body],
    [DIRTY, A.dirty.after.body],
  ]);
  stub.answers = new Map([
    [CLEAN, A.clean.check],
    [DIRTY, A.dirty.check],
  ]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      stub.sent.push({ method, path, body });
      if (method === "GET" && path === "/blocks") return json({ block_models: list() });
      const one = /^\/blocks\/(\d+)$/.exec(path);
      if (method === "GET" && one && stub.models.has(Number(one[1]))) return json(stub.models.get(Number(one[1])));
      const check = /^\/blocks\/(\d+)\/check$/.exec(path);
      if (method === "POST" && check) {
        const c = stub.answers.get(Number(check[1]));
        if (!c) return json({ code: "block_model_not_found", message: "Блочная модель не найдена.", hint: "Обновите дерево проекта." }, 404);
        return c.status === 200 ? startJob(c) : json(c.body, c.status);
      }
      if (method === "GET" && path === "/jobs") return json({ jobs: [...stub.jobs.values()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      const job = /^\/jobs\/proj\/(\d+)$/.exec(path);
      if (method === "GET" && job && stub.jobs.has(Number(job[1]))) return json(stub.jobs.get(Number(job[1])));
      const cancel = /^\/jobs\/proj\/(\d+)\/cancel$/.exec(path);
      if (method === "POST" && cancel) {
        const j = stub.jobs.get(Number(cancel[1]));
        if (j) stub.jobs.set(Number(cancel[1]), { ...j, status: "cancelled", result: null });
        return json({});
      }
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  jobsStore.set({ jobs: [], workers: null, error: null });
  resetChecks();
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  if (!app.registry.get("blocks.check")) app.registry.register(commands); // until the catalog names `checkCommands.ts`
  bindBlocks(app);
  load();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
});

const attr = (h: string, id: string, name: string): string | undefined => new RegExp(`<[a-z]+ [^>]*data-testid="${id}"[^>]*>`).exec(h)?.[0].match(new RegExp(`${name}="([^"]*)"`))?.[1];
const has = (h: string, id: string) => new RegExp(`data-testid="${id}"`).test(h);
const disabledOf = (h: string, id: string) => /disabled/.test(new RegExp(`<button [^>]*data-testid="${id}"[^>]*>`).exec(h)?.[0] ?? "");
const checksOf = (id: number) => readDetails(blocksStore.get().details[id]).checks;

describe("the command `blocks.check`", () => {
  it("is a record of the registry: flat `model_id`, no confirmation, open to the assistant", () => {
    const c = app.registry.get("blocks.check")!;
    expect(c.title).toBe("Перепроверить блочную модель");
    expect(Object.keys(c.params ?? {})).toEqual(["model_id"]);
    expect(c.params?.model_id.type).toBe("integer");
    expect(c.confirm).toBeUndefined();
    expect(c.uiOnly).toBeUndefined();
    expect(c.description).toMatch(/версия модели не меняется/);
    expect(commands.filter((x) => x.id === "blocks.check")).toHaveLength(1);
  });

  it("is off with no project and in «только просмотр», with the reason", () => {
    expect(app.registry.reason("blocks.check")).toBeNull();
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("blocks.check")).toBe("Проект открыт только для чтения");
    app.store.set({ projectReadOnly: false, projectName: null });
    expect(app.registry.reason("blocks.check")).not.toBeNull();
  });

  it("starts `POST /blocks/{id}/check {}`, follows the job and brings the checks to «Свойства»: the version stays", async () => {
    const before = blocksStore.get().details[DIRTY];
    expect(before.version).toBe(A.dirty.before.body.version);
    expect(checksOf(DIRTY)).toBeNull();
    const r = await app.registry.execute("blocks.check", { model_id: DIRTY }, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(posts().map((p) => [p.path, p.body])).toEqual([[`/blocks/${DIRTY}/check`, {}]]);
    const res = A.dirty.check.job.result;
    expect(r.result).toBe(summaryText("С замечаниями", res.checks));
    const after = blocksStore.get().details[DIRTY];
    expect(after.version).toBe(before.version);
    expect((after as { checks?: unknown }).checks).toEqual(res.checks);
    expect(checksOf(DIRTY)).toMatchObject({ duplicates: 0, overlaps: { n: 4, method: "exact" }, empty: ["EMPTYF"], constant: ["SG"] });
    expect(runOf(DIRTY)).toMatchObject({ phase: "done", error: null, conflict: false });
    expect(r.result).toContain("перекрытия блоков: 4 (точно)");
    expect(r.result).toContain("поля без единого значения: EMPTYF");
    expect(r.result).toMatch(/объём блоков 12\s125 м³/);
  });

  it("a model without findings: «замечаний нет»; the gap of a grade is not a zero anywhere", async () => {
    const r = await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/^Проверка модели «Чистая» выполнена: замечаний нет; объём блоков /);
    expect(checkLines(checksOf(CLEAN))).toEqual({ lines: [], clean: true });
    const au = blocksStore.get().details[CLEAN].fields.find((f) => f.name === "AU")!;
    expect(au.stats).toMatchObject({ missing: 1 }); // the gap stays a gap in the statistics of the field
  });

  it("without `model_id`: the selected model; with several and none selected — told in Russian; a model that is not there — too", async () => {
    const none = await app.registry.execute("blocks.check", {}, "claude");
    expect(none.ok).toBe(false);
    expect(none.result).toBe("Не выбрана блочная модель. Выберите модель в дереве проекта или укажите model_id.");
    app.store.set({ selected: { kind: "blocks", id: CLEAN } as never });
    const sel = await app.registry.execute("blocks.check", {}, "claude");
    expect(sel.ok, sel.result).toBe(true);
    expect(posts()[0].path).toBe(`/blocks/${CLEAN}/check`);
    const gone = await app.registry.execute("blocks.check", { model_id: 999 }, "claude");
    expect(gone.result).toBe("Блочная модель №999 не найдена. Номера моделей — в дереве проекта, раздел «Блочные модели».");
    expect(posts()).toHaveLength(1);
  });

  it("a project open for reading never reaches the core; the core's 423 and 404 are told with their hints", async () => {
    app.store.set({ projectReadOnly: true });
    const ro = await app.registry.execute("blocks.check", { model_id: CLEAN }, "claude");
    expect(ro.ok).toBe(false);
    expect(ro.result).toBe("Проект открыт только для чтения");
    expect(posts()).toEqual([]);
    app.store.set({ projectReadOnly: false });
    stub.answers.set(CLEAN, A.read_only);
    const late = await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(late.ok).toBe(false);
    expect(late.result).toBe(`${A.read_only.body.message} ${A.read_only.body.hint}`);
    expect(runOf(CLEAN)).toMatchObject({ phase: "failed", conflict: false, error: { code: "project_read_only" } });
    stub.answers.set(CLEAN, A.missing);
    const missing = await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(missing.result).toBe(`${A.missing.body.message} ${A.missing.body.hint}`);
    expect(checksOf(CLEAN)).toBeNull();
  });

  it("the model changed while it was checked: the failed job is told, the model is read again with «Перечитать», nothing is written", async () => {
    stub.answers.set(CLEAN, A.conflict.check);
    stub.models.set(CLEAN, A.conflict.before.body);
    stub.after.set(CLEAN, A.conflict.after.body);
    load();
    const err = A.conflict.check.job.error;
    const r = await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${err.message} ${err.hint} Нажмите «Перечитать», затем повторите проверку.`);
    expect(runOf(CLEAN)).toMatchObject({ phase: "failed", conflict: true, error: { code: "version_conflict" } });
    const h = html(createElement(CheckControl, { id: CLEAN }));
    expect(attr(h, "props-check-error", "data-code")).toBe("version_conflict");
    expect(has(h, "props-check-reread")).toBe(true);
    expect(checksOf(CLEAN)).toEqual(readDetails(A.conflict.before.body).checks);
    // «Перечитать»: the core now tells the model of the next version
    stub.models.set(CLEAN, A.conflict.after.body);
    await rereadModel(CLEAN);
    expect(blocksStore.get().details[CLEAN].version).toBe(A.conflict.after.body.version);
    expect(blocksStore.get().list.find((b) => b.id === CLEAN)?.version).toBe(A.conflict.after.body.version);
    expect(runOf(CLEAN)).toBeUndefined();
    expect(has(html(createElement(CheckControl, { id: CLEAN })), "props-check-reread")).toBe(false);
  });

  it("«Отменить»: the job is cancelled, the run says nothing was changed, the old checks stay", async () => {
    stub.status = "running";
    const run = app.registry.execute("blocks.check", { model_id: DIRTY }, "user");
    await vi.waitFor(() => expect(runOf(DIRTY)?.job).toBeTruthy());
    expect(isChecking(DIRTY)).toBe(true);
    const busy = await app.registry.execute("blocks.check", { model_id: DIRTY }, "user");
    expect(busy.result).toBe("Модель «С замечаниями» уже проверяется. Дождитесь конца или отмените проверку кнопкой «Отменить».");
    let h = html(createElement(CheckControl, { id: DIRTY }));
    expect(attr(h, "props-check", "data-phase")).toBe("running");
    expect(disabledOf(h, "props-check-run")).toBe(true);
    expect(has(h, "props-check-cancel")).toBe(true);
    await cancelCheck(DIRTY);
    const r = await run;
    expect(stub.sent.some((x) => x.method === "POST" && /^\/jobs\/proj\/\d+\/cancel$/.test(x.path))).toBe(true);
    expect(r).toEqual({ ok: true, result: "Проверка модели «С замечаниями» отменена: прежние результаты не изменены" });
    expect(runOf(DIRTY)?.phase).toBe("cancelled");
    expect(checksOf(DIRTY)).toBeNull();
    h = html(createElement(CheckControl, { id: DIRTY }));
    expect(disabledOf(h, "props-check-run")).toBe(false);
    expect(has(h, "props-check-cancel")).toBe(false);
  });

  it("a project that changes drops the runs", async () => {
    await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(runOf(CLEAN)).toBeDefined();
    projectStore.set({ current: { uid: "p2" } as ProjectInfo });
    expect(checkStore.get().runs).toEqual({});
  });
});

describe("«Свойства» of the window of the model", () => {
  it("before the run: «ещё не выполнялись» and the button; after: the findings of the real answer", async () => {
    let h = html(createElement(PropsPanel, { id: DIRTY }));
    expect(h).toContain("Проверки ещё не выполнялись.");
    expect(has(h, "props-check-run")).toBe(true);
    expect(disabledOf(h, "props-check-run")).toBe(false);
    expect(h).toContain("Перепроверить");
    await app.registry.execute("blocks.check", { model_id: DIRTY }, "user");
    h = html(createElement(PropsPanel, { id: DIRTY }));
    expect(h).not.toContain("Проверки ещё не выполнялись.");
    expect(h).toContain("Перекрытия блоков: 4 (точно)");
    expect(h).toContain("Поля без единого значения: EMPTYF");
    expect(h).toContain("Поля с одним и тем же значением: SG");
    expect(h).toMatch(/Объём блоков/);
    expect(h).toContain("строка 4: перекрытие блоков");
    expect(h).toContain("строка 12: перекрытие блоков");
    expect(attr(h, "props-check", "data-phase")).toBe("done");
    expect(h).toContain("Проверка модели «С замечаниями» выполнена");
  });

  it("a clean model says «Замечаний нет»", async () => {
    await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(html(createElement(PropsPanel, { id: CLEAN }))).toContain("Замечаний нет");
  });

  it("the button is off in «только просмотр» (with the reason) and the progress and the refusal are shown by the run", async () => {
    app.store.set({ projectReadOnly: true });
    let h = html(createElement(CheckControl, { id: CLEAN }));
    expect(disabledOf(h, "props-check-run")).toBe(true);
    expect(attr(h, "props-check-run", "title")).toContain("Проект открыт только для чтения");
    expect(has(h, "props-check-reason")).toBe(true);
    app.store.set({ projectReadOnly: false });
    // a running job: the percent and the stage of the line of «Задания»
    stub.status = "running";
    const run = app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    await vi.waitFor(() => expect(runOf(CLEAN)?.job).toBeTruthy());
    const job = runOf(CLEAN)!.job!;
    jobsStore.set({ jobs: [{ id: job.id, source: job.source, kind: "blocks.check", title: "", queue: "quick", status: "running", progress: 0.42, message: "Проверки", error: null, result: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null }], workers: null, error: null });
    h = html(createElement(CheckControl, { id: CLEAN }));
    expect(h).toContain("Проверка 42 % · Проверки");
    await cancelCheck(CLEAN);
    await run;
  });
});

describe("the result of the job", () => {
  it("is read as the core wrote it; a number that is not known stays `null`", () => {
    const res = A.dirty.check.job.result;
    expect(readCheckResult(res)).toEqual({ modelId: DIRTY, checks: res.checks });
    const nulled = { ...res, checks: { ...res.checks, volume: null } };
    expect(readDetails({ checks: readCheckResult(nulled)!.checks }).checks?.volume).toBeNull();
    expect(summaryText("М", nulled.checks)).not.toMatch(/объём/);
    for (const bad of [null, {}, { model_id: 1 }, { model_id: 1, checks: {} }, { model_id: "1", checks: res.checks }, { model_id: 1, checks: { ...res.checks, empty: "EMPTYF" } }]) expect(readCheckResult(bad)).toBeNull();
  });

  it("an answer without the checks is told in Russian and writes nothing", async () => {
    const bad = { ...A.clean.check, job: { ...A.clean.check.job, result: { model_id: CLEAN } } };
    stub.answers.set(CLEAN, bad);
    const r = await app.registry.execute("blocks.check", { model_id: CLEAN }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe("Ядро вернуло проверку без результата. Повторите проверку; если повторится — обновите программу.");
    expect(checksOf(CLEAN)).toBeNull();
  });
});
