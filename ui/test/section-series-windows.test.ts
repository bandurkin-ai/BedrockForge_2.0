import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCoreTarget } from "../src/shell/api";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { answerChoice, choiceStore } from "../src/modules/lines/choice";
import type { SeriesRow } from "../src/modules/section/seriesApi";
import { deleteSeries, openNewSeries, openSeriesManager, SERIES_MANAGER, SERIES_WINDOW } from "../src/modules/section/seriesActions";
import { fmt, formField, formFromLine, formFromRow, formFromSection, newForm, overridesWith, parseVertices, rangeOver, readForm, readWindow, saveSeries, type SeriesForm } from "../src/modules/section/seriesForm";
import { currentSection, seriesStore } from "../src/modules/section/seriesState";
import { seriesSummary } from "../src/modules/section/SeriesManager";
import { seriesTraces } from "../src/viz/section/miniPlan";
import { seriesGeometry } from "../src/viz/section/series";

function row(id: number, version = 1, extra: Partial<SeriesRow["params"]> = {}, name = "Профили"): SeriesRow {
  return {
    id, name, folder: null, version, number: id, count: 5, created_at: "", updated_at: "",
    params: { type: "vertical", p1: [1000, 5000, 0], p2: null, az: 90, phi: 90, s0: 0, s1: 100, step: 25, towards: 12.5, away: 12.5, overrides: {}, template: null, polyline: null, corridor: null, shadow: "prev", contour_layer: null, layout: null, ...extra },
  };
}

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const LINE = { x0: 1000.5, y0: 5000, az: 90, length: null, towards: 10, away: 15 };

function core(handler: (method: string, path: string, body: Record<string, unknown>) => Response) {
  const calls: { method: string; path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ method, path, body });
    return handler(method, path, body);
  }));
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  return calls;
}

