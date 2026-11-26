// The window «Сравнить отчёты» (Э20, 20.16; ТЗ 7.35 Д-37, ТД37-1/2; ADR 0027 решение 26): drawn from the REAL answer of the core (`compare_reports` of `contract_answers.json`: two reports of one model with
// another density, the rows of the cut-offs 2 and 3 are only in one of them) — the table «A | B | разность» (a row only in one report is marked and has no difference: «—», not 0), the head of the
// versions of the inputs (Д-35), the notes in Russian («Сетки различаются» is a banner), the two curves on one chart; and from the report against itself made of it (A = B: all the differences 0).
// The command `resources.compare` opens the window (the interface) or answers with the text (the assistant).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { readCompare, type CompareAnswer } from "../../src/modules/resources/answers";
import { CompareChart } from "../../src/modules/resources/CompareChart";
import { dateText, elementOf, fmtDelta, headLines, noCurveText, notes, onlyCount, seriesOf, tableRows } from "../../src/modules/resources/compareModel";
import { COMPARE_WINDOW, compareStore, resetCompare } from "../../src/modules/resources/compareState";
import { CompareWindow } from "../../src/modules/resources/CompareWindow";
import { openCompareWindow, windowOfCompare } from "../../src/modules/resources/compareBind";
import { compareBody, otherCurve, realCurve, selfCompare } from "./compareSample";
import { resourcesStore } from "../../src/modules/resources/state";
import { ID, installCore, newApp, teardown, waitFor, type Core } from "./harness";

const real = (): CompareAnswer => readCompare(compareBody())!;
const same = (): CompareAnswer => readCompare(selfCompare())!;
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("the table: the cells of the real answer", () => {
  const rows = tableRows(real(), "AU", false);
  it("12 rows: the total and the three categories at three cut-offs; the rows of the cut-offs 2 and 3 are only in one report", () => {
    expect(rows).toHaveLength(12);
    expect(rows.map((r) => r.note)).toEqual([null, null, null, null, ...Array(4).fill("только в A"), ...Array(4).fill("только в B")]);
    expect(rows[0]).toMatchObject({ group: "Итого", title: "≥ 1", total: true, differs: true });
    expect(rows[1].group).toBe("Измеренные");
    expect(onlyCount(real())).toEqual({ a: 4, b: 4 });
  });
  it("T: A, B and A − B of the core; the grade and the metal of AU", () => {
    expect(rows[0].tonnes.a.text).toMatch(/12\s500/);
    expect(rows[0].tonnes.b.text).toMatch(/13\s500/);
    expect(rows[0].tonnes.d).toEqual({ text: expect.stringMatching(/^[-−]1\s000$/), kind: "value" });
    expect(rows[0].grade.a.text).toBe("2,5");
    expect(rows[0].grade.d.text).toBe("0"); // the grade is the same: the difference is a real 0
    expect(rows[0].metal.d.text).toMatch(/^[-−]2\s500$/);
  });
  it("a row only in one report: the other side and the difference are «—» (absent), not 0", () => {
    const a = rows[4];
    expect(a.only).toBe("a");
    expect(a.tonnes.a.kind).toBe("value");
    for (const c of [a.tonnes.b, a.tonnes.d, a.grade.b, a.grade.d, a.metal.b, a.metal.d]) expect(c).toEqual({ text: "—", kind: "absent" });
    const b = rows[8];
    expect(b.only).toBe("b");
    for (const c of [b.tonnes.a, b.tonnes.d, b.grade.a, b.metal.a]) expect(c).toEqual({ text: "—", kind: "absent" });
    expect(b.tonnes.b.kind).toBe("value");
  });
  it("«только различия»: the real rows all differ; the report against itself has none", () => {
    expect(tableRows(real(), "AU", true)).toHaveLength(12);
    expect(tableRows(same(), "AU", false).length).toBeGreaterThan(1);
    expect(tableRows(same(), "AU", true)).toHaveLength(0);
    for (const r of tableRows(same(), "AU", false)) expect(r.tonnes.d).toEqual({ text: "0", kind: "value" }); // A = B: 0 everywhere
  });
  it("a value no block has stays «нет значения»; an element the answer has not — a gap, not 0", () => {
    const c = real();
    const row = c.rows[0];
    const bare = { ...c, rows: [{ ...row, a: { ...row.a!, elements: { AU: { ...row.a!.elements.AU, grade: null, metal: null } } }, delta: { ...row.delta!, elements: { AU: { grade: null, metal: null } } } }] };
    const t = tableRows(bare, "AU", false)[0];
    expect(t.grade.a).toEqual({ text: "нет значения", kind: "none" });
    expect(t.grade.d.text).toBe("—");
    expect(t.metal.a.kind).toBe("none");
    expect(elementOf(c, "CU")).toBe("AU"); // an element the answer has not — the first one
    expect(fmtDelta(null)).toBe("—");
    expect(fmtDelta(0)).toBe("0");
    expect(fmtDelta(2.5)).toBe("+2,5");
  });
});

