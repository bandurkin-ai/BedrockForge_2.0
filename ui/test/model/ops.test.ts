/** Э9, подшаг 9.30 (ADR 0015 §6; ТЗ 7.23): the windows of the operations built from the schema of the core — 13
 * windows of 16 operations (`operations.json` is `GET /operations`: `cd core && python3 -c "from
 * bf_core.ops.schema import listing; …"`), the values → `params`, the open wireframe named with its open edges
 * and «Исправить каркас», `model.operation` with the confirmation of a call of Claude, `model.rebuild`, and the
 * job followed: its wireframes get `wireframe.check`, the report opens. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, setCoreTarget } from "../../src/shell/api";
import { createApp, type App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { jobsStore, type Job } from "../../src/modules/jobs/state";
import { bindModel, paramsArg } from "../../src/modules/model/bind";
import {
  DIALOG_COMMANDS,
  dialogOps,
  dialogsOf,
  dialogTitle,
  initialValues,
  numberText,
  openWireframeNote,
  paramsOf,
  parseLineNumbers,
  switchValues,
  visibleFields,
  type FormValues,
  type OpSpec,
} from "../../src/modules/model/opForms";
import { afterOperation, forgetOperations, operationsStore, refusalOf, startOperation } from "../../src/modules/model/run";
import { projectStore, type ProjectInfo } from "../../src/modules/projects/state";
import { loadSceneObjects, sceneObjects } from "../../src/modules/scene/objects";
import type { WireframeInfo } from "../../src/modules/wireframes/state";

const LIST = (JSON.parse(readFileSync(resolve(__dirname, "operations.json"), "utf-8")) as { operations: OpSpec[] }).operations;
const spec = (op: string): OpSpec => LIST.find((s) => s.op === op)!;

const wf = (id: number, p: Partial<WireframeInfo> = {}): WireframeInfo => ({
  id, name: `Тело ${id}`, version: 1, color: null, point_count: 8, triangle_count: 12, extent: [0, 0, 0, 1, 1, 1], dtm: null, folder: null, attrs: [], stats: { closed: true, open_edges: 0 }, ...p,
});
const WFS = [wf(41, { name: "Куб" }), wf(42, { name: "Тело 2", stats: { closed: false, open_edges: 14, nonmanifold_edges: 0 } }), wf(43, { name: "Рельеф", stats: { closed: false, open_edges: 120 } })];

/** Values that fill every field the way a person would. */
function filled(s: OpSpec): FormValues {
  const v = initialValues(s, { wireframeId: 41, layerId: 7 });
  for (const f of s.fields) {
    if (f.type === "wireframe" && v[f.key] === null) v[f.key] = f.kinds?.includes("open") ? 43 : 41;
    if (f.type === "wireframes" && !(v[f.key] as number[]).length) v[f.key] = [41, 44];
    if (f.type === "wireframes" && (v[f.key] as number[]).length < (f.min ?? 1)) v[f.key] = [41, 44];
    if ((f.type === "number" || f.type === "integer") && f.required && !v[f.key]) v[f.key] = "2,5";
    if (f.type === "vector" && !v[f.key]) v[f.key] = "100; 200,5; 300";
    if (f.type === "line" && f.required) v[f.key] = { layer_id: 7, text: "" };
  }
  if (s.op === "simplify") v.target_triangles = "1000";
  if (s.op === "volume_between") v.bottom = 43;
  return v;
}

