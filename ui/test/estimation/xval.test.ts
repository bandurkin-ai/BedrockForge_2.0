// The window «Перекрёстная проверка» (Э18, 18.23) on the real answers of the core (`core/tests/data/estimate/contract_answers.json`:
// `xval`, `xval_submit`, `xval_result`, `estimations`): the result read page by page, no result, the run and its job (done, failed,
// cancelled, one at a time), the command `estimate.xval`, the window drawn (the summary, the table with its sort and pages, the charts,
// the stale words), the errors in the 3D window.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { renderEstimationWindow } from "../../src/modules/estimation/bind";
import { estimationStore, loadRows } from "../../src/modules/estimation/state";
import { cellText } from "../../src/modules/estimation/xvalModel";
import { hide3d, openXval, runXval, setSize, setSort, setTab, setTablePage, show3d, syncXval, XVAL_WINDOW, xvalStore, resetXval, loadXval } from "../../src/modules/estimation/xvalState";
import { XvalWindow } from "../../src/modules/estimation/XvalWindow";
import { cancelJob, jobsStore, refresh } from "../../src/modules/jobs/state";
import { setCurrentScene } from "../../src/modules/scene/sceneHost";
import { A, installCore, newApp, teardown, type Core } from "./harness";
import { installXval, ROW, TOTAL, type XvalCore } from "./xvalKit";

let app: App;
let core: Core;
let kit: XvalCore;