describe("the head of the versions and the notes", () => {
  it("the inputs of both reports side by side by (kind, id); the same fingerprint is not marked, another one is", () => {
    const lines = headLines(real());
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.a?.name)).toEqual(["Модель", "Модель.AU", "Модель.CLASS"]);
    expect(lines.every((l) => l.a && l.b && !l.a.changed)).toBe(true);
    const c = real();
    const moved: CompareAnswer = { ...c, b: { ...c.b, inputs: c.b.inputs.map((i) => (i.id === "1:AU" ? { ...i, fp: "cells|7" } : i)) } };
    const l2 = headLines(moved);
    expect(l2.filter((l) => l.a?.changed).map((l) => l.key)).toEqual(["bm_field|1:AU"]);
    expect(headLines({ ...c, b: { ...c.b, inputs: c.b.inputs.slice(0, 2) } })[2].b).toBeNull(); // an input only in A
    expect(dateText("2026-11-26T08:09:16.671149+00:00")).toMatch(/^2[56] ноября 2026, \d\d:\d\d$/);
    expect(dateText("")).toBe("");
  });
  it("the notes: the words of the core in Russian; «сетки различаются» comes first", () => {
    expect(notes(real()).map((n) => n.code)).toEqual(["density_differs"]);
    expect(notes(real())[0].text).toBe("Плотность в отчётах разная: разность тонн включает и её.");
    expect(notes({ ...real(), grids_differ: true }).map((n) => n.code)).toEqual(["grids_differ", "density_differs"]);
    expect(notes(same())).toEqual([]);
  });
});

