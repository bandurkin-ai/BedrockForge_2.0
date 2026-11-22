// The edit of the model with the mouse, «Подобрать» and «Сохранить» (16.24; ТЗ 8.6.3–8.6.4; ADR 0022 §15, decisions
// 11, 19, 22) on the REAL answers of the core (`core/tests/data/variography/window_answers.json`): the handles of
// the charts (the nugget, the sills of the structures, the ranges of the axis of the chart), their hit and drag; the
// fit request = the body the core was asked, its answer (`order`, `fixed`) in the window; the save body with `fit`,
// a taken name — the next free one, the replacement asked in our own window.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { makeScale } from "../src/charts/core/axisScale";
import { plotBox } from "../src/charts/core/PlotFrame";
import { CoreError } from "../src/shell/api";
import { readFit } from "../src/modules/variography/api";
import { AxisCharts } from "../src/modules/variography/AxisCharts";
import { readJobCalc } from "../src/modules/variography/calc";
import { chartHandles, dragTo, hitHandle, round3, sameAxes, valueAt, type DragScales } from "../src/modules/variography/chartDrag";
import { readModel, type VModel } from "../src/modules/variography/model";
import { fitFix, fitStart, fitText, fitWork, saveBody, saveWork } from "../src/modules/variography/save";
import { dropWork, newWork, setModel, setWork, workOf } from "../src/modules/variography/state";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/variography/window_answers.json"), "utf-8"));
const CALC = readJobCalc(A.job_fit_calc);
const FIT = readFit(A.fit);
const START: VModel = readModel(A.bodies_models.fit.model);

function scales(hMax = 100, vMax = 2): DragScales {
  const box = plotBox(420, 280, { left: 58, right: 18, top: 16, bottom: 44 });
  return { box, sx: makeScale("linear", { min: 0, max: hMax }, box.x0, box.x1), sy: makeScale("linear", { min: 0, max: vMax }, box.y1, box.y0), hMax };
}

const WIN = "vario-test";
afterEach(() => dropWork(WIN));

describe("the handles of a chart", () => {
  const sc = scales();
  it("the nugget node at h = 0, the sill of each structure, the range of the axis of the chart", () => {
    const m = FIT.model;
    const h = chartHandles(m, 1, sc);
    expect(h.map((x) => x.id)).toEqual(["nugget", "sill:0"]); // a1 = 130 m is beyond h_max 100 m: not drawn
    const wide = chartHandles(m, 1, scales(150));
    expect(wide.map((x) => x.id)).toEqual(["nugget", "sill:0", "range:0:a1"]);
    expect(wide[2].x1).toBeCloseTo(scales(150).sx.map(m.structures[0].a1)!, 9);
    expect(chartHandles(m, 2, scales(150)).map((x) => x.id)).toEqual(["nugget", "sill:0", "range:0:a2"]);
    expect(chartHandles(m, 3, sc).find((x) => x.id === "range:0:a3")?.x1).toBeCloseTo(sc.sx.map(10)!, 9);
    expect(h[0].y1).toBeCloseTo(sc.sy.map(m.c0)!, 9);
    expect(h[1].y1).toBeCloseTo(sc.sy.map(m.c0 + m.structures[0].c)!, 9);
    // a direction of its own or downhole: no range
    expect(chartHandles(m, null, sc).map((x) => x.id)).toEqual(["nugget", "sill:0"]);
  });

  it("a structure with angles of its own has no range on the axes of the model", () => {
    const two: VModel = { ...START, common_axes: false, structures: [START.structures[0], { ...START.structures[0], ang1: 75, a1: 50, a2: 30, a3: 5 }] };
    expect(sameAxes(two, 1)).toBe(false);
    expect(chartHandles(two, 1, sc).map((x) => x.id)).toEqual(["nugget", "sill:0", "sill:1", "range:0:a1"]);
    expect(chartHandles({ ...two, common_axes: true }, 1, sc).map((x) => x.id)).toContain("range:1:a1");
  });

  it("the hit: within 8 px, the nugget over the lines, a range over a sill", () => {
    const m = { ...START, c0: 0 };
    const h = chartHandles(m, 1, sc);
    const x0 = sc.sx.map(0)!;
    const y0 = sc.sy.map(0)!;
    expect(hitHandle(h, x0 + 3, y0 - 3)?.id).toBe("nugget");
    const range = h.find((x) => x.id === "range:0:a1")!;
    const sill = h.find((x) => x.id === "sill:0")!;
    expect(hitHandle(h, range.x1 + 2, sill.y1 + 2)?.id).toBe("range:0:a1");
    expect(hitHandle(h, range.x1 + 40, sill.y1 + 5)?.id).toBe("sill:0");
    expect(hitHandle(h, range.x1 + 40, sill.y1 + 9)).toBeNull();
  });
});

