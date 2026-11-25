// The global check, its Q-Q and the swath of the estimate (Э18, 18.24) on the real answers of the core
// (`core/tests/data/estimate/contract_answers.json`, keys `global`, `qq`, `swath`): the readers, the norm of 5 % in words and colour, the
// table, the variance of the blocks against C̄_VV, the commands `estimate.global`, `estimate.qq`, `estimate.swath` (the window of Э13
// opens with the settings of the core), the window drawn.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { readGlobal, readQq } from "../../src/modules/estimation/checksApi";
import { GlobalWindow } from "../../src/modules/estimation/GlobalWindow";
import { blocksText, checkedRows, GLOBAL_WINDOW, globalReason, globalStore, globalSummary, qqSummary, ratioHint, referenceText, resetGlobal, setGlobalRow, setWeighted, statRows, varianceText, verdictOf, verdictText } from "../../src/modules/estimation/globalState";
import { loadRows } from "../../src/modules/estimation/state";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;

const ROW4 = A.estimations.body.estimations[0];
const NEVER_RUN = { ...ROW4, id: 8, name: "AU · OK · перв", last_run: null };
const STARTED = { status: 200, body: { source: "proj", id: 5 } };

beforeEach(async () => {
  core = installCore();
  core.estimations = { estimations: [ROW4, A.estimations.body.estimations[1], NEVER_RUN] };
  app = newApp();
  resetGlobal();
  await loadRows();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const render = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(GlobalWindow, { winId: "w1" }))));
