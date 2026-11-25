// The inspector of a block (Э18, 18.Д4; ТЗ 7.35 Д-30) on the REAL answers of the core (`core/tests/data/estimate/inspect_answers.json`,
// `BF_REGEN_INSPECT=1` of 18.Д3: `pass_1` — a block taken in the first pass, `pass_3` — a block of the third pass with negative weights
// and the reasons of the table, `few_samples` — STATUS 2, `out_of_domain` — STATUS 1, `changed` — the field does not match, `errors`):
// the readers, the table of the probes (the order, the filter by the reason), the colours of the weights, the state and the overtaken
// requests, the command `estimate.inspect`, the click of a block, the window drawn, the tabs of several rows, «Скопировать снимок»,
// the jump to the interval of 7.17.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { pickedBlockStore } from "../../src/modules/blocks/display";
import { estimationStore } from "../../src/modules/estimation/state";
import { tablesStore } from "../../src/modules/tables/state";
import { isSuperseded, readInspect, type InspectAnswer } from "../../src/modules/estimation/inspect/api";
import { answerText, bindInspect, inspectWindow, parseTriple, renderInspectWindow, requestOf } from "../../src/modules/estimation/inspect/bind";
import { copySnapshot, InspectWindow } from "../../src/modules/estimation/inspect/InspectWindow";
import { intervalTarget, openInterval } from "../../src/modules/estimation/inspect/interval";
import { activeResult, inspect, inspectStore, INSPECT_WINDOW, pick, pickedCandidate, reinspect, resetInspect, setActive, setReason, setShow, sortBy } from "../../src/modules/estimation/inspect/state";
import { blockTitle, compareText, COLORS, countsText, cssColor, intervalText, maxWeight, nextSort, reasonOptions, shownCandidates, snapshotText, tabLabel, valueRows, weightClass, weightColor, weightSums } from "../../src/modules/estimation/inspect/view";
import { blocksStore } from "../../src/modules/blocks/state";
import { MODEL, newApp, installCore, teardown, type Core } from "./harness";

/** The model of `inspect_answers.json` (its own world, the block model 7), next to the one of `contract_answers.json` of the harness. */
const INSPECT_MODEL = 7;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const F: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/estimate/inspect_answers.json"), "utf-8"));
const ANS = (k: string): InspectAnswer => readInspect(F.answers[k]);
const ROUTE = "POST /geostat/estimate/inspect";

let app: App;
let core: Core;

