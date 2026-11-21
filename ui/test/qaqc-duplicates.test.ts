// «QAQC › Дубликаты» (ТЗ 8.4.2; ADR 0020 §4, §11): Т8.4-4 — the points of the ranked HARD equal the answer, the
// marks of the threshold of the type, of the share of the criterion and of the share reached (the boundary HARD = the
// threshold is on the soft side); Т8.4-3 — HARD and HRD of a pair on its card; the lines of the scatter (1:1, ±10 %,
// ±20 %, RMA) clipped to the square plot; the Thompson–Howard line (Т8.4-5 numbers) and the table P(c); «мало пар»;
// the counts of the pairs left out; the canvas layer from 5001 pairs, 100 000 pairs drawn ≤ 33 ms; the CSV equals the
// answer; one request per type, an overtaken one dropped. Fetch stub (the core of 14.12 is mocked).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeScale } from "../src/charts/core/axisScale";
import { csvCell } from "../src/charts/core/csv";
import { axisMap, rasterPoints, rasterSize } from "../src/charts/core/PointsCanvas";
import { PAIR_BOX, PairScatter } from "../src/charts/qaqc/PairScatter";
import {
  atMost, drawOrder, precisionTitle, rankedNote, excludedRows, excludedTotal, lineSegment, nearestPair, PAIR_CSV_HEADER, PAIR_PIXELS, pairCsv, pairFacts, pairLines, pairModel, pairPixels, rankedCsv, rankedModel, rankedPath, thCsv, thFormula, thModel, verdictTitle,
} from "../src/charts/qaqc/pairModel";
import { RankedHardChart } from "../src/charts/qaqc/RankedHardChart";
import { ThChart, ThTable } from "../src/charts/qaqc/ThChart";
import type { DuplicatesAnswer, PairPoints } from "../src/modules/qaqc/api";
import { int } from "../src/modules/qaqc/format";
import { answerKind, DupSummary, duplicatesNotes, loadDuplicates, thStateText, type DupAnswer } from "../src/modules/qaqc/DuplicatesTab";
import { DROPPED } from "../src/modules/qaqc/state";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const hardOf = (x1: number, x2: number) => (100 * Math.abs(x1 - x2)) / (x1 + x2);

// Т8.4-4: pairs (100 + h; 100 − h), h = 1…20 give HARD = h; ranked 100·i / n = 5·h
const H = Array.from({ length: 20 }, (_, i) => i + 1);
const POINTS: PairPoints = { row: H.map((h) => 200 + h), x1: H.map((h) => 100 + h), x2: H.map((h) => 100 - h), hard: H.map((h) => hardOf(100 + h, 100 - h)) };
const RANKED = { p: H.map((h) => (100 * h) / 20), hard: [...POINTS.hard] };
// Т8.4-5: 10 groups, |x1 − x2| = 0,1·m → a = 0, b = 0,1; P(c) = 20,9672 %
const K = 1 / (Math.SQRT2 * 0.6744897501960817);
// the block as the core answers it (`qaqc/calc_pairs.py`): `need`, `groups.n`, `precision[].q` beyond ADR 0020 §10
const TH = { state: "ok", group: 11, need: 50, a: 0, b: 0.1, k: K, groups: { m: H.slice(0, 10), med: H.slice(0, 10).map((m) => 0.1 * m), n: H.slice(0, 10).map(() => 11) }, precision: [{ c: 2, p: 200 * K * 0.1, q: 10 }, { c: 5, p: 200 * K * 0.1, q: 50 }, { c: 0.5, p: null, q: null }] };

function answer(over: Partial<DupAnswer> = {}): DupAnswer {
  return {
    kind: "DUP_P", element: "AU", unit: "г/т", counts: { rows: 500, filtered: 400, control: 60, unmapped: 0, missing: 2 }, limits: { hard_p: 10, hard_c: 20, hard_f: 30, hard_share: 90 } as DuplicatesAnswer["limits"], notes: [],
    pairs: 27, used: 20, excluded: { no_parent: 3, missing: 2, negative: 0, zero: 1, below: 1 }, hard_limit: 10, share_limit: 90, share10: 50, share20: 100, share30: 100, share_at_limit: 50, passed: false,
    stats: { mean1: 110.5, mean2: 89.5, pearson: -1, rma: { a: 200, b: -1 } }, ranked: RANKED, points: POINTS, th: TH, ...over,
  };
}