const tag = (html: string, id: string): string => html.match(new RegExp(`<[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? "";
const off = (html: string, id: string): boolean => tag(html, id).includes('disabled=""');
const glob = () => readGlobal(A.global.body)!;
async function check(args: Record<string, unknown>, command = "estimate.global", status = "done") {
  jobsStore.set({ jobs: [], workers: null, error: null });
  core.force("POST /geostat/estimate/global", STARTED);
  core.force("POST /geostat/estimate/qq", A.qq);
  core.job.status = status;
  core.job.result = A.global.body;
  return app.registry.execute(command, args, "claude");
}

describe("the readers on the answers of the core", () => {
  it("the global check: the moments, the difference, the norm, the variance of the blocks, the blocks the sums stand on", () => {
    const g = glob();
    expect(g).toMatchObject({ estimation_id: 4, field: "AU_OK_EST", status_field: "AU_OK_STATUS", method: "ok", sub: "parent", limit_pct: 5, norm: true, norm_nn: true });
    expect(g.composites.reference).toBe("declus");
    expect(g.composites.declus).toMatchObject({ n: 147, mean: 1.1874132779515132 });
    expect(g.nn).toMatchObject({ n: 400, volume: 200000, mean: 1.1796312398573805 });
    expect(g.estimate).toMatchObject({ n: 400, volume: 200000, mean: 1.1671182930710835, var: 0.22167389910743718 });
    expect(g.diff_pct).toBeCloseTo(-1.7091761779388166, 12);
    expect(g.block_var).toMatchObject({ expected: 0.8042895942825241, sill: 1, disc: [2, 2, 1], size: [10, 10, 5] });
    expect(g.blocks).toEqual({ rows: 800, by_status: [400, 400, 0, 0], used: 400, empty: 0, no_volume: 0 });
    expect(g.warnings).toEqual([]);
  });
  it("a gap stays null; not an answer — null", () => {
    const g = readGlobal({ ...A.global.body, estimate: { n: 0, volume: null, mean: null, var: null, cv: null }, diff_pct: null, norm: null, block_var: { expected: null, estimate: null, sill: null, ratio: null } })!;
    expect(g.estimate).toEqual({ n: 0, volume: null, mean: null, var: null, cv: null });
    expect(g).toMatchObject({ diff_pct: null, norm: null });
    expect(g.block_var).toMatchObject({ expected: null, estimate: null, ratio: null });
    expect(readGlobal(null)).toBeNull();
    expect(readGlobal({ estimation_id: 4 })).toBeNull();
  });
  it("the Q-Q: [x, y, P %], the marks, the ratios, the extremes, the distance", () => {
    const q = readQq(A.qq.body)!;
    expect(q.points).toHaveLength(10);
    expect(q.points[0]).toEqual([0.43898309661797547, 0.5851337953990579, 9.090909090909092]);
    expect(q.marks.map((m) => m[2])).toEqual([10, 50, 90]);
    expect(q.ratio).toHaveLength(10);
    expect(q.extremes).toEqual({ x: [0.22296277328325173, 6.174629972205275], y: [0.4584824899780183, 2.7533104870981373] });
    expect(q).toMatchObject({ weighted: true, field: "AU_OK_EST", ks: 0.1152841468681118 });
    expect(q.x).toMatchObject({ n: 147 });
    expect(q.y).toMatchObject({ n: 400 });
    expect(readQq({ estimation_id: 4 })).toBeNull();
  });
});

describe("the words and the norm", () => {
  it("within the norm — green, with the number and the words; beyond — red; unknown — neither", () => {
    expect(verdictOf(-1.709, true)).toBe("ok");
    expect(verdictOf(6.2, false)).toBe("bad");
    expect(verdictOf(null, null)).toBe("none");
    expect(verdictOf(null, true)).toBe("none");
    expect(verdictText(-1.7091761779388166, true, 5)).toBe("−1,71 % — в норме (допуск ±5 %)");
    expect(verdictText(6.24, false, 5)).toBe("+6,24 % — вне нормы (допуск ±5 %)");
    expect(verdictText(null, null, 5)).toBe("не определено");
  });
  it("what the estimate is compared with: the declustered mean, or the naive one when there are no weights", () => {
    expect(referenceText(glob())).toBe("декластерированного среднего проб");
    expect(referenceText({ composites: { ...glob().composites, reference: "naive" } })).toContain("весов декластеризации нет");
  });
  it("the table: the naive and the declustered composites, then the blocks — NN and the estimate", () => {
    const rows = statRows(glob());
    expect(rows.map((r) => r.key)).toEqual(["naive", "declus", "nn", "estimate"]);
    expect(rows[0]).toMatchObject({ title: "Пробы домена, без весов", n: 147, volume: null, mean: 1.2030617500336696 });
    expect(rows[3]).toMatchObject({ title: "Блоки, оценка", n: 400, volume: 200000, var: 0.22167389910743718 });
    expect(statRows({ ...glob(), nn: null }).map((r) => r.key)).toEqual(["naive", "declus", "estimate"]);
  });
  it("the variance of the blocks against C̄_VV, and what the ratio says", () => {
    expect(varianceText(glob())).toBe("Дисперсия оценки блоков 0,2217, ожидаемая дисперсия блоков C̄_VV 0,8043, отношение 0,28; порог вариограммы 1.");
    expect(ratioHint(glob())).toContain("Кригинг сглаживает");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: 1.4 } })).toContain("больше ожидаемой");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: 0.05 } })).toContain("сильно сглажена");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: null } })).toBeNull();
    expect(varianceText({ block_var: { ...glob().block_var, expected: null, ratio: null } })).toContain("ожидаемая дисперсия не считается");
  });
  it("the blocks the sums stand on", () => {
    expect(blocksText(glob())).toBe("В сумме 400 из 800 блоков: оценён 400, вне домена 400.");
  });
  it("the answer of the command and the words of the Q-Q", () => {
    expect(globalSummary(glob())).toContain("в норме");
    expect(globalSummary(glob())).toContain("Глобальная проверка «AU · OK · окисл» (AU_OK_EST)");
    expect(qqSummary(readQq(A.qq.body)!)).toMatch(/^Q-Q: композиты \(с весами декластеризации\) против оценки блоков — отношение Y\/X по квантилям от 0,85 до 1,33, расстояние Колмогорова–Смирнова 0,115\.$/);
  });
});

describe("the rows to check", () => {
  it("only the rows that were run; why a row cannot be checked", () => {
    expect(checkedRows().map((r) => r.id)).toEqual([4, 5]);
    expect(globalReason({ rowId: null, phase: "idle" })).toBe("Выберите строку «Параметры оценки»");
    expect(globalReason({ rowId: 8, phase: "idle" })).toMatch(/ещё не выполнялась/);
    expect(globalReason({ rowId: 99, phase: "idle" })).toBe("Строка №99 не найдена");
    expect(globalReason({ rowId: 4, phase: "running" })).toBe("Проверка уже выполняется");
    expect(globalReason({ rowId: 4, phase: "idle" })).toBeNull();
  });
});

describe("the command estimate.global", () => {
  it("without a row it opens the window", async () => {
    const r = await app.registry.execute("estimate.global", {}, "user");
    expect(r).toMatchObject({ ok: true, result: "Открыто окно «Глобальная проверка оценки»" });
    expect(windowStore.get().windows.filter((w) => w.kind === GLOBAL_WINDOW)).toHaveLength(1);
    expect(requests("POST /geostat/estimate/global")).toHaveLength(0);
  });
  it("with a row it runs the job, then the Q-Q, and answers with the verdict; nothing is written", async () => {
    const r = await check({ estimation_id: 4 });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("−1,71 % — в норме");
    expect(r.result).toContain("Q-Q: композиты");
    expect(requests("POST /geostat/estimate/global")[0].body).toEqual({ estimation_id: 4 });
    expect(requests("POST /geostat/estimate/qq")[0].body).toEqual({ estimation_id: 4, weighted: true });
    expect(globalStore.get()).toMatchObject({ phase: "done", rowId: 4, qqPhase: "done" });
    expect(core.sent.filter((q) => q.method === "PUT")).toEqual([]);
    expect(windowStore.get().windows.some((w) => w.kind === GLOBAL_WINDOW)).toBe(true);
  });
  it("a row that was never run: the core says `not_estimated`, the words stand under the bar", async () => {
    core.force("POST /geostat/estimate/global", { status: 409, body: { code: "not_estimated", message: "Поля оценки нет в блочной модели.", hint: "Выполните оценку." } });
    const r = await app.registry.execute("estimate.global", { estimation_id: 4 }, "claude");
    expect(r.ok).toBe(false);
    expect(globalStore.get()).toMatchObject({ phase: "failed", error: { code: "not_estimated" } });
    expect(render()).toContain('data-testid="global-error"');
  });
  it("a row that is not in the project is refused without the core", async () => {
    const r = await app.registry.execute("estimate.global", { estimation_id: 99 }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("№99 не найдена");
    expect(requests("POST /geostat/estimate/global")).toHaveLength(0);
    const never = await app.registry.execute("estimate.global", { estimation_id: 8 }, "claude");
    expect(never.ok).toBe(false);
    expect(never.result).toContain("ещё не выполнялась");
  });
  it("a cancelled job is a line", async () => {
    const r = await check({ estimation_id: 4 }, "estimate.global", "cancelled");
    expect(r).toMatchObject({ ok: true, result: "Глобальная проверка отменена: ничего не записано" });
    expect(globalStore.get()).toMatchObject({ phase: "cancelled", result: null });
  });
  it("a project that changed under it drops the result", async () => {
    await check({ estimation_id: 4 });
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(globalStore.get()).toMatchObject({ rowId: null, phase: "idle", result: null, qq: null });
    projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 3 });
  });
});

describe("the command estimate.qq", () => {
  it("the Q-Q alone, in the window of the global check", async () => {
    core.force("POST /geostat/estimate/qq", A.qq);
    const r = await app.registry.execute("estimate.qq", { estimation_id: 4 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Q-Q: композиты");
    expect(requests("POST /geostat/estimate/global")).toHaveLength(0);
    expect(windowStore.get().windows.some((w) => w.kind === GLOBAL_WINDOW)).toBe(true);
    expect(globalStore.get()).toMatchObject({ rowId: 4, phase: "idle", qqPhase: "done" });
  });
  it("«С весами» off asks the core again without the weights", async () => {
    core.force("POST /geostat/estimate/qq", A.qq);
    await app.registry.execute("estimate.qq", { estimation_id: 4 }, "claude");
    core.force("POST /geostat/estimate/qq", { status: 200, body: { ...A.qq.body, weighted: false } });
    setWeighted(false);
    await new Promise((r) => setTimeout(r, 50));
    expect(requests("POST /geostat/estimate/qq").map((q) => q.body)).toEqual([{ estimation_id: 4, weighted: true }, { estimation_id: 4, weighted: false }]);
    expect(globalStore.get().qq?.weighted).toBe(false);
  });
  it("a refusal of the core stands under the chart", async () => {
    core.force("POST /geostat/estimate/qq", { status: 409, body: { code: "not_estimated", message: "Поля оценки нет в блочной модели.", hint: "Выполните оценку." } });
    const r = await app.registry.execute("estimate.qq", { estimation_id: 4 }, "claude");
    expect(r.ok).toBe(false);
    expect(globalStore.get().qqError).toMatchObject({ code: "not_estimated" });
    expect(render()).toContain('data-testid="qq-error"');
  });
});

describe("the command estimate.swath", () => {
  it("opens the window of the swath of Э13 with the series the core gave: the blocks of the estimate and the samples of the row", async () => {
    core.force("POST /geostat/estimate/swath", A.swath);
    const r = await app.registry.execute("estimate.swath", { estimation_id: 4 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("«AU · OK · окисл»");
    expect(requests("POST /geostat/estimate/swath")[0].body).toEqual({ estimation_id: 4 });
    const w = windowStore.get().windows.find((x) => x.kind === "swath")!;
    expect(w).toBeDefined();
    const s = (w.props as { settings: { direction: string; series: Record<string, unknown>[] } }).settings;
    expect(s.direction).toBe("z");
    expect(s.series).toHaveLength(2);
    expect(s.series[0]).toMatchObject({ kind: "blocks", model_id: 2, field: "AU_OK_EST", weight: "volume", filter: { field: "ZONE", labels: ["окисл"] } });
    expect(s.series[1]).toMatchObject({ kind: "samples", set_id: 1, table: "1", field: "AU", weight: "field", weight_field: "DECL_W", within: "blocks", within_series: 0 });
  });
  it("the direction goes to the core; a wrong one is refused before it; without a row the one chosen in a window is taken", async () => {
    core.force("POST /geostat/estimate/swath", A.swath);
    await app.registry.execute("estimate.swath", { estimation_id: 4, direction: "x" }, "claude");
    expect(requests("POST /geostat/estimate/swath")[0].body).toEqual({ estimation_id: 4, direction: "x" });
    const bad = await app.registry.execute("estimate.swath", { estimation_id: 4, direction: "up" }, "claude");
    expect(bad.ok).toBe(false);
    windowStore.set({ windows: [] });
    setGlobalRow(5);
    core.force("POST /geostat/estimate/swath", A.swath);
    await app.registry.execute("estimate.swath", {}, "claude");
    expect(requests("POST /geostat/estimate/swath")[1].body).toEqual({ estimation_id: 5 });
  });
  it("with two rows and none chosen it asks for the row", async () => {
    const r = await app.registry.execute("estimate.swath", {}, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("estimation_id");
    expect(requests("POST /geostat/estimate/swath")).toHaveLength(0);
  });
});

describe("the window", () => {
  it("before the check: the executed rows only, «Проверить» and «Swath…» off until a row is chosen", () => {
    const html = render();
    expect(html).toContain('data-testid="estimation-global"');
    expect(html).toContain("AU · OK · окисл");
    expect(html).not.toContain("AU · OK · перв");
    expect(off(html, "global-run")).toBe(true);
    expect(off(html, "global-swath")).toBe(true);
    expect(html).toContain('data-testid="global-hint"');
    setGlobalRow(4);
    const after = render();
    expect(off(after, "global-run")).toBe(false);
    expect(off(after, "global-swath")).toBe(false);
  });
  it("the result: the verdict in green with words, the table, the variance, the Q-Q with its marks, «Swath…»", async () => {
    await check({ estimation_id: 4 });
    const html = render();
    expect(tag(html, "global-verdict")).toContain('data-verdict="ok"');
    expect(html).toContain("−1,71 % — в норме (допуск ±5 %)");
    expect(tag(html, "global-verdict-nn")).toContain('data-verdict="ok"');
    for (const k of ["naive", "declus", "nn", "estimate"]) expect(html).toContain(`data-testid="global-row-${k}"`);
    expect(html).toContain('data-testid="global-variance"');
    expect(html).toContain("C̄_VV 0,8043");
    expect(html).toContain('data-testid="global-blocks"');
    expect(tag(html, "qq-chart")).toContain('data-points="10"');
    expect(html).toContain('data-line="1:1"');
    expect((html.match(/data-mark="P/g) ?? []).length).toBe(3);
    expect(html).toContain('data-testid="qq-ks"');
    expect(html).toContain("Swath…");
    expect(html).not.toContain('data-testid="global-warnings"');
  });
  it("beyond the norm: red, with words", async () => {
    await check({ estimation_id: 4 });
    globalStore.set({ result: { ...glob(), diff_pct: 7.5, norm: false, warnings: ["Среднее оценки не сходится."] } });
    const html = render();
    expect(tag(html, "global-verdict")).toContain('data-verdict="bad"');
    expect(html).toContain("+7,5 % — вне нормы (допуск ±5 %)");
    expect(html).toContain('data-testid="global-warnings"');
    expect(html).toContain("Среднее оценки не сходится.");
  });
  it("the progress and «Отменить» while the job runs", () => {
    setGlobalRow(4);
    globalStore.set({ phase: "running", job: { source: "proj", id: 5 } });
    const html = render();
    expect(html).toContain('data-testid="global-progress"');
    expect(html).toContain('data-testid="global-cancel"');
    expect(off(html, "global-run")).toBe(true);
  });
});
