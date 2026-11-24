// The report «Содержание–тоннаж» and the command `blocks.report` (ТЗ 7.31; ADR 0023 §10, decision 15; Э17 17.35) on the
// REAL answers of the core (`core/tests/data/blocks/report_window_answers.json`, made by
// `core/tests/test_blocks_report_window_answers.py` through `POST /blocks/{id}/report` and the job `blocks.report`):
// the request of the form = the body the core got; the result read as the core wrote it (a gap — null, never 0);
// the table with the groups, the curve (a gap breaks the line), the CSV (copy and save), the refusals 404 / 422 in
// Russian in their place of the form, the run of the command through a stubbed core that answers with those answers.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GradeTonnageChart } from "../src/charts/gradetonnage/GradeTonnageChart";
import { curveCsv, nearestIndex, pathOf, pieces } from "../src/charts/gradetonnage/model";
import { AppContext } from "../src/shell/AppContext";
import { CoreError, setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { windowStore } from "../src/shell/windows";
import { jobsStore } from "../src/modules/jobs/state";
import { blocksStore } from "../src/modules/blocks/state";
import { filtersStore } from "../src/modules/blocks/filters/state";
import { sceneObjects } from "../src/modules/scene/objects";
import { readReport, refusalOf, type ReportBody, type ReportResult } from "../src/modules/blocks/report/api";
import { bindReport, cancelReport, openReportWindow, renderReportWindow, windowOfReport } from "../src/modules/blocks/report/bind";
import { commands, REPORT_BUTTONS } from "../src/modules/blocks/report/commands";
import { copyCsv, saveCsv } from "../src/modules/blocks/report/csvOut";
import { csvName, excludedLines, gapCell, gradeCell, gradeNames, groupsOf, metalCell, NO_VALUE, summaryText } from "../src/modules/blocks/report/format";
import { ReportTable, ResultView } from "../src/modules/blocks/report/ResultView";
import { ReportWindow } from "../src/modules/blocks/report/ReportWindow";
import {
  argsOfForm,
  autoDensity,
  bodyOfArgs,
  currentForm,
  currentRun,
  defaultGrades,
  EMPTY_FORM,
  fieldsOf,
  formForModel,
  formOfArgs,
  issueOfRefusal,
  parseCutoffs,
  REPORT_WINDOW,
  reportStore,
  resetReport,
  type ReportForm,
} from "../src/modules/blocks/report/state";
import type { BlockModelInfo } from "../src/modules/blocks/api";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/report_window_answers.json"), "utf-8"));
const IDS = A.ids as { model: number; gaps: number; bare: number; filter: number };
type Case = { request: ReportBody; job: { status: string; progress: number; message: string; error: null; result: unknown } };
const REPORTS = A.reports as Record<string, Case>;
const resultOf = (name: string): ReportResult => readReport(REPORTS[name].job.result)!;
const draftCond = (field: string, op: ">" | "=", value: string) => ({ cond: { field, op, value, list: "" } }) as const;

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const JOB = { source: "proj", id: 5 };
/** A JSON with the keys in order: two bodies are the same request when their canon is the same. */
const canon = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let answerFor: (modelId: number, body: Record<string, unknown>) => Response;
let jobStatus = "done";
let jobCase = "defaults";

const jobRow = () => ({ ...JOB, kind: "blocks.report", title: "Содержание–тоннаж", queue: "quick", status: jobStatus, progress: jobStatus === "done" ? 1 : 0.4, message: jobStatus === "done" ? "Готово" : "Расчёт", error: null, params: null, result: jobStatus === "done" ? REPORTS[jobCase].job.result : null, attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: null, why_waiting: null });

beforeEach(() => {
  sent.length = 0;
  jobStatus = "done";
  jobCase = "defaults";
  windowStore.set({ windows: [] });
  resetReport();
  jobsStore.set({ jobs: [], workers: null, error: null });
  answerFor = (_id, body) => {
    jobCase = Object.entries(REPORTS).find(([, c]) => canon(c.request) === canon(body))?.[0] ?? "defaults";
    return json(JOB);
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      const post = /^\/blocks\/(\d+)\/report$/.exec(path);
      if (post && method === "POST") return answerFor(Number(post[1]), body);
      if (path === "/jobs") return json({ jobs: [jobRow()], workers: { workers: [], heavy_workers: 1, stopped_reason: null } });
      if (path === `/jobs/${JOB.source}/${JOB.id}` && method === "GET") return json(jobRow());
      if (path === `/jobs/${JOB.source}/${JOB.id}/cancel`) {
        jobStatus = "cancelled";
        return json({});
      }
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  app.store.set({ projectName: "Проект", projectReadOnly: false, selected: null });
  // until the orchestrator takes the stub `blocks.report` out of modules/charts/commands.ts and names modules/blocks/report in shell/catalog.ts
  (app.registry as unknown as { commands: Map<string, unknown> }).commands.delete("blocks.report");
  app.registry.register(commands);
  bindReport(app);
  blocksStore.set({ list: A.blocks.body.block_models, loaded: true, error: null, details: { [IDS.model]: A.model.body, [IDS.gaps]: A.gaps_model.body }, detailErrors: {} });
  filtersStore.set({ rows: A.filters.body.filters, loaded: true, selected: null, edit: null });
  sceneObjects.set({ ...sceneObjects.get(), wireframes: [], loaded: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
});

const detailFields = (id: number) => fieldsOf((id === IDS.model ? A.model.body : A.gaps_model.body) as BlockModelInfo);
const reports = () => sent.filter((r) => r.path.endsWith("/report"));

/** The form of a case: what the person sets in the window to ask for the recorded request. */
const FORMS: Record<string, Partial<ReportForm>> = {
  defaults: { cutoffs: "0; 0,5; 1", curveOn: true, curvePoints: "20" },
  groups: { grades: ["AU_CUT", "CU_CUT"], densityMode: "value", densityValue: "2,7", cutoffs: "0,5; 1", groups: "OXIDE", catOn: true, catField: "ALT", catValues: ["G", "Руда окисл. ёЁ"], curveOn: true, curvePoints: "10" },
  filtered: { grades: ["AU_CUT"], cutoffs: "0; 1", curveOn: false, filterId: IDS.filter, restrictOn: true, restrict: draftCond("AU", ">", "1") },
  gaps: { modelId: IDS.gaps, grades: ["AU_CUT", "CU_CUT", "ZN_CUT"], densityMode: "value", densityValue: "2,5", cutoffs: "0; 1; 1000000000", curveOn: true, curvePoints: "6" },
  gaps_by_zn: { modelId: IDS.gaps, grades: ["AU_CUT", "ZN_CUT"], cutoffField: "ZN_CUT", densityMode: "value", densityValue: "2,5", cutoffs: "0; 2", curveOn: false },
  unknown_unit: { modelId: IDS.gaps, grades: ["ZN_CUT"], densityMode: "value", densityValue: "2,5", cutoffs: "0", curveOn: true, curvePoints: "6" },
  density_field: { grades: ["AU_CUT"], densityMode: "field", densityField: "DENSITY", cutoffs: "0", curveOn: false },
};
const formOf = (name: string): ReportForm => ({ ...EMPTY_FORM, modelId: IDS.model, ...FORMS[name] });

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(REPORTS)) {
    it(`${name}: the form → the arguments → the body of the recorded request`, () => {
      const got = argsOfForm(formOf(name));
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      const body = bodyOfArgs(got.args);
      expect(body.ok).toBe(true);
      if (!body.ok) return;
      expect(body.modelId).toBe(formOf(name).modelId);
      expect({ ...body.body, csv: REPORTS[name].request.csv }).toEqual(REPORTS[name].request); // the window always asks the CSV
      expect(body.body.csv).toBe(true);
    });
    it(`${name}: the arguments of a call show in the form and come back as the same body`, () => {
      const got = argsOfForm(formOf(name));
      if (!got.ok) throw new Error("form");
      const form = formOfArgs(got.args, detailFields(formOf(name).modelId as number));
      const again = argsOfForm(form);
      if (!again.ok) throw new Error(JSON.stringify(again.issue));
      const first = bodyOfArgs(got.args);
      if (!first.ok) throw new Error("body");
      // a call that names no grades shows the defaults of the model in the form: the same request, the grades written out
      const grades = first.body.grades ?? defaultGrades(detailFields(formOf(name).modelId as number));
      expect(bodyOfArgs(again.args)).toEqual({ ...first, body: { ...first.body, grades } });
    });
  }

  it("the numbers of the cut-offs: a decimal comma, «;», spaces, a new line; a comma after a number is dropped", () => {
    expect(parseCutoffs("0; 0,5; 1")).toEqual({ ok: true, values: [0, 0.5, 1] });
    expect(parseCutoffs("0 0.5 1")).toEqual({ ok: true, values: [0, 0.5, 1] });
    expect(parseCutoffs("0 1,5 2")).toEqual({ ok: true, values: [0, 1.5, 2] });
    expect(parseCutoffs("0, 1")).toEqual({ ok: true, values: [0, 1] });
    expect(parseCutoffs("0,\n1")).toEqual({ ok: true, values: [0, 1] });
    expect(parseCutoffs("1 000; 2 500,5")).toEqual({ ok: true, values: [1000, 2500.5] });
    expect(parseCutoffs("0,5")).toEqual({ ok: true, values: [0.5] });
    for (const bad of ["", "  ", "0; a", "1,2,3"]) {
      const r = parseCutoffs(bad);
      expect(r.ok, bad).toBe(false);
      if (!r.ok) expect(r.message + r.hint).toMatch(/[А-Яа-яЁё]/);
    }
    const many = parseCutoffs(Array.from({ length: 201 }, (_, i) => i).join("; "));
    expect(many.ok).toBe(false);
  });

  it("a request that is not right is told in Russian, with a hint and the place, before the core is asked", () => {
    const bad = (a: Record<string, unknown>) => {
      const r = bodyOfArgs(a);
      expect(r.ok).toBe(false);
      if (r.ok) throw new Error("accepted");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return r.issue.field;
    };
    expect(bad({})).toBe("model");
    expect(bad({ model_id: 1.5 })).toBe("model");
    expect(bad({ model_id: 1, grades: Array.from({ length: 21 }, (_, i) => `G${i}`).join(", ") })).toBe("grades");
    expect(bad({ model_id: 1, density: 0 })).toBe("density");
    expect(bad({ model_id: 1, density: "-2" })).toBe("density");
    expect(bad({ model_id: 1, cutoffs: "0; много" })).toBe("cutoffs");
    expect(bad({ model_id: 1, filter_id: 1.5 })).toBe("filter");
    expect(bad({ model_id: 1, restrict: "{не json" })).toBe("restrict");
    expect(bad({ model_id: 1, restrict: "[1]" })).toBe("restrict");
    expect(bad({ model_id: 1, categories_field: "ALT" })).toBe("categories");
    expect(bad({ model_id: 1, categories: "G" })).toBe("categories");
    expect(bad({ model_id: 1, curve: 1 })).toBe("curve");
    expect(bad({ model_id: 1, curve: 501 })).toBe("curve");
    expect(sent).toEqual([]);
    // the window: an unfinished restrictor, no density field, a wrong number of points
    const f = (patch: Partial<ReportForm>) => argsOfForm({ ...formOf("defaults"), ...patch });
    for (const [patch, field] of [
      [{ densityMode: "field" as const }, "density"],
      [{ restrictOn: true }, "restrict"],
      [{ catOn: true }, "categories"],
      [{ curvePoints: "1" }, "curve"],
      [{ modelId: null }, "model"],
    ] as const) {
      const r = f(patch);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.issue.field).toBe(field);
    }
  });

  it("the defaults of 7.31 as the core takes them: the grades *_CUT, the density SG (a constant DENSITY is skipped)", () => {
    const real = detailFields(IDS.model);
    expect(defaultGrades(real)).toEqual(["AU_CUT", "CU_CUT"]);
    expect(autoDensity(real)).toBe("SG");
    expect(defaultGrades(detailFields(IDS.gaps))).toEqual(["AU_CUT", "CU_CUT", "ZN_CUT"]);
    expect(autoDensity(detailFields(IDS.gaps))).toBeNull();
    const form = formForModel(IDS.model, real);
    expect(form.grades).toEqual(["AU_CUT", "CU_CUT"]);
    expect(form.densityMode).toBe("auto");
    // another model: the choices that belong to the first one go; the cut-offs and the curve stay
    const next = formForModel(IDS.gaps, detailFields(IDS.gaps), { ...form, cutoffs: "0; 2", groups: "OXIDE", cutoffField: "AU_CUT" });
    expect(next.groups).toBe("");
    expect(next.cutoffs).toBe("0; 2");
    expect(next.grades).toEqual(["AU_CUT", "CU_CUT", "ZN_CUT"]);
  });
});

