// The control chart of «QAQC › Стандарты» (ТЗ 8.4.2; ADR 0020 §4, §11): Т8.4-1 — colours and titles of the
// statuses of the series as the core answers them; the lines μ, ±2σ, ±3σ, x̄ and the moving average with its gap;
// the axes «по дате» (a point without a date counted, not drawn) and «по порядку»; a click picks the nearest point,
// opens its card and shows its batch; the canvas layer from 5001 points; the CSV of the series equals the answer;
// 100 000 points drawn ≤ 33 ms; a new filter — one request of the groups and one of the series. Fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeScale } from "../src/charts/core/axisScale";
import { csvCell } from "../src/charts/core/csv";
import { plotBox } from "../src/charts/core/PlotFrame";
import { axisMap, rasterPoints, rasterSize } from "../src/charts/core/PointsCanvas";
import { CONTROL_BOX, ControlChart } from "../src/charts/qaqc/ControlChart";
import { batchPoints, controlCsv, controlLines, controlModel, CSV_HEADER, dateTicks, maPath, markedPoints, nearestPoint, pointFacts, pointLabel, statusPixels, STATUS_PIXELS } from "../src/charts/qaqc/controlModel";
import type { StandardSeries, StandardsAnswer } from "../src/modules/qaqc/api";
import { STATUS_COLORS } from "../src/modules/qaqc/format";
import { fieldText, recordFacts } from "../src/modules/qaqc/SampleCard";
import { chooseGroup, loadStandards } from "../src/modules/qaqc/StandardsTab";
import { DROPPED, initialQaqc, pickSample, qaqcStore } from "../src/modules/qaqc/state";
import { setCoreTarget } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// Т8.4-1: μ = 1,00, σ = 0,05; 1,11; 1,12; 0,84; 1,16; 0,95; 9 × 1,01 → предупреждение; отказ (две подряд); отказ;
// отказ; норма; «смещение» (серия) на 9-й пробе 1,01 — as the core of 14.2 answers them (1,16 also marked as a
// possible swap, bit 4, to check the mark)
const X = [1.11, 1.12, 0.84, 1.16, 0.95, ...Array<number>(9).fill(1.01)];
const MA = X.map((_, i) => (i < 9 ? null : X.slice(i - 9, i + 1).reduce((a, b) => a + b, 0) / 10));
const T84_1: StandardSeries = {
  row: X.map((_, i) => 100 + i),
  date: X.map((_, i) => (i === 6 ? null : `2024-01-${String(i + 1).padStart(2, "0")}`)),
  seq: X.map((_, i) => i + 1),
  batch: X.map((_, i) => (i < 5 ? "B1" : "B2")),
  x: X,
  z: X.map((x) => (x - 1) / 0.05),
  status: [1, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
  rule: [0, 1, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
  ma: MA,
  swap: [null, null, null, ["OREAS 47"], null, null, null, null, null, null, null, null, null, null],
};
const GROUP = { crm: "OREAS 45", lab: "ALS", state: "ok", n: 14, mu: 1, sd: 0.05, unit: "г/т", mean: X.reduce((a, b) => a + b, 0) / X.length, std: 0.08, bias: 2, bias_class: 0, norm: 9, warn: 2, fail: 3, pair_fails: 1, run_flags: 1, swaps: 1 };

function big(n: number): StandardSeries {
  const s: StandardSeries = { row: [], date: [], seq: [], batch: [], x: [], z: [], status: [], rule: [], ma: [], swap: [] };
  for (let i = 0; i < n; i++) {
    const x = 1 + 0.05 * Math.sin(i * 0.7) * 3.5;
    const z = (x - 1) / 0.05;
    s.row.push(i);
    s.date.push(Date.UTC(2023, 0, 1) + i * 600_000);
    s.seq.push(i % 40);
    s.batch.push(`B${Math.floor(i / 40)}`);
    s.x.push(x);
    s.z.push(z);
    s.status.push(Math.abs(z) > 3 ? 2 : Math.abs(z) > 2 ? 1 : 0);
    s.rule.push(0);
    s.ma.push(i < 9 ? null : x);
    s.swap.push(null);
  }
  return s;
}

describe("Т8.4-1: statuses of the series on the chart", () => {
  it("titles and colours: warning; failure (two in a row); failure; failure; norm; the 9th 1,01 — warning by a run", () => {
    expect(X.map((_, i) => pointLabel(T84_1, i))).toEqual([
      "предупреждение", "отказ (две подряд за 2σ)", "отказ", "отказ (возможна подмена)", "норма", "норма", "норма", "норма", "норма", "норма", "норма", "норма", "норма", "предупреждение (серия по одну сторону μ)",
    ]);
    const m = controlModel(T84_1, GROUP, 2, 3, "order");
    expect(Array.from(m.status)).toEqual(T84_1.status);
    expect(m.byStatus).toEqual([9, 2, 3]);
    expect(Array.from(statusPixels(m))).toEqual(T84_1.status.map((s) => STATUS_PIXELS[s]));
    const html = renderToStaticMarkup(createElement(ControlChart, { model: m, series: T84_1, label: "Карта", yTitle: "AU, г/т", batch: null, picked: null }));
    const fills = [...html.matchAll(/<circle[^>]*fill="(#[0-9a-f]{6})"[^>]*data-status="(\d)"/g)].map((r) => [r[1], Number(r[2])]);
    expect(fills).toEqual(T84_1.status.map((s) => [STATUS_COLORS[s], s]));
    expect(html).toContain("норма: 9");
    expect(html).toContain("предупреждение: 2");
    expect(html).toContain("отказ: 3");
    expect(markedPoints(m, T84_1)).toEqual({ run: [13], swap: [3] });
    expect(html.match(/data-mark="run"/g)).toHaveLength(1);
    expect(html.match(/data-mark="swap"/g)).toHaveLength(1);
  });

  it("the lines μ, ±2σ, ±3σ and x̄; the moving average starts at the 10th point", () => {
    const lines = controlLines(GROUP, 2, 3);
    expect(lines.map((l) => l.key)).toEqual(["fail_hi", "warn_hi", "mu", "warn_lo", "fail_lo", "mean"]);
    expect(lines.map((l) => l.label)).toEqual(["+3σ", "+2σ", "μ", "−2σ", "−3σ", "x̄"]);
    [1.15, 1.1, 1, 0.9, 0.85, GROUP.mean].forEach((y, i) => expect(lines[i].y).toBeCloseTo(y, 12));
    expect(controlLines({ mu: null, sd: null, mean: 1.2 }, 2, 3).map((l) => l.key)).toEqual(["mean"]); // no reference: no bands
    const m = controlModel(T84_1, GROUP, 2, 3, "order");
    expect(m.yDomain.min).toBeLessThan(0.84);
    expect(m.yDomain.max).toBeGreaterThan(1.16);
    const sx = makeScale("linear", m.xDomain, CONTROL_BOX.x0, CONTROL_BOX.x1);
    const sy = makeScale("linear", m.yDomain, CONTROL_BOX.y1, CONTROL_BOX.y0);
    const path = maPath(m, sx, sy);
    expect(path.match(/M/g)).toHaveLength(1);
    expect(path.match(/[ML]/g)).toHaveLength(5); // points 10…14
    const html = renderToStaticMarkup(createElement(ControlChart, { model: m, series: T84_1, label: "Карта", yTitle: "AU", batch: null, picked: null }));
    for (const key of ["fail_hi", "warn_hi", "mu", "warn_lo", "fail_lo", "mean", "ma"]) expect(html).toContain(`data-line="${key}"`);
  });

  it("axes: by date a point without a date is counted, not drawn; by order every point is 1…n", () => {
    const byDate = controlModel(T84_1, GROUP, 2, 3, "date");
    expect(byDate.n).toBe(13);
    expect(byDate.noDate).toBe(1);
    expect(byDate.xs[0]).toBe(Date.UTC(2024, 0, 1));
    expect(Array.from(byDate.idx)).not.toContain(6);
    const byOrder = controlModel(T84_1, GROUP, 2, 3, "order");
    expect(Array.from(byOrder.xs)).toEqual(X.map((_, i) => i + 1));
    expect(byOrder.noDate).toBe(0);
    expect(dateTicks(byDate.xDomain).map((t) => t.label).slice(0, 3)).toEqual(["01.01", "03.01", "05.01"]);
    expect(dateTicks({ min: Date.UTC(2023, 10, 15), max: Date.UTC(2024, 5, 10) }).map((t) => t.label)).toEqual(["12.2023", "01.2024", "02.2024", "03.2024", "04.2024", "05.2024", "06.2024"]);
    expect(dateTicks({ min: Date.UTC(2015, 3, 1), max: Date.UTC(2024, 3, 1) }).map((t) => t.label)).toEqual(["2016", "2018", "2020", "2022", "2024"]);
    const html = renderToStaticMarkup(createElement(ControlChart, { model: byDate, series: T84_1, label: "Карта", yTitle: "AU", batch: null, picked: null }));
    expect(html).toContain(">Дата</text>");
    expect(html).toContain(">01.01</text>");
  });
});

describe("a click, the batch, the canvas layer, the CSV", () => {
  afterEach(() => qaqcStore.set(initialQaqc()));

  it("a click picks the nearest point: its card facts and its batch ringed on the chart", () => {
    const m = controlModel(T84_1, GROUP, 2, 3, "order");
    const sx = makeScale("linear", m.xDomain, CONTROL_BOX.x0, CONTROL_BOX.x1);
    const sy = makeScale("linear", m.yDomain, CONTROL_BOX.y1, CONTROL_BOX.y0);
    const j = nearestPoint(m, sx, sy, sx.map(4)! + 3, sy.map(1.16)! - 2);
    expect(j).toBe(3);
    expect(nearestPoint(m, sx, sy, CONTROL_BOX.x0 + 1, CONTROL_BOX.y0 + 1)).toBe(-1);
    const i = m.idx[j];
    const facts = pointFacts(T84_1, i, "г/т");
    expect(Object.fromEntries(facts)).toMatchObject({ Дата: "04.01.2024", Партия: "B1", Значение: "1,16 г/т", z: "3,2", Статус: "отказ (возможна подмена)", "Возможна подмена": "OREAS 47" });
    pickSample({ row: T84_1.row[i], section: "standards", lab: "ALS", batch: T84_1.batch[i], facts });
    expect(qaqcStore.get().highlight).toEqual({ lab: "ALS", batch: "B1" });
    expect(qaqcStore.get().sample?.row).toBe(103);
    expect(batchPoints(m, T84_1, "B1")).toEqual([0, 1, 2, 3, 4]);
    const html = renderToStaticMarkup(createElement(ControlChart, { model: m, series: T84_1, label: "Карта", yTitle: "AU", batch: "B1", picked: i }));
    expect(html).toContain('data-batch="5"');
    expect(html).toContain('data-picked="3"');
    pickSample(null);
    expect(qaqcStore.get().sample).toBeNull();
  });

  it("more than 5000 points go to the canvas layer, up to 5000 stay SVG circles", () => {
    const at = (n: number) => {
      const s = big(n);
      return renderToStaticMarkup(createElement(ControlChart, { model: controlModel(s, GROUP, 2, 3, "date"), series: s, label: "Карта", yTitle: "AU", batch: null, picked: null }));
    };
    const svg = at(5000);
    expect(svg).not.toContain('data-raster="1"');
    expect(svg.match(/data-status=/g)).toHaveLength(5000);
    const raster = at(5001);
    expect(raster).toContain('data-raster="1"');
    expect(raster).toContain('data-points="5001"');
    expect(raster).not.toContain("data-status=");
  });

  it("the CSV of the series is the answer: every point, in its order, the cells as answered", () => {
    const lines = controlCsv(T84_1).split("\r\n");
    expect(lines[0]).toBe(CSV_HEADER.map((h) => `"${h}"`).join(";"));
    expect(lines).toHaveLength(X.length + 1);
    lines.slice(1).forEach((line, i) => {
      const want = [T84_1.row[i], T84_1.date[i], T84_1.seq[i], T84_1.batch[i], T84_1.x[i], T84_1.z[i], T84_1.status[i], T84_1.rule[i], T84_1.ma[i], i === 3 ? "OREAS 47" : null].map(csvCell);
      expect(line.split(";")).toEqual(want);
    });
    expect(lines[7].split(";")[1]).toBe(""); // no date — empty, not 0
    expect(lines[1].split(";")[8]).toBe(""); // moving average undefined — empty
  });

  it("the card: the fields of the record without the service ones; numbers with a comma, missing «—»", () => {
    expect(recordFacts({ sample_id: "S-1", lab: "ALS", "v:AU": 1.16, "x:PROJECT": "Север", parent_row: -1, prev_row: 3, date: null })).toEqual([
      ["sample_id", "S-1"], ["lab", "ALS"], ["v:AU", "1,16"], ["PROJECT", "Север"], ["date", "—"],
    ]);
    expect(fieldText(Number.NaN)).toBe("—");
  });
});

describe("speed and requests", () => {
  it("100 000 points: the model, the colours, the canvas layer and the moving average ≤ 33 ms", () => {
    const s = big(100_000);
    const box = plotBox(900, 380, CONTROL_BOX.margin);
    const { w, h } = rasterSize(box);
    const buf = new Uint32Array(w * h);
    const draw = () => {
      const m = controlModel(s, GROUP, 2, 3, "date");
      const sx = makeScale("linear", m.xDomain, box.x0, box.x1);
      const sy = makeScale("linear", m.yDomain, box.y1, box.y0);
      buf.fill(0);
      rasterPoints(buf, w, h, m.xs, m.ys, axisMap(sx, box.x0), axisMap(sy, box.y0), statusPixels(m), 4);
      return [m, maPath(m, sx, sy)] as const;
    };
    draw(); // warm-up
    let best = Infinity;
    for (let k = 0; k < 3; k++) {
      const t0 = performance.now();
      const [m, path] = draw();
      best = Math.min(best, performance.now() - t0);
      expect(m.n).toBe(100_000);
      expect(path.length).toBeLessThan(200_000);
    }
    expect(best).toBeLessThanOrEqual(33);
  });

  let calls: { group?: unknown }[];
  beforeEach(() => {
    calls = [];
    const groups = [{ ...GROUP, crm: "NONE", state: "no_reference", mu: null, sd: null }, GROUP, { ...GROUP, crm: "OREAS 47" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { group?: { crm: string } };
        calls.push({ group: body.group });
        const answer: StandardsAnswer = { element: "AU", unit: "г/т", counts: { rows: 100, filtered: 14, control: 14, unmapped: 0, missing: 0 }, limits: { sigma_warn: 2, sigma_fail: 3 } as StandardsAnswer["limits"], notes: [], groups };
        return json(body.group ? { ...answer, series: T84_1 } : answer);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
  });

  it("a new filter: one request of the groups and one of the series — the group kept, or the first with a reference", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1, labs: ["ALS"] } };
    const first = await loadStandards(body, null);
    expect(calls).toEqual([{ group: undefined }, { group: { crm: "OREAS 45", lab: "ALS" } }]);
    expect(first !== DROPPED && first.group).toEqual({ crm: "OREAS 45", lab: "ALS" });
    calls = [];
    const kept = await loadStandards({ ...body, filter: { v: 1 } }, { crm: "OREAS 47", lab: "ALS" });
    expect(calls).toHaveLength(2);
    expect(calls.filter((c) => c.group === undefined)).toHaveLength(1);
    expect(calls.filter((c) => c.group !== undefined)).toEqual([{ group: { crm: "OREAS 47", lab: "ALS" } }]);
    expect(kept !== DROPPED && kept.series?.series?.x).toEqual(X);
    expect(chooseGroup([], null)).toBeNull();
  });

  it("an overtaken load is dropped", async () => {
    const body = { data_id: 4, element: "AU", filter: { v: 1 } };
    const old = loadStandards(body, null);
    const now = loadStandards({ ...body, filter: { v: 1, crm: ["OREAS 45"] } }, null);
    expect(await old).toBe(DROPPED);
    expect(await now).not.toBe(DROPPED);
  });
});
