// The cross-validation (Э18, 18.23): the readers and the pure words of the window on the real answer of the core
// (`core/tests/data/estimate/contract_answers.json`: `xval`, `xval_result`) — the table, the sort, the summary, the data of the charts,
// the colours of the errors in 3D.
import { describe, expect, it } from "vitest";
import { BRAND } from "../../src/charts/column/classes";
import { readRow, readSummary, readXvalPage, readXvalResult } from "../../src/modules/estimation/xvalApi";
import { atText, COLUMNS, cellText, errorCloud, errorColor, errorLimit, histogramMarks, histogramResult, nextSort, numberText, pageRows, pagesOf, resultLine, scatterResult, sortRows, staleText, statusLine, statusText, summaryFacts } from "../../src/modules/estimation/xvalModel";
import { A } from "./harness";
import { table, XV } from "./xvalKit";

const page = readXvalPage(XV)!;
const rows = table().map(readRow);
const flat = (s: string) => s.replace(/[\u00a0\u202f]/g, " ");

describe("the readers on the real answer", () => {
  it("the page: the row, what was left out, the time, the counts, the sample of the rows", () => {
    expect(page).toMatchObject({ estimation_id: 4, by: "hole", stale: false, total: 147, page: 1, pages: 19, size: 8 });
    expect(page.at).toBe(XV.at);
    expect(page.rows).toHaveLength(8);
    expect(page.rows[0]).toMatchObject({ i: 0, row_id: 6, hole: "С-00", from: 10, to: 12, x: 500000, y: 6700000, z: 289, pass: 1, ns: 24, status: 0 });
  });
  it("«true» of the core is `truth`; a number is a number, the error is the estimate minus the true value", () => {
    const r = page.rows[0];
    expect(r.truth).toBe(XV.rows[0].true);
    expect(r.est).toBe(XV.rows[0].est);
    expect(r.error).toBeCloseTo((r.est as number) - (r.truth as number), 12);
  });
  it("the summary and the histogram as the core gave them", () => {
    expect(page.summary).toEqual({ ...XV.summary });
    expect(page.summary.by_status).toEqual([147, 0, 0, 0]);
    expect(page.histogram.edges).toHaveLength(41);
    expect(page.histogram.counts).toHaveLength(40);
    expect(page.histogram.counts.reduce((a, b) => a + b, 0)).toBe(147);
  });
  it("the result of the job", () => {
    expect(readXvalResult(A.xval_result.body)).toMatchObject({ estimation_id: 4, by: "hole", n: 147 });
    expect(readXvalResult(A.xval_result.body)!.summary).toEqual(page.summary);
  });
  it("a gap stays null (never 0); an answer without the row is none", () => {
    const g = readRow({ i: 2, hole: "С-1", true: 1.5, est: null, error: null, kv: null, z_error: null, status: 2 });
    expect([g.est, g.error, g.kv, g.z_error, g.pass, g.ns, g.x]).toEqual([null, null, null, null, null, null, null]);
    expect(g.status).toBe(2);
    expect(readSummary({ n: 3, mean_ratio: null }).mean_ratio).toBeNull();
    expect(readXvalPage({})).toBeNull();
    expect(readXvalPage(null)).toBeNull();
  });
});

