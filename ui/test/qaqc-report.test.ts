// «QAQC › Отчёт» and the section «Отчёты» (ADR 0020 §7, §8, §10, §11): the order of the steps (calculation →
// charts «N из M» → XLSX → save), a cancel on each step (the job of the step is cancelled, nothing after it runs),
// «Повторить» after a failure sends only the charts missing, a report without charts, «устарел» and «Перестроить»,
// «PDF» with the reason «Э22», the charts drawn by the tab's components by `report_id` (one calculation for the
// three views of duplicates) as PNG twice the chart. The calls are stubs (`RunDeps`); the commands — a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QaqcData, QaqcReport, ReportChart, StandardsAnswer } from "../src/modules/qaqc/api";
import { bindQaqc } from "../src/modules/qaqc/bind";
import { chartBody, chartCalcKey, chartElement, REPORT_PNG_SCALE, reportDrawer } from "../src/modules/qaqc/reportCharts";
import { abandonRun, cancelRun, DEFAULT_DEPS, isRunning, PDF_REASON, rebuildReason, reportLine, reportsStore, resetReports, retrySpec, runReport, runStore, runText, staleTitle, summaryText, waitOrLost, type RunDeps, type RunSpec } from "../src/modules/qaqc/reportRun";
import { reportActions, reportLabel } from "../src/modules/qaqc/ReportsTree";
import { ReportTab, runShare } from "../src/modules/qaqc/ReportTab";
import { initialQaqc, qaqcStore } from "../src/modules/qaqc/state";
import { PNG_SCALE } from "../src/charts/core/ChartToolbar";
import { AppContext } from "../src/shell/AppContext";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { Store } from "../src/shell/store";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const LIMITS = { sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 };
const COUNTS = { rows: 900, filtered: 600, control: 120, unmapped: 0, missing: 0 };

const CHARTS: ReportChart[] = [
  { key: "img001", section: "standards", element: "AU", group: { crm: "OREAS 45", lab: "ALS" }, title: "Стандарт OREAS 45 — ALS, AU" },
  { key: "img002", section: "blanks", element: "AU", group: { lab: "ALS" }, title: "Бланки — ALS, AU" },
  { key: "img003", section: "duplicates", element: "AU", kind: "DUP_F", view: "scatter", title: "Дубликаты DUP_F — рассеяние, AU" },
];

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** Stubs of the calls: a log of what ran, in order; the hooks change a step. */
function stubs(over: Partial<RunDeps> = {}, charts = CHARTS) {
  const log: string[] = [];
  const deps: RunDeps = {
    create: async (body) => (log.push(`create ${body.charts}`), { report_id: 7, job_id: 31, source: "project", id: 31 }),
    rebuild: async (id) => (log.push(`rebuild ${id}`), { report_id: id, job_id: 32, source: "project", id: 32 }),
    wait: async (job) => (log.push(`wait ${job.id}`), { status: "done", error: null, result: {} }),
    cancel: async (job) => void log.push(`cancel ${job.id}`),
    charts: async (id) => (log.push(`charts ${id}`), { charts, total: charts.length, omitted: 0 }),
    drawer: () => async (c) => (log.push(`draw ${c.key}`), new Uint8Array([137, 80])),
    put: async (_id, key) => void log.push(`put ${key}`),
    file: async (id) => (log.push(`file ${id}`), { job_id: 33, source: "project", id: 33 }),
    save: async (_id, path) => void log.push(`save ${path}`),
    ...over,
  };
  return { log, deps };
}

const CREATE: RunSpec = { kind: "create", body: { data_id: 4, filter: { v: 1 }, charts: "flagged" }, name: "Отчёт", charts: "flagged", path: "C:/out/qc.xlsx" };

afterEach(() => {
  runStore.set({ run: null });
  resetReports();
});

