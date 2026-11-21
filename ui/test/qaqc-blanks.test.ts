// «QAQC › Бланки» and «Партии» (ТЗ 8.4.2; ADR 0020 §4, §10, §11): Т8.4-2 — the line k_б·DL and the colours of
// the statuses as the core answers them, «не оценён» with a colour of its own (never the norm), the legend; the
// carry-over pairs «предыдущая проба партии — бланк» (both values only), r and the CSV; the rows of the batches with
// their status and reasons; a click sets the filter to the batch (`qaqc.filter`); 20 thousand batches — a window of
// rows and pages of 200, never all at once; one request of the laboratories and one of the series. Fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BlankChart, CarryChart, legendStatuses } from "../src/charts/qaqc/BlankChart";
import { BLANK_CSV_HEADER, BLANK_STATUS_COLORS, blankBatchPoints, blankCsv, blankFacts, blankModel, blankStatus, blankStatusTitle, carryCsv, carryModel, carryTitle, ownLimits, type BlankSeries } from "../src/charts/qaqc/blankModel";
import { orderTitle } from "../src/charts/qaqc/controlModel";
import type { BatchRow, BlankGroup, BlanksAnswer, QaqcData } from "../src/modules/qaqc/api";
import { BATCH_PAGE, BatchPager, batchDates, batchesSummary, batchFilterArgs, batchReasons, batchStatusTitle, BatchWindow, OVERSCAN, pagesOf, ROW_H, rowWindow } from "../src/modules/qaqc/BatchesTab";
import { chooseLab, limitNote, loadBlanks } from "../src/modules/qaqc/BlanksTab";
import { bindQaqc } from "../src/modules/qaqc/bind";
import { BRAND } from "../src/charts/column/classes";
import { csvCell } from "../src/charts/core/csv";
import { STATUS_COLORS } from "../src/modules/qaqc/format";
import { SUBTABS } from "../src/modules/qaqc/QaqcPanel";
import { DROPPED, initialQaqc, qaqcStore } from "../src/modules/qaqc/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const LIMITS = { sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 };
const COUNTS = { rows: 900, filtered: 600, control: 120, unmapped: 0, missing: 1 };

// Т8.4-2: DL = 0,01, k_б = 5; бланки 0,04; 0,05; 0,06 → норма, норма, отказ (as the kernel `qaqc/blanks.py` answers);
// a fourth blank without a DL — «не оценён» (−1); a fifth without a value — counted by the core, not drawn
const T84_2: BlankSeries = {
  row: [10, 11, 12, 13, 14],
  date: ["2024-02-01", "2024-02-02", null, "2024-02-04", "2024-02-05"],
  seq: [1, 1, 1, 1, 1],
  batch: ["B1", "B2", "B3", "B4", "B4"],
  x: [0.04, 0.05, 0.06, 0.02, Number.NaN],
  status: [0, 0, 2, -1, -1],
  prev: [0.9, null, 1.4, 0.3, 0.2],
};
const LAB: BlankGroup = { lab: "ALS", n: 4, dl: 0.01, limit: 0.05, norm: 2, fail: 1, not_assessed: 1 };

