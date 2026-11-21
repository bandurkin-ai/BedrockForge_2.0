// «QAQC › Внешний контроль» (ТЗ 8.4.2; ADR 0020 §4, §11): empty t and p shown «—» with a note (every difference
// equal); the slope and the intercept with their bootstrap intervals; Т8.4-7 — equal values: bias 0, b = 1, a = 0,
// intervals [1; 1] and [0; 0], the RMA line is the diagonal; the choice of a group (wanted, else the first with pairs)
// with one request of the groups and one of the points, an overtaken load dropped; the CSV of the table equals the
// answer. Fetch stub (the core of 14.12 is mocked).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { csvCell } from "../src/charts/core/csv";
import { pairModel, rankedModel } from "../src/charts/qaqc/pairModel";
import { PairScatter } from "../src/charts/qaqc/PairScatter";
import type { ExternalAnswer, PairPoints } from "../src/modules/qaqc/api";
import { chooseExternal, EXTERNAL_CSV_HEADER, ExternalTable, externalCsv, externalLimits, groupTitle, loadExternal, withInterval, type ExtAnswer, type ExtGroup } from "../src/modules/qaqc/ExternalTab";
import { DROPPED } from "../src/modules/qaqc/state";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

const NOTE = "Все разности равны: t и p не определены.";
// Т8.4-7: the check lab equals the main one
const EQUAL: ExtGroup = {
  lab_main: "ALS", lab_check: "SGS", pairs: 40, used: 38, excluded: { no_parent: 1, missing: 1, negative: 0, zero: 0, below: 0 }, mean_main: 2.5, mean_check: 2.5, bias: 0, t: null, p: null, df: null, pearson: 1,
  rma: { a: 0, b: 1, a_ci: [0, 0], b_ci: [1, 1], n_boot: 1000, seed: 12345 }, share10: 100, share20: 100, share30: 100, share_at_limit: 100, passed: true, notes: [NOTE],
};
const OTHER: ExtGroup = {
  lab_main: "ALS", lab_check: null, pairs: 120, used: 117, excluded: { no_parent: 0, missing: 2, negative: 0, zero: 1, below: 0 }, mean_main: 1.04, mean_check: 0.98, bias: 6.12, t: 3.21, p: 0.0017, df: 116, pearson: 0.973,
  rma: { a: -0.012, b: 0.954, a_ci: [-0.031, 0.006], b_ci: [0.921, 0.988], n_boot: 1000, seed: 12345 }, share10: 81.2, share20: 96.6, share30: 99.1, share_at_limit: 81.2, passed: false, notes: [],
};
const EMPTY: ExtGroup = { ...OTHER, lab_main: null, lab_check: "SGS", pairs: 3, used: 0, mean_main: null, mean_check: null, bias: null, t: null, p: null, df: null, pearson: null, rma: { a: null, b: null, a_ci: null, b_ci: null, n_boot: 0, seed: 12345 }, share10: null, share20: null, share30: null, share_at_limit: null, passed: null };