describe("the result of the core is read as it was written", () => {
  it("every recorded report is a report: the rows, the cut-offs, the units, the curve", () => {
    for (const [name, c] of Object.entries(REPORTS)) {
      const r = readReport(c.job.result);
      expect(r, name).not.toBeNull();
      expect(r!.rows.length).toBe((c.job.result as { rows: unknown[] }).rows.length);
      expect(r!.units).toEqual({ volume: "м³", tonnes: "т" });
    }
    const d = resultOf("defaults");
    expect(d.model_id).toBe(IDS.model);
    expect(d.density).toEqual({ field: "SG", value: null });
    expect(d.rows.map((r) => r.cutoff)).toEqual([0, 0.5, 1]);
    expect(d.rows[0].blocks).toBe(45);
    expect(d.rows[0].grades.AU_CUT).toMatchObject({ unit: "g/t", metal_unit: "тыс. унц", missing_blocks: 0 });
    expect(d.curve!.cutoffs).toHaveLength(20);
    expect(d.curve!.tonnes).toHaveLength(20);
    expect(d.excluded).toEqual({ filtered: 0, no_volume: 0, no_density: 1, no_cutoff: 0 });
    expect(d.csv!.startsWith("Группа;Борт;Блоков;Объём, м³;Тоннаж, т;AU_CUT")).toBe(true);
    expect(resultOf("filtered").curve).toBeNull();
    expect(resultOf("density_field").density).toEqual({ field: "DENSITY", value: null });
    expect(resultOf("groups").density).toEqual({ field: null, value: 2.7 });
  });

  it("an answer that is not a report is not read (no rows, a row without numbers)", () => {
    expect(readReport(null)).toBeNull();
    expect(readReport({})).toBeNull();
    expect(readReport({ model_id: 1, rows: [{ group: null, cutoff: 0 }] })).toBeNull();
    expect(readReport({ model_id: 1, rows: [] })?.rows).toEqual([]);
  });

  it("a gap is a gap: the grade with no value is null («нет значения»), never 0; the unit that is not known gives no metal", () => {
    const g = resultOf("gaps");
    const zero = g.rows.find((r) => r.cutoff === 0)!;
    expect(zero.grades.CU_CUT.grade).toBeNull();
    expect(zero.grades.CU_CUT.metal).toBeNull();
    expect(gradeCell(zero.grades.CU_CUT)).toMatchObject({ text: NO_VALUE, missing: true });
    expect(metalCell(zero.grades.CU_CUT)).toMatchObject({ text: NO_VALUE, missing: true });
    expect(zero.grades.ZN_CUT.metal).toBeNull();
    expect(zero.grades.ZN_CUT.grade).not.toBeNull();
    expect(metalCell(zero.grades.ZN_CUT)).toMatchObject({ text: "—", missing: false });
    expect(metalCell(zero.grades.ZN_CUT).title).toMatch(/не распознана/);
    expect(zero.grades.CU_CUT.missing_blocks).toBe(3);
    expect(gapCell(zero.grades.CU_CUT).text).toMatch(/^3 бл\. · /);
    expect(gapCell(zero.grades.ZN_CUT).text).toBe("0");
    // a cut-off no block is above: no blocks, no grade — and tonnes are a real 0
    const top = g.rows.find((r) => r.cutoff === 1e9)!;
    expect(top.blocks).toBe(0);
    expect(top.tonnes).toBe(0);
    expect(top.grades.AU_CUT.grade).toBeNull();
    expect(gradeCell(top.grades.AU_CUT).text).toBe(NO_VALUE);
    // a grade the cut-off field does not have in a block: the block is outside the mean, its tonnes are told apart
    const byZn = resultOf("gaps_by_zn").rows[0];
    expect(byZn.grades.AU_CUT.missing_blocks).toBe(1);
    expect(byZn.grades.AU_CUT.missing_tonnes).toBeGreaterThan(0);
    expect(byZn.grades.AU_CUT.grade).not.toBeNull();
  });

  it("what was not counted is told in words, only what is not zero", () => {
    expect(excludedLines(resultOf("defaults").excluded, "AU_CUT")).toEqual(["без плотности (нет значения или плотность меньше нуля): 1 блок"]);
    const gaps = excludedLines(resultOf("gaps").excluded, "AU_CUT");
    expect(gaps).toEqual(["без значения поля борта «AU_CUT»: 1 блок"]);
    const filtered = excludedLines(resultOf("filtered").excluded, "AU_CUT");
    expect(filtered[0]).toMatch(/^отсеяны ограничителем, фильтром или отбором категорий: \d+ блок/);
  });

  it("the text for the log and the assistant: the table, the gaps, what was not counted", () => {
    const text = summaryText(resultOf("gaps"), "Пропуски");
    expect(text).toContain("модель «Пропуски»");
    expect(text).toContain("плотность — 2,5 т/м³");
    expect(text).toMatch(/CU_CUT: нет значения \(без значения 3 блока\)/);
    expect(text).toContain("Не вошли в расчёт: без значения поля борта «AU_CUT»: 1 блок");
    expect(text).not.toMatch(/CU_CUT 0/);
    const long = summaryText(resultOf("groups"), "Модель", 3);
    expect(long).toMatch(/и ещё \d+ строк/);
  });
});