describe("Т8.4-2: blanks against k_б·DL", () => {
  it("statuses: norm, norm, failure; −1 — «не оценён» in Slate Grey, never the norm", () => {
    expect(T84_2.status.map(blankStatusTitle)).toEqual(["норма", "норма", "отказ", "не оценён", "не оценён"]);
    expect([blankStatus(null), blankStatus(undefined), blankStatus(Number.NaN), blankStatus(-1), blankStatus(0), blankStatus(2)]).toEqual([3, 3, 3, 3, 0, 2]);
    expect(BLANK_STATUS_COLORS[3]).toBe(BRAND.slate);
    expect(BLANK_STATUS_COLORS[3]).not.toBe(STATUS_COLORS[0]);
    const m = blankModel(T84_2, LAB, 5, "order");
    expect(m.n).toBe(4);
    expect(Array.from(m.status)).toEqual([0, 0, 2, 3]);
    expect(m.byStatus).toEqual([2, 0, 1, 1]);
    expect(m.yDomain.min).toBeLessThanOrEqual(0);
  });

  it("the line k_б·DL at 0,05 with its label; none without a common DL", () => {
    const m = blankModel(T84_2, LAB, 5, "order");
    expect(m.lines).toEqual([{ key: "limit", y: 0.05, label: "5·DL" }]);
    const html = renderToStaticMarkup(createElement(BlankChart, { model: m, series: T84_2, label: "Бланки", yTitle: "AU, г/т", batch: null, picked: null }));
    expect(html).toMatch(/data-line="limit" data-y="0.05"/);
    expect(html).toContain("5·DL");
    expect(blankModel(T84_2, { limit: null }, 5, "order").lines).toEqual([]);
    expect(limitNote(LAB)).toBeNull();
    expect(limitNote({ ...LAB, limit: null, mixed: true })).toMatch(/разные DL/);
    expect(limitNote({ ...LAB, limit: null, dl: null, norm: 0, fail: 0, not_assessed: 4 })).toMatch(/не оценено бланков 4/);
  });

  it("the legend of the statuses: norm, failure, «не оценён» (a warning only when there is one); colours of the points", () => {
    const m = blankModel(T84_2, LAB, 5, "order");
    const html = renderToStaticMarkup(createElement(BlankChart, { model: m, series: T84_2, label: "Бланки", yTitle: "AU, г/т", batch: null, picked: null }));
    expect(html).toContain("норма: 2");
    expect(html).toContain("отказ: 1");
    expect(html).toContain("не оценён: 1");
    expect(html).not.toContain("предупреждение:");
    expect(legendStatuses([1, 2, 0, 0])).toEqual([0, 1, 2, 3]);
    const fills = [...html.matchAll(/<circle[^>]*fill="(#[0-9a-f]{6})"[^>]*data-status="(\d)"/g)].map((r) => [r[1], Number(r[2])]);
    expect(fills).toEqual([[STATUS_COLORS[0], 0], [STATUS_COLORS[0], 0], [BRAND.slate, 3], [STATUS_COLORS[2], 2]]); // failures drawn last
  });

  it("by date: a blank without a date is counted, not drawn; the batch shown is ringed; the card", () => {
    const m = blankModel(T84_2, LAB, 5, "date");
    expect(m.n).toBe(3);
    expect(m.noDate).toBe(1);
    expect(blankBatchPoints(m, T84_2, "B4")).toEqual([2]);
    const html = renderToStaticMarkup(createElement(BlankChart, { model: m, series: T84_2, label: "Бланки", yTitle: "AU, г/т", batch: "B4", picked: 3 }));
    expect(html).toContain('data-batch="1"');
    expect(html).toContain('data-picked="3"');
    const facts = new Map(blankFacts(T84_2, 2, "г/т", LAB));
    expect(facts.get("Статус")).toBe("отказ");
    expect(facts.get("Порог k_б·DL")).toBe("0,05 г/т");
    expect(facts.get("Дата")).toBe("—");
    expect(new Map(blankFacts(T84_2, 1, "г/т", LAB)).get("Предыдущая проба партии")).toBe("—");
  });

  it("the CSV of the series equals the answer: a missing value is empty, never 0", () => {
    const lines = blankCsv(T84_2).trim().split(/\r?\n/);
    expect(lines[0]).toBe(BLANK_CSV_HEADER.map((h) => csvCell(h)).join(";"));
    expect(lines).toHaveLength(6);
    expect(lines[2].split(";")[6]).toBe("");
    expect(lines[5].split(";")[4]).toBe("");
  });
});