describe("Т8.4-4: the ranked HARD", () => {
  it("the points are the answer; the threshold, the share needed and the share reached are marked", () => {
    POINTS.hard.forEach((h, i) => expect(h).toBeCloseTo(H[i], 12));
    const m = rankedModel(RANKED, 10, 90, 50);
    expect(Array.from(m.xs)).toEqual(RANKED.p);
    expect(Array.from(m.ys)).toEqual(RANKED.hard);
    // HARD = 10 lies on the threshold: the soft side (ADR 0020 §4)
    expect(Array.from(m.over)).toEqual(H.map((h) => (h > 10 ? 1 : 0)));
    expect(atMost(10 + 1e-12, 10)).toBe(true);
    expect(atMost(10.001, 10)).toBe(false);
    const html = renderToStaticMarkup(createElement(RankedHardChart, { model: m, label: "Ранжированный HARD", note: "пар 20" }));
    expect(html.match(/data-rank=/g)).toHaveLength(20);
    expect(html).toContain('data-points="20"');
    expect(html).toMatch(/data-mark="limit" data-value="10"/);
    expect(html).toMatch(/data-mark="share" data-value="90"/);
    expect(html).toMatch(/data-mark="at" data-value="50" data-passed="0"/);
    expect(html).toContain("порог HARD 10 %");
    expect(html).toContain("нужно 90 %");
    expect(html).toContain("50,0 % пар ≤ 10 %");
    // passed when the share reached is the share needed
    const ok = renderToStaticMarkup(createElement(RankedHardChart, { model: rankedModel(RANKED, 20, 90, 100), label: "HARD" }));
    expect(ok).toMatch(/data-mark="at" data-value="100" data-passed="1"/);
    const sx = makeScale("linear", { min: 0, max: 100 }, 0, 100);
    const sy = makeScale("linear", m.yDomain, 100, 0);
    expect(rankedPath(m, sx, sy).match(/[ML]/g)).toHaveLength(20);
    const csv = rankedCsv(RANKED).split("\r\n");
    expect(csv).toHaveLength(21);
    csv.slice(1).forEach((line, i) => expect(line.split(";")).toEqual([csvCell(RANKED.p[i]), csvCell(RANKED.hard[i])]));
  });

  it("Т8.4-3 on the card: x1 = 1,0, x2 = 1,2 — HARD 9,09 %, HRD −9,09 %", () => {
    const p: PairPoints = { row: [7], x1: [1], x2: [1.2], hard: [hardOf(1, 1.2)] };
    expect(p.hard[0]).toBeCloseTo(9.090909, 6);
    expect(pairFacts(p, 0, "г/т", ["Исходная проба", "Дубликат"])).toEqual([
      ["Исходная проба", "1 г/т"], ["Дубликат", "1,2 г/т"], ["HARD", "9,09 %"], ["HRD", "-9,09 %"],
    ]);
  });
});