beforeEach(() => {
  core = installCore();
  app = newApp();
  blocksStore.set((s) => ({ list: [...s.list, { ...MODEL, id: INSPECT_MODEL }] }));
  bindInspect(app);
  resetInspect();
  inspectStore.set({ show: { ellipsoid: true, segments: true, excluded: true } });
  pickedBlockStore.set({ picked: null });
});
afterEach(() => {
  resetInspect();
  windowStore.set({ windows: [] });
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
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const render = () => flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(InspectWindow, { winId: "w1" }))));
const sent = () => core.sent.filter((r) => `${r.method} ${r.path}` === ROUTE);
const answer = (k: string) => core.force(ROUTE, { status: 200, body: F.answers[k] });
const tag = (html: string, id: string): string => html.match(new RegExp(`<[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? "";
const count = (html: string, id: string): number => (html.match(new RegExp(`data-testid="${id}"`, "g")) ?? []).length;

describe("the reader on the answers of the core", () => {
  it("the block, the row, the probes with their reasons and weights", () => {
    const a = ANS("pass_1");
    expect(a.block).toMatchObject({ ijk: [13, 7, 1], row: 473, rows: 1, sub: null });
    expect(a.block.size).toEqual([5, 5, 2.5]);
    expect(a.model_id).toBe(7);
    const r = a.results[0];
    expect(r).toMatchObject({ estimation_id: 9, method: "ok", status: 0, status_label: "оценён", pass: 1, active: true, changed: false, warning: null });
    expect(r.candidates).toHaveLength(121);
    expect(r.counts).toEqual({ candidates: 121, selected: 9, shown: 121 });
    expect(r.candidates.filter((c) => c.reason === "selected")).toHaveLength(9);
    expect(r.segments).toHaveLength(9);
    expect(r.source).toEqual({ set_id: 8, table: "3", field: "AU" });
    expect(r.ellipsoid).toMatchObject({ pass: 1, r1: 18, r2: 13, r3: 7, ang1: 35 });
    expect(r.ellipsoid!.centre_xyz).toEqual([1040.20671, 2066.47595, -296.25]);
  });
  it("a gap stays null, never 0: no weight, no value of the block", () => {
    const few = ANS("few_samples").results[0];
    expect(few.status).toBe(2);
    expect(few.values.EST).toBeNull();
    expect(few.mu).toBeNull();
    expect(few.candidates.every((c) => c.lambda === null)).toBe(true);
    expect(few.segments).toEqual([]);
    const out = ANS("out_of_domain").results[0];
    expect(out).toMatchObject({ active: false, status: 1, status_label: "вне домена", candidates: [] });
    expect(out.fields.every((f) => f.same === null)).toBe(true);
  });
  it("a damaged answer is a Russian error with its hint", () => {
    expect(() => readInspect({ model_id: 7, results: [] })).toThrow(/непонятном виде/);
    expect(() => readInspect(null)).toThrow();
  });
});

describe("the table of the probes", () => {
  const r = ANS("pass_3").results[0];
  it("the reasons present with their number, «выбрана» first", () => {
    const o = reasonOptions(r.candidates);
    expect(o[0]).toMatchObject({ code: "selected", label: "выбрана" });
    expect(o.reduce((s, x) => s + x.count, 0)).toBe(65);
    expect(o.map((x) => x.code)).toEqual(expect.arrayContaining(["selected", "soft_far", "over_n_max"]));
    expect(o.find((x) => x.code === "selected")!.count).toBe(24);
  });
  it("the filter by the reason and the order by a column; a gap goes last either way", () => {
    expect(shownCandidates(r.candidates, "selected", null)).toHaveLength(24);
    expect(shownCandidates(r.candidates, "nothing", null)).toEqual([]);
    const asc = shownCandidates(r.candidates, "selected", { key: "lambda", dir: "asc" });
    const lam = asc.map((c) => c.lambda as number);
    expect([...lam].sort((x, y) => x - y)).toEqual(lam);
    expect(lam[0]).toBeLessThan(0);
    const desc = shownCandidates(r.candidates, "selected", { key: "lambda", dir: "desc" }).map((c) => c.lambda as number);
    expect(desc).toEqual([...lam].reverse());
    // the probes cut off have no weight: they go after the weights in both directions
    const all = shownCandidates(r.candidates, null, { key: "lambda", dir: "desc" });
    expect(all.slice(0, 24).every((c) => c.lambda !== null)).toBe(true);
    expect(all.slice(24).every((c) => c.lambda === null)).toBe(true);
    const byDist = shownCandidates(r.candidates, null, { key: "dist", dir: "asc" }).map((c) => c.dist as number);
    expect([...byDist].sort((x, y) => x - y)).toEqual(byDist);
    const byHole = shownCandidates(r.candidates, null, { key: "hole", dir: "asc" }).map((c) => c.hole);
    expect([...byHole].sort((x, y) => x.localeCompare(y, "ru", { numeric: true }))).toEqual(byHole);
  });
  it("the click on a header: ascending, descending, off; another column starts again", () => {
    expect(nextSort(null, "z")).toEqual({ key: "z", dir: "asc" });
    expect(nextSort({ key: "z", dir: "asc" }, "z")).toEqual({ key: "z", dir: "desc" });
    expect(nextSort({ key: "z", dir: "desc" }, "z")).toBeNull();
    expect(nextSort({ key: "z", dir: "desc" }, "h")).toEqual({ key: "h", dir: "asc" });
  });
  it("the words: the interval, the counts, the sums of the weights (OK: 1; negative ones apart)", () => {
    expect(intervalText({ from: 20, to: 22.5 })).toBe("20 – 22,5");
    expect(intervalText({ from: null, to: 1 })).toBe("—");
    expect(countsText(r, 24)).toBe("Кандидатов 65, выбрано 24, в таблице 24");
    const sel = r.candidates.filter((c) => c.reason === "selected");
    const w = weightSums(sel);
    expect(w.sum).toBeCloseTo(1, 5);
    expect(w.negative).toBeLessThan(0);
    expect(weightSums(ANS("few_samples").results[0].candidates).sum).toBeNull();
  });
});

describe("the colours of the weights", () => {
  it("a positive weight is blue (Cobalt at the largest), a negative one is Copper Orange, no weight is Slate", () => {
    const hex = (c: number[]) => `#${c.map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("")}`;
    expect(hex(weightColor(0.4, 0.4))).toBe(COLORS.positive);
    expect(hex(weightColor(-0.4, 0.4))).toBe(COLORS.negative);
    expect(hex(weightColor(null, 0.4))).toBe(COLORS.excluded);
    expect(hex(weightColor(0, 0.4))).toBe(COLORS.positiveLight);
    expect(cssColor(weightColor(0.4, 0.4))).toBe("rgb(31, 107, 255)");
    expect(weightClass(-0.01)).toBe("negative");
    expect(weightClass(0.01)).toBe("positive");
    expect(weightClass(null)).toBeNull();
    expect(maxWeight(ANS("pass_3").results[0].segments)).toBeGreaterThan(0);
  });
});