describe("the run: calculation → charts → XLSX → save", () => {
  it("the steps in order; «Графики N из M»; «Готово»", async () => {
    const { log, deps } = stubs();
    const steps: string[] = [];
    const off = runStore.subscribe(() => {
      const r = runStore.get().run;
      if (r && steps.at(-1) !== `${r.step}:${r.state}`) steps.push(`${r.step}:${r.state}`);
    });
    const run = await runReport(CREATE, deps);
    off();
    expect(log).toEqual(["create flagged", "wait 31", "charts 7", "draw img001", "put img001", "draw img002", "put img002", "draw img003", "put img003", "file 7", "wait 33", "save C:/out/qc.xlsx"]);
    expect(steps).toEqual(["calc:running", "charts:running", "xlsx:running", "save:running", "save:done"]);
    expect(run).toMatchObject({ state: "done", reportId: 7, done: 3, total: 3, path: "C:/out/qc.xlsx" });
    expect(runText(run)).toBe("Готово: сохранено C:/out/qc.xlsx");
    expect(runText({ ...run, state: "running", step: "charts", done: 37, total: 120 })).toBe("Графики 37 из 120");
    expect(runShare({ ...run, state: "running", step: "charts", done: 30, total: 120 }, null)).toBe(0.25);
  });

  it("a report without charts: no list, nothing drawn — straight to XLSX", async () => {
    const { log, deps } = stubs();
    const run = await runReport({ ...CREATE, charts: "none", body: { ...CREATE.body, charts: "none" }, path: null }, deps);
    expect(log).toEqual(["create none", "wait 31", "file 7", "wait 33"]);
    expect(run.state).toBe("done");
    expect(runText(run)).toBe("Готово");
  });

  it("a chart with nothing to draw is counted, not sent; a second run while one goes on is refused", async () => {
    const gate = deferred<void>();
    const { log, deps } = stubs({ drawer: () => async (c) => (log.push(`draw ${c.key}`), c.key === "img002" ? null : new Uint8Array([1])), wait: async (job) => (job.id === 31 && (await gate.promise), { status: "done", error: null, result: {} }) });
    const going = runReport({ ...CREATE, path: null }, deps);
    await expect(runReport(CREATE, deps)).rejects.toThrow("Отчёт QAQC уже формируется");
    gate.resolve();
    const run = await going;
    expect(log.filter((l) => l.startsWith("put"))).toEqual(["put img001", "put img003"]);
    expect(run).toMatchObject({ state: "done", done: 3, empty: 1 });
  });
});

describe("«Отменить» on each step", () => {
  it("the calculation: its job is cancelled, nothing after it", async () => {
    const gate = deferred<{ status: "cancelled"; error: null; result: null }>();
    const { log, deps } = stubs({ wait: async (job) => (log.push(`wait ${job.id}`), gate.promise) });
    const going = runReport(CREATE, deps);
    await vi.waitFor(() => expect(log).toContain("wait 31"));
    await cancelRun();
    expect(runStore.get().run?.cancelling).toBe(true);
    gate.resolve({ status: "cancelled", error: null, result: null });
    const run = await going;
    expect(log).toEqual(["create flagged", "wait 31", "cancel 31"]);
    expect(run).toMatchObject({ state: "cancelled", step: "calc" });
    expect(runText(run)).toBe("Отменено: Расчёт");
    expect(retrySpec(run)).toEqual(CREATE);
  });

  it("the charts: the one being drawn is sent, the loop stops, no XLSX", async () => {
    const gate = deferred<Uint8Array>();
    const { log, deps } = stubs({ drawer: () => async (c) => (log.push(`draw ${c.key}`), c.key === "img002" ? gate.promise : new Uint8Array([1])) });
    const going = runReport(CREATE, deps);
    await vi.waitFor(() => expect(log).toContain("draw img002"));
    await cancelRun();
    gate.resolve(new Uint8Array([2]));
    const run = await going;
    expect(log.slice(2)).toEqual(["charts 7", "draw img001", "put img001", "draw img002", "put img002"]);
    expect(run).toMatchObject({ state: "cancelled", step: "charts", done: 2, total: 3 });
    expect(retrySpec(run)).toMatchObject({ kind: "resume", reportId: 7, from: "charts" });
  });

  it("the XLSX: the job of the file is cancelled, nothing saved", async () => {
    const gate = deferred<{ status: "cancelled"; error: null; result: null }>();
    const { log, deps } = stubs({ wait: async (job) => (log.push(`wait ${job.id}`), job.id === 33 ? gate.promise : { status: "done", error: null, result: {} }) });
    const going = runReport(CREATE, deps);
    await vi.waitFor(() => expect(log).toContain("wait 33"));
    await cancelRun();
    gate.resolve({ status: "cancelled", error: null, result: null });
    const run = await going;
    expect(log.slice(-3)).toEqual(["file 7", "wait 33", "cancel 33"]);
    expect(log.some((l) => l.startsWith("save"))).toBe(false);
    expect(run).toMatchObject({ state: "cancelled", step: "xlsx" });
  });
});