describe("the table with the groups", () => {
  it("the whole model first, then the groups of the category field; the gaps drawn apart", () => {
    const r = resultOf("groups");
    expect(groupsOf(r)[0]).toBeNull();
    expect(new Set(groupsOf(r))).toContain("Ox");
    expect(new Set(groupsOf(r))).toContain("Sulf");
    expect(gradeNames(r)).toEqual(["AU_CUT", "CU_CUT"]);
    const html = renderToStaticMarkup(createElement(ReportTable, { result: r }));
    expect(html).toContain('data-testid="report-table"');
    expect(html).toContain("Всего");
    expect(html).toContain(">Ox<");
    expect(html).toContain(">Sulf<");
    expect(html).toContain("Борт, g/t");
    expect(html).toContain("Металл, тыс. унц");
    expect(html).toContain("Металл, т");
    // the tonnes of the groups add up to the whole (the table shows what the core counted)
    const whole = r.rows.filter((x) => x.group === null && x.cutoff === 0.5)[0].tonnes;
    const parts = r.rows.filter((x) => x.group !== null && x.cutoff === 0.5).reduce((s, x) => s + x.tonnes, 0);
    expect(parts).toBeCloseTo(whole, 6);
  });

  it("a grade with no value is «нет значения» in the markup, never a 0", () => {
    const html = renderToStaticMarkup(createElement(ReportTable, { result: resultOf("gaps") }));
    expect(html).toMatch(/data-testid="report-grade-0-CU_CUT"[^>]*data-missing="true"[^>]*>нет значения</);
    expect(html).toMatch(/data-testid="report-metal-0-CU_CUT"[^>]*data-missing="true"[^>]*>нет значения</);
    expect(html).toMatch(/data-testid="report-grade-2-AU_CUT"[^>]*data-missing="true"[^>]*>нет значения</); // the cut-off 1e9
    expect(html).toMatch(/data-testid="report-metal-0-ZN_CUT"[^>]*>—</);
    expect(html).not.toMatch(/data-testid="report-grade-0-CU_CUT"[^>]*>0</);
  });
});