describe("the row: the values and the comparison with the fields", () => {
  it("the values in the order of 8.8.2; STATUS with its words; a gap is a dash", () => {
    const rows = valueRows(ANS("pass_1").results[0]);
    expect(rows.slice(0, 3).map((v) => v.key)).toEqual(["STATUS", "PASS", "EST"]);
    expect(rows[0].text).toBe("0 — оценён");
    expect(rows.find((v) => v.key === "EST")!.text).toBe("1,168826");
    const few = valueRows(ANS("few_samples").results[0]);
    expect(few[0].text).toBe("2 — мало проб");
    expect(few.find((v) => v.key === "EST")!.text).toBe("—");
  });
  it("the field that does not match is told: the stored value, the recalculation, the verdict", () => {
    const f = ANS("changed").results[0].fields.find((x) => x.key === "EST")!;
    expect(compareText(f)).toEqual({ stored: "1,16882594", calc: "2,8564092", mark: "не совпало" });
    expect(compareText(ANS("out_of_domain").results[0].fields[0]).mark).toBe("—");
    expect(compareText(ANS("pass_1").results[0].fields[1]).mark).toBe("совпало");
  });
  it("the title and the tabs", () => {
    expect(blockTitle(ANS("pass_1"))).toBe("Блок · 13, 7, 1");
    expect(blockTitle(null)).toBe("Блок · i, j, k");
    expect(tabLabel(ANS("out_of_domain").results[0])).toBe("AU · OK · руда (вне домена)");
    expect(tabLabel(ANS("pass_1").results[0])).toBe("AU · OK · руда");
  });
});