describe("the table", () => {
  it("the cells: a number with a decimal comma, a gap is «—», the status in words", () => {
    expect(cellText(rows[0], "truth")).toBe("1,386");
    expect(cellText(rows[0], "error")).toBe("-0,2063");
    expect(cellText(rows[0], "hole")).toBe("С-00");
    expect(cellText(rows[0], "i")).toBe("1");
    expect(flat(cellText(rows[0], "y"))).toBe("6 700 000");
    expect(cellText(rows[0], "status")).toBe("оценён");
    const last = rows[rows.length - 1];
    expect([cellText(last, "est"), cellText(last, "error"), cellText(last, "kv"), cellText(last, "z_error"), cellText(last, "pass")]).toEqual(["—", "—", "—", "—", "—"]);
    expect(cellText(last, "status")).toBe("мало проб");
    expect(statusText(9)).toBe("код 9");
    expect(numberText(0)).toBe("0");
    expect(numberText(null)).toBe("—");
  });
  it("every column of the table has a sort key and a hint", () => {
    expect(COLUMNS.map((c) => c.key)).toContain("z_error");
    for (const c of COLUMNS) expect(c.hint).toMatch(/[А-Яа-я]/);
  });
  it("the sort: a number by value, a name as a person reads it, a gap is last either way, the order of equals is kept", () => {
    const up = sortRows(rows, { key: "error", dir: "asc" });
    expect(up[0].error).toBe(Math.min(...rows.flatMap((r) => (r.error === null ? [] : [r.error]))));
    expect(up[up.length - 1].error).toBeNull();
    const down = sortRows(rows, { key: "error", dir: "desc" });
    expect(down[0].error).toBe(Math.max(...rows.flatMap((r) => (r.error === null ? [] : [r.error]))));
    expect(down[down.length - 1].error).toBeNull();
    const ids = sortRows(rows, { key: "hole", dir: "asc" }).map((r) => r.hole);
    expect(ids[0]).toBe("С-00");
    expect(ids[ids.length - 1]).toBe("С-99");
    const eq = sortRows(rows, { key: "status", dir: "asc" });
    expect(eq.filter((r) => r.status === 0).map((r) => r.i)).toEqual(rows.filter((r) => r.status === 0).map((r) => r.i));
    expect(sortRows(rows, null).map((r) => r.i)).toEqual(rows.map((r) => r.i));
    expect(rows[0].i).toBe(0); // the rows given are not touched
  });
  it("a click on a header: ascending, descending, none; another column — ascending", () => {
    const a = nextSort(null, "est");
    expect(a).toEqual({ key: "est", dir: "asc" });
    const d = nextSort(a, "est");
    expect(d).toEqual({ key: "est", dir: "desc" });
    expect(nextSort(d, "est")).toBeNull();
    expect(nextSort(d, "kv")).toEqual({ key: "kv", dir: "asc" });
  });
  it("the pages", () => {
    expect(pagesOf(147, 50)).toBe(3);
    expect(pagesOf(0, 50)).toBe(1);
    expect(pageRows(rows, 3, 50)).toHaveLength(47);
    expect(pageRows(rows, 1, 50)[0].i).toBe(0);
  });
});

describe("the summary", () => {
  const facts = summaryFacts(page.summary);
  const by = Object.fromEntries(facts.map((f) => [f.key, f]));
  it("every number of the core has its card with the meaning", () => {
    expect(facts.map((f) => f.key)).toEqual(["n", "mean_error", "std_error", "mae", "r", "slope", "mean_ratio", "n_kv_zero"]);
    expect(by.n.value).toBe("147");
    expect(by.mean_error.value).toBe("-0,01084");
    expect(by.std_error.value).toBe("0,6661");
    expect(by.mae.value).toBe("0,409");
    expect(by.r.value).toBe("0,5011");
    expect(by.slope.value).toBe("0,9273");
    expect(by.mean_ratio.value).toBe("0,7259");
    expect(by.n_kv_zero.value).toBe("0");
    for (const f of facts) expect(f.hint).toMatch(/[А-Яа-я]/);
  });
  it("IDW and NN have no variance of kriging: the ratio is «—» and the card says why", () => {
    const f = summaryFacts({ ...page.summary, mean_ratio: null, n_ratio: 0 }).find((x) => x.key === "mean_ratio")!;
    expect(f.value).toBe("—");
    expect(f.hint).toMatch(/IDW, NN/);
  });
  it("the rows outside the summary are named", () => {
    expect(summaryFacts({ ...page.summary, n_skipped: 3 })[0].hint).toMatch(/Ещё 3 строк не оценено/);
  });
  it("the statuses in words, the empty ones are not named", () => {
    expect(statusLine([147, 0, 0, 0])).toBe("оценён — 147");
    expect(flat(statusLine([1100, 60, 0, 5]))).toBe("оценён — 1 100, вне домена — 60, вырожденная система — 5");
    expect(statusLine([])).toBe("—");
  });
  it("the line of the command", () => {
    const line = resultLine("AU · OK · окисл", "hole", page.summary);
    expect(line).toContain("«AU · OK · окисл» по скважине");
    expect(line).toContain("проб 147");
    expect(line).toContain("средняя ошибка -0,01084");
    expect(line).toContain("наклон 0,9273");
    expect(resultLine("S", "sample", page.summary)).toContain("по пробе");
  });
});

describe("the words about an out-of-date result", () => {
  it("what changed, when, and the reason of the row with a capital letter; the advice", () => {
    const t = staleText(XV.at, "изменена таблица «Композиты»");
    expect(t).toMatch(/Параметры оценки или исходные данные изменились после расчёта от \d\d\.\d\d\.\d{4} \d\d:\d\d\./);
    expect(t).toContain("Изменена таблица «Композиты».");
    expect(t).toMatch(/Выполните проверку заново\.$/);
    expect(staleText(null, null)).toBe("Параметры оценки или исходные данные изменились после расчёта. Выполните проверку заново.");
    expect(atText("не дата")).toBe("");
    expect(atText(null)).toBe("");
  });
});

