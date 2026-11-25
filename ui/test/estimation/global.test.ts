// The global check, its Q-Q and the swath of the estimate (Э18, 18.24) on the real answers of the core
// (`core/tests/data/estimate/contract_answers.json`, keys `global`, `qq`, `swath`): the readers, the norm of 5 % in words and colour, the
// table, the variance of the blocks against C̄_VV, the commands `estimate.global`, `estimate.qq`, `estimate.swath` (the window of Э13
// opens with the settings of the core), the window drawn. The expectations are read from the answers; the numbers are formatted as the code does.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { readGlobal, readQq } from "../../src/modules/estimation/checksApi";
import { GlobalWindow } from "../../src/modules/estimation/GlobalWindow";
import { blocksText, checkedRows, GLOBAL_WINDOW, globalReason, globalStore, globalSummary, qqSummary, ratioHint, referenceText, resetGlobal, setGlobalRow, setWeighted, statRows, varianceText, verdictOf, verdictText } from "../../src/modules/estimation/globalState";
import { num } from "../../src/modules/estimation/knaState";
import { loadRows } from "../../src/modules/estimation/state";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;

const ROW4 = A.estimations.body.estimations[0];
const NEVER_RUN = { ...ROW4, id: 8, name: "AU · OK · перв", last_run: null };
const STARTED = { status: 200, body: { source: "proj", id: 5 } };

// the answers of the core: the expectations are read from them
const G = A.global.body;
const Q = A.qq.body;

/** «+12,76 %» / «−1,71 %» — as the code tells a difference */
const signed = (x: number): string => `${x < 0 ? "−" : x > 0 ? "+" : ""}${num(Math.abs(x), 2)} %`;
/** the words of the norm, and the colour the code gives them */
const normWords = (norm: boolean): string => (norm ? "в норме" : "вне нормы");
const verdictCls = (norm: boolean | null): string => (norm === null ? "none" : norm ? "ok" : "bad");
const nfInt = (n: number): string => n.toLocaleString("ru-RU");

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
const glob = () => readGlobal(G)!;
async function check(args: Record<string, unknown>, command = "estimate.global", status = "done") {
  jobsStore.set({ jobs: [], workers: null, error: null });
  core.force("POST /geostat/estimate/global", STARTED);
  core.force("POST /geostat/estimate/qq", A.qq);
  core.job.status = status;
  core.job.result = G;
  return app.registry.execute(command, args, "claude");
}