describe("another project: the run is abandoned at once", () => {
  it("a job that never ends: the run is forgotten, `isRunning` false, a new run starts", async () => {
    const { log, deps } = stubs({ wait: (job) => (log.push(`wait ${job.id}`), new Promise(() => undefined)) });
    const going = runReport(CREATE, deps);
    await vi.waitFor(() => expect(log).toContain("wait 31"));
    expect(isRunning()).toBe(true);
    resetReports();
    expect(runStore.get().run).toBeNull();
    expect(isRunning()).toBe(false);
    expect(await going).toMatchObject({ state: "cancelled", step: "calc" });
    expect(log).toContain("cancel 31");
    expect(runStore.get().run).toBeNull();
    const next = await runReport({ ...CREATE, path: null }, stubs().deps);
    expect(next.state).toBe("done");
  });

  it("an abandoned run changes nothing of the run after it", async () => {
    const gate = deferred<Uint8Array>();
    const { log, deps } = stubs({ drawer: () => async (c) => (log.push(`draw ${c.key}`), c.key === "img002" ? gate.promise : new Uint8Array([1])) });
    const old = runReport(CREATE, deps);
    await vi.waitFor(() => expect(log).toContain("draw img002"));
    abandonRun();
    const fresh = runReport({ ...CREATE, charts: "none", body: { ...CREATE.body, charts: "none" }, path: null }, stubs().deps);
    gate.resolve(new Uint8Array([2]));
    await old;
    expect(await fresh).toMatchObject({ state: "done", total: 0 });
    expect(runStore.get().run).toMatchObject({ state: "done", total: 0 });
    expect(log).not.toContain("put img002");
  });
});

describe("a job gone from the list of jobs", () => {
  it("ends the wait as a failure; a job never seen waits", async () => {
    const store = new Store<{ jobs: { source: string; id: number }[] }>({ jobs: [{ source: "project", id: 31 }] });
    const never = () => new Promise<never>(() => undefined);
    const lost = waitOrLost({ source: "project", id: 31 }, never, store as never);
    store.set({ jobs: [] });
    await expect(lost).rejects.toThrow("Задание №31 пропало из списка заданий.");
    const done = waitOrLost({ source: "project", id: 32 }, async () => ({ status: "done", error: null, result: {} }), store as never);
    store.set({ jobs: [] });
    await expect(done).resolves.toMatchObject({ status: "done" });
  });
});