describe("the drag", () => {
  const sc = scales();
  it("the nugget ≥ 0; the contribution under the sill of its structure; the range in (0; 100 000], 3 digits", () => {
    const m = START;
    expect(dragTo(m, { kind: "nugget" }, 0, 0.4567).c0).toBe(0.457);
    expect(dragTo(m, { kind: "nugget" }, 0, -0.2).c0).toBe(0);
    const s = dragTo(m, { kind: "sill", s: 0 }, 0, 1.25);
    expect(s.structures[0].c).toBeCloseTo(1.15, 12); // 1,25 − C0 0,1
    expect(s.c0).toBe(m.c0);
    expect(dragTo(m, { kind: "sill", s: 0 }, 0, 0.05).structures[0].c).toBe(0);
    const r = dragTo(m, { kind: "range", s: 0, axis: 2 }, 47.26, 0, { minRange: 5 });
    expect(r.structures[0]).toEqual({ ...m.structures[0], a2: 47.3 });
    expect(dragTo(m, { kind: "range", s: 0, axis: 1 }, 1, 0, { minRange: 5 }).structures[0].a1).toBe(5);
    expect(dragTo(m, { kind: "range", s: 0, axis: 3 }, 2e6, 0).structures[0].a3).toBe(100_000);
    expect(dragTo(m, { kind: "nugget" }, 0, Number.NaN)).toEqual(m);
    expect(round3(0.0012345)).toBe(0.00123);
    expect(round3(123456)).toBe(123000);
  });

  it("the pixel → the value, and back (the handle stays under the pointer)", () => {
    const v = valueAt(sc, sc.sx.map(40)!, sc.sy.map(1.2)!);
    expect(v.h).toBeCloseTo(40, 9);
    expect(v.value).toBeCloseTo(1.2, 9);
    const moved = dragTo(START, { kind: "sill", s: 0 }, v.h, v.value);
    expect(chartHandles(moved, 1, sc).find((x) => x.id === "sill:0")!.y1).toBeCloseTo(sc.sy.map(1.2)!, 6);
  });

  it("the charts of the real calculation draw the handles on every chart; none without onModel", () => {
    const html = renderToStaticMarkup(createElement(AxisCharts, { calc: CALC, model: FIT.model, type: "semivariogram", fileName: "В", onModel: () => undefined }));
    expect((html.match(/data-drag-handles=/g) ?? []).length).toBe(4); // 3 axes and downhole
    expect(html).toContain('data-handle="nugget"');
    expect(html).toContain('data-handle="range:0:a3"'); // a3 = 10 m; a1, a2 beyond the last lag
    const plain = renderToStaticMarkup(createElement(AxisCharts, { calc: CALC, model: FIT.model, type: "semivariogram", fileName: "В" }));
    expect(plain).not.toContain("data-drag-handles");
  });

  it("an edit by hand drops the fit of the old model", () => {
    setWork(WIN, newWork({ model: FIT.model, fitInfo: { weights: "n_h", residual: 1, n: 2, fixed: [] }, back: { share: 1 } }));
    setModel(WIN, dragTo(FIT.model, { kind: "nugget" }, 0, 0.5));
    expect(workOf(WIN)).toMatchObject({ dirty: true, fitInfo: null, back: null });
  });
});

describe("«Подобрать»", () => {
  it("the request is the body the core was asked; the answer in the window with its fixings", async () => {
    setWork(WIN, newWork({ model: START, fix: ["structures.0.a3", "structures.4.c"], expKey: A.bodies_models.fit.key, result: CALC }));
    let asked: unknown = null;
    const got = await fitWork(WIN, {}, async (body) => {
      asked = body;
      return FIT;
    });
    expect(asked).toEqual(A.bodies_models.fit);
    expect(got.fixed).toEqual(["structures.0.a3"]);
    expect(got.order).toEqual([0]);
    const w = workOf(WIN)!;
    expect(w.model).toEqual(FIT.model);
    expect(w.model.structures[0].a3).toBe(START.structures[0].a3); // fixed
    expect(w.fix).toEqual(A.fit.fixed);
    expect(w.fitInfo).toEqual({ weights: "n_h", residual: A.fit.residual, n: A.fit.n, fixed: A.fit.fixed, warnings: A.fit.warnings });
    expect(w.dirty).toBe(true);
    expect(fitText(got)).toMatch(/^Модель подобрана: структур 1, невязка 39,07, точек 14\. Дальность на границе/);
  });

  it("the start model: the types of the command, nested; normal scores; the fixings that exist; no result — refused", async () => {
    const w = newWork({ model: START, result: CALC });
    const two = fitStart(w, ["spherical", "exponential"]);
    expect(two.structures.map((s) => s.type)).toEqual(["spherical", "exponential"]);
    expect(two.structures[1].a1).toBe(START.structures[0].a1 * 2);
    expect(fitStart({ ...w, data: { ...w.data, transform: "nscore" } }).type).toBe("nscore");
    expect(fitFix({ fix: ["c0", "structures.1.a1", "structures.0.c", "x"] }, START, true)).toEqual(["c0", "structures.0.c"]);
    setWork(WIN, newWork({ model: START }));
    await expect(fitWork(WIN, {}, async () => FIT)).rejects.toMatchObject({ code: "no_result" });
  });
});

