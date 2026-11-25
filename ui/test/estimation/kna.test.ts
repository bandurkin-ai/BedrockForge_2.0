// The analysis of the neighbourhood, KNA (Э18, 18.24) on the real answer of the core (`core/tests/data/estimate/contract_answers.json`, key
// `kna`): the reader, the words of the recommendation, the points of the charts (a gap breaks a line), the test blocks of the person,
// the request, the command `estimate.kna` (the job, the refusal, the cancel, a row that is not kriging), the window drawn, and
// «Применить к параметрам…» — it opens the row in the wizard with the value put in and saves nothing.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { dialogStore } from "../../src/shell/dialogs";
import { windowStore } from "../../src/shell/windows";
import { KNA_AXIS, knaValueText, readKna, type KnaBlock } from "../../src/modules/estimation/checksApi";
import { INDICATORS, lineOf, segments } from "../../src/modules/estimation/KnaChart";
import { KnaWindow } from "../../src/modules/estimation/KnaWindow";
import { addPick, applyNMax, blockTitle, knaBody, knaReason, knaRows, knaStore, KNA_WINDOW, knaSummary, parseBlock, recommendText, removePick, resetKna, setDraftKind, setDraftText, setMode, setKnaRow, toggleVary, triple, withNMax } from "../../src/modules/estimation/knaState";
import { loadRows } from "../../src/modules/estimation/state";
import { wizardStore } from "../../src/modules/estimation/wizardState";
import { jobsStore } from "../../src/modules/jobs/state";
import { projectStore } from "../../src/modules/projects/state";
import { A, installCore, newApp, teardown, type Core } from "./harness";

let app: App;
let core: Core;

const ROW4 = A.estimations.body.estimations[0];
const IDW = { ...ROW4, id: 9, name: "AU · IDW", method: "idw", prefix: "AU_IDW_" };
const STARTED = { status: 200, body: { source: "proj", id: 5 } };