describe("a laboratory with different DL (`mixed`): the threshold of each blank", () => {
  const MIXED: BlankSeries = { ...T84_2, limit: [0.05, 0.05, 0.1, null, null] };
  const LABM: BlankGroup = { ...LAB, dl: null, limit: null, mixed: true };
  it("a dash at each own threshold, the column `limit` in the CSV, the DL of the sample = limit / k_б", () => {
    const m = blankModel(MIXED, LABM, 5, "order");
    expect(m.lines).toEqual([]);
    expect(Array.from(ownLimits(m, MIXED)!)).toEqual([0.05, 0.05, 0.1, Number.NaN]);
    expect(ownLimits(m, T84_2)).toBeNull();
    const html = renderToStaticMarkup(createElement(BlankChart, { model: m, series: MIXED, label: "Бланки", yTitle: "AU", batch: null, picked: null }));
    expect(html.match(/data-own-limit=/g)).toHaveLength(3);
    const csv = blankCsv(MIXED).trim().split(/\r?\n/);
    expect(csv[0].endsWith(csvCell("limit"))).toBe(true);
    expect(csv[3].split(";")[7]).toBe("0.1");
    const facts = new Map(blankFacts(MIXED, 2, "ppm", LABM, 5));
    expect(facts.get("Порог k_б·DL")).toBe("0,1 ppm");
    expect(facts.get("DL")).toBe("0,02 ppm");
    expect(new Map(blankFacts(MIXED, 3, "ppm", LABM, 5)).get("DL")).toBe("—");
  });

  it("the axis «по порядку»: the position of the core when it is given; a thinned series is titled so", () => {
    const m = blankModel({ ...T84_2, pos: [1, 40, 41, 90, 91] }, LAB, 5, "order");
    expect(Array.from(m.xs)).toEqual([1, 40, 41, 90]);
    expect(orderTitle(null, "бланка")).toBe("Номер бланка в ряду");
    expect(orderTitle({ shown: 10, total: 10 })).toBe("Номер пробы в ряду");
    expect(orderTitle({ shown: 10, total: 900 })).toBe("Номер в показанной выборке");
    const html = renderToStaticMarkup(createElement(BlankChart, { model: m, series: T84_2, label: "Бланки", yTitle: "AU", batch: null, picked: null, orderTitle: orderTitle({ shown: 4, total: 9 }) }));
    expect(html).toContain("Номер в показанной выборке");
  });
});

describe("carry-over: «предыдущая проба партии — бланк»", () => {
  it("pairs only where both have a value; r and the number of pairs in the title; the CSV of the pairs", () => {
    const c = carryModel(T84_2);
    expect(Array.from(c.idx)).toEqual([0, 2, 3]);
    expect(Array.from(c.xs)).toEqual([0.9, 1.4, 0.3]);
    expect(Array.from(c.ys)).toEqual([0.04, 0.06, 0.02]);
    expect(Array.from(c.status)).toEqual([0, 2, 3]);
    expect(carryTitle({ n: 3, pearson: 0.9819805 }, c.n)).toBe("r = 0,982, пар 3");
    expect(carryTitle({ n: 2, pearson: null }, c.n)).toBe("r не определён, пар 2");
    expect(carryTitle(undefined, c.n)).toBe("r не определён, пар 3");
    const csv = carryCsv(T84_2, c).trim().split(/\r?\n/);
    expect(csv).toHaveLength(4);
    expect(csv[0]).toBe(["row", "batch", "prev", "x", "status"].map((h) => csvCell(h)).join(";"));
    const html = renderToStaticMarkup(createElement(CarryChart, { model: c, series: T84_2, lines: [{ key: "limit", y: 0.05, label: "5·DL" }], label: "Перенос", xTitle: "Предыдущая", yTitle: "Бланк", batch: null, picked: null }));
    expect(html).toContain('data-points="3"');
    expect(html).toContain('data-line="limit"');
  });
});

describe("loading the blanks", () => {
  let calls: { group?: unknown }[];
  const groups: BlankGroup[] = [{ lab: null, n: 3, dl: null, limit: null, norm: 0, fail: 0, not_assessed: 3 }, LAB, { ...LAB, lab: "SGS" }];
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { group?: { lab: string | null } };
        calls.push({ group: body.group });
        const answer: BlanksAnswer = { element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], groups };
        return json(body.group ? { ...answer, series: T84_2, carryover: { n: 3, pearson: 0.98 } } : answer);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
  });

  it("one request of the laboratories and one of the series: the laboratory kept, or the first with assessed blanks", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1 } };
    const first = await loadBlanks(body, null);
    expect(calls).toEqual([{ group: undefined }, { group: { lab: "ALS" } }]);
    expect(first !== DROPPED && first.series?.carryover).toEqual({ n: 3, pearson: 0.98 });
    calls = [];
    await loadBlanks(body, { lab: "SGS" });
    expect(calls).toHaveLength(2);
    expect(calls.filter((c) => c.group !== undefined)).toEqual([{ group: { lab: "SGS" } }]);
    expect(chooseLab(groups, { lab: null })).toEqual({ lab: null });
    expect(chooseLab([], null)).toBeNull();
  });
});

