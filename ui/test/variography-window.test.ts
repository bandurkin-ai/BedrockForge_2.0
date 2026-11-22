// The window «Вариография» (ТЗ 8.6.4; ADR 0022 §15, decisions 13, 19, 23) on the REAL answers of the core
// (`core/tests/data/variography/window_answers.json`, made by `core/tests/test_vario_window_answers.py` through the
// routes and a job in a worker): `job.result` with `fresh` and `stages`, the preview with `sampled`, `job.error`, the
// 422 of the preview with `counts`; the body of the window = the body the core was asked; the runner — the preview
// 200 ms after an edit (the newest only), a drag waits for the release, the job on «Рассчитать» and then after every
// settled edit with the previous unfinished one cancelled; the charts and the table of the model.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CoreError } from "../src/shell/api";
import { readCalc, type CalcResult } from "../src/modules/variography/api";
import { AxisCharts } from "../src/modules/variography/AxisCharts";
import { axesOf, axisSeries, calcNotes, chartType, countsLine, createRunner, curveLabel, pickShown, readJobCalc, readRefusal, stagesText, type Clock, type RunnerState } from "../src/modules/variography/calc";
import { cellValue, ModelTable } from "../src/modules/variography/ModelTable";
import type { VModel } from "../src/modules/variography/model";
import { DEFAULT_DATA, experimentalBody } from "../src/modules/variography/state";
import type { Started } from "../src/modules/composites/api";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/variography/window_answers.json"), "utf-8"));
const MODEL: VModel = { type: "semivariogram", c0: 0.3, common_axes: true, structures: [{ type: "spherical", c: 1.2, a1: 60, a2: 40, a3: 20, ang1: 30, ang2: 0, ang3: 0 }] };

describe("the answers of the core", () => {
  it("job.result: the form, fresh, the stages; a lag without pairs stays null", () => {
    const r = readJobCalc(A.job_axes);
    expect(r.key).toMatch(/^[0-9a-f]{64}$/);
    expect(r.fresh).toBe(false);
    expect(Object.keys(r.stages).sort()).toEqual(["downhole", "pairs", "read", "total", "trajectories", "write"]);
    expect(r.directions.map((d) => d.axis)).toEqual([1, 2, 3]);
    expect(r.directions[0].gamma.slice(0, 2)).toEqual([null, null]);
    expect(r.directions[0].n.slice(0, 2)).toEqual([0, 0]);
    expect(r.downhole?.lag).toBe(2);
    expect(r.counts).toEqual(A.job_axes.counts);
    expect(r.sampled).toBeNull();
    const text = stagesText(r)!;
    expect(text.startsWith("Расчёт ")).toBe(true);
    for (const t of ["чтение таблицы", "траектории", "пары", "вдоль скважин", "запись"]) expect(text).toContain(t);
    expect(text).not.toContain("нормальные оценки");
    expect(stagesText({ fresh: true, stages: { total: 0.03 } })).toBe("Итог из кеша: те же данные и параметры, расчёт не повторялся (0,03 с)");
  });

  it("normal scores: σ² = 1, σ²(Z) of the data, the stage of the scores, no downhole", () => {
    const r = readJobCalc(A.job_nscore);
    expect(r.transform).toBe("nscore");
    expect(r.sigma2).toBe(1);
    expect(r.sigma2_z).toBe(A.job_nscore.sigma2_z);
    expect(r.downhole).toBeNull();
    expect(stagesText(r)).toContain("нормальные оценки");
    expect(r.directions.map((d, i) => curveLabel(d, i))).toEqual(["Направление 1 · Аз 0° · Пог 0°", "Направление 2 · Аз 90° · Пог 0°"]);
    expect(chartType(MODEL, "nscore")).toBe("nscore");
  });

  it("the preview: key null, no stages; sampled — «по выборке 20 000 из 21 000 проб»", () => {
    const p = readCalc(A.preview);
    expect(p.key).toBeNull();
    expect(p.directions).toEqual(readCalc(A.job_axes).directions);
    expect(calcNotes(p, "preview")).toEqual(["Предпросмотр по всем пробам"]);
    const big = readCalc(A.preview_sampled);
    expect(big.sampled).toEqual({ n: 20_000, of: 21_000, seed: 12345 });
    expect(calcNotes(big, "preview")[0].replace(/\u00a0/g, " ")).toBe("Предпросмотр: расчёт по выборке 20 000 из 21 000 проб; итог — после расчёта всех проб");
    expect(calcNotes(readJobCalc(A.job_axes), "stale")).toContain("Данные или параметры изменены: показан прежний расчёт");
  });

  it("refusals: 422 of the preview with counts and param; job.error {code, message, hint}", () => {
    const body = A.preview_refused.body;
    const e = new CoreError(422, body.code, body.message, body.hint);
    e.body = body;
    const r = readRefusal(e);
    expect(r.code).toBe("no_values");
    expect(r.text).toBe(`${body.message} ${body.hint}`);
    expect(r.counts?.outside_domain).toBe(452);
    expect(countsLine(r.counts!)).toBe("Точек: 0 из 480 (пропуск значения 28, вне домена 452)");
    const bp = A.preview_bad_param.body;
    const e2 = new CoreError(422, bp.code, bp.message, bp.hint);
    e2.body = bp;
    expect(readRefusal(e2)).toMatchObject({ code: "bad_param", param: "nlag", counts: null });
    const job = readRefusal(A.job_failed.error);
    expect(job).toEqual({ text: `${A.job_failed.error.message} ${A.job_failed.error.hint}`, code: "no_values", counts: null, param: null });
    expect(countsLine(readCalc(A.job_axes).counts)).toBe("Точек: 452 из 480 (пропуск значения 28)");
  });

  it("the body of the window is the body the core was asked", () => {
    const b = A.bodies.axes;
    const data = { ...DEFAULT_DATA, setId: b.set_id, table: b.table, field: b.field };
    const model: VModel = { ...MODEL, structures: [{ ...MODEL.structures[0], ang1: b.axes.ang1, ang2: b.axes.ang2, ang3: b.axes.ang3 }] };
    const built = experimentalBody(data, { nlag: String(b.nlag), min_pairs: String(b.min_pairs) }, { axes: axesOf(model) });
    expect(built.body).toEqual({ ...b, domain: null, weights: null, transform: "none", downhole: true });
    expect(axesOf({ structures: [] })).toEqual({ ang1: 0, ang2: 0, ang3: 0 });
    // the core put every default in `params`: the same angles, the given lags
    expect(A.job_axes.params.axes).toEqual(b.axes);
    expect(A.job_axes.params.nlag).toBe(b.nlag);
  });
});