describe("the curve", () => {
  it("a gap breaks the line: the pieces are the runs of values", () => {
    const x = [0, 1, 2, 3, 4, 5];
    const p = pieces(x, [1, 2, null, 4, null, 6], (v) => v * 10, (v) => 100 - v);
    expect(p).toEqual([[[0, 99], [10, 98]], [[30, 96]], [[50, 94]]]);
    expect(pathOf(p[0])).toBe("M0.0,99.0L10.0,98.0");
    expect(pieces([0, 1], [null, null], (v) => v, (v) => v)).toEqual([]);
    expect(nearestIndex([0, 1, 2, 4], 2.9)).toBe(2);
    expect(nearestIndex([0, 1, 2, 4], 3.1)).toBe(3);
  });

  it("the chart of the real curve: the tonnes fall, every grade point is drawn, a missing one is not", () => {
    const real = resultOf("defaults").curve!;
    expect(real.tonnes[0]).toBeGreaterThan(real.tonnes[real.tonnes.length - 1]); // above a higher cut-off — less
    const html = renderToStaticMarkup(createElement(GradeTonnageChart, { curve: real, right: "grade", marks: [2, 3, 5, 100] }));
    expect(html).toContain('data-series="tonnes"');
    expect(html).toContain('data-series="grade"');
    expect(html).toContain("Тоннаж выше борта, т");
    expect(html).toContain("Среднее содержание выше борта, g/t");
    expect((html.match(/data-mark=/g) ?? []).length).toBe(3); // a cut-off outside the curve is not drawn
    const grade = html.slice(html.indexOf('data-series="grade"'));
    expect((grade.match(/<circle/g) ?? []).length).toBe(real.grade.filter((v) => v !== null).length);
    const metal = renderToStaticMarkup(createElement(GradeTonnageChart, { curve: real, right: "metal" }));
    expect(metal).toContain("Металл выше борта, тыс. унц");
  });

  it("a curve with points that have no grade: the points are not drawn at 0, a series with no values is told in words", () => {
    const real = resultOf("gaps").curve!;
    const gaps = { ...real, grade: real.grade.map((v, i) => (i === 3 ? null : v)) }; // a point with no grade (made from the real curve)
    const html = renderToStaticMarkup(createElement(GradeTonnageChart, { curve: gaps, right: "grade" }));
    const grade = html.slice(html.indexOf('data-series="grade"'));
    expect((grade.match(/<circle/g) ?? []).length).toBe(gaps.grade.filter((v) => v !== null).length);
    expect(grade.match(/data-pieces="(\d+)"/)![1]).not.toBe("0");
    const none = renderToStaticMarkup(createElement(GradeTonnageChart, { curve: { ...gaps, grade: gaps.grade.map(() => null) }, right: "grade" }));
    expect(none).toContain('data-empty="grade"');
    expect(none).toContain("нет значения ни в одной точке");
    expect(none).not.toMatch(/data-series="grade"[^>]*>\s*<path/);
    const csv = curveCsv(gaps);
    expect(csv.split("\r\n")[0]).toBe('"Борт, g/t";"Тоннаж выше борта, т";"Содержание выше борта, g/t";"Металл выше борта, тыс. унц"');
    expect(csv).toMatch(/;;/); // a gap stays an empty cell
  });
});