describe("the scatter of the pairs", () => {
  it("the lines 1:1, ±10 %, ±20 % and RMA inside the square plot; RMA on y = x is the diagonal", () => {
    const d = { min: 0, max: 200 };
    const lines = pairLines(d, { a: 0, b: 1 });
    expect(lines.map((l) => l.key)).toEqual(["p20", "p10", "one", "m10", "m20", "rma"]);
    const by = Object.fromEntries(lines.map((l) => [l.key, l.seg]));
    expect(by.one).toEqual([{ x: 0, y: 0 }, { x: 200, y: 200 }]);
    expect(by.rma).toEqual(by.one);
    expect(by.p20[1].x).toBeCloseTo(200 / 1.2, 12);
    expect(by.p20[1].y).toBeCloseTo(200, 12);
    expect(by.m20[1]).toEqual({ x: 200, y: 160 });
    expect(pairLines(d, { a: null, b: null }).map((l) => l.key)).not.toContain("rma");
    expect(lineSegment(500, 0, d)).toBeNull();
    expect(lineSegment(null, 1, d)).toBeNull();
    const m = pairModel(POINTS, 10, { a: 200, b: -1 });
    expect(m.domain.min).toBe(0);
    expect(m.domain.max).toBeGreaterThan(120);
    expect(m.byClass).toEqual([10, 10]);
    expect(Array.from(pairPixels(m))).toEqual(Array.from(m.over, (o) => PAIR_PIXELS[o]));
    const html = renderToStaticMarkup(createElement(PairScatter, { model: m, points: POINTS, label: "Рассеяние", xTitle: "Исходная", yTitle: "Дубликат", limit: 10, pearson: -1, rma: { a: 200, b: -1 }, picked: null }));
    for (const key of ["one", "p10", "m10", "p20", "m20", "rma"]) expect(html).toContain(`data-line="${key}"`);
    expect(html.match(/data-over=/g)).toHaveLength(20);
    expect(html).toContain("HARD ≤ 10 %: 10");
    expect(html).toContain("HARD &gt; 10 %: 10");
    expect(html).toContain("r = -1");
    expect(html).toContain("RMA: y = 200 + -1·x");
  });

  it("a click picks the nearest pair; a missing value is not drawn (never 0)", () => {
    const p: PairPoints = { row: [1, 2, 3], x1: [1, Number.NaN, 3], x2: [1.1, 2, 2.9], hard: [4.76, Number.NaN, 1.69] };
    const m = pairModel(p, 10, null);
    expect(m.n).toBe(2);
    expect(Array.from(m.idx)).toEqual([0, 2]);
    const sx = makeScale("linear", m.domain, PAIR_BOX.x0, PAIR_BOX.x1);
    const sy = makeScale("linear", m.domain, PAIR_BOX.y1, PAIR_BOX.y0);
    expect(nearestPair(m, sx, sy, sx.map(3)! + 2, sy.map(2.9)! - 2)).toBe(1);
    expect(nearestPair(m, sx, sy, PAIR_BOX.x1 - 1, PAIR_BOX.y0 + 1)).toBe(-1);
    const html = renderToStaticMarkup(createElement(PairScatter, { model: m, points: p, label: "Р", xTitle: "x1", yTitle: "x2", limit: 10, pearson: null, rma: null, picked: 2 }));
    expect(html).toContain('data-picked="2"');
    expect(html).toContain("r = —");
  });

  it("the pairs above the threshold are drawn over the norm: the SVG sorted, the canvas in two passes", () => {
    const m = pairModel(POINTS, 10, null);
    const o = drawOrder(m);
    expect(Array.from(o.low)).toEqual(H.slice(0, 10).map((h) => h - 1));
    expect(Array.from(o.high)).toEqual(H.slice(10).map((h) => h - 1));
    // a point above the threshold placed first in the answer is still drawn last
    const p: PairPoints = { row: [1, 2, 3], x1: [100, 1, 2], x2: [50, 1.01, 2.02], hard: [33.3, 0.5, 0.5] };
    const html = renderToStaticMarkup(createElement(PairScatter, { model: pairModel(p, 10, null), points: p, label: "Р", xTitle: "x1", yTitle: "x2", limit: 10, pearson: null, rma: null, picked: null }));
    expect([...html.matchAll(/data-over="(\d)"/g)].map((r) => r[1])).toEqual(["0", "0", "1"]);
    // the canvas: the failure colour wins where a norm point lies on the same pixel
    const same: PairPoints = { row: [1, 2], x1: [5, 5], x2: [5, 5], hard: [50, 0] };
    const sm = pairModel(same, 10, null);
    const so = drawOrder(sm);
    const sx = makeScale("linear", sm.domain, PAIR_BOX.x0, PAIR_BOX.x1);
    const sy = makeScale("linear", sm.domain, PAIR_BOX.y1, PAIR_BOX.y0);
    const { w, h } = rasterSize(PAIR_BOX);
    const buf = new Uint32Array(w * h);
    const mx = axisMap(sx, PAIR_BOX.x0);
    const my = axisMap(sy, PAIR_BOX.y0);
    rasterPoints(buf, w, h, sm.xs, sm.ys, mx, my, pairPixels(sm), 4, so.low);
    rasterPoints(buf, w, h, sm.xs, sm.ys, mx, my, pairPixels(sm), 4, so.high);
    expect(new Set(buf.filter((v) => v !== 0))).toEqual(new Set([PAIR_PIXELS[1]]));
  });

  it("the CSV of the pairs is the answer; more than 5000 pairs go to the canvas layer", () => {
    const lines = pairCsv(POINTS).split("\r\n");
    expect(lines[0]).toBe(PAIR_CSV_HEADER.map((h) => `"${h}"`).join(";"));
    lines.slice(1).forEach((line, i) => expect(line.split(";")).toEqual([POINTS.row[i], POINTS.x1[i], POINTS.x2[i], POINTS.hard[i]].map(csvCell)));
    const at = (n: number) => {
      const p = bigPairs(n);
      return renderToStaticMarkup(createElement(PairScatter, { model: pairModel(p, 10, null), points: p, label: "Р", xTitle: "x1", yTitle: "x2", limit: 10, pearson: null, rma: null, picked: null }));
    };
    expect(at(5000).match(/data-over=/g)).toHaveLength(5000);
    const raster = at(5001);
    expect(raster).toContain('data-raster="1"');
    expect(raster).not.toContain("data-over=");
  });
});