describe("the state: the newest request only", () => {
  it("an answer is kept with the row shown; the probe lit is one of the row; a new block resets it", async () => {
    answer("pass_1");
    const a = await inspect({ model_id: 7, block: [13, 7, 1] });
    expect(a?.block.row).toBe(473);
    expect(inspectStore.get()).toMatchObject({ loading: false, error: null, picked: null });
    expect(activeResult()!.estimation_id).toBe(9);
    const first = activeResult()!.candidates[0];
    pick(first.i);
    expect(pickedCandidate()!.i).toBe(first.i);
    pick(first.i);
    expect(pickedCandidate()).toBeNull();
    pick(first.i);
    answer("pass_3");
    await inspect({ model_id: 7, block: [1, 2, 3] });
    expect(inspectStore.get().picked).toBeNull();
  });
  it("an older answer does not overwrite a newer one", async () => {
    let release: (a: InspectAnswer) => void = () => undefined;
    const slow = () => new Promise<InspectAnswer>((r) => (release = r));
    const p1 = inspect({ model_id: 7, block: [1, 1, 1] }, slow);
    const p2 = inspect({ model_id: 7, block: [2, 2, 2] }, async () => ANS("pass_3"));
    expect((await p2)?.results[0].pass).toBe(3);
    release(ANS("pass_1"));
    expect(await p1).toBeNull();
    expect(inspectStore.get().answer!.results[0].pass).toBe(3);
    expect(inspectStore.get().request).toEqual({ model_id: 7, block: [2, 2, 2] });
  });
  it("the tab, the order and the filter stay when the same block is asked again; the request carries the channel and a growing seq", async () => {
    answer("pass_3");
    await inspect({ model_id: 7, row: 10 });
    sortBy("lambda");
    setReason("selected");
    answer("pass_3");
    await reinspect();
    expect(inspectStore.get()).toMatchObject({ sort: { key: "lambda", dir: "asc" }, reason: "selected" });
    const [a, b] = sent();
    expect(a.body).toMatchObject({ model_id: 7, row: 10, channel: "inspector" });
    expect(Number(b.body.seq)).toBeGreaterThan(Number(a.body.seq));
    answer("pass_3");
    await inspect({ model_id: 8, row: 10 });
    expect(inspectStore.get()).toMatchObject({ reason: null });
  });
  it("a refusal of the core is kept with its hint; a superseded request is not a failure", async () => {
    core.force(ROUTE, { status: 404, body: F.errors.no_model });
    expect(await inspect({ model_id: 99, row: 1 })).toBeNull();
    expect(inspectStore.get().error).toMatchObject({ code: "block_model_not_found", message: "Блочная модель не найдена.", hint: "Обновите дерево проекта." });
    core.force(ROUTE, { status: 409, body: { code: "superseded", message: "Заменён", hint: "—" } });
    expect(await inspect({ model_id: 7, row: 1 })).toBeNull();
    expect(inspectStore.get().error).toBeNull();
    expect(isSuperseded(new Error("x"))).toBe(false);
  });
});