describe("a metal of an unknown unit", () => {
  it("has no value in any point of the curve: the button «Металл» is off and the chart says so in words", () => {
    const r = resultOf("unknown_unit");
    expect(r.curve!.metal).toEqual(Array(6).fill(null));
    expect(r.curve!.metal_unit).toBeNull();
    expect(r.curve!.grade.every((v) => v !== null)).toBe(true);
    const chart = renderToStaticMarkup(createElement(GradeTonnageChart, { curve: r.curve!, right: "metal" }));
    expect(chart).toContain('data-empty="metal"');
    expect(chart).toContain("нет значения");
    const view = renderToStaticMarkup(createElement(ResultView, { result: r, modelName: "Пропуски" }));
    expect(view).toMatch(/<button[^>]*disabled=""[^>]*data-testid="report-right-metal"/);
    expect(view).toMatch(/data-testid="report-right-grade"/);
    expect(view).toContain('data-series="grade"'); // the grade is drawn instead
    expect(view).toMatch(/data-testid="report-metal-0-ZN_CUT"[^>]*>—</);
  });
});

describe("the refusals of the core in the form", () => {
  const FIELD: Record<string, string> = {
    not_object: "general",
    grades_not_list: "grades",
    grade_missing: "grades",
    grade_category: "grades",
    density_category: "density",
    density_zero: "density",
    no_density: "density",
    cutoffs_empty: "cutoffs",
    cutoff_text: "cutoffs",
    curve_one: "curve",
    groups_number: "groups",
    category_label: "categories",
    filter_missing: "filter",
    restrict_field: "filter",
    model_missing: "model",
    too_many_grades: "grades",
  };
  for (const [name, field] of Object.entries(FIELD)) {
    it(`${name}: the words and the hint of the core, under «${field}»`, () => {
      const a = A.refused[name] as { status: number; body: Record<string, unknown> & { code: string; message: string; hint: string } };
      expect([404, 422]).toContain(a.status);
      const err = Object.assign(new CoreError(a.status, a.body.code, a.body.message, a.body.hint), { body: a.body });
      const r = refusalOf(err);
      expect(r).toMatchObject({ code: a.body.code, message: a.body.message, hint: a.body.hint });
      expect(r.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.hint).not.toBe("");
      expect(issueOfRefusal(r, EMPTY_FORM)).toEqual({ field, message: a.body.message, hint: a.body.hint });
    });
  }

  it("a filter field the quick restrictor reads that the model has not: under the restrictor when there is no filter", () => {
    const a = A.refused.restrict_field.body as { code: string; message: string; hint: string };
    const r = refusalOf(Object.assign(new CoreError(422, a.code, a.message, a.hint), { body: a }));
    expect(issueOfRefusal(r, { ...EMPTY_FORM, restrictOn: true }).field).toBe("restrict");
    expect(issueOfRefusal(r, { ...EMPTY_FORM, restrictOn: true, filterId: IDS.filter }).field).toBe("filter");
  });
});