describe("«Сохранить»", () => {
  it("the body: the model, the key of its points and the fit — as the core took it", () => {
    const w = newWork({ model: FIT.model, expKey: A.bodies_models.save.exp_key, fitInfo: { weights: "n_h", residual: A.fit.residual, n: A.fit.n, fixed: A.fit.fixed } });
    expect(saveBody(w, false)).toEqual(A.bodies_models.save);
    expect(saveBody({ ...w, fitInfo: null }, false)).toEqual({ model: A.bodies_models.save.model, exp_key: A.bodies_models.save.exp_key });
    expect(saveBody({ ...w, fitInfo: null }, true)).toMatchObject({ fit: null, back: null });
  });

  it("a taken name (409 of the core) — the next free one; the window holds the stored model", async () => {
    setWork(WIN, newWork({ model: FIT.model, expKey: A.bodies_models.save.exp_key, data: { ...newWork().data, field: "AU" }, defaults: { table_name: "Пробы" } as never }));
    const names: (string | undefined)[] = [];
    const taken = A.model_taken.body;
    const saved = await saveWork(WIN, undefined, {
      create: async (b) => {
        names.push(b.name);
        if (names.length === 1) throw new CoreError(409, taken.code, taken.message, taken.hint);
        return A.model_second;
      },
      refresh: async () => undefined,
    });
    expect(names).toEqual([A.model_created.name, A.model_second.name]);
    expect(saved).toEqual(A.model_second);
    expect(workOf(WIN)).toMatchObject({ modelId: A.model_second.id, dirty: false });
  });

  it("the replacement asks in our own window first; «Отмена» — nothing is written", async () => {
    setWork(WIN, newWork({ model: FIT.model, expKey: A.bodies_models.save.exp_key, modelId: A.model_created.id, modelVersion: A.model_read.version, dirty: true }));
    const put: unknown[] = [];
    const replace = async (id: number, b: unknown) => {
      put.push([id, b]);
      return A.model_created;
    };
    let question: unknown = null;
    expect(await saveWork(WIN, undefined, { ask: async (q) => ((question = q), false), replace, refresh: async () => undefined })).toBeNull();
    expect(put).toEqual([]);
    expect(question).toMatchObject({ title: "Заменить модель", okLabel: "Заменить", danger: true });
    expect(await saveWork(WIN, undefined, { ask: async () => true, replace, refresh: async () => undefined })).toEqual(A.model_created);
    expect(put[0]).toEqual([A.model_created.id, { model: A.bodies_models.save.model, exp_key: A.bodies_models.save.exp_key, fit: null, back: null, expected_version: A.model_read.version }]);
    expect(workOf(WIN)!.dirty).toBe(false);
  });

  it("the model saved in another window (409 `version_conflict`): «Сохранить как новую» or «Перечитать», in our own window", async () => {
    const c = A.model_conflict.body;
    const conflict = async (): Promise<never> => {
      throw new CoreError(409, c.code, c.message, c.hint);
    };
    const start = () => setWork(WIN, newWork({ model: FIT.model, expKey: A.bodies_models.save.exp_key, data: { ...newWork().data, field: "AU" }, defaults: { table_name: "Пробы" } as never, modelId: A.model_second.id, modelVersion: 1, dirty: true }));
    // «Сохранить как новую»: a POST with the name of the window, the window holds the new model
    start();
    const questions: { title?: string }[] = [];
    const made: unknown[] = [];
    const saved = await saveWork(WIN, undefined, {
      ask: async (q) => (questions.push(q as never), true),
      replace: conflict,
      create: async (b) => (made.push(b), A.model_created),
      refresh: async () => undefined,
    });
    expect(questions.map((q) => q.title)).toEqual(["Заменить модель", "Модель изменена в другом окне"]);
    expect(made).toHaveLength(1);
    expect(saved).toEqual(A.model_created);
    expect(workOf(WIN)).toMatchObject({ modelId: A.model_created.id, modelVersion: 1, dirty: false });
    // «Перечитать»: the stored model replaces the window's, nothing written
    start();
    const answers = [true, false, true];
    const read = { ...readModel(A.model_read.meta), c0: 0.33 };
    const got = await saveWork(WIN, undefined, {
      ask: async () => answers.shift()!,
      replace: conflict,
      create: async () => expect.unreachable("not saved as new"),
      load: async (id) => ({ id, name: "Вторая", color: null, meta: {}, model: read, source: {} as never, points: null, stale: false, stale_reason: null, source_lost: false, version: 2 }),
      refresh: async () => undefined,
    });
    expect(got).toBeNull();
    expect(workOf(WIN)).toMatchObject({ modelId: A.model_second.id, modelVersion: 2, dirty: false, model: { c0: 0.33 } });
    // neither: the window stays as it is
    start();
    expect(await saveWork(WIN, undefined, { ask: async (q) => (q as { title?: string }).title === "Заменить модель", replace: conflict, refresh: async () => undefined })).toBeNull();
    expect(workOf(WIN)).toMatchObject({ modelVersion: 1, dirty: true });
  });
});