describe("the scatter plot «истинное — оценка»", () => {
  const r = scatterResult(rows, page.summary)!;
  it("x is the estimate, y is the true value; the sample that was not estimated has no point", () => {
    expect(r.n).toBe(146);
    expect(r.x).toHaveLength(146);
    expect(r.x![0]).toBe(rows[0].est);
    expect(r.y![0]).toBe(rows[0].truth);
    expect(r.mode).toBe("points");
    expect([r.x_field, r.y_field]).toEqual(["Оценка", "Истинное"]);
    expect(r.row).toBeUndefined(); // the points are not rows of a table of the holes: no selection of samples is made from here
    expect(r.counts).toMatchObject({ rows: 147, missing_x: 1, used: 146 });
  });
  it("the regression is the slope of the summary and passes through the means", () => {
    const fit = r.fits.ols!;
    expect(fit.b).toBe(page.summary.slope);
    expect((fit.a as number) + (fit.b as number) * (r.x_stats.mean as number)).toBeCloseTo(r.y_stats.mean as number, 10);
    expect(r.pearson).toBe(page.summary.r);
  });
  it("no slope — no line; no pairs — nothing", () => {
    expect(scatterResult(rows, { ...page.summary, slope: null })!.fits.ols).toBeNull();
    expect(scatterResult([], page.summary)).toBeNull();
  });
});

describe("the histogram of the errors", () => {
  const h = histogramResult(page.histogram, page.summary)!;
  it("the bins of the core: the counts, the share and the running share in %", () => {
    expect(h.bins.edges).toEqual(page.histogram.edges);
    expect(h.bins.counts).toEqual(page.histogram.counts);
    expect(h.bins.percent.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 9);
    expect(h.bins.cumulative_percent[h.bins.cumulative_percent.length - 1]).toBeCloseTo(100, 9);
    expect(h.bins.percent[0]).toBeCloseTo((1 / 147) * 100, 9);
    expect(h.stats.mean).toBe(page.summary.mean_error);
    expect(h.log).toBe(false);
  });
  it("the marks: zero and the mean error", () => {
    expect(histogramMarks(page.summary)).toEqual([{ value: 0, label: "0" }, { value: page.summary.mean_error, label: "средняя -0,01084", dashed: true }]);
    expect(histogramMarks({ ...page.summary, mean_error: null })).toEqual([{ value: 0, label: "0" }]);
  });
  it("an answer without bins draws nothing", () => {
    expect(histogramResult({ edges: [], counts: [] }, page.summary)).toBeNull();
    expect(histogramResult({ edges: [0, 1, 2], counts: [1] }, page.summary)).toBeNull();
  });
});

describe("the errors in 3D", () => {
  it("the full colour is the 95th percentile of the absolute errors; none of them zero — one", () => {
    const e = rows.flatMap((r) => (r.error === null ? [] : [r.error]));
    const abs = e.map(Math.abs).sort((a, b) => a - b);
    expect(errorLimit(e)).toBe(abs[Math.floor(0.95 * (abs.length - 1))]);
    expect(errorLimit([])).toBe(1);
    expect(errorLimit([0, 0])).toBe(1);
    expect(errorLimit([0, 0, -2])).toBe(2);
  });
  it("Cobalt below, Light Stone at zero, Copper Orange above; beyond the limit the colour stays", () => {
    const hex = (c: number[]) => `#${c.map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("")}`;
    expect(hex(errorColor(-3, 1))).toBe(BRAND.cobalt);
    expect(hex(errorColor(0, 1))).toBe(BRAND.stone);
    expect(hex(errorColor(1, 1))).toBe(BRAND.copper);
    expect(hex(errorColor(9, 1))).toBe(BRAND.copper);
    expect(errorColor(0.5, 1)).not.toEqual(errorColor(1, 1));
  });
  it("the cloud: the place of every sample with an error in float64, its colour; the one without an error is not in it", () => {
    const c = errorCloud(rows)!;
    expect(c.n).toBe(146);
    expect(c.xyz).toBeInstanceOf(Float64Array);
    expect(Array.from(c.xyz.subarray(0, 3))).toEqual([500000, 6700000, 289]);
    expect(c.rgb).toHaveLength(146 * 3);
    expect(c.limit).toBeGreaterThan(0);
    expect(errorCloud([])).toBeNull();
    expect(errorCloud([{ ...rows[0], x: null }])).toBeNull();
  });
});