function bigPairs(n: number): PairPoints {
  const p: PairPoints = { row: [], x1: [], x2: [], hard: [] };
  for (let i = 0; i < n; i++) {
    const t = 1 + (i % 997) * 0.01;
    const x2 = t * (1 + 0.1 * Math.sin(i));
    p.row.push(i);
    p.x1.push(t);
    p.x2.push(x2);
    p.hard.push(hardOf(t, x2));
  }
  return p;
}

describe("Thompson–Howard, the summary, the notes", () => {
  it("Т8.4-5: the line a + b·m through the groups, a, b, K·b; the table P(c) with an empty value «—»", () => {
    const m = thModel(TH);
    expect(m.n).toBe(10);
    expect(m.line?.[0].y).toBeCloseTo(0.1 * m.xDomain.min, 12);
    expect(m.line?.[1].y).toBeCloseTo(0.1 * m.xDomain.max, 12);
    expect(TH.precision[0].p).toBeCloseTo(20.9672, 4);
    expect(thFormula(TH)).toContain("a = 0, b = 0,1");
    expect(thFormula({ a: null, b: null, k: null })).toContain("K·b = —");
    const html = renderToStaticMarkup(createElement(ThChart, { model: m, th: TH, label: "Т-Х", unit: "г/т" }));
    expect(html).toContain('data-line="th"');
    expect(html).toContain('data-points="10"');
    expect(html).toContain("Медиана |x1 − x2|, г/т");
    const table = renderToStaticMarkup(createElement(ThTable, { precision: TH.precision, unit: "г/т" }));
    expect(table).toContain("20,97 %");
    expect(table).toContain("<td>—</td>");
    expect(table).toContain("<th>P10 средних пар</th><td>2</td>");
    expect(table).toContain("<th>P50 средних пар</th>");
    expect(table).toContain("<th>задано</th><td>0,5</td>");
    expect([10, 90, null, undefined].map(precisionTitle)).toEqual(["P10 средних пар", "P90 средних пар", "задано", "задано"]);
    expect(html).toContain("группа 1 (пар 11)");
    expect(thModel({ a: null, b: null, groups: { m: [1, 2], med: [0.1, 0.2] } }).line).toBeNull();
    const thLines = thCsv(TH).split("\r\n");
    expect(thLines).toHaveLength(11);
    expect(thLines[0]).toBe('"m";"med";"n"');
    expect(thLines[1]).toBe("1;0.1;11");
  });

  it("«мало пар» — fewer than `th.need` = max(50, 2·group); `no_fit` — one mean of every group: no chart, the reason in words", () => {
    expect(thStateText(answer())).toBeNull();
    expect(thStateText(answer({ used: 30, th: { ...TH, state: "few_pairs", a: null, b: null, groups: { m: [], med: [], n: [] }, precision: [] } }))).toBe("Мало пар для графика Томпсона–Ховарта: в расчёте 30, нужно не меньше 50 (наибольшее из 50 и двух групп по 11 пар).");
    expect(thStateText({ used: 60, th: { state: "few_pairs", need: 62, group: 31 } })).toContain("в расчёте 60, нужно не меньше 62 (наибольшее из 50 и двух групп по 31 пар)");
    expect(thStateText({ used: 60, th: { state: "few_pairs", group: 31 } })).toContain("нужно не меньше 62"); // no `need`: the rule of the core
    expect(thStateText({ used: 120, th: { state: "no_fit", need: 50, group: 11 } })).toBe("График Томпсона–Ховарта не построен: у всех групп пар одно и то же среднее — прямую через них провести нельзя.");
  });

  it("the ranked HARD thinned by the core to 2000 points: the note tells both counts", () => {
    expect(rankedNote(20, 20)).toBe("пар 20");
    expect(rankedNote(4000, 2000)).toBe(`пар ${int(4000)}, показано ${int(2000)} (доли — по всем)`);
  });

  it("the summary: pairs, those left out by reason, the shares, the criterion of the type", () => {
    expect(excludedRows({ no_parent: 3, missing: 2, negative: 0, zero: 1, below: 1 })).toEqual([
      ["без исходной пробы", 3], ["нет значения", 2], ["отрицательные", 0], ["сумма равна 0", 1], ["среднее ниже k_д·DL", 1],
    ]);
    expect(excludedTotal({ no_parent: 3, missing: 2, negative: 0, zero: 1, below: 1 })).toBe(7);
    expect(excludedRows(null).every(([, n]) => n === null)).toBe(true);
    expect(excludedTotal(null)).toBeNull();
    expect([true, false, null].map(verdictTitle)).toEqual(["выполнен", "не выполнен", "—"]);
    // the summary is of the type the answer is of, not of the one chosen meanwhile
    expect(answerKind({ kind: "DUP_C" }, "DUP_F")).toBe("DUP_C");
    expect(answerKind({}, "DUP_F")).toBe("DUP_F");
    expect(answerKind({ kind: "UMP" }, "DUP_P")).toBe("DUP_P");
    const html = renderToStaticMarkup(createElement(DupSummary, { answer: answer(), kind: "DUP_P" }));
    for (const t of ["пульповые дубликаты (DUP_P)", "<th>Пар</th><td>27</td>", "<th>Отброшено</th><td>7</td>", "без исходной пробы</th><td>3</td>", "HARD ≤ 10 % (порог типа)</th><td>50,0 %</td>", "не меньше 90 % пар", "не выполнен"]) expect(html).toContain(t);
    expect(html).toContain("x̄ исходных</th><td>110,5 г/т");
    const empty = renderToStaticMarkup(createElement(DupSummary, { answer: answer({ share10: null, passed: null }), kind: "DUP_F" }));
    expect(empty).toContain("HARD ≤ 10 %</th><td>—</td>");
    expect(duplicatesNotes({ notes: ["n1"], thinned: { shown: 100_000, total: 250_000 } })).toEqual(["n1", `Показано ${int(100_000)} из ${int(250_000)} пар: все пары с HARD выше порога и равномерная выборка остальных; числа — по всем.`]);
    expect(duplicatesNotes({ notes: [], thinned: { shown: 10, total: 10 } })).toEqual([]);
  });
});