describe("the two curves on one chart", () => {
  const withCurves = (a: boolean, b: boolean): CompareAnswer => ({ ...real(), curves: { a: a ? realCurve() : null, b: b ? otherCurve() : null } });
  const html = (c: CompareAnswer, metric: "tonnes" | "grade" | "metal" = "tonnes") => renderToStaticMarkup(createElement(CompareChart, { answer: c, element: "AU", metric }));
  it("the real answer was built without curves: it is said in words, no chart", () => {
    const h = html(real());
    expect(h).toContain('data-testid="cmp-chart-empty"');
    expect(h).toContain("У обоих отчётов нет кривой");
    expect(noCurveText(real())).toBe("У обоих отчётов нет кривой: она выключена (0 точек).");
  });
  it("A and B: two lines, B dashed; the points of the real curve, the metrics of the switch", () => {
    const h = html(withCurves(true, true));
    expect(h).toContain('data-series="2"');
    expect(count(h, /data-series="[ab]"/g)).toBe(2);
    expect(h).toContain('stroke-dasharray="7 4"');
    expect(h).toContain('data-metric="tonnes"');
    expect(html(withCurves(true, true), "grade")).toContain('data-metric="grade"');
    const s = seriesOf("a", "A", realCurve(), "grade", "AU")!;
    expect(s.xs).toEqual([0.2, 1.4, 2.6, 3.8, 5.0]);
    expect(s.ys).toEqual([1.75, 2.875, 4.0, 5.0, 5.0]);
    expect(seriesOf("a", "A", realCurve(), "metal", "CU")).toBeNull(); // an element the curve has not
  });
  it("one report without a curve: the other one is drawn, the missing one is told", () => {
    const h = html(withCurves(false, true));
    expect(h).toContain('data-series="1"');
    expect(h).toContain("У отчёта A нет кривой");
    expect(noCurveText(withCurves(true, true))).toBeNull();
  });
  it("a point with no value breaks the line: a gap is not 0", () => {
    const c = realCurve();
    const gap = { ...c, elements: { AU: { ...c.elements.AU, grade: [1.75, null, 4.0, 5.0, 5.0] } } };
    const h = html({ ...real(), curves: { a: gap, b: null } }, "grade");
    expect((h.match(/ d="M/g) ?? []).length).toBe(2); // two pieces: the first point alone, then the rest
    const none = { ...c, elements: { AU: { grade: [null, null, null, null, null], metal: [null, null, null, null, null] } } };
    expect(html({ ...real(), curves: { a: none, b: none } }, "grade")).toContain('data-testid="cmp-chart-empty"');
  });
});

describe("the window and the command", () => {
  let app: App;
  let core: Core;
  let unbind: () => void;
  beforeEach(() => {
    core = installCore();
    ({ app, unbind } = newApp());
    resetCompare();
  });
  afterEach(() => teardown(unbind));
  const windows = () => windowStore.get().windows.filter((w) => w.kind === COMPARE_WINDOW);
  const draw = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(CompareWindow, { winId: windowOfCompare() ?? "w" })));

  it("without the reports the command opens one window; the reports of the project are listed; «Сравнить» waits for both", async () => {
    const r = await app.registry.execute("resources.compare", {}, "user");
    expect(r).toMatchObject({ ok: true, result: "Открыто окно «Сравнить отчёты»" });
    await app.registry.execute("resources.compare", {}, "user");
    expect(windows()).toHaveLength(1);
    expect(core.sent.some((x) => x.path === "/resources/compare")).toBe(false);
    await waitFor(() => resourcesStore.get().reportsLoaded);
    const h = draw();
    expect(h).toContain('data-testid="cmp-window"');
    expect(h).toContain("№2 · Отчёт по ресурсам · Модель");
    expect(h).toMatch(/data-testid="cmp-run"[^>]*disabled|disabled[^>]*data-testid="cmp-run"/);
    expect(h).toContain("Выберите два отчёта и нажмите «Сравнить»");
  });

  it("one report is not enough: a refusal in Russian with a hint", async () => {
    const r = await app.registry.execute("resources.compare", { report_a: ID.report }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Нужны оба отчёта");
    expect(windows()).toHaveLength(0);
  });

  it("from the interface with both reports: the window opens and shows the real answer — the table, the head, the notes, the chart", async () => {
    const r = await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report + 1 }, "user");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Строки только в одном отчёте: 8");
    expect(windows()).toHaveLength(1);
    expect(compareStore.get()).toMatchObject({ a: ID.report, b: ID.report + 1, phase: "done" });
    const h = draw();
    expect(count(h, /data-testid="cmp-row"/g)).toBe(12);
    expect(count(h, /data-testid="cmp-only"/g)).toBe(8);
    expect(h).toContain("только в A");
    expect(h).toContain("только в B");
    expect(h).toContain('data-testid="cmp-head"');
    expect(h).toContain("Сравнение A");
    expect(h).toContain("Сравнение B");
    expect(h).toContain("Версии входов отчётов");
    expect(h).toContain("Модель.AU");
    expect(h).toContain("Плотность в отчётах разная");
    expect(h).not.toContain("cmp-grids-differ");
    expect(h).toContain("У обоих отчётов нет кривой");
    expect(h).toContain("Строк: 12; только в A: 4, только в B: 4");
  });

  it("«Сетки различаются» is a banner of the window", async () => {
    core.force("POST /resources/compare", { status: 200, body: { ...compareBody(), grids_differ: true } });
    await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report + 1 }, "user");
    const h = draw();
    expect(h).toContain('data-testid="cmp-grids-differ"');
    expect(h).toContain("Сетки различаются");
    expect(h).toContain('role="alert"');
  });

  it("the report against itself: no difference, «только различия» leaves nothing; the choice of a report drops the old answer", async () => {
    core.force("POST /resources/compare", { status: 200, body: selfCompare() });
    await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report }, "user");
    expect(compareStore.get().answer?.rows.every((r) => r.delta?.tonnes === 0)).toBe(true);
    expect(draw()).not.toContain('data-testid="cmp-only"');
    compareStore.set({ onlyDiff: true });
    expect(draw()).toContain('data-testid="cmp-no-rows"');
    expect(draw()).toContain("Различий нет: во всех строках разность равна нулю.");
    const { chooseReport } = await import("../../src/modules/resources/compareState");
    chooseReport("b", ID.report + 1);
    expect(compareStore.get()).toMatchObject({ phase: "idle", answer: null });
  });

  it("a refusal of the core stands in the window in Russian with its hint; the assistant gets it as a text", async () => {
    core.force("POST /resources/compare", { status: 404, body: { code: "resource_report_not_found", message: "Отчёт по ресурсам №999 не найден.", hint: "Номера отчётов — в разделе «Отчёты» дерева." } });
    const r = await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.noReport }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("№999 не найден");
    expect(compareStore.get().phase).toBe("failed");
    const h = draw();
    expect(h).toContain('data-testid="cmp-error"');
    expect(h).toContain("Номера отчётов — в разделе «Отчёты» дерева.");
  });

  it("the assistant with the window open fills it too; the window is the same one", async () => {
    openCompareWindow();
    await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report + 1 }, "claude");
    expect(windows()).toHaveLength(1);
    expect(compareStore.get().phase).toBe("done");
  });

  it("a project open for reading compares too: the comparison writes nothing", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("resources.compare", { report_a: ID.report, report_b: ID.report + 1 }, "user");
    expect(r.ok).toBe(true);
  });
});