describe("the command estimate.inspect", () => {
  it("«i; j; k» opens the window «Блок · 13, 7, 1» and asks the core; the answer is in words", async () => {
    answer("pass_1");
    const r = await app.registry.execute("estimate.inspect", { model_id: 7, block: "13; 7; 1" }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Блок · 13, 7, 1");
    expect(r.result).toContain("«AU · OK · руда»: оценён, EST 1,1688, проб в системе 9 из 121");
    expect(sent()[0].body).toMatchObject({ model_id: 7, block: [13, 7, 1], channel: "inspector" });
    const w = windowStore.get().windows.filter((x) => x.kind === INSPECT_WINDOW);
    expect(w).toHaveLength(1);
    expect(w[0].title).toBe("Блок · 13, 7, 1");
    expect(renderInspectWindow(w[0])).not.toBeNull();
    expect(renderInspectWindow({ ...w[0], kind: "other" })).toBeNull();
    // again — the same window
    answer("pass_3");
    await app.registry.execute("estimate.inspect", { model_id: 7, xyz: "998,7; 2048,3; −298,75", params_id: 9 }, "user");
    expect(windowStore.get().windows.filter((x) => x.kind === INSPECT_WINDOW)).toHaveLength(1);
    expect(sent()[1].body).toMatchObject({ xyz: [998.7, 2048.3, -298.75], params_id: 9 });
  });
  it("the block is given once; the numbers are checked before the core is asked", async () => {
    const run = (a: Record<string, unknown>) => app.registry.execute("estimate.inspect", { model_id: 7, ...a }, "claude");
    expect((await run({})).result).toContain("Задайте блок");
    expect((await run({ block: "1; 2; 3", xyz: "1; 2; 3" })).result).toContain("что-то одно");
    expect((await run({ block: "1; 2" })).result).toContain("три целых");
    expect((await run({ block: "1; 2,5; 3" })).result).toContain("три целых");
    expect((await run({ block: "1; -2; 3" })).result).toContain("неотрицательных");
    expect((await run({ xyz: "a; b; c" })).result).toContain("три числа");
    expect((await run({ row: -1 })).result).toContain("целое число от 0");
    expect((await run({ block: "1; 2; 3", params_id: 0 })).result).toContain("от 1");
    expect((await app.registry.execute("estimate.inspect", { model_id: 99, block: "1; 2; 3" }, "claude")).result).toContain("Блочная модель №99 не найдена");
    expect(sent()).toHaveLength(0);
    expect(parseTriple("1040,2; 2066,5; −296", false)).toEqual([1040.2, 2066.5, -296]);
    expect(parseTriple([1, 2, 3], true)).toEqual([1, 2, 3]);
    expect(requestOf({ model_id: 7, row: 12 })).toEqual({ model_id: 7, row: 12 });
  });
  it("the refusal of the core reaches the caller with its hint, and stays in the window", async () => {
    core.force(ROUTE, { status: 422, body: F.errors.no_such_row });
    const r = await app.registry.execute("estimate.inspect", { model_id: 7, row: 10_000_000 }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("В модели нет блока с номером строки 10000000");
    const html = render();
    expect(html).toContain('data-testid="inspect-error"');
    expect(html).toContain("Номер строки — от 0 до 2559.");
  });
  it("the text of the answer for every case: not estimated, out of the domain, a change", () => {
    expect(answerText(ANS("out_of_domain"))).toContain("«AU · OK · руда»: вне домена");
    expect(answerText(ANS("few_samples"))).toContain("мало проб, EST —, проб в системе 0 из 4");
    expect(answerText(ANS("changed"))).toContain("параметры или входы изменились");
    expect(answerText({ ...ANS("pass_1"), results: [] })).toContain("нет строк");
  });
});

describe("the click of a block", () => {
  const found = (model: number, row: number) => ({ model, point: [0, 0, 0] as [number, number, number], status: "found" as const, error: null, answer: { found: true as const, row, p: row, ijk: [13, 7, 1] as [number, number, number], sub: null, centre: [0, 0, 0] as [number, number, number], size: [5, 5, 2.5] as [number, number, number], passed: true, f: 1, values: {} } });
  const runRow = (model: number, last: boolean) => ({ ...(estimationStore.get().rows[0] ?? {}), id: 9, model_id: model, last_run: last ? { at: "x", by_status: [1, 0, 0, 0] } : null });

  it("a block of a model with an estimation that was run opens the inspector on its row", async () => {
    await tick(40); // the rows the app read when it opened come first
    estimationStore.set({ rows: [runRow(7, true)] as never, loaded: true });
    answer("pass_1");
    pickedBlockStore.set({ picked: found(7, 473) });
    await waitFor(() => inspectStore.get().answer !== null);
    expect(sent()[0].body).toMatchObject({ model_id: 7, row: 473 });
    expect(inspectWindow()).not.toBeNull();
    // the next click is asked in the same window
    answer("pass_3");
    pickedBlockStore.set({ picked: found(7, 12) });
    await waitFor(() => inspectStore.get().answer?.results[0].pass === 3);
    expect(windowStore.get().windows.filter((x) => x.kind === INSPECT_WINDOW)).toHaveLength(1);
  });
  it("a model without a run, a block not found, a model with no rows — no window", async () => {
    await tick(40);
    estimationStore.set({ rows: [runRow(7, false)] as never, loaded: true });
    pickedBlockStore.set({ picked: found(7, 1) });
    estimationStore.set({ rows: [runRow(8, true)] as never, loaded: true });
    pickedBlockStore.set({ picked: found(7, 2) });
    pickedBlockStore.set({ picked: { ...found(7, 3), status: "missing", answer: { found: false } } });
    await tick(30);
    expect(inspectWindow()).toBeNull();
    expect(sent()).toHaveLength(0);
  });
  it("the project changed or the window closed: the block is forgotten", async () => {
    answer("pass_1");
    await app.registry.execute("estimate.inspect", { model_id: 7, row: 1 }, "user");
    expect(inspectStore.get().answer).not.toBeNull();
    windowStore.set({ windows: [] });
    expect(inspectStore.get()).toMatchObject({ answer: null, request: null });
  });
});

describe("the window drawn", () => {
  const open = async (k: string, args: Record<string, unknown> = { block: "13; 7; 1" }) => {
    answer(k);
    await app.registry.execute("estimate.inspect", { model_id: 7, ...args }, "user");
    return render();
  };
  it("the block, the status, the values, the passes, the 121 probes with their reasons and the weights", async () => {
    const html = await open("pass_1");
    expect(html).toContain("Блок · 13, 7, 1");
    expect(html).toContain("X 1040,207, Y 2066,476, Z −296,25");
    expect(html).toMatch(/data-testid="inspect-status" data-status="0">оценён</);
    expect(html).toContain('data-testid="inspect-value-EST">1,168826<');
    expect(html).toContain("Проход 1: проб 9 — принят");
    expect(html).toContain("Эллипсоид прохода 1: дальности 18 × 13 × 7 м");
    expect(count(html, "inspect-reread")).toBe(1);
    expect(html).toContain("Скопировать снимок");
    expect(html).not.toContain('data-testid="inspect-changed"');
    expect(html).not.toContain('data-testid="inspect-tabs"');
    expect((html.match(/data-testid="inspect-row-\d+"/g) ?? []).length).toBe(121);
    expect(html).toContain('<option value="selected">выбрана (9)</option>');
    expect(html).toContain("вне эллипсоида");
    expect(html).toContain("дальше d_м (мягкая граница)");
    expect(html).toContain("лимит скважины");
    expect(html).toContain("Кандидатов 121, выбрано 9");
    expect(html).toContain('aria-sort="none"');
    expect(html).toContain('data-testid="inspect-interval-173"');
  });
  it("the filter and the order are in the table; a negative weight is in the class of Copper Orange", async () => {
    await open("pass_3", { row: 5 });
    setReason("selected");
    sortBy("lambda");
    let html = render();
    expect((html.match(/data-testid="inspect-row-\d+"/g) ?? []).length).toBe(24);
    expect(html).toContain('aria-sort="ascending"');
    expect(html).toMatch(/class="[^"]*negative[^"]*" data-weight="negative"/);
    const first = html.match(/data-testid="inspect-row-(\d+)"/)![1];
    const lowest = Math.min(...ANS("pass_3").results[0].candidates.filter((c) => c.lambda !== null).map((c) => c.lambda as number));
    expect(ANS("pass_3").results[0].candidates.find((c) => c.i === Number(first))!.lambda).toBe(lowest);
    pick(Number(first));
    html = render();
    expect(html).toMatch(new RegExp(`data-testid="inspect-row-${first}"[^>]*data-picked="true"`));
    setReason("over_n_max");
    html = render();
    expect((html.match(/data-testid="inspect-row-\d+"/g) ?? []).length).toBe(12);
    expect(html).toContain("за n_max");
  });
  it("«Параметры или входы изменились» and the field that does not match", async () => {
    const html = await open("changed");
    expect(html).toContain('data-testid="inspect-changed"');
    expect(html).toContain("Параметры или входы изменились");
    expect(html).toContain("изменена таблица «Пробы инспектора»; пересчёт не совпал с полями: AU_OK_EST");
    expect(html).toMatch(/data-testid="inspect-field-EST" data-same="false"/);
    expect(html).toContain("не совпало");
  });
  it("a block not estimated: the status in words, no probes, no weights, no invented zero", async () => {
    let html = await open("few_samples", { row: 6 });
    expect(html).toContain("мало проб");
    expect(html).toContain("Проход 1: проб 0 — мало проб");
    expect(html).not.toContain("Сумма весов");
    expect(html).toMatch(/data-testid="inspect-value-EST">—</);
    html = await open("out_of_domain", { row: 7 });
    expect(html).toContain("вне домена");
    expect(html).toContain("Блок вне домена строки: пробы для него не отбирались.");
  });
  it("several rows of the plan are tabs; the tab changes the row, the table and the 3D", async () => {
    const one = F.answers.pass_1.results[0];
    const two = { ...F.answers.pass_3.results[0], estimation_id: 10, name: "AU · IDW · руда", method: "idw" };
    core.force(ROUTE, { status: 200, body: { ...F.answers.pass_1, results: [one, two] } });
    await app.registry.execute("estimate.inspect", { model_id: 7, row: 473 }, "user");
    let html = render();
    expect(html).toContain('data-testid="inspect-tabs"');
    expect(html).toMatch(/data-testid="inspect-tab-9"[^>]*>AU · OK · руда</);
    expect(tag(html, "inspect-tab-9")).toContain('aria-selected="true"');
    expect(tag(html, "inspect-tab-10")).toContain('aria-selected="false"');
    expect((html.match(/data-testid="inspect-row-\d+"/g) ?? []).length).toBe(121);
    setActive(10);
    html = render();
    expect(activeResult()!.name).toBe("AU · IDW · руда");
    expect((html.match(/data-testid="inspect-row-\d+"/g) ?? []).length).toBe(65);
    expect(html).toContain("Проход 3");
  });
  it("without the block asked: the hint; while it is asked: «проверяется»", () => {
    expect(render()).toContain("Щёлкните блок модели в 3D");
    inspectStore.set({ loading: true });
    expect(render()).toContain("Блок проверяется…");
    expect(render()).toContain('data-state="loading"');
  });
  it("the toggles of the 3D are in the store", () => {
    setShow({ ellipsoid: false });
    expect(inspectStore.get().show).toEqual({ ellipsoid: false, segments: true, excluded: true });
  });
});

describe("«Скопировать снимок»", () => {
  it("the JSON of the block: the block, the row, the parameters, the probes and the weights as the core wrote them", async () => {
    const a = ANS("pass_1");
    const text = snapshotText(a, a.results[0]);
    const j = JSON.parse(text);
    expect(j).toMatchObject({ model_id: 7, block: { ijk: [13, 7, 1] }, estimation: { id: 9, method: "ok", changed: false } });
    expect(j.snapshot.samples).toBeDefined();
    expect(j.snapshot.estimation.params.search.passes).toHaveLength(3);
    expect(j.snapshot).toEqual(F.answers.pass_1.results[0].snapshot);
  });
  it("goes to the clipboard; there is none — the note says so", async () => {
    answer("pass_1");
    await app.registry.execute("estimate.inspect", { model_id: 7, row: 473 }, "user");
    const written: string[] = [];
    vi.stubGlobal("navigator", { clipboard: { writeText: async (t: string) => void written.push(t) } });
    expect(await copySnapshot()).toMatch(/^Снимок блока скопирован: \d+ символов/);
    expect(JSON.parse(written[0]).block.ijk).toEqual([13, 7, 1]);
    vi.stubGlobal("navigator", {});
    expect(await copySnapshot()).toBe("Снимок не скопирован: Буфер обмена недоступен");
    vi.unstubAllGlobals();
    resetInspect();
    expect(await copySnapshot()).toBe("Нет снимка: блок ещё не проверен");
  });
});

describe("the jump to the interval of 7.17", () => {
  const SETS = [{ id: 8, name: "Скважины", holes: 20 }] as never;
  it("a probe → the window «Данные по скважинам»: its hole, its table, its interval with a margin", () => {
    tablesStore.set({ sets: SETS });
    const r = ANS("pass_1").results[0];
    const c = r.candidates.find((x) => x.i === 173)!;
    expect(intervalTarget(r, c)).toEqual({ setId: 8, setName: "Скважины", tableHint: "3", holeHint: "H006", initial: { holes: ["H006"], from: "15", to: "27,5" } });
    const id = openInterval(r, c)!;
    const w = windowStore.get().windows.find((x) => x.id === id)!;
    expect(w).toMatchObject({ kind: "striplog", title: "Данные по скважинам · Скважины" });
    expect(w.props).toMatchObject({ setId: 8, setName: "Скважины", tableHint: "3", holeHint: "H006", initial: { holes: ["H006"], from: "15", to: "27,5" } });
    // the top of the hole: the margin does not go above the collar
    expect(intervalTarget(r, { hole: "H1", from: 0, to: 2 })!.initial).toMatchObject({ from: "0", to: "7" });
    // the set is not in the list (not read yet): a name by the number
    tablesStore.set({ sets: [] });
    expect(intervalTarget(r, c)!.setName).toBe("Набор №8");
    // a row without a set, a probe without a hole — nowhere to go
    expect(intervalTarget({ source: { set_id: null, table: null, field: null } }, c)).toBeNull();
    expect(intervalTarget(r, { ...c, hole: "" })).toBeNull();
  });
  it("the button of the row and the double click are in the table; the note says where it opened", async () => {
    tablesStore.set({ sets: SETS });
    answer("pass_1");
    await app.registry.execute("estimate.inspect", { model_id: 7, row: 473 }, "user");
    expect(render()).toContain('data-testid="inspect-interval-173"');
  });
});