describe("what is shown", () => {
  const job = readJobCalc(A.job_axes);
  const pv = readCalc(A.preview);
  it("the result of the body, else its preview, else the last one stale", () => {
    expect(pickShown("b", job, "b", pv, "b")).toEqual({ calc: job, kind: "job" });
    expect(pickShown("c", job, "b", pv, "c")).toEqual({ calc: pv, kind: "preview" });
    expect(pickShown("c", job, "b", null, null)).toEqual({ calc: job, kind: "stale" });
    expect(pickShown("c", job, null, null, null)).toEqual({ calc: job, kind: "job" }); // the points of a stored model
    expect(pickShown(null, null, null, pv, "b")).toEqual({ calc: pv, kind: "stale" });
    expect(pickShown("b", null, null, null, null)).toEqual({ calc: null, kind: null });
  });
});

// ---- the runner ----

function fakeClock() {
  let now = 0;
  const timers: { at: number; fn: () => void; id: number }[] = [];
  let next = 1;
  const clock: Clock = {
    set: (fn, ms) => {
      const id = next++;
      timers.push({ at: now + ms, fn, id });
      return id;
    },
    clear: (t) => {
      const k = timers.findIndex((x) => x.id === t);
      if (k >= 0) timers.splice(k, 1);
    },
  };
  const advance = (ms: number) => {
    now += ms;
    for (const t of timers.filter((x) => x.at <= now)) {
      timers.splice(timers.indexOf(t), 1);
      t.fn();
    }
  };
  return { clock, advance };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function setup() {
  const { clock, advance } = fakeClock();
  const previews: string[] = [];
  const started: Started[] = [];
  const cancelled: number[] = [];
  const resolvers: ((r: CalcResult) => void)[] = [];
  let id = 0;
  let last: RunnerState | null = null;
  const runner = createRunner(
    {
      preview: (b) => {
        previews.push(JSON.stringify(b));
        return new Promise<CalcResult>((res) => resolvers.push(res));
      },
      start: async () => {
        const s = { source: "proj", id: ++id };
        started.push(s);
        return s;
      },
      cancel: async (j) => void cancelled.push(j.id),
      clock,
    },
    (s) => (last = s),
  );
  return { runner, advance, previews, started, cancelled, resolvers, state: () => last ?? runner.state() };
}

describe("the runner", () => {
  const B1 = { set_id: 1, nlag: 6 };
  const B2 = { set_id: 1, nlag: 7 };
  const PV = readCalc(A.preview);

  it("the preview 200 ms after the last edit; the newest answer only; the same body once", async () => {
    const t = setup();
    t.runner.edit(B1);
    t.advance(150);
    t.runner.edit(B2);
    t.advance(199);
    expect(t.previews).toEqual([]);
    t.advance(1);
    expect(t.previews).toEqual([JSON.stringify(B2)]);
    t.runner.edit(B1);
    t.advance(200);
    expect(t.previews).toHaveLength(1); // one in flight: B1 waits for the answer of B2
    expect(t.state().previewBusy).toBe(true);
    t.resolvers[0](readCalc(A.preview_sampled));
    await flush();
    expect(t.previews).toEqual([JSON.stringify(B2), JSON.stringify(B1)]); // the newest body after the answer
    expect(t.state().previewFor).toBe(JSON.stringify(B2));
    t.resolvers[1](PV);
    await flush();
    expect(t.state().preview).toBe(PV);
    expect(t.state().previewFor).toBe(JSON.stringify(B1));
    expect(t.state().previewBusy).toBe(false);
    t.runner.edit(B1);
    t.advance(500);
    expect(t.previews).toHaveLength(2);
    expect(t.started).toEqual([]); // no job before «Рассчитать»
  });

  it("an answer for an older body is not shown when the body was emptied meanwhile; edits in flight coalesce", async () => {
    const t = setup();
    t.runner.edit(B1);
    t.advance(200);
    t.runner.edit(B2);
    t.advance(200);
    t.runner.edit({ set_id: 1, nlag: 8 });
    t.advance(200);
    expect(t.previews).toHaveLength(1);
    t.runner.edit(null);
    t.resolvers[0](PV);
    await flush();
    expect(t.state().preview).toBeNull();
    expect(t.state().previewBusy).toBe(false);
    expect(t.previews).toHaveLength(1); // nothing goes for an incomplete body
    t.runner.edit(B2);
    t.advance(200);
    expect(t.previews).toEqual([JSON.stringify(B1), JSON.stringify(B2)]);
  });

  it("a drag waits for the release", () => {
    const t = setup();
    t.runner.edit(B1, true);
    t.advance(1000);
    expect(t.previews).toEqual([]);
    t.runner.edit(B1, false);
    t.advance(200);
    expect(t.previews).toEqual([JSON.stringify(B1)]);
  });

  it("«Рассчитать» starts the job; then a settled edit starts it again, the previous unfinished one cancelled", async () => {
    const t = setup();
    await t.runner.run(B1);
    expect(t.started.map((s) => s.id)).toEqual([1]);
    expect(t.state()).toMatchObject({ job: { id: 1 }, jobLive: true, auto: true, jobFor: JSON.stringify(B1) });
    t.runner.edit(B2, true);
    t.advance(500);
    expect(t.started).toHaveLength(1); // dragging
    t.runner.edit(B2);
    t.advance(200);
    await flush();
    expect(t.cancelled).toEqual([1]);
    expect(t.started.map((s) => s.id)).toEqual([1, 2]);
    t.runner.finished({ source: "proj", id: 2 });
    expect(t.state().jobLive).toBe(false);
    t.runner.edit(B1);
    t.advance(200);
    await flush();
    expect(t.cancelled).toEqual([1]); // the finished job is not cancelled
    expect(t.started.map((s) => s.id)).toEqual([1, 2, 3]);
    t.runner.stop();
    expect(t.cancelled).toEqual([1, 3]); // the window closed: its unfinished job goes too
  });

  it("an incomplete body sends nothing; a refusal of the start is kept", async () => {
    const t = setup();
    t.runner.edit(null);
    t.advance(1000);
    expect(t.previews).toEqual([]);
    const bp = A.preview_bad_param.body;
    const bad = createRunner(
      {
        preview: async () => PV,
        start: async () => {
          const e = new CoreError(422, bp.code, bp.message, bp.hint);
          e.body = bp;
          throw e;
        },
        cancel: async () => undefined,
      },
      () => undefined,
    );
    await bad.run(B1);
    expect(bad.state().startError).toMatchObject({ code: "bad_param", param: "nlag" });
    expect(bad.state().jobLive).toBe(false);
  });
});

// ---- the charts and the table ----

const render = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("the charts of the axes", () => {
  const r = readJobCalc(A.job_axes);
  it("three axes with the model along each, the downhole one without; lags without pairs not drawn", () => {
    const s = axisSeries(r, MODEL);
    expect(s.axes.map((x) => x.label)).toEqual(["Ось 1 · Аз 30° · Пог 0°", "Ось 2 · Аз 300° · Пог 0°", "Ось 3 · Аз 0° · Пог -90°"]);
    expect(s.downhole?.u).toBeNull();
    const html = render(createElement(AxisCharts, { calc: r, model: MODEL, type: "semivariogram", fileName: "Вариография AU" }));
    expect(count(html, /<figure /g)).toBe(4);
    expect(html).toContain('data-series="Ось 1 · Аз 30° · Пог 0°" data-points="4" data-few="1"');
    expect(html).toContain('data-series="Вдоль скважин" data-points="15" data-few="15"');
    expect(count(html, /data-model=/g)).toBe(3);
    expect(html).toContain(`data-line="variance" data-value="${r.sigma2}"`);
    const dh = render(createElement(AxisCharts, { calc: r, model: MODEL, type: "semivariogram", fileName: "В", downholeOnly: true }));
    expect(count(dh, /<figure /g)).toBe(1);
    const none = render(createElement(AxisCharts, { calc: readJobCalc(A.job_nscore), model: MODEL, type: "nscore", fileName: "В", downholeOnly: true }));
    expect(none).toContain("Вариограмма вдоль скважин не рассчитана");
  });
});

describe("the table of the model", () => {
  it("cells take values only in their ranges", () => {
    expect(cellValue("c0", "0,2")).toEqual({ value: 0.2 });
    expect(cellValue("c0", "-1")).toEqual({ error: "Самородок: не меньше 0" });
    expect(cellValue("a1", "0")).toEqual({ error: "Дальность a1: больше 0 до 100 000 м" });
    expect(cellValue("a2", "100000")).toEqual({ value: 100_000 });
    expect(cellValue("ang1", "360")).toEqual({ value: 0 });
    expect(cellValue("ang3", "-180")).toEqual({ value: 180 });
    expect("error" in cellValue("ang2", "91")).toBe(true);
    expect(cellValue("c", "")).toEqual({ error: "Нужно число" });
  });

  it("renders the structures, the fixings, the sill and the warning above 1,2·σ²", () => {
    const html = render(createElement(ModelTable, { model: MODEL, sigma2: A.job_axes.sigma2, transform: "none", fix: ["structures.0.a3"], onChange: () => undefined, onFix: () => undefined }));
    expect(count(html, /data-structure=/g)).toBe(1);
    expect(count(html, /type="checkbox"[^>]*checked=""/g)).toBe(2); // a3 fixed and «общие оси»
    expect(html).toContain("Порог 1,5; σ² = 1,669");
    expect(html).not.toContain("1,2·σ²");
    const big = render(createElement(ModelTable, { model: { ...MODEL, c0: 1 }, sigma2: A.job_axes.sigma2, transform: "none", fix: [], onChange: () => undefined, onFix: () => undefined }));
    expect(big).toContain("больше 1,2·σ²");
    const empty = render(createElement(ModelTable, { model: { ...MODEL, structures: [] }, sigma2: null, transform: "nscore", fix: [], onChange: () => undefined, onFix: () => undefined }));
    expect(empty).toContain("Структур нет");
    expect(empty).toContain("σ² = —");
  });
});