describe("the command `blocks.report`", () => {
  it("is in the registry with a Russian title and a description for the assistant; bound; the button of the ribbon is it", () => {
    expect(commands.map((c) => c.id)).toEqual(["blocks.report"]);
    const c = app.registry.get("blocks.report")!;
    expect(c.run).toBeTypeOf("function");
    expect(c.stage).toBeUndefined();
    expect(c.uiOnly).toBeUndefined();
    expect(c.confirm).toBeUndefined(); // the report writes nothing
    expect(c.title).toBe("Содержание–тоннаж");
    expect(c.description).toMatch(/нет значения/);
    expect(c.required ?? []).toEqual([]); // without model_id — the window
    for (const b of REPORT_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
    expect(Object.keys(c.params!)).toEqual(["model_id", "grades", "density", "cutoffs", "cutoff_field", "filter_id", "restrict", "categories_field", "categories", "groups", "curve", "csv"]);
  });

  it("is unavailable without a project and without block models; works in a project open for reading", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("blocks.report", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект" });
    blocksStore.set({ list: [], loaded: true });
    expect((await app.registry.execute("blocks.report", {})).result).toBe("В проекте нет блочных моделей");
    blocksStore.set({ list: A.blocks.body.block_models, loaded: true });
    app.store.set({ projectReadOnly: true });
    const r = await app.registry.execute("blocks.report", { model_id: IDS.model, curve: 0 }, "claude");
    expect(r.ok, r.result).toBe(true);
  });

  it("without model_id it opens the window with the model of the tree and its defaults; once", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.gaps } });
    const r = await app.registry.execute("blocks.report", {}, "user");
    expect(r).toEqual({ ok: true, result: "Открыто окно «Содержание–тоннаж»" });
    const win = windowStore.get().windows.filter((w) => w.kind === REPORT_WINDOW);
    expect(win).toHaveLength(1);
    expect(win[0].title).toBe("Содержание–тоннаж · Пропуски");
    expect(currentForm().modelId).toBe(IDS.gaps);
    expect(currentForm().grades).toEqual(["AU_CUT", "CU_CUT", "ZN_CUT"]);
    expect(openReportWindow(app)).toBe(windowOfReport());
    expect(windowStore.get().windows.filter((w) => w.kind === REPORT_WINDOW)).toHaveLength(1);
    expect(renderReportWindow({ ...win[0], kind: "other" })).toBeNull();
    expect(reports()).toEqual([]);
  });

  it("runs the report: the body of the request, the job, the result in the window, the text for the assistant", async () => {
    const args = { model_id: IDS.gaps, grades: "AU_CUT, CU_CUT, ZN_CUT", density: "2,5", cutoffs: "0; 1; 1000000000", curve: 6, csv: true };
    const r = await app.registry.execute("blocks.report", args, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(reports()).toHaveLength(1);
    expect(reports()[0].path).toBe(`/blocks/${IDS.gaps}/report`);
    expect(reports()[0].body).toEqual(REPORTS.gaps.request);
    expect(r.result).toMatch(/^Отчёт «Содержание–тоннаж», модель «Пропуски»/);
    expect(r.result).toContain("CU_CUT: нет значения");
    const run = currentRun();
    expect(run.phase).toBe("done");
    expect(run.job).toEqual(JOB);
    expect(run.result).toEqual(resultOf("gaps"));
    expect(windowOfReport()).toBeNull(); // the assistant does not open windows
    // the form shows what was asked
    expect(currentForm()).toMatchObject({ modelId: IDS.gaps, densityMode: "value", densityValue: "2,5", curveOn: true, curvePoints: "6" });
    // a call of the user shows the window with the result
    jobCase = "gaps";
    const again = await app.registry.execute("blocks.report", args, "user");
    expect(again.ok).toBe(true);
    const win = windowOfReport()!;
    const html = renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ReportWindow, { winId: win })));
    for (const id of ["report-window", "report-model", "report-grades", "report-density-auto", "report-cutoffs", "report-filter", "report-curve-on", "report-build", "report-result", "report-table", "report-curve", "report-csv-copy", "report-csv-save", "report-excluded"]) expect(html, id).toContain(`data-testid="${id}"`);
    for (const t of ["Модель", "Содержания", "Плотность", "Борта", "Ограничение", "Категории и группы", "Кривая", "Построить", "Копировать CSV", "Сохранить CSV…", "нет значения"]) expect(html, t).toContain(t);
    expect(html).toContain("Модель «Пропуски»");
  });

  it("the window before a run: the form of the defaults and the invitation; a refusal of the core under its field", async () => {
    app.store.set({ selected: { kind: "blocks", id: IDS.model } });
    await app.registry.execute("blocks.report", {}, "user");
    const html = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(ReportWindow, { winId: windowOfReport()! })));
    expect(html()).toContain('data-testid="report-idle"');
    expect(html()).toContain("Автоматически: поле SG");
    expect(html()).toContain("Выбрано 2 из 20");
    expect(html()).toContain('data-testid="report-grade-AU_CUT"');
    answerFor = () => json(A.refused.grade_missing.body, 404);
    const r = await app.registry.execute("blocks.report", { model_id: IDS.model, grades: "NOPE" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toBe(`${A.refused.grade_missing.body.message} ${A.refused.grade_missing.body.hint}`);
    expect(currentRun().phase).toBe("failed");
    expect(reportStore.get().issue).toMatchObject({ field: "grades", message: A.refused.grade_missing.body.message });
    const after = html();
    expect(after).toContain('data-testid="report-issue-grades"');
    expect(after).toContain('data-testid="report-failed"');
    expect(after).toContain(A.refused.grade_missing.body.message);
  });

  it("a wrong request never reaches the core; the model that is not there is told", async () => {
    const r = await app.registry.execute("blocks.report", { model_id: IDS.model, cutoffs: "0; много" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Борт «много» — не число\./);
    expect(reportStore.get().issue?.field).toBe("cutoffs");
    const gone = await app.registry.execute("blocks.report", { model_id: 999 }, "claude");
    expect(gone.result).toBe("Блочная модель №999 не найдена. Номера моделей — в разделе «Блочные модели» дерева.");
    expect(reports()).toEqual([]);
  });

  it("the job that is cancelled leaves nothing: «Отменить» → the job is cancelled, the run is told so", async () => {
    jobStatus = "running";
    const run = app.registry.execute("blocks.report", { model_id: IDS.model, curve: 0 }, "user");
    await vi.waitFor(() => expect(currentRun().job).toEqual(JOB));
    expect(currentRun().phase).toBe("running");
    const busy = await app.registry.execute("blocks.report", { model_id: IDS.model }, "claude");
    expect(busy.result).toBe("Отчёт уже строится. Дождитесь конца или отмените его в окне «Содержание–тоннаж».");
    await cancelReport();
    const r = await run;
    expect(sent.some((x) => x.path === `/jobs/proj/5/cancel` && x.method === "POST")).toBe(true);
    expect(r).toEqual({ ok: true, result: "Отчёт по модели «Модель» отменён: ничего не записано" });
    expect(currentRun().phase).toBe("cancelled");
    expect(currentRun().result).toBeNull();
  });

  it("another project: the window starts clean", async () => {
    await app.registry.execute("blocks.report", { model_id: IDS.model, curve: 0 }, "user");
    expect(currentRun().phase).toBe("done");
    resetReport();
    expect(currentRun().phase).toBe("idle");
    expect(currentForm()).toEqual(EMPTY_FORM);
  });
});