beforeEach(() => {
  windowStore.set({ windows: [] });
  seriesStore.set({ list: [row(1)], loaded: true, error: null, tables: {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  choiceStore.set({ pending: [] });
  dialogStore.set({ queue: [] });
});

describe("the form of a series", () => {
  it("numbers with a comma or a dot; a vertical series from the line has one section on it", () => {
    const f = formFromLine(LINE);
    expect(f.p1x).toBe("1000,5");
    expect(f.step).toBe("25");
    const r = readForm({ ...f, towards: "12,5", away: "12.5", s1: "1 000,5", step: "0,5" });
    expect(r).toMatchObject({ params: { type: "vertical", p1: [1000.5, 5000, 0], az: 90, p2: null, phi: 90, s0: 0, s1: 1000.5, step: 0.5, towards: 12.5, away: 12.5, polyline: null }, name: null });
    expect(readForm(f)).toMatchObject({ params: { s0: 0, s1: 0 } });
    expect(readForm({ ...f, towards: "abc" })).toEqual({ error: "«Окно «к себе», м»: введите число (десятичный знак — запятая или точка)", field: "towards" });
    expect(readForm({ ...f, step: "0" })).toMatchObject({ field: "step" });
    expect(readForm({ ...f, s0: "10", s1: "5" })).toMatchObject({ field: "s1" });
    expect(readForm({ ...f, s1: "100000", step: "1" })).toMatchObject({ field: "step", error: expect.stringMatching(/100001 разрезов — больше 5 000/) });
    // P1 → P2
    expect(readForm({ ...f, dir: "p2", p2x: "1000,5", p2y: "5000" })).toMatchObject({ field: "p2x" });
    expect(readForm({ ...f, dir: "p2", p2x: "1100", p2y: "5000,25" })).toMatchObject({ params: { p2: [1100, 5000.25], az: null } });
    expect(fmt(-0)).toBe("0");
    expect(fmt(0.1 + 0.2)).toBe("0,3");
  });

  it("a plan from its levels; an inclined one with φ; a polyline from its vertices", () => {
    const f = formFromLine(LINE);
    expect(readForm({ ...f, type: "plan", top: "120,5", bottom: "20,5", step: "10" })).toMatchObject({ params: { type: "plan", p1: [1000.5, 5000, 120.5], az: 90, phi: null, s0: 0, s1: 100 } });
    expect(readForm({ ...f, type: "plan", top: "10", bottom: "20" })).toMatchObject({ field: "bottom" });
    expect(readForm({ ...f, type: "inclined", p1z: "-50,5", phi: "60" })).toMatchObject({ params: { type: "inclined", p1: [1000.5, 5000, -50.5], phi: 60 } });
    expect(readForm({ ...f, type: "inclined", phi: "95" })).toMatchObject({ field: "phi" });
    expect(parseVertices("1000,5; 5000\n1100 5000,5\n\n1200\t5100")).toEqual([[1000.5, 5000], [1100, 5000.5], [1200, 5100]]);
    expect(parseVertices("1000; 5000")).toMatch(/не меньше двух вершин/);
    expect(parseVertices("1000; 5000\nабв")).toMatch(/^Строка 2/);
    expect(readForm({ ...f, type: "polyline", polyline: "0; 0\n100; 0\n100; 100", corridor: "12,5" })).toMatchObject({ params: { type: "polyline", polyline: [[0, 0], [100, 0], [100, 100]], corridor: 12.5, p1: null, az: null } });
  });

  it("a stored series reads back into the same parameters; one section of the active series", () => {
    for (const r of [row(1), row(2, 1, { type: "plan", p1: [0, 0, 300], s0: 0, s1: 200, step: 20, phi: null }), row(3, 1, { type: "inclined", p1: [1, 2, 3], phi: 60, az: null, p2: [100, 200] })]) {
      const back = readForm(formFromRow(r));
      expect("params" in back && back.params.p1).toEqual(r.params.p1);
      expect("params" in back && back.params.s1).toBe(r.params.s1);
    }
    const cur = currentSection({ section: { series: { id: 1, k: 2 } } })!;
    const one = formFromSection(cur);
    expect(one).toMatchObject({ name: "Профили — разрез 3", p1x: "1000", p1y: "5050", s0: "0", s1: "0", towards: "12,5" });
    expect(readForm(one)).toMatchObject({ params: { p1: [1000, 5050, 0], s0: 0, s1: 0 } });
    expect(seriesSummary(row(1))).toBe("Вертикальная · A = 90° · s 0…100 м, шаг 25 м · окно 12,5 / 12,5 м · 5 разр. · тень: предыдущий");
  });

  it("the defaults: P1 and A of the free line, s0 and s1 over the visible data, multiples of Δ", () => {
    const corners: [number, number, number][] = [[900, 4980, -100], [1100, 5110, 50]];
    expect(newForm(LINE, corners)).toMatchObject({ p1x: "1000,5", az: "90", s0: "-25", s1: "125", step: "25" });
    const free = newForm(null, corners);
    expect(free).toMatchObject({ p1x: "1000", p1y: "5045", az: "90", step: "50", s0: "-100", s1: "100" });
    expect(rangeOver({ ...free, type: "plan", step: "20" }, corners)).toEqual({ top: "60", bottom: "-100", step: "20" });
    expect(rangeOver({ ...free, type: "polyline" }, corners)).toEqual({});
  });

  it("errors of the core name the field of the window; 409 asks in an own window", async () => {
    const calls = core((method, _path, body) => {
      if (method === "POST") return json({ code: "bad_series", message: "Окно «к себе»: от 0,01 до 10 000 м.", hint: "Введите расстояние в этих пределах.", field: "towards" }, 422);
      if (method === "PATCH" && body.expected_version === 1) return json({ code: "version_conflict", message: "Серию разрезов только что изменили в другом окне.", hint: "Параметры серии обновлены — проверьте их и повторите изменение.", version: 4, series: row(1, 4, {}, "Профили-2") }, 409);
      if (method === "PATCH") return json(row(1, 5));
      return json({}, 404);
    });
    const f: SeriesForm = formFromLine(LINE);
    expect(await saveSeries({ mode: "create" }, { ...f, towards: "20000" })).toEqual({ ok: false, error: "Окно «к себе»: от 0,01 до 10 000 м.", hint: "Введите расстояние в этих пределах.", field: "towards" });
    expect(formField("p1", "plan")).toBe("top");
    expect(formField("s1", "plan")).toBe("bottom");
    expect(formField("p2", "vertical")).toBe("p2x");
    expect(formField("overrides", "vertical")).toBeNull();
    // 409: «Записать мои» writes over the new version
    const mine = saveSeries({ mode: "edit", id: 1, version: 1 }, formFromRow(row(1)));
    await vi.waitFor(() => expect(choiceStore.get().pending).toHaveLength(1));
    const q = choiceStore.get().pending[0];
    expect(q.title).toBe("Серия изменена");
    expect(q.message).toMatch(/«Профили-2».*версия 4/);
    expect(q.actions.map((a) => a.label)).toEqual(["Записать мои", "Взять текущие", "Отмена"]);
    expect(windowStore.get().windows.some((w) => w.kind === "lines-choice")).toBe(true);
    answerChoice(q.id, "mine");
    expect(await mine).toMatchObject({ ok: true, row: { version: 5 } });
    expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body.expected_version)).toEqual([1, 4]);
    // «Взять текущие» gives the series as it is
    const theirs = saveSeries({ mode: "edit", id: 1, version: 1 }, formFromRow(row(1)));
    await vi.waitFor(() => expect(choiceStore.get().pending).toHaveLength(1));
    answerChoice(choiceStore.get().pending[0].id, "theirs");
    expect(await theirs).toMatchObject({ ok: false, reload: { version: 4, name: "Профили-2" } });
    const cancel = saveSeries({ mode: "edit", id: 1, version: 1 }, formFromRow(row(1)));
    await vi.waitFor(() => expect(choiceStore.get().pending).toHaveLength(1));
    answerChoice(choiceStore.get().pending[0].id, "cancel");
    expect(await cancel).toEqual({ ok: false, cancelled: true });
  });

  it("own windows of sections; deleting asks in an own window; the windows open by kind", async () => {
    const meta = { overrides: { "3": { towards: 5 } }, towards: 12.5, away: 12.5 };
    expect(overridesWith(meta, 1, { towards: 12.5, away: 30 })).toEqual({ "1": { away: 30 }, "3": { towards: 5 } });
    expect(overridesWith(meta, 3, null)).toEqual({});
    expect(overridesWith(meta, 2, { towards: 12.5, away: 12.5 })).toEqual({ "3": { towards: 5 } });
    expect(readWindow("7,5", "10")).toEqual({ towards: 7.5, away: 10 });
    expect(readWindow("0", "10")).toMatchObject({ error: expect.stringMatching(/больше 0/) });
    const calls = core(() => json({ deleted: 1 }));
    const kept = deleteSeries(1);
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind === "confirm" && d.options.message).toMatch(/Удалить серию «Профили» \(5 разр\.\)\? Контуры.*останутся/);
    closeDialog(d.id);
    if (d.kind === "confirm") d.resolve(false);
    expect(await kept).toBe(false);
    expect(calls).toHaveLength(0);
    expect(await deleteSeries(1, false)).toBe(true);
    expect(calls).toEqual([{ method: "DELETE", path: "/section-series/1", body: {} }]);
    expect(seriesStore.get().list).toEqual([]);
    openNewSeries(formFromLine(LINE));
    openSeriesManager();
    expect(windowStore.get().windows.map((w) => w.kind)).toEqual([SERIES_WINDOW, SERIES_MANAGER]);
    expect(windowStore.get().windows[0].props).toMatchObject({ mode: "create", form: { p1x: "1000,5" } });
  });

  it("the traces of a series on the plan: the current one marked, at most 200, a polyline by its segments", () => {
    const g = seriesGeometry(row(1).params);
    const t = seriesTraces(g, 2, [900, 4900, 1100, 5200]);
    expect(t.lines).toHaveLength(5);
    expect(t.active).toBe(2);
    expect(t.lines[2][1]).toBe(5050);
    expect(t.lines[2][3]).toBe(5050);
    const many = seriesTraces(seriesGeometry({ ...row(1).params, s1: 4999, step: 1 }), 1234, null);
    expect(many.lines.length).toBeLessThanOrEqual(202);
    expect(many.thinned).toBe(true);
    expect(many.lines[many.active!][1]).toBe(5000 + 1234);
    const poly = seriesTraces(seriesGeometry({ type: "polyline", polyline: [[0, 0], [100, 0], [100, 100]], corridor: 10 }), 1, null);
    expect(poly.lines).toEqual([[0, 0, 100, 0], [100, 0, 100, 100]]);
    expect(poly.active).toBe(1);
    expect(seriesTraces(seriesGeometry({ type: "plan", p1: [5, 6, 100], s1: 50, step: 10 }), 0, null).lines).toEqual([]);
  });

  it("no window.confirm, prompt, alert or open in the section module", () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.tsx?$/.test(name)) files.push(p);
      }
    };
    walk(join(__dirname, "..", "src/modules/section"));
    expect(files.length).toBeGreaterThan(10);
    for (const f of files) expect(readFileSync(f, "utf8"), f).not.toMatch(/window\.(confirm|prompt|alert|open)\s*\(|(^|[^.\w])(confirm|prompt|alert)\s*\(/m);
  });
});