beforeEach(async () => {
  core = installCore();
  core.estimations = { estimations: [ROW4, A.estimations.body.estimations[1], IDW] };
  app = newApp();
  resetKna();
  await loadRows();
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const render = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(KnaWindow, { winId: "w1" }))));
const tag = (html: string, id: string): string => html.match(new RegExp(`<[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? "";
const off = (html: string, id: string): boolean => tag(html, id).includes('disabled=""');
const kna = () => readKna(A.kna.body)!;
/** The command `estimate.kna` with the job of the core ending with `result`. */
async function run(args: Record<string, unknown>, status = "done", result: unknown = A.kna.body) {
  jobsStore.set({ jobs: [], workers: null, error: null }); // every run is a new job of the core
  core.force("POST /geostat/estimate/kna", STARTED);
  core.job.status = status;
  core.job.result = result;
  return app.registry.execute("estimate.kna", args, "claude");
}

describe("the reader on the answer of the core", () => {
  it("the blocks, the series by the parameters, the common recommendation", () => {
    const r = kna();
    expect(r).toMatchObject({ estimation_id: 4, name: "AU · OK · окисл", method: "ok", vary: ["n_max", "radius"], threshold: 0.95, recommend: 16, recommended: true, candidates: null, warnings: [] });
    expect(r.blocks).toHaveLength(1);
    const b = r.blocks[0];
    expect(b.at).toEqual({ row: 744, p: 744, ijk: [4, 4, 7] });
    expect(b.centre).toEqual([500040, 6700040, 297.5]);
    expect(b.n_r1).toBe(94);
    expect(b.vary.n_max).toHaveLength(16);
    expect(b.vary.n_max!.map((v) => v.value)).toEqual([4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64]);
    expect(b.vary.radius!.map((v) => v.value)).toEqual([0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]);
    expect(b.vary.disc).toBeUndefined();
    expect(b.recommend).toBe(16);
  });
  it("the indicators as the core gave them: bit for bit, the status, the number of samples", () => {
    const b = kna().blocks[0];
    expect(b.base).toEqual(A.kna.body.blocks[0].base);
    expect(b.vary.n_max![3]).toMatchObject({ value: 16, SLOPE: 1.1408573309962384, KE: 0.7167952627502802, NS: 16, status: 0 });
  });
  it("a gap stays null (a block outside the domain), never 0; disc is d of d × d × d; not an answer — null", () => {
    const raw = { ...A.kna.body, blocks: [{ ...A.kna.body.blocks[0], base: { KE: null, SLOPE: null, KV: null, SNW: null, w_far: null, LAGR: null, NS: null, status: 1 }, vary: { disc: [{ value: [3, 3, 3], KE: null, SLOPE: null, KV: null, SNW: null, w_far: null, LAGR: null, NS: null, status: 1 }] }, recommend: { n_max: null } }], vary: ["disc"], recommend: {} };
    const r = readKna(raw)!;
    expect(r.blocks[0].base).toMatchObject({ KE: null, SLOPE: null, NS: null, status: 1 });
    expect(r.blocks[0].vary.disc).toEqual([{ value: 3, KE: null, SLOPE: null, KV: null, SNW: null, w_far: null, LAGR: null, NS: null, status: 1 }]);
    expect(r.recommended).toBe(false);
    expect(r.recommend).toBeNull();
    expect(readKna(null)).toBeNull();
    expect(readKna({ blocks: [] })).toBeNull();
  });
});

describe("the words", () => {
  it("the values of the parameters", () => {
    expect(knaValueText("n_max", 16)).toBe("16");
    expect(knaValueText("radius", 0.75)).toBe("×0,75");
    expect(knaValueText("disc", 3)).toBe("3×3×3");
    expect(knaValueText("size", 2)).toBe("×2");
    expect(KNA_AXIS.n_max).toBe("n_max, проб");
  });
  it("the recommendation: the number, why, and that it is not applied by itself", () => {
    expect(recommendText(kna())).toBe("Рекомендуется n_max = 16: наименьшее число проб, при котором SLOPE не ниже 95 % от лучшего.");
    const three = { ...kna(), blocks: [kna().blocks[0], kna().blocks[0], kna().blocks[0]] };
    expect(recommendText(three)).toContain("наибольшее из рекомендаций блоков");
    expect(recommendText({ ...kna(), recommend: null })).toMatch(/^Рекомендации нет/);
    expect(recommendText({ ...kna(), recommended: false, recommend: null })).toMatch(/n_max не перебирался/);
    expect(knaSummary(kna())).toContain("Не применяется само");
  });
  it("a block by its label and its place", () => {
    const b: KnaBlock = { ...kna().blocks[0], auto: "много" };
    expect(blockTitle(b, 0)).toBe("много · родитель 4; 4; 7 · проб в r₁: 94");
    expect(blockTitle(kna().blocks[0], 1)).toBe("Блок 2 · родитель 4; 4; 7 · проб в r₁: 94");
  });
});

describe("the charts", () => {
  it("a line per block: the value of the parameter and the indicator; the indicators are SLOPE, KE, KV, NS", () => {
    expect(INDICATORS.map((i) => i.key)).toEqual(["SLOPE", "KE", "KV", "NS"]);
    const line = lineOf(kna().blocks[0], "n_max", "SLOPE");
    expect(line).toHaveLength(16);
    expect(line[3]).toEqual({ x: 16, y: 1.1408573309962384 });
    expect(lineOf(kna().blocks[0], "disc", "KE")).toEqual([]);
  });
  it("a gap breaks the line, it is not drawn as 0", () => {
    const segs = segments([{ x: 1, y: 1 }, { x: 2, y: 2 }, { x: 3, y: null }, { x: 4, y: 4 }, { x: 5, y: null }]);
    expect(segs.map((s) => s.map((p) => p.x))).toEqual([[1, 2], [4]]);
    expect(segments([{ x: 1, y: null }])).toEqual([]);
  });
});

describe("the test blocks of the person and the request", () => {
  it("three numbers with «;» or spaces; a decimal comma; indices — integers from 0", () => {
    expect(triple("4; 4; 7")).toEqual([4, 4, 7]);
    expect(triple("500040 6700040 297,5")).toEqual([500040, 6700040, 297.5]);
    expect(triple("1; 2")).toBeNull();
    expect(parseBlock("ijk", "4; 4; 7")).toEqual({ ok: true, value: { ijk: [4, 4, 7] } });
    expect(parseBlock("xyz", "500040; 6700040; 297,5")).toEqual({ ok: true, value: { xyz: [500040, 6700040, 297.5] } });
    expect(parseBlock("ijk", "4; 4; -1")).toMatchObject({ ok: false });
    expect(parseBlock("ijk", "4; 4,5; 1")).toMatchObject({ ok: false });
    expect(parseBlock("ijk", "4; 4")).toMatchObject({ ok: false, message: expect.stringContaining("три индекса") });
  });
  it("«Добавить»: the draft becomes a block (no repeats, up to ten), a wrong one says why", () => {
    setDraftText("4; 4");
    expect(addPick()).toBe(false);
    expect(knaStore.get().draftIssue).toMatch(/три индекса/);
    setDraftText("4; 4; 7");
    expect(knaStore.get().draftIssue).toBeNull();
    expect(addPick()).toBe(true);
    expect(knaStore.get()).toMatchObject({ mode: "own", draftText: "", picks: [{ kind: "ijk", text: "4; 4; 7", at: { ijk: [4, 4, 7] } }] });
    setDraftText("4; 4; 7");
    expect(addPick()).toBe(false);
    expect(knaStore.get().draftIssue).toBe("Такой блок уже добавлен.");
    setDraftKind("xyz");
    for (let i = 0; i < 9; i++) {
      setDraftText(`${i}; 0; 0`);
      expect(addPick()).toBe(true);
    }
    setDraftText("100; 0; 0");
    expect(addPick()).toBe(false);
    expect(knaStore.get().draftIssue).toMatch(/не больше 10/);
    removePick(0);
    expect(knaStore.get().picks).toHaveLength(9);
  });
  it("the body: automatic blocks send none; the own ones go as they are; the parameters in the order of the core", () => {
    setKnaRow(4);
    expect(knaBody(knaStore.get())).toEqual({ ok: true, body: { estimation_id: 4, vary: ["n_max", "radius", "disc", "size"] } });
    toggleVary("disc");
    toggleVary("n_max");
    toggleVary("n_max");
    expect(knaBody(knaStore.get())).toEqual({ ok: true, body: { estimation_id: 4, vary: ["n_max", "radius", "size"] } });
    setDraftText("4; 4; 7");
    addPick();
    expect(knaBody(knaStore.get())).toEqual({ ok: true, body: { estimation_id: 4, vary: ["n_max", "radius", "size"], blocks: [{ ijk: [4, 4, 7] }] } });
  });
  it("why it cannot be run: no row, a row that is not kriging, nothing to vary, own blocks without a block", () => {
    expect(knaReason(knaStore.get())).toBe("Не выбрана строка оценки.");
    setKnaRow(9);
    expect(knaReason(knaStore.get())).toBe("Анализ окрестности — только для простого и обычного кригинга.");
    setKnaRow(4);
    expect(knaReason(knaStore.get())).toBeNull();
    setMode("own");
    expect(knaReason(knaStore.get())).toBe("Не добавлено ни одного тестового блока.");
    setMode("auto");
    for (const p of ["n_max", "radius", "disc", "size"] as const) toggleVary(p);
    expect(knaReason(knaStore.get())).toBe("Не выбраны параметры для перебора.");
    setKnaRow(77);
    expect(knaReason(knaStore.get())).toMatch(/№77 не найдена/);
  });
  it("only SK and OK rows are offered", () => {
    expect(knaRows().map((r) => r.id)).toEqual([4, 5]);
  });
});

describe("the command estimate.kna", () => {
  it("without a row it opens the window (the only suitable row is taken when there is one)", async () => {
    const r = await app.registry.execute("estimate.kna", {}, "user");
    expect(r).toMatchObject({ ok: true, result: "Открыто окно «Анализ окрестности»" });
    expect(windowStore.get().windows.filter((w) => w.kind === KNA_WINDOW)).toHaveLength(1);
    await app.registry.execute("estimate.kna", {}, "user");
    expect(windowStore.get().windows.filter((w) => w.kind === KNA_WINDOW)).toHaveLength(1); // one window, it comes to the front
    expect(requests("POST /geostat/estimate/kna")).toHaveLength(0);
    expect(knaStore.get().rowId).toBeNull(); // two kriging rows: the person chooses
  });
  it("with a row it runs the job and answers with the recommendation, which is not applied", async () => {
    const r = await run({ estimation_id: 4 });
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Рекомендуется n_max = 16");
    expect(r.result).toContain("Не применяется само");
    expect(requests("POST /geostat/estimate/kna")[0].body).toEqual({ estimation_id: 4, vary: ["n_max", "radius", "disc", "size"] });
    expect(knaStore.get()).toMatchObject({ phase: "done", rowId: 4, shown: "n_max" });
    expect(knaStore.get().result?.recommend).toBe(16);
    expect(windowStore.get().windows.some((w) => w.kind === KNA_WINDOW)).toBe(true);
    // nothing of the row was written
    expect(core.sent.filter((q) => q.method === "PUT" || q.method === "POST").map((q) => q.path)).toEqual(["/geostat/estimate/kna"]);
  });
  it("vary, block and xyz of the arguments reach the core; a wrong one is refused before the request", async () => {
    await run({ estimation_id: 4, vary: "radius; n_max", block: "4; 4; 7 | 5; 4; 7", xyz: "500040; 6700040; 297,5" });
    expect(requests("POST /geostat/estimate/kna")[0].body).toEqual({ estimation_id: 4, vary: ["n_max", "radius"], blocks: [{ ijk: [4, 4, 7] }, { ijk: [5, 4, 7] }, { xyz: [500040, 6700040, 297.5] }] });
    resetKna();
    const bad = await app.registry.execute("estimate.kna", { estimation_id: 4, vary: "n_max; speed" }, "claude");
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Неизвестный параметр перебора «speed»");
    const bad2 = await app.registry.execute("estimate.kna", { estimation_id: 4, block: "4; 4" }, "claude");
    expect(bad2.ok).toBe(false);
    expect(requests("POST /geostat/estimate/kna")).toHaveLength(1);
  });
  it("a row that is not kriging or not in the project is refused with the words, nothing is asked of the core", async () => {
    const idw = await app.registry.execute("estimate.kna", { estimation_id: 9 }, "claude");
    expect(idw).toMatchObject({ ok: false });
    expect(idw.result).toContain("только для простого и обычного кригинга");
    const gone = await app.registry.execute("estimate.kna", { estimation_id: 99 }, "claude");
    expect(gone.result).toContain("№99 не найдена");
    expect(requests("POST /geostat/estimate/kna")).toHaveLength(0);
  });
  it("a refusal of the core stands under the bar with its words and code", async () => {
    core.force("POST /geostat/estimate/kna", { status: 422, body: { code: "kna_method", message: "Анализ параметров кригинга — только для простого и обычного кригинга.", hint: "Выберите метод SK или OK.", param: "method.kind" } });
    const r = await app.registry.execute("estimate.kna", { estimation_id: 4 }, "claude");
    expect(r.ok).toBe(false);
    expect(knaStore.get()).toMatchObject({ phase: "failed", error: { code: "kna_method", hint: "Выберите метод SK или OK." } });
    expect(render()).toContain('data-testid="kna-error"');
  });
  it("a failed job tells why; a cancelled one is a line, not an error", async () => {
    core.job.error = { code: "job_failed", message: "Не хватает памяти.", hint: "Закройте другие окна." };
    const failed = await run({ estimation_id: 4 }, "failed", null);
    expect(failed).toMatchObject({ ok: false });
    expect(failed.result).toContain("Не хватает памяти.");
    resetKna();
    core.job.error = null;
    const cancelled = await run({ estimation_id: 4 }, "cancelled", null);
    expect(cancelled).toMatchObject({ ok: true, result: "Анализ окрестности отменён: ничего не записано" });
    expect(knaStore.get()).toMatchObject({ phase: "cancelled", result: null, error: null, note: "Анализ окрестности отменён: ничего не записано" });
    expect(render()).toContain('data-testid="kna-note"');
  });
  it("a project that changed under it drops the result", async () => {
    await run({ estimation_id: 4 });
    expect(knaStore.get().result).not.toBeNull();
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    expect(knaStore.get()).toMatchObject({ rowId: null, phase: "idle", result: null });
    projectStore.set({ current: { uid: "p1", name: "П" } as never, version: 3 });
  });
});

describe("the window", () => {
  it("before the run: the row, the four parameters, the blocks, «Выполнить анализ» off until a row is chosen", () => {
    const html = render();
    expect(html).toContain('data-testid="estimation-kna"');
    expect(html).toContain('data-testid="kna-row"');
    expect(html).toContain("AU · OK · окисл");
    expect(html).not.toContain("AU · IDW");
    for (const p of ["n_max", "radius", "disc", "size"]) expect(html).toContain(`data-testid="kna-vary-${p}"`);
    expect(html).toContain('data-testid="kna-mode-auto"');
    expect(off(html, "kna-run")).toBe(true);
    expect(tag(html, "kna-run")).toContain("Не выбрана строка оценки.");
    expect(html).toContain('data-testid="kna-hint"');
    setKnaRow(4);
    expect(off(render(), "kna-run")).toBe(false);
  });
  it("own blocks: the field, the kind, «Добавить», the list with «×»", () => {
    setKnaRow(4);
    setMode("own");
    setDraftText("4; 4; 7");
    addPick();
    const html = render();
    expect(html).toContain('data-testid="kna-draft-text"');
    expect(html).toContain('data-testid="kna-add"');
    expect(html).toContain("Родитель 4; 4; 7");
    expect(html).toContain('data-testid="kna-unpick-0"');
  });
  it("the result: the recommendation, four charts with the mark and the ring, the table with the recommended row", async () => {
    await run({ estimation_id: 4 });
    const html = render();
    expect(html).toContain('data-testid="kna-recommend" data-n-max="16"');
    expect(html).toContain("Рекомендуется n_max = 16");
    for (const i of ["SLOPE", "KE", "KV", "NS"]) expect(html).toContain(`data-testid="kna-chart-${i}"`);
    expect((html.match(/data-mark="recommend"/g) ?? []).length).toBe(4);
    expect((html.match(/data-ring="16"/g) ?? []).length).toBe(4);
    expect(html).toContain('data-line="ideal"'); // SLOPE = 1
    expect((html.match(/data-points="16"/g) ?? []).length).toBe(4);
    expect(html).toContain("n_max = 16");
    expect(html).toContain('data-testid="kna-table"');
    expect(tag(html, "kna-variant-16")).toContain('data-recommended="true"');
    expect(tag(html, "kna-variant-20")).toContain('data-recommended="false"');
    expect(html).toContain('data-testid="kna-base"');
    expect(html).toContain("← рекомендация");
    expect(html).toContain('data-testid="kna-shown-n_max"');
    expect(html).toContain('data-testid="kna-shown-radius"');
    expect(html).not.toContain('data-testid="kna-shown-disc"');
  });
  it("the radius: the charts have no recommendation mark, the table has no recommended row", async () => {
    await run({ estimation_id: 4 });
    knaStore.set({ shown: "radius" });
    const html = render();
    expect(html).not.toContain('data-mark="recommend"');
    expect(html).toContain('data-testid="kna-variant-0.5"');
    expect(html).not.toContain("← рекомендация");
  });
  it("the progress and «Отменить» while the job runs", () => {
    setKnaRow(4);
    knaStore.set({ phase: "running", job: { source: "proj", id: 5 } });
    const html = render();
    expect(html).toContain('data-testid="kna-progress"');
    expect(html).toContain('data-testid="kna-cancel"');
    expect(off(html, "kna-run")).toBe(true);
    expect(off(html, "kna-row")).toBe(true);
  });
  it("«Применить к параметрам…» is off without a recommendation and says why", async () => {
    await run({ estimation_id: 4 });
    knaStore.set({ result: { ...kna(), recommend: null } });
    const html = render();
    expect(off(html, "kna-apply")).toBe(true);
    expect(tag(html, "kna-apply")).toContain("Рекомендации нет");
  });
});

describe("«Применить к параметрам…»", () => {
  it("the passes of the search get n_max, n_min is cut to it; a search without passes is not changed", () => {
    const s = withNMax({ r1: 60, passes: [{ n_min: 4, n_max: 24 }, { n_min: 20, n_max: 24 }] }, 16)!;
    expect(s.passes).toEqual([{ n_min: 4, n_max: 16 }, { n_min: 16, n_max: 16 }]);
    expect(s.r1).toBe(60);
    expect(withNMax({ passes: [] }, 16)).toBeNull();
    expect(withNMax(null, 16)).toBeNull();
  });
  it("it opens the row in the wizard with n_max put in, on the step of the search, unsaved: nothing is saved by itself", async () => {
    core.force("GET /geostat/estimations/4", { status: 200, body: A.estimation.body });
    const text = await applyNMax(app, 4, 16);
    expect(text).toContain("n_max = 16");
    const w = wizardStore.get();
    expect(w.editing?.id).toBe(4);
    expect(w.step).toBe(4);
    expect(w.dirty).toBe(true);
    const passes = w.form.search.passes ?? [];
    expect(passes.length).toBeGreaterThan(0);
    expect(passes.every((p) => p.nMax === "16")).toBe(true);
    expect(core.sent.filter((q) => q.method === "PUT" || (q.method === "POST" && q.path === "/geostat/estimations"))).toEqual([]);
  });
  it("the button of the window does the same by the person's click only (the recommendation alone changes nothing)", async () => {
    await run({ estimation_id: 4 });
    expect(wizardStore.get().editing).toBeNull();
    expect(wizardStore.get().dirty).toBe(false);
    expect(dialogStore.get().queue).toHaveLength(0);
  });
});