describe("the windows of the operations from the schema of the core (ADR 0015 §6)", () => {
  it("16 operations, 13 windows — the three clips share one; every window has its command", () => {
    expect(LIST).toHaveLength(16);
    const dialogs = dialogsOf(LIST);
    expect(dialogs).toHaveLength(13);
    expect(new Set(Object.values(DIALOG_COMMANDS))).toEqual(new Set(dialogs));
    expect(dialogOps(LIST, "clip").map((s) => s.op)).toEqual(["clip_plane", "clip_polygon", "clip_surface"]);
    expect(dialogTitle(LIST, "clip")).toBe("Обрезать");
    expect(dialogTitle(LIST, "repair")).toBe("Исправить каркас");
    expect(LIST.find((s) => s.op === "edit_triangles")!.dialog).toBeNull(); // a panel of the edit mode
  });

  it("every one of the 13 windows builds its form and gives params the core takes", () => {
    for (const dialog of dialogsOf(LIST)) {
      for (const s of dialogOps(LIST, dialog)) {
        const values = filled(s);
        expect(visibleFields(s, values).length, s.op).toBeGreaterThan(0);
        const r = paramsOf(s, values);
        expect(r.error, s.op).toBeNull();
        for (const f of visibleFields(s, values)) if (f.required) expect(r.params, `${s.op}.${f.key}`).toHaveProperty(f.key);
      }
    }
    expect(paramsOf(spec("repair"), filled(spec("repair"))).params).toEqual({ wireframe_id: 41, weld_on: true, weld: 0.000001, drop_duplicate: true, drop_degenerate: true, fix_nonmanifold: true, fill_holes: true, orient: true });
    expect(paramsOf(spec("stitch"), filled(spec("stitch"))).params).toEqual({ contours: [{ layer_id: 7, lines: null }], start_cap: "flat", end_cap: "flat", cap_distance: 0.5 });
    expect(paramsOf(spec("clip_plane"), filled(spec("clip_plane"))).params).toEqual({ wireframe_id: 41, point: [100, 200.5, 300], normal: [0, 0, 1], keep: "both" });
  });

  it("defaults as the person reads them: a comma, no exponent; an empty number is not 0", () => {
    expect(numberText(0.5)).toBe("0,5");
    expect(numberText(1e-6)).toBe("0,000001");
    expect(initialValues(spec("smooth")).iterations).toBe("10");
    const s = spec("simplify");
    expect(paramsOf(s, { ...initialValues(s, { wireframeId: 41 }), tolerance: "0,25" }).params).toEqual({ wireframe_id: 41, tolerance: 0.25 }); // target_triangles — not sent
  });

  it("why the values cannot be sent — in Russian, with the field", () => {
    const c = spec("contours");
    expect(paramsOf(c, initialValues(c, { wireframeId: 43 }))).toMatchObject({ params: null, error: "Не задано: «Шаг».", field: "interval" });
    expect(paramsOf(c, { ...initialValues(c, { wireframeId: 43 }), interval: "пять" }).error).toBe("«Шаг» — не число. Введите число, например 0,5.");
    expect(paramsOf(c, { ...initialValues(c), interval: "5" }).error).toBe("Выберите: «Каркас».");
    const sm = spec("smooth");
    expect(paramsOf(sm, { ...initialValues(sm, { wireframeId: 41 }), strength: "1,5" }).error).toBe("«Сила» — от 0 до 0,95.");
    expect(paramsOf(sm, { ...initialValues(sm, { wireframeId: 41 }), iterations: "2,5" }).error).toBe("«Проходов» — целое число.");
    expect(paramsOf(spec("merge"), { wireframe_ids: [41] }).error).toBe("«Каркасы»: отметьте не меньше 2 объектов.");
    expect(paramsOf(spec("clip_plane"), { ...initialValues(spec("clip_plane"), { wireframeId: 41 }), point: "1; 2" }).error).toBe("«Точка плоскости X; Y; Z» — три числа через точку с запятой, например 0; 0; 1.");
    expect(paramsOf(spec("stitch"), { ...initialValues(spec("stitch")), contours: [{ layer_id: 7, text: "1, x" }] }).field).toBe("contours");
  });

  it("a field with `when` is shown and sent only while the other field has that value", () => {
    const s = spec("volume_between");
    const v = { ...initialValues(s), top: 43, bottom: 44, level: "250" };
    expect(visibleFields(s, v).map((f) => f.key)).toContain("bottom");
    expect(paramsOf(s, v).params).toEqual({ top: 43, mode: "surface", bottom: 44, report_only: false });
    expect(paramsOf(s, { ...v, mode: "level" }).params).toEqual({ top: 43, mode: "level", level: 250, report_only: false });
    const e = spec("extrude");
    expect(visibleFields(e, initialValues(e)).some((f) => f.key === "vector")).toBe(false);
    expect(visibleFields(e, { ...initialValues(e), direction: "vector" }).some((f) => f.key === "vector")).toBe(true);
  });

  it("line numbers from 1 become `lid`s; one closed line of a layer", () => {
    expect(parseLineNumbers("")).toBeNull();
    expect(parseLineNumbers("1, 3–5; 7 7")).toEqual([0, 2, 3, 4, 6]);
    expect(parseLineNumbers("0")).toBeUndefined();
    expect(parseLineNumbers("5-3")).toBeUndefined();
    const s = spec("clip_polygon");
    expect(paramsOf(s, { ...initialValues(s, { wireframeId: 41 }), polygon: { layer_id: 7, text: "2" } }).params).toMatchObject({ polygon: { layer_id: 7, line: 1 } });
    expect(paramsOf(s, { ...initialValues(s, { wireframeId: 41 }), polygon: { layer_id: 7, text: "" } }).params).toMatchObject({ polygon: { layer_id: 7, line: null } });
    expect(paramsOf(s, initialValues(s, { wireframeId: 41 })).error).toBe("Выберите слой: «Полигон в плане».");
  });

  it("another clip keeps the wireframe chosen", () => {
    const before = { ...initialValues(spec("clip_plane"), { wireframeId: 41 }), keep: "positive" };
    expect(switchValues(spec("clip_surface"), before)).toMatchObject({ wireframe_id: 41, keep: "both" });
  });

  it("an open wireframe where a solid is needed: named, its open edges, «Исправить каркас»", () => {
    const s = spec("boolean");
    expect(openWireframeNote(s, { ...initialValues(s), a: 41, b: [42] }, WFS)).toEqual({ text: "Каркас «Тело 2» не замкнут: открытых рёбер — 14. Запустите «Исправить каркас» и повторите.", fix: { op: "repair", wireframe_id: 42 } });
    expect(openWireframeNote(s, { ...initialValues(s), a: 41, b: [41] }, WFS)).toBeNull();
    expect(openWireframeNote(spec("split"), { wireframe_id: 42, max_parts: "200" }, WFS)).toBeNull(); // no solid needed
    const e = new CoreError(422, "wireframe_open", "Каркас «Тело 2» не замкнут: открытых рёбер — 14.", "Запустите «Исправить каркас» и повторите.");
    e.body = { code: "wireframe_open", open_edges: 14, fix: { op: "repair", wireframe_id: 42 } };
    expect(refusalOf(e)).toEqual({ text: "Каркас «Тело 2» не замкнут: открытых рёбер — 14. Запустите «Исправить каркас» и повторите.", fix: { op: "repair", wireframe_id: 42 } });
    expect(refusalOf(new CoreError(422, "bad_param", "«Шаг» должно быть больше нуля.", "")).fix).toBeNull();
  });
});