const row = (i: number, extra: Partial<BatchRow> = {}): BatchRow => ({ lab: i % 2 ? "SGS" : "ALS", batch: `B${i}`, date_from: "2024-03-01", date_to: "2024-03-02", n: 40, crm: 2, blanks: 1, crm_fail: 0, blank_fail: 0, status: 0, ...extra });

describe("«Партии»: rows, status and reasons", () => {
  it("the status and why: failed CRM, failed blanks, not assessed — never the norm", () => {
    expect([2, 0, -1, null].map((s) => batchStatusTitle(s as number))).toEqual(["отказ", "норма", "не оценена", "не оценена"]);
    expect(batchReasons(row(1, { crm_fail: 1, status: 2 }))).toBe("отказ CRM: 1 из 2");
    expect(batchReasons(row(1, { crm_fail: 2, blank_fail: 1, status: 2 }))).toBe("отказ CRM: 2 из 2; отказ бланка: 1 из 1");
    expect(batchReasons(row(1, { blanks: 3, blank_fail: 2, status: 2 }))).toBe("отказ бланков: 2 из 3");
    expect(batchReasons(row(1, { status: -1 }))).toMatch(/^CRM и бланки не оценены/);
    expect(batchReasons(row(1, { crm: 0, blanks: 0, status: -1 }))).toBe("нет CRM и бланков");
    expect(batchReasons(row(1))).toBe("");
    expect(batchDates(row(1))).toBe("01.03.2024 — 02.03.2024");
    expect(batchDates(row(1, { date_to: "2024-03-01" }))).toBe("01.03.2024");
    expect(batchDates(row(1, { date_from: null, date_to: null }))).toBe("—");
    expect(batchesSummary({ element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], total: 200, failed: 5, no_batch: 7, not_assessed: 3 })).toEqual(["Партий 200", "отказ 5 (2,5 %)", "не оценены 3", "записей без партии 7"]);
  });

  it("the window draws its rows only: status and reasons in them, a row not read yet — «…», the batch shown chosen", () => {
    const rows = new Map([[100, row(100, { crm_fail: 1, status: 2 })], [101, row(101, { status: -1 })]]);
    const html = renderToStaticMarkup(createElement(BatchWindow, { total: 20_000, from: 100, to: 103, row: (i) => rows.get(i), chosen: { lab: "ALS", batch: "B100" }, onPick: () => undefined }));
    expect(html).toContain(`height:${20_000 * ROW_H}px`);
    expect(html).toContain(`top:${100 * ROW_H}px`);
    expect(html).toContain('data-rows="3"');
    expect(html.match(/role="row"/g)).toHaveLength(3);
    expect(html).toContain("отказ CRM: 1 из 2");
    expect(html).toContain("не оценена");
    expect(html).toMatch(/class="[^"]*chosen[^"]*"[^>]*data-batch="B100"/);
    expect(html).toContain("…");
  });

  it("rowWindow and pagesOf: the sight with overscan; the pages of its rows", () => {
    expect(rowWindow(0, 15 * ROW_H, 20_000)).toEqual({ from: 0, to: 15 + OVERSCAN });
    expect(rowWindow(10_000 * ROW_H, 15 * ROW_H, 20_000)).toEqual({ from: 10_000 - OVERSCAN, to: 10_015 + OVERSCAN });
    expect(rowWindow(1e9, 300, 20_000).to).toBe(20_000);
    expect(rowWindow(0, 300, 0)).toEqual({ from: 0, to: 0 });
    expect(pagesOf(0, 23)).toEqual([0]);
    expect(pagesOf(190, 230)).toEqual([0, 1]);
    expect(pagesOf(5, 5)).toEqual([]);
  });
});

