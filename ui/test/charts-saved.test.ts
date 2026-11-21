// Saved charts (ТЗ 7.28; ADR 0019 §2, §9, §11): a chart is saved when its command opens it (`auto_name`), an edit
// of its settings 400 ms later with `expected_version` and the name of the new settings while it follows them;
// a name given by hand does not change with the settings (Т7.28-2); 409 — the window takes the server's
// settings; a project open only for reading — nothing is saved; `chart.open` opens the window with the stored
// settings (one window per chart), the assistant gets the text of `POST /stats/chart/{id}`; rename, delete,
// the events of the core, the menu of the tree. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HistogramWindow } from "../src/charts/histogram/HistogramWindow";
import type { ChartRow, HistogramResult } from "../src/modules/charts/api";
import { bindCharts } from "../src/modules/charts/bind";
import { chartMenu, kindTitle } from "../src/modules/charts/ChartsTree";
import { removeChart } from "../src/modules/charts/open";
import { createSaver, savedTitle, SAVE_DELAY_MS, type Clock } from "../src/modules/charts/saved";
import { defaultSettings, type HistogramSettings } from "../src/modules/charts/settings";
import { chartChanged, chartsOfSet, chartsStore, loadCharts } from "../src/modules/charts/state";
import { tablesStore } from "../src/modules/tables/state";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const settings = (patch: Partial<HistogramSettings> = {}): HistogramSettings => ({ ...defaultSettings("histogram", 3), table: "пробы", field: "AU", ...patch });
const row = (patch: Partial<ChartRow> = {}): ChartRow => ({ id: 12, set_id: 3, kind: "histogram", name: "Гистограмма · AU · по длине", auto_name: true, settings: settings() as unknown as Record<string, unknown>, version: 1, ...patch });