describe("speed and requests", () => {
  it("100 000 pairs: the model, the colours and the canvas layer ≤ 33 ms", () => {
    const p = bigPairs(100_000);
    const { w, h } = rasterSize(PAIR_BOX);
    const buf = new Uint32Array(w * h);
    const draw = () => {
      const m = pairModel(p, 10, { a: 0, b: 1 });
      const sx = makeScale("linear", m.domain, PAIR_BOX.x0, PAIR_BOX.x1);
      const sy = makeScale("linear", m.domain, PAIR_BOX.y1, PAIR_BOX.y0);
      buf.fill(0);
      return [m, rasterPoints(buf, w, h, m.xs, m.ys, axisMap(sx, PAIR_BOX.x0), axisMap(sy, PAIR_BOX.y0), pairPixels(m), 4)] as const;
    };
    draw(); // warm-up
    let best = Infinity;
    for (let k = 0; k < 3; k++) {
      const t0 = performance.now();
      const [m, drawn] = draw();
      best = Math.min(best, performance.now() - t0);
      expect(m.n).toBe(100_000);
      expect(drawn).toBe(100_000);
    }
    expect(best).toBeLessThanOrEqual(33);
  });

  let bodies: Record<string, unknown>[];
  beforeEach(() => {
    bodies = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        bodies.push({ url: String(input).replace(/^.*\/api\/v1/, ""), ...body });
        return json(answer({ hard_limit: body.kind === "DUP_F" ? 30 : 10 }));
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
  });

  it("one request per type with `kind`; an overtaken one is dropped", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1 } };
    const a = await loadDuplicates({ ...body, kind: "DUP_F" });
    expect(a !== DROPPED && a.hard_limit).toBe(30);
    expect(bodies).toEqual([{ url: "/qaqc/calc/duplicates", ...body, kind: "DUP_F" }]);
    const old = loadDuplicates({ ...body, kind: "DUP_C" });
    const now = loadDuplicates({ ...body, kind: "DUP_P" });
    expect(await old).toBe(DROPPED);
    expect(await now).not.toBe(DROPPED);
  });
});