describe("«Партии»: a click sets the filter to the batch", () => {
  const DATA: QaqcData = {
    id: 4, name: "Контроль", version: 1, rows: 900, elements: [{ name: "AU", unit: "г/т" }], fields: [], labs: ["ALS", "SGS"], batches: 2, date_from: null, date_to: null,
    type_codes: [], source: null, stale: false, sha256: "ab", counts: {},
  };
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => (String(input).endsWith("/qaqc/data") ? json([DATA]) : json({ element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [] }))),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    qaqcStore.set(initialQaqc());
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
    qaqcStore.set(initialQaqc());
  });

  it("the laboratory and the batch (a name with a comma too) through `qaqc.filter`; without a laboratory — the batch only", async () => {
    const app = createApp({ persist: false });
    bindQaqc(app);
    app.store.set({ projectName: "Проект" });
    qaqcStore.set({ datas: [DATA], filter: { ...initialQaqc().filter, crm: ["OREAS 45"] } });
    const r = await app.registry.execute("qaqc.filter", batchFilterArgs({ lab: "ALS", batch: "B-7, повтор" }));
    expect(r.ok).toBe(true);
    expect(qaqcStore.get().filter).toMatchObject({ labs: ["ALS"], batches: ["B-7, повтор"], crm: ["OREAS 45"] });
    expect(batchFilterArgs({ lab: null, batch: "B9" })).toEqual({ batches: '["B9"]' });
  });

  it("the subtabs «Бланки» and «Партии» are drawn by their own components", () => {
    expect(SUBTABS.blanks).toBeTypeOf("function");
    expect(SUBTABS.batches).toBeTypeOf("function");
  });
});

describe("20 thousand batches: a window of rows, pages of 200, never all at once", () => {
  let asked: { offset?: number; limit?: number }[];
  const TOTAL = 20_000;
  beforeEach(() => {
    asked = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { offset?: number; limit?: number };
        asked.push({ offset: body.offset, limit: body.limit });
        const from = body.offset ?? 0;
        const n = Math.max(0, Math.min(body.limit ?? 5000, TOTAL - from));
        return json({ element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], total: TOTAL, failed: 120, no_batch: 9, batches: Array.from({ length: n }, (_, k) => row(from + k)) });
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
  });

  it("the first sight — one page; a scroll to the middle — its page only; a page is asked once", async () => {
    const changed = vi.fn();
    const pager = new BatchPager({ data_id: 4, element: "AU", filter: { v: 1 } }, changed);
    const first = rowWindow(0, 15 * ROW_H, pager.total);
    await pager.ensure(first.from, first.to);
    expect(asked).toEqual([{ offset: 0, limit: BATCH_PAGE }]);
    expect(pager.total).toBe(TOTAL);
    expect(pager.head?.failed).toBe(120);
    const mid = rowWindow(10_000 * ROW_H, 15 * ROW_H, pager.total);
    await Promise.all([pager.ensure(mid.from, mid.to), pager.ensure(mid.from, mid.to)]);
    expect(asked).toEqual([{ offset: 0, limit: BATCH_PAGE }, { offset: 9_800, limit: BATCH_PAGE }, { offset: 10_000, limit: BATCH_PAGE }]);
    expect(pager.row(10_005)?.batch).toBe("B10005");
    expect(pager.row(5_000)).toBeUndefined();
    await pager.ensure(mid.from, mid.to);
    expect(pager.requests).toBe(3);
    expect(asked.every((a) => (a.limit ?? Infinity) <= 5000)).toBe(true);
    expect(changed).toHaveBeenCalledTimes(3);
    const html = renderToStaticMarkup(createElement(BatchWindow, { total: pager.total, from: mid.from, to: mid.to, row: (i) => pager.row(i), chosen: null, onPick: () => undefined }));
    expect(html.match(/role="row"/g)?.length).toBe(mid.to - mid.from);
    expect(mid.to - mid.from).toBeLessThan(40);
  });

  it("a new calculation drops the pages of the old one on their way", async () => {
    const changed = vi.fn();
    const old = new BatchPager({ data_id: 4, element: "AU" }, changed);
    const going = old.ensure(0, 20);
    old.dispose();
    await going;
    expect(old.head).toBeNull();
    expect(changed).not.toHaveBeenCalled();
  });
});