describe("the table of the groups", () => {
  it("empty t and p are «—» with a note mark and the note below; the intervals of b and a", () => {
    const html = renderToStaticMarkup(createElement(ExternalTable, { groups: [OTHER, EQUAL], chosen: { lab_main: "ALS", lab_check: "SGS" }, limit: 10 }));
    expect(html).toContain('data-cell="t">3,21<');
    expect(html).toContain('data-cell="p">0,0017<');
    expect(html).toMatch(/title="Все разности равны: t и p не определены\." data-cell="t">—<sup>1<\/sup>/);
    expect(html).toMatch(/data-cell="p">—<sup>1<\/sup>/);
    expect(html).toContain('data-note="1">1. ALS → SGS: Все разности равны: t и p не определены.</p>');
    expect(html).toContain('data-cell="b">0,954 [0,921; 0,988]<');
    expect(html).toContain('data-cell="a">-0,012 [-0,031; 0,006]<');
    expect(html).toContain("<th>≤ 10 %</th>");
    expect(html).toContain("<th>≤ 30 %</th>");
    expect(html).toContain("<td>99,1 %</td>");
    expect(html).toContain("не выполнен");
    expect(html.match(/class="[^"]*chosen[^"]*"/g)).toHaveLength(1);
    expect(withInterval(null, null)).toBe("—");
    expect(withInterval(1, null)).toBe("1");
    expect(groupTitle(EMPTY)).toBe("без лаборатории → SGS");
  });

  it("Т8.4-7: equal values — bias 0, b = 1, a = 0, intervals [1; 1] and [0; 0]; the RMA line is the diagonal", () => {
    const html = renderToStaticMarkup(createElement(ExternalTable, { groups: [EQUAL], chosen: null, limit: 10 }));
    expect(html).toContain("<td>0,0 %</td>");
    expect(html).toContain('data-cell="b">1 [1; 1]<');
    expect(html).toContain('data-cell="a">0 [0; 0]<');
    const x = [1, 2, 2.5, 3, 4];
    const pts: PairPoints = { row: x.map((_, i) => i), x1: x, x2: [...x], hard: x.map(() => 0) };
    const m = pairModel(pts, 10, EQUAL.rma);
    const rma = m.lines.find((l) => l.key === "rma");
    expect(rma?.seg).toEqual(m.lines.find((l) => l.key === "one")?.seg);
    expect(m.byClass).toEqual([5, 0]);
    const svg = renderToStaticMarkup(createElement(PairScatter, { model: m, points: pts, label: "Внешний контроль", xTitle: "Основная", yTitle: "Контроль", limit: 10, pearson: 1, rma: EQUAL.rma, picked: null }));
    expect(svg).toContain("RMA: y = 0 + 1·x");
    expect(rankedModel({ p: [20, 40, 60, 80, 100], hard: [0, 0, 0, 0, 0] }, 10, 90, 100).over.every((o) => o === 0)).toBe(true);
  });

  it("the CSV of the table is the answer: every group, every number as answered, empty stays empty", () => {
    const lines = externalCsv([OTHER, EQUAL, EMPTY]).split("\r\n");
    expect(lines[0]).toBe(EXTERNAL_CSV_HEADER.map((h) => `"${h}"`).join(";"));
    expect(lines).toHaveLength(4);
    const want = (g: ExtGroup) =>
      [
        g.lab_main, g.lab_check, g.pairs, g.used, g.excluded.no_parent, g.excluded.missing, g.excluded.negative, g.excluded.zero, g.excluded.below, g.mean_main, g.mean_check, g.bias, g.t, g.p, g.df, g.pearson,
        g.rma.a, g.rma.b, g.rma.a_ci?.[0], g.rma.a_ci?.[1], g.rma.b_ci?.[0], g.rma.b_ci?.[1], g.rma.n_boot, g.rma.seed, g.share10, g.share20, g.share30, g.share_at_limit, g.passed === null ? null : g.passed ? 1 : 0, g.notes.join(" | ") || null,
      ].map(csvCell);
    [OTHER, EQUAL, EMPTY].forEach((g, i) => expect(lines[i + 1].split(";")).toEqual(want(g)));
    expect(lines[2].split(";")[12]).toBe(""); // t empty, not 0
    expect(lines[1].split(";")[1]).toBe(""); // no check lab
    expect(lines[1].split(";")[28]).toBe("0"); // not passed
    expect(lines[1].split(";")[26]).toBe("99.1"); // share30
  });
});

describe("the choice of a group and the requests", () => {
  it("the group wanted when it is there, else the first with pairs used, else the first", () => {
    expect(chooseExternal([EMPTY, OTHER, EQUAL], { lab_main: "ALS", lab_check: "SGS" })).toEqual({ lab_main: "ALS", lab_check: "SGS" });
    expect(chooseExternal([EMPTY, OTHER, EQUAL], { lab_main: "X", lab_check: "Y" })).toEqual({ lab_main: "ALS", lab_check: null });
    expect(chooseExternal([EMPTY], null)).toEqual({ lab_main: null, lab_check: "SGS" });
    expect(chooseExternal([], null)).toBeNull();
  });

  it("the threshold and the share of the criterion from the top of the answer, else from its limits", () => {
    const limits = { hard_ump: 12, hard_share: 85 } as ExternalAnswer["limits"];
    expect(externalLimits({ hard_limit: 10, share_limit: 90, limits })).toEqual({ limit: 10, share: 90 });
    expect(externalLimits({ limits })).toEqual({ limit: 12, share: 85 });
    expect(externalLimits(null)).toEqual({ limit: null, share: 90 });
  });

  let calls: { group?: unknown }[];
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { group?: unknown };
        calls.push({ group: body.group });
        const answer: ExtAnswer = { hard_limit: 10, share_limit: 90, element: "AU", unit: "г/т", counts: { rows: 900, filtered: 900, control: 160, unmapped: 0, missing: 0 }, limits: { hard_ump: 10, hard_share: 90 } as ExternalAnswer["limits"], notes: [], groups: [EMPTY, OTHER, EQUAL] };
        return json(body.group ? { ...answer, points: { row: [1], x1: [1], x2: [1.1], hard: [4.76] }, ranked: { p: [100], hard: [4.76] } } : answer);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
  });

  it("one request of the groups and one of the points: the first group with pairs, or the one wanted", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1 } };
    const first = await loadExternal(body, null);
    expect(calls).toEqual([{ group: undefined }, { group: { lab_main: "ALS", lab_check: null } }]);
    expect(first !== DROPPED && first.detail?.points?.x2).toEqual([1.1]);
    calls = [];
    const kept = await loadExternal(body, { lab_main: "ALS", lab_check: "SGS" });
    expect(calls).toHaveLength(2);
    expect(calls.filter((c) => c.group !== undefined)).toEqual([{ group: { lab_main: "ALS", lab_check: "SGS" } }]);
    expect(kept !== DROPPED && kept.group).toEqual({ lab_main: "ALS", lab_check: "SGS" });
  });

  it("an overtaken load is dropped", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1 } };
    const old = loadExternal(body, null);
    const now = loadExternal({ ...body, filter: { v: 1, labs: ["ALS"] } }, null);
    expect(await old).toBe(DROPPED);
    expect(await now).not.toBe(DROPPED);
  });
});