describe("«Повторить» after a failure", () => {
  it("sends only the charts missing, then the XLSX", async () => {
    let fail = true;
    const first = stubs({
      put: async (_id, key) => {
        if (key === "img002" && fail) throw new Error("Ядро недоступно.");
      },
    });
    const run = await runReport(CREATE, first.deps);
    expect(run).toMatchObject({ state: "failed", step: "charts", error: "Ядро недоступно." });
    expect(runText(run)).toBe("Ошибка: Графики 1 из 3 — Ядро недоступно.");
    fail = false;
    const again = stubs();
    const done = await runReport(retrySpec(run), again.deps);
    expect(again.log).toEqual(["charts 7", "draw img002", "put img002", "draw img003", "put img003", "file 7", "wait 33", "save C:/out/qc.xlsx"]);
    expect(done).toMatchObject({ state: "done", done: 3 });
  });

  it("a failed calculation is made again; a failed job says its reason", async () => {
    const { deps } = stubs({ wait: async () => ({ status: "failed", error: { code: "x", message: "Нет записей.", hint: "Проверьте фильтр." }, result: null }) });
    const run = await runReport(CREATE, deps);
    expect(run).toMatchObject({ state: "failed", step: "calc", error: "Нет записей. Проверьте фильтр." });
    expect(retrySpec(run)).toEqual(CREATE);
    const rebuild = await runReport({ kind: "rebuild", reportId: 7, name: "Отчёт", charts: "all" }, stubs().deps);
    expect(rebuild.state).toBe("done");
  });
});

const REPORT: QaqcReport = {
  id: 7, name: "Отчёт QAQC — Контроль", version: 2, state: "ready", stale: null, data_name: "Контроль", data_sha256: "3d89aa", inputs_sha256: "a1f0bb",
  elements: ["AU", "CU"], filter: { v: 1, labs: ["ALS"] }, charts: "flagged", created_at: "2024-01-01", files: [{ kind: "xlsx", size: 10 }], images: 12,
};

describe("«устарел», «Перестроить», «PDF»", () => {
  it("the marks and the reasons", () => {
    expect(staleTitle(null)).toBe("");
    expect(staleTitle("data")).toBe("устарел: данные изменились");
    expect(staleTitle("config")).toBe("устарел: изменились настройка или справочник CRM");
    expect(reportLabel({ ...REPORT, stale: "data" })).toBe("Отчёт QAQC — Контроль · устарел");
    expect(reportLabel({ ...REPORT, state: "building" })).toBe("Отчёт QAQC — Контроль · строится");
    expect(rebuildReason(REPORT)).toBeNull();
    expect(rebuildReason({ ...REPORT, stale: "source_deleted" })).toMatch(/^Данные отчёта удалены/);
    const actions = reportActions({ ...REPORT, stale: "source_deleted" }, () => null, () => undefined);
    expect(actions.map((a) => a.label)).toEqual(["Открыть", "Сохранить XLSX…", "PDF", "Перестроить", "Переименовать…", "Удалить…"]);
    expect(actions.find((a) => a.label === "PDF")).toEqual({ label: "PDF", reason: "Появится на этапе Э22" });
    expect(actions.find((a) => a.label === "Перестроить")?.reason).toMatch(/удалены/);
    expect(reportActions({ ...REPORT, files: [] }, () => null, () => undefined)[1].label).toBe("Сохранить XLSX… (файл будет записан)");
    expect(reportLine({ ...REPORT, stale: "config" })).toBe("Отчёт QAQC «Отчёт QAQC — Контроль» (№ 7, версия 2): готов, устарел: изменились настройка или справочник CRM; данные «Контроль», элементы AU, CU; рисунков 12, файл XLSX готов");
    expect(summaryText({ v: 1, elements: { CU: { summary: { batches: { total: 13 } } } } })).toBe("CU: партий 13, отказ —");
    expect(summaryText({ v: 1, elements: { AU: { summary: { batches: { total: 13, failed: 2 } }, "duplicates:DUP_F": { passed: true }, "duplicates:DUP_P": { passed: null } } } })).toBe("AU: партий 13, отказ 2; DUP_F — критерий выполнен; DUP_P — критерий не оценён");
  });

  it("the tab lists the reports with «устарел» and «PDF» disabled with its reason", () => {
    const app = createApp({ persist: false });
    reportsStore.set({ list: [{ ...REPORT, stale: "data" }, { ...REPORT, id: 8, name: "Б", stale: null }], error: null, selected: 7 });
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ReportTab)));
    expect(html).toContain("данные изменились");
    expect(html).toContain('data-testid="qaqc-report-card"');
    expect(html).toContain("3d89aa");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*title="Появится на этапе Э22"[^>]*>PDF<\/button>/);
    // the rows from the keyboard: in the order of Tab, the chosen one marked
    expect(html).toMatch(/<tr class="[^"]*" data-report="7" tabindex="0" aria-selected="true"/);
    expect(html).toMatch(/<tr data-report="8" tabindex="0" aria-selected="false"/);
  });
});