function fakeClock(): Clock & { run: (ms: number) => void } {
  let now = 0;
  let timers: { at: number; fn: () => void; id: number }[] = [];
  let next = 0;
  return {
    set: (fn, ms) => {
      const id = ++next;
      timers.push({ at: now + ms, fn, id });
      return id;
    },
    clear: (t) => {
      timers = timers.filter((x) => x.id !== t);
    },
    run(ms) {
      now += ms;
      const due = timers.filter((t) => t.at <= now);
      timers = timers.filter((t) => t.at > now);
      for (const t of due) t.fn();
    },
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("the autosave of a chart", () => {
  beforeEach(() => chartsStore.set({ charts: [], loaded: false, components: {} }));

  it("400 ms after the last edit, with expected_version and the name of the new settings while it follows them", async () => {
    const clock = fakeClock();
    const put = vi.fn(async (id: number, body: { settings?: object; auto_name?: string; expected_version: number }) => row({ id, version: body.expected_version + 1, settings: body.settings as Record<string, unknown>, name: body.auto_name ?? "?" }));
    const saved: ChartRow[] = [];
    const saver = createSaver("histogram", row(), { saved: (r) => saved.push(r) }, put, SAVE_DELAY_MS, clock);
    expect(SAVE_DELAY_MS).toBe(400);
    saver.change(settings()); // as stored — nothing to send
    clock.run(1000);
    expect(put).not.toHaveBeenCalled();
    saver.change(settings({ top_cut: 10 }));
    clock.run(200);
    saver.change(settings({ top_cut: 12, weight: "none" }));
    clock.run(399);
    expect(put).not.toHaveBeenCalled();
    clock.run(1);
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][1]).toMatchObject({ settings: { top_cut: 12, weight: "none" }, auto_name: "Гистограмма · AU · без веса", expected_version: 1 });
    await flush();
    expect(saved[0].version).toBe(2);
    expect(chartsStore.get().charts[0]).toMatchObject({ id: 12, version: 2, name: "Гистограмма · AU · без веса" });
    saver.change(settings({ top_cut: 5 }));
    clock.run(400);
    expect(put.mock.calls[1][1].expected_version).toBe(2);
    saver.stop();
  });

  it("a name given by hand does not change with the settings (Т7.28-2)", async () => {
    const clock = fakeClock();
    const put = vi.fn(async (_id: number, body: { settings?: object; auto_name?: string; expected_version: number }) => row({ name: "Моя гистограмма", auto_name: false, version: body.expected_version + 1, settings: body.settings as Record<string, unknown> }));
    const saver = createSaver("histogram", row({ name: "Моя гистограмма", auto_name: false }), {}, put, 400, clock);
    saver.change(settings({ field: "AG" }));
    clock.run(400);
    expect(put.mock.calls[0][1]).not.toHaveProperty("auto_name");
    await flush();
    const r = chartsStore.get().charts[0];
    expect(r.name).toBe("Моя гистограмма");
    expect(savedTitle("histogram", settings({ field: "CU" }), r)).toBe("Моя гистограмма");
    // while the name follows: the stored name for the stored settings, the name of new settings until saved
    expect(savedTitle("histogram", settings(), row({ name: "Гистограмма · AU · по длине (2)" }))).toBe("Гистограмма · AU · по длине (2)");
    expect(savedTitle("histogram", settings({ field: "CU" }), row())).toBe("Гистограмма · CU · по длине");
    saver.stop();
  });

  it("409 version_conflict — the server's settings replace the window's, the next save goes with its version", async () => {
    const clock = fakeClock();
    const server = row({ version: 7, settings: settings({ field: "CU" }) as unknown as Record<string, unknown> });
    const conflict = new CoreError(409, "version_conflict", "График изменили, пока вы его правили.", "График обновлён — повторите правку.");
    conflict.body = { code: "version_conflict", chart: server };
    const put = vi.fn().mockRejectedValueOnce(conflict).mockResolvedValue(row({ version: 8 }));
    const got: ChartRow[] = [];
    const saver = createSaver("histogram", row(), { conflict: (r) => got.push(r) }, put, 400, clock);
    saver.change(settings({ top_cut: 3 }));
    clock.run(400);
    await flush();
    expect(got.map((r) => r.version)).toEqual([7]);
    expect(got[0].settings.field).toBe("CU");
    saver.change(settings({ field: "CU" })); // what the window now shows — the stored settings
    clock.run(400);
    expect(put).toHaveBeenCalledTimes(1);
    saver.change(settings({ field: "CU", top_cut: 4 }));
    clock.run(400);
    expect(put.mock.calls[1][1].expected_version).toBe(7);
    saver.stop();
  });

  it("one save at a time: an edit during a save goes after it with the new version; a failure is told", async () => {
    const clock = fakeClock();
    let release: (r: ChartRow) => void = () => undefined;
    const put = vi.fn().mockImplementationOnce(() => new Promise<ChartRow>((r) => (release = r))).mockRejectedValueOnce(new Error("Ядро недоступно."));
    const failed: unknown[] = [];
    const saver = createSaver("histogram", row(), { failed: (e) => failed.push(e) }, put, 400, clock);
    saver.change(settings({ top_cut: 1 }));
    clock.run(400);
    saver.change(settings({ top_cut: 2 }));
    clock.run(400);
    expect(put).toHaveBeenCalledTimes(1);
    release(row({ version: 2, settings: settings({ top_cut: 1 }) as unknown as Record<string, unknown> }));
    await flush();
    clock.run(0);
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[1][1]).toMatchObject({ settings: { top_cut: 2 }, expected_version: 2 });
    await flush();
    expect(failed).toHaveLength(1);
    saver.stop();
  });

  it("a newer row from elsewhere: a rename keeps the window's settings, other settings replace them", () => {
    const conflict = vi.fn();
    const saver = createSaver("histogram", row(), { conflict }, vi.fn(), 400, fakeClock());
    saver.sync(row({ version: 2, name: "Переименован", auto_name: false }));
    expect(conflict).not.toHaveBeenCalled();
    saver.sync(row({ version: 3, settings: settings({ field: "AG" }) as unknown as Record<string, unknown> }));
    expect(conflict).toHaveBeenCalledTimes(1);
    saver.sync(row({ version: 2, settings: settings({ field: "ZN" }) as unknown as Record<string, unknown> })); // older — ignored
    expect(conflict).toHaveBeenCalledTimes(1);
    saver.stop();
  });

  it("only for reading: the window says it is not saved", () => {
    const html = renderToStaticMarkup(createElement(HistogramWindow, { winId: "w", setId: 3, setName: "Набор", settings: settings(), readOnly: true }));
    expect(html).toContain("Проект открыт только для чтения: график не сохраняется.");
    const failed = renderToStaticMarkup(createElement(HistogramWindow, { winId: "w", setId: 3, setName: "Набор", settings: settings(), saveError: "Ядро недоступно." }));
    expect(failed).toContain("График не сохранён: Ядро недоступно.");
  });
});