const job = (p: Partial<Job>): Job => ({
  id: 7, source: "p1", kind: "geometry.operation", title: "Булева операция", queue: "quick", status: "running", progress: 0, message: null, error: null, params: {}, result: null,
  attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null, ...p,
});

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function core() {
  const calls: Call[] = [];
  const state = { jobs: [] as Job[], refuse: false };
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (method !== "GET") calls.push({ method, path, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET") {
      if (path === "/operations") return json({ operations: LIST });
      if (path === "/lines/files") return json({ files: [] });
      if (path === "/rasters") return json({ rasters: [] });
      if (path === "/wireframes") return json({ wireframes: WFS });
      if (path === "/jobs") return json({ jobs: state.jobs, workers: null });
      return json({});
    }
    if (state.refuse) return json({ code: "wireframe_open", message: "Каркас «Тело 2» не замкнут: открытых рёбер — 14.", hint: "Запустите «Исправить каркас» и повторите.", open_edges: 14, fix: { op: "repair", wireframe_id: 42 } }, 422);
    if (path === "/wireframes/check") return json({ source: "p1", id: 90 });
    const summary = { op: body?.op ?? "boolean", title: "Булева операция", job: "geometry.operation", queue: "quick", size: 24, sources: [{ role: "a", kind: "wireframe", id: 41, name: "Куб" }], warning: null };
    state.jobs = [job({ id: 7 }), ...state.jobs];
    return json({ source: "p1", id: 7, ...summary, ...(path.endsWith("/rebuild") ? { rebuild_of: 41 } : {}) });
  });
  return { calls, state, fetch };
}

