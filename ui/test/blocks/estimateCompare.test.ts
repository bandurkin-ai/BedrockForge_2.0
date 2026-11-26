// The window and the command «Сравнить оценки» (Э20, 20.16; ТЗ 7.35 Д-37, ТД37-2; ADR 0027 решение 25; вопросы № 108, 109) on the REAL answers of the core (`estimate_compare`, `compare_post` of
// `core/tests/data/resources/contract_answers.json`: the comparison of OK_EST and ID_EST of a model of 8 blocks; the domains Измеренные, Выявленные, Предполагаемые; STATUS changed in 2 blocks):
// the declaration of the command (Russian, no keys, the window without the fields), the request = the body the core gets, the job followed to its end, the statistics of Δ, the histogram by the
// number of blocks, the table by the domains (the means of A, B and Δ, STATUS; a gap is «—», not 0), the refusals in Russian, «Показать Δ в 3D» through `blocks.show` and `blocks.style`.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commandTools } from "../../src/shell/commands";
import { AppContext } from "../../src/shell/AppContext";
import { setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import type { BlockModelInfo } from "../../src/modules/blocks/api";
import { BLOCK_STYLES_KEY, resetShow, revealOnShow, setPicked, styleIn } from "../../src/modules/blocks/display";
import { filtersStore } from "../../src/modules/blocks/filters/state";
import { blocksStore } from "../../src/modules/blocks/state";
import { readCompareResult } from "../../src/modules/blocks/compare/api";
import { bindEstimateCompare, windowOfCompare } from "../../src/modules/blocks/compare/bind";
import { COMPARE_BUTTONS, commands } from "../../src/modules/blocks/compare/commands";
import { EstimateCompareWindow } from "../../src/modules/blocks/compare/EstimateCompareWindow";
import { bars, bodyOfForm, defaultOut, defaultPair, resultText, signed, symmetricRange } from "../../src/modules/blocks/compare/model";
import { COMPARE_WINDOW, compareStore, currentForm, resetCompare, setForm } from "../../src/modules/blocks/compare/state";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { viewStore } from "../../src/modules/scene/bind";
import { answer } from "../resources/harness";
import { answerDialog } from "../blocks-fields-kit";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const REAL = answer("estimate_compare").body as Record<string, unknown>;
const POST = answer("compare_post").body;
const RESULT = readCompareResult(REAL)!;
const JOB = { source: "project", id: 1 };

const field = (name: string, kind: "number" | "category" = "number", extra: Record<string, unknown> = {}) => ({ name, kind, unit: null, ...extra });
const FIELDS = [field("OK_EST"), field("ID_EST"), field("OK_STATUS"), field("ID_STATUS"), field("DOM", "category", { labels: ["Измеренные", "Выявленные", "Предполагаемые"] }), field("SG")];
const model = (fields: unknown[]): BlockModelInfo =>
  ({ id: 1, name: "Оценка", folder: null, version: 2, extent: null, crs: null, grid: { corner: [0, 0, 0], counts: [2, 2, 2], size: [10, 10, 10], order: "F", variable: false }, cells: 8, fields, source: null, warnings: [], size: null, created_at: "", updated_at: "" }) as unknown as BlockModelInfo;

interface Sent {
  method: string;
  path: string;
  body: Record<string, unknown>;
}
let app: App;
let unbind: () => void;
const sent: Sent[] = [];
let written = false;
let job: { status: string; result: unknown; error: unknown };
let post: () => Response;

const jobRow = () => ({ ...JOB, kind: "estimate.compare", title: "estimate.compare", queue: "quick", status: job.status, progress: job.status === "done" ? 1 : 0.4, message: job.status === "done" ? "Готово" : "Расчёт", error: job.error, result: job.result, params: null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null });
const current = (): BlockModelInfo => model(written ? [...FIELDS, field("OK_EST_ID_EST_D")] : FIELDS);

beforeEach(() => {
  sent.length = 0;
  written = false;
  job = { status: "done", result: REAL, error: null };
  post = () => {
    written = true;
    return json(POST);
  };
  windowStore.set({ windows: [] });
  jobsStore.set({ jobs: [], workers: null, error: null });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path.startsWith("/view/")) return json({ version: 1, value: body.value });
      if (route(method, path) === "POST /blocks/1/compare") return post();
      if (path === "/blocks") return json({ block_models: [current()] });
      if (path === "/blocks/1") return json(current());
      if (path === "/jobs") return json({ jobs: [jobRow()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}`) return json(jobRow());
      if (path === `/jobs/${JOB.source}/${JOB.id}/cancel`) return json({});
      if (path === "/filters") return json({ filters: [] });
      return json({ code: "not_found", message: "Нет", hint: path }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 1 });
  viewStore.set({ [BLOCK_STYLES_KEY]: undefined });
  app = createApp({ persist: false });
  app.store.set({ projectName: "П", projectReadOnly: false, selected: null });
  if (!app.registry.get("estimate.compare")) app.registry.register(commands);
  resetShow();
  setPicked(null);
  revealOnShow.clear();
  resetCompare();
  blocksStore.set({ list: [current()], loaded: true, error: null, details: { 1: current() }, detailErrors: {} });
  filtersStore.set({ rows: [], loaded: true, selected: null, edit: null });
  unbind = bindEstimateCompare(app);
});

afterEach(() => {
  unbind();
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null, version: 0 });
});

const route = (method: string, path: string) => `${method} ${path}`;
const posts = () => sent.filter((s) => s.method === "POST" && s.path === "/blocks/1/compare");
const windows = () => windowStore.get().windows.filter((w) => w.kind === COMPARE_WINDOW);
const draw = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(EstimateCompareWindow, { winId: windowOfCompare() ?? "w" })));
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;
const ARGS = { model_id: 1, field_a: "OK_EST", field_b: "ID_EST" };

describe("the declaration", () => {
  it("`estimate.compare`: Russian, no keys, no required fields (without them the window opens), the assistant sees it; the button of the ribbon is the command", () => {
    expect(commands.map((c) => c.id)).toEqual(["estimate.compare"]);
    const c = commands[0];
    expect(c.title).toBe("Сравнить оценки");
    expect(c.description).toMatch(/[А-Яа-я]/);
    expect(Object.keys(c.params ?? {})).toEqual(["model_id", "field_a", "field_b", "domain", "out"]);
    expect(c.required).toBeUndefined();
    expect(c.uiOnly).toBeUndefined();
    // Δ is written into the model (a field that is there is replaced): the call of the assistant is asked first, as `blocks.calc`
    expect(c.confirm).toBe(true);
    expect(JSON.stringify(commands)).not.toMatch(/"key"/);
    expect(commandTools(commands).map((t) => t.name)).toEqual(["ui_estimate_compare"]);
    expect(app.registry.get("estimate.compare")?.run).toBeTypeOf("function");
    expect(app.registry.reason("estimate.compare")).toBeNull();
    for (const b of COMPARE_BUTTONS) {
      expect(app.registry.get(b.command)).toBeDefined();
      expect(b.label + b.hint).toMatch(/[А-Яа-я]/);
    }
    expect(COMPARE_BUTTONS[0].label).toBe("Сравнить оценки");
  });
});

describe("the real answer is what the window reads", () => {
  it("the result: Δ, the statistics, the 50 bins of the histogram, the domains, the status", () => {
    expect(RESULT).toMatchObject({ model_id: 1, field: "OK_EST_ID_EST_D", a: "OK_EST", b: "ID_EST", field_written: true, version: 3, rows: 8 });
    expect(RESULT.stats.n).toBe(6);
    expect(RESULT.stats.percentiles["50"]).toBeCloseTo(-0.075, 12);
    expect(RESULT.histogram.edges).toHaveLength(51);
    expect(RESULT.histogram.counts).toHaveLength(50);
    expect(bars(RESULT.histogram)).toHaveLength(50);
    expect(bars(RESULT.histogram).reduce((n, b) => n + b.count, 0)).toBe(RESULT.stats.n); // by the number of blocks: all of them
    expect(RESULT.domains.map((d) => d.domain)).toEqual(["Измеренные", "Выявленные", "Предполагаемые"]);
    expect(RESULT.status).toEqual({ a: "OK_STATUS", b: "ID_STATUS", changed: 2 });
    expect(RESULT.status?.changed).toBe(RESULT.domains.reduce((n, d) => n + (d.status_changed ?? 0), 0));
    expect(readCompareResult({})).toBeNull();
    expect(bars({ edges: [0, 1], counts: [1, 2] })).toEqual([]); // a broken pair of the core gives no bars
  });
  it("the words: the statistics with a sign, the range of the 3D scale is symmetric around 0, a gap is «—»", () => {
    expect(signed(0)).toBe("0");
    expect(signed(null)).toBe("—");
    expect(signed(0.5)).toBe("+0,5");
    expect(signed(-0.25)).toMatch(/^[-−]0,25$/);
    expect(symmetricRange(RESULT.stats)).toEqual({ from: -0.25000000000000044, to: 0.25000000000000044 });
    expect(symmetricRange({ ...RESULT.stats, min: 0, max: 0 })).toBeNull();
    expect(symmetricRange({ ...RESULT.stats, min: null, max: null })).toBeNull();
    const text = resultText(RESULT, "Оценка");
    expect(text).toContain("Δ = A − B записана в поле OK_EST_ID_EST_D");
    expect(text).toContain("Домен «Предполагаемые»");
    expect(text).toContain("STATUS (OK_STATUS и ID_STATUS): сменился у 2 бл.");
  });
  it("the form: the pair by default is the two fields `*_EST`, the name of Δ is `<A>_<B>_D`, the domain is only a category", () => {
    expect(defaultPair(model(FIELDS))).toEqual({ a: "OK_EST", b: "ID_EST" });
    expect(defaultPair(model([field("AU"), field("CU"), field("DOM", "category")]))).toEqual({ a: "AU", b: "CU" });
    expect(defaultPair(model([]))).toEqual({ a: "", b: "" });
    expect(defaultOut("OK_EST", "ID_EST")).toBe("OK_EST_ID_EST_D");
    const none = bodyOfForm({ modelId: null, a: "", b: "", domain: "", out: "" });
    expect(none).toMatchObject({ ok: false, issue: { field: "model" } });
    expect(bodyOfForm({ modelId: 1, a: "OK_EST", b: "", domain: "", out: "" })).toMatchObject({ ok: false, issue: { field: "b" } });
    expect(bodyOfForm({ modelId: 1, a: "OK_EST", b: "ID_EST", domain: " DOM ", out: " " })).toEqual({ ok: true, modelId: 1, body: { field_a: "OK_EST", field_b: "ID_EST", domain: "DOM" } });
  });
});

describe("`estimate.compare`", () => {
  it("without the fields it opens one window on the model chosen in the tree; the fields are the pair of the estimates; nothing is sent", async () => {
    app.store.set({ selected: { kind: "blocks", id: 1 } });
    const r = await app.registry.execute("estimate.compare", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Сравнить оценки»" });
    await app.registry.execute("estimate.compare", {}, "user");
    expect(windows()).toHaveLength(1);
    expect(windows()[0].title).toBe("Сравнить оценки · Оценка");
    expect(currentForm()).toMatchObject({ modelId: 1, a: "OK_EST", b: "ID_EST", domain: "", out: "" });
    expect(posts()).toHaveLength(0);
    const h = draw();
    expect(h).toContain('data-testid="ecmp-window"');
    expect(h).toContain('data-testid="ecmp-hint"');
    expect(h).toMatch(/<option value="OK_EST" selected="">OK_EST<\/option>/);
    expect(h).not.toMatch(/<option value="DOM">DOM<\/option><option value="SG"/); // the category is not in the choice of A
    expect(h).toContain('placeholder="OK_EST_ID_EST_D"');
  });

  it("a call of the assistant is asked first (Δ is written into the model): «Нет» — nothing is sent, «Разрешить» — the job runs", async () => {
    const no = app.registry.execute("estimate.compare", ARGS, "claude");
    expect(await answerDialog(false)).toContain("Сравнить оценки");
    expect((await no).ok).toBe(false);
    expect(posts()).toHaveLength(0);
    const yes = app.registry.execute("estimate.compare", ARGS, "claude");
    await answerDialog(true);
    expect((await yes).ok).toBe(true);
    expect(posts()).toHaveLength(1);
  });

  it("the request is the body of the form (the domain and the name only when given); the job is followed; the answer is the text of the result", async () => {
    const run = app.registry.execute("estimate.compare", { ...ARGS, domain: "DOM", out: "DELTA" }, "claude");
    await answerDialog(true);
    const r = await run;
    expect(r.ok).toBe(true);
    expect(posts()[0].body).toEqual({ field_a: "OK_EST", field_b: "ID_EST", domain: "DOM", out: "DELTA" });
    expect(r.result).toContain("Сравнение оценок «OK_EST» и «ID_EST» модели «Оценка»");
    expect(r.result).toContain("Δ = A − B записана в поле OK_EST_ID_EST_D");
    expect(r.result).toContain("блоков с Δ 6");
    expect(compareStore.get().run).toMatchObject({ phase: "done", modelId: 1 });
    expect(windows(), "the assistant does not open a window").toHaveLength(0);
    sent.length = 0;
    await app.registry.execute("estimate.compare", ARGS, "user");
    expect(posts()[0].body).toEqual({ field_a: "OK_EST", field_b: "ID_EST" }); // nothing but the fields
  });

  it("the field Δ is in the list of the fields of the model after the run (the list is read again)", async () => {
    await app.registry.execute("estimate.compare", ARGS, "user");
    expect(blocksStore.get().list[0].fields.map((f) => f.name)).toContain("OK_EST_ID_EST_D");
  });

  it("from the interface: the window opens and shows the statistics, the histogram (50 bars), the table by the domains, the words of the run", async () => {
    const r = await app.registry.execute("estimate.compare", ARGS, "user");
    expect(r.ok).toBe(true);
    expect(windows()).toHaveLength(1);
    const h = draw();
    expect(h).toContain("Δ = OK_EST − ID_EST");
    expect(h).toContain("записано в модель");
    expect(count(h, /data-stat="/g)).toBe(12);
    expect(h).toMatch(/data-stat="Среднее">[-−]0,0967</);
    expect(h).toMatch(/data-stat="Блоков с Δ">6</);
    expect(h).toContain('data-testid="ecmp-hist"');
    expect(h).toContain('data-bars="50"');
    expect(count(h, /<rect x="/g)).toBeGreaterThanOrEqual(50);
    expect(count(h, /data-testid="ecmp-domain"/g)).toBe(3);
    for (const d of ["Измеренные", "Выявленные", "Предполагаемые"]) expect(h).toContain(`>${d}</td>`);
    expect(h).toContain("Сменился STATUS, блоков");
    expect(h).toContain("STATUS сменился у 2 бл.");
    expect(h).toContain("Пропуск в A или B");
    expect(h).not.toContain('data-testid="ecmp-error"');
    expect(h).toMatch(/data-testid="ecmp-3d"(?![^>]*disabled)/);
  });

  it("a gap is «—», not 0: the means of a domain without values and a model without STATUS", async () => {
    const domains = (REAL.domains as Record<string, unknown>[]).map((d, i) => (i === 2 ? { ...d, mean_a: null, mean_b: null, mean_delta: null, volume: null, status_changed: null } : d));
    job = { status: "done", result: { ...REAL, domains, status: null }, error: null };
    await app.registry.execute("estimate.compare", ARGS, "user");
    const h = draw();
    const rows = h.split('data-testid="ecmp-domain"').slice(1);
    const last = rows[2].split("</tr>")[0];
    expect(count(last, /<td[^>]*>—<\/td>/g)).toBe(5); // the volume, the means of A, B and Δ, STATUS
    expect(last).not.toMatch(/>0<\/td>/);
    expect(h).not.toContain("STATUS сменился");
  });

  it("the refusals are in Russian with a hint; nothing wrong reaches the core", async () => {
    const one = await app.registry.execute("estimate.compare", { model_id: 1, field_a: "OK_EST" }, "user");
    expect(one.ok).toBe(false);
    expect(one.result).toContain("Нужны оба поля");
    const miss = await app.registry.execute("estimate.compare", { model_id: 9, field_a: "OK_EST", field_b: "ID_EST" }, "user");
    expect(miss.result).toContain("Блочная модель №9 не найдена");
    app.store.set({ projectReadOnly: true });
    const ro = await app.registry.execute("estimate.compare", ARGS, "user");
    expect(ro.ok).toBe(false);
    expect(ro.result).toContain("только для чтения");
    expect((await app.registry.execute("estimate.compare", {}, "user")).ok, "the window still opens").toBe(true);
    expect(posts()).toHaveLength(0);
  });

  it("a refusal of the core stands in the window: no field → 404, a failed job", async () => {
    post = () => json({ code: "block_field_not_found", message: "Поля «AU_X» нет в модели «Оценка».", hint: "Поля модели: OK_EST, ID_EST." }, 404);
    const r = await app.registry.execute("estimate.compare", { ...ARGS, field_a: "AU_X" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Поля «AU_X» нет в модели");
    expect(compareStore.get().run.phase).toBe("failed");
    expect(draw()).toContain('data-code="block_field_not_found"');
    post = () => json(POST);
    job = { status: "failed", result: null, error: { code: "bad_values", message: "В полях нет общих значений.", hint: "Проверьте поля." } };
    const f = await app.registry.execute("estimate.compare", ARGS, "user");
    expect(f.result).toContain("В полях нет общих значений. Проверьте поля.");
    expect(draw()).toContain("Проверьте поля.");
  });

  it("a cancelled job writes no field and says so", async () => {
    job = { status: "cancelled", result: null, error: null };
    const r = await app.registry.execute("estimate.compare", ARGS, "user");
    expect(r.result).toContain("отменено: поле Δ не записано");
    expect(compareStore.get().run.phase).toBe("cancelled");
    expect(draw()).toContain("Сравнение отменено: поле Δ не записано.");
  });

  it("the window runs the command with the arguments of its form; a form that is not the one asked is replaced by what is asked", async () => {
    await app.registry.execute("estimate.compare", {}, "user");
    setForm({ domain: "DOM", out: "Д1" });
    await app.registry.execute("estimate.compare", { model_id: 1, field_a: "OK_EST", field_b: "ID_EST", domain: "DOM", out: "Д1" }, "user");
    expect(currentForm()).toMatchObject({ a: "OK_EST", b: "ID_EST", domain: "DOM", out: "Д1" });
    await app.registry.execute("estimate.compare", { model_id: 1, field_a: "ID_EST", field_b: "OK_EST" }, "user");
    expect(currentForm()).toMatchObject({ a: "ID_EST", b: "OK_EST", domain: "", out: "" });
    expect(windows()).toHaveLength(1);
  });
});

describe("«Показать Δ в 3D»", () => {
  it("the model is shown and coloured by the field Δ on a scale symmetric around 0, with the commands of the display of the block models", async () => {
    const { showDelta } = await import("../../src/modules/blocks/compare/bind");
    await expect(showDelta(app)).rejects.toThrow(/Поля Δ нет в модели/); // before the run
    await app.registry.execute("estimate.compare", ARGS, "user");
    const text = await showDelta(app);
    expect(text).toContain("Поле Δ «OK_EST_ID_EST_D» показано в 3D");
    expect(text).toContain("серые блоки — пропуск в A или B");
    const s = styleIn(viewStore.get(), 1);
    expect(s.visible).toBe(true);
    expect(s.colour).toMatchObject({ field: "OK_EST_ID_EST_D", kind: "scale" });
    expect(s.colour.from).toBeCloseTo(-0.25, 12);
    expect(s.colour.to).toBeCloseTo(0.25, 12);
    expect(compareStore.get().shown).toBe("1:OK_EST_ID_EST_D");
    expect(draw()).toContain('data-testid="ecmp-3d-note"');
  });

  it("Δ is 0 everywhere: the scale stays the field's own (no range to make)", async () => {
    job = { status: "done", result: { ...REAL, stats: { ...(REAL.stats as object), min: 0, max: 0 } }, error: null };
    await app.registry.execute("estimate.compare", ARGS, "user");
    const { showDelta } = await import("../../src/modules/blocks/compare/bind");
    await showDelta(app);
    const s = styleIn(viewStore.get(), 1);
    expect(s.colour.field).toBe("OK_EST_ID_EST_D");
    expect(s.colour.from).toBeNull();
    expect(s.colour.to).toBeNull();
  });
});