describe("the charts of the report: the tab's components by `report_id`, PNG × 2", () => {
  const SERIES = { row: [1, 2, 3], date: [1707523200000, 1707609600000, 1707696000000], seq: [1, 2, 3], batch: ["B1", "B1", "B2"], x: [1.0, 1.12, 1.2], z: [0, 2.4, 4], status: [0, 1, 2], rule: [0, 0, 0], ma: [null, null, null], swap: [null, null, null] };
  const STD: StandardsAnswer = {
    element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], series: SERIES,
    groups: [{ crm: "OREAS 45", lab: "ALS", state: "ok", n: 3, mu: 1, sd: 0.05, unit: "г/т", mean: 1.1, std: 0.1, bias: 10, bias_class: 1, norm: 1, warn: 1, fail: 1, pair_fails: 0, run_flags: 0, swaps: 0 }],
  };

  it("PNG twice the chart on screen; the control chart of the group by `report_id`", () => {
    expect(REPORT_PNG_SCALE).toBe(2);
    expect(REPORT_PNG_SCALE).toBe(PNG_SCALE);
    expect(chartBody(CHARTS[0], 7)).toEqual({ report_id: 7, element: "AU", group: { crm: "OREAS 45", lab: "ALS" } });
    expect(chartBody(CHARTS[2], 7)).toEqual({ report_id: 7, element: "AU", kind: "DUP_F" });
    const el = chartElement(CHARTS[0], STD);
    expect(el).not.toBeNull();
    const html = renderToStaticMarkup(el!);
    expect(html).toContain('viewBox="0 0 900 380"');
    expect(html).toContain("Стандарт OREAS 45 — ALS, AU");
    expect(chartElement(CHARTS[0], { ...STD, series: null })).toBeNull();
  });

  describe("the drawer", () => {
    let bodies: unknown[];
    beforeEach(() => {
      bodies = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_u: string, init?: RequestInit) => {
          bodies.push(JSON.parse(String(init?.body)));
          const points = { row: [1, 2, 3], x1: [1, 2, 3], x2: [1.1, 2.2, 2.7], hard: [4.8, 4.8, 5.3] };
          return json({ element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], pairs: 3, used: 3, excluded: { no_parent: 0, missing: 0, negative: 0, zero: 0, below: 0 }, hard_limit: 30, share_limit: 90, share10: 100, share20: 100, share30: 100, share_at_limit: 100, passed: true, stats: { mean1: 2, mean2: 2, pearson: 0.99, rma: { a: 0, b: 1 } }, ranked: { p: [33, 66, 100], hard: [4.8, 4.8, 5.3] }, points, th: { state: "few_pairs", group: 11, a: null, b: null, k: null, groups: { m: [], med: [] }, precision: [] } });
        }),
      );
      setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    });
    afterEach(() => {
      setCoreTarget(null);
      vi.unstubAllGlobals();
    });

    it("one calculation for the three views of duplicates; a view with nothing to draw — null, no PNG", async () => {
      const made: string[] = [];
      const views = ["scatter", "ranked", "th"].map((view, i) => ({ key: `img00${i + 1}`, section: "duplicates", element: "AU", kind: "DUP_F", view, title: `Дубликаты DUP_F — ${view}, AU` }));
      const draw = reportDrawer(7, views, async (make) => (made.push(renderToStaticMarkup(make(() => undefined)).slice(0, 40)), new Uint8Array([1])));
      const out = await Promise.all(views.map(draw));
      expect(bodies).toEqual([{ report_id: 7, element: "AU", kind: "DUP_F" }]);
      expect(out.map((p) => p !== null)).toEqual([true, true, false]);
      expect(made).toHaveLength(2);
      expect(chartCalcKey(views[0])).toBe(chartCalcKey(views[1]));
      expect(await draw({ key: "img009", section: "batches", element: "AU", title: "?" })).toBeNull();
      // the three views drawn: the answer is forgotten — one more chart of the key asks again
      await draw({ ...views[0], key: "img010" });
      expect(bodies).toHaveLength(2);
    });
  });
});