async function open(): Promise<{ app: App; srv: ReturnType<typeof core> }> {
  const app = createApp({ persist: false });
  bindModel(app); // the line of `shell/app.ts` (the orchestrator adds it)
  const srv = core();
  vi.stubGlobal("fetch", srv.fetch);
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект", projectReadOnly: false });
  await loadSceneObjects();
  return { app, srv };
}

beforeEach(() => forgetOperations());
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  projectStore.set({ current: null });
  sceneObjects.set({ files: [], rasters: [], wireframes: [], loaded: false, error: null });
  jobsStore.set({ jobs: [], workers: null, error: null });
  windowStore.set({ windows: [] });
});

describe("`model.*`, `model.operation` and `model.rebuild` (ADR 0015 §6 «Команды»)", () => {
  it("the 12 windows are bound (the 13th — «Построить ЦТМ» of modules/dtm); a closed project gives the reason", async () => {
    const { app } = await open();
    for (const id of Object.keys(DIALOG_COMMANDS)) expect(app.registry.get(id)!.stage, id).toBeUndefined();
    for (const id of ["model.operation", "model.rebuild"]) {
      expect(app.registry.get(id)!.confirm, id).toBe(true);
      expect(app.registry.get(id)!.stage, id).toBeUndefined();
    }
    for (const id of ["model.assemble", "model.triangles"]) expect(app.registry.get(id)!.stage, id).toBeUndefined(); // the panels of 9.32–9.33
    app.store.set({ projectName: null });
    expect(app.registry.reason("model.boolean")).toBe("Откройте или создайте проект");
  });

  it("a window command opens the window of its schema with the chosen object", async () => {
    const { app } = await open();
    app.store.set({ selected: { kind: "wireframe", id: 41 } });
    const r = await app.registry.execute("model.clip", { op: "clip_surface" });
    expect(r).toEqual({ ok: true, result: "Открыто окно «Обрезать»" });
    expect(windowStore.get().windows.at(-1)).toMatchObject({ kind: "model-operation", title: "Обрезать", props: { dialog: "clip", op: "clip_surface", wireframeId: 41 } });
    expect((await app.registry.execute("model.repair", { wireframe_id: 42 })).result).toBe("Открыто окно «Исправить каркас»");
    expect(windowStore.get().windows.at(-1)!.props).toEqual({ dialog: "repair", wireframeId: 42 });
    expect((await app.registry.execute("model.repair", {}, "claude")).ok).toBe(false); // a window needs a person
  });

  it("a call of Claude runs only after the person allows it; then the job starts and is followed", async () => {
    const { app, srv } = await open();
    const confirm = vi.fn(async () => false);
    app.registry.hooks.confirm = confirm;
    const args = { op: "boolean", params: JSON.stringify({ op: "difference", a: 41, b: [44] }), name: "Карьер − тело" };
    expect((await app.registry.execute("model.operation", args, "claude")).ok).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(srv.calls).toEqual([]);
    confirm.mockResolvedValue(true);
    const r = await app.registry.execute("model.operation", args, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Запущена операция «Булева операция» (задание №7, быстрая очередь)");
    expect(srv.calls).toEqual([{ method: "POST", path: "/operations", body: { op: "boolean", params: { op: "difference", a: 41, b: [44] }, name: "Карьер − тело" } }]);
    expect(app.store.get().workspace.bottomTab).toBe("jobs");
  });

  it("a refusal names the open wireframe and its open edges and the way to fix it", async () => {
    const { app, srv } = await open();
    srv.state.refuse = true;
    const r = await app.registry.execute("model.operation", { op: "boolean", params: '{"a": 41, "b": [42]}' });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("открытых рёбер — 14");
    expect(r.result).toContain("model.repair, wireframe_id 42");
    expect((await app.registry.execute("model.operation", { op: "boolean", params: "[1]" })).result).toContain("params — не JSON-объект.");
    expect(() => paramsArg("{")).toThrow("params — не JSON-объект.");
    expect(paramsArg("")).toEqual({});
  });

  it("«Перестроить» — the chosen wireframe or the layer named", async () => {
    const { app, srv } = await open();
    app.store.set({ selected: { kind: "wireframe", id: 41 } });
    expect((await app.registry.execute("model.rebuild")).result).toContain("Запущено «Перестроить» для «Куб»");
    await app.registry.execute("model.rebuild", { layer_id: 9 });
    expect(srv.calls.map((c) => c.path)).toEqual(["/wireframes/41/rebuild", "/lines/layers/9/rebuild"]);
    app.store.set({ selected: null });
    expect((await app.registry.execute("model.rebuild")).result).toContain("Не выбран объект для «Перестроить».");
  });

  it("the window reads the list of operations once", async () => {
    const { app, srv } = await open();
    await app.registry.execute("model.split", { wireframe_id: 41 });
    await app.registry.execute("model.merge");
    expect(srv.fetch.mock.calls.filter(([u]) => String(u).endsWith("/operations")).length).toBe(1);
    expect(operationsStore.get().list).toHaveLength(16);
  });
});

describe("after the job of an operation (ADR 0015 §6 «После операции»)", () => {
  it("its wireframes get `wireframe.check`; the report of a job started here opens; once", async () => {
    await open();
    await startOperation({ op: "boolean", params: { op: "union", a: 41, b: [44] } });
    const startMany = vi.fn(async () => ({ source: "p1", id: 90 }));
    const showReport = vi.fn();
    const done = job({ id: 7, status: "done", finished_at: "t1", result: { op: "boolean", title: "Булева операция", name: "Куб ∪ Тело 44", wireframe_ids: [51], layer_ids: [], report: { volume: 1000 }, warnings: [], volumes: [1000] } });
    expect(await afterOperation(done, { startMany, showReport })).toEqual({ checked: 1, report: true });
    expect(startMany).toHaveBeenCalledWith([51]);
    expect(showReport).toHaveBeenCalledWith(expect.objectContaining({ op: "boolean", name: "Куб ∪ Тело 44", wireframeIds: [51], volumes: [1000], report: { volume: 1000 } }));
    expect(await afterOperation(done, { startMany, showReport })).toEqual({ checked: 0, report: false });
  });

  it("a retry seen in «Задания» is checked without a report; another project, a failed job and a running check — nothing", async () => {
    await open();
    const startMany = vi.fn(async () => ({ source: "p1", id: 90 }));
    const showReport = vi.fn();
    expect(await afterOperation(job({ id: 8, status: "done", finished_at: "t", result: { wireframe_ids: [52, 53] } }), { startMany, showReport })).toEqual({ checked: 2, report: false });
    expect(await afterOperation(job({ id: 9, source: "p2", status: "done", result: { wireframe_ids: [54] } }), { startMany, showReport })).toEqual({ checked: 0, report: false });
    expect(await afterOperation(job({ id: 10, status: "failed", result: null }), { startMany, showReport })).toEqual({ checked: 0, report: false });
    jobsStore.set({ jobs: [job({ id: 90, kind: "wireframe.check", status: "running", params: { wireframe_ids: [55] } })] });
    expect(await afterOperation(job({ id: 11, status: "done", finished_at: "t", result: { wireframe_ids: [55] } }), { startMany, showReport })).toEqual({ checked: 0, report: false });
    expect(showReport).not.toHaveBeenCalled();
  });
});