beforeEach(async () => {
  core = installCore();
  kit = installXval(core);
  app = newApp();
  resetXval();
  await loadRows();
});
afterEach(() => {
  hide3d();
  setCurrentScene(null);
  resetXval();
  teardown();
});

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond: () => boolean, ms = 2000): Promise<void> {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("the condition did not come true");
    await tick(5);
  }
}
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const render = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(XvalWindow, { winId: "w1" }))));
const tag = (html: string, id: string): string => html.match(new RegExp(`<[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? "";
const off = (html: string, id: string): boolean => tag(html, id).includes('disabled=""');
const exec = (args: Record<string, unknown>) => app.registry.execute("estimate.xval", args, "user");
const rowsOf = (html: string): string[] => html.split("<tr ").filter((r) => r.includes('data-testid="xval-row"')).map((r) => r.slice(0, r.indexOf("</tr>")));
const PAGES = `GET /geostat/estimations/${ROW}/xval`;

describe("the result is read", () => {
  it("the first page of 5000 gives the summary and the histogram; the table is all of it", async () => {
    await openXval(ROW);
    expect(requests(PAGES).map((r) => r.query)).toEqual(["?page=1&size=5000"]);
    const s = xvalStore.get();
    expect(s.phase).toBe("ready");
    expect(s.rows).toHaveLength(TOTAL);
    expect(s.meta).toMatchObject({ by: "hole", stale: false, total: 147, summary: { n: 147, mean_error: A.xval.body.summary.mean_error } });
    expect(s.meta!.histogram.counts).toHaveLength(40);
    expect(s.reading).toBeNull();
  });
  it("no calculation (404 xval_not_found) — «нет результата», not an error", async () => {
    kit.table = null;
    await openXval(ROW);
    const s = xvalStore.get();
    expect([s.phase, s.meta, s.rows, s.error]).toEqual(["none", null, [], null]);
  });
  it("a refusal of the core is told in its words; another row — a clean table", async () => {
    kit.fail = { status: 500, body: { code: "boom", message: "Ядро не смогло прочитать таблицу.", hint: "Повторите." } };
    await openXval(ROW);
    expect(xvalStore.get()).toMatchObject({ phase: "failed", error: { code: "boom", message: "Ядро не смогло прочитать таблицу." } });
    await openXval(null);
    expect(xvalStore.get()).toMatchObject({ estimationId: null, phase: "idle", error: null, rows: [] });
  });
  it("the table is read page by page (the core may give less than 5000)", async () => {
    kit.clamp = 100;
    const reading: unknown[] = [];
    const off1 = xvalStore.subscribe(() => reading.push(xvalStore.get().reading));
    await openXval(ROW);
    off1();
    expect(requests(PAGES).map((r) => r.query)).toEqual(["?page=1&size=5000", "?page=2&size=5000"]);
    expect(xvalStore.get().rows.map((r) => r.i)).toEqual(Array.from({ length: TOTAL }, (_, i) => i));
    expect(reading).toContainEqual({ done: 1, of: 2 });
    expect(xvalStore.get().reading).toBeNull();
  });
  it("calculated again while the table is read — it is read again from the start", async () => {
    kit.clamp = 100;
    kit.atLater = "2026-11-26T10:00:00+00:00";
    await openXval(ROW);
    expect(requests(PAGES).map((r) => r.query)).toEqual(["?page=1&size=5000", "?page=2&size=5000", "?page=1&size=5000", "?page=2&size=5000"]);
    expect(xvalStore.get().meta!.at).toBe("2026-11-26T10:00:00+00:00");
    expect(xvalStore.get().rows).toHaveLength(TOTAL);
  });
  it("a look at the first row only: «устарел» is taken, the table is read again only for another calculation", async () => {
    await openXval(ROW);
    kit.stale = true;
    await syncXval();
    expect(xvalStore.get().meta!.stale).toBe(true);
    expect(requests(PAGES).map((r) => r.query)).toEqual(["?page=1&size=5000", "?page=1&size=1"]);
    kit.at = "2026-11-26T10:00:00+00:00";
    await syncXval();
    expect(requests(PAGES)).toHaveLength(4);
    expect(xvalStore.get().meta!.at).toBe("2026-11-26T10:00:00+00:00");
    kit.table = null; // the result is gone
    await syncXval();
    expect(xvalStore.get().phase).toBe("none");
  });
  it("another project — a clean window", async () => {
    await openXval(ROW);
    resetXval();
    expect(xvalStore.get()).toMatchObject({ estimationId: null, phase: "idle", rows: [], meta: null });
  });
});

describe("the run", () => {
  it("POST /estimate/xval {estimation_id, by}; the job followed; the table read again; the line with the summary", async () => {
    await openXval(ROW);
    kit.table = null;
    await loadXval();
    expect(xvalStore.get().phase).toBe("none");
    kit.table = (await import("./xvalKit")).table();
    const line = await runXval("sample");
    const post = requests("POST /geostat/estimate/xval");
    expect(post.map((r) => r.body)).toEqual([{ estimation_id: ROW, by: "sample" }]);
    expect(line).toContain("Перекрёстная проверка «AU · OK · окисл»");
    expect(line).toContain("проб 147");
    const s = xvalStore.get();
    expect([s.phase, s.by, s.job, s.error]).toEqual(["ready", "sample", null, null]);
    expect(s.rows).toHaveLength(TOTAL);
  });
  it("a job that failed: the words of the core under the bar, the old result stays", async () => {
    await openXval(ROW);
    kit.job = { status: "failed", result: null, error: { code: "no_samples", message: "Нет проб для проверки.", hint: "Проверьте набор проб." }, progress: 0, message: null };
    await expect(runXval("hole")).rejects.toThrow(/Нет проб для проверки/);
    const s = xvalStore.get();
    expect([s.phase, s.error?.code, s.rows.length]).toEqual(["ready", "no_samples", TOTAL]);
  });
  it("a job cancelled: a line, not an error; nothing is written", async () => {
    await openXval(ROW);
    kit.job = { status: "cancelled", result: null, error: null, progress: 0.3, message: null };
    const line = await runXval("hole");
    expect(line).toBe("Перекрёстная проверка «AU · OK · окисл» отменена: прежний результат не изменён");
    expect(xvalStore.get()).toMatchObject({ phase: "ready", error: null, note: line, job: null });
    kit.table = null;
    await loadXval();
    kit.job = { ...kit.job };
    expect(await runXval("hole")).toMatch(/ничего не записано/);
  });
  it("one at a time; the progress is the job's; «Отменить» cancels it", async () => {
    await openXval(ROW);
    kit.job = { status: "running", result: null, error: null, progress: 0.4, message: "Скважина 12 из 30" };
    const run = runXval("hole");
    await waitFor(() => xvalStore.get().job !== null);
    expect(xvalStore.get().phase).toBe("running");
    await refresh();
    await expect(runXval("sample")).rejects.toThrow(/уже выполняется/);
    const html = render();
    expect(html).toContain('data-testid="xval-progress"');
    expect(html).toContain("Скважина 12 из 30");
    expect(html).toContain('data-testid="xval-cancel"');
    expect(off(html, "xval-run")).toBe(true);
    await cancelJob(xvalStore.get().job!);
    expect(await run).toMatch(/отменена/);
    expect(core.sent.some((r) => r.path.endsWith("/cancel"))).toBe(true);
    expect(jobsStore.get().jobs[0].status).toBe("cancelled");
    expect(xvalStore.get().phase).toBe("ready");
  });
});

describe("the command estimate.xval", () => {
  it("is available with a project; without `by` it opens the window with the row asked and reads the result, nothing is run", async () => {
    expect(app.registry.reason("estimate.xval")).toBeNull();
    const r = await exec({ estimation_id: ROW });
    expect(r).toEqual({ ok: true, result: "Открыто окно «Перекрёстная проверка»" });
    expect(windowStore.get().windows.filter((w) => w.kind === XVAL_WINDOW)).toHaveLength(1);
    expect(xvalStore.get()).toMatchObject({ estimationId: ROW, phase: "ready" });
    expect(requests("POST /geostat/estimate/xval")).toHaveLength(0);
    await exec({});
    expect(windowStore.get().windows.filter((w) => w.kind === XVAL_WINDOW)).toHaveLength(1); // the same window, the same row
    expect(xvalStore.get().estimationId).toBe(ROW);
  });
  it("without a row: the one the project has, else the window waits for the choice", async () => {
    expect((await exec({})).ok).toBe(true); // the contract has two rows: nothing is chosen
    expect(xvalStore.get()).toMatchObject({ estimationId: null, phase: "idle" });
    estimationStore.set({ rows: estimationStore.get().rows.slice(0, 1) });
    await exec({});
    expect(xvalStore.get()).toMatchObject({ estimationId: ROW, phase: "ready" });
  });
  it("with `by` it runs the check, follows the job and answers with the summary; the window is open", async () => {
    const r = await exec({ estimation_id: ROW, by: "hole" });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("по скважине: проб 147");
    expect(requests("POST /geostat/estimate/xval").map((x) => x.body)).toEqual([{ estimation_id: ROW, by: "hole" }]);
    expect(windowStore.get().windows.some((w) => w.kind === XVAL_WINDOW)).toBe(true);
  });
  it("refusals in Russian: no such row, a wrong `by`, a project open for reading, no row to run", async () => {
    expect(await exec({ estimation_id: 999 })).toMatchObject({ ok: false, result: expect.stringContaining("№999 не найдена") });
    expect((await exec({ estimation_id: ROW, by: "block" })).ok).toBe(false);
    app.store.set({ projectReadOnly: true });
    expect(await exec({ estimation_id: ROW, by: "hole" })).toMatchObject({ ok: false, result: expect.stringContaining("только для чтения") });
    expect((await exec({ estimation_id: ROW })).ok).toBe(true); // looking is allowed
    app.store.set({ projectReadOnly: false });
    estimationStore.set({ rows: [...estimationStore.get().rows, { ...estimationStore.get().rows[0], id: 5 }] });
    resetXval();
    expect(await exec({ by: "hole" })).toMatchObject({ ok: false, result: expect.stringContaining("Не указана строка") });
    expect(requests("POST /geostat/estimate/xval")).toHaveLength(0);
  });
  it("a row whose source is gone is not run", async () => {
    estimationStore.set({ rows: estimationStore.get().rows.map((r) => ({ ...r, source_lost: true })) });
    expect(await exec({ estimation_id: ROW, by: "hole" })).toMatchObject({ ok: false, result: expect.stringContaining("Источник строки удалён") });
  });
  it("without a project it says so; the window layer draws the window", () => {
    expect(app.registry.reason("estimate.xval", { hasProject: false, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true })).toBe("Откройте или создайте проект");
    const w = { id: "w1", kind: XVAL_WINDOW, title: "t", x: 0, y: 0, w: 1, h: 1, z: 1, maximized: false };
    expect(renderEstimationWindow(w)).not.toBeNull();
    expect(renderEstimationWindow({ ...w, kind: "other" })).toBeNull();
  });
});

describe("the window", () => {
  it("no row chosen: the words about what the check is; «Выполнить» says why it is off", () => {
    const html = render();
    expect(html).toContain("Выберите строку «Параметры оценки»");
    expect(off(html, "xval-run")).toBe(true);
    expect(tag(html, "xval-run")).toContain('title="Выберите строку «Параметры оценки»"');
  });
  it("no result yet: the way to get it", async () => {
    kit.table = null;
    await openXval(ROW);
    const html = render();
    expect(html).toContain('data-testid="xval-none"');
    expect(html).toContain("«AU · OK · окисл» перекрёстной проверки ещё нет");
    expect(off(html, "xval-run")).toBe(false);
    app.store.set({ projectReadOnly: true });
    const ro = render();
    expect(off(ro, "xval-run")).toBe(true);
    expect(tag(ro, "xval-run")).toContain("Проект открыт только для чтения");
  });
  it("the summary: every number of the core; the statuses", async () => {
    await openXval(ROW);
    const html = render();
    expect(html).toContain('data-testid="xval-summary"');
    for (const [k, v] of [["n", "147"], ["mean_error", "-0,01084"], ["std_error", "0,6661"], ["mae", "0,409"], ["r", "0,5011"], ["slope", "0,9273"], ["mean_ratio", "0,7259"]]) {
      expect(html).toMatch(new RegExp(`data-testid="xval-fact-${k}"[^>]*><dt>[^<]*</dt><dd>${v}</dd>`));
    }
    expect(html).toContain("По статусам: оценён — 147");
    expect(html).toContain("Расчёт от ");
    expect(html).toContain("по скважине");
    expect(html).not.toContain('data-testid="xval-stale"');
  });
  it("the table: the real rows, a gap is «—» (not 0), the sample that was not estimated is marked", async () => {
    await openXval(ROW);
    setSize(500);
    const html = render();
    expect(html).toContain('data-rows="147"');
    expect(html.match(/data-testid="xval-row"/g)).toHaveLength(147);
    const all = rowsOf(html);
    expect(all[0]).toContain("<td>С-00</td>");
    expect(all[0]).toContain(">1,386</td>");
    expect(all[0]).toContain('data-skipped="false"');
    const row = all[all.length - 1];
    expect(row).toContain("<td>С-99</td>");
    expect(row).toContain("мало проб");
    expect(row.match(/>—<\/td>/g)!.length).toBe(5); // the estimate, the error, KV, the standardised error, the pass
    expect(row).toContain('data-skipped="true"');
    expect(row).toContain(">0,5</td>"); // the true value is there
  });
  it("the sort by a header: the order of the whole table, the arrow, aria-sort", async () => {
    await openXval(ROW);
    setSize(50);
    setSort("error");
    const rows = xvalStore.get().rows.filter((r) => r.error !== null);
    const lowest = rows.reduce((a, b) => (b.error! < a.error! ? b : a));
    let html = render();
    expect(tag(html, "xval-th-error")).toContain('aria-sort="ascending"');
    expect(rowsOf(html)[0]).toContain(`>${cellText(lowest, "error")}</td>`);
    expect(html).toMatch(/Ошибка ▲/);
    setSort("error");
    html = render();
    expect(tag(html, "xval-th-error")).toContain('aria-sort="descending"');
    expect(html).toMatch(/Ошибка ▼/);
    setSort("error");
    expect(tag(render(), "xval-th-error")).toContain('aria-sort="none"');
    expect(xvalStore.get().tablePage).toBe(1);
  });
  it("the pages: the size, the page of the sorted table, the buttons at the ends", async () => {
    await openXval(ROW);
    setSize(50);
    let html = render();
    expect(html).toContain("Страница 1 из 3");
    expect(off(html, "xval-prev")).toBe(true);
    expect(html.match(/data-testid="xval-row"/g)).toHaveLength(50);
    setTablePage(3);
    html = render();
    expect(html).toContain("Страница 3 из 3");
    expect(html.match(/data-testid="xval-row"/g)).toHaveLength(47);
    expect(off(html, "xval-next")).toBe(true);
    setTablePage(9); // beyond the end: the last page
    expect(render()).toContain("Страница 3 из 3");
  });
  it("«Рассеяние»: the 146 pairs, the line 1:1 and the regression", async () => {
    await openXval(ROW);
    setTab("scatter");
    const html = render();
    expect(html).toContain('data-testid="xval-scatter"');
    expect(html).toContain('data-points="146"');
    expect(html).toContain('data-line="1:1"');
    expect(html).toContain('data-line="ols"');
    expect(html).toContain("Рассеяние: Истинное против Оценка");
    expect(html).toContain("наклон 0,9273");
  });
  it("«Гистограмма ошибок»: the 40 bins of the core, the zero and the mean error", async () => {
    await openXval(ROW);
    setTab("hist");
    const html = render();
    expect(html).toContain('data-testid="xval-histogram"');
    expect(html).toContain('data-bins="40"');
    expect(html).toContain('data-bars="40"');
    expect(html).toContain('data-mark="0"');
    expect(html).toContain('data-mark="средняя -0,01084"');
    expect(html).toContain("Ошибка (оценка − истинное)");
  });
  it("«устарел»: the banner with the cause of the row", async () => {
    kit.stale = true;
    core.estimations = { estimations: [{ ...A.estimations.body.estimations[0], stale: true, stale_reason: "изменена таблица «Композиты»" }] };
    await loadRows();
    await openXval(ROW);
    const html = render();
    expect(tag(html, "xval-stale")).toContain('role="status"');
    expect(html).toContain("Результат устарел. Параметры оценки или исходные данные изменились после расчёта от ");
    expect(html).toContain("Изменена таблица «Композиты».");
    expect(html).toContain("Выполните проверку заново.");
  });
  it("an error of the core stands under the bar with its words", async () => {
    await openXval(ROW);
    xvalStore.set({ error: { code: "x", message: "Не вышло.", hint: "Повторите.", param: null } });
    expect(render()).toMatch(/data-testid="xval-error"[^>]*>Не вышло\. Повторите\./);
  });
});

describe("the errors in 3D", () => {
  /** A scene of the test: what the layer set was told. */
  function fakeScene() {
    const log = { added: [] as { key: string; layer: { count: number; bbox: number[] | null; origin: unknown; dispose: () => void } }[], removed: [] as string[], revealed: 0, invalidated: 0 };
    const scene = {
      clipPlanes: [],
      addLayer: (key: string, layer: never) => log.added.push({ key, layer }),
      removeLayer: (key: string) => log.removed.push(key),
      onRestored: () => () => undefined,
      reveal: () => void log.revealed++,
      invalidate: () => void log.invalidated++,
    };
    setCurrentScene(scene as never);
    return log;
  }

  it("the button is off until there is a result; without the 3D window — the words, not a silence", async () => {
    await openXval(null);
    expect(off(render(), "xval-3d")).toBe(true);
    await openXval(ROW);
    expect(off(render(), "xval-3d")).toBe(false);
    expect(() => show3d()).toThrow(/3D-окно ещё не готово/);
    expect(xvalStore.get().shown).toBeNull();
  });
  it("«Показать ошибки в 3D»: a layer of the points of the 146 samples with an error, in the place of the sample, shown to the person", async () => {
    await openXval(ROW);
    const log = fakeScene();
    const cloud = show3d();
    await waitFor(() => log.added.length === 1);
    expect(cloud.n).toBe(146);
    expect(log.added[0].key).toBe("xval-errors");
    expect(log.added[0].layer.count).toBe(146);
    expect(log.added[0].layer.bbox).toEqual([500000, 6700000, 289, 500000, 6700025, 299]);
    expect(log.revealed).toBe(1);
    expect(xvalStore.get().shown).toEqual({ n: 146, limit: cloud.limit });
    const html = render();
    expect(html).toContain("Скрыть ошибки в 3D");
    expect(html).toContain('data-testid="xval-3d-legend"');
    expect(html).toContain("проб: 146");
  });
  it("«Скрыть»: the layer goes; a new result changes the points", async () => {
    await openXval(ROW);
    const log = fakeScene();
    show3d();
    await waitFor(() => log.added.length === 1);
    kit.table = kit.table!.slice(0, 20);
    await loadXval();
    await waitFor(() => log.added.length === 2);
    expect(log.added[1].layer.count).toBe(20);
    expect(xvalStore.get().shown!.n).toBe(20);
    hide3d();
    expect(log.removed).toContain("xval-errors");
    expect(xvalStore.get().shown).toBeNull();
    expect(render()).toContain("Показать ошибки в 3D");
  });
  it("the scene made again gets the layer back while it is shown; the table gone — the layer is gone", async () => {
    await openXval(ROW);
    const first = fakeScene();
    show3d();
    await waitFor(() => first.added.length === 1);
    const second = fakeScene();
    await waitFor(() => second.added.length === 1);
    expect(second.added[0].key).toBe("xval-errors");
    kit.table = null;
    await loadXval();
    expect(xvalStore.get().shown).toBeNull();
    expect(second.removed).toContain("xval-errors");
  });
});