describe("the commands of the reports", () => {
  const DATA: QaqcData = {
    id: 4, name: "Контроль", version: 1, rows: 900, elements: [{ name: "AU", unit: "г/т" }, { name: "CU", unit: "%" }], fields: [], labs: ["ALS"], batches: 2, date_from: null, date_to: null,
    type_codes: [], source: null, stale: false, sha256: "ab", counts: {},
  };
  let calls: { url: string; body: unknown }[];
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const url = String(input).replace(/^.*\/api\/v1/, "");
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (url === "/qaqc/data") return json({ items: [DATA] });
        if (url === "/qaqc/reports" && init?.method === "POST") return json({ report_id: 7, job_id: 31, source: "project", id: 31 }, 202);
        if (url.startsWith("/jobs")) return json({ jobs: [{ id: 31, source: "project", kind: "qaqc.report", status: "cancelled", progress: 0 }], workers: null });
        if (url === "/qaqc/reports/7") return json(REPORT);
        return json({ code: "not_found", message: "Нет", hint: "" }, 404);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    qaqcStore.set({ ...initialQaqc(), datas: [DATA], filter: { ...initialQaqc().filter, labs: ["ALS"] } });
  });
  afterEach(async () => {
    await vi.waitFor(() => expect(runStore.get().run?.state).not.toBe("running"));
    setCoreTarget(null);
    vi.unstubAllGlobals();
    qaqcStore.set(initialQaqc());
  });

  it("`qaqc.report`: the elements checked, the filter of the tab, the charts; the tab opens on «Отчёт»", async () => {
    const app = createApp({ persist: false });
    bindQaqc(app);
    app.store.set({ projectName: "Проект" });
    expect((await app.registry.execute("qaqc.report", { elements: "cu, AU", charts: "all", name: "Q1" })).result).toBe("Формируется отчёт QAQC «Q1»");
    expect(calls.find((c) => c.url === "/qaqc/reports")?.body).toEqual({ data_id: 4, name: "Q1", elements: ["CU", "AU"], filter: { v: 1, labs: ["ALS"] }, charts: "all" });
    expect(qaqcStore.get().section).toBe("report");
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: "qaqc" });
    await vi.waitFor(() => expect(runStore.get().run?.state).toBe("cancelled"));
    expect((await app.registry.execute("qaqc.report", { elements: "ZN" })).result).toBe("Элемента «ZN» нет в данных «Контроль». Элементы: AU, CU.");
  });

  it("`qaqc.report_save` with kind pdf — refused with «Э22»; `report_rebuild` of a report whose data are gone — refused", async () => {
    const app = createApp({ persist: false });
    bindQaqc(app);
    app.store.set({ projectName: "Проект" });
    const pdf = await app.registry.execute("qaqc.report_save", { report_id: 7, kind: "pdf" });
    expect(pdf.ok).toBe(false);
    expect(pdf.result).toContain("этапе Э22");
    expect(PDF_REASON).toBe("Появится на этапе Э22");
    reportsStore.set({ list: [{ ...REPORT, stale: "source_deleted" }], error: null, selected: null });
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...REPORT, stale: "source_deleted" })));
    const r = await app.registry.execute("qaqc.report_rebuild", { report_id: 7 });
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Данные отчёта удалены/);
    expect(DEFAULT_DEPS.file).toBeTypeOf("function");
  });
});