describe("the commands and the tree", () => {
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let stored: ChartRow[] = [];
  let app: App;
  const RESULT = { field: "AU", log: false, weighted: true, counts: { rows: 3, used: 3 }, stats: { count: 3, mean: 1.5, median: 1, cv: 0.4 }, stats_unweighted: null, bins: { edges: [0, 1], counts: [3], weights: [3], percent: [100], cumulative_percent: [100], width: 1, below: { count: 0, weight: 0 }, above: { count: 0, weight: 0 } }, conventions: "" } as unknown as HistogramResult;

  beforeEach(() => {
    sent.length = 0;
    stored = [row(), row({ id: 13, kind: "qq", name: "Q-Q · AU · логнормальное", settings: { v: 1, field: "AU" } }), row({ id: 14, kind: "striplog", name: "Стволы" }), row({ id: 15, set_id: null, kind: "swath", name: "Swath" })];
    windowStore.set({ windows: [] });
    chartsStore.set({ charts: [], loaded: false, components: {} });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const u = new URL(url);
        const path = u.pathname.replace("/api/v1", "");
        const method = init?.method ?? "GET";
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method, path: `${path}${u.search}`, body });
        if (path === "/stats/fields") return Promise.resolve(json({ set_id: 3, tables: [{ id: 11, name: "Пробы", name_key: "пробы", kind: "assay", rows: 3, rev: 1, numbers: [{ name: "AU", unit: "г/т", role: "component" }], texts: [] }] }));
        if (path === "/charts" && method === "GET") return Promise.resolve(json({ charts: stored }));
        if (path === "/charts" && method === "POST") return Promise.resolve(json({ id: 20, version: 1, ...body }));
        const m = /^\/charts\/(\d+)$/.exec(path);
        if (m && method === "GET") {
          const r = stored.find((c) => c.id === Number(m[1]));
          return Promise.resolve(r ? json(r) : json({ code: "chart_not_found", message: "Нет", hint: "" }, 404));
        }
        if (m && method === "PUT") {
          const r = stored.find((c) => c.id === Number(m[1]))!;
          if (body.expected_version !== r.version) return Promise.resolve(json({ code: "version_conflict", message: "Изменён", hint: "", chart: r }, 409));
          return Promise.resolve(json({ ...r, name: body.name, auto_name: false, version: r.version + 1 }));
        }
        if (m && method === "DELETE") return Promise.resolve(json({ deleted: Number(m[1]) }));
        if (path === "/stats/chart/12") return Promise.resolve(json({ chart: stored[0], result: RESULT }));
        return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = createApp({ persist: false });
    bindCharts(app);
    app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
    tablesStore.set({ sets: [{ id: 3, name: "Набор", holes: 2 }], setId: 3, tabs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
  });

  it("a chart command saves the chart into the set at once (auto_name) and ties the window to it", async () => {
    const r = await app.registry.execute("chart.histogram", { field: "AU" });
    expect(r).toEqual({ ok: true, result: "Открыто окно «Гистограмма · AU · по длине · Набор»" });
    const post = sent.find((x) => x.method === "POST" && x.path === "/charts")!;
    expect(post.body).toMatchObject({ set_id: 3, kind: "histogram", name: "Гистограмма · AU · по длине", auto_name: true, settings: { v: 1, set_id: 3, field: "AU", table: "пробы" } });
    expect(windowStore.get().windows[0].props).toMatchObject({ chartId: 20 });
    expect(chartsStore.get().charts.map((c) => c.id)).toEqual([20]);
  });

  it("only for reading: the window opens, nothing is saved", async () => {
    app.store.set({ projectReadOnly: true });
    expect((await app.registry.execute("chart.histogram", { field: "AU" })).ok).toBe(true);
    expect(sent.some((x) => x.method === "POST" && x.path === "/charts")).toBe(false);
    expect(windowStore.get().windows[0].props).toMatchObject({ readOnly: true });
    expect(app.registry.reason("chart.rename")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("chart.delete")).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("chart.open")).toBeNull();
  });

  it("the charts of the project: only the charts of the sets (no displays, no swath), by name", async () => {
    await loadCharts();
    expect(chartsStore.get().charts.map((c) => c.id).sort()).toEqual([12, 13]);
    expect(chartsOfSet(chartsStore.get().charts, 3).map((c) => c.name)).toEqual(["Гистограмма · AU · по длине", "Q-Q · AU · логнормальное"]);
    expect(kindTitle("qq")).toBe("Q-Q график");
    await chartChanged({ id: 13, deleted: true, chart: "qq" });
    expect(chartsStore.get().charts.map((c) => c.id)).toEqual([12]);
    stored[1] = { ...stored[1], name: "Q-Q новое", version: 2 };
    await chartChanged({ id: 13, chart: "qq" });
    expect(chartsStore.get().charts.find((c) => c.id === 13)?.name).toBe("Q-Q новое");
    await chartChanged({ id: 99, chart: "histogram" }); // gone already — 404
    expect(chartsStore.get().charts.some((c) => c.id === 99)).toBe(false);
  });

  it("chart.open: the stored settings in one window per chart; the assistant gets the text of /stats/chart/{id}", async () => {
    await loadCharts();
    expect((await app.registry.execute("chart.open", { chart_id: 12 })).result).toBe("Открыт график «Гистограмма · AU · по длине»");
    expect((await app.registry.execute("chart.open", { chart_id: 12 })).ok).toBe(true);
    expect(windowStore.get().windows).toHaveLength(1);
    expect(windowStore.get().windows[0]).toMatchObject({ kind: "chart-histogram", title: "Гистограмма · AU · по длине · Набор", props: { chartId: 12, settings: { field: "AU", table: "пробы" } } });
    const a = await app.registry.execute("chart.open", { chart_id: 12 }, "claude");
    expect(a.result).toBe("«Гистограмма · AU · по длине» (график №12): проб 3, среднее 1.5, медиана 1, CV 0.4 (с весом)");
    expect((await app.registry.execute("chart.open", { chart_id: 14 })).result).toMatch(/не график набора/);
    expect((await app.registry.execute("chart.open", { chart_id: 77 })).result).toMatch(/График №77 не найден/);
  });

  it("chart.rename: a name by hand with the version known (a changed version — taken and tried once more)", async () => {
    await loadCharts();
    chartsStore.set((s) => ({ charts: s.charts.map((c) => (c.id === 12 ? { ...c, version: 0 } : c)) })); // stale
    const r = await app.registry.execute("chart.rename", { chart_id: 12, name: "Моя" });
    expect(r).toEqual({ ok: true, result: "График переименован: «Моя»" });
    const puts = sent.filter((x) => x.method === "PUT");
    expect(puts.map((p) => p.body)).toEqual([
      { name: "Моя", expected_version: 0 },
      { name: "Моя", expected_version: 1 },
    ]);
    expect(chartsStore.get().charts.find((c) => c.id === 12)).toMatchObject({ name: "Моя", auto_name: false });
  });

  it("chart.delete: removed from the set (the assistant's call is confirmed by the registry)", async () => {
    await loadCharts();
    expect(await removeChart({ chart_id: 12 }, "claude")).toBe("График «Гистограмма · AU · по длине» удалён");
    expect(sent.some((x) => x.method === "DELETE" && x.path === "/charts/12")).toBe(true);
    expect(chartsStore.get().charts.map((c) => c.id)).toEqual([13]);
  });

  it("the menu of a chart in the tree: open, rename, delete — the commands with their reasons", () => {
    const run = vi.fn();
    const items = chartMenu(row(), (id) => (id === "chart.delete" ? "Проект открыт только для чтения" : null), run);
    expect(items.map((i) => [i.label, i.reason])).toEqual([
      ["Открыть", null],
      ["Переименовать…", null],
      ["Удалить…", "Проект открыт только для чтения"],
    ]);
    items[0].run();
    items[2].run();
    expect(run.mock.calls).toEqual([
      ["chart.open", { chart_id: 12 }],
      ["chart.delete", { chart_id: 12 }],
    ]);
  });
});