describe("the CSV", () => {
  const CSV = (REPORTS.groups.job.result as { csv: string }).csv;

  async function done() {
    await app.registry.execute("blocks.report", { model_id: IDS.model, grades: "AU_CUT, CU_CUT", density: "2,7", cutoffs: "0,5; 1", groups: "OXIDE", categories_field: "ALT", categories: JSON.stringify(["G", "Руда окисл. ёЁ"]), curve: 10, csv: true }, "claude");
    expect(currentRun().phase).toBe("done");
  }

  it("is the text of the core: «;», a decimal comma, CRLF; the file name is the model's", async () => {
    jobCase = "groups";
    await done();
    expect(currentRun().result!.csv).toBe(CSV);
    expect(CSV.startsWith("Группа;Борт;Блоков;")).toBe(true);
    expect(CSV).toContain("\r\n");
    expect(CSV.split("\r\n")[1]).toMatch(/^;0,5;\d+;[\d,]+;/);
    expect(csvName("Модель")).toBe("Содержание-тоннаж Модель");
    expect(csvName('Мод:ель/1 "Б"')).toBe("Содержание-тоннаж Мод ель 1 Б");
  });

  it("«Копировать CSV» puts it on the clipboard, «Сохранить CSV…» writes it through the dialog of the shell", async () => {
    jobCase = "groups";
    await done();
    const copyText = vi.fn();
    const saveFile = vi.fn(async () => "C:\\out\\report.csv");
    vi.stubGlobal("window", { bf: { copyText, saveFile } });
    expect(await copyCsv()).toBe("Таблица CSV скопирована");
    expect(copyText).toHaveBeenCalledWith(CSV);
    expect(await saveCsv()).toBe("C:\\out\\report.csv");
    expect(saveFile).toHaveBeenCalledWith("Содержание-тоннаж Модель.csv", "csv", CSV);
    saveFile.mockResolvedValueOnce(null as never); // the dialog was cancelled
    expect(await saveCsv()).toBeNull();
    saveFile.mockResolvedValueOnce({ error: "Диск защищён от записи." } as never);
    await expect(saveCsv()).rejects.toThrow("Диск защищён от записи.");
    vi.unstubAllGlobals();
  });

  it("without a report there is nothing to copy: a refusal in Russian", async () => {
    await expect(copyCsv()).rejects.toMatchObject({ code: "no_csv", message: "В отчёте нет таблицы CSV." });
    await expect(saveCsv()).rejects.toMatchObject({ code: "no_csv" });
  });
});