describe("the readers on the answers of the core", () => {
  it("the global check: the moments, the difference, the norm, the variance of the blocks, the blocks the sums stand on", () => {
    const g = glob();
    expect(g).toMatchObject({ estimation_id: G.estimation_id, field: G.field, status_field: G.status_field, method: G.method, sub: G.sub, limit_pct: G.limit_pct, norm: G.norm, norm_nn: G.norm_nn });
    expect(g.composites.reference).toBe(G.composites.reference);
    expect(g.composites.declus).toMatchObject(G.composites.declus);
    expect(g.nn).toMatchObject(G.nn);
    expect(g.estimate).toMatchObject(G.estimate);
    expect(g.diff_pct).toBeCloseTo(G.diff_pct, 12);
    expect(g.block_var).toMatchObject({ expected: G.block_var.expected, sill: G.block_var.sill, disc: G.block_var.disc, size: G.block_var.size });
    expect(g.blocks).toEqual({ rows: G.blocks.rows, by_status: G.blocks.by_status, used: G.blocks.used, empty: G.blocks.empty, no_volume: G.blocks.no_volume });
    expect(g.warnings).toEqual(G.warnings);
  });
  it("a gap stays null; not an answer — null", () => {
    const g = readGlobal({ ...G, estimate: { n: 0, volume: null, mean: null, var: null, cv: null }, diff_pct: null, norm: null, block_var: { expected: null, estimate: null, sill: null, ratio: null } })!;
    expect(g.estimate).toEqual({ n: 0, volume: null, mean: null, var: null, cv: null });
    expect(g).toMatchObject({ diff_pct: null, norm: null });
    expect(g.block_var).toMatchObject({ expected: null, estimate: null, ratio: null });
    expect(readGlobal(null)).toBeNull();
    expect(readGlobal({ estimation_id: 4 })).toBeNull();
  });
  it("the Q-Q: [x, y, P %], the marks, the ratios, the extremes, the distance", () => {
    const q = readQq(Q)!;
    expect(q.points).toHaveLength(Q.points.length);
    expect(q.points[0]).toEqual(Q.points[0]);
    expect(q.marks.map((m) => m[2])).toEqual(Q.marks.map((m: number[]) => m[2]));
    expect(q.ratio).toEqual(Q.ratio);
    expect(q.extremes).toEqual(Q.extremes);
    expect(q).toMatchObject({ weighted: Q.weighted, field: Q.field, ks: Q.ks });
    expect(q.x).toMatchObject(Q.x);
    expect(q.y).toMatchObject(Q.y);
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
    expect(rows[0]).toMatchObject({ title: "Пробы домена, без весов", n: G.composites.naive.n, volume: null, mean: G.composites.naive.mean });
    expect(rows[3]).toMatchObject({ title: "Блоки, оценка", n: G.estimate.n, volume: G.estimate.volume, var: G.estimate.var });
    expect(statRows({ ...glob(), nn: null }).map((r) => r.key)).toEqual(["naive", "declus", "estimate"]);
  });
  it("the variance of the blocks against C̄_VV, and what the ratio says", () => {
    const bv = G.block_var;
    expect(varianceText(glob())).toBe(`Дисперсия оценки блоков ${num(bv.estimate, 4)}, ожидаемая дисперсия блоков C̄_VV ${num(bv.expected, 4)}, отношение ${num(bv.ratio, 2)}; порог вариограммы ${num(bv.sill, 4)}.`);
    expect(ratioHint(glob())).toContain("Кригинг сглаживает");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: 1.4 } })).toContain("больше ожидаемой");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: 0.05 } })).toContain("сильно сглажена");
    expect(ratioHint({ block_var: { ...glob().block_var, ratio: null } })).toBeNull();
    expect(varianceText({ block_var: { ...glob().block_var, expected: null, ratio: null } })).toContain("ожидаемая дисперсия не считается");
  });
  it("the blocks the sums stand on", () => {
    const labels = ["оценён", "вне домена", "мало проб", "вырожденная система"];
    const words = labels.flatMap((w, i) => (G.blocks.by_status[i] ? [`${w} ${nfInt(G.blocks.by_status[i])}`] : []));
    expect(blocksText(glob())).toBe(`В сумме ${nfInt(G.blocks.used)} из ${nfInt(G.blocks.rows)} блоков${words.length ? `: ${words.join(", ")}` : ""}.`);
  });
  it("the answer of the command and the words of the Q-Q", () => {
    expect(globalSummary(glob())).toContain(`${signed(G.diff_pct)} — ${normWords(G.norm)}`);
    expect(globalSummary(glob())).toContain(`Глобальная проверка «${G.name}» (${G.field})`);
    const r = Q.ratio.filter((x: number | null): x is number => x !== null);
    expect(qqSummary(readQq(Q)!)).toBe(`Q-Q: композиты (с весами декластеризации) против оценки блоков — отношение Y/X по квантилям от ${num(Math.min(...r), 2)} до ${num(Math.max(...r), 2)}, расстояние Колмогорова–Смирнова ${num(Q.ks, 3)}.`);
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
    expect(r.result).toContain(`${signed(G.diff_pct)} — ${normWords(G.norm)}`);
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
    core.force("POST /geostat/estimate/qq", { status: 200, body: { ...Q, weighted: false } });
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
  it("the result: the verdict in colour with words, the table, the variance, the Q-Q with its marks, «Swath…»", async () => {
    await check({ estimation_id: 4 });
    const html = render();
    expect(tag(html, "global-verdict")).toContain(`data-verdict="${verdictCls(G.norm)}"`);
    expect(html).toContain(`${signed(G.diff_pct)} — ${normWords(G.norm)} (допуск ±${G.limit_pct} %)`);
    expect(tag(html, "global-verdict-nn")).toContain(`data-verdict="${verdictCls(G.norm_nn)}"`);
    for (const k of ["naive", "declus", "nn", "estimate"]) expect(html).toContain(`data-testid="global-row-${k}"`);
    expect(html).toContain('data-testid="global-variance"');
    expect(html).toContain(`C̄_VV ${num(G.block_var.expected, 4)}`);
    expect(html).toContain('data-testid="global-blocks"');
    expect(tag(html, "qq-chart")).toContain(`data-points="${Q.points.length}"`);
    expect(html).toContain('data-line="1:1"');
    expect((html.match(/data-mark="P/g) ?? []).length).toBe(Q.marks.length);
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
